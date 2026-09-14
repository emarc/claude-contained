# Same-container concurrency — 2026-09-14

Tested now, before launcher implementation. Both installed `claude-contained:latest`
images reported Codex CLI 0.154.0. Each test ran multiple processes inside **one**
container, with fresh host bind-mounted state. No native Codex state or credentials
were mounted. The existing loopback fake model supplied responses; no external
model calls were made. Test containers exited and were removed.

| Check | Apple Containers | Docker on this Mac |
| --- | --- | --- |
| POSIX lock, flock, SQLite writer exclusion | All blocked the contender | All allowed both writers |
| SQLite acknowledged writes retained | 300 / 300 | 257 / 300 |
| SQLite integrity after write test | `ok` | `ok`, despite 43 missing writes |
| Independent Codex backends, legacy history | 10 / 10 completed turns retained after backend restart; resume passed | Same check passed |
| Independent Codex backends, paginated history | 10 / 10 completed turns retained after backend restart; resume passed | Incomplete: fake-model barrier timed out; no pass claimed |
| Two TUIs resuming the same conversation, both history modes | Second client blocked while owner was active and idle | Second client proceeded; idle-owner contender also proceeded |

Independent conversation tests used two app-server processes sharing one synthetic
Codex home, five turns per conversation, and a model-response barrier to force
overlap. Reopened history was checked for every expected prompt and assistant
response, not just database integrity. These were backend tests, not independent
conversation TUIs. Duplicate-resume tests used actual TUIs with PTYs.

Apple's duplicate-resume TUIs displayed “This conversation is open in another app”
and remained open awaiting retry, instead of exiting as observed with 0.153.4.
Only the seed and client A's continuation were retained. Resume succeeded after
closing the clients. All inspected synthetic Codex databases passed integrity.

Docker's duplicate-resume runs sent requests from competing clients. Legacy
history retained the seed and A/B/C continuations. Paginated history returned four
turns but lacked C's expected prompt. All inspected databases nevertheless passed
integrity. This is additional evidence against relying on the same-container
duplicate-session guard on this Docker bind mount.

## Consequence for implementation

The proposed Apple same-container concurrency behavior has passed this focused
test gate. Implement reuse with concurrent independent Codex conversations there,
then verify it through the final launcher and dev-user setup. Per-folder private
state and host ownership excluding other containers are still necessary.

Do not enable concurrent Codex processes against Docker's host bind-mounted home
on the strength of sharing a container. Investigate Linux-local persistent storage
(for example a Docker named volume), or enforce one Codex process per store until
an alternative passes these checks. No named-volume test was performed here.

These results apply to the installed images/runtime configuration. The probes
bypassed the normal entrypoint and ran as container root; final launcher tests
must still cover the dev user, attach lifecycle, container replacement, and crashes.

## Evidence and reproduction

- [Apple summary](results/same-container-apple.json)
- [Docker summary](results/same-container-docker.json)
- [Combined probe](same_container_probe.py)
- [TUI probe](concurrent_resume_probe.py), now accepts `--binary`

Create a fresh host scratch directory, mount it at `/private/tmp` in one container,
and mount this investigation directory read-only at `/probe`. Override the image
entrypoint with `/bin/bash`, then run:

```sh
python3 /probe/same_container_probe.py
python3 /probe/concurrent_resume_probe.py --binary /usr/local/bin/codex --history-mode paginated
python3 /probe/concurrent_resume_probe.py --binary /usr/local/bin/codex --history-mode legacy
```

The first probe asserts Codex history retention and records raw SQLite failures.
Inspect `summary.json` as well as exit status. The TUI probe saves terminal logs
and post-exit history for inspection; its successful exit alone is not a pass.
Raw results remain under `/private/tmp/codex-one-container-{apple,docker}-20260914`
on the host. Summary `root` paths beginning `/private/tmp/codex-...` refer to the
inside-container mount and resolve underneath the corresponding host directory.
