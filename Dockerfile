# Claude Code + JetBrains Runtime (JBR) + HotswapAgent (always on) + Python
FROM node:24-bookworm-slim

# ---- JBR pins ---------------------------------------------------------------
# Use the jbrsdk flavor (full JDK) so developer tools like javac/javap/jar are
# available; the plain "jbr" flavor is runtime-only and omits them.
ARG JBR_VERSION=25.0.1
ARG JBR_BUILD=b268.52
ARG JBR_FLAVOR=jbrsdk
ARG JBR_BASE_URL=https://cache-redirector.jetbrains.com/intellij-jbr

# ---- HotswapAgent pin (Maven Central) ---------------------------------------
ARG HOTSWAP_AGENT_VERSION=2.0.3

# ---- Eclipse JDT Language Server pin ----------------------------------------
ARG JDTLS_VERSION=1.40.0
ARG JDTLS_TIMESTAMP=202409261450

# ---- System packages + custom packages (single apt-get update) -------------
# Use glob trick: Dockerfile always exists, custom-packages.txt is optional
COPY Dockerfile custom-packages.tx[t] /tmp/
RUN set -eux; \
    # Base packages
    BASE_PACKAGES=" \
      git openssh-client ca-certificates ripgrep jq \
      curl bash xz-utils unzip tzdata \
      python3 python3-pip python3-venv \
      iproute2 gosu socat \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 \
      libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 \
      libnspr4 libnss3 libpango-1.0-0 libxcomposite1 libxdamage1 \
      libxfixes3 libxkbcommon0 libxrandr2 libxtst6 xvfb zip unzip bubblewrap"; \
    \
    # Extract custom packages (non-comment, non-empty lines)
    CUSTOM_PACKAGES=""; \
    if [ -f /tmp/custom-packages.txt ]; then \
      CUSTOM_PACKAGES=$(grep -v '^#' /tmp/custom-packages.txt | grep -v '^[[:space:]]*$' | tr '\n' ' ' || true); \
      echo "Custom packages: [$CUSTOM_PACKAGES]"; \
      rm -f /tmp/custom-packages.txt; \
    fi; \
    \
    # Single apt-get update for all packages
    apt-get update && apt-get install -y --no-install-recommends \
      $BASE_PACKAGES \
      $CUSTOM_PACKAGES \
    && rm -rf /var/lib/apt/lists/*

# ---- Install JetBrains Runtime ----------------------------------------------
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "$ARCH" in \
      arm64)  JBR_ARCH="aarch64" ;; \
      amd64)  JBR_ARCH="x64" ;; \
      *)      echo "Unsupported architecture: $ARCH"; exit 1 ;; \
    esac; \
    FILE="${JBR_FLAVOR}-${JBR_VERSION}-linux-${JBR_ARCH}-${JBR_BUILD}.tar.gz"; \
    URL="${JBR_BASE_URL}/${FILE}"; \
    echo "Downloading: $URL"; \
    mkdir -p /opt/jbr; \
    curl -fL "$URL" -o /tmp/jbr.tar.gz; \
    tar -xzf /tmp/jbr.tar.gz -C /opt/jbr --strip-components=1; \
    rm -f /tmp/jbr.tar.gz; \
    /opt/jbr/bin/java -version

ENV JAVA_HOME=/opt/jbr
ENV PATH="$JAVA_HOME/bin:$PATH"

# ---- Install HotswapAgent ---------------------------------------------------
RUN set -eux; \
    mkdir -p /opt/jbr/lib/hotswap; \
    curl -fL \
      "https://repo1.maven.org/maven2/org/hotswapagent/hotswap-agent/${HOTSWAP_AGENT_VERSION}/hotswap-agent-${HOTSWAP_AGENT_VERSION}.jar" \
      -o /opt/jbr/lib/hotswap/hotswap-agent.jar

# ---- HotswapAgent global configuration --------------------------------------
RUN cat <<'EOF' > /opt/jbr/lib/hotswap/hotswap-agent.properties
# Auto-swap classes without requiring debug mode
autoHotswap=true

# Watch for class changes in common locations
extraClasspath=target/classes

# Disable plugins that add overhead (keep Spring, Vaadin, Proxy, AnonymousClassPatch)
disabledPlugins=Hibernate,Logback,Log4j2,Weld,Deltaspike,WebObjects,WildFlyELResolver,MyFaces,OmniFaces,Mojarra,Resteasy,Jersey

# Vaadin specific: reduce browser refresh delay
vaadin.liveReloadQuietTime=500
EOF

# HotSwap always on (JBR 17/21/25) - requires G1 or Serial GC
# --add-opens flags enable deep reflection for HotswapAgent class redefinition
ENV JAVA_TOOL_OPTIONS="\
  -XX:+UseG1GC \
  -XX:+AllowEnhancedClassRedefinition \
  -XX:+ClassUnloading \
  -XX:HotswapAgent=fatjar \
  --add-opens=java.base/java.lang=ALL-UNNAMED \
  --add-opens=java.base/java.lang.reflect=ALL-UNNAMED \
  --add-opens=java.base/java.io=ALL-UNNAMED \
  --add-opens=java.base/sun.nio.ch=ALL-UNNAMED \
  --add-opens=java.base/sun.security.action=ALL-UNNAMED \
  --add-opens=java.base/jdk.internal.loader=ALL-UNNAMED \
  --add-opens=java.desktop/java.beans=ALL-UNNAMED \
  --add-opens=java.desktop/com.sun.beans=ALL-UNNAMED \
  --add-opens=java.desktop/com.sun.beans.introspect=ALL-UNNAMED \
  --add-opens=java.desktop/com.sun.beans.util=ALL-UNNAMED \
  -Dvaadin.productionMode=false \
  -Dspring.devtools.restart.enabled=false"

# ---- Eclipse JDT Language Server (jdtls) ------------------------------------
RUN set -eux; \
    mkdir -p /opt/jdtls; \
    curl -fL \
      "https://download.eclipse.org/jdtls/milestones/${JDTLS_VERSION}/jdt-language-server-${JDTLS_VERSION}-${JDTLS_TIMESTAMP}.tar.gz" \
      -o /tmp/jdtls.tar.gz; \
    tar -xzf /tmp/jdtls.tar.gz -C /opt/jdtls; \
    rm -f /tmp/jdtls.tar.gz; \
    ln -s /opt/jdtls/bin/jdtls /usr/local/bin/jdtls

# ---- Install Bun ------------------------------------------------------------
ARG BUN_VERSION=latest
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "$ARCH" in \
      arm64)  BUN_ARCH="aarch64" ;; \
      amd64)  BUN_ARCH="x64" ;; \
      *)      echo "Unsupported architecture: $ARCH"; exit 1 ;; \
    esac; \
    if [ "$BUN_VERSION" = "latest" ]; then \
      BUN_VERSION=$(curl -fsSL https://api.github.com/repos/oven-sh/bun/releases/latest | grep -oP '"tag_name": "bun-v\K[^"]+'); \
    fi; \
    echo "Installing Bun v${BUN_VERSION}"; \
    URL="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-${BUN_ARCH}.zip"; \
    curl -fL "$URL" -o /tmp/bun.zip; \
    unzip -q /tmp/bun.zip -d /tmp; \
    mv /tmp/bun-linux-${BUN_ARCH}/bun /usr/local/bin/bun; \
    chmod +x /usr/local/bin/bun; \
    rm -rf /tmp/bun.zip /tmp/bun-linux-${BUN_ARCH}; \
    bun --version

# ---- Language Servers + AI CLIs --------------------------------------------
ARG AI_TOOLS_CACHE_BUST=stable
RUN set -eux; \
    echo "Refreshing AI tool layers: ${AI_TOOLS_CACHE_BUST}" >/dev/null; \
    npm install -g \
    @github/copilot \
    @google/gemini-cli \
    @openai/codex \
    typescript \
    typescript-language-server \
    pyright \
  && npm cache clean --force

# ---- Native Claude Code binary ----------------------------------------------
# Download native binary to /opt/claude/ (runtime creates user symlinks)
ARG CLAUDE_VERSION=latest
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "$ARCH" in \
      arm64)  CLAUDE_PLATFORM="linux-arm64" ;; \
      amd64)  CLAUDE_PLATFORM="linux-x64" ;; \
      *)      echo "Unsupported architecture: $ARCH"; exit 1 ;; \
    esac; \
    GCS_BUCKET="https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"; \
    if [ "$CLAUDE_VERSION" = "latest" ]; then \
      CLAUDE_VERSION=$(curl -fsSL "$GCS_BUCKET/latest"); \
    fi; \
    mkdir -p /opt/claude; \
    curl -fsSL "$GCS_BUCKET/$CLAUDE_VERSION/$CLAUDE_PLATFORM/claude" -o /opt/claude/claude; \
    chmod +x /opt/claude/claude; \
    /opt/claude/claude --version

# ---- Mistral Vibe (requires Python 3.12+, use uv for version management) ---
ENV UV_TOOL_BIN_DIR=/usr/local/bin
ENV UV_TOOL_DIR=/opt/uv-tools
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
  && /root/.local/bin/uv tool install mistral-vibe --python 3.12 \
  && chmod -R a+rX /opt/uv-tools /opt/uv-python

# ---- Playwright browser (build-time install for reliability) ----------------
# Install Chromium to a fixed location instead of user cache for container use
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN npx playwright@1.61.0 install --with-deps chromium chromium-headless-shell

# ---- Chrome wrapper for Playwright MCP compatibility ------------------------
# When projects use @playwright/mcp without --browser flag, it looks for Chrome.
# This wrapper redirects to our installed Playwright Chromium.
RUN mkdir -p /opt/google/chrome && cat <<'EOF' > /opt/google/chrome/chrome
#!/bin/bash
exec /ms-playwright/chromium-*/chrome-linux/chrome --no-sandbox "$@"
EOF
RUN chmod +x /opt/google/chrome/chrome

# ---- Non-root user ----------------------------------------------------------
RUN useradd -m -s /bin/bash dev \
  && mkdir -p /work \
  && chown -R dev:dev /work /home/dev /ms-playwright

# ---- Install SDKMAN! with Maven + JBang (as dev user) ----------------------
USER dev
RUN set -eux; \
    curl -s "https://get.sdkman.io?rcupdate=false" | bash; \
    echo 'source "/home/dev/.sdkman/bin/sdkman-init.sh"' >> /home/dev/.bashrc; \
    bash -c 'source "/home/dev/.sdkman/bin/sdkman-init.sh" && sdk install maven'; \
    bash -c 'source "/home/dev/.sdkman/bin/sdkman-init.sh" && sdk install jbang'; \
    bash -c 'source "/home/dev/.sdkman/bin/sdkman-init.sh" && mvn --version'; \
    bash -c 'source "/home/dev/.sdkman/bin/sdkman-init.sh" && jbang version'; \
    echo "SDKMAN! setup complete - Maven and JBang installed"
USER root

# ---- Symlink key binaries to /usr/local/bin ---------------------------------
# Codex runs `bash -lc` which sources /etc/profile, clobbering inherited PATH.
# Symlinks ensure JBR/Maven/JBang binaries are found via the default Debian PATH.
RUN set -eux; \
    for bin in /opt/jbr/bin/*; do \
      ln -sf "$bin" "/usr/local/bin/$(basename "$bin")"; \
    done; \
    ln -sf /home/dev/.sdkman/candidates/maven/current/bin/mvn /usr/local/bin/mvn; \
    ln -sf /home/dev/.sdkman/candidates/jbang/current/bin/jbang /usr/local/bin/jbang

# ---- Claude Code clipboard workaround --------------------------------------
# Force the classic inline TUI renderer ("tui": "default") inside the container.
# The newer fullscreen ("no-flicker") renderer (default since ~2.1.168) routes
# copy-on-select only through OSC 52 and captures the mouse. Inside a container
# attached to the host terminal there is no clipboard tool (no pbcopy/display),
# OSC 52 is silently dropped by terminals like Terminal.app, and the mouse
# capture also breaks native shift/option-drag selection -- so copying from
# Claude stops working entirely. See anthropics/claude-code#66192.
#
# This is a managed-settings file (highest precedence, Linux path), so it is
# container-scoped and never touches the host-mounted ~/.claude/settings.json.
# Remove this once the upstream renderer regression is fixed.
RUN mkdir -p /etc/claude-code \
    && printf '%s\n' '{ "tui": "default" }' > /etc/claude-code/managed-settings.json

# ---- Entrypoint (host.local setup + path parity) ---------------------------
RUN cat <<'EOF' > /usr/local/bin/entrypoint.sh
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
    "$@"
else
  # Also update PATH for root/non-gosu case
  export PATH="${HOME}/.local/bin:$PATH"
  exec "$@"
fi
EOF
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /work
# HOME is set dynamically in entrypoint based on HOST_HOME

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["claude"]
