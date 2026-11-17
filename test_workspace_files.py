import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import workspace_files as files


class WorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job = Path(self.temp.name)/'20261116-120000-abcdef12'
        self.base = self.job/'goal/workspaces/baseline'
        self.base.mkdir(parents=True)
        (self.job/'input').mkdir()
        (self.base/'index.html').write_text('<script src="app.js"></script>')
        (self.base/'app.js').write_text('document.body.dataset.loaded="yes"')

    def tearDown(self):self.temp.cleanup()

    def test_files_are_available_before_answer_attempt_and_remain_unverified(self):
        group = next(g for g in files.inventory(self.job, {}) if g['id']=='workspace-baseline')
        self.assertFalse(group['final'])
        self.assertFalse(group['verified'])
        self.assertEqual(len(group['files']),2)
        html = next(f for f in group['files'] if f['name']=='index.html')
        self.assertIn('/preview/workspace-baseline/index.html',html['preview_url'])
        self.assertIn('/source/index.html',html['source_url'])

    def test_path_traversal_hidden_files_and_symlink_ancestors_rejected(self):
        outside = self.job/'outside'
        outside.mkdir();(outside/'secret.txt').write_text('private')
        (self.base/'link').symlink_to(outside,target_is_directory=True)
        (self.base/'secret.key').write_text('private')
        (self.base/'.env').write_text('private')
        (self.base/'node_modules').mkdir();(self.base/'node_modules/a.js').write_text('dependency')
        for relative in ['../outside/secret.txt','link/secret.txt','secret.key','.env','node_modules/a.js','/etc/passwd']:
            with self.assertRaises(ValueError):files.resolve(self.job,'workspace-baseline',relative)
        group=next(g for g in files.inventory(self.job,{}) if g['id']=='workspace-baseline')
        self.assertEqual(sorted(f['name'] for f in group['files']),['app.js','index.html'])

    def test_unicode_paths_and_zip_preserve_files(self):
        (self.base/'样式# 1.css').write_text('body{color:red}')
        group=next(g for g in files.inventory(self.job,{}) if g['id']=='workspace-baseline')
        record=next(f for f in group['files'] if f['name']=='样式# 1.css')
        self.assertIn('%23%20',record['download_url'])
        raw=files.archive(self.job,'workspace-baseline',{})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.read('样式# 1.css'),b'body{color:red}')

    def test_source_preview_is_bounded_and_binary_rejected(self):
        target=self.base/'large.txt';target.write_bytes(b'x'*(files.SOURCE_BYTES+10))
        data=files.source(target)
        self.assertTrue(data['truncated']);self.assertEqual(len(data['text']),files.SOURCE_BYTES)
        target.write_bytes(b'\x00\x01')
        with self.assertRaises(ValueError):files.source(target)

    def test_final_and_verification_are_separate_labels(self):
        reports=self.job/'goal/attempts';reports.mkdir()
        (reports/'baseline.json').write_text(json.dumps({'feedback':{'valid':True,'passed':True}}))
        group=next(g for g in files.inventory(self.job,{'answer_attempt':'baseline'}) if g['id']=='workspace-baseline')
        self.assertTrue(group['verified']);self.assertTrue(group['final'])

    def test_results_remain_visible_when_source_cache_exceeds_list_limit(self):
        for i in range(305):(self.base/f'cache-{i:04}.html').write_text('source cache')
        results=self.base/'results';results.mkdir();(results/'arxiv_digest.md').write_text('actual deliverable')
        group=next(g for g in files.inventory(self.job,{}) if g['id']=='workspace-baseline')
        self.assertEqual(group['files'][0]['name'],'results/arxiv_digest.md')
        self.assertTrue(group['truncated'])
        self.assertFalse(group['archive_available'])


if __name__=='__main__':unittest.main(verbosity=2)
