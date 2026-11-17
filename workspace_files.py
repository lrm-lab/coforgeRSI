"""Browse task inputs and workspaces, including unverified in-progress files."""
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from urllib.parse import quote
import zipfile

MAX_BYTES = 128 * 1024 * 1024
SOURCE_BYTES = 512 * 1024
EXCLUDED = {'node_modules', '__pycache__', 'venv', 'env', 'site-packages'}
TEXT_SUFFIXES = {'.html','.htm','.css','.js','.mjs','.cjs','.json','.md','.txt','.csv','.ts','.tsx','.jsx','.py','.sh','.yaml','.yml','.toml','.xml','.svg','.log'}


def bases(job):
    found = {'input': job/'input'}
    root = job/'goal/workspaces'
    if root.is_dir():
        for p in sorted(root.iterdir(), key=lambda p:p.stat().st_mtime):
            if p.is_dir() and not p.is_symlink() and not p.name.startswith('.'):
                found['workspace-'+p.name] = p
    return found


def allowed_parts(parts):
    return bool(parts) and all(p not in ('.','..') and not p.startswith('.') and p not in EXCLUDED for p in parts)


def resolve(job, scope, relative):
    base = bases(job).get(scope)
    if base is None or base.is_symlink():raise ValueError('文件分组不存在')
    return resolve_in(base, relative)


def resolve_in(base, relative):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not allowed_parts(rel.parts):raise ValueError('无效文件路径')
    target = base
    for part in rel.parts:
        target = target/part
        if target.is_symlink():raise ValueError('不支持符号链接')
    if not target.resolve().is_relative_to(base.resolve()) or target.suffix.lower() in ('.key','.pem'):
        raise ValueError('无效文件')
    if not target.is_file() or target.stat().st_size>MAX_BYTES:raise ValueError('文件不存在或过大')
    return target


def inventory(job, state):
    groups = []
    for scope, base in bases(job).items():
        files = []
        # Deliverables remain visible even when a research workspace has hundreds of caches.
        priority_dirs = [base/name for name in ['results','output','outputs','artifacts']
                         if (base/name).is_dir() and not (base/name).is_symlink()]
        def walk():
            for root in [*priority_dirs,base]:
                for folder, dirs, names in os.walk(root,followlinks=False):
                    if Path(folder)==base:dirs[:] = [d for d in dirs if base/d not in priority_dirs]
                    yield folder,dirs,names
        for folder, dirs, names in walk():
            dirs[:] = sorted(d for d in dirs if allowed_parts([d]) and not (Path(folder)/d).is_symlink())
            for name in sorted(names):
                p = Path(folder)/name
                rel = p.relative_to(base).as_posix()
                try:target = resolve_in(base, rel);stat = target.stat()
                except (ValueError, OSError):continue
                url = f'/api/jobs/{job.name}/files/{quote(scope, safe="")}'
                files.append({'name':rel, 'size':stat.st_size, 'modified':stat.st_mtime_ns,
                    'download_url':url+'/download/'+quote(rel),
                    'source_url':url+'/source/'+quote(rel) if p.suffix.lower() in TEXT_SUFFIXES or not p.suffix else None,
                    'preview_url':f'/api/jobs/{job.name}/preview/{quote(scope, safe="")}/'+quote(rel) if p.suffix.lower() in ('.html','.htm') else None})
                if len(files)>=300:break
            if len(files)>=300:break
        attempt = base.name
        feedback_file = job/'goal/attempts'/f'{attempt}.json'
        try:feedback = json.loads(feedback_file.read_text()).get('feedback', {})
        except (OSError, ValueError):feedback = {}
        final = scope!='input' and attempt==state.get('answer_attempt')
        verified = bool(feedback.get('valid') and feedback.get('passed'))
        label = ('原始输入' if scope=='input' else '验收规划' if attempt=='bootstrap' else attempt)
        if final:label += ' · 最终产物'
        elif scope!='input':label += ' · 已通过验收' if verified else ' · 尚未通过验收'
        groups.append({'id':scope,'label':label,'final':final,'verified':verified,'files':files,
                       'truncated':len(files)>=300,
                       'archive_available':len(files)<300 and sum(f['size'] for f in files)<=MAX_BYTES,
                       'archive_url':f'/api/jobs/{job.name}/files/{quote(scope, safe="")}/archive'})
    return groups


def source(target):
    with target.open('rb') as stream:raw = stream.read(SOURCE_BYTES+1)
    if b'\x00' in raw:raise ValueError('二进制文件请下载查看')
    return {'name':target.name,'text':raw[:SOURCE_BYTES].decode('utf-8', errors='replace'),
            'truncated':len(raw)>SOURCE_BYTES,'size':target.stat().st_size}


def archive(job, scope, state):
    group = next((g for g in inventory(job,state) if g['id']==scope),None)
    if group is None:raise ValueError('文件分组不存在')
    if group['truncated']:raise ValueError('文件超过300个，请逐个下载所需文件')
    data = io.BytesIO();total = 0
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as out:
        for file in group['files']:
            target = resolve(job,scope,file['name'])
            total += target.stat().st_size
            if total>MAX_BYTES:raise ValueError('文件合计超过128 MiB，请逐个下载')
            out.write(target,arcname=file['name'])
    return data.getvalue()


def preview_type(target):
    # Never execute generated Python/shell files on the server.
    return mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
