"""Visualize the GAIA evidence actually included in the recorded demo package."""
from evolution_evidence import version_label


def summarize(summary, events):
    gates = {e['candidate_id']:e for e in events if e['type']=='experience_retained'}
    attempts=[]
    completed={e.get('attempt_id'):e['time'] for e in events if e['type']=='environment_result'}
    for row in summary.get('attempts',[]):
        identity=row['id'];method=identity.split('-validation-')[0]
        attempts.append({**row,'experience_id':method,'recorded_at':completed.get(identity,0),
            'score':row['score'] if row['valid'] else None,'tokens':row.get('total_tokens'),
            'diagnostic':'原试跑设置的 180 秒单次上限导致执行未完成，未形成有效验收。' if row['timed_out'] else '',
            'feedback_summary':'验收通过，得分 1.0000。' if row['passed'] else '执行达到原试跑的 180 秒上限，未形成有效验收。',
            'checks':[],'components':[],'failed_items':[]})
    attempts.sort(key=lambda a:a['recorded_at'])
    methods=['baseline']+sorted({a['experience_id'] for a in attempts if a['experience_id']!='baseline'})
    aliases={identity:version_label(i) for i,identity in enumerate(methods)}
    for row in attempts:row['version_label']=aliases[row['experience_id']]
    versions=[]
    for identity in methods:
        related=[a for a in attempts if a['experience_id']==identity]
        main=next((a for a in related if a['stage']!='validation'),None)
        parent=gates.get(identity,{}).get('parent_id','baseline') if identity!='baseline' else None
        versions.append({'id':identity,'label':aliases[identity],'parent_id':parent,
            'parent_label':aliases.get(parent),'depth':int(parent is not None),'kind':'recorded_method',
            'status':'retained' if identity in gates else 'execution_incomplete',
            'result':main,'validation_results':[a for a in related if a['stage']=='validation'],
            'observation':'原试跑未在 180 秒内交付有效结果。' if parent else '',
            'hypothesis':'报告摘要：先提取可核验事实、优先形成证明并落盘产物，限制可选搜索。' if identity in gates else '',
            'experience':'回放包未包含完整方法正文，无法提供逐行方法差异。',
            'evidence_refs':['baseline'] if parent else [],'diff':'','attempt_refs':[a['id'] for a in related]})
    return {'code':'recorded_improvement','title':'已完成的 RSI 实测 · 修订方法通过两次新工作区复测',
        'description':'初始执行超时未完成；B 版通过验收并复测两次，C 版超时未完成。原试跑有 180 秒单次限制。',
        'mechanism':'固定基础模型，通过环境反馈修订执行方法并保留经验。','model':'deepseek-v4.1-flash',
        'baseline_note':'回放保留原试跑条件，不是当前现场调用。','baseline_origin':'agent_execution',
        'limitation':'回放包包含执行结果与保留事件，未包含完整方法正文，不能展示真实逐行方法差异。',
        'sealed':True,'baseline':next((a for a in attempts if a['id']=='baseline'),None),
        'baseline_score':None,'best_score':1,'latest_result':attempts[-1] if attempts else None,
        'current_activity':'实测已结束，展示已保存的真实记录。','active_calls':[],
        'revision_count':len(methods)-1,'validation_runs':sum(a['stage']=='validation' for a in attempts),
        'confirmed_improvements':len(gates),'attempts':attempts,'versions':versions,'revisions':[],
        'version_note':'回放版本来自实际执行 ID，B/C 同源于 A。复测沿用 B 版；未评分的超时执行不显示为有效零分。',
        'steps':[{'id':key,'label':label,'done':True} for key,label in [
            ('acceptance','固定验收'),('baseline','初始执行与反馈'),('revision','修订执行方法'),
            ('validation','新工作区复测'),('retention','达标后保留经验')]]}
