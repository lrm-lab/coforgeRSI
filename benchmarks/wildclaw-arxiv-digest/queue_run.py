"""Queue a fresh difficult WildClawBench run, with historical selection evidence outside the input."""
import json
from pathlib import Path
import time
import uuid

ROOT = Path(__file__).resolve().parent
WEB = ROOT.parents[1]


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)


def main():
    receipt = ROOT/'queued-run.json'
    if receipt.exists():print(receipt.read_text());return
    identity = time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]
    job = WEB/'data/jobs'/identity;job.mkdir(parents=True,exist_ok=False);(job/'input').mkdir()
    config = {'backend':{'type':'pi','provider':'zhizengzeng','model':'deepseek-v4.1-flash',
        'args':['--offline','--no-extensions','--no-skills','--no-context-files','--append-system-prompt',
            'The benchmark logical /tmp_workspace path means YOUR supplied current workspace. Resolve all task outputs against your working directory, without creating host-wide /tmp_workspace. Do not inspect benchmark grading/provenance/history files outside your supplied workspace. Only the original task and subsequently measured environmental feedback guide the solution. The system has curl and pdftotext. A Python with BeautifulSoup and lxml is available at '+str(ROOT/'.venv/bin/python')+'.']},
        'limits':{'max_seconds':None,'agent_timeout':None,'verifier_timeout':None,
                  'max_agent_calls':24,'max_verifier_calls':24,'max_rounds':5,'parallelism':2,'verification_repeats':2},
        'verifier':{'command':['/usr/bin/python3',str(ROOT/'grade_bridge.py'),'{workspace}',str(ROOT/'grader_upstream.py'),str(ROOT/'provenance.json')],
                    'mode':'json','check_ids':['classify_score','metadata_score','interest_score'],'authority':'user_provided'}}
    write(job/'config.json',config)
    provenance = json.loads((ROOT/'provenance.json').read_text())
    historical = json.loads((ROOT/'historical-reference.json').read_text())
    meta = {'id':identity,'question':(ROOT/'prompt-original.txt').read_text().strip(),
        'display_question':'WildClawBench 难题 · ArXiv 每日论文分类与元数据审计',
        'mode':'custom','created_at':time.time(),'status':'queued','fixture':False,'isolated_memory':True,'attachments':[],
        'benchmark':{**provenance,'label':'WildClawBench · ArXiv Daily Paper Digest',
            'historical_reference':historical,
            'note':'按143历史多次修订提分案例选题；本次从空经验重新执行，不导入旧方法、答案或分数。使用当前官方题目与原grader，仅映射工作区路径；RSI/Pi、无默认时限的适配运行，不是官方限时排行榜复现。'}}
    write(job/'job.json',meta)
    result={'id':identity,'url':f'http://10.110.158.144:18766/?job={identity}#evolution','task_id':provenance['task_id'],'status':'queued'}
    write(receipt,result);print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
