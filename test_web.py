"""Integration checks against actual controller subprocesses and an explicitly labeled fixture."""
import io,json,os,socket,subprocess,sys,tempfile,time,unittest,urllib.request,urllib.error,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent

class WebIntegration(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory(prefix='rsi-web-test-')
  with socket.socket() as s:s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1]
  cls.base=f'http://127.0.0.1:{cls.port}'
  cls.proc=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(cls.port),'--data',cls.temp.name,'--test-fixture'],stdout=subprocess.DEVNULL)
  for _ in range(100):
   try:cls.request('/api/health');break
   except OSError:time.sleep(.05)
  else:raise RuntimeError('Server did not start')
 @classmethod
 def tearDownClass(cls):
  cls.proc.send_signal(__import__('signal').SIGINT);cls.proc.wait(timeout=10)
  for path in (Path(cls.temp.name)/'jobs').glob('*/job.json'):
   meta=json.loads(path.read_text());pid=meta.get('process',{}).get('pid')
   if pid:
    try:os.kill(pid,__import__('signal').SIGINT)
    except ProcessLookupError:pass
  time.sleep(.3);cls.temp.cleanup()
 @classmethod
 def request(cls,path,data=None,headers=None,raw=False):
  payload=data if isinstance(data,bytes) else json.dumps(data).encode() if data is not None else None
  req=urllib.request.Request(cls.base+path,data=payload,headers={'X-RSI-Client':'web','Content-Type':'application/json',**(headers or {})})
  with urllib.request.urlopen(req,timeout=15) as r:return r.read() if raw else json.load(r)
 def multipart(self,files):
  boundary='RSI_TEST_UPLOAD_BOUNDARY'
  parts=[]
  for name,filename,content in files:
   parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'.encode()+(f'; filename="{filename}"'.encode() if filename else b'')+b'\r\n\r\n'+content+b'\r\n')
  return b''.join(parts)+f'--{boundary}--\r\n'.encode(),{'Content-Type':f'multipart/form-data; boundary={boundary}'}
 def test_complete_queue_upload_and_boundaries(self):
  health=self.request('/api/health');self.assertTrue(health['fixture'])
  self.assertIn(b'RSI',self.request('/',raw=True))
  first=self.request('/api/jobs',{'mode':'sample'});second=self.request('/api/jobs',{'mode':'sample'})
  body,headers=self.multipart([('question',None,b'Check the attached inputs'),('files','data.csv',b'x,y\n1,2'),('skills','extract.md',b'---\nname: extract\ndescription: Read a table\n---\nRead the data.')])
  upload=self.request('/api/jobs',body,headers)
  self.request('/api/jobs/'+upload['id']+'/stop',{})
  self.assertEqual(self.request('/api/jobs/'+upload['id'])['status'],'cancelled')
  stored=Path(self.temp.name)/'jobs'/upload['id']
  self.assertEqual((stored/'input/files/data.csv').read_bytes(),b'x,y\n1,2')
  self.assertTrue(list((stored/'input/skills').rglob('SKILL.md')))
  # Draft files are browsable even without answer_attempt or successful verification.
  draft=stored/'goal/workspaces/web-draft';draft.mkdir(parents=True)
  (draft/'index.html').write_text('<html><script src="app.js"></script></html>')
  (draft/'app.js').write_text('document.body.dataset.ready="yes"')
  (draft/'data.json').write_text('{"ready":true}')
  (draft/'secret.key').write_text('must not be exposed')
  (draft/'outside').symlink_to(stored/'config.json')
  groups=self.request('/api/jobs/'+upload['id'])['file_workspaces']
  group=next(g for g in groups if g['id']=='workspace-web-draft')
  self.assertFalse(group['verified']);self.assertFalse(group['final'])
  html=next(f for f in group['files'] if f['name']=='index.html')
  self.assertIn('<html>',self.request(html['source_url'])['text'])
  self.assertIn(b'<html>',self.request(html['download_url'],raw=True))
  with urllib.request.urlopen(self.base+html['preview_url']) as response:
   self.assertIn('sandbox allow-scripts',response.headers['Content-Security-Policy'])
   self.assertNotIn('allow-same-origin',response.headers['Content-Security-Policy'])
  self.assertEqual(self.request(html['preview_url'].replace('index.html','data.json')),{'ready':True})
  with zipfile.ZipFile(io.BytesIO(self.request(group['archive_url'],raw=True))) as archive:
   self.assertIn('index.html',archive.namelist());self.assertNotIn('secret.key',archive.namelist())
  for rel in ['../input/files/data.csv','outside','secret.key']:
   with self.assertRaises(urllib.error.HTTPError):self.request('/api/jobs/'+upload['id']+'/files/workspace-web-draft/source/'+rel)
  for job in [first,second]:
   for _ in range(300):
    result=self.request('/api/jobs/'+job['id'])
    if result['status'] not in ('queued','launching','running','stopping'):break
    time.sleep(.1)
   self.assertEqual(result['status'],'solved',result['reason'])
   self.assertGreaterEqual(result['round_index'],1)
   self.assertGreaterEqual(result['validated_experience_count'],1)
   self.assertGreaterEqual(len(result['events']),12)
   self.assertTrue(result['method'])
   for key in ['max_seconds','agent_timeout','verifier_timeout']:self.assertIsNone(result['limits'][key])
   report=self.request('/api/jobs/'+job['id']+'/report');self.assertEqual(report['status'],'solved')
   artifact=next(f for f in result['artifacts'] if f['name']=='result.json')
   self.assertEqual(json.loads(self.request(artifact['url'],raw=True))['x'],7)
   state=json.loads((Path(self.temp.name)/'jobs'/job['id']/'goal/state.json').read_text())
   base=Path(self.temp.name)/'jobs'/job['id']/'goal/workspaces'/state['answer_attempt']
   (base/'检查# 1.txt').write_text('unicode and fragment filename')
   special=next(f for f in self.request('/api/jobs/'+job['id'])['artifacts'] if f['name']=='检查# 1.txt')
   self.assertEqual(self.request(special['url'],raw=True),b'unicode and fragment filename')
  self.assertEqual(self.request('/api/replay')['summary']['agent_calls'],8)
  # Browser cross-origin writes and uploaded path escapes must fail.
  with self.assertRaises(urllib.error.HTTPError) as denied:self.request('/api/jobs',{'mode':'sample'},{'Origin':'http://elsewhere.invalid'})
  self.assertEqual(denied.exception.code,400)
  bad,headers=self.multipart([('question',None,b'bad path'),('files','../escape.txt',b'no')])
  with self.assertRaises(urllib.error.HTTPError):self.request('/api/jobs',bad,headers)
  z=io.BytesIO()
  with zipfile.ZipFile(z,'w') as archive:archive.writestr('../escape','bad')
  bad,headers=self.multipart([('question',None,b'bad zip'),('skills','evil.zip',z.getvalue())])
  with self.assertRaises(urllib.error.HTTPError):self.request('/api/jobs',bad,headers)
  with self.assertRaises(urllib.error.HTTPError):self.request('/api/jobs/'+first['id']+'/artifact/../../../config.json',raw=True)
  self.assertFalse((Path(self.temp.name)/'escape.txt').exists())

 def test_protocol_error_still_exports_retained_experience_and_partial_files(self):
  job=self.request('/api/jobs',{'mode':'sample'})
  for _ in range(300):
   result=self.request('/api/jobs/'+job['id'])
   if result['status']=='solved':break
   time.sleep(.1)
  else:self.fail('Fixture did not finish')
  path=Path(self.temp.name)/'jobs'/job['id'];goal=path/'goal'
  state=json.loads((goal/'state.json').read_text())
  state.update(status='protocol_error',reason='planner did not produce valid JSON after a bounded repair')
  state.pop('answer_attempt',None);state.pop('final_experience',None)
  (goal/'state.json').write_text(json.dumps(state))
  (goal/'answer.md').unlink()
  meta=json.loads((path/'job.json').read_text());meta['status']='protocol_error'
  (path/'job.json').write_text(json.dumps(meta))
  result=self.request('/api/jobs/'+job['id'])
  self.assertEqual(result['status'],'protocol_error')
  self.assertTrue(result['method']);self.assertFalse(result['method_is_final'])
  self.assertEqual(len(result['retained_experiences']),result['validated_experience_count'])
  self.assertIn('任务尚未通过全部验收',result['partial_summary'])
  self.assertFalse(result['answer'])
  self.assertTrue(result['artifacts']);self.assertTrue(result['artifacts'][0]['partial'])
  artifact=next(f for f in result['artifacts'] if f['name']=='result.json')
  self.assertEqual(json.loads(self.request(artifact['url'],raw=True))['x'],7)
  exported=self.request('/api/jobs/'+job['id']+'/experiences')
  self.assertEqual(exported['experiences'],result['retained_experiences'])
  self.assertEqual(exported['status'],'protocol_error')
  self.assertEqual(json.loads((goal/'state.json').read_text()),state)

if __name__=='__main__':unittest.main(verbosity=2)
