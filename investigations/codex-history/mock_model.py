"""Loopback Responses API fixture. No upstream calls or credentials."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import uuid


class MockModel:
    def __init__(self, root, choose_output=None):
        self.requests = []
        self.root = root
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                (root / "model-requests.json").write_text(json.dumps(owner.requests, indent=2))
                uid = uuid.uuid4().hex
                item = choose_output(body) if choose_output else None
                if item is None:
                    item = {"id": "msg_" + uid, "type": "message", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text",
                            "text": "Synthetic completed turn.", "annotations": []}]}
                response = {"id": "resp_" + uid, "object": "response", "status": "completed",
                            "model": "probe-model", "output": [item],
                            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
                events = [
                    {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                    {"type": "response.output_item.added", "output_index": 0, "item": item},
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": response}]
                raw = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        config = root / 'codex/config.toml'
        config.write_text(config.read_text().replace('http://127.0.0.1:1/v1',
                           'http://127.0.0.1:' + str(self.server.server_port) + '/v1'))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
