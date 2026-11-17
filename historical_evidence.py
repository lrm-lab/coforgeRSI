"""Read-only, hash-checked visualization of an archived benchmark history."""
import gzip
import hashlib
import json
from functools import lru_cache

from execution_trace import safe_text
from evolution_evidence import version_label, method_diff


@lru_cache(maxsize=4)
def _load(path, mtime, expected_hash):
    raw = gzip.decompress(path.read_bytes())
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('历史源文件校验不匹配')
    return json.loads(raw)


def enrich(benchmark, archive):
    if not benchmark or not benchmark.get('historical_reference'):return benchmark
    result = {**benchmark};ref = {**benchmark['historical_reference']}
    result['historical_reference'] = ref
    try:
        data = _load(archive, archive.stat().st_mtime_ns, ref['source_sha256'])
        if data['task_id'] != ref['task_id']:raise ValueError('历史题目不匹配')
    except (OSError, ValueError, KeyError) as error:
        ref['evidence_error'] = str(error)
        return result
    lineage = {c['candidate_id']:c for c in data['lineage']}
    gates = {c['candidate_id']:c.get('gate') for c in data['verification_history']}
    revisions = data['revision_attempts']
    aliases = {'baseline':'A', **{c['candidate_id']:version_label(i+1) for i,c in enumerate(revisions)}}
    depths = {'baseline':0}
    # Baseline rows record no injected method. Never use the final appendix as baseline guidance.
    versions = [{'id':'baseline','label':'A','parent_id':None,'parent_label':None,'depth':0,
        'status':'historical_baseline','scores':[r['reward'] for r in data['baseline_rows']],
        'validation_scores':[], 'experience':'','diff':'','changes':[],
        'observation':'历史初始三次执行均正常完成，分数波动较大。','hypothesis':'','reason':''}]
    for revision in revisions:
        identity = revision['candidate_id'];parent = revision['parent']
        record = lineage.get(identity);decision = revision.get('decision') or {}
        old = lineage.get(parent,{}).get('appendix','');new = (record or {}).get('appendix','')
        depths[identity] = depths.get(parent,0)+1
        accepted = identity==data.get('selected_candidate_id') and data.get('gate',{}).get('accepted')
        versions.append({'id':identity,'label':aliases[identity], 'parent_id':parent,
            'parent_label':aliases.get(parent,parent),'depth':depths[identity],
            'status':'retained' if accepted else 'text_rejected' if not record else 'not_retained',
            'executed':bool(record),'text_approved':revision.get('text_approved'),
            'scores':[r['reward'] for r in (record or {}).get('rows',[])],
            'validation_scores':[r['reward'] for r in (record or {}).get('verification_rows',[])],
            'observation':safe_text(decision.get('observation',''),6000),
            'hypothesis':safe_text(decision.get('cause_hypothesis',''),6000),
            'reason':safe_text(decision.get('reason',''),6000),
            'proposed_action':safe_text(decision.get('next_action',''),6000),
            'changes':(record or {}).get('changes',[]), 'experience':safe_text(new,12000),
            'diff':safe_text(method_diff(old,new,parent,identity),24000) if record else '',
            'gate':data.get('gate') if accepted else gates.get(identity),
            'review':(record or {}).get('review')})
    ref.update(versions=versions,source_verified=True,
        revision_note='5 次修订中，e1 未通过文本审查、未执行；e2/e3/e4/e5 实际执行。连线表示真实父方法。')
    return result
