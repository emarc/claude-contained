#!/usr/bin/env python3
"""Exercise local history APIs with synthetic messages and no model/API calls."""
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import argparse
import sqlite3
import shutil


class AppServer:
    def __init__(self, root, name="server", extra=(), binary='/opt/homebrew/bin/codex'):
        self.root = Path(root)
        self.messages = []
        self.seq = 0
        self.q = queue.Queue()
        self.log = (self.root / (name + ".stderr")).open("w")
        # Deliberately don't inherit auth tokens, real HOME, or Codex process state.
        env = {"PATH": os.environ["PATH"], "HOME": str(self.root / "home"),
               "CODEX_HOME": str(self.root / "codex"), "TMPDIR": str(self.root)}
        self.p = subprocess.Popen([binary, "app-server", "--stdio", *extra],
                                  cwd=self.root, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        def reader():
            for line in self.p.stdout:
                try:
                    self.q.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
            self.q.put(None)
        threading.Thread(target=reader, daemon=True).start()
        self.request("initialize", {"clientInfo": {"name": "contained_history_probe", "version": "1"},
                                    "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})

    def send(self, value):
        self.p.stdin.write(json.dumps(value) + "\n")
        self.p.stdin.flush()

    def receive(self, timeout=30):
        value = self.q.get(timeout=timeout)
        if value is None:
            raise RuntimeError("app-server exited; inspect " + self.log.name)
        self.messages.append(value)
        if "id" in value and "method" in value:
            self.send({"id": value["id"], "error": {"code": -32601, "message": "Probe rejects client actions"}})
        return value

    def request(self, method, params):
        self.seq += 1
        seq = self.seq
        self.send({"id": seq, "method": method, "params": params})
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            value = self.receive(max(0.01, deadline - time.monotonic()))
            if value.get("id") == seq and "method" not in value:
                return value
        raise TimeoutError(method)

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            self.p.wait(timeout=5)
        self.log.close()


def prepare(prefix="codex-history-probe-"):
    root = Path(tempfile.mkdtemp(prefix=prefix, dir="/private/tmp"))
    (root / "home").mkdir()
    (root / "codex").mkdir()
    (root / "codex/config.toml").write_text('''model = "probe-model"
model_provider = "probe"
check_for_update_on_startup = false
[model_providers.probe]
name = "Offline probe (never called)"
base_url = "http://127.0.0.1:1/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
''')
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--completed-turns', action='store_true', help='Use a loopback fake model to complete real turns')
    parser.add_argument('--damage-thread-history', action='store_true', help='Damage ONLY the newly generated synthetic history before reopening')
    parser.add_argument('--binary', default='/opt/homebrew/bin/codex', help='Codex binary to test in isolated state')
    args = parser.parse_args()
    root = prepare()
    print("Synthetic state:", root, flush=True)
    results, ids = {}, {}
    model = None
    if args.completed_turns:
        from mock_model import MockModel
        model = MockModel(root)
    for phase in ["create", "reopen"]:
        if phase == 'reopen' and args.damage_thread_history:
            path = root / 'codex/thread_history_1.sqlite'
            with sqlite3.connect(path) as c:
                page_size = c.execute('PRAGMA page_size').fetchone()[0]
                page = c.execute("SELECT rootpage FROM sqlite_master WHERE name='thread_turns'").fetchone()[0]
            c.close()
            shutil.copyfile(path, path.with_suffix('.before-probe-damage'))
            with path.open('r+b') as f:
                f.seek((page - 1) * page_size)
                f.write(b'\xff')
            print('Damaged synthetic thread_turns root page', page, flush=True)
        server = AppServer(root, phase, binary=args.binary)
        try:
            for mode in ["legacy", "paginated"]:
                if phase == "create":
                    response = server.request("thread/start", {"cwd": str(root), "historyMode": mode,
                                                               "approvalPolicy": "never", "sandbox": "read-only"})
                    results[mode + "_start"] = response
                    print(mode, "start", response.get("error", "ok"), flush=True)
                    if "result" not in response:
                        continue
                    tid = response["result"]["thread"]["id"]
                    ids[mode] = tid
                    if model:
                        response = server.request('turn/start', {'threadId': tid, 'input': [
                            {'type': 'text', 'text': 'Synthetic history probe. Reply briefly.'}]})
                        results[mode + '_turn_start'] = response
                        deadline = time.monotonic() + 35
                        while time.monotonic() < deadline:
                            event = server.receive(max(0.01, deadline - time.monotonic()))
                            if event.get('method') == 'turn/completed':
                                results[mode + '_turn_completed'] = event
                                print(mode, 'turn', event['params']['turn']['status'], flush=True)
                                break
                        else:
                            raise TimeoutError('turn completion')
                    results[mode + "_inject"] = server.request("thread/inject_items", {
                        "threadId": tid,
                        "items": [{"type": "message", "role": "user", "content": [
                            {"type": "input_text", "text": "Synthetic persistence probe."}]},
                            {"type": "message", "role": "assistant", "content": [
                                {"type": "output_text", "text": "Synthetic response; no model was called."}]}]})
                if mode not in ids:
                    continue
                tid = ids[mode]
                for method, params in [
                    ("thread/read", {"threadId": tid, "includeTurns": True}),
                    ("thread/turns/list", {"threadId": tid}),
                    ("thread/resume", {"threadId": tid}),
                    ("thread/resume", {"threadId": tid, "excludeTurns": True})]:
                    response = server.request(method, params)
                    key = phase + "_" + mode + "_" + method + ("_exclude" if params.get("excludeTurns") else "")
                    results[key] = response
                    print(key, response.get("error", "ok"), flush=True)
        finally:
            (root / (phase + "-messages.json")).write_text(json.dumps(server.messages, indent=2))
            server.close()
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    integrity = {}
    for path in (root / 'codex').glob('*.sqlite'):
        try:
            with sqlite3.connect(path) as c:
                integrity[path.name] = c.execute('PRAGMA integrity_check').fetchall()
            c.close()
        except sqlite3.Error as e:
            integrity[path.name] = {'error': str(e)}
    (root / 'integrity.json').write_text(json.dumps(integrity, indent=2) + '\n')
    if model:
        model.close()
    print("Results:", root / "results.json", flush=True)


if __name__ == "__main__":
    main()
