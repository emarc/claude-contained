# Implementation validation — 2026-09-14

Both launchers now use the embedded `managed-launch.py` supervisor. Per-folder
private Codex homes are protected by native host flock and persistent runtime/token
records. `codex-guard.py` pins state paths and enforces Docker's single-process rule
with a Linux-local lock. Apple permits concurrent guarded Codex processes.

Existing containers are reused only after affirmative consent and setup matching.
Explicit attach and debug-shell Codex invocation use the same ownership/guard
rules. The broad state-parent mount was replaced by the shared Claude config
subdirectory; native Codex state is never mounted. Old images cannot use the new
managed entrypoint and must be rebuilt. Production images were not replaced.

Validation completed with Codex 0.154.0 in separate test images layered over the
installed images, using the current entrypoint and guard:

| Check | Result |
| --- | --- |
| Nine host tests, including real launcher invocations against fake runtimes | Passed |
| Simultaneous same-folder launches | Exactly one container created |
| Consent, EOF/decline, incompatible options, forwarded arguments, explicit attach | Passed |
| Mixed-runtime refusal and Docker busy refusal | Passed |
| Private setup seeding and Claude config migration | Passed |
| Existing shared-skills and worktree shell suites | Passed |
| Embedded-source parity, shell syntax, whitespace | Passed |
| Apple real launcher attachment as UID 501 | Passed |
| Apple two independent guarded backends, legacy and paginated | All 12 synthetic turns retained |
| Docker second guarded Codex process as UID 501 | Refused while first was active |
| Docker sequential legacy and paginated history | All 6 synthetic turns retained |
| Both runtimes: abrupt container kill, replacement, read and resume | Passed |
| Both runtimes: surviving orphan, subsequent launcher attachment | Passed |

The worktree test needed canonical temporary paths on macOS (`/var` versus
`/private/var`). Its failure was reproduced against the pre-change launcher before
fixing the test. Docker's orphan test deliberately keeps its shell alive after
terminal disconnect; an ordinary idle shell may exit, in which case a fresh
container is legitimate rather than an orphan-reuse case.

Real-runtime evidence remains in disposable directories:

- Apple: `/private/tmp/cc-managed-container-fcvd8zib`
- Docker: `/private/tmp/cc-managed-docker-dtg7uf50`

These tests used fresh HOME/project directories and a loopback fake model. No
real credentials, existing history repair, native-state import, or production
rollout was performed. Authentication/provider and plugin behavior with real user
accounts was not exercised. Direct binaries bypassing the guard and background
app-server daemons are unsupported.

Reproduce the host tests with `python3 tests/managed-launch.test.py`. The opt-in
`tests/managed-runtime.test.py container|docker` requires the documented separate
test image. See the README for normal builds and rollout requirements.
