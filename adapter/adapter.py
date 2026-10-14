#!/usr/bin/env python3
"""Ordered Ollama fallback. No request, response, credential or exception logging."""
import datetime, http.client, json, os, pathlib, ssl, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
CONFIG = os.environ.get('ADAPTER_CONFIG', '/etc/paperless-ai/config.json')
HEALTH = {}
COUNTS = {}
LOCK = threading.Lock()

def config():
    return json.loads(pathlib.Path(CONFIG).read_text())

def request(host, port, path, body=None, tls=False, headers=None, timeout=180):
    conn = http.client.HTTPSConnection(host, port, timeout=2, context=ssl.create_default_context()) if tls else http.client.HTTPConnection(host, port, timeout=2)
    try:
        conn.connect()
        conn.sock.settimeout(timeout)
        conn.request('POST' if body is not None else 'GET', path, body=json.dumps(body).encode() if body is not None else None, headers=headers or {'Content-Type': 'application/json'})
        r = conn.getresponse()
        data = r.read(16 * 1024 * 1024 + 1)
        if r.status != 200 or len(data) > 16 * 1024 * 1024:
            raise ValueError('upstream_failed')
        return json.loads(data)
    finally:
        conn.close()

def check():
    while True:
        for b in config()['backends']:
            try:
                request(b['host'], b.get('port', 11434), '/api/tags', timeout=2)
                ok = True
            except Exception:
                ok = False
            with LOCK:
                HEALTH[b['name']] = ok
        time.sleep(5)

def cloud(p, path):
    if p.get('images') or p.get('suffix') or p.get('context') or any((m.get('images') for m in p.get('messages', []))):
        raise ValueError('unsupported_cloud_input')
    messages = p.get('messages') if path == '/api/chat' else ([{'role': 'system', 'content': p['system']}] if p.get('system') else []) + [{'role': 'user', 'content': p.get('prompt', '')}]
    body = {'model': config()['cloud_model'], 'messages': messages, 'stream': False}
    for a, b in [('temperature', 'temperature'), ('top_p', 'top_p'), ('num_predict', 'max_tokens'), ('stop', 'stop')]:
        if a in p.get('options', {}) and (a != 'num_predict' or p['options'][a] > 0):
            body[b] = p['options'][a]
    if p.get('tools'):
        body['tools'] = p['tools']
    if p.get('format') == 'json':
        body['response_format'] = {'type': 'json_object'}
    elif isinstance(p.get('format'), dict):
        body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'response', 'schema': p['format'], 'strict': True}}
    key = pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'] + '/openai-key').read_text().strip()
    d = request('api.openai.com', 443, '/v1/chat/completions', body, True, {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, 60)
    choice = d['choices'][0]
    msg = choice['message']
    usage = d.get('usage', {})
    result = {'model': p['model'], 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'done': True, 'done_reason': choice.get('finish_reason', 'stop'), 'prompt_eval_count': usage.get('prompt_tokens', 0), 'eval_count': usage.get('completion_tokens', 0)}
    if path == '/api/generate':
        result['response'] = msg.get('content') or ''
    else:
        result['message'] = {'role': 'assistant', 'content': msg.get('content') or ''}
        if msg.get('tool_calls'):
            result['message']['tool_calls'] = [{'function': {'name': t['function']['name'], 'arguments': json.loads(t['function']['arguments'])}} for t in msg['tool_calls']]
    return result

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def send(self, status, data, backend=None, stream=False):
        if status == 200 and backend and (self.path in ['/api/chat', '/api/generate']):
            with LOCK:
                COUNTS[backend] = COUNTS.get(backend, 0) + 1
        raw = (json.dumps(data) + '\n').encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/x-ndjson' if stream else 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        if backend:
            self.send_header('X-Ollama-Backend', backend)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            with LOCK:
                state = dict(HEALTH)
                counts = dict(COUNTS)
            return self.send(200, {'status': 'ready', 'backends': state, 'priority': [b['name'] for b in config()['backends']] + ['openai'], 'inference_counts': counts, 'cloud_only_if_unreachable': config().get('cloud_only_if_unreachable', False)})
        if self.path == '/api/version':
            return self.send(200, {'version': 'reference-fallback-1'})
        if self.path == '/api/tags':
            for b in config()['backends']:
                try:
                    return self.send(200, request(b['host'], b.get('port', 11434), '/api/tags', timeout=2), b['name'])
                except Exception:
                    pass
            return self.send(200, {'models': [{'name': config().get('advertised_model', 'qwen3:14b'), 'model': config().get('advertised_model', 'qwen3:14b'), 'size': 0, 'digest': 'cloud-fallback', 'modified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'details': {'family': 'fallback', 'parameter_size': '', 'quantization_level': ''}}]})
        self.send(404, {'error': 'endpoint_not_supported'})

    def do_POST(self):
        try:
            self.connection.settimeout(15)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4 * 1024 * 1024:
                return self.send(413, {'error': 'invalid_body_size'})
            p = json.loads(self.rfile.read(size))
            if self.path not in ['/api/generate', '/api/chat']:
                return self.send(404, {'error': 'endpoint_not_supported'})
            if not isinstance(p, dict) or not isinstance(p.get('model'), str):
                return self.send(400, {'error': 'model_required'})
            if self.path == '/api/chat' and (not isinstance(p.get('messages'), list)):
                return self.send(400, {'error': 'messages_required'})
            streaming = p.get('stream', True)
            upstream = dict(p, stream=False)
            for b in config()['backends']:
                if b['name'] in config().get('disabled', []):
                    continue
                with LOCK:
                    healthy = HEALTH.get(b['name'], True)
                if not healthy:
                    continue
                try:
                    result = request(b['host'], b.get('port', 11434), self.path, upstream, timeout=180)
                    if result.get('error') or not result.get('done'):
                        raise ValueError('incomplete')
                except Exception:
                    continue
                return self.send(200, result, b['name'], streaming)
            if config().get('cloud_only_if_unreachable', False):
                reachable = False
                for b in config()['backends']:
                    try:
                        request(b['host'], b.get('port', 11434), '/api/tags', timeout=2)
                        ok = True
                    except Exception:
                        ok = False
                    with LOCK:
                        HEALTH[b['name']] = ok
                    reachable = reachable or ok
                if reachable:
                    return self.send(503, {'error': 'local_inference_failed_cloud_not_permitted'})
            try:
                result = cloud(p, self.path)
            except ValueError as e:
                if str(e) == 'unsupported_cloud_input':
                    return self.send(422, {'error': 'cloud_fallback_supports_text_and_tools_only'})
                return self.send(502, {'error': 'all_backends_failed'})
            except Exception:
                return self.send(502, {'error': 'all_backends_failed'})
            self.send(200, result, 'openai', streaming)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self.send(400, {'error': 'invalid_request'})
            except Exception:
                pass
if __name__ == '__main__':
    threading.Thread(target=check, daemon=True).start()
    ThreadingHTTPServer((os.environ.get('ADAPTER_BIND', '127.0.0.1'), int(os.environ.get('ADAPTER_PORT', '11434'))), Handler).serve_forever()
