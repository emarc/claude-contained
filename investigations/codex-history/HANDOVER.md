# Codex history isolation: findings and implementation handover

Date: 2026-09-10. Repository: `/Users/marc/Projects/claude-contained`.

Update 2026-09-14: [same-container tests](SAME-CONTAINER.md) now pass the focused
concurrency gate for Apple Containers with Codex 0.154.0. Docker host bind mounts
failed even inside one container (43 of 300 acknowledged SQLite writes lost).
Implement Apple concurrent reuse and verify the final launcher integration;
Docker needs a validated storage alternative or single-process enforcement.
The earlier [Docker follow-up](DOCKER-FOLLOWUP.md) also supersedes the original
“Docker not tested” statements below.

Implementation update 2026-09-14: the launchers now implement private per-folder
homes, native host lifetime locks with orphan detection, and consent-based reuse
for all agents. Apple supports concurrent guarded Codex processes; Docker uses a
container-local single-process lock. See the repository README and
`managed-launch.py` / `codex-guard.py`. Rebuild images before rollout; existing
history is not imported or repaired. The design text below preserves the original
handoff and its reasoning.

## Start here

**The preferred next implementation is persistent, independent Codex state per
folder under `~/.claude-contained/codex/`, with host-side exclusion preventing
two containers from using the same folder's Codex state concurrently.**

This is the user's chosen direction for further work, not an implemented or
fully validated fix. The current request was to preserve the investigation and
design in files before this conversation might become unavailable.

More precisely, this is **per-folder Codex state isolation**, not Codex's
conversation-fork operation. Each folder gets its own durable Codex home,
including history databases and transcripts. Its containers remain ephemeral;
its history survives and is reused on subsequent launches for that folder.
Contained history is intentionally disconnected from native Codex CLI and
Desktop history. There is no automatic synchronization or merge back.

This tradeoff is accepted: reliable contained history is more useful than
seamless sharing that can lose or corrupt history. Different folders should
still support simultaneous contained Codex use. State isolation is **Codex-only**;
do not isolate Claude Code or other tools' state without further evidence and a separate decision. The same-folder
container reuse prompt described below applies to **all supported agents**.

No production launcher, alias, image, user configuration, or real history has
been changed by this investigation. Diagnostic scripts, results, and these
notes are ordinary repository files, currently untracked under `investigations/`.
They survive terminal closure independently of Codex history, but have not been
committed or published. Raw scratch evidence in `/private/tmp` is less durable;
the essential results have been saved in [results/](results/).

## What prompted this

The user sometimes runs multiple Codex CLIs through a `codex` alias that launches
`claude-contained`, alongside native ChatGPT Desktop with Codex on the Mac.
Containers are normally disposable, one CLI session at a time. Resuming history
across container launches is an essential feature, not an optional convenience.

Reported errors:

```text
thread/resume failed during TUI bootstrap: thread/resume failed:
list_turns is not supported yet (code -32601)

failed to list thread history: thread-store internal error:
failed to access thread history: error returned from database:
(code: 11) database disk image is malformed
```

Do not infer historical safety from whether a container is running now. The
problem concerns earlier overlapping access as well as future launches.

## Verified findings

### Shared mounts permit conflicting writers

Both `claude-contained` and `claude-docked` currently bind-mount the entire native
`~/.codex` read/write at the corresponding path in the container. In
`claude-contained`, see `HOST_CODEX_DIR` near line 957 and its mount near 1017.
The existing mutex protects Git worktree bookkeeping, not Codex state. Claude's
`.claude.json` workaround also does not coordinate Codex databases.

With Apple Container CLI 1.4.1, disposable synthetic tests found:

| Holder and contender | POSIX lock / flock / SQLite WAL writer exclusion |
| --- | --- |
| Mac and Mac | Exclusion worked |
| Mac and container | Both acquired |
| Container and Mac | Both acquired |
| Container and container | Both acquired |

Two writers each committed 150 uniquely identified inserts. Native/native
retained all 300. Native/container and container/container each reported 300
successful commits but retained only 150 rows. Workers reported no errors and
the final database integrity checks returned `ok`.

This proves a reproducible silent-write-loss mechanism using the launcher's
mount arrangement. It does not identify the exact historical operation that
damaged the user's state or the specific underlying Apple implementation bug.
Desktop is not needed to trigger the demonstrated failure. Docker was not tested.
See [locking results](results/locking.json) and [the detailed report](README.md).

### Real state already has damage; the short error remains unexplained

Earlier read-only checks of native `~/.codex` found corruption in
`state_5.sqlite`, `thread_history_1.sqlite`, and `logs_2.sqlite`. Memories, goals,
and queue databases passed `quick_check` at that time.

The reported rollout file:

```text
/Users/marc/.codex/sessions/2026/09/09/rollout-2026-09-09T16-02-03-01a08642-dca8-7030-90f5-f17873ee2fd6.jsonl
```

had 454 valid JSONL lines, 3,126,797 bytes, and recorded CLI version 0.153.4.
Valid JSONL and successful SQLite checks do not establish that every turn survived.

Fresh histories worked with standalone and Desktop-bundled Codex 0.153.4, for
both legacy and paginated history, including completed turns and backend restart.
Deliberately damaging a fresh synthetic thread-history database reproduced the
long malformed-database error. We did **not** reproduce or explain
`list_turns is not supported yet`; do not report that its cause is proven.
The Desktop backend was tested directly, not through the Desktop UI.

### Folder filtering does not mean separate databases

Codex stores state for multiple working directories in databases under one
Codex home (unless database location is overridden). Thread metadata includes
`cwd`; folder-specific history selection is filtering, not storage isolation.
There are multiple databases, not one SQLite file per folder or one SQLite file
containing absolutely all state. JSONL rollout files also matter.

Observed schema: `state_5.sqlite` had 52 SQLx migrations; thread history had six.
Migration checksums are available, but SQLite `user_version` was zero in both.
The two tested Mac backends had matching schemas despite different binary hashes.
This is not a cross-version compatibility guarantee. Checking a filename or
`user_version` alone is insufficient for a safe importer.

### Native duplicate resume has a session writer guard

Actual native Codex 0.153.4 TUIs resuming the same synthetic session were tested
with both legacy and paginated histories. The second CLI exited with:

```text
thread/resume failed: thread <id> already has an active writer (code -32600)
```

It was also rejected while the first CLI was idle after completing its turn.
Resume succeeded after the first CLI exited. The saved history contained only
the successful continuation; there was no automatic fork or overwrite, and
all test databases passed integrity checks. See
[concurrent-resume results](results/concurrent-resume.json).

This tests independent native CLI instances, not multiple clients attached to
one shared backend. That particular thread-writer guard has not been tested
across containers. Even a working per-thread guard would not solve concurrent
access to shared databases by different threads.

## Preferred design to implement next

### Durable state and folder identity

Suggested layout; exact naming is an implementation choice:

```text
~/.claude-contained/codex/
  projects/
    <readable-folder-name>-<hash-of-canonical-absolute-path>/
      identity.json            # original/canonical path and format version
      home/                    # this folder's entire persistent CODEX_HOME
  locks/
    <same-path-key>/            # host-owned reservation/ownership information
```

Use the resolved primary launch directory as the initial definition of
"folder", consistent with the wrapper's effective initial working directory.
Resolve relative paths, `..`, and symlink aliases before deriving the key;
account for equivalent path spellings on macOS case-insensitive filesystems.
Store and validate the full canonical path alongside the key. A basename alone
is not unique. Different worktrees should get distinct state; do not silently
collapse everything to the common Git repository root.

Additional mounted directories do not automatically select or share another
history store. Define handling of forwarded Codex `--cd`/`-C` arguments so the
documented folder identity matches the actual launch behavior. Moving a folder
will ordinarily select a new key; deliberate relocation of its state can be
added later instead of guessing or merging automatically.

Mount only the selected persistent Codex home into the container at Codex's
effective home location. Retaining the familiar in-container `~/.codex` path
may simplify existing skill and launcher behavior; an explicit `CODEX_HOME`
is another option. The key requirement is that **all mutable Codex runtime
state resolves inside this folder's private store**. Audit configuration,
environment overrides such as `CODEX_SQLITE_HOME`/`sqlite_home`, absolute paths,
and symlinks so they cannot redirect databases back to shared native state.

Do not mount the native `~/.codex` wholesale, or one shared contained Codex home
for every folder. Separate filenames beneath one directory are useful only if
every relevant mutable database and sidecar is actually isolated.

### Exclusion must be enforced on the Mac for the full lifetime

Acquire a host-side reservation before opening, initializing, copying into, or
mounting the selected store. A second launch for that same key must identify
the active folder/container and offer to start the requested agent inside that container, subject to the
validation below. Declining or failing that offer must cancel the launch, not
create a competing container. Do not silently create another private history or
allow another container to open the store just because session IDs differ.

Hold ownership until all processes in the owning container that could access
the store have stopped. A startup-only mutex is insufficient. All cooperating
launch paths must acquire the same host-side protection; it cannot depend on
file locks propagating across the container boundary.

Handle normal exit, launch failure, terminal disconnect, signals, and orphaned
containers. A dead wrapper PID alone is not evidence that the container stopped.
If a wrapper dies while its container survives, a later launch must detect the
existing owner and refuse to mount its store in another container. Stale recovery
must check container identity
and liveness; an ambiguous runtime error must not be treated as proof of death.
Release/reclaim must verify ownership to avoid deleting a successor's lock.

The policy should cover attachment/reuse paths and starting Codex from a shell
inside a container originally launched for another tool. Both launchers currently
mount Codex state for all tools, so checking only `-t codex` at one call site can
leave a bypass. Decide and document how those entry points enforce the policy
without imposing new history isolation on Claude or other tools.

`claude-docked` should use compatible state/ownership rules if it accesses the
same new stores; otherwise mixed launchers can bypass exclusion. This is a
coordination requirement, not evidence that Docker has the same filesystem bug.

Native CLI and Desktop keep using native `~/.codex`. They do not participate in
the contained-store lock and must not be configured to open the new contained
stores while those are in use. Different contained folder keys can run in
parallel because they have separate databases. This protects history storage;
it does not serialize edits to project files shared through overlapping mounts.

### Same-folder container reuse for all agents

User-requested plan amendment, 2026-09-12: when another agent is launched for a
folder that already has a running managed container, **inform the user and ask
before attaching**. This applies to Claude, Codex, Copilot, Gemini, and Vibe,
including switching tools from the one that originally started the container.
Use the same policy in `claude-contained` and `claude-docked`.

- Match the canonical primary folder identity, not a sanitized basename,
  container-name prefix, or the presence of the folder as an extra mount.
  Record the owning runtime and container identity alongside the reservation.
  Resolve simultaneous launches under host-side exclusion before either can
  initialize or mount state. If the owner is still starting, wait for readiness
  or report that state; do not launch a second container.
- Show the folder, runtime, and container name, and explain that attachment
  starts a new agent process in the existing container, sharing its filesystem
  and lifecycle. Ask, for example, `Start codex in this container? [y/N]`.
  Require an affirmative answer; decline, EOF, or unavailable interactive input
  cancels. Tool permission flags such as `--yolo` do not imply attach consent.
  If several legacy containers match, require explicit selection and validate
  ownership; do not guess or assume their shared Codex state is safe.
- Preserve the selected tool, forwarded tool arguments, permission flags, and
  working directory. Check requested extra mounts and their access modes,
  shared skills, ports, SSH forwarding, and other container setup options against
  the existing container. Explain incompatible requests and stop; do not silently
  ignore them or fall back to another container using the same store. Explicit
  `--attach` paths and debug shells must obey the same state-ownership checks.
- Revalidate the owner and readiness after confirmation. If it exits during
  the prompt or exec fails, report the failure; any retry must go through normal
  ownership acquisition. Mixed-runtime launches must identify the existing
  owner and either attach through its runtime or clearly refuse reuse.
- The container owns the Codex reservation. An attached process exiting must
  not release it or clean up the owning container's worktree locks. Existing
  containers are ephemeral: explain that attached agents can be terminated when
  the original container session exits. Keeping a container alive until the last
  attached agent exits would require a separate lifecycle implementation.

The existing `--attach` exec paths make the interaction feasible, but **reuse is
not itself a verified corruption fix**. The saved probes tested separate
containers and native processes, not concurrent Codex processes within one
container against its persistent bind mount. Before enabling concurrent Codex
attachment, validate Linux/SQLite writer exclusion, acknowledged-turn retention,
independent conversations, and duplicate-session resume in that exact topology.
Until those checks pass, refuse a second Codex process with a clear explanation.
Per-folder isolated state and exclusion between containers remain required;
reuse alone does not eliminate the current native/contained sharing problem.

### Configuration, credentials, skills, and initial history

Preserve useful setup deliberately: inventory authentication, configuration,
skills, plugins, MCP settings, and helper paths before choosing copy/read-only
sharing rules. Do not assume everything under `~/.codex` is history, or that all
non-history files are immutable. Credential refresh and plugin updates can
write state too. Avoid introducing another uncontrolled shared mutable store.
Do not print secrets during diagnostics.

**Start new isolated histories by default for the first implementation.** This
is a proposed conservative default, not a requirement to discard old history.
Keep existing native state and an offline backup intact. Importing usable past
chats is a separate recovery/migration task; the word "fork" must not imply a
startup copy of the currently damaged native databases. There is no shutdown
merge, and no per-launch snapshot needed for normal operation of this design.

The initial design removes the demonstrated concurrent-access mechanism. It
has not yet established that a single container's repeated use of a persistent
bind-mounted store is reliable across crashes and restarts. Validate that before
calling the design safe or complete.

## Other options considered

| Option | Assessment |
| --- | --- |
| Per-folder persistent contained state plus lifetime exclusion | Preferred next step. Allows different folders concurrently, preserves history across ephemeral containers, and accepts separation from native history. Requires the implementation and validation above. |
| One contained-only shared home plus one global lifetime lock | Simpler fallback, but serializes all contained Codex work, including different folders. A separate home alone does not fix multi-container writes. |
| Snapshot at launch, merge at exit | Possible engineering project, not a simple file copy. Needs consistent multi-file snapshots, baseline/current/incoming conflict handling, version checks, crash-retained pending imports, and a single importer. Same-session divergence and live backend caches remain issues. |
| Mac app-server owns history; disposable containers run exec-server | Promising prototype for unified history. Shell execution, concurrent threads, replacement containers, and resume worked. Missing environment assignment allowed a command to execute on the Mac; remote MCP configuration was rejected. CLI/Desktop integration and full feature parity remain unverified. More work than the preferred design. |
| CLI remote connection / Desktop remote connection by itself | A connection mechanism, not sufficient proof of correct execution placement or database ownership. It needs an explicitly designed backend and lifecycle. |
| Several Codex processes in one persistent Linux environment, using Linux-local storage | Could restore ordinary kernel/SQLite coordination. A shared Linux VM or Docker named volume is an alternative to investigate, but changes isolation/runtime assumptions and was not tested. |
| Continue native/container sharing with a wrapper-only global lock | Cannot coordinate native Desktop or unwrapped CLI automatically. A lock held only by cooperating containers does not exclude native writers. |
| Wait for upstream filesystem/runtime fixes | Could preserve the old architecture, but no fix has been verified. A standalone synthetic reproducer is available; no issue has been published. |

For the Mac app-server prototype, the correct topology is **app-server on Mac,
exec-server in each container**, not app-server inside each container sharing
the Mac databases. A broker would need to verify and set the container
assignment on every contained turn and fail if unavailable. Native Desktop
execution can intentionally be local; that does not establish containment for
turns intended to run inside a container. See the detailed report's acceptance
table and [execution results](results/execution.json).

## Implementation acceptance checks

Use disposable state and fake-model fixtures first. Do not use the damaged real
history as a test database.

1. Same canonical folder through relative, absolute, and symlink paths selects
   the same persistent store. Different folders, including equal basenames and
   separate worktrees, select different stores. Test paths with spaces.
2. Two simultaneous same-folder launches: exactly one acquires ownership;
   the other identifies the owner and asks to attach before opening the store.
   Acceptance starts the requested tool in the owning container only; decline,
   EOF, incompatible options, and failed attachment create no competing container.
   Repeat with the owner idle, starting, or exiting during the prompt, relevant
   explicit attach paths, and mixed launchers if supported. Cover every agent,
   including a different tool from the original, and verify argument preservation.
   Validate concurrent Codex processes within one container as described above.
3. Different folders run concurrently and retain every acknowledged synthetic
   turn across repeated runs. Compare expected turns/content, not only SQLite
   integrity checks.
4. Container exit/removal followed by a fresh container resumes the same folder's
   history. Abrupt container termination and wrapper death leave recoverable
   history and correct ownership. A still-running orphan prevents a replacement container from using its store;
   validated attachment to that owner remains possible.
5. Native fake-history activity can overlap contained activity without either
   accessing the other's databases. Inspect effective mounts and state paths,
   including database overrides and symlinks.
6. Verify authentication/setup, skills and built-in skills, MCP, plugin behavior,
   normal resume selection, and permission settings in the isolated home.
   Verify that other tools keep their existing state layout and gain the
   same-folder reuse prompt without silently losing requested launch options.
7. Validate with supported Codex versions; record any migration constraints.
   Report any unsupported entry point clearly rather than allowing it to fall
   back silently to the native writable Codex home.

## Existing tools and recovery status

| File | Purpose |
| --- | --- |
| [README.md](README.md) | Detailed original investigation and prototype limitations |
| [RECOVERY.md](RECOVERY.md) | Offline backup instructions; no destructive repair prescribed |
| [backup_state.py](backup_state.py) | Verified copy after all writers stop; tested only on synthetic state |
| [locking_probe.py](locking_probe.py) | Standalone host/container lock and lost-write reproducer |
| [history_probe.py](history_probe.py), [mock_model.py](mock_model.py) | Fresh history API tests with optional local fake model and synthetic corruption |
| [concurrent_resume_probe.py](concurrent_resume_probe.py) | Actual native CLI duplicate-resume test with fake model |
| [execution_probe.py](execution_probe.py), [mcp_fixture.py](mcp_fixture.py) | Experimental host-backend/container-execution test, including intentional fault cases |
| [results/](results/) | Saved evidence independent of chat history |

The probes used no inherited credentials or external model calls. Test containers
were stopped. No real-state backup or recovery has been performed. Before any
repair or migration, follow [RECOVERY.md](RECOVERY.md) from an ordinary Terminal
after closing all users of that state. Preserve the original and verified backup;
experiment on a separate working copy. Do not delete databases on the assumption
that JSONL can reconstruct everything.

The next agent should read this file and the existing launcher lifecycle, then
implement the preferred design when asked. Do not restart the investigation by
assuming the only options are global history sharing or disposable per-session
history: the chosen middle ground is **durable per-folder contained history**.

## Scope assessment after inspecting launcher code

This is a moderate launcher/lifecycle change. Ordinary per-folder launches can
be supported without changing Codex itself or introducing a daemon, remote
backend, or database importer. Making the supported launch paths consistent and
handling crashes is the substantial part. A rough planning estimate is several
hundred lines of implementation plus focused regression/integration tests;
duplicating the common logic in both existing standalone launchers increases the
diff. This is an estimate, not a completed implementation or measured effort.

Concrete code observations:

- Path resolution already exists (`resolve_path`, near line 113), and the
  primary directory is resolved near line 925. Store selection can reuse that
  structure, with additional identity validation.
- Mount assembly is centralized around `HOST_CODEX_DIR` and `container_argv`.
  Replacing the normal Codex home mount is a small part of the work.
- **Every container also mounts all of `~/.claude-contained`**, near line 1107,
  for `.claude.json` and `.gitconfig`. Putting all new stores and host locks
  underneath that directory would expose them to every container through this
  second path. Narrow or mask that exposure while preserving existing Claude
  behavior; replacing only the `~/.codex` mount is insufficient.
- Attach paths near lines 402 and 423 directly execute a new tool inside an
  existing container, before ordinary path selection and mount setup. They need
  explicit validation; a check added only around `container run` misses them.
- Exit and signal traps already exist near line 997. Add Codex ownership cleanup
  without replacing worktree cleanup. Account for a surviving container after
  the host wrapper dies.
- The existing worktree mutex deliberately treats an aged owner as stale even
  when its PID is live. Do not copy that policy into a full-session Codex lock.
  Long-running sessions are legitimate; elapsed age must not authorize a second
  writer. PID-only checks are also insufficient when the container can outlive
  the launcher.
- Shared-skills setup currently uses `HOST_CODEX_DIR` as both a host source and
  a container destination. Separate those concepts when introducing the new
  source, so built-in and explicitly shared skills keep resolving correctly.
- `entrypoint.sh` does not currently contain Codex-specific launch protection.
  A normal-launch-only solution can largely live in the host scripts. Supporting
  Codex started manually in a debug shell or in another tool's container may
  require an in-container command guard and an image/entrypoint change. A clear
  refusal for unsupported entry points can keep the initial scope smaller.
- Existing shell tests source launcher helpers using
  `CLAUDE_CONTAINED_LIB_ONLY=1` and cover both launchers. They provide a useful
  pattern for path, reservation, and generated-mount tests. Real runtime tests
  are still needed for orphan handling and retained history across restarts.
- The README permits copying a single launcher onto PATH. Introducing a shared
  external helper has installation implications; do not silently make that
  supported installation require an adjacent repository checkout.

Likely touched files: `claude-contained`, `claude-docked`, launcher help/README,
and new tests; potentially `entrypoint.sh`, `Dockerfile`, or a small command
guard if shell/manual Codex entry points are supported. Existing-history repair,
import, continuous config synchronization, and folder-relocation tooling are
separate work and excluded from this estimate.
