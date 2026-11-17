"""Adapt only the official grader's workspace path and RSI result envelope."""
import hashlib
import json
from pathlib import Path
import sys

CHECK_IDS = ['classify_score','metadata_score','interest_score']


def main():
    workspace = Path(sys.argv[1]).resolve()
    source_file = Path(sys.argv[2])
    provenance = json.loads(Path(sys.argv[3]).read_text())
    raw = source_file.read_text()
    actual_hash = hashlib.sha256(raw.encode()).hexdigest()
    if actual_hash != provenance['grader_sha256']:raise ValueError('Official grader source hash changed')
    # The upstream grader hardcodes /tmp_workspace. This is the same path-only
    # adaptation used by the benchmark suite's external-workspace bridge.
    adapted = raw.replace('/tmp_workspace',str(workspace))
    namespace = {}
    exec(compile(adapted,str(source_file),'exec'),namespace)
    scores = namespace['grade'](workspace_path=str(workspace),transcript=[])
    checks = [{'id':name,'passed':scores.get(name,0)>=1-1e-9,
               'evidence':json.dumps({'value':scores.get(name),'original_criteria':scores},ensure_ascii=False)}
              for name in CHECK_IDS]
    print(json.dumps({'passed':all(c['passed'] for c in checks),'score':scores['overall_score'],
        'checks':checks,'feedback':'WildClawBench 原 grader：'+json.dumps(scores,ensure_ascii=False),
        'metrics':{'benchmark':'WildClawBench','task_id':provenance['task_id'],'original_scores':scores,
            'grader_sha256':actual_hash,'adaptation':'replace logical /tmp_workspace with supplied workspace only',
            'pass_rule':'All three original aggregate components must be 1.0; weighted overall score is unchanged.'}},ensure_ascii=False))


if __name__=='__main__':main()
