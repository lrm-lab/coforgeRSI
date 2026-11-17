"""Explain the recorded RSI mechanism without equating task success with improvement."""
import difflib
import json
import re

from execution_trace import safe_text
from general_rsi.protocol import retention_gate, retention_gate_legacy, RETENTION_POLICY
from general_rsi.storage import digest
from general_rsi.backend import process_alive

ITEM_LABELS = {'classify_score':'论文分类','metadata_score':'元数据审计','interest_score':'推荐与对比表',
    'metadata_dr_seg':'Dr. Seg 元数据','metadata_nolan':'NoLan 元数据',
    'metadata_global_local_dual_perception':'Global-Local Dual Perception 元数据',
    'metadata_dynamic_multimodal_activation_steering':'Dynamic Multimodal Activation Steering 元数据',
    'metadata_appendix_flags':'附录识别','metadata_split_consistency':'主文/附录计数一致性',
    'integer_domain':'整数取值范围','minimum':'全域最小值','mae_pdf_valid':'MAE 论文 PDF',
    'rule_resnet_summary_preserved':'原 ResNet 摘要保护','rule_mae_summary_exist':'独立 MAE 摘要',
    'llm_mae_summary_quality':'MAE 摘要质量'}


def item_label(key):
    return ITEM_LABELS.get(key, key.replace('metadata_','').replace('_',' '))


def read(path, default=None):
    try:return json.loads(path.read_text())
    except (OSError, ValueError):return default


def attempt_summary(row):
    feedback = row.get('feedback') or {}
    original = (feedback.get('metrics') or {}).get('original_scores') or {}
    aggregates = {'classify_score','metadata_score','interest_score'}
    numeric = {k:v for k,v in original.items() if isinstance(v,(int,float)) and not isinstance(v,bool) and 0<=v<=1}
    components = [{'id':k,'label':item_label(k),'score':numeric[k]} for k in aggregates if k in numeric]
    components.sort(key=lambda x:['classify_score','metadata_score','interest_score'].index(x['id']))
    failed = [{'id':k,'label':item_label(k),'score':v} for k,v in numeric.items()
              if k not in aggregates|{'overall_score'} and v<1-1e-9]
    if not numeric:
        failed = [{'id':c.get('id'),'label':item_label(c.get('id','check')),'score':None}
                  for c in feedback.get('checks',[]) if not c.get('passed')]
    score = feedback.get('score')
    if not feedback.get('valid'):summary='未形成有效验收，需查看执行或验收记录。'
    elif feedback.get('passed'):summary=f'得分 {score:.4f}，验收通过。' if isinstance(score,(int,float)) else '验收通过。'
    else:summary=(f'得分 {score:.4f}，' if isinstance(score,(int,float)) else '')+(
        f'有 {len(failed)} 项待修订：'+ '、'.join(c['label'] for c in failed[:5]) if failed else '尚未满足全部验收要求。')
    return {'id':row.get('id'), 'experience_id':row.get('experience_id'), 'stage':row.get('stage'),
            'experience_sha256':row.get('experience_sha256'), 'valid':bool(feedback.get('valid')),
            'passed':bool(feedback.get('passed')), 'score':feedback.get('score'),
            'tokens':(row.get('usage') or {}).get('total_tokens'),
            'recorded_at':row.get('_recorded_time'), 'components':components,'failed_items':failed,
            'feedback_summary':summary,
            'verifier_seal':feedback.get('verifier_seal'),
            'diagnostic':safe_text(feedback.get('diagnostic',''),3000),
            'checks':[{'id':c.get('id'),'passed':c.get('passed'),
                       'evidence':safe_text(c.get('evidence',''),1800)} for c in feedback.get('checks',[])]}


def version_label(index):
    label = ''
    while True:
        index, digit = divmod(index,26)
        label = chr(65+digit)+label
        if not index:return label
        index -= 1


def method_diff(old, new, parent, identity):
    # Guidance is often a single line without a final newline; normalize for readable +/- lines.
    return ''.join(difflib.unified_diff([line+'\n' for line in old.splitlines()],
        [line+'\n' for line in new.splitlines()],fromfile=str(parent),tofile=identity))


def summarize(goal, state):
    raw_rows = [{**read(p,{}),'_recorded_time':p.stat().st_mtime} for p in sorted((goal/'attempts').glob('*.json'))]
    rows = {r['id']:r for r in raw_rows if r.get('id')}
    experiences = state.get('experiences',{})
    baseline = rows.get('baseline')
    seal = state.get('verifier_seal')
    evaluations = {g['candidate_id']:g for r in state.get('rounds',[]) for g in r.get('evaluated',[]) if g.get('candidate_id')}
    proposals = {}
    for r in state.get('rounds',[]):
        for c in r.get('candidates',[]):proposals[c['id']] = (r['index'],c,r.get('decision',{}).get('reason',''))
    # A plan is persisted before its candidate executions finish.
    for p in sorted((goal/'plans').glob('round-*.json')):
        match = re.fullmatch(r'round-(\d+)\.json',p.name)
        if not match:continue
        plan = read(p,{})
        for n,c in enumerate(plan.get('candidates',[])):
            index = int(match[1]);identity=f'round-{index}-candidate-{n}'
            proposals.setdefault(identity,(index,{**c,'id':identity},plan.get('reason','')))
    revisions = []
    confirmed = []
    for identity,(index,candidate,reason) in sorted(proposals.items(),key=lambda item:(item[1][0],int(item[0].rsplit('-',1)[-1]))):
        parent = experiences.get(candidate.get('parent_id'),{})
        old = parent.get('experience','')
        new = candidate.get('experience','')
        gate = evaluations.get(identity)
        refs = (gate or {}).get('validation_refs',[])
        validations = [rows[ref] for ref in refs if ref in rows]
        parent_rows = [rows[p['id']] for p in state.get('retained',{}).get(candidate.get('parent_id'),[])
                       if p.get('id') in rows]
        same_method = len(validations)>=2 and len({r['id'] for r in validations})==len(validations) and all(
            r.get('stage')=='validation' and r.get('experience_id')==identity
            and r.get('experience_sha256')==digest(new.strip()) for r in validations)
        same_checks = bool(seal) and all((r.get('feedback') or {}).get('verifier_seal')==seal for r in validations+parent_rows)
        if parent_rows and validations:
            measured = (retention_gate(validations,parent_rows,[baseline] if baseline else [])
                        if (gate or {}).get('policy') == RETENTION_POLICY
                        else retention_gate_legacy(validations,parent_rows))
        else:measured = None
        accepted = bool(identity in state.get('retained',{}) and gate and gate.get('accepted')
                        and same_method and same_checks and measured and measured['accepted'])
        if accepted:confirmed.append(identity)
        revisions.append({'id':identity,'round':index+1,'parent_id':candidate.get('parent_id'),
            'observation':safe_text(candidate.get('observation',''),3000),
            'hypothesis':safe_text(candidate.get('hypothesis',''),3000),
            'reason':safe_text(reason,3000),'evidence_refs':candidate.get('evidence_refs',[]),
            'before':safe_text(old,6000),'after':safe_text(new,6000),
            'diff':safe_text(method_diff(old,new,candidate.get('parent_id'),identity),16000),
            'validation_refs':refs,'same_method':same_method,'same_checks':same_checks,
            'accepted':accepted,'gate':gate,'measured_gate':measured,
            'status':'retained' if accepted else 'rejected' if gate else 'not_retained' if index<state.get('round_index',0)
                     else 'execution_incomplete' if state.get('status') not in ('ready','running') and identity not in rows else 'testing'})
    baseline_validations = [r for r in raw_rows if r.get('experience_id')=='baseline' and r.get('stage')=='validation']
    initial_verified = bool(seal and baseline and baseline.get('feedback',{}).get('valid') and baseline['feedback'].get('passed')
        and baseline['feedback'].get('verifier_seal')==seal
        and baseline.get('experience_sha256')==digest(experiences.get('baseline',{}).get('experience',''))
        and len(baseline_validations)>=2 and len({r['id'] for r in baseline_validations})==len(baseline_validations)
        and all(r.get('feedback',{}).get('valid') and r['feedback'].get('passed')
                and r.get('experience_sha256')==baseline.get('experience_sha256')
                and r['feedback'].get('verifier_seal')==seal for r in baseline_validations))
    active_calls=[]
    for folder in sorted((goal/'calls').glob('*')):
        if (folder/'result.json').exists():continue
        if process_alive(read(folder/'process.json',{})):
            active_calls.append({'id':folder.name,'role':read(folder/'request.json',{}).get('role')})
    if confirmed:
        code,title='validated_improvement','已发生并验证方法进化'
        description=f'方法经过环境反馈修订，{len(confirmed)} 份新经验通过同一验收下的独立复测和改进门槛。'
        if state.get('retention_policy') == RETENTION_POLICY:
            code,title='validated_retention','方法复测达到 baseline 底线，已保留'
            description=f'{len(confirmed)} 份方法的两次有效复测均不低于本次初始 baseline；允许从候选高分或父方法回落，不要求每次严格提高。'
        if state.get('baseline_existing'):
            title='预置初稿改进流程已完成，并保留执行方法'
            description=f'预置初稿未通过，修订方法的独立复测通过，保留 {len(confirmed)} 份执行方法；这验证了改进流程，未测量初始模型方法的表现。'
    elif state.get('status')=='solved' and initial_verified:
        code,title='initial_verified','初始方法通过复测，本任务未发生方法进化'
        description='初始方法通过环境验收及两次新工作区复测；没有采纳新的方法或经验。'
    elif revisions:
        code,title='revision_unverified','已尝试方法改进，尚无已验证的新经验'
        description='已根据执行证据提出方法修改；以复测和采纳门槛的实际结果为准。'
    elif state.get('status')=='solved':
        code,title='evidence_incomplete','任务已结束，方法进化证据不足'
        description='当前记录未组成完整的方法修订与重复验证证据链。'
    elif baseline and baseline.get('feedback',{}).get('valid') and not baseline['feedback'].get('passed'):
        code,title='revision_planning','A 版已完成验收，仍有待修订项'
        description=attempt_summary(baseline)['feedback_summary']+' 新方法提交后才会出现 B/C 版本。'
    elif state.get('bootstrapped'):
        code,title='baseline_execution','正在执行或验证初始方法'
        description='已确定验收要求；只有完成方法修订、复测并保留新经验，才标记发生方法进化。'
    else:
        code,title='planning','正在建立验收要求，尚未进入方法进化'
        description='模型调用重试、JSON 修复和工具数量不计作方法进化。'
    baseline_origin='existing_artifact' if state.get('baseline_existing') else 'agent_execution'
    aliases = {'baseline':'A', **{r['id']:version_label(i+1) for i,r in enumerate(revisions)}}
    baseline_failures = [r for r in ([baseline] if baseline else [])+baseline_validations
                         if not r.get('feedback',{}).get('valid') or not r.get('feedback',{}).get('passed')]
    baseline_observation = '\n'.join(r['id']+': '+r.get('feedback',{}).get('diagnostic','') for r in baseline_failures)
    versions = [{'id':'baseline','label':'A','parent_id':None,'parent_label':None,'depth':0,
        'kind':'existing_artifact' if state.get('baseline_existing') else 'initial_method',
        'status':'verified' if initial_verified else 'failed' if baseline_failures else 'pending',
        'experience':safe_text(experiences.get('baseline',{}).get('experience',''),6000),
        'observation':safe_text(baseline_observation or (baseline or {}).get('feedback',{}).get('diagnostic',''),3000),
        'hypothesis':'','evidence_refs':[],'diff':'','attempt_refs':[r['id'] for r in raw_rows if r.get('experience_id')=='baseline']}]
    depths = {'baseline':0}
    for r in revisions:
        depth = depths.get(r['parent_id'],0)+1;depths[r['id']]=depth
        versions.append({'id':r['id'],'label':aliases[r['id']], 'parent_id':r['parent_id'],
            'parent_label':aliases.get(r['parent_id'],r['parent_id']),'depth':depth,'kind':'method_revision',
            'status':r['status'],'experience':r['after'],'observation':r['observation'],
            'hypothesis':r['hypothesis'],'evidence_refs':r['evidence_refs'],'diff':r['diff'],
            'attempt_refs':[row['id'] for row in raw_rows if row.get('experience_id')==r['id']]})
    attempts = [attempt_summary(r) for r in raw_rows]
    for row in attempts:row['version_label'] = aliases.get(row['experience_id'],row['experience_id'])
    attempts.sort(key=lambda r:r.get('recorded_at') or 0)
    for version in versions:
        related=[r for r in attempts if r['experience_id']==version['id']]
        main=next((r for r in related if r['stage'] in ('baseline','existing_artifact_baseline','exploration')),None)
        version['result']=main
        version['validation_results']=[r for r in related if r['stage']=='validation']
        version['feedback_summary']=(main or {}).get('feedback_summary','尚未形成验收结果。')
    latest=attempts[-1] if attempts else None
    measured_scores=[r['score'] for r in attempts if r['valid'] and isinstance(r['score'],(int,float))]
    activity=('正在规划下一版方法，尚未提交新的候选。' if any(c['role']=='planner' for c in active_calls)
              else '正在执行：'+ '、'.join(('版本 '+aliases.get(c['id'],aliases.get(c['id'].split('-validation-')[0],c['id']))) for c in active_calls if c['role']=='worker')
              if any(c['role']=='worker' for c in active_calls) else '正在整理验收和执行记录。' if state.get('status')=='running' else '本次运行已结束，以下保留实际记录。')
    return {'code':code,'title':title,'description':description,
        'mechanism':'固定基础模型上的执行方法与经验改进；不修改模型权重。',
        'limitation':'当前任务的重复验收不等于跨任务泛化或模型能力提升。',
        'model':state.get('backend',{}).get('model'),'baseline_origin':baseline_origin,
        'baseline_note':'从预置不合格初稿开始，是流程演示，不是模型前后性能对照。' if baseline_origin=='existing_artifact' else '初始方法由 Agent 从原始输入执行。',
        'recalled_experiences':len(experiences.get('baseline',{}).get('recalled_refs',[])),
        'sealed':bool(seal),'verifier_seal':seal,'verifier_authority':(state.get('verifier') or {}).get('authority'),
        'acceptance':state.get('acceptance',[]),'baseline':attempt_summary(baseline) if baseline else None,
        'attempts':attempts, 'revisions':revisions,'versions':versions,
        'retention_policy':state.get('retention_policy'), 'retention_review':state.get('_retention_review'),
        'retention_policy_note':('采纳规则：两次有效复测均不低于本次 A 版 baseline；允许分数波动和打平，不要求 token 减少。已有通过验收的产物则结束。'
            if state.get('retention_policy') == RETENTION_POLICY else '本次采用启动时的旧采纳规则；历史实验使用各自当时的规则。'),
        'latest_result':latest,'baseline_score':(baseline or {}).get('feedback',{}).get('score'),
        'best_score':max(measured_scores) if measured_scores else None,'current_activity':activity,
        'active_calls':active_calls,
        'version_note':'版本表示执行方法或经验。每个版本连向其实际父版本；同一方法的复测沿用原版本，调用故障重试不产生新版本。',
        'revision_count':len(revisions),'confirmed_improvements':len(confirmed),
        'validation_runs':sum(r.get('stage')=='validation' for r in raw_rows),
        'steps':[{'id':'acceptance','label':'固定验收','done':bool(seal)},
                 {'id':'baseline','label':'初始执行与反馈','done':bool(baseline)},
                 {'id':'revision','label':'修订执行方法','done':bool(revisions)},
                 {'id':'validation','label':'新工作区复测','done':bool(baseline_validations) or any(r['validation_refs'] for r in revisions)},
                 {'id':'retention','label':'达标后保留经验','done':bool(confirmed)}]}
