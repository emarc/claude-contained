#!/usr/bin/env python3
"""Host-only lifecycle supervisor, embedded in both standalone launchers.
Regenerate embedded copies with: python3 tests/embed-managed-launch.py
Never mount this supervisor's lock directory into a container.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import uuid

LABEL = 'io.claude-contained.owner'


def fail(message):
    raise RuntimeError(message)


def canonical(path):
    path = Path(path).resolve(strict=True)
    if not path.is_dir():
        fail(f'Not a directory: {path}')
    # realpath alone preserves case aliases on case-insensitive macOS volumes.
    parts = []
    while path != path.parent:
        matches = [n for n in os.listdir(path.parent)
                   if n.casefold() == path.name.casefold() and os.path.samefile(path.parent / n, path)]
        parts.append(path.name if path.name in matches else matches[0])
        path = path.parent
    value = str(path.joinpath(*reversed(parts)))
    if any(c in value for c in '\n\r,:'):
        fail('Mount paths cannot contain commas, colons, or newlines.')
    return value


def key_for(folder):
    name = re.sub('[^a-z0-9]+', '-', Path(folder).name.lower()).strip('-')[:20] or 'root'
    return name + '-' + hashlib.sha256(folder.encode()).hexdigest()[:24]


def atomic_json(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with temp.open('x') as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if p.returncode:
        fail(f'Cannot verify {args[0]} container state; ownership retained. {p.stderr.strip()}')
    return p.stdout


def containers(runtime):
    if runtime == 'container':
        rows = json.loads(run(['container', 'list', '--all', '--format', 'json']))
        return [{'name': r['configuration']['id'], 'state': r['status']['state'],
                 'token': r['configuration'].get('labels', {}).get(LABEL)} for r in rows]
    rows = run(['docker', 'ps', '--all', '--format', '{{json .}}']).splitlines()
    return [{'name': r['Names'], 'state': r['State'],
             'token': dict(s.split('=', 1) for s in r.get('Labels', '').split(',') if '=' in s).get(LABEL)}
            for r in map(json.loads, rows)]


def live_owner(record):
    rows = containers(record['runtime'])
    matches = [r for r in rows if r['name'] == record['name']]
    if not matches:
        return None
    row = matches[0]
    if row['token'] != record['token']:
        fail('Container name now belongs to a different owner; refusing reuse or automatic recovery.')
    # An existing stopped container can still be restarted manually. Retain its
    # reservation until it is removed, rather than authorize a second mount.
    return row


def validate_mount_sources(home, paths, skills):
    protected = [Path(home) / '.codex', Path(home) / '.claude-contained']
    for source in paths + skills:
        p = Path(source)
        for q in protected:
            if p == q or p in q.parents or q in p.parents:
                fail(f'Mount {p} would expose native Codex state or contained stores. Select a project/skills subdirectory outside {q}.')


def validate_tool_args(tool, args):
    if tool != 'codex':
        return
    for arg in args:
        if arg in ('-C', '--cd') or arg.startswith(('--cd=', '-C')):
            fail('Pass the working folder to the launcher, not Codex --cd/-C, so history ownership matches it.')
        if 'sqlite_home' in arg or 'CODEX_HOME' in arg or 'CODEX_SQLITE_HOME' in arg:
            fail('Contained Codex database/home overrides are not supported.')


def prepare_store(base, folder):
    project = base / 'projects' / key_for(folder)
    identity = {'format': 1, 'canonical_path': folder}
    project.mkdir(parents=True, exist_ok=True)
    if project.is_symlink() or project.resolve() != project:
        fail('Contained state must not be redirected by symlinks.')
    existing = read_json(project / 'identity.json')
    if existing is not None and existing != identity:
        fail('Stored folder identity does not match this folder.')
    store = project / 'home'
    if existing is None:
        if store.exists():
            fail(f'Incomplete initialization at {project}; inspect it before retrying.')
        temporary = project / ('seed-' + uuid.uuid4().hex)
        temporary.mkdir(mode=0o700)
        native = Path(os.environ['HOME']) / '.codex'
        # Private copies only. Never copy databases, sidecars, transcripts,
        # caches, sockets, or the current native runtime directory wholesale.
        for name in ('auth.json', 'config.toml', 'AGENTS.md', 'skills', 'plugins'):
            source = native / name
            if source.is_dir():
                shutil.copytree(source, temporary / name, symlinks=False)
            elif source.is_file():
                shutil.copyfile(source, temporary / name)
        os.replace(temporary, store)
        atomic_json(project / 'identity.json', identity)
        print(f'Created private Codex home for {folder}. History starts fresh; setup is copied once.', file=sys.stderr)
    if not store.is_dir() or store.is_symlink():
        fail('Invalid contained Codex home.')
    # Do not follow state symlinks into some other store, even after a prior run.
    for parent, dirs, files in os.walk(store):
        for name in dirs + files:
            p = Path(parent) / name
            if p.is_symlink() and not p.resolve().is_relative_to(store):
                fail(f'Contained Codex home has an external symlink: {p}')
    return store


def prepare_shared(home):
    parent = Path(home) / '.claude-contained'
    with (parent / 'shared.lock').open('a') as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        shared = parent / 'shared'
        shared.mkdir(exist_ok=True)
        old, new = parent / '.claude.json', shared / '.claude.json'
        if old.is_file() and not old.is_symlink():
            if new.exists():
                fail('Both old and new shared Claude configs exist; resolve them before launch.')
            old.rename(new)
        if not old.exists() and not old.is_symlink():
            old.symlink_to('shared/.claude.json')
        native = Path(home) / '.claude.json'
        if native.is_file() and not native.is_symlink():
            os.replace(native, new)
        if not native.exists() and not native.is_symlink() and new.exists():
            native.symlink_to(new)
        gitconfig = Path(home) / '.gitconfig'
        if gitconfig.is_file():
            temporary = shared / ('.gitconfig.' + uuid.uuid4().hex)
            shutil.copyfile(gitconfig, temporary)
            os.replace(temporary, shared / '.gitconfig')
        return shared


def ready(record):
    row = live_owner(record)
    if not row or row['state'] != 'running':
        fail('The owning container is starting, stopped, or exiting. No second container was started.')
    p = subprocess.run([record['runtime'], 'exec', record['name'], 'cat',
                        '/run/claude-contained/identity.json'], capture_output=True, text=True, timeout=20)
    if p.returncode:
        fail('Container is not ready or uses an old image. Wait for startup, or rebuild the image after stopping it.')
    info = json.loads(p.stdout)
    if info != {'token': record['token'], 'runtime': record['runtime'], 'home': record['home']}:
        fail('Container ownership/readiness identity mismatch.')


def attach(record, request):
    if request['runtime'] != record['runtime']:
        fail(f"Folder is owned by {record['runtime']} container {record['name']}; use that runtime to attach.")
    if request['setup'] != record['setup']:
        differences = [k for k in request['setup'] if request['setup'][k] != record['setup'].get(k)]
        fail('Existing container has incompatible launch options: ' + ', '.join(differences) + '. Repeat the original setup options or stop the owner first.')
    ready(record)
    if request['tool'] == 'codex' and not request['shell'] and record['runtime'] == 'docker':
        p = subprocess.run(['docker', 'exec', '-u', 'dev', record['name'], '/usr/local/bin/codex', '--cc-check-idle'],
                           capture_output=True, text=True, timeout=20)
        if p.returncode:
            fail(p.stderr.strip() or 'Docker permits only one Codex process per folder.')
    print(f"Folder {record['folder']} already uses {record['runtime']} container {record['name']}.\n"
          'Attachment starts another process sharing its filesystem and lifecycle.\n'
          'It can be terminated when the original container session exits.', file=sys.stderr)
    try:
        answer = input(f"Start {'bash' if request['shell'] else request['tool']} in this container? [y/N] ")
    except EOFError:
        answer = ''
    if answer.lower() not in ('y', 'yes'):
        fail('Attachment cancelled; no new container started.')
    ready(record)
    home = record['home']
    command = ['bash'] if request['shell'] else [('/opt/claude/claude' if request['tool'] == 'claude' else request['tool'])]
    if not request['shell']:
        if request['yolo'] and request['tool'] != 'vibe':
            command.append('--dangerously-skip-permissions' if request['tool'] == 'claude' else '--yolo')
        for path in request['setup']['paths'][1:]:
            if request['tool'] in ('claude', 'codex'):
                command.extend(['--add-dir', path])
        command.extend(request['args'])
    env = {'HOME': home, 'JAVA_HOME': '/opt/jbr', 'CARGO_HOME': home + '/.cargo',
           'RUSTUP_HOME': home + '/.rustup', 'DYLINT_DRIVER_PATH': home + '/.cargo/dylint-drivers',
           'PATH': home + '/.cargo/bin:' + home + '/.local/bin:/opt/claude:/opt/jbr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}
    argv = [record['runtime'], 'exec', '-it', '-u', 'dev', '-w', record['folder']]
    for k, v in env.items():
        argv.extend(['-e', k + '=' + v])
    return subprocess.call(argv + [record['name']] + command)


def supervise(request, launcher, original):
    home = canonical(os.environ['HOME'])
    base = Path(home) / '.claude-contained' / 'codex'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if base.resolve() != base:
        fail('The contained state root cannot be a symlink.')
    locks = base / 'locks'
    locks.mkdir(exist_ok=True)
    rows = containers(request['runtime'])
    # Legacy containers still expose the entire state parent through a broad
    # bind mount. Require their shutdown before creating private stores there.
    legacy = [r['name'] for r in rows if r['name'].startswith('aic-') and not r['token']]
    if legacy:
        fail('Remove legacy containers before using isolated state: ' + ', '.join(legacy))
    selected = None
    if request['attach']:
        records = [read_json(p) for p in locks.glob('*.json')]
        records = [r for r in records if r and r['runtime'] == request['runtime']]
        name = request['name']
        if not name:
            records = [r for r in records if any(c['token'] == r['token'] and c['state'] == 'running' for c in rows)]
            if not records:
                fail('No running managed containers to attach to.')
            for i, r in enumerate(records, 1):
                print(f"{i}) {r['name']} — {r['folder']}")
            try:
                choice = input('Select container number (empty cancels): ')
            except EOFError:
                choice = ''
            if not choice.isdigit() or not 1 <= int(choice) <= len(records):
                fail('Attachment cancelled.')
            selected = records[int(choice) - 1]
        else:
            name = 'aic-' + re.sub('[^a-z0-9-]', '-', name.removeprefix('aic-').lower())
            selected = next((r for r in records if r['name'] == name), None)
            if selected is None and any(r['name'] == name for r in rows):
                fail('Container has no matching ownership record; refusing unsafe attachment.')
        if selected and not request['explicit_paths']:
            request['setup']['paths'] = [selected['folder']]
            request['setup']['modes'] = ['rw']
    request['setup']['paths'] = [canonical(p) for p in request['setup']['paths']]
    request['setup']['skills'] = [canonical(p) for p in request['setup']['skills']]
    folder = request['setup']['paths'][0]
    if selected and folder != selected['folder']:
        fail('Requested working folder differs from the selected container.')
    validate_mount_sources(home, request['setup']['paths'], request['setup']['skills'])
    validate_tool_args(request['tool'], request['args'])
    key = key_for(folder)
    record_path = locks / (key + '.json')
    # Never unlink these lock files: deleting a locked inode would create two
    # independent locks. The native kernel releases flock after process death.
    with (locks / (key + '.lock')).open('a') as mutex:
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record = read_json(record_path)
            if not record:
                fail('Another launch is reserving this folder. Retry when it is ready.')
            return attach(record, request)
        record = read_json(record_path)
        if record and live_owner(record):
            return attach(record, request)
        if selected:
            fail('Selected container has exited. Retry as a normal launch.')
        token = uuid.uuid4().hex
        name = request['name']
        name = ('aic-' + re.sub('[^a-z0-9-]', '-', name.removeprefix('aic-').lower())) if name else 'aic-' + key[:20] + '-' + token[:8]
        if any(r['name'] == name for r in rows):
            fail('Requested container name is already in use.')
        store = prepare_store(base, folder)
        shared = prepare_shared(home)
        record = {'format': 1, 'folder': folder, 'home': home, 'runtime': request['runtime'],
                  'name': name, 'token': token, 'setup': request['setup']}
        atomic_json(record_path, record)
        env = dict(os.environ, HOME=home, CC_TOKEN=token, CC_CODEX_HOME=str(store), CC_CONTAINER_NAME=name,
                   CC_MAIN_FOLDER=folder, CC_RUNTIME=request['runtime'], CC_SHARED_DIR=str(shared),
                   CC_PATHS='\n'.join(request['setup']['paths']), CC_SKILLS='\n'.join(request['setup']['skills']))
        # The child bash retains the lock FD too, so killing this supervisor
        # cannot authorize a competing launcher while the first is starting.
        child = subprocess.Popen(['bash', launcher, *original], env=env, pass_fds=(mutex.fileno(),))
        def forward(sig, frame):
            if child.poll() is None:
                child.send_signal(sig)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, forward)
        status = child.wait()
        try:
            if not live_owner(record) and read_json(record_path) == record:
                record_path.unlink()
        except (RuntimeError, subprocess.TimeoutExpired):
            print('Runtime state unavailable; retaining folder ownership for the next launch to verify.', file=sys.stderr)
        return status


def main(argv):
    if sys.version_info < (3, 9):
        fail('Managed launchers require host Python 3.9 or newer.')
    runtime, launcher, tool, shell, yolo, attach_mode, name, ssh, worktree, locks, node, memory, explicit = argv[:13]
    rest = iter(argv[13:])
    def array():
        return [next(rest) for _ in range(int(next(rest)))]
    paths, modes, skills, ports, forwards, args, original = [array() for _ in range(7)]
    request = {'runtime': runtime, 'tool': tool, 'shell': shell == '1', 'yolo': yolo == '1',
               'attach': attach_mode == '1', 'name': name, 'explicit_paths': explicit == '1', 'args': args,
               'setup': {'paths': paths, 'modes': modes, 'skills': skills, 'ports': ports, 'forwards': forwards,
                         'ssh': ssh, 'worktree': worktree, 'worktree_locks': locks, 'node_modules': node, 'memory': memory,
                         'image': os.environ.get('CLAUDE_CONTAINED_IMAGE', 'claude-contained:latest'),
                         'gh_token_hash': hashlib.sha256(os.environ.get('AI_GH_TOKEN', '').encode()).hexdigest()}}
    if tool not in ('claude', 'codex', 'copilot', 'gemini', 'vibe'):
        fail('Unknown tool: ' + tool)
    os.umask(0o077)
    return supervise(request, launcher, original)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
