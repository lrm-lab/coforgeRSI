"""Atomically publish an idempotent FIFO batch, each with a three-hour run deadline."""
import json
from pathlib import Path
import shutil
import time
import uuid

from prepare_three_tasks import TASKS

ROOT = Path(__file__).resolve().parent
WEB = ROOT.parent
BATCH = '20261116-three-tasks-baseline-v2'


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    path.chmod(0o600)


def main():
    receipt = ROOT/(BATCH+'.json')
    if receipt.exists():
        print(receipt.read_text());return
    # Assemble outside jobs/ so the web scheduler cannot observe incomplete entries.
    staging = WEB/'data/queue-staging'/BATCH
    staging.mkdir(parents=True,exist_ok=True)
    results = []
    start = time.time()
    for index,(slug,task_id,label) in enumerate(TASKS):
        folder = ROOT/('wildclaw-'+slug)
        provenance = json.loads((folder/'provenance.json').read_text())
        identity = time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]
        job = staging/identity
        job.mkdir()
        shutil.copytree(folder/'input',job/'input')
        guidance = ('The logical /tmp_workspace path means YOUR supplied current workspace. Resolve task paths '
                    'against your supplied working directory. Do not inspect benchmark grading, ground truth, '
                    'provenance, other jobs, or private files outside your workspace. Solve only from the original '
                    'task, its input files, public sources and measured feedback. curl and pdftotext are available. '
                    'A Python with PDF/text parsing tools is available at '+str(ROOT/'pdf-tools-venv/bin/python')+'.')
        config = {'backend':{'type':'pi','provider':'zhizengzeng','model':'deepseek-v4.1-flash',
                  'args':['--offline','--no-extensions','--no-skills','--no-context-files','--append-system-prompt',guidance]},
                  'limits':{'max_seconds':10800,'agent_timeout':None,'verifier_timeout':None,
                            'max_agent_calls':10000,'max_verifier_calls':10000,'max_rounds':1000,
                            'parallelism':2,'verification_repeats':2},
                  'verifier':{'command':['/usr/bin/python3',str(ROOT/'grade_three_tasks.py'),'{workspace}',str(folder)],
                              'mode':'json','check_ids':['official_score'],'authority':'user_provided'}}
        write(job/'config.json',config)
        meta = {'id':identity,'question':(folder/'prompt-original.txt').read_text().strip(),
                'display_question':f'排队 {index+1}/3 · {label}（最多3小时）','mode':'custom',
                'created_at':start+index*.001,'status':'queued','fixture':False,'isolated_memory':True,
                'queue_batch':BATCH,'queue_order':index+1,
                'attachments':[{'name':p.name,'bytes':p.stat().st_size,'kind':'benchmark input'}
                               for p in sorted((job/'input').iterdir()) if p.is_file()],
                'benchmark':provenance}
        write(job/'job.json',meta)
        results.append({'id':identity,'task_id':task_id,'label':label,'max_seconds':10800,
                        'url':f'http://10.110.158.144:18766/?job={identity}#evolution'})
    # Metadata timestamps preserve FIFO order, even if the first task starts immediately.
    for result in results:
        (staging/result['id']).rename(WEB/'data/jobs'/result['id'])
    write(receipt,{'batch':BATCH,'policy':'baseline_floor_v2','mode':'FIFO; advance on completion or 3h deadline','jobs':results})
    print(receipt.read_text())


if __name__=='__main__':main()
