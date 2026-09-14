#!/usr/bin/env python3
"""Two native TUIs, same synthetic session, loopback model only."""
import argparse
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import termios
import threading
import time

from history_probe import AppServer, prepare
from mock_model import MockModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-mode', choices=['legacy', 'paginated'], default='paginated')
    parser.add_argument('--binary', default='/opt/homebrew/bin/codex')
    args = parser.parse_args()
    root = prepare('codex-concurrent-resume-')
    print(root, flush=True)
    release = threading.Event()
    hold = threading.Event()
    def reply(body):
        if hold.is_set():
            release.wait(25)
        return None
    model = MockModel(root, reply)
    env = {'PATH': os.environ['PATH'], 'HOME': str(root / 'home'),
           'CODEX_HOME': str(root / 'codex'), 'TMPDIR': str(root),
           'TERM': 'xterm-256color', 'LANG': 'en_US.UTF-8'}
    config = root / 'codex/config.toml'
    with config.open('a') as f:
        f.write('\n[projects.' + json.dumps(str(root)) + ']\ntrust_level = "trusted"\n')
    seed = AppServer(root, 'seed', binary=args.binary)
    tid = seed.request('thread/start', {'cwd': str(root), 'historyMode': args.history_mode,
          'approvalPolicy': 'never', 'sandbox': 'read-only'})['result']['thread']['id']
    seed.request('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': 'Seed conversation.'}]})
    while seed.receive().get('method') != 'turn/completed':
        pass
    seed.close()
    print('Seed', tid, flush=True)
    hold.set()
    children = []
    def start(name):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 0, 0))
        proc = subprocess.Popen([args.binary, 'resume', tid,
                'Synthetic continuation from client ' + name + '.', '--no-alt-screen',
                '-a', 'never', '-s', 'read-only', '-C', str(root)],
                env=env, cwd=root, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True)
        os.close(slave)
        child = {'name': name, 'p': proc, 'fd': master, 'log': bytearray()}
        children.append(child)
        print('Started', name, proc.pid, flush=True)
    def pump(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([c['fd'] for c in children], [], [], .1)
            for c in children:
                if c['fd'] not in ready:
                    continue
                try:
                    data = os.read(c['fd'], 65536)
                except OSError:
                    continue
                c['log'].extend(data)
                if b'\x1b[6n' in data:
                    os.write(c['fd'], b'\x1b[1;1R')
    try:
        start('A')
        for _ in range(30):
            pump(.25)
            if len(model.requests) >= 2:
                break
        print('Requests with A active:', len(model.requests), flush=True)
        start('B')
        pump(5)
        print('Requests before release:', len(model.requests), flush=True)
        release.set()
        pump(6)
        start('C-after-A-turn-completed')
        pump(3)
    finally:
        release.set()
        for c in children:
            (root / (c['name'] + '.terminal')).write_bytes(c['log'])
            print(c['name'], 'exit before cleanup:', c['p'].poll(), flush=True)
            if c['p'].poll() is None:
                os.write(c['fd'], b'\x03')
        pump(1)
        for c in children:
            if c['p'].poll() is None:
                c['p'].terminate()
            c['p'].wait(timeout=5)
            os.close(c['fd'])
        # Only the disposable CODEX_HOME can be affected by this cleanup.
        stopped = subprocess.run([args.binary, 'app-server', 'daemon', 'stop'],
                                 env=env, cwd=root, capture_output=True, text=True, timeout=10)
        (root / 'daemon-stop.txt').write_text(stopped.stdout + stopped.stderr)
        model.close()
    reader = AppServer(root, 'inspect', binary=args.binary)
    result = reader.request('thread/read', {'threadId': tid, 'includeTurns': True})
    (root / 'thread-after.json').write_text(json.dumps(result, indent=2))
    resumed = reader.request('thread/resume', {'threadId': tid})
    (root / 'resume-after-exit.json').write_text(json.dumps(resumed, indent=2))
    print('Resume after closing TUIs:', resumed.get('error', 'ok'), flush=True)
    reader.close()
    print('Total model requests:', len(model.requests), flush=True)


if __name__ == '__main__':
    main()
