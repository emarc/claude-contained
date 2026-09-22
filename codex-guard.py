#!/usr/bin/python3
"""Container-local guard. Runtime identity is root-owned, never host-mounted."""
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys


def reconcile_auth(home, shared):
    """Codex logout unlinks the symlink; a subsequent login may replace it."""
    auth = home / 'auth.json'
    target = shared / 'auth.json'
    if auth.is_symlink():
        if os.readlink(auth) != str(target):
            raise RuntimeError('Unexpected Codex auth symlink.')
        return
    if auth.is_file():
        shutil.copyfile(auth, target)
        target.chmod(0o600)
        auth.unlink()
    else:
        target.unlink(missing_ok=True)
    auth.symlink_to(target)


def config_overrides(args):
    """Yield the keys of -c/--config overrides only; prompt text is never inspected."""
    pending = False
    for arg in args:
        if pending:
            pending = False
            yield arg.split('=', 1)[0].strip().strip('"')
        elif arg in ('-c', '--config'):
            pending = True
        elif arg.startswith('--config='):
            yield arg[9:].split('=', 1)[0].strip().strip('"')
        elif arg.startswith('-c') and not arg.startswith('--'):
            yield arg[2:].removeprefix('=').split('=', 1)[0].strip().strip('"')


def main():
    identity_path = Path('/run/claude-contained/identity.json')
    if not identity_path.is_file():
        raise RuntimeError('Codex requires a managed per-folder container. Rebuild the image and launch through claude-contained or claude-docked.')
    identity = json.loads(identity_path.read_text())
    home = Path(identity['home']) / '.codex'
    if home.is_symlink() or home.resolve() != home:
        raise RuntimeError('Codex home must remain inside the managed folder store.')
    args = sys.argv[1:]
    for arg in args:
        if arg in ('-C', '--cd') or arg.startswith(('-C', '--cd=')):
            raise RuntimeError('Select the folder through the launcher; Codex --cd/-C is disabled.')
    for key in config_overrides(args):
        if key.rsplit('.', 1)[-1] in ('sqlite_home', 'codex_home'):
            raise RuntimeError('Contained Codex state-location overrides are disabled.')
    if 'app-server' in args and 'daemon' in args:
        raise RuntimeError('Background Codex daemons are unsupported in managed containers.')
    for p in home.iterdir():
        if ('.sqlite' in p.name or p.name in ('sessions', 'archived_sessions')) and p.is_symlink():
            raise RuntimeError('Codex database/transcript paths cannot be symlinks.')
    # This file is on container-local /run, NOT the problematic host bind mount.
    with open('/run/claude-contained/codex.lock', 'r+') as lock:
        if identity['runtime'] == 'docker':
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Docker permits only one Codex session per folder. Close the active Codex session first.')
        if args == ['--cc-check-idle']:
            return 0
        shared = Path(identity['home']) / '.claude-contained/codex/auth'
        if not shared.is_dir():
            raise RuntimeError('Shared Codex auth is not mounted. Restart with the updated launcher.')
        auth = home / 'auth.json'
        if not auth.is_symlink() or os.readlink(auth) != str(shared / 'auth.json'):
            raise RuntimeError('Shared Codex auth link is missing. Restart the managed container.')
        env = dict(os.environ, HOME=identity['home'], CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home))
        # CLI logout must delete the shared file, rather than just its symlink.
        if args == ['logout']:
            env['CODEX_HOME'] = str(shared)
        # The credential file is shared; history remains in the private home.
        command = ['/usr/local/bin/cc-codex-real', '-c', 'sqlite_home=' + json.dumps(str(home)),
                   '-c', 'cli_auth_credentials_store="file"', *args]
        child = subprocess.Popen(command, env=env, pass_fds=(lock.fileno(),))
        def forward(sig, frame):
            if child.poll() is None:
                child.send_signal(sig)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, forward)
        try:
            return child.wait()
        finally:
            reconcile_auth(home, shared)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
