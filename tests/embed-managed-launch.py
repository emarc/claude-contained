#!/usr/bin/env python3
"""Keep standalone launcher copies identical to the maintained Python source."""
from pathlib import Path
import sys
root = Path(__file__).resolve().parent.parent
source = (root / 'managed-launch.py').read_text().rstrip()
for name in ('claude-contained', 'claude-docked'):
    p = root / name
    text = p.read_text()
    start = text.index('# BEGIN EMBEDDED MANAGED LAUNCH\n')
    end = text.index('# END EMBEDDED MANAGED LAUNCH\n', start)
    replacement = '# BEGIN EMBEDDED MANAGED LAUNCH\n' + source + '\n'
    updated = text[:start] + replacement + text[end:]
    if '--check' in sys.argv:
        if text != updated:
            raise SystemExit(f'{name}: run python3 tests/embed-managed-launch.py')
    else:
        p.write_text(updated)
