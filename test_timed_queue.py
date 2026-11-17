"""Verify deadlines end real subprocesses and advance the FIFO queue without overlap."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parent


class TimedQueueTests(unittest.TestCase):
    def test_three_deadlines_advance_fifo(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            identities = [f'20261116-12000{i}-abcdef0{i}' for i in range(3)]
            sleeper = data/'slow_agent.py'
            sleeper.write_text('import time\ntime.sleep(30)\n')
            for i, identity in enumerate(identities):
                job = data/'jobs'/identity
                (job/'input').mkdir(parents=True)
                (job/'input/task.txt').write_text('Deadline queue fixture')
                config = {'backend':{'type':'command','command':[sys.executable,str(sleeper)]},
                          'limits':{'max_seconds':.5,'agent_timeout':None}}
                (job/'config.json').write_text(json.dumps(config))
                (job/'job.json').write_text(json.dumps({'id':identity,'question':'Deadline fixture',
                    'mode':'custom','fixture':True,'status':'queued','created_at':time.time()+i}))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
            proc = subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(port),
                                     '--data',str(data),'--test-fixture'],stdout=subprocess.DEVNULL)
            try:
                for _ in range(150):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/jobs',timeout=2) as response:
                            jobs = json.load(response)
                        finished = all(json.loads((data/'jobs'/i/'job.json').read_text()).get('finished_at')
                                       for i in identities)
                        if finished and all(j['status']=='budget_exhausted' for j in jobs):break
                    except OSError:pass
                    time.sleep(.1)
                else:self.fail('Timed queue did not complete')
                metas = [json.loads((data/'jobs'/i/'job.json').read_text()) for i in identities]
                states = [json.loads((data/'jobs'/i/'goal/state.json').read_text()) for i in identities]
                for i in range(3):
                    self.assertEqual(states[i]['status'],'budget_exhausted')
                    self.assertEqual(states[i]['agent_calls'],1)
                    self.assertLess(metas[i]['finished_at']-metas[i]['started_at'],3)
                    if i:self.assertGreaterEqual(metas[i]['started_at'],metas[i-1]['finished_at'])
            finally:
                proc.send_signal(signal.SIGINT);proc.wait(timeout=5)


if __name__=='__main__':unittest.main()
