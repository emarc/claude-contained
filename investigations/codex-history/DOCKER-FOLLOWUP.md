# Docker follow-up — 2026-09-11

The same synthetic probe was run against Docker Engine 27.4.0 on macOS, using host bind mounts and python:3.11-slim (SQLite 3.46.1; host SQLite 3.53.4). Only disposable temporary data was mounted. No real Codex state was accessed.

POSIX locks, flock, and SQLite BEGIN IMMEDIATE excluded the second native writer, but allowed both writers across host/container, container/host, and container/container boundaries.

| Writers | Reported commits | Retained rows | Missing writes | Integrity |
| --- | ---: | ---: | ---: | --- |
| Host + host | 300 | 300 | 0 | ok |
| Host + container | 300 | 151 | 149 | ok |
| Container + container | 300 | 165 | 135 | index errors |

All writers reported zero SQLite errors. This was one run on the installed Docker configuration, not a claim about every Docker runtime or filesystem. Native Linux Docker and Docker named volumes were not tested.

Reproduce: `python3 investigations/codex-history/docker_locking_probe.py`. This is the original probe with Docker run/stop commands and a distinct scratch-directory prefix. Recorded evidence: [results/docker-locking.json](results/docker-locking.json).

Online context: [docker/for-mac#7004](https://github.com/docker/for-mac/issues/7004) reports an exclusive flock failure with VirtioFS. It is related evidence, not proof of the precise cause of this run. [SQLite WAL documentation](https://www.sqlite.org/wal.html) explains the shared-memory requirement.

The earlier investigation's “Docker not tested” statements describe its original scope; this follow-up supplies that test.
