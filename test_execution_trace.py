import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from execution_trace import call_trace, safe_text
from general_rsi.backend import Backend, call_diagnostic
from general_rsi.engine import Controller
from general_rsi.protocol import Halt


def save_lines(path, entries):
    path.write_text(''.join(json.dumps(e) + '\n' for e in entries))


class ExecutionTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def message(self, role, content, **extra):
        return {'type': 'message', 'id': str(extra.pop('id', role)),
                'message': {'role': role, 'timestamp': 1794795761000, 'content': content, **extra}}

    def test_pair_tools_hide_thinking_and_keep_genuine_repeated_commands(self):
        rows = [self.message('assistant', [
            {'type': 'thinking', 'thinking': 'PRIVATE_REASONING'},
            {'type': 'text', 'text': 'I will check the process.'},
            {'type': 'toolCall', 'id': 'a', 'name': 'bash', 'arguments': {'command': 'ps -p 12'}},
            {'type': 'toolCall', 'id': 'b', 'name': 'bash', 'arguments': {'command': 'ps -p 12'}}]),
            self.message('toolResult', [{'type': 'text', 'text': 'PID 12\nready'}], toolCallId='a'),
            self.message('toolResult', [{'type': 'text', 'text': 'Command exited with code 1'}], toolCallId='b', isError=True)]
        save_lines(self.folder/'session.jsonl', rows)
        events, _ = call_trace(self.folder, {'ok': True}, False, 1)
        tools = [e for e in events if e['type'] == 'tool']
        self.assertEqual(len(tools), 2)
        self.assertEqual([e['status'] for e in tools], ['completed', 'error'])
        self.assertIn('PID 12', tools[0]['output'])
        self.assertNotIn('PRIVATE_REASONING', json.dumps(events))

    def test_live_partial_output_is_updated_without_duplicate_tool(self):
        save_lines(self.folder/'session.jsonl', [self.message('assistant', [
            {'type': 'toolCall', 'id': 'a', 'name': 'bash', 'arguments': {'command': 'long-job'}}])])
        save_lines(self.folder/'stdout.log', [
            {'type': 'tool_execution_start', 'toolCallId': 'a', 'toolName': 'bash', 'args': {'command': 'long-job'}},
            {'type': 'tool_execution_update', 'toolCallId': 'a', 'partialResult': {'content': [{'type': 'text', 'text': 'step 1'}]}}])
        events, _ = call_trace(self.folder, None, True, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['output'], 'step 1')
        self.assertEqual(events[0]['status'], 'running')
        events, _ = call_trace(self.folder, {'ok': False}, False, 1)
        self.assertEqual(events[0]['status'], 'incomplete')

    def test_file_edits_and_write_content_are_labelled_truthfully(self):
        save_lines(self.folder/'session.jsonl', [self.message('assistant', [
            {'type': 'toolCall', 'id': 'e', 'name': 'edit', 'arguments': {'path': 'app.py', 'oldText': 'old\n', 'newText': 'new\n'}},
            {'type': 'toolCall', 'id': 'w', 'name': 'write', 'arguments': {'path': 'other.py', 'content': 'hello\n'}}])])
        events, _ = call_trace(self.folder, None, True, 1)
        self.assertIn('-old\n+new', events[0]['change'])
        self.assertEqual(events[1]['change_kind'], 'write_content')

    def test_redaction_before_truncation_and_no_html_interpretation(self):
        secrets = ['sk-abcdefghijklmno', 'top secret', 'abcd123', 'some-token', 'privatecontents']
        raw = 'sk-abcdefghijklmno {"api_key": "top secret"} password=abcd123 Bearer some-token\n-----BEGIN PRIVATE KEY-----\nprivatecontents\n-----END PRIVATE KEY-----'
        redacted = safe_text(raw)
        for secret in secrets:self.assertNotIn(secret, redacted)
        self.assertIn('内容过长', safe_text('x'*100, 10))

    def test_historical_length_failure_has_precise_diagnostic(self):
        save_lines(self.folder/'session.jsonl', [self.message('assistant', [
            {'type': 'thinking', 'thinking': 'PRIVATE_REASONING'}], stopReason='length', usage={'output': 8192})])
        diagnostic = call_diagnostic(self.folder, {'ok': False, 'process': {'returncode': 0, 'timed_out': False}})
        self.assertEqual(diagnostic['code'], 'output_limit')
        self.assertIn('8192', diagnostic['message'])
        self.assertIn('不是执行超时', diagnostic['message'])

    def test_backend_does_not_accept_stale_public_text_after_length_stop(self):
        fake = self.folder/'fake-pi'
        fake.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
session=Path(sys.argv[sys.argv.index('--session')+1])
messages=[{'role':'assistant','content':[{'type':'text','text':'Checking capabilities'}],'stopReason':'toolUse'},
          {'role':'assistant','content':[{'type':'thinking','thinking':'secret'}],'stopReason':'length','usage':{'output':8192}}]
session.write_text(''.join(json.dumps({'type':'message','id':str(i),'message':m})+'\\n' for i,m in enumerate(messages)))
for m in messages: print(json.dumps({'type':'message_end','message':m}))
''')
        fake.chmod(0o700)
        result = Backend({'type': 'pi', 'executable': str(fake)}).call(
            {'role': 'bootstrap', 'instructions': 'test', 'input': {}}, self.folder, self.folder/'call', None, None)
        self.assertFalse(result['ok'])
        self.assertEqual(result['text'], '')
        self.assertEqual(result['diagnostic']['code'], 'output_limit')

    def test_length_repair_is_bounded_and_charged_to_call_budget(self):
        source = self.folder/'source'
        source.mkdir()
        controller = Controller.create(self.folder/'goal', 'test', source,
            {'backend': {'type': 'command', 'command': ['unused']}}, None, self.folder/'memory')
        failed = {'ok': False, 'text': '', 'usage': None,
                  'diagnostic': {'code': 'output_limit', 'message': 'truncated'}}
        with patch.object(controller.backend, 'call', return_value=failed) as backend:
            with self.assertRaises(Halt):controller.checked_json_call('test', 'bootstrap', {}, source, 'test')
            self.assertEqual(backend.call_count, 2)
        self.assertEqual(controller.state['agent_calls'], 2)
        self.assertTrue((controller.root/'calls/test-length-repair/result.json').exists())
        self.assertIn('agent_output_repair', (controller.root/'events.jsonl').read_text())

    def test_resume_selects_sealed_runtime_instead_of_rewriting_hashes(self):
        import hashlib
        package = self.folder/'runtimes/original/general_rsi'
        package.mkdir(parents=True)
        (package/'engine.py').write_text('original code')
        goal = self.folder/'jobs/example/goal'
        goal.mkdir(parents=True)
        expected = {'engine.py':hashlib.sha256(b'original code').hexdigest()}
        (goal/'state.json').write_text(json.dumps({'implementation_hashes':expected}))
        with patch.object(server, 'ROOT', self.folder):
            self.assertEqual(server.runtime_for(goal.parent), package.parent)
        self.assertEqual(json.loads((goal/'state.json').read_text())['implementation_hashes'], expected)

    def test_demo_output_cap_does_not_change_shared_config_or_persist_credentials(self):
        from demo_model_config import process_env
        source = self.folder/'.pi/agent/models.json'
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({'providers':{'zhizengzeng':{'apiKey':'dummy-private-key',
            'baseUrl':'https://example.invalid/v1','models':[{'id':'deepseek-v4.1-flash','maxTokens':8192}]}}}))
        before = source.read_bytes()
        with patch('demo_model_config.Path.home', return_value=self.folder):
            env = process_env(self.folder/'demo')
        target = Path(env['PI_CODING_AGENT_DIR'])/'models.json'
        config = json.loads(target.read_text())
        self.assertEqual(config['providers']['zhizengzeng']['models'][0]['maxTokens'], 32768)
        self.assertEqual(config['providers']['zhizengzeng']['models'][0]['compat']['maxTokensField'], 'max_tokens')
        self.assertNotIn('dummy-private-key', target.read_text())
        self.assertEqual(env['RSI_WEB_MODEL_API_KEY'], 'dummy-private-key')
        self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':unittest.main(verbosity=2)
