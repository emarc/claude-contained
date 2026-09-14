# Keep shared history when running Codex in disposable containers

When running agents in disposable containers, the containers should be disposable — the conversations should not. It should be easy to run several agents in parallel, replace their containers, and pick up their history from the CLI or desktop app. Container isolation is useful for limiting what an agent can access on the host; keeping history should not make that harder.

Mounting `~/.codex` into the containers seems like the obvious solution, but sharing live SQLite files across that boundary can lose data. Separate persistent homes keep the history, but split it across environments. Running only one agent at a time defeats the point of parallel agents.

This came up after Codex 0.153.4 started failing to resume conversations with “database disk image is malformed”. A small test using fresh SQLite databases reproduced broken locking and lost writes on macOS, with both Docker and Apple Containers. In the Docker test, two containers reported 300 successful commits, but only 165 rows remained, with index corruption. Two native processes retained all 300. This does not prove what damaged the original history, but it shows why sharing the directory is unsafe in these setups. The reproducer uses no Codex data or credentials.

Could Codex support this out of the box? Perhaps a local daemon could own history and state, with containerized instances talking to it. Or [app-server](https://learn.chatgpt.com/docs/app-server) could keep history on the host while execution stays in disposable containers. A prototype of the latter worked for concurrent turns and resume, though container assignment needed care after restart. Whatever the approach, resuming a conversation should preserve its containment.

Related: [#30957](https://github.com/openai/codex/issues/30957) (NFS corruption), [#23168](https://github.com/openai/codex/issues/23168) (storage backends), [#10450](https://github.com/openai/codex/issues/10450) (desktop remote development).
