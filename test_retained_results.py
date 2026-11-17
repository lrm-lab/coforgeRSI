import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent/'rsi'))
from retained_results import present


class RetainedResultsTests(unittest.TestCase):
    def setUp(self):
        self.state = {'status':'protocol_error', 'current_experience':'v1',
                      'experiences':{'v1':{'experience':'Extract PDF metadata, then verify against arXiv.'}}}
        self.row = {'id':'repeat-1', 'experience_id':'v1', 'valid':True, 'passed':False,
                    'score':.98, 'stage':'validation', 'recorded_at':1,
                    'failed_items':[{'id':'bibtex', 'label':'BibTeX 精确匹配'}]}
        self.evolution = {'baseline_score':.98, 'versions':[{'id':'v1','label':'B','status':'retained',
                          'validation_results':[self.row, {**self.row,'id':'repeat-2'}]}], 'attempts':[self.row]}
        self.groups = [{'id':'workspace-repeat-1','final':False,'files':[
            {'name':'results/manifest.json','size':10,'download_url':'/safe-file'}]}]

    def test_error_preserves_methods_scores_and_partial_downloads_without_mutating_state(self):
        before = copy.deepcopy(self.state)
        result = present(self.state, self.evolution, self.groups)
        self.assertEqual(result['method'], self.state['experiences']['v1']['experience'])
        self.assertEqual(len(result['retained_experiences']), 1)
        self.assertFalse(result['method_is_final'])
        self.assertIn('0.9800',result['partial_summary'])
        self.assertIn('BibTeX 精确匹配',result['partial_summary'])
        self.assertEqual(result['partial_artifacts'][0]['url'],'/safe-file')
        self.assertTrue(result['partial_artifacts'][0]['partial'])
        self.assertEqual(result['preferred_workspace'],'workspace-repeat-1')
        self.assertEqual(self.state,before)

    def test_rejected_method_and_running_drafts_are_not_presented_as_retained_or_final(self):
        self.evolution['versions'][0]['status'] = 'rejected'
        result = present(self.state,self.evolution,self.groups,running=True)
        self.assertFalse(result['method'])
        self.assertEqual(result['retained_experiences'],[])
        self.assertEqual(result['partial_artifacts'],[])
        self.assertEqual(result['partial_summary'],'')

    def test_solved_job_keeps_final_method_and_workspace(self):
        self.state.update(status='solved',final_experience='v1')
        self.groups[0]['final'] = True
        result = present(self.state,self.evolution,self.groups)
        self.assertTrue(result['method_is_final'])
        self.assertEqual(result['partial_artifacts'],[])
        self.assertEqual(result['partial_summary'],'')

    def test_complete_experience_is_exported_without_display_truncation(self):
        self.state['experiences']['v1']['experience'] = 'Process instruction.\n'*1500
        result = present(self.state,self.evolution,self.groups)
        self.assertEqual(result['retained_experiences'][0]['experience'],self.state['experiences']['v1']['experience'])

    def test_workspace_input_copies_are_not_listed_as_generated_deliverables(self):
        original = {'name':'original.pdf','size':100,'download_url':'/input-copy'}
        self.groups[0]['files'].append(original)
        self.groups.append({'id':'input','files':[original]})
        result = present(self.state,self.evolution,self.groups)
        self.assertEqual([f['name'] for f in result['partial_artifacts']],['results/manifest.json'])


if __name__ == '__main__':unittest.main()
