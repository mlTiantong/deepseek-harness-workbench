"""Exercise DSH's real compaction transaction against a local mock provider."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.store import Store


class Provider(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        texts = [str(part.get('text', '')) for msg in body.get('messages', [])
                 for part in (msg.get('content') if isinstance(msg.get('content'), list)
                              else [{'text': msg.get('content', '')}]) if isinstance(part, dict)]
        text = '\n'.join(texts)
        self.requests.append(text)
        if 'You are now acting as a compaction engine' in text:
            payload = json.dumps({'error': {'message': 'maximum context length exceeded',
                                            'code': 'context_length_exceeded',
                                            'type': 'invalid_request_error'}}).encode()
            self.send_response(400); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        content = '用户希望保留简短自然的回复。' if '历史片段：' in text else '收到。'
        chunks = [
            {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'mock',
             'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}]},
            {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'mock',
             'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}},
        ]
        payload = (''.join('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n' for chunk in chunks)
                   + 'data: [DONE]\n\n').encode('utf-8')
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)


class CompactionIntegrationTest(unittest.TestCase):
    def test_overflow_uses_chunked_summary_and_session_remains_usable(self):
        Provider.requests = []
        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db.sqlite')
            runtime = DshRuntime(store, Path.cwd(), data_dir=tmp)
            runtime.configure_model(model='mock', base_url=f'http://127.0.0.1:{server.server_port}/v1',
                                    api_key='mock', context_length=64000, max_tokens=1024,
                                    reasoning_effort='off', compatibility='standard')
            try:
                runtime.start()
                runtime.run_turn('main', '用户偏好：回复自然。' + '测试内容' * 3000)
                result = runtime.compact_session('main')
                self.assertTrue(result['compacted'])
                self.assertTrue(any('You are now acting as a compaction engine' in text for text in Provider.requests))
                self.assertTrue(any('历史片段：' in text for text in Provider.requests))
                self.assertEqual(runtime.run_turn('main', '继续')['finish_reason'], 'completed')
            finally:
                runtime.close()


if __name__ == '__main__':
    unittest.main()
