"""Only synthetic requests and loopback HTTP servers; no external inference."""
import contextlib, http.client, json, os, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from adapter import adapter as a

class Backend(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self): self.respond()
    def do_POST(self): self.respond()
    def respond(self):
        s=self.server.state
        b=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
        s['calls'].append((self.path,b,self.headers.get('Authorization')))
        if self.path=='/api/tags': code,result=s.get('tags',200),{'models':[]}
        else:
            time.sleep(s.get('delay',0)); code,result=s.get('code',200),s.get('result',{'done':True,'response':'synthetic'})
        raw=result if isinstance(result,bytes) else json.dumps(result).encode()
        try:
            self.send_response(code); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError): pass

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.stack=contextlib.ExitStack(); self.servers=[]; self.states=[]; self.calls=[]
        for _ in range(3):
            s=ThreadingHTTPServer(('127.0.0.1',0),Backend); s.state={'calls':[]}
            threading.Thread(target=s.serve_forever,daemon=True).start(); self.servers.append(s); self.states.append(s.state)
        self.states[2]['result']={'choices':[{'message':{'content':'synthetic cloud'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2}}
        self.cfg={'backends':[{'name':'rtx4090','host':'primary.test'},{'name':'rtx4090m','host':'secondary.test'}],'cloud_model':'test-cloud','cloud_only_if_unreachable':True}
        Path(self.tmp.name,'openai-key').write_text('synthetic-test-credential')
        self.stack.enter_context(patch.object(a,'config',lambda:self.cfg)); self.stack.enter_context(patch.dict(os.environ,{'CREDENTIALS_DIRECTORY':self.tmp.name}))
        original=a.request
        def transport(host,port,path,body=None,tls=False,headers=None,timeout=180):
            destinations={'primary.test':0,'secondary.test':1,'api.openai.com':2}
            if host not in destinations: raise AssertionError('External destination blocked')
            self.calls.append((host,path,timeout,tls))
            return original('127.0.0.1',self.servers[destinations[host]].server_port,path,body,False,headers,min(timeout,.05))
        self.stack.enter_context(patch.object(a,'request',transport)); a.HEALTH.clear(); a.COUNTS.clear()
        self.api=ThreadingHTTPServer(('127.0.0.1',0),a.Handler); threading.Thread(target=self.api.serve_forever,daemon=True).start()
    def tearDown(self):
        for s in [self.api]+self.servers: s.shutdown(); s.server_close()
        self.stack.close(); self.tmp.cleanup()
    def call(self,p=None,path='/api/generate',method='POST'):
        c=http.client.HTTPConnection('127.0.0.1',self.api.server_port,timeout=3)
        c.request(method,path,json.dumps(p if p is not None else {'model':'test-local','prompt':'synthetic','stream':False}) if method=='POST' else None,{'Content-Type':'application/json'})
        r=c.getresponse(); out=r.status,dict(r.getheaders()),json.loads(r.read()); c.close(); return out
    def fail(self):
        for s in self.states[:2]: s.update(code=503,tags=503)
    def test_primary(self):
        status,h,_=self.call(); self.assertEqual(status,200); self.assertEqual(h['X-Ollama-Backend'],'rtx4090'); self.assertFalse(self.states[1]['calls']); self.assertFalse(self.states[2]['calls']); self.assertFalse(self.states[0]['calls'][0][1]['stream'])
    def test_secondary_on_http_error(self):
        self.states[0]['code']=500; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m')
    def test_secondary_on_timeout(self):
        self.states[0]['delay']=.15; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m'); self.assertEqual(self.calls[0][2],180)
    def test_secondary_on_invalid_json(self):
        self.states[0]['result']=b'bad'; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m')
    def test_secondary_on_incomplete(self):
        self.states[0]['result']={'done':False}; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m')
    def test_secondary_on_error_payload(self):
        self.states[0]['result']={'done':True,'error':'synthetic'}; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m')
    def test_secondary_on_refused_connection(self):
        self.servers[0].shutdown(); self.servers[0].server_close(); self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m')
    def test_cloud_order_and_mapping(self):
        self.fail(); status,h,b=self.call({'model':'test-local','system':'classify','prompt':'synthetic','format':'json','options':{'temperature':.1,'num_predict':12},'stream':False})
        self.assertEqual(status,200); self.assertEqual(h['X-Ollama-Backend'],'openai'); self.assertEqual(b['model'],'test-local')
        self.assertEqual([x[0] for x in self.calls],['primary.test','secondary.test','primary.test','secondary.test','api.openai.com'])
        _,sent,auth=self.states[2]['calls'][0]; self.assertEqual(sent['model'],'test-cloud'); self.assertEqual(sent['max_tokens'],12); self.assertEqual(sent['response_format'],{'type':'json_object'}); self.assertEqual(auth,'Bearer synthetic-test-credential'); self.assertEqual(self.calls[-1][2:],(60,True))
    def test_reachable_failure_blocks_cloud(self):
        for s in self.states[:2]: s['code']=500
        self.assertEqual(self.call()[::2],(503,{'error':'local_inference_failed_cloud_not_permitted'})); self.assertFalse(self.states[2]['calls'])
    def test_relaxed_cloud_policy(self):
        for s in self.states[:2]: s['code']=500
        self.cfg['cloud_only_if_unreachable']=False; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'openai')
    def test_all_fail(self):
        self.fail(); self.states[2]['code']=429; self.assertEqual(self.call()[::2],(502,{'error':'all_backends_failed'}))
    def test_malformed_cloud(self):
        self.fail(); self.states[2]['result']={}; self.assertEqual(self.call()[0],502)
    def test_missing_credential(self):
        self.fail(); Path(self.tmp.name,'openai-key').unlink(); self.assertEqual(self.call()[0],502); self.assertFalse(self.states[2]['calls'])
    def test_unsupported_cloud_inputs(self):
        self.fail()
        for k,v in [('images',['synthetic']),('context',[1]),('suffix','synthetic')]: self.assertEqual(self.call({'model':'test',k:v})[0],422)
        self.assertFalse(self.states[2]['calls'])
    def test_chat_schema_and_tools(self):
        self.fail(); schema={'type':'object','properties':{},'additionalProperties':False}; tools=[{'type':'function','function':{'name':'test','parameters':schema}}]
        self.states[2]['result']={'choices':[{'message':{'tool_calls':[{'function':{'name':'test','arguments':'{}'}}]}}]}
        status,_,b=self.call({'model':'test','messages':[{'role':'user','content':'synthetic'}],'format':schema,'tools':tools},'/api/chat')
        self.assertEqual(status,200); self.assertEqual(b['message']['tool_calls'][0]['function']['arguments'],{}); sent=self.states[2]['calls'][0][1]; self.assertEqual(sent['tools'],tools); self.assertTrue(sent['response_format']['json_schema']['strict'])
    def test_health_is_liveness(self):
        a.HEALTH.update(rtx4090=False,rtx4090m=False); self.assertEqual(self.call(path='/health',method='GET')[0],200)
    def test_unhealthy_skipped(self):
        a.HEALTH['rtx4090']=False; self.assertEqual(self.call()[1]['X-Ollama-Backend'],'rtx4090m'); self.assertFalse(self.states[0]['calls'])
    def test_disabled_reachable_still_blocks_cloud(self):
        self.cfg['disabled']=['rtx4090','rtx4090m']; self.assertEqual(self.call()[0],503); self.assertFalse(self.states[2]['calls'])
    def test_tags_synthetic_fallback(self):
        self.fail(); status,_,b=self.call(path='/api/tags',method='GET'); self.assertEqual(status,200); self.assertEqual(b['models'][0]['digest'],'cloud-fallback'); self.assertFalse(self.states[2]['calls'])
    def test_stream_single_chunk(self):
        self.assertEqual(self.call({'model':'test','stream':True})[1]['Content-Type'],'application/x-ndjson')
    def test_validation(self):
        self.assertEqual(self.call({'prompt':'synthetic'})[0],400); self.assertEqual(self.call({'model':'test'},'/api/chat')[0],400); self.assertEqual(self.call(path='/unsupported')[0],404)
    def test_body_limit(self):
        c=http.client.HTTPConnection('127.0.0.1',self.api.server_port,timeout=3); c.request('POST','/api/chat',headers={'Content-Length':str(4*1024*1024+1)}); r=c.getresponse(); self.assertEqual(r.status,413); r.read(); c.close()

if __name__=='__main__': unittest.main()
