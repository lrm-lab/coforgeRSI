"""Read-only presentation of retained methods and unfinished task outputs."""
from execution_trace import safe_text


def present(state, evolution, groups, running=False):
    experiences = state.get('experiences', {})
    methods = []
    for version in evolution.get('versions', []):
        identity = version['id']
        if version.get('status') != 'retained' or identity == 'baseline':
            continue
        text = experiences.get(identity, {}).get('experience', '')
        if not text:
            continue
        methods.append({'id': identity, 'label': version['label'],
            'experience': safe_text(text, max(16000, len(text))),
            'validation_results': version.get('validation_results', []),
            'baseline_score': evolution.get('baseline_score')})
    final = state.get('final_experience')
    selected = final or state.get('current_experience')
    if not final and selected not in {m['id'] for m in methods}:
        selected = methods[-1]['id'] if methods else None
    method = experiences.get(selected, {}).get('experience', '')
    # Prefer a completed repeat of the selected method, then the best measured
    # attempt. Never turn a partial workspace into a verified final answer.
    candidates = []
    for attempt in evolution.get('attempts', []):
        group = next((g for g in groups if g['id'] == 'workspace-' + attempt['id'] and g['files']), None)
        if group and attempt.get('valid') and isinstance(attempt.get('score'), (int, float)):
            candidates.append((attempt, group))
    choice = max(candidates, key=lambda pair: (
        pair[0].get('experience_id') == selected,
        pair[0]['score'], pair[0].get('stage') == 'validation',
        pair[0].get('recorded_at') or 0), default=None)
    preferred = next((g for g in groups if g.get('final')), None)
    if preferred is None and not running and choice:
        preferred = choice[1]
    summary = ''
    if not running and state.get('status') != 'solved' and (methods or candidates):
        summary = f'任务尚未通过全部验收；已保存 {len(methods)} 份经复测保留的经验。'
        if choice:
            row = choice[0]
            summary += f"\n所展示产物的验收分数：{row['score']:.4f}；执行记录：{row['id']}。"
            failed = [item.get('label', item.get('id', '')) for item in row.get('failed_items', [])]
            if failed:
                summary += '\n待解决：' + '、'.join(failed) + '。'
        summary += '\n已保留经验和阶段产物可在下方查看、下载。'
    files = preferred['files'] if preferred else []
    deliverables = [f for f in files if f['name'].split('/')[0] in ('results','output','outputs','artifacts')
                    or f['name'].rsplit('/',1)[-1] in ('answer.md','report.md','summary.md','result.json','index.html')]
    if not deliverables:
        inputs = {f['name'] for g in groups if g['id'] == 'input' for f in g['files']}
        deliverables = [f for f in files if f['name'] not in inputs]
    return {'retained_experiences': methods, 'method_experience_id': selected,
            'method': safe_text(method, max(16000, len(method))),
            'method_is_final': bool(final), 'partial_summary': summary,
            'preferred_workspace': preferred['id'] if preferred else None,
            'partial_artifacts': [] if running or final or preferred is None else [
                {'name': f['name'], 'size': f['size'], 'url': f['download_url'],
                 'partial': True} for f in deliverables[:100]]}
