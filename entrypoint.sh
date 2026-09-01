#!/bin/bash
set -e

# JBR as primary Java (with HotswapAgent support)
export JAVA_HOME=/opt/jbr
export PATH="/opt/claude:/home/dev/.sdkman/candidates/maven/current/bin:/home/dev/.sdkman/candidates/jbang/current/bin:$JAVA_HOME/bin:$PATH"

# Add host.local pointing to host machine
# Docker Desktop (macOS/Windows): use host.docker.internal
# Apple Containers / Docker on Linux: use gateway IP
if getent ahostsv4 host.docker.internal >/dev/null 2>&1; then
  HOST_IP=$(getent ahostsv4 host.docker.internal | head -1 | awk '{print $1}')
else
  HOST_IP=$(ip route | grep default | awk '{print $3}')
fi
if [ -n "$HOST_IP" ]; then
  grep -q "host.local" /etc/hosts 2>/dev/null || echo "$HOST_IP host.local" >> /etc/hosts
fi

# Forward host ports to container localhost (for MCPs that expect localhost)
if [ -n "${HOST_FORWARD_PORTS:-}" ]; then
  IFS=',' read -ra PORTS <<< "$HOST_FORWARD_PORTS"
  for mapping in "${PORTS[@]}"; do
    if [[ "$mapping" == *:* ]]; then
      local_port="${mapping%%:*}"
      host_port="${mapping##*:}"
    else
      local_port="$mapping"
      host_port="$mapping"
    fi
    socat TCP-LISTEN:${local_port},fork,reuseaddr TCP:host.local:${host_port} &
  done
fi

# Path parity setup: match host HOME and UID/GID
if [ -n "${HOST_HOME:-}" ]; then
  mkdir -p "${HOST_HOME}"

  # Match host UID/GID (handle conflicts)
  if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ]; then
    EXISTING_GROUP=$(getent group "${HOST_GID}" | cut -d: -f1)
    if [ -n "$EXISTING_GROUP" ] && [ "$EXISTING_GROUP" != "dev" ]; then
      groupmod -g $((HOST_GID + 10000)) "$EXISTING_GROUP" 2>/dev/null || true
    fi

    EXISTING_USER=$(getent passwd "${HOST_UID}" | cut -d: -f1)
    if [ -n "$EXISTING_USER" ] && [ "$EXISTING_USER" != "dev" ]; then
      usermod -u $((HOST_UID + 10000)) "$EXISTING_USER" 2>/dev/null || true
    fi

    groupmod -g "${HOST_GID}" dev 2>/dev/null || true
    usermod -u "${HOST_UID}" -g "${HOST_GID}" -d "${HOST_HOME}" dev 2>/dev/null || true
  fi

  chown dev:dev "${HOST_HOME}" 2>/dev/null || true
  chown -R dev:dev "${HOST_HOME}/.claude" 2>/dev/null || true
  chown -R dev:dev /ms-playwright 2>/dev/null || true

  export HOME="${HOST_HOME}"

  # Rust: persist the crate registry/cache to the bind-mounted ~/.cargo. The
  # rustup proxy binaries live in /opt/rust/cargo/bin (already on PATH via the
  # image ENV) and RUSTUP_HOME stays /opt/rust/rustup, so redirecting CARGO_HOME
  # only moves the downloaded/registry cache, not the toolchain itself.
  export CARGO_HOME="${HOST_HOME}/.cargo"
  mkdir -p "${CARGO_HOME}/bin"
  chown -R dev:dev "${CARGO_HOME}" 2>/dev/null || true
  export PATH="${CARGO_HOME}/bin:$PATH"

  # Create container-side symlink to ~/.claude.json in shared directory
  # (Apple Containers can't bind-mount individual files, so we use symlinks)
  SHARED_CLAUDE_JSON="${HOST_HOME}/.claude-contained/.claude.json"
  if [ -e "${SHARED_CLAUDE_JSON}" ] && [ ! -e "${HOST_HOME}/.claude.json" ]; then
    ln -s "${SHARED_CLAUDE_JSON}" "${HOST_HOME}/.claude.json"
    chown -h dev:dev "${HOST_HOME}/.claude.json" 2>/dev/null || true
  fi

  # Copy .gitconfig for git commit identity (read-only, no sync back needed)
  SHARED_GITCONFIG="${HOST_HOME}/.claude-contained/.gitconfig"
  if [ -e "${SHARED_GITCONFIG}" ] && [ ! -e "${HOST_HOME}/.gitconfig" ]; then
    cp "${SHARED_GITCONFIG}" "${HOST_HOME}/.gitconfig"
    chown dev:dev "${HOST_HOME}/.gitconfig" 2>/dev/null || true
  fi

  # Create native Claude symlink structure (satisfies installMethod: native in shared config)
  mkdir -p "${HOST_HOME}/.local/bin" 2>/dev/null || true
  if [ ! -e "${HOST_HOME}/.local/bin/claude" ]; then
    ln -sf /opt/claude/claude "${HOST_HOME}/.local/bin/claude"
  fi
  # Expose the image-provided gh CLI extensions (e.g. `gh stack`). gh only looks
  # in $HOME/.local/share/gh/extensions, which is container-local and starts out
  # empty, so symlink the baked-in ones there. Extensions the user installs at
  # runtime land in the same directory and are left alone.
  if [ -d /opt/gh/gh/extensions ]; then
    mkdir -p "${HOST_HOME}/.local/share/gh/extensions" 2>/dev/null || true
    for _ext in /opt/gh/gh/extensions/*; do
      [ -d "${_ext}" ] || continue
      _ext_dst="${HOST_HOME}/.local/share/gh/extensions/$(basename "${_ext}")"
      [ -e "${_ext_dst}" ] || ln -s "${_ext}" "${_ext_dst}"
    done
  fi

  chown -R dev:dev "${HOST_HOME}/.local" 2>/dev/null || true
fi

# Protect .git/config files from modification (prevents AI tools from changing remote URLs)
# Files are made root-owned and read-only so the dev user cannot modify or chmod them
if [ -n "${GIT_PROTECT_DIRS:-}" ]; then
  IFS=':' read -ra _git_dirs <<< "$GIT_PROTECT_DIRS"
  for _dir in "${_git_dirs[@]}"; do
    _git_config="${_dir}/.git/config"
    # Handle worktrees where .git is a file pointing elsewhere
    if [ -f "${_dir}/.git" ] && ! [ -d "${_dir}/.git" ]; then
      _gitdir=$(sed -n 's/^gitdir: //p' "${_dir}/.git")
      # Resolve relative paths
      case "$_gitdir" in
        /*) ;;
        *) _gitdir="${_dir}/${_gitdir}" ;;
      esac
      _git_config="${_gitdir}/config"
    fi
    if [ -f "$_git_config" ]; then
      chown root:root "$_git_config" 2>/dev/null || true
      chmod 444 "$_git_config" 2>/dev/null || true
    fi
  done
fi

# Start virtual framebuffer so Chrome/Chromium can run without a real display
if [ -z "${DISPLAY:-}" ]; then
  export DISPLAY=:99
  Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
fi

# Drop to dev user (or stay root if STAY_ROOT=1)
if [ "$(id -u)" = "0" ] && [ "${STAY_ROOT:-}" != "1" ]; then
  USER_HOME="${HOME:-/home/dev}"
  exec gosu dev env \
    JAVA_HOME="$JAVA_HOME" \
    PATH="${USER_HOME}/.local/bin:$PATH" \
    HOME="$USER_HOME" \
    DISPLAY="$DISPLAY" \
    ${CARGO_HOME:+CARGO_HOME="$CARGO_HOME"} \
    "$@"
else
  # Also update PATH for root/non-gosu case
  export PATH="${HOME}/.local/bin:$PATH"
  exec "$@"
fi
