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

- **Dockerfile** - Builds on Node 24 (Debian Bookworm). Installs JetBrains Runtime 25, HotswapAgent, AI CLI tools (Claude Code, OpenAI Codex, GitHub Copilot, Google Gemini CLI, Mistral Vibe), the Rust toolchain (rustup: cargo/rustc/clippy/rustfmt) + `just` + Rust dev tools (`typos`, `taplo`, `cargo-nextest`, `cargo-dylint`/`dylint-link`, installed by `install-rust-tools.sh`), the upstream GitHub CLI (`gh`, Debian's is years behind), ripgrep, Python 3, and the native build dependencies `-sys` crates expect (`libssl-dev`, `cmake`, `clang`/`libclang-dev`, `zlib1g-dev`, `libsqlite3-dev` — the base image ships only the runtime libs, so `openssl-sys`, bindgen, etc. fail without them). Copies in entrypoint.sh which configures `host.local` for host service access, matches host UID/GID, and sets up path parity. Rustup lives in `/opt/rust` (shared, on PATH); entrypoint.sh redirects `CARGO_HOME` to the persisted `~/.cargo` mount so the crate registry survives across runs, and `RUSTUP_HOME` to the persisted `~/.rustup` mount so extra toolchains can be installed at all (the baked one is root-owned). entrypoint.sh also sets `DYLINT_DRIVER_PATH` under that mount, since dylint otherwise rebuilds its per-toolchain driver into the container-local `~/.dylint_drivers` on every start. `/opt/rust/rustup` is world-writable because entrypoint.sh symlinks its toolchains into the persisted `~/.rustup` and `rustup component add` writes inside the toolchain directory. `gh` extensions (default: `github/gh-stack`, configurable via the `GH_EXTENSIONS` build arg) are installed at build time into `/opt/gh` with `XDG_DATA_HOME=/opt/gh`, because gh only loads extensions from `$HOME/.local/share/gh/extensions` — a container-local path that is empty on every start; entrypoint.sh symlinks them into HOME. **Size limit**: Apple Containers rejects Dockerfiles over 16 KiB (apple/container#735), which is why entrypoint.sh and hotswap-agent.properties live as separate COPYed files rather than inline heredocs — keep new file content out of the Dockerfile itself.

- **entrypoint.sh** - Container entrypoint, COPYed into the image at `/usr/local/bin/entrypoint.sh`. Handles `host.local` resolution, optional host port forwarding (`HOST_FORWARD_PORTS`), UID/GID matching, HOME/path parity, `.claude.json` symlinking, git config protection, linking the image's `gh` extensions into `~/.local/share/gh/extensions`, Xvfb startup, and dropping to the dev user via gosu.

- **install-rust-tools.sh** - Build-time installer for the Rust dev tools: prebuilt binaries for `typos` (crate-ci/typos), `taplo` (tamasfe/taplo) and `cargo-nextest` (via `get.nexte.st/<version>/<platform>`, where aarch64 is spelled `linux-arm`), and a `cargo install` of `cargo-dylint`/`dylint-link` (no prebuilt releases; also needs the `rustc-dev` and `llvm-tools-preview` rustup components). Separate file for the same 16 KiB Dockerfile reason as entrypoint.sh, and its Dockerfile layer sits above the cheaper installs so the slow dylint build stays cached.

- **hotswap-agent.properties** - HotswapAgent global config, COPYed into the image under `/opt/jbr/lib/hotswap/`.

- **.mcp.json** - MCP server configuration, notably enabling Figma Desktop MCP via `host.local:3845`.

### Container Design

- **Full path parity**: Directories mounted at their original host paths (e.g., `/Users/me/project` → `/Users/me/project`)
- **HOME parity**: Container HOME matches host HOME for consistent behavior
- **UID/GID matching**: Container user matches host user IDs for proper file permissions
- **State sharing**: Tool configs (`~/.claude`, `~/.codex`, `~/.copilot`, `~/.gemini`, `~/.vibe`), Maven cache (`~/.m2`), and Vaadin state (`~/.vaadin`) bind-mounted from host
- **Rust state**: `~/.cargo` and `~/.rustup` are mounted from arch-tagged, container-only dirs (`~/.claude-contained/{cargo,rustup}-linux-<arch>`), never from the host's own — both hold platform-specific binaries, the container's `~/.cargo/bin` is prepended to `PATH`, and `rustup toolchain install` rewrites every proxy in `$CARGO_HOME/bin` (overwriting existing files), which would wreck a macOS Rust install. entrypoint.sh seeds the persisted `RUSTUP_HOME` by symlinking the image's baked toolchains into it (rustup accepts a symlinked toolchain dir as the real thing), prunes links left over from an older image, and re-seeds `settings.toml` if its `default_toolchain` disappeared with them. It also creates `$CARGO_HOME/bin/rustup`, without which rustup exits 1 after a successful `toolchain install` (`error: rustup is not installed at ...`) and breaks callers like dylint
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
