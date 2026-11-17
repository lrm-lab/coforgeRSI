"""Fetch official prompts, unchanged graders and fixtures for a three-task queue."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parent
TASKS = [
    ('task_2_table_tex_download','01_Productivity_Flow_task_2_table_tex_download','论文原始 LaTeX 表格恢复'),
    ('task_3_bibtex','01_Productivity_Flow_task_3_bibtex','受损 PDF 识别、重命名与 BibTeX 恢复'),
    ('task_10_pdf_digest','01_Productivity_Flow_task_10_pdf_digest','65 篇论文分类与表格提取'),
]


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def prepare(task):
    slug, identity, label = task
    folder = ROOT/('wildclaw-'+slug)
    (folder/'input').mkdir(parents=True,exist_ok=True)
    (folder/'private/gt').mkdir(parents=True,exist_ok=True)
    source_path = 'tasks/01_Productivity_Flow/'+identity+'.md'
    raw = fetch('https://raw.githubusercontent.com/InternLM/WildClawBench/main/'+source_path)
    text = raw.decode()
    prompt = text.split('## Prompt\n',1)[1].split('\n## Expected Behavior',1)[0].strip()
    grader = re.search(r'## Automated Checks\s*```python\n(.*?)\n```',text,re.S)[1]+'\n'
    (folder/'task-upstream.md').write_bytes(raw)
    (folder/'prompt-original.txt').write_text(prompt+'\n')
    (folder/'grader_upstream.py').write_text(grader)
    tree = json.loads(fetch('https://huggingface.co/api/datasets/internlm/WildClawBench/tree/main/'
                           'workspace/01_Productivity_Flow/'+slug+'?recursive=true&limit=1000'))
    items = [x for x in tree if x['type']=='file' and '/'+slug+'/' in x['path']
             and any('/'+part+'/' in x['path'] for part in ('exec','gt')) and not Path(x['path']).name.startswith('.')]
    def download(item):
        name = Path(item['path']).name
        target = folder/('input' if '/exec/' in item['path'] else 'private/gt')/name
        expected = item.get('lfs',{}).get('oid')
        if target.exists() and target.stat().st_size==item['size']:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected and actual==expected:return name,actual
            if not expected:
                data=target.read_bytes()
                if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==item['oid']:return name,actual
        url = 'https://huggingface.co/datasets/internlm/WildClawBench/resolve/main/'+item['path']+'?download=true'
        partial = target.with_name(target.name+'.part')
        digest = hashlib.sha256()
        with urllib.request.urlopen(url,timeout=180) as response, partial.open('wb') as stream:
            while chunk:=response.read(1024*1024):
                digest.update(chunk);stream.write(chunk)
        actual = digest.hexdigest()
        blob_ok = True if expected else hashlib.sha1(b'blob '+str(partial.stat().st_size).encode()+b'\0'+partial.read_bytes()).hexdigest()==item['oid']
        if partial.stat().st_size!=item['size'] or expected and expected!=actual or not blob_ok:
            raise ValueError('Fixture size/hash mismatch: '+name)
        partial.replace(target)
        print(identity,name,'downloaded',flush=True)
        return name,actual
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        hashes = dict(pool.map(download,items))
    provenance = {'suite':'WildClawBench','task_id':identity,'category':'01_Productivity_Flow',
                  'label':label,'source_url':'https://github.com/InternLM/WildClawBench/blob/main/'+source_path,
                  'task_sha256':hashlib.sha256(raw).hexdigest(),
                  'grader_sha256':hashlib.sha256(grader.encode()).hexdigest(),
                  'fixture_sha256':hashes,
                  'adaptation':'Workspace and private ground-truth paths only; official scoring unchanged.',
                  'note':'官方题目、附件及原评分器；本次采用 RSI/Pi、每题最多三小时和 baseline 底线采纳规则，非官方限时排行榜复现。'}
    (folder/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    print(identity,'READY',flush=True)


if __name__=='__main__':
    for task in TASKS:prepare(task)
