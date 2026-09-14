#!/usr/bin/env python3
"""Run INSIDE one container with disposable host storage mounted at /private/tmp.
No native state or credentials. Uses existing lock and fake-model fixtures.
"""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from history_probe import AppServer, prepare
from locking_probe import Process
from mock_model import MockModel

binary = shutil.which('codex')
root = prepare('codex-same-container-')
result = {'root': str(root), 'version': subprocess.check_output([binary, '--version'], text=True).strip()}
print(json.dumps(result), flush=True)

def check_integrity(folder):
    results = {}
    for p in folder.glob('*.sqlite'):
        with sqlite3.connect(p) as c:
            results[p.name] = c.execute('PRAGMA integrity_check').fetchall()
        c.close()
    assert all(v == [('ok',)] for v in results.values()), results
    return results

try:
    with sqlite3.connect(root / 'probe.sqlite') as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE entries (id TEXT PRIMARY KEY, payload TEXT)')
    c.close()
    def worker(action, name):
        return Process([sys.executable, str(Path(__file__).with_name('locking_probe.py')), '--worker', str(root), action, name], root / (name + '.stderr'))
    a = worker('hold', 'holder')
    try:
        held = a.receive()
        b = worker('contend', 'contender')
        try:
            attempted = b.receive()
        finally:
            b.close()
    finally:
        a.close()
    assert set(held['locks'].values()) == {'acquired'}, held
    result['lock_exclusion_passed'] = attempted['locks'] == {'posix': 'blocked', 'flock': 'blocked', 'sqlite_writer': 'database is locked'}
    result['locks'] = attempted
    a, b = worker('write', 'a'), worker('write', 'b')
    try:
        a.receive(); b.receive(); a.send(); b.send()
        outcomes = [a.receive(), b.receive()]
    finally:
        a.close(); b.close()
    expected = set().union(*(set(x['committed']) for x in outcomes))
    with sqlite3.connect(root / 'probe.sqlite') as c:
        actual = {r[0] for r in c.execute('SELECT id FROM entries')}
    c.close()
    result['all_sqlite_writes_retained'] = len(expected) == 300 and expected == actual
    result['sqlite_writes'] = {'acknowledged': len(expected), 'retained': len(actual), 'integrity': check_integrity(root)}
    print(json.dumps({k: result[k] for k in ['locks', 'lock_exclusion_passed', 'all_sqlite_writes_retained', 'sqlite_writes']}), flush=True)

    for mode in ['legacy', 'paginated']:
        state = prepare('codex-independent-' + mode + '-')
        barrier = threading.Barrier(2)
        model = MockModel(state, lambda body: (barrier.wait(timeout=20), None)[1])
        servers = []
        expected_turns = {}
        try:
            servers = [AppServer(state, 'writer-a', binary=binary), AppServer(state, 'writer-b', binary=binary)]
            ids = [s.request('thread/start', {'cwd': str(state), 'historyMode': mode, 'approvalPolicy': 'never', 'sandbox': 'read-only'})['result']['thread']['id'] for s in servers]
            def turn(index, n):
                s, tid = servers[index], ids[index]
                prompt = f'Unique synthetic {mode} writer {index} turn {n}.'
                response = s.request('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': prompt}]})
                assert 'error' not in response, response
                while True:
                    event = s.receive()
                    if event.get('method') == 'turn/completed':
                        assert event['params']['turn']['status'] == 'completed', event
                        return prompt
            with ThreadPoolExecutor(2) as pool:
                for n in range(5):
                    futures = [pool.submit(turn, i, n) for i in range(2)]
                    for i, f in enumerate(futures):
                        expected_turns.setdefault(ids[i], []).append(f.result())
        finally:
            for s in servers:
                (state / (Path(s.log.name).stem + '-messages.json')).write_text(json.dumps(s.messages, indent=2))
                s.close()
            model.close()
        reader = AppServer(state, 'reopened', binary=binary)
        try:
            for tid, prompts in expected_turns.items():
                read = reader.request('thread/read', {'threadId': tid, 'includeTurns': True})
                turns = read['result']['thread']['turns']
                assert len(turns) == 5, read
                for t, prompt in zip(turns, prompts):
                    assert prompt in json.dumps(t) and 'Synthetic completed turn.' in json.dumps(t), t
                resumed = reader.request('thread/resume', {'threadId': tid})
                assert 'error' not in resumed, resumed
                (state / (tid + '-read.json')).write_text(json.dumps(read, indent=2))
        finally:
            reader.close()
        result[mode] = {'overlapping_turn_pairs': 5, 'acknowledged_and_retained_turns': 10, 'resume': 'passed', 'state': str(state), 'integrity': check_integrity(state / 'codex')}
        print(mode + ' independent conversations, reopen, resume: PASS', flush=True)
    result['status'] = 'passed' if result['lock_exclusion_passed'] and result['all_sqlite_writes_retained'] else 'failed'
except Exception as exc:
    result['status'] = 'failed_or_incomplete'
    result['exception'] = repr(exc)
    raise
finally:
    (root / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Summary:', root / 'summary.json', flush=True)
