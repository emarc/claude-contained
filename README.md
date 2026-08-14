# AI Contained

Seamlessly run CLI coding agents (Claude, Codex, Gemini, Vibe) inside an [Apple Container](https://github.com/apple/container) or [Docker](https://www.docker.com) container with persistent state. 

The main goal is to provide a seamless experience; `alias claude='claude-contained --yolo'` and now `claude` runs in a container with your settings. Only `.` or the folders you specify are shared with the container.
History is retained (even if you switch back to un-contained `claude`). 

There are some caveats:

- **Host localhost access**: `-H PORT` works with `claude-docked` (Docker) but not `claude-contained` (Apple Containers) for services bound to localhost. See [Accessing Host Services](#accessing-host-services). (Apple Containers seems to be gaining support soon.)
- **`~/.claude.json` is relocated**: On first run, your `~/.claude.json` is moved to `~/.claude-contained/.claude.json` and replaced with a symlink. This allows containers to share the file. **If you delete `~/.claude-contained/`, you will lose your Claude credentials and some settings.** You'll have to log in again. This is a limitation on how files can be shared with the container. 
- **Don't mix contained and uncontained at the same time**: Running `claude-contained` and regular `claude` simultaneously may cause issues, as both access the same config file but through different paths. Run one or the other, not both at once. This will be fixed in the future.
- **Codex and PATH**: Codex runs commands via `bash -lc`, which sources `/etc/profile` and resets PATH to the Debian default. This means tools installed outside standard locations (e.g., via SDKMAN) won't be found unless symlinked into `/usr/local/bin/`. The image includes symlinks for `java`, `javac`, `jar`, `mvn`, and `jbang`. If you install additional tools in non-standard paths, add similar symlinks in the Dockerfile.

## Quick Start

### Apple Containers (macOS)

1. Build the container:
   ```bash
   container build --platform linux/arm64 -t claude-contained .
   ```

2. Put `claude-contained` somewhere on your PATH, optionally aliasing to `claude`.
   ```
   alias claude='claude-contained --yolo'
   alias vibe='claude-contained -t vibe --yolo'
   alias codex='claude-contained -t codex --yolo'
   alias gemini='claude-contained -t gemini --yolo'
   ```

4. Run:
   ```bash
   claude-contained              # Current directory
   claude-contained ./my-project # Specific directory
   ```

### Docker

1. Build the container:
   ```bash
   docker build --platform linux/arm64 -t claude-contained .
   ```

2. Put `claude-docked` somewhere on your PATH.

3. Run:
   ```bash
   claude-docked              # Current directory
   claude-docked ./my-project # Specific directory
   ```

## Usage

```
claude-contained [options] [main_dir] [extra_dir ...] [-- <tool args...>]
```

### Options

| Flag | Description |
|------|-------------|
| `-t`, `--tool TOOL` | AI tool to run: `claude` (default), `codex`, `gemini`, `vibe` |
| `-R`, `--rebuild[=MODE]` | Rebuild image before run: `tools` (default) or `full` |
| `-H PORT[:HOSTPORT]` | Forward host port to container localhost (can be repeated) |
| `-p HOST:CONTAINER` | Publish container port to host (can be repeated) |
| `-s`, `--shell` | Start a bash shell instead of the AI tool (for debugging) |
| `-S`, `--ssh` | Enable SSH agent forwarding (for git push) |
| `-w`, `--worktree` | Auto-include git worktree's main repository (skip prompt) |
| `-y`, `--yolo` | Skip all permission prompts (tool-specific flag) |
| `-N`, `--contained-node-modules` | Use container-specific node_modules (skip prompt) |
| `--share-skills=DIR` | Mount shared skill folders from `DIR` (opt-in, no default; use a full path) |
| `-a`, `--attach [NAME]` | Attach to running container (runs tool, or bash with `-s`) |
| `-h`, `--help` | Show help message |

### Supported Tools

| Tool | Command | Yolo Flag | Config Dir |
|------|---------|-----------|------------|
| [Claude Code](https://claude.ai/code) | `claude` | `--dangerously-skip-permissions` | `~/.claude` |
| [OpenAI Codex](https://github.com/openai/codex) | `codex` | `--yolo` | `~/.codex` |
| [Google Gemini CLI](https://github.com/google-gemini/gemini-cli) | `gemini` | `--yolo` | `~/.gemini` |
| [Mistral Vibe](https://github.com/mistralai/mistral-vibe) | `vibe` | `--auto-approve` | `~/.vibe` |

All config directories are bind-mounted regardless of which tool you run.

### Behavior

- First directory is the working directory
- Additional directories are mounted and auto-added via `--add-dir` (Claude and Codex only)
- Append `:ro` to an extra dir to mount it read-only (or `:rw` to force read-write); use `--readonly-extras` to default all extras to read-only
- Tool configs and Maven cache (`~/.m2`) are bind-mounted for persistence
- `--share-skills=DIR` mounts `DIR` as each tool's skills directory: `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`, and `~/.<tool>/skills` for Copilot, Gemini, and Vibe. For Codex, the host's `~/.codex/skills/.system` is mounted back over `DIR/.system` so built-in skills remain visible while new installs write to `DIR`. Use a full path; `~` is not expanded by the launcher.
- SSH agent forwarding is disabled by default; use `-S`/`--ssh` to enable
- The GitHub CLI is included, with the [`gh-stack`](https://github.com/github/gh-stack) extension preinstalled (`gh stack ...`). Extensions are baked into the image under `/opt/gh` and linked into `~/.local/share/gh/extensions` at startup; add more with the `GH_EXTENSIONS` build arg (space-separated, e.g. `container build --build-arg GH_EXTENSIONS="github/gh-stack owner/gh-foo" -t claude-contained .`)
- Git worktrees are detected; main repository is included for full git access
- If a mounted main repository has linked worktrees outside the mounted directories, the launcher offers to auto-lock those worktrees while the container runs (otherwise an in-container `git worktree prune`/`git gc` could remove them). Auto-lock reasons use `cc-autolocked-by:` and are removed when the last owning container exits. The locking is self-healing — a lock left behind by a launcher that was killed is reclaimed automatically by the next run — and fail-safe, applying the locks even if the internal mutex is unavailable so the container never runs with worktrees unprotected.

### Examples

```bash
# Tool selection
claude-contained                                    # Claude (default)
claude-contained -t codex .                         # OpenAI Codex
claude-contained -t gemini .                        # Google Gemini CLI
claude-contained -t vibe .                          # Mistral Vibe

# Common usage
claude-contained . ../other/project                 # Multiple directories
claude-contained . ../lib:ro                        # Mount ../lib read-only
claude-contained --readonly-extras . ../a ../b      # All extras read-only
claude-contained . -- --model sonnet --verbose      # Pass args to tool
claude-contained -y -t codex .                      # Codex with --yolo
claude-contained --rebuild .                        # Refresh AI tools first
claude-contained --rebuild=full .                   # Full fresh rebuild first
claude-contained -s                                 # Debug shell
claude-contained --share-skills=/Users/me/Projects/skills . # Share skills into tool skill dirs

# Port forwarding
claude-contained -p 8080:8080 .                     # Expose port 8080
claude-contained -H 3845 .                          # Forward host:3845 to container
```

## Rebuilding the Image

Use the launcher when you want the image refreshed before starting a new session:

```bash
claude-contained --rebuild .      # Refresh AI CLI layers
claude-contained --rebuild=full . # Full fresh rebuild (--pull --no-cache)
claude-docked --rebuild .
claude-docked --rebuild=full .
```

`tools` rebuilds the AI CLI portion of the image and everything after it, which updates Claude Code, Codex, Gemini, Vibe, and Copilot without invalidating the entire build. If that targeted rebuild fails, the launcher automatically retries with a full rebuild.

`full` forces a clean rebuild of the entire image and pulls the latest base image. Rebuild requires the launcher script to run from this repo checkout, or via a symlink into it, so it can find the local `Dockerfile`.

## Node.js Projects (node_modules Overlay)

When running on macOS, the container is Linux — but `node_modules` often contains platform-specific native binaries (e.g., esbuild, swc, sharp) that are compiled for the host architecture. macOS `arm64` binaries won't work inside a Linux `aarch64` container, even though the CPU architecture is the same, because the OS ABI differs.

To handle this, the scripts automatically detect Node.js projects (directories with a `package.json`) and offer to create a **container-specific `node_modules`** directory:

```
Node.js project detected. Use container-specific node_modules? [Y/n]
```

If accepted, a `.claude-contained/node_modules-linux-aarch64/` directory (or `node_modules-linux-x86_64` on Intel Macs) is created inside your project and mounted over `node_modules` inside the container. You should add `.claude-contained/` to your `.gitignore` manually (this also tells IDEs like IntelliJ and VS Code to skip indexing it). This keeps host and container dependencies separate — each platform gets the correct native binaries.

### First run

After accepting the prompt, run your package manager inside the container to install Linux-native dependencies:

```bash
npm install    # or yarn, pnpm, bun, etc.
```

### Subsequent runs

The overlay directory persists on the host, so dependencies survive across container sessions. No re-install needed unless you change `package.json`.

### Skipping the prompt

Use `-N` (or `--contained-node-modules`) to auto-accept without prompting:

```bash
claude-contained -N .
```

### .gitignore

You should manually add `.claude-contained/` to your project's `.gitignore` to exclude overlay directories from version control.

### When it's skipped

- **Linux hosts**: No overlay needed — host and container share the same OS, so native binaries are already compatible.
- **No `package.json`**: No prompt, no overlay.

## Accessing Host Services

The container runs in an isolated network, so `localhost` refers to the container itself, not your Mac. To connect to services running on your Mac, use `host.local` or the `-H` flag.

### Docker (`claude-docked`) - Recommended for Host Services

Use `-H PORT` to forward host ports to container localhost. This works because Docker Desktop has special routing to reach services bound to `127.0.0.1` on the host.

```bash
claude-docked -H 3845 .           # Forward host:3845 to container localhost:3845
claude-docked -H 3845 -H 8080 .   # Multiple ports
```

### Apple Containers (`claude-contained`) - Limited Host Access

Apple Containers can only reach host services bound to `0.0.0.0` (all interfaces), not `127.0.0.1` (localhost only). Most services (including Figma Desktop) bind to localhost only for security. See [apple/container#346](https://github.com/apple/container/issues/346) for the feature request to add `host.docker.internal` equivalent.

**What works:**
- Services you control that bind to `0.0.0.0`
- Using `host.local` hostname for services on all interfaces

**What doesn't work:**
- `-H` flag for localhost-bound services (like Figma Desktop MCP)

For localhost-bound services, use `claude-docked` instead.

### Configuring Figma Desktop MCP

Figma Desktop MCP binds to `localhost:3845`. Use Docker:

```bash
claude-docked -H 3845 .
```

**Requirements:**
- Figma Desktop must be running on your Mac
- The Figma MCP server must be enabled (Figma Desktop → Settings → enable MCP)
- Port 3845 is the default; adjust if you've changed it

### Other MCPs

For MCPs that expect `localhost`, use `claude-docked -H PORT`.

For services bound to all interfaces (`0.0.0.0`), you can use `host.local` in a `.mcp.json` override:

```json
{
  "mcpServers": {
    "my-mcp": {
      "type": "http",
      "url": "http://host.local:PORT/mcp"
    }
  }
}
```

## VS Code Devcontainer

Use the claude-contained image as a VS Code devcontainer for Java/Spring/Vaadin development with full IDE features and Claude in the terminal.

### What It Provides

- Full Java IntelliSense, debugging, and Spring Boot support via pre-installed extensions
- `claude` command available in the integrated terminal
- **Path parity**: Your project stays at its original path (not `/workspaces/project`)
- Maven cache and git config shared with host

### Setup

1. Build the Docker image first:
   ```bash
   docker build -t claude-contained .
   ```

2. Copy the template to your project:
   ```bash
   cp -r devcontainer/ /path/to/your-project/.devcontainer/
   ```

3. Open the project in VS Code and select "Reopen in Container"

### Included Extensions

- Red Hat Java (IntelliSense)
- Debugger for Java
- Test Runner for Java
- Maven for Java
- Spring Boot Extension Pack
- Lombok Annotations Support
- GitLens

### Limitations

- **Image must be pre-built**: Run `docker build` before using
- **Don't run simultaneously**: Avoid running devcontainer while also using standalone `claude-contained` or `claude-docked` (shared `~/.claude` state)
- **host.local may not work**: VS Code manages networking differently; use explicit port forwarding instead

See `devcontainer/README.md` for detailed usage and customization options.
