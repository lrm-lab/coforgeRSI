"""Intranet UI for the sealed RSI runtime. Python stdlib only, no model proxy keys."""
from __future__ import annotations
import argparse
import email.parser
import email.policy
import fcntl
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, quote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'rsi'))
from general_rsi.backend import process_alive, process_start
from general_rsi.storage import write_json, append_feedback
from run_live import explain
from execution_trace import safe_text, call_trace
from demo_model_config import process_env, MAX_OUTPUT_TOKENS
import workspace_files
import evolution_evidence
import historical_evidence
import recorded_evidence
import retained_results
from retention_review import load_state
MAX_UPLOAD = 128 * 1024 * 1024
LOCK = threading.RLock()
DATA = None
FIXTURE = False


def read(path, default=None):
    try:return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):return default


def lines(path):
    try:
        result = []
        for raw in path.read_text(errors='replace').splitlines():
            try:result.append(json.loads(raw))
            except ValueError:pass
        return result
    except FileNotFoundError:return []


def jobs():
    return sorted((p for p in (DATA/'jobs').glob('*') if p.is_dir()), key=lambda p:read(p/'job.json',{}).get('created_at',0), reverse=True)


def job_at(identity):
    if not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[a-f0-9]{8}', identity):raise ValueError('任务不存在')
    path = DATA/'jobs'/identity
    if not (path/'job.json').is_file():raise ValueError('任务不存在')
    return path


def status(path):
    meta = read(path/'job.json', {})
    state = load_state(path/'goal')
    report = read(path/'goal/report.json', {})
    active = process_alive(meta.get('process', {}))
    phase = meta.get('status', 'unknown')
    if state.get('_retention_review') and not active:
        phase = state.get('status', phase)
    if phase in ('running', 'stopping') and not active:
        phase = state.get('status', 'interrupted')
        if phase in ('ready', 'running'):phase = 'interrupted'
    queued = sorted((read(p/'job.json',{}) for p in jobs() if read(p/'job.json',{}).get('status')=='queued'),
                    key=lambda m:m.get('created_at',0)) if phase=='queued' else []
    position = next((i+1 for i,m in enumerate(queued) if m.get('id')==path.name),None)
    limits = state.get('limits') or read(path/'config.json',{}).get('limits',{})
    return {'id':path.name, 'question':meta.get('display_question') or meta.get('question',''), 'mode':meta.get('mode'),
            'status':phase, 'created_at':meta.get('created_at'), 'running':bool(active),
            'agent_calls':state.get('agent_calls',0), 'verifier_calls':state.get('verifier_calls',0),
            'round_index':state.get('round_index',0), 'known_tokens':state.get('known_tokens',0),
            'validated_experience_count':max(0,len(state.get('retained',{}))-1),
            'reason':safe_text(state.get('reason','') if state.get('_retention_review') else report.get('reason') or meta.get('error') or state.get('reason','')),
            'retention_review':state.get('_retention_review'),
            'attachments':meta.get('attachments', []), 'fixture':meta.get('fixture',False),
            'benchmark':meta.get('benchmark'),
            'verifier_authority':(state.get('verifier') or {}).get('authority'),
            'limits':limits, 'acceptance':state.get('acceptance',[]),
            'queue_position':position, 'queue_batch':meta.get('queue_batch'),
            'started_at':meta.get('started_at'), 'deadline':state.get('deadline')}


def detail(path, event_limit=700):
    result = status(path)
    result['benchmark'] = historical_evidence.enrich(result.get('benchmark'), ROOT/'benchmarks/wildclaw-arxiv-digest/historical-progress.json.gz')
    goal = path/'goal'
    events = []
    for index, entry in enumerate(lines(goal/'events.jsonl')):
        message = explain(entry)
        if message:events.append({'id':f'e{index}', 'time':entry['time'], 'type':entry['type'],
                                  'text':message, 'call_id':entry.get('call_id'),
                                  'role':entry.get('role'), 'passed':entry.get('passed')})
    calls = []
    for folder in sorted((goal/'calls').glob('*')):
        req = read(folder/'request.json', {})
        receipt = read(folder/'result.json')
        calls.append({'id':folder.name, 'role':req.get('role','agent'),
                      'status':'running' if receipt is None and result['running'] else
                               ('finished' if receipt and receipt.get('ok') else 'stopped'),
                      'ok':receipt.get('ok') if receipt else None})
        trace, diagnostic = call_trace(folder, receipt, result['running'], result['created_at'])
        events.extend(trace)
        calls[-1]['diagnostic'] = diagnostic
        calls[-1]['elapsed_seconds'] = (receipt or {}).get('process', {}).get('elapsed_seconds')
        calls[-1]['usage'] = (receipt or {}).get('usage')
        if diagnostic:
            for event in events:
                if event['type']=='agent_finished' and event.get('call_id')==folder.name:
                    event.update(text=f"{folder.name}：{diagnostic['message']}；失败记录已保存", diagnostic=diagnostic)
    if result['status']=='execution_error':
        failed = [c for c in calls if c.get('diagnostic')]
        if failed:result['reason'] = f"{failed[-1]['id']}：{failed[-1]['diagnostic']['message']}；失败记录已保存。可查看下方命令与输出。"
    result['event_total'] = len(events)
    result['events'] = sorted(events, key=lambda e:e['time'])
    if event_limit:result['events'] = result['events'][-event_limit:]
    result['calls'] = calls
    result['plans'] = [safe_text(p.get('reason',''),2400) for p in
                       (read(file,{}) for file in sorted((goal/'plans').glob('round-*.json'))) if p.get('reason')]
    result['feedback'] = [{'attempt':p.stem, 'valid':r.get('valid'), 'passed':r.get('passed'),
                           'score':r.get('score'), 'diagnostic':safe_text(r.get('diagnostic',''),2000)}
                          for p in sorted((goal/'attempts').glob('*.json'))
                          for r in [read(p,{}).get('feedback',{})]]
    state = load_state(goal)
    result['evolution'] = evolution_evidence.summarize(goal,state)
    result['answer'] = safe_text(state['_review_answer']) if '_review_answer' in state else safe_text((goal/'answer.md').read_text(errors='replace')) if (goal/'answer.md').is_file() else ''
    result['artifacts'] = []
    result['file_workspaces'] = workspace_files.inventory(path, state)
    result.update(retained_results.present(state, result['evolution'], result['file_workspaces'], result['running']))
    result['artifacts'] = result.pop('partial_artifacts')
    attempt = state.get('answer_attempt')
    if attempt and re.fullmatch(r'[\w.-]+',attempt):
        base = goal/'workspaces'/attempt
        if base.is_dir():
            def artifact_order(p):
                rel=p.relative_to(base)
                primary=rel.parts[0] in ('results','output','outputs','artifacts') or p.name in ('index.html','answer.md','report.md','summary.md','result.json')
                return (not primary,rel.as_posix())
            for p in sorted(base.rglob('*'),key=artifact_order):
                if p.is_symlink() or not p.is_file():continue
                rel = p.relative_to(base)
                if any(x.startswith('.') for x in rel.parts) or p.suffix in ('.key','.pem'):continue
                if p.stat().st_size <= MAX_UPLOAD:
                    result['artifacts'].append({'name':str(rel), 'size':p.stat().st_size,
                                                'url':f'/api/jobs/{path.name}/artifact/{quote(rel.as_posix())}'})
                if len(result['artifacts'])>=100:break
    return result


def basename(name):
    if not name or '\\' in name or '/' in name or name in ('.','..') or name.startswith('.'):
        raise ValueError('文件名无效，请使用普通文件名')
    if len(name)>180:raise ValueError('文件名过长')
    return name


def add_uploads(path, uploads):
    attached, skills = [], []
    for field, name, content in uploads:
        name = basename(name)
        if field not in ('files','skills'):raise ValueError('上传字段无效')
        folder = path/'input'/field
        folder.mkdir(parents=True,exist_ok=True)
        target = folder/name
        if target.exists():raise ValueError('同一组附件中存在重名文件')
        if field == 'skills':
            if name.lower().endswith('.zip'):
                target = folder/(name[:-4]+'-'+uuid.uuid4().hex[:6]);target.mkdir()
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    entries = archive.infolist()
                    if len(entries)>1000 or sum(x.file_size for x in entries)>MAX_UPLOAD:
                        raise ValueError('Skill解压后过大')
                    for entry in entries:
                        parts = PurePosixPath(entry.filename)
                        if parts.is_absolute() or '..' in parts.parts or '\\' in entry.filename or (entry.external_attr>>16)&0o170000 == 0o120000:
                            raise ValueError('Skill压缩包包含无效路径或链接')
                        out = target.joinpath(*parts.parts)
                        if entry.is_dir():out.mkdir(parents=True,exist_ok=True)
                        else:
                            out.parent.mkdir(parents=True,exist_ok=True)
                            out.write_bytes(archive.read(entry))
                found = sorted(target.rglob('SKILL.md'))
                if not found:raise ValueError('Skill ZIP需要包含SKILL.md')
                skills.extend(str(x) for x in found)
            elif name.lower().endswith('.md'):
                # Pi expects an actual SKILL.md, retain one directory per upload.
                target = folder/(target.stem+'-'+uuid.uuid4().hex[:6]);target.mkdir()
                (target/'SKILL.md').write_bytes(content);skills.append(str(target/'SKILL.md'))
            else:raise ValueError('Skill请上传.md或含SKILL.md的.zip')
        else:target.write_bytes(content)
        attached.append({'name':name,'bytes':len(content),'kind':field})
    return attached, skills


def create_job(fields, uploads):
    question = fields.get('question','').strip()
    mode = fields.get('mode','custom')
    if mode not in ('custom','sample'):raise ValueError('运行模式无效')
    if mode == 'sample':question = '按task.txt改进现有result.json，计算合法整数x，使目标达到全域最小。保留可复核计算，已有初稿先由环境验收。'
    if not question or len(question)>16000:raise ValueError('请填写问题，长度不超过16000字符')
    if mode == 'sample' and uploads:raise ValueError('预置计算演示不接收额外附件，请切换为新任务')
    identity = time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]
    path = DATA/'jobs'/identity
    path.mkdir()
    try:
        (path/'input').mkdir()
        attached,skills = add_uploads(path, uploads)
        if mode == 'sample':
            for p in (ROOT/'sample-input').iterdir():
                (path/'input'/p.name).write_bytes(p.read_bytes())
        backend = {'type':'pi','provider':'zhizengzeng','model':'deepseek-v4.1-flash',
                   'env_file':'~/.pi/agent/zhizengzeng.env',
                   'args':['--offline','--no-extensions','--no-skills','--no-context-files']}
        for skill in skills:backend['args'] += ['--skill',skill]
        config = {'backend':backend, 'limits':{'max_seconds':None,'agent_timeout':None,'verifier_timeout':None}}
        if FIXTURE:
            backend.clear();backend.update(type='command',command=[sys.executable,str(ROOT/'rsi/examples/demo_agent.py')])
        if mode == 'sample':
            config['verifier'] = {'command':[sys.executable,str(ROOT/'rsi/examples/demo_verifier.py'),'{workspace}'],
                                  'mode':'json','check_ids':['integer_domain','minimum'],'authority':'user_provided'}
        write_json(path/'config.json', config)
        meta = {'id':identity,'question':question,'mode':mode,'created_at':time.time(), 'status':'queued',
                'attachments':attached,'fixture':FIXTURE}
        with LOCK:write_json(path/'job.json',meta)
        return status(path)
    except BaseException:
        import shutil
        shutil.rmtree(path)
        raise


def runtime_for(path):
    """Resume with the exact code sealed by the goal, including pre-upgrade jobs."""
    expected = read(path/'goal/state.json', {}).get('implementation_hashes')
    if not expected:return ROOT/'rsi'
    for candidate in [ROOT/'rsi', *sorted((ROOT/'runtimes').glob('*'))]:
        package = candidate/'general_rsi'
        hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob('*.py'))}
        if hashes==expected:return candidate
    raise ValueError('找不到任务封存的运行时版本，未自动更改任务完整性记录')


def scheduler():
    while True:
        try:
            with LOCK:
                paths = jobs()
                metas = [(p,read(p/'job.json',{})) for p in paths]
                if any(process_alive(m.get('process',{})) for _,m in metas):
                    target = None
                else:
                    target = next(((p,m) for p,m in reversed(metas) if m.get('status')=='queued'),None)
                    if target:
                        path,meta = target
                        resume = meta.pop('resume',False)
                        meta['status']='launching';write_json(path/'job.json',meta)
                        command = ['bash',str((runtime_for(path) if resume else ROOT/'rsi')/'run.sh')]
                        if resume:command += ['resume',str(path/'goal')]
                        else:
                            command += ['ask',meta['question'],'--workspace',str(path/'input'),
                                        '--output',str(path/'goal'),'--memory',str(path/'memory' if meta['mode']=='sample' or FIXTURE or meta.get('isolated_memory') else DATA/'memory'),
                                        '--config',str(path/'config.json')]
                            if meta['mode']=='sample':command.append('--baseline-existing')
                        with (path/'stdout.log').open('a') as out,(path/'stderr.log').open('a') as err:
                            proc = subprocess.Popen(command,cwd=ROOT,stdout=out,stderr=err,start_new_session=True,
                                                    env=process_env(ROOT, FIXTURE))
                        meta.update(status='running',started_at=time.time(),process={'pid':proc.pid,'start_ticks':process_start(proc.pid)})
                        write_json(path/'job.json',meta)
            if target:
                code = proc.wait()
                with LOCK:
                    meta = read(path/'job.json',meta)
                    final = read(path/'goal/state.json',{}).get('status','execution_error')
                    if final in ('ready','running'):final='interrupted'
                    meta.update(status=final,exit_code=code,finished_at=time.time())
                    if final=='execution_error' and not (path/'goal/state.json').exists():
                        meta['error']=safe_text((path/'stderr.log').read_text(errors='replace')[-3000:])
                    write_json(path/'job.json',meta)
        except Exception as error:
            print('Scheduler error:',type(error).__name__,flush=True)
            if 'path' in locals():
                with LOCK:
                    meta = read(path/'job.json',{})
                    if meta.get('status')=='launching':
                        meta.update(status='launch_uncertain',error='启动状态不确定，未自动重放。请由维护者检查。')
                        write_json(path/'job.json',meta)
        time.sleep(.4)


class Handler(BaseHTTPRequestHandler):
    server_version = 'RSI-Intranet/1.0'
    def log_message(self,fmt,*args):pass
    def respond(self,value,code=200,kind='application/json; charset=utf-8',download=None,preview=False):
        body = json.dumps(value,ensure_ascii=False).encode() if kind.startswith('application/json') and not isinstance(value,bytes) else value
        self.send_response(code)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if preview:
            self.send_header('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; media-src 'self' data: blob:; connect-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
            self.send_header('Access-Control-Allow-Origin','*')
            self.send_header('Referrer-Policy','no-referrer')
        else:self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if download:self.send_header('Content-Disposition','attachment; filename="artifact"; filename*=UTF-8\'\''+__import__('urllib.parse',fromlist=['quote']).quote(download))
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        try:self.get()
        except (ValueError,KeyError,FileNotFoundError) as error:self.respond({'error':str(error)},404)
        except (BrokenPipeError,ConnectionResetError):pass
    def get(self):
        route = unquote(urlsplit(self.path).path)
        if route=='/api/health':return self.respond({'ok':True,'version':'1.0','runtime':'0.1.1','fixture':FIXTURE,
            'model':'zhizengzeng/deepseek-v4.1-flash','max_upload_bytes':MAX_UPLOAD,'default_time_limits':None,
            'configured_model_max_output_tokens':MAX_OUTPUT_TOKENS})
        if route=='/api/jobs':return self.respond([status(p) for p in jobs()][:100])
        if route=='/api/replay':
            summary = read(ROOT/'evidence/result-summary.json',{})
            raw = lines(ROOT/'evidence/events.jsonl')
            events = [{'id':i,'time':e['time'],'text':explain(e),'type':e['type']} for i,e in enumerate(raw) if explain(e)]
            return self.respond({'summary':summary,'events':events,'evolution':recorded_evidence.summarize(summary,raw),
                'notice':'已完成的真实GAIA实测回放；不是当前现场调用。原试跑使用180秒单次限制，当前默认已取消。'})
        match = re.fullmatch(r'/api/jobs/([^/]+)(?:/(.*))?',route)
        if match:
            path = job_at(match[1]);extra = match[2]
            if not extra:return self.respond(detail(path))
            if extra=='report':
                report = read(path/'goal/report.json')
                if not report:raise ValueError('报告尚未生成')
                state = load_state(path/'goal')
                if state.get('_retention_review'):
                    report = {**report, 'original_report':report, 'retention_review':state['_retention_review'],
                              **{k:state.get(k) for k in ['status','reason','answer_attempt','final_experience','current_experience']},
                              'validated_experience_count':max(0,len(state.get('retained',{}))-1)}
                return self.respond(report,download='report.json')
            if extra=='trace':
                data = detail(path, event_limit=None)
                return self.respond({'id':data['id'], 'status':data['status'], 'reason':data['reason'],
                                     'calls':data['calls'], 'events':data['events']}, download='execution-trace.json')
            if extra=='evolution':
                proof = evolution_evidence.summarize(path/'goal',load_state(path/'goal'))
                proof['benchmark'] = historical_evidence.enrich(read(path/'job.json',{}).get('benchmark'), ROOT/'benchmarks/wildclaw-arxiv-digest/historical-progress.json.gz')
                return self.respond(proof,download='rsi-evidence.json')
            if extra=='experiences':
                data = detail(path)
                return self.respond({'id':data['id'], 'status':data['status'],
                    'selected_experience':data['method_experience_id'],
                    'experiences':data['retained_experiences']}, download='retained-experiences.json')
            files_match = re.fullmatch(r'files/([^/]+)/(archive|download/(.+)|source/(.+))',extra)
            if files_match:
                scope,action = files_match[1],files_match[2]
                if action=='archive':
                    return self.respond(workspace_files.archive(path,scope,load_state(path/'goal')),
                                        kind='application/zip',download=scope+'.zip')
                target = workspace_files.resolve(path,scope,files_match[3] or files_match[4])
                if action.startswith('source/'):
                    data = workspace_files.source(target)
                    data['text'] = safe_text(data['text'],workspace_files.SOURCE_BYTES)
                    return self.respond(data)
                return self.respond(target.read_bytes(),kind='application/octet-stream',download=target.name)
            preview_match = re.fullmatch(r'preview/([^/]+)/(.*)',extra)
            if preview_match:
                target = workspace_files.resolve(path,preview_match[1],preview_match[2] or 'index.html')
                return self.respond(target.read_bytes(),kind=workspace_files.preview_type(target),preview=True)
            if extra.startswith('artifact/'):
                state = load_state(path/'goal')
                base = path/'goal/workspaces'/str(state.get('answer_attempt','__none__'))
                rel = PurePosixPath(extra[len('artifact/'):])
                if rel.is_absolute() or any(p in ('..','.') or p.startswith('.') for p in rel.parts):raise ValueError('无效路径')
                target = base.joinpath(*rel.parts)
                if target.is_symlink() or not target.resolve().is_relative_to(base.resolve()) or target.suffix in ('.pem','.key'):
                    raise ValueError('无效文件')
                if not target.is_file() or target.stat().st_size>MAX_UPLOAD:raise ValueError('文件不可下载')
                return self.respond(target.read_bytes(),kind='application/octet-stream',download=target.name)
            raise ValueError('接口不存在')
        static = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if route in static:
            target = ROOT/'static'/static[route]
            return self.respond(target.read_bytes(),kind=(mimetypes.guess_type(target.name)[0] or 'text/plain')+'; charset=utf-8')
        raise ValueError('页面不存在')
    def do_POST(self):
        try:
            # Browser writes require a custom header and same-origin; no cross-origin API.
            if self.headers.get('X-RSI-Client')!='web':raise ValueError('请从本页面发起请求')
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):raise ValueError('来源不匹配')
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_UPLOAD:raise ValueError('请求为空或超过128MiB')
            self.connection.settimeout(120)  # HTTP upload I/O, not Agent execution time.
            body = self.rfile.read(length)
            if len(body)!=length:raise ValueError('文件上传不完整')
            route = urlsplit(self.path).path
            if route=='/api/jobs':
                fields,uploads = {},[]
                if self.headers.get('Content-Type','').startswith('multipart/form-data'):
                    envelope = b'Content-Type: '+self.headers['Content-Type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+body
                    parsed = email.parser.BytesParser(policy=email.policy.default).parsebytes(envelope)
                    if not parsed.is_multipart():raise ValueError('上传格式无效')
                    for part in parsed.iter_parts():
                        name = part.get_param('name',header='content-disposition')
                        filename = part.get_filename()
                        content = part.get_payload(decode=True) or b''
                        if filename:uploads.append((name,filename,content))
                        else:fields[name]=content.decode('utf-8')
                    if len(uploads)>30:raise ValueError('一次最多30个附件')
                else:fields=json.loads(body)
                return self.respond(create_job(fields,uploads),201)
            match = re.fullmatch(r'/api/jobs/([^/]+)/(stop|resume|feedback)',route)
            if not match:raise ValueError('接口不存在')
            path = job_at(match[1])
            with LOCK:
                meta = read(path/'job.json',{})
                active = process_alive(meta.get('process',{}))
                if match[2]=='stop':
                    if active:
                        os.kill(meta['process']['pid'],signal.SIGINT);meta['status']='stopping'
                    elif meta.get('status')=='queued':meta['status']='cancelled'
                    else:raise ValueError('任务当前没有运行')
                elif match[2]=='resume':
                    if active or meta.get('status') in ('queued','launching','launch_uncertain'):raise ValueError('任务已在运行或启动状态需要维护者检查')
                    current = status(path)['status']
                    if current not in ('interrupted','execution_error'):raise ValueError('此状态不支持网页恢复；预算耗尽或协议错误请由维护者处理')
                    if not (path/'goal/state.json').is_file():raise ValueError('缺少可恢复状态')
                    meta.update(status='queued',resume=True)
                else:
                    text = json.loads(body).get('text','').strip()
                    if not text or len(text)>8000:raise ValueError('反馈须为1至8000字符')
                    if not (path/'goal/state.json').is_file():raise ValueError('任务尚未开始')
                    append_feedback(path/'goal',text)
                    return self.respond({'queued':True,'message':'反馈将在下次规划时读取；已封存验收标准保持不变'})
                write_json(path/'job.json',meta)
            return self.respond(status(path))
        except (ValueError,KeyError,OSError,UnicodeError,zipfile.BadZipFile) as error:
            self.respond({'error':safe_text(error,1000)},400)


def main():
    global DATA,FIXTURE
    parser = argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data',default=str(ROOT/'data'))
    parser.add_argument('--test-fixture',action='store_true',help='明确标记的脚本Agent测试；不得用于真实模型演示')
    args = parser.parse_args()
    DATA = Path(args.data).expanduser().resolve();FIXTURE=args.test_fixture
    (DATA/'jobs').mkdir(parents=True,exist_ok=True)
    owned = (DATA/'.server.lock').open('a+')
    fcntl.flock(owned,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for p in jobs():
        m = read(p/'job.json',{})
        if m.get('status')=='launching':
            m.update(status='launch_uncertain',error='上次服务在启动边界退出，未自动重放；需维护者核对进程。')
            write_json(p/'job.json',m)
    server = ThreadingHTTPServer((args.host,args.port),Handler)
    threading.Thread(target=scheduler,daemon=True).start()
    print(f'RSI intranet listening http://{args.host}:{args.port} fixture={FIXTURE}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
