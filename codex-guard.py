#!/usr/bin/python3
"""Container-local guard. Runtime identity is root-owned, never host-mounted."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


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
        if 'sqlite_home' in arg or 'CODEX_HOME' in arg or 'CODEX_SQLITE_HOME' in arg:
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
        env = dict(os.environ, HOME=identity['home'], CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home))
        # CLI overrides win over copied config/profile defaults. File credentials
        # live in this private home, avoiding a shared credential refresh store.
        command = ['/usr/local/bin/cc-codex-real', '-c', 'sqlite_home=' + json.dumps(str(home)),
                   '-c', 'cli_auth_credentials_store="file"', *args]
        child = subprocess.Popen(command, env=env, pass_fds=(lock.fileno(),))
        def forward(sig, frame):
            if child.poll() is None:
                child.send_signal(sig)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, forward)
        return child.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
