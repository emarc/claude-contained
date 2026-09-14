#!/usr/bin/env python3
"""Copy stopped Codex state to a NEW backup directory; never repair the source.

Run from ordinary Terminal AFTER all clients/servers/containers using the source
are stopped. --clients-stopped is an explicit acknowledgement, not a process check.
Sockets/FIFOs are recorded and skipped. Regular files and symlinks are preserved.
Hashes verify file contents; extended attributes/ACLs are not part of this backup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    result = {}
    def walk(folder):
        for path in sorted(folder.iterdir()):
            s = path.lstat()
            key = str(path.relative_to(root))
            item = {'mode': s.st_mode, 'size': s.st_size, 'mtime_ns': s.st_mtime_ns}
            if stat.S_ISLNK(s.st_mode):
                item['kind'] = 'symlink'
                item['target'] = os.readlink(path)
            elif stat.S_ISDIR(s.st_mode):
                item = {'kind': 'directory', 'mode': s.st_mode}
            elif stat.S_ISREG(s.st_mode):
                item['kind'] = 'file'
                item['sha256'] = digest(path)
            else:
                item = {'kind': 'skipped-special-file', 'mode': s.st_mode}
            result[key] = item
            if item['kind'] == 'directory':
                walk(path)
    walk(root)
    return result


def backup(source, destination):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or source in destination.parents:
        raise ValueError('Backup must be outside the source directory')
    if destination.exists():
        raise FileExistsError('Refusing to overwrite ' + str(destination))
    before = inventory(source)
    destination.mkdir(mode=0o700, parents=False)
    target = destination / 'state'
    target.mkdir(mode=0o700)
    try:
        for name, item in before.items():
            src, dst = source / name, target / name
            if item['kind'] == 'directory':
                dst.mkdir()
            elif item['kind'] == 'symlink':
                dst.symlink_to(item['target'])
            elif item['kind'] == 'file':
                shutil.copy2(src, dst)
                if digest(dst) != item['sha256']:
                    raise RuntimeError('File changed while copying: ' + name)
        after = inventory(source)
        if before != after:
            raise RuntimeError('Source changed during backup; this copy is not a verified snapshot')
        manifest = {'source': str(source), 'verified': True, 'files': before}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('Verified backup:', destination)
        print('Copied', sum(v['kind'] == 'file' for v in before.values()), 'regular files')
        print('Skipped', sum(v['kind'] == 'skipped-special-file' for v in before.values()), 'runtime special files')
    except BaseException:
        (destination / 'INCOMPLETE.txt').write_text('Backup did not complete verification. Keep the source intact.\n')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True, help='New directory; parent must exist')
    parser.add_argument('--clients-stopped', action='store_true', required=True)
    args = parser.parse_args()
    backup(args.source, args.destination)
