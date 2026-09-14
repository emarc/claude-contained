#!/usr/bin/env python3
"""Prototype Mac-owned Codex history with disposable remote execution servers.

Uses synthetic state, a loopback fake model, and only a scratch project mount.
No account credentials, model charges, real history, or launcher changes.
"""
import collections
import argparse
import json
from pathlib import Path
import shlex
import shutil
import socket
import sqlite3
import subprocess
import time
import uuid

from history_probe import AppServer, prepare
from mock_model import MockModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict-config', action='store_true', help='Check whether remote-MCP config is accepted')
    args = parser.parse_args()
    root = prepare('codex-execution-probe-')
    project = root / 'project'
    project.mkdir()
    shutil.copyfile(Path(__file__).with_name('mcp_fixture.py'), project / 'mcp_fixture.py')
    skill = root / 'codex/skills/contained-probe-skill'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: contained-probe-skill\ndescription: Synthetic skill discovery marker for container investigation.\n---\nThis is a synthetic skill.\n')
    with (root / 'codex/config.toml').open('a') as f:
        f.write('\n[mcp_servers.probe_remote]\ncommand = "/usr/bin/python3"\nargs = [' +
                json.dumps(str(project / 'mcp_fixture.py')) + ']\nexperimental_environment = "remote"\n')
    calls = {}
    results = {'directory': str(root)}
    containers = []
    server = None
    def choose(body):
        label = None
        for item in body.get('input', []):
            if item.get('role') == 'user':
                for content in item.get('content', []):
                    text = content.get('text', '')
                    if text.startswith('Synthetic execution check '):
                        label = text.removeprefix('Synthetic execution check ')
        pending = calls.get(label)
        if not pending:
            return None
        name, args = pending.popleft()
        uid = uuid.uuid4().hex
        item = {'type': 'function_call', 'id': 'fc_' + uid, 'call_id': 'call_' + uid,
                'name': name, 'arguments': json.dumps(args)}
        if '::' in name:
            item['namespace'], item['name'] = name.split('::', 1)
        return item
    model = MockModel(root, choose)
    print('Synthetic execution state:', root, flush=True)

    def launch(label):
        name = root.name + '-' + label
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        log = root / (label + '-container.log')
        cmd = ['container', 'run', '--rm', '-d', '--name', name,
               '--publish', '127.0.0.1:' + str(port) + ':4500',
               '--mount', 'type=bind,src=' + str(project) + ',dst=' + str(project),
               '--env', 'CODEX_HOME=/tmp/probe-codex', '--env', 'HOME=/tmp/probe-home',
               '--entrypoint', '/bin/sh', 'claude-contained:latest', '-c',
               'mkdir -p /tmp/probe-codex /tmp/probe-home; '
               'printf %s ' + shlex.quote(label) + ' > /tmp/execution-probe-identity; '
               'exec /usr/local/bin/codex exec-server --listen ws://0.0.0.0:4500']
        containers.append(name)
        with log.open('w') as f:
            subprocess.run(cmd, stdout=f, stderr=f, check=True, timeout=55)
        version = subprocess.run(['container', 'exec', name, '/usr/local/bin/codex', '--version'],
                                 text=True, capture_output=True, timeout=15)
        results['version_' + label] = version.stdout.strip()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                    return name, 'ws://127.0.0.1:' + str(port)
            except OSError:
                time.sleep(0.2)
        raise TimeoutError('container listener: ' + name)

    def require(response):
        if 'error' in response:
            raise RuntimeError(str(response['error']))
        return response['result']

    def attach(label, url):
        response = server.request('environment/add', {'environmentId': label,
                       'execServerUrl': url, 'connectTimeoutMs': 10000})
        results['attach_' + label] = response
        print('Attach', label, response.get('error', 'ok'), flush=True)
        require(response)
        deadline = time.monotonic() + 20
        while True:
            info = server.request('environment/info', {'environmentId': label})
            if 'result' in info or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        results['info_' + label] = info
        print('Environment info', label, str(info)[:1400], flush=True)
        require(info)

    def begin_turn(tid, label, environment):
        command = 'python3 -c ' + shlex.quote(
            'import json,pathlib,platform,time; time.sleep(1); '
            'print("execution_system="+platform.system(),flush=True); '
            'identity=pathlib.Path("/tmp/execution-probe-identity").read_text(); '
            'assert platform.system()=="Linux", platform.system(); '
            'pathlib.Path(' + repr(label + '.txt') + ').write_text(identity); '
            'print(json.dumps({"system":platform.system(),"container":identity,"cwd":str(pathlib.Path.cwd())}))')
        calls[label] = collections.deque([('exec_command', {'cmd': command, 'workdir': str(project), 'login': False}),
                                         ('mcp__probe_remote::prove_container', {})])
        offset = len(server.messages)
        params = {'threadId': tid, 'input': [{'type': 'text', 'text': 'Synthetic execution check ' + label}]}
        if environment is not None:
            params['environments'] = [{'environmentId': environment, 'cwd': str(project)}]
        response = server.request('turn/start', params)
        require(response)
        return offset

    def finish_turn(tid, label, offset):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            event = next((m for m in server.messages[offset:] if m.get('method') == 'turn/completed'
                          and m.get('params', {}).get('threadId') == tid), None)
            if event:
                results[label] = {'turn': event, 'messages': server.messages[offset:],
                                  'marker': (project / (label + '.txt')).read_text() if (project / (label + '.txt')).exists() else None}
                print(label, event['params']['turn']['status'], 'marker', results[label]['marker'], flush=True)
                return
            server.receive(max(0.01, deadline - time.monotonic()))
        raise TimeoutError('turn ' + label)

    def turn(tid, label, environment):
        finish_turn(tid, label, begin_turn(tid, label, environment))

    try:
        a, url_a = launch('container-a')
        b, url_b = launch('container-b')
        server = AppServer(root, 'backend', extra=['--strict-config'] if args.strict_config else [])
        attach('a', url_a)
        attach('b', url_b)
        thread_ids = []
        for label in ['a', 'b']:
            response = server.request('thread/start', {'cwd': str(project),
                       'approvalPolicy': 'never', 'sandbox': 'danger-full-access',
                       'environments': [{'environmentId': label, 'cwd': str(project)}]})
            results['start_' + label] = response
            tid = require(response)['thread']['id']
            thread_ids.append(tid)
            turn(tid, 'initial-' + label, label)
        offset_a = begin_turn(thread_ids[0], 'concurrent-a', 'a')
        offset_b = begin_turn(thread_ids[1], 'concurrent-b', 'b')
        finish_turn(thread_ids[0], 'concurrent-a', offset_a)
        finish_turn(thread_ids[1], 'concurrent-b', offset_b)
        subprocess.run(['container', 'stop', a], check=True, timeout=20, capture_output=True)
        containers.remove(a)
        c, url_c = launch('container-c')
        attach('c', url_c)
        results['resume_new_container'] = server.request('thread/resume', {'threadId': thread_ids[0]})
        require(results['resume_new_container'])
        turn(thread_ids[0], 'resumed-c', 'c')
        (root / 'backend-messages.json').write_text(json.dumps(server.messages, indent=2))
        server.close()
        server = AppServer(root, 'restarted-backend', extra=['--strict-config'] if args.strict_config else [])
        results['resume_restarted_backend'] = server.request('thread/resume', {'threadId': thread_ids[0],
                         'approvalPolicy': 'never', 'sandbox': 'danger-full-access'})
        require(results['resume_restarted_backend'])
        try:
            turn(thread_ids[0], 'missing-environment', None)
        except RuntimeError as e:
            results['missing-environment'] = {'error': str(e)}
        attach('c-restarted', url_c)
        turn(thread_ids[0], 'resumed-after-backend-restart', 'c-restarted')
        for tid in thread_ids:
            response = server.request('thread/read', {'threadId': tid, 'includeTurns': True})
            results['final_history_' + tid] = response
        results['all_markers_present'] = all(results[k]['marker'] for k in
            ['initial-a', 'initial-b', 'concurrent-a', 'concurrent-b', 'resumed-c', 'resumed-after-backend-restart'])
        results['skill_discovered'] = any('contained-probe-skill' in json.dumps(r.get('input')) for r in model.requests)
        print('Execution checks finished', flush=True)
    finally:
        if server:
            (root / 'last-backend-messages.json').write_text(json.dumps(server.messages, indent=2))
            server.close()
        model.close()
        for name in containers:
            logs = subprocess.run(['container', 'logs', name], capture_output=True, text=True, timeout=10)
            (root / (name + '.log')).write_text(logs.stdout + logs.stderr)
            subprocess.run(['container', 'stop', name], capture_output=True, timeout=20)
        (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print('Results:', root / 'results.json', flush=True)


if __name__ == '__main__':
    main()
