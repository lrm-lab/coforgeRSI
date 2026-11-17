"""Check credential transport and the environment-based deployment path without API calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import demo_model_config
from scripts import team_setup


class TeamSetupTests(unittest.TestCase):
    def credentials(self):
        return {'RSI_WEB_MODEL_API_KEY': 'fixture-team-key', 'RSI_PI_MODELS_JSON': json.dumps({
            'providers': {'zhizengzeng': {'apiKey': '${RSI_WEB_MODEL_API_KEY}',
                'baseUrl': 'https://fixture.invalid/v1',
                'models': [{'id': 'deepseek-v4.1-flash', 'maxTokens': 1024}]}}})}

    def test_repository_local_config_works_without_account_configuration(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, self.credentials()):
            root = Path(temporary)
            with patch.object(Path, 'home', return_value=root / 'no-account'):
                env = demo_model_config.process_env(root)
            self.assertEqual(env['RSI_WEB_MODEL_API_KEY'], 'fixture-team-key')
            generated = (root / 'private/pi-agent/models.json').read_text()
            self.assertNotIn('fixture-team-key', generated)
            self.assertEqual(json.loads(generated)['providers']['zhizengzeng']['models'][0]['maxTokens'], 32768)

    def test_local_secret_file_permissions_and_validation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(team_setup, 'ROOT', Path(temporary)):
            team_setup.save_credentials(self.credentials())
            path = Path(temporary) / 'private/team-credentials.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(path.read_text()), self.credentials())
        with self.assertRaises(ValueError):
            team_setup.validate_credentials({})

    @unittest.skipUnless(shutil.which('openssl'), 'requires openssl')
    def test_only_recipient_can_decrypt_workflow_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for name in ('recipient', 'other'):
                subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                                '-keyout', str(folder / (name + '.key')), '-out', str(folder / (name + '.pem')),
                                '-days', '1', '-subj', '/CN=fixture/'], check=True, capture_output=True)
            encrypted = subprocess.run(['openssl', 'cms', '-encrypt', '-binary', '-aes-256-gcm',
                                        '-outform', 'DER', str(folder / 'recipient.pem')],
                                       input=json.dumps(self.credentials()).encode(), capture_output=True, check=True).stdout
            for name in ('recipient', 'other'):
                result = subprocess.run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER',
                                         '-recip', str(folder / (name + '.pem')), '-inkey', str(folder / (name + '.key'))],
                                        input=encrypted, capture_output=True)
                if name == 'recipient':
                    self.assertEqual(json.loads(result.stdout), self.credentials())
                else:
                    self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
