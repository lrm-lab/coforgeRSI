"""Map official fixture paths into an RSI workspace; preserve upstream score math."""
import hashlib
import json
from pathlib import Path
import sys


def grade(workspace, folder):
    provenance = json.loads((folder/'provenance.json').read_text())
    source = (folder/'grader_upstream.py').read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=provenance['grader_sha256']:
        raise ValueError('Official grader hash changed')
    gt = folder/'private/gt'
    input_names = {p.name for p in (folder/'input').iterdir() if p.is_file()}
    for name, expected in provenance['fixture_sha256'].items():
        if name in input_names:continue
        p = gt/name
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('Private reference missing or changed: '+name)
    if 'task_2_' in provenance['task_id']:
        source = source.replace('Path("/tmp_workspace") / "gt"',f'Path({str(gt)!r})')
    elif 'task_3_' in provenance['task_id']:
        source = source.replace('gt_dir = workspace / "gt"',f'gt_dir = Path({str(gt)!r})')
    elif 'task_10_' in provenance['task_id']:
        source = source.replace('Path("/tmp_workspace") / "gt"',f'Path({str(gt)!r})')
    source = source.replace('/tmp_workspace',str(workspace))
    namespace = {}
    exec(compile(source,str(folder/'grader_upstream.py'),'exec'),namespace)
    scores = namespace['grade'](workspace_path=str(workspace),transcript=[])
    # A missing candidate output is a real task failure, not a broken verifier.
    if 'error' in scores and any(s in str(scores['error']) for s in ('gt_dir','ground_truth')):
        raise ValueError(scores['error'])
    if not isinstance(scores.get('overall_score'),(int,float)):
        raise ValueError('Official grader did not return an overall score')
    score = scores['overall_score']
    checks = [{'id':'official_score','passed':score>=1-1e-9,
               'evidence':json.dumps(scores,ensure_ascii=False)}]
    return {'passed':checks[0]['passed'],'score':score,'checks':checks,
            'feedback':'WildClawBench 原 grader：'+json.dumps(scores,ensure_ascii=False),
            'metrics':{'original_scores':scores,'benchmark':'WildClawBench','task_id':provenance['task_id'],
                       'grader_sha256':provenance['grader_sha256'],'adaptation':provenance['adaptation']}}


if __name__=='__main__':
    print(json.dumps(grade(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve()),ensure_ascii=False))
