# Codex history investigation — 2026-09-10

**Start with [HANDOVER.md](HANDOVER.md) for the latest decision and next steps.**
The preferred direction is now persistent per-folder contained Codex state plus
host-side lifetime exclusion for each folder, intentionally separate from native
CLI/Desktop history. It is not implemented yet. The report below preserves the
evidence and earlier prototype exploration.

The current shared-directory design reproducibly loses SQLite writes across Apple
Container boundaries. A synthetic test reproduced the failure without Codex,
credentials, or real chat history. A host-side Codex backend with disposable
execution containers works for shell commands, file writes, concurrent threads,
and resume, but the prototype does not yet preserve the complete containment and
desktop workflow.

No launcher, alias, image definition, user configuration, or real history was
changed. The probes used fresh directories under `/private/tmp`. Test containers
were stopped afterward. No real model calls were made: completed-turn tests used a
local deterministic Responses API fixture. This investigation did not launch the
desktop UI or change its connections.

## Confirmed filesystem failure

Environment: Apple Container CLI 1.4.1; native Python SQLite 3.53.4;
`python:3.11-slim` SQLite 3.46.1. The probe mounts one synthetic directory using
the same bind-mount mechanism as the launcher.

| Holder → contender | POSIX lock | flock | SQLite WAL writer transaction |
| --- | --- | --- | --- |
| Mac → Mac | Blocked correctly | Blocked correctly | Blocked correctly |
| Mac → container | Both acquired | Both acquired | Both acquired |
| Container → Mac | Both acquired | Both acquired | Both acquired |
| Container → container | Both acquired | Both acquired | Both acquired |

The holder waits for an explicit release after the contender has reported its
result, so this is not a timing guess about whether transactions overlapped.

Each write test starts two workers behind a barrier. Each attempts 150 uniquely
identified inserts, each committed separately with `synchronous=FULL`.

| Writers | Successful commits reported | Rows retained | Missing acknowledged writes | Final integrity check |
| --- | ---: | ---: | ---: | --- |
| Mac + Mac | 300 | 300 | 0 | ok |
| Mac + container | 300 | 150 | 150 | ok |
| Container + container | 300 | 150 | 150 | ok |

All workers reported zero SQLite errors. This reproduces silent data loss and
shows why `integrity_check = ok` cannot prove that history is complete. It does
not establish which past operation corrupted the user's actual databases, or
identify the particular Apple filesystem/runtime implementation defect.

Multiple contained Codex sessions alone can encounter this mechanism. The
desktop app is not required. The existing launcher mutex covers Git worktree
bookkeeping; it does not coordinate SQLite transactions.

Evidence: [locking results](results/locking.json). Full synthetic databases:
`/private/tmp/codex-boundary-probe-8tq7uhvp/`.

Reproduce:

```sh
python3 investigations/codex-history/locking_probe.py
```

## Codex history behavior

Tested standalone Codex CLI 0.153.4 and the desktop application's bundled
0.153.4 backend independently with fresh `HOME` and `CODEX_HOME`, a minimal custom
provider, no inherited credentials, and a fake model bound to loopback.

For each backend, both legacy and paginated threads completed a turn, survived
backend shutdown, and passed `thread/read`, `thread/turns/list`, and
`thread/resume`. The six generated databases passed integrity checks. Injected
synthetic messages were also included, but the completed-turn check is what
establishes that nonempty turn history survived.

The two macOS binaries have different SHA-256 values despite the same version:

- Standalone: `b973d440acac501fd2594a43e7ca9ce41e0a65b9dfb28d0d7a7837c99e1261e3`
- Desktop-bundled: `c147aa90d34139599711fb568102ceefc6319ca1ac5cb6f4056ca46a1834edd9`

In a separate synthetic run, overwriting one byte of the `thread_turns` root page
after shutdown reproduced the reported longer error on paginated reads/resume:

```text
failed to list thread history: thread-store internal error: failed to access thread history: error returned from database: (code: 11) database disk image is malformed
```

Legacy threads in that same synthetic state remained readable. This is a reason
to preserve databases during recovery rather than assume every transcript is
fully reconstructible from JSONL. The shorter `list_turns is not supported yet`
error was not reproduced and remains unexplained.

The public [app-server documentation](https://learn.chatgpt.com/docs/app-server)
retrieved during this investigation describes limitations on paginated history
that do not match these installed binaries. Treat runtime results as evidence
for this installation, not that document as proof of the shorter error's cause.

Evidence: [standalone results](results/healthy-history.json),
[integrity checks](results/healthy-history-integrity.json),
[desktop backend summary](results/desktop-history-summary.json), and
[deliberate corruption summary](results/damaged-history-summary.json).

```sh
python3 investigations/codex-history/history_probe.py --completed-turns
python3 investigations/codex-history/history_probe.py --completed-turns --binary /Applications/ChatGPT.app/Contents/Resources/codex
python3 investigations/codex-history/history_probe.py --completed-turns --damage-thread-history
```

The damage option acts only on a new synthetic database generated by that run.
It has no option to target existing user state.

## Prototype: history on Mac, execution in disposable containers

The installed binary's experimental schema exposes the needed primitives:

```text
environment/add { environmentId, execServerUrl }
thread/start { environments: [{ environmentId, cwd }], ... }
turn/start { environments: [{ environmentId, cwd }], ... }
```

The fixture starts `codex exec-server` in each container, publishes its port only
on host loopback, and connects a Mac app-server to it. It mounts only a synthetic
project. Neither the real nor the synthetic Mac `CODEX_HOME` is mounted into
execution containers. Each container has an independent scratch Codex home for
the execution service, not a separate conversation history.

All tested execution-server binaries reported 0.153.4.

| Acceptance check | Result |
| --- | --- |
| Shell command runs on Linux | Pass: platform and container-private marker checked |
| File writes occur through container execution | Pass: expected project markers written |
| Two threads execute in different containers | Pass |
| Two turns overlap in time | Pass: command event intervals overlap |
| Destroy original container, resume chat in fresh container | Pass with explicit environment assignment |
| Restart Mac backend, reattach environment, resume history | Pass with explicit assignment and permission settings |
| Synthetic skill discovered by backend | Pass; skill execution not tested |
| Missing environment refuses execution | **Fail**: after backend restart and resume without environment override, harmless probe command ran on Darwin |
| Remote stdio MCP placement | **Not supported by tested config**: strict mode rejects `experimental_environment` |
| Actual CLI terminal attachment and resume picker | Not tested |
| Actual desktop opening/resuming a contained thread | Not tested |
| Dedicated patch tool, interactive shell sessions, full skill/plugin parity | Not tested |

The native-execution result was intentional fault injection with a harmless
command, not an accidental real workload. It proves that environment selection
cannot be assumed to survive a backend restart. The command failed before
writing a project marker because the container-private marker was absent.

The remote MCP setting is documented in the current
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
but this binary reports:

```text
unknown configuration field `mcp_servers.probe_remote.experimental_environment`
```

Without strict mode, it ignores the setting and the MCP fixture fails to find
the container-private marker. That result is not successful remote-MCP routing.

An earlier execution attempt also showed that after backend restart, resume
without explicit permission settings used workspace sandboxing; the container
then lacked `codex-linux-sandbox` on its PATH in this deliberately minimal
entrypoint. Passing the intended policy made that probe work. A real launcher
must handle policies and helper paths rather than depend on defaults.

Evidence: [execution summary](results/execution.json),
[strict config rejection](results/strict-config.txt). Raw synthetic transcripts
and fixture requests remain under the temporary path recorded in the summary.

```sh
python3 investigations/codex-history/execution_probe.py
python3 investigations/codex-history/execution_probe.py --strict-config
```

The second command intentionally demonstrates the configuration rejection on
the tested version. The first is a diagnostic, not a safe production launcher:
it deliberately includes the missing-environment fault case.

## Earlier shared-history fix exploration

This section describes the unified-history alternative. The preferred next
implementation is the per-folder design in [HANDOVER.md](HANDOVER.md).

A full-session host mutex could serialize cooperating contained launchers, but
it would remove multi-container concurrency and would not make desktop obey the
mutex. A startup-only lock cannot protect later writes.

The host-backend prototype is a plausible basis for preserving one history
while keeping execution containers disposable. Before changing either launcher:

1. Build an opt-in broker that creates/attaches the execution container and sets
   the environment for every contained turn, including resume. It must reject
   requests if that assignment is missing or the container is unavailable.
2. Prove the actual CLI interface works through that broker. An ordinary
   `codex --remote` connection alone does not enforce container execution.
3. Decide and test the desktop behavior explicitly. A separate desktop backend
   can read Mac-owned databases using native locks, but cannot be assumed to
   honor the broker's execution policy. Viewing history and executing a resumed
   turn are separate acceptance checks.
4. Route container-scoped MCP services through a supported mechanism, and
   validate skill scripts, patches, interactive commands, mounts, and helpers.
5. Keep both launchers in sync only after the acceptance checks pass. Docker's
   locking behavior has not been tested by this investigation.

If upstream fixes cross-boundary locking and coherence, that may preserve the
existing architecture with fewer changes. The standalone synthetic reproducer
is suitable evidence for an upstream report, but nothing has been published.

## Two native CLIs resuming the same session

Tested two actual native Codex 0.153.4 TUIs with a disposable shared CODEX_HOME,
the same working directory and session ID, and a loopback fake model. Repeated
for both legacy and paginated histories. The first CLI resumed and continued
normally. The second exited with code 1 and:

```text
thread/resume failed: thread <id> already has an active writer (code -32600)
```

A third attempt after the first turn completed, while the first CLI remained
open and idle, was also rejected. After closing the CLI, app-server resume
succeeded. Saved history contained the seed turn and the first CLI's completed
turn; neither rejected client's prompt appeared. No fork was created, and all
synthetic databases passed integrity_check. No shared daemon was running in
these disposable homes; this tests ordinary native CLI instances, not two
clients deliberately connected to one shared backend.

Evidence: [concurrent resume results](results/concurrent-resume.json).

```sh
python3 investigations/codex-history/concurrent_resume_probe.py
python3 investigations/codex-history/concurrent_resume_probe.py --history-mode legacy
```

## Real-history recovery

Recovery has not been attempted. See [the offline backup procedure](RECOVERY.md).
The backup utility was tested only on synthetic files, a symlink, a FIFO, and
refusal to overwrite or nest the destination inside the source.
