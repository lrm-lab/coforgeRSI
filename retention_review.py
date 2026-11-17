"""Review completed runs under an explicitly requested policy without rewriting evidence."""
import copy
import hashlib
import json
import time
from pathlib import Path

from general_rsi.protocol import RETENTION_POLICY, retention_gate
from general_rsi.storage import digest, write_json


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def evidence_hashes(goal):
    paths = [goal/'state.json', *sorted((goal/'attempts').glob('*.json'))]
    return {str(p.relative_to(goal)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def project(goal, state, review):
    """Recompute every verdict from original attempts; never trust an edited verdict."""
    if not review or review.get('policy') != RETENTION_POLICY:
        return state
    if review.get('evidence_sha256') != evidence_hashes(goal):
        return state
    result = copy.deepcopy(state)
    rows = {p.stem: read(p, {}) for p in (goal/'attempts').glob('*.json')}
    baseline = [rows[r['id']] for r in state.get('baseline', []) if r['id'] in rows]
    if not baseline:
        return state
    seal = state.get('verifier_seal')
    retained, choices = [], []
    for round_record in result.get('rounds', []):
        for candidate in round_record.get('candidates', []):
            identity = candidate['id']
            validations = sorted((r for r in rows.values() if r.get('experience_id') == identity
                                  and r.get('stage') == 'validation'), key=lambda r: r['id'])
            observed = rows.get(identity)
            expected = digest(candidate['experience'].strip())
            evidence = [*baseline, *validations, *([observed] if observed else [])]
            if (not seal or not observed or not observed.get('feedback', {}).get('valid')
                or observed.get('experience_sha256') != expected
                or any(r.get('feedback', {}).get('verifier_seal') != seal for r in evidence)
                or any(r.get('experience_sha256') != expected for r in validations)):
                continue
            parent = result.get('retained', {}).get(candidate['parent_id'], [])
            gate = retention_gate(validations, parent, baseline)
            if not gate['accepted']:
                continue
            evaluations = round_record.setdefault('evaluated', [])
            original = next((g for g in evaluations if g.get('candidate_id') == identity), None)
            evaluations[:] = [g for g in evaluations if g.get('candidate_id') != identity]
            evaluations.append({**gate, 'candidate_id': identity, 'original_gate': original,
                                'reviewed_at': review['reviewed_at'],
                                'validation_refs': [r['id'] for r in validations]})
            result.setdefault('experiences', {})[identity] = candidate
            result.setdefault('retained', {})[identity] = validations
            retained.append(identity)
            passing = [r for r in [observed, *validations] if r['feedback']['valid'] and r['feedback']['passed']]
            if passing:
                best = max(passing, key=lambda r: (r['feedback']['score'], r.get('stage') == 'validation'))
                choices.append((gate['validation_mean_score'], candidate, best))
    if not retained:
        return state
    result['retention_policy'] = RETENTION_POLICY
    result['_retention_review'] = {**review, 'original_status': state.get('status'),
                                   'retained_candidates': retained}
    if choices:
        _, selected, best = max(choices, key=lambda c: c[0])
        result.update(status='solved', current_experience=selected['id'],
                      final_experience=selected['id'], answer_attempt=best['id'],
                      reason='按新规则完成：两次有效复测均不低于本次初始 baseline，已选取通过验收的产物。',
                      completed_at=review['reviewed_at'])
        result['_review_answer'] = best.get('answer', '')
    return result


def load_state(goal):
    goal = Path(goal)
    state = read(goal/'state.json', {})
    return project(goal, state, read(goal/'retention-review.json'))


def write_review(goal):
    goal = Path(goal)
    state = read(goal/'state.json', {})
    if state.get('status') in ('ready', 'running'):
        raise ValueError('Review requires a stopped run')
    review = {'policy': RETENTION_POLICY, 'reviewed_at': time.time(),
              'reason': 'User requested baseline-preserving retention; no new model or verifier calls.',
              'evidence_sha256': evidence_hashes(goal)}
    projected = project(goal, state, review)
    if '_retention_review' not in projected:
        raise ValueError('No candidate meets the requested policy')
    write_json(goal/'retention-review.json', review)
    return projected


if __name__ == '__main__':
    import sys
    result = write_review(sys.argv[1])
    print(json.dumps({k: result.get(k) for k in ['status', 'final_experience', 'answer_attempt', 'agent_calls']}, ensure_ascii=False))
