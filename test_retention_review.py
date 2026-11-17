import copy
import json
import tempfile
import unittest
from pathlib import Path

import server
from general_rsi.storage import digest
from retention_review import evidence_hashes, load_state, write_review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job = Path(self.temp.name)/'20261116-141624-4f74a96d'
        self.goal = self.job/'goal'
        (self.goal/'attempts').mkdir(parents=True)
        self.candidate = {'id':'round-0-candidate-0','parent_id':'baseline',
                          'experience':'revised method','scope':'test','evidence_refs':['baseline']}
        def row(identity, score, stage, method, guidance):
            return {'id':identity,'experience_id':method,'stage':stage,
                    'experience_sha256':digest(guidance), 'answer':'saved passing answer',
                    'feedback':{'valid':True,'passed':score==1,'score':score,'checks':[], 'verifier_seal':'sealed'}}
        base = row('baseline',.9667,'baseline','baseline','')
        self.rows = [base, row('round-0-candidate-0',1,'exploration','round-0-candidate-0','revised method'),
                     row('round-0-candidate-0-validation-0',1,'validation','round-0-candidate-0','revised method'),
                     row('round-0-candidate-0-validation-1',.9667,'validation','round-0-candidate-0','revised method')]
        for r in self.rows:
            (self.goal/'attempts'/f"{r['id']}.json").write_text(json.dumps(r))
        self.state = {'status':'budget_exhausted','agent_calls':24,'verifier_calls':14,'round_index':1,
                      'baseline':[base],'retained':{'baseline':[base]}, 'verifier_seal':'sealed',
                      'experiences':{'baseline':{'experience':''}},
                      'rounds':[{'index':0,'candidates':[self.candidate], 'evaluated':[
                          {'candidate_id':self.candidate['id'],'accepted':False,'reasons':['quality_gain_not_replicated_twice']}]}]}
        (self.goal/'state.json').write_text(json.dumps(self.state))
        (self.goal/'report.json').write_text(json.dumps({'status':'budget_exhausted'}))
        (self.job/'job.json').write_text(json.dumps({'status':'budget_exhausted','created_at':1}))
        self.original = evidence_hashes(self.goal)

    def tearDown(self):
        self.temp.cleanup()

    def test_review_preserves_original_and_selects_real_passing_artifact(self):
        reviewed = write_review(self.goal)
        self.assertEqual(reviewed['status'],'solved')
        self.assertEqual(reviewed['answer_attempt'],'round-0-candidate-0-validation-0')
        self.assertEqual(reviewed['agent_calls'],24)
        self.assertEqual(evidence_hashes(self.goal),self.original)
        self.assertEqual(load_state(self.goal)['status'],'solved')
        self.assertEqual(server.status(self.job)['status'],'solved')
        detail = server.detail(self.job)
        self.assertEqual(detail['answer'],'saved passing answer')
        self.assertEqual(detail['evolution']['confirmed_improvements'],1)
        self.assertTrue(detail['evolution']['revisions'][0]['gate']['original_gate'])

    def test_changed_evidence_invalidates_review(self):
        write_review(self.goal)
        (self.goal/'attempts/baseline.json').write_text('{}')
        self.assertEqual(load_state(self.goal)['status'],'budget_exhausted')

    def test_changed_method_or_verifier_cannot_be_reviewed_as_retained(self):
        for field in ['experience_sha256','verifier_seal']:
            row = copy.deepcopy(self.rows[-1])
            if field == 'verifier_seal':row['feedback'][field] = 'changed'
            else:row[field] = 'changed'
            p = self.goal/'attempts'/f"{row['id']}.json"
            p.write_text(json.dumps(row))
            with self.assertRaises(ValueError):write_review(self.goal)
            p.write_text(json.dumps(self.rows[-1]))


if __name__ == '__main__':
    unittest.main()
