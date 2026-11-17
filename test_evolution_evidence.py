import json
from pathlib import Path
import tempfile
import unittest

import server
from evolution_evidence import summarize, attempt_summary
import historical_evidence
import recorded_evidence
from general_rsi.protocol import retention_gate
from general_rsi.storage import digest


class EvolutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.goal=Path(self.temp.name)
        (self.goal/'attempts').mkdir()
        self.state={'status':'solved','bootstrapped':True,'verifier_seal':'sealed-checks',
                    'verifier':{'authority':'user_provided'},'backend':{'model':'test-model'},
                    'experiences':{'baseline':{'experience':'initial method','recalled_refs':[]}},
                    'retained':{},'rounds':[],'round_index':0}

    def tearDown(self):self.temp.cleanup()

    def row(self,identity,method,stage,score,tokens=100,guidance='initial method'):
        row={'id':identity,'experience_id':method,'experience_sha256':digest(guidance),'stage':stage,
             'feedback':{'valid':True,'passed':score==1,'score':score,'verifier_seal':'sealed-checks','checks':[]},
             'usage':{'total_tokens':tokens}}
        (self.goal/'attempts'/f'{identity}.json').write_text(json.dumps(row))
        return row

    def improved(self,base_score=0,new_score=1,base_tokens=100,new_tokens=100):
        identity='round-0-candidate-0';text='revised method'
        baseline=self.row('baseline','baseline','baseline',base_score,base_tokens)
        observed=self.row(identity,identity,'exploration',new_score,new_tokens,text)
        validations=[self.row(identity+f'-validation-{i}',identity,'validation',new_score,new_tokens,text) for i in range(2)]
        candidate={'id':identity,'parent_id':'baseline','experience':text,'observation':'observed failure',
                   'hypothesis':'test a new method','evidence_refs':['baseline']}
        gate={**retention_gate(validations,[baseline]),'candidate_id':identity,'validation_refs':[r['id'] for r in validations]}
        self.state.update(retained={'baseline':[baseline],identity:validations},round_index=1,
            rounds=[{'index':0,'candidates':[candidate],'decision':{'reason':'feedback'},'evaluated':[gate]}])
        return validations

    def test_initial_success_and_repeated_validation_are_not_method_improvement(self):
        baseline=self.row('baseline','baseline','baseline',1)
        for i in range(2):self.row(f'baseline-validation-{i}','baseline','validation',1)
        self.state['retained']={'baseline':[baseline]}
        e=summarize(self.goal,self.state)
        self.assertEqual(e['code'],'initial_verified')
        self.assertEqual(e['revision_count'],0);self.assertEqual(e['confirmed_improvements'],0)
        self.assertEqual(e['validation_runs'],2)
        self.assertEqual([v['label'] for v in e['versions']],['A'])
        self.assertEqual({a['version_label'] for a in e['attempts']},{'A'})

    def test_protocol_retries_are_not_revision_evidence(self):
        (self.goal/'calls/bootstrap-protocol-2').mkdir(parents=True)
        self.state.update(status='running',bootstrapped=False)
        e=summarize(self.goal,self.state)
        self.assertEqual(e['code'],'planning');self.assertEqual(e['revision_count'],0)
        self.assertEqual(len(e['versions']),1)

    def test_failed_repeat_is_attached_to_initial_version_without_inventing_revision(self):
        self.row('baseline','baseline','baseline',1)
        self.row('baseline-validation-0','baseline','validation',.5)
        e=summarize(self.goal,self.state)
        self.assertEqual(e['versions'][0]['status'],'failed')
        self.assertEqual(len(e['versions']),1)
        self.assertIn('baseline-validation-0',e['versions'][0]['observation'])

    def test_retained_improvement_requires_same_method_checks_and_real_gain(self):
        self.improved()
        e=summarize(self.goal,self.state)
        self.assertEqual(e['code'],'validated_improvement')
        self.assertEqual(e['confirmed_improvements'],1)
        self.assertTrue(e['revisions'][0]['accepted'])

    def test_wrong_method_or_changed_acceptance_cannot_confirm_improvement(self):
        validations=self.improved()
        for field,value in [('experience_sha256','different-method')]:
            validations[0][field]=value
        path=self.goal/'attempts'/f"{validations[0]['id']}.json"
        path.write_text(json.dumps(validations[0]))
        self.assertEqual(summarize(self.goal,self.state)['confirmed_improvements'],0)
        validations[0]['experience_sha256']=digest('revised method')
        validations[0]['feedback']['verifier_seal']='changed-checks'
        path.write_text(json.dumps(validations[0]))
        self.assertEqual(summarize(self.goal,self.state)['confirmed_improvements'],0)

    def test_missing_or_single_validation_cannot_confirm_improvement(self):
        validations=self.improved()
        (self.goal/'attempts'/f"{validations[1]['id']}.json").unlink()
        self.assertEqual(summarize(self.goal,self.state)['confirmed_improvements'],0)

    def test_quality_preserving_token_gain_is_labelled_from_measurement(self):
        self.improved(base_score=1,new_score=1,base_tokens=200,new_tokens=100)
        e=summarize(self.goal,self.state)
        self.assertTrue(e['revisions'][0]['measured_gate']['token_gain'])
        self.assertEqual(e['confirmed_improvements'],1)

    def test_preset_artifact_demo_is_not_a_model_baseline_comparison(self):
        self.improved();self.state['baseline_existing']=True
        e=summarize(self.goal,self.state)
        self.assertEqual(e['baseline_origin'],'existing_artifact')
        self.assertIn('流程',e['title']);self.assertIn('未测量初始模型方法',e['description'])

    def test_pending_plan_is_visible_without_retention_claim(self):
        self.state['status']='running'
        plans=self.goal/'plans';plans.mkdir()
        (plans/'round-0.json').write_text(json.dumps({'reason':'failed check','candidates':[
            {'parent_id':'baseline','experience':'new method','observation':'failure','hypothesis':'improve','evidence_refs':['baseline']}]}))
        e=summarize(self.goal,self.state)
        self.assertEqual(e['code'],'revision_unverified');self.assertEqual(e['confirmed_improvements'],0)
        self.assertEqual(e['revisions'][0]['status'],'testing')
        self.state['status']='budget_exhausted'
        self.assertEqual(summarize(self.goal,self.state)['revisions'][0]['status'],'execution_incomplete')

    def test_version_graph_keeps_sibling_branches_and_actual_parent(self):
        self.improved()
        first=self.state['rounds'][0]
        first['candidates'].append({'id':'round-0-candidate-1','parent_id':'baseline','experience':'another method'})
        self.state['rounds'].append({'index':1,'candidates':[
            {'id':'round-1-candidate-0','parent_id':'round-0-candidate-0','experience':'child method'}],
            'decision':{},'evaluated':[]})
        e=summarize(self.goal,self.state)
        versions={v['label']:v for v in e['versions']}
        self.assertEqual(set(versions),{'A','B','C','D'})
        self.assertEqual(versions['B']['parent_label'],'A')
        self.assertEqual(versions['C']['parent_label'],'A')
        self.assertEqual(versions['D']['parent_label'],'B')
        self.assertEqual(versions['D']['depth'],2)
        self.assertTrue(versions['B']['diff'])

    def test_partial_benchmark_has_readable_feedback_and_exact_failed_item(self):
        row={'feedback':{'valid':True,'passed':False,'score':.9667,'metrics':{'original_scores':{
            'classify_score':1,'metadata_score':.9167,'interest_score':1,'metadata_dr_seg':0,'overall_score':.9667}}}}
        a=attempt_summary(row)
        self.assertEqual(len(a['components']),3)
        self.assertEqual([c['id'] for c in a['failed_items']],['metadata_dr_seg'])
        self.assertIn('得分 0.9667',a['feedback_summary']);self.assertIn('1 项待修订',a['feedback_summary'])

    def test_archived_history_keeps_rejected_proposal_and_actual_parent_diffs(self):
        root=Path(__file__).resolve().parent
        archive=root/'benchmarks/wildclaw-arxiv-digest/historical-progress.json.gz'
        ref=json.loads((archive.parent/'historical-reference.json').read_text())
        h=historical_evidence.enrich({'historical_reference':ref},archive)['historical_reference']
        self.assertTrue(h['source_verified'])
        nodes={v['id']:v for v in h['versions']};self.assertEqual(len(nodes),6)
        self.assertEqual(nodes['e1']['status'],'text_rejected');self.assertFalse(nodes['e1']['executed'])
        self.assertEqual(nodes['e1']['scores'],[]);self.assertEqual(nodes['e1']['diff'],'')
        self.assertEqual(nodes['e3']['parent_id'],'e2');self.assertEqual(nodes['e4']['parent_id'],'e2')
        self.assertEqual(nodes['e5']['parent_id'],'e4');self.assertIn('--- e4',nodes['e5']['diff'])
        self.assertEqual(nodes['e5']['status'],'retained')
        # Changing the claimed source hash must hide enriched evidence, not trust the archive.
        ref['source_sha256']='wrong'
        failed=historical_evidence.enrich({'historical_reference':ref},archive)['historical_reference']
        self.assertIn('evidence_error',failed);self.assertNotIn('versions',failed)

    def test_gaia_replay_uses_recorded_edges_and_does_not_fabricate_method_diff(self):
        root=Path(__file__).resolve().parent
        s=json.loads((root/'evidence/result-summary.json').read_text())
        events=[json.loads(line) for line in (root/'evidence/events.jsonl').read_text().splitlines()]
        e=recorded_evidence.summarize(s,events)
        self.assertEqual([v['parent_label'] for v in e['versions']],[None,'A','A'])
        self.assertEqual(e['confirmed_improvements'],1);self.assertEqual(e['validation_runs'],2)
        self.assertIsNone(e['baseline_score']);self.assertTrue(all(not v['diff'] for v in e['versions']))


if __name__=='__main__':unittest.main(verbosity=2)
