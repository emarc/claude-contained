# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code Contained is a bash-based containerization wrapper that runs AI coding assistants (Claude, Codex, Copilot, Gemini, Vibe) inside an Apple Containers sandbox with persistent state. It enables isolated, repeatable sessions on macOS with support for multi-project workflows and host service access.

## Build and Run Commands

```bash
# Build the container image
container build -t claude-contained .

# Run Claude (default)
claude-contained

# Run other tools
claude-contained -t codex .
claude-contained -t copilot .
claude-contained -t gemini .
claude-contained -t vibe .

# Run with multiple directories (first is working dir, others auto-added for claude/codex)
claude-contained . ../other/project

# Pass arguments to tool (use -- separator)
claude-contained . -- --model sonnet

# Yolo mode (maps to tool-specific flag)
claude-contained -y -t codex .

# Use container-specific node_modules (skip prompt)
claude-contained -N .
```

## Architecture

### Key Files

- **claude-contained** - Main bash entry point for Apple Containers. Handles argument parsing, path resolution (with Python3/realpath/readlink fallbacks), and container execution with full path parity.

- **claude-docked** - Docker equivalent of claude-contained. **Must be kept in sync with claude-contained** to maintain feature parity. Both scripts share the same flag interface and behavior.

- **Dockerfile** - Builds on Node 20 (Debian Bookworm). Installs JetBrains Runtime 25, HotswapAgent, AI CLI tools (Claude Code, OpenAI Codex, GitHub Copilot, Google Gemini CLI, Mistral Vibe), ripgrep, Python 3. Creates entrypoint.sh that configures `host.local` for host service access, matches host UID/GID, and sets up path parity.

- **.mcp.json** - MCP server configuration, notably enabling Figma Desktop MCP via `host.local:3845`.

### Container Design

- **Full path parity**: Directories mounted at their original host paths (e.g., `/Users/me/project` → `/Users/me/project`)
- **HOME parity**: Container HOME matches host HOME for consistent behavior
- **UID/GID matching**: Container user matches host user IDs for proper file permissions
- **State sharing**: Tool configs (`~/.claude`, `~/.codex`, `~/.copilot`, `~/.gemini`, `~/.vibe`), Maven cache (`~/.m2`), and Vaadin state (`~/.vaadin`) bind-mounted from host
- **Shared skills**: `--share-skills=DIR` is opt-in and has no default. It requires a full path and mounts `DIR` as each tool's skills directory. Codex gets a nested mount for host `~/.codex/skills/.system` so built-ins remain visible while new installs write to `DIR`.
- **SSH agent forwarding**: Disabled by default for security; enable with `-S/--ssh` flag (required for `git push` to SSH remotes)
- Host services accessible via `host.local` hostname (resolved from container gateway IP)

### Notable Patterns

- Path resolution prioritizes Python3 for reliability, with multiple fallbacks
- Entrypoint dynamically adjusts UID/GID to match host user (handles conflicts)
- Strict bash error handling with `set -euo pipefail`
- `--` separator distinguishes directory arguments from tool arguments
- `-t/--tool` flag selects which AI tool to run; `-y/--yolo` maps to tool-specific permission flags; `-N/--contained-node-modules` auto-accepts the node_modules overlay prompt
- Only Claude and Codex support `--add-dir` for extra directories; others just get mounts
- **Container naming**: Both scripts use the `aic-` prefix for container names. Auto-generated names follow the pattern `aic-{folder}-{HHMM}` (e.g., `aic-my-app-1423`). If a container with that name already exists, a numeric suffix is appended (`aic-my-app-1423-2`, `-3`, etc.). Custom names via `-a` also use the `aic-` prefix.
- **Script parity**: `claude-contained` and `claude-docked` should always be updated together when adding/changing flags or behavior to maintain feature parity across both container runtimes
- **Worktree pruning protection**: When mounted Git metadata can see linked worktrees that are not mounted into the container, both scripts offer to auto-lock unlocked or already auto-locked linked worktrees while the container runs. Auto-lock reasons use `cc-autolocked-by:` owner tokens; non-matching user locks are never changed. Owner-list edits are serialized by a `mkdir`-based mutex at `.git/claude-contained-worktree-locks.lock` (portable to macOS, which lacks `flock`). The mutex records the holder PID + timestamp in an `owner` file and is **self-healing**: a directory left behind by a launcher that died mid-hold (crash, SIGKILL, kill during cleanup) is reclaimed by the next run via `kill -0` liveness plus an age fallback — otherwise one stale directory would permanently make every later run time out and silently skip locking. Acquisition is **fail-safe**: if the mutex genuinely can't be taken, the launch still applies the locks (never runs the container with worktrees unprotected); cleanup, by contrast, leaves locks in place if it can't take the mutex, since erring toward over-locking can never destroy data. `INT`/`TERM`/`HUP` are trapped (alongside `EXIT`) so cleanup runs on common kills; bash defers these traps until the foreground container run returns, so locks are never released while the container could still prune. Regression tests live in `tests/` (sourced with `CLAUDE_CONTAINED_LIB_ONLY=1`).

### Devcontainer Support

The `devcontainer/` directory provides a VS Code devcontainer configuration for Java/Spring development.

**Key design decisions:**

- **Template directory, not in-repo `.devcontainer/`**: Users copy to their own projects; avoids confusion with developing claude-contained itself
- **`workspaceMount: ""`**: Disables VS Code's default `/workspaces` mount to enable path parity
- **`overrideCommand: true`**: Bypasses entrypoint.sh since VS Code manages container lifecycle
- **Pre-built image reference**: Simpler than embedding Dockerfile; users build once, reuse everywhere

**Differences from standalone scripts:**

- VS Code manages the container lifecycle, not entrypoint.sh
- UID/GID handled by VS Code's `remoteUser` feature (may differ from host)
- Networking managed by VS Code; `host.local` trick may not work

## Known Caveats

- Port forwarding not available for local MCPs (use `host.local` workaround)
- Multiple simultaneous sessions share `~/.claude` state; concurrent writes may conflict (Claude Code limitation)
- `~/.claude.json` is relocated to `~/.claude-contained/.claude.json` (with symlink at original location) to work around Apple Containers' inability to bind-mount individual files. Deleting `~/.claude-contained/` will lose credentials.
- Running `claude-contained` and regular `claude` simultaneously is not recommended (both access same config via different paths)
- **node_modules overlay**: On macOS hosts, Node.js projects are prompted to create a `.claude-contained/node_modules-linux-<arch>/` directory that gets mounted over `node_modules` inside the container (since macOS native binaries don't work on Linux). You should manually add `.claude-contained/` to `.gitignore` if needed. Use `-N` to skip the prompt.
- **Devcontainer limitation**: Don't run VS Code devcontainer and standalone scripts simultaneously on same `~/.claude` directory
- **Claude Code clipboard / copy workaround**: The Dockerfile writes `/etc/claude-code/managed-settings.json` with `{ "tui": "default" }` to force Claude Code's classic inline renderer inside the container. The newer fullscreen ("no-flicker") renderer (default since ~2.1.168) routes copy-on-select only through OSC 52 and captures the mouse; in a containerized terminal there is no clipboard tool, OSC 52 is dropped (e.g. Terminal.app), and mouse capture breaks native shift/option-drag selection, so copying from Claude stops working (anthropics/claude-code#66192). Managed settings are container-scoped (highest precedence, Linux path) and never touch the host-mounted `~/.claude/settings.json`. Remove this RUN once the upstream renderer regression is fixed.
