# Preserve real state before recovery

Run this procedure from an ordinary macOS Terminal, after saving the investigation
notes. The current Codex conversation itself is a database client, so the agent
has not performed this step while investigating.

1. End Codex CLI sessions and fully quit the ChatGPT/Codex desktop app and any IDE
   Codex extension using this state.
2. Stop the Codex app-server daemon if one is running. Use the direct native
   binary so a shell alias does not start another container:

   ```sh
   /opt/homebrew/bin/codex app-server daemon stop
   ```

   A report that no daemon is running is fine. This command does not necessarily
   stop independently launched app-server processes; close those too.
3. Stop any container processes using the shared Codex state. Do not stop
   unrelated workloads. Leave desktop and CLI closed during the copy.
4. From the repository directory, make a new verified backup:

   ```sh
   python3 investigations/codex-history/backup_state.py \
     --source "$HOME/.codex" \
     --destination "$HOME/codex-state-backup-2026-09-10" \
     --clients-stopped
   ```

   Pick another unused destination if that directory exists. The utility refuses
   to overwrite anything. It copies all regular files, including databases,
   remaining `-wal`/`-shm` files, session JSONL, configuration, and credentials.
   It preserves symlinks without following their external targets. Runtime
   sockets/FIFOs are skipped and recorded. Extended attributes and ACLs are not
   preserved by this utility.

   `--clients-stopped` acknowledges that you stopped the clients; it does not
   detect every possible writer. Before/after inventories and SHA-256 comparison
   detect source changes during the copy, but are not a substitute for stopping
   all writers. Failure leaves an `INCOMPLETE.txt` marker rather than claiming a
   verified backup. The backup's parent directory must already exist.

5. Check that the command reports `Verified backup` and the new directory
   contains `manifest.json` and `state/`. The backup includes credentials; keep
   it private. Its enclosing directory is created with owner-only access.

Do not delete or rename the original databases as a recovery shortcut. Preserve
this verified copy and make a second working copy for recovery experiments.

The next recovery investigation should compare readable thread metadata, JSONL
transcripts, and paginated history on that working copy. Test any SQLite recovery
output and Codex resume against the copy before deciding what can be restored.
No command to replace the live database is supplied yet because that procedure
has not been established. A healthy integrity check alone does not establish
that all chats or turns survived.

For fresh investigation conversations before a repair, a separately created
temporary `CODEX_HOME` can isolate diagnostic state. That is a troubleshooting
measure. The preferred future launcher design is persistent per-folder contained
Codex state, intentionally separate from native history; see
[HANDOVER.md](HANDOVER.md). That design has not been implemented yet and does not
repair the existing damaged history.
