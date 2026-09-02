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
ARG JDTLS_VERSION=1.60.0
ARG JDTLS_TIMESTAMP=202606262232

# ---- System packages + custom packages (single apt-get update) -------------
# Use glob trick: Dockerfile always exists, custom-packages.txt is optional
COPY Dockerfile custom-packages.tx[t] /tmp/
RUN set -eux; \
    # Base packages
    BASE_PACKAGES=" \
      git openssh-client ca-certificates ripgrep jq \
      curl bash xz-utils unzip tzdata \
      build-essential pkg-config libssl-dev \
      cmake clang libclang-dev zlib1g-dev libsqlite3-dev \
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
COPY hotswap-agent.properties /opt/jbr/lib/hotswap/hotswap-agent.properties

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
      BUN_VERSION=$(curl -fsSL https://api.github.com/repos/oven-sh/bun/releases/latest | grep -oP '"tag_name":\s*"bun-v\K[^"]+'); \
    fi; \
    echo "Installing Bun v${BUN_VERSION}"; \
    URL="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-${BUN_ARCH}.zip"; \
    curl -fL "$URL" -o /tmp/bun.zip; \
    unzip -q /tmp/bun.zip -d /tmp; \
    mv /tmp/bun-linux-${BUN_ARCH}/bun /usr/local/bin/bun; \
    chmod +x /usr/local/bin/bun; \
    rm -rf /tmp/bun.zip /tmp/bun-linux-${BUN_ARCH}; \
    bun --version

# ---- Install Rust toolchain (rustup) ---------------------------------------
# Rust projects need a real toolchain (cargo build/test, cargo insta snapshots,
# clippy, rustfmt, ...). Install rustup into a shared system location so every
# user -- including the dynamic-UID dev user -- can use it. Linking needs a C
# toolchain (cc/ld) -- provided by build-essential in the base packages above.
# CARGO_HOME here holds
# the rustup proxy binaries (cargo/rustc/...) and the default crate cache; at
# runtime entrypoint.sh redirects CARGO_HOME to the persisted ~/.cargo mount
# while these proxies stay reachable on PATH.
ENV RUSTUP_HOME=/opt/rust/rustup \
    CARGO_HOME=/opt/rust/cargo \
    PATH=/opt/rust/cargo/bin:$PATH
ARG RUST_VERSION=stable
RUN set -eux; \
    curl -fsSL https://sh.rustup.rs -o /tmp/rustup-init.sh; \
    sh /tmp/rustup-init.sh -y --no-modify-path --profile minimal \
      --default-toolchain "${RUST_VERSION}"; \
    rm -f /tmp/rustup-init.sh; \
    rustup component add clippy rustfmt; \
    # World-writable so cargo can populate its cache even without the ~/.cargo mount
    chmod -R a+rwX "$CARGO_HOME"; \
    # Writable too: entrypoint.sh symlinks these toolchains into the persisted
    # ~/.rustup, and `rustup component add` writes into the toolchain directory
    chmod -R a+rwX "$RUSTUP_HOME"; \
    rustc --version; \
    cargo --version

# ---- Install just (command runner) -----------------------------------------
# Prebuilt static musl binary (works on both amd64 and arm64 Debian).
ARG JUST_VERSION=latest
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "$ARCH" in \
      arm64)  JUST_ARCH="aarch64" ;; \
      amd64)  JUST_ARCH="x86_64" ;; \
      *)      echo "Unsupported architecture: $ARCH"; exit 1 ;; \
    esac; \
    if [ "$JUST_VERSION" = "latest" ]; then \
      JUST_VERSION=$(curl -fsSL https://api.github.com/repos/casey/just/releases/latest | grep -oP '"tag_name":\s*"\K[^"]+'); \
    fi; \
    echo "Installing just ${JUST_VERSION}"; \
    URL="https://github.com/casey/just/releases/download/${JUST_VERSION}/just-${JUST_VERSION}-${JUST_ARCH}-unknown-linux-musl.tar.gz"; \
    curl -fL "$URL" -o /tmp/just.tar.gz; \
    mkdir -p /tmp/just; \
    tar -xzf /tmp/just.tar.gz -C /tmp/just; \
    mv /tmp/just/just /usr/local/bin/just; \
    chmod +x /usr/local/bin/just; \
    rm -rf /tmp/just.tar.gz /tmp/just; \
    just --version

# ---- Rust lint/format helpers (typos, taplo, dylint) ------------------------
# Logic lives in install-rust-tools.sh to keep this Dockerfile under the 16 KiB
# limit Apple Containers enforces. dylint is a slow source build, so this layer
# sits above the cheaper tool installs below.
ARG TYPOS_VERSION=latest
ARG TAPLO_VERSION=latest
ARG DYLINT_VERSION=latest
ARG NEXTEST_VERSION=latest
COPY install-rust-tools.sh /tmp/install-rust-tools.sh
RUN set -eux; \
    bash /tmp/install-rust-tools.sh; \
    rm -f /tmp/install-rust-tools.sh

# ---- GitHub CLI + extensions ------------------------------------------------
# Debian's gh is years behind, so install the upstream release. Extensions are
# baked into /opt/gh; gh only loads them from $HOME/.local/share/gh/extensions,
# which is container-local, so entrypoint.sh symlinks them into HOME at startup.
ARG GH_VERSION=latest
ARG GH_EXTENSIONS="github/gh-stack"
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    if [ "$GH_VERSION" = "latest" ]; then \
      GH_VERSION=$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | grep -oP '"tag_name":\s*"v\K[^"]+'); \
    fi; \
    URL="https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz"; \
    curl -fL "$URL" -o /tmp/gh.tar.gz; \
    mkdir -p /tmp/gh; \
    tar -xzf /tmp/gh.tar.gz -C /tmp/gh --strip-components=1; \
    install -m 0755 /tmp/gh/bin/gh /usr/local/bin/gh; \
    rm -rf /tmp/gh.tar.gz /tmp/gh; \
    for ext in $GH_EXTENSIONS; do XDG_DATA_HOME=/opt/gh gh extension install "$ext"; done; \
    chmod -R a+rX /opt/gh; \
    gh --version; \
    # Not `gh extension list`: that needs auth, which the build has no token for
    [ -z "$GH_EXTENSIONS" ] || ls -d /opt/gh/gh/extensions/*

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

# ---- Chromium + ChromeDriver for Selenium/TestBench ------------------------
# Selenium/TestBench need a chromedriver whose major version matches the browser.
# Google's "Chrome for Testing" only publishes chromedriver for linux64 (x86-64)
# — there is no Linux/arm64 build — so it cannot be used on Apple Silicon. Debian
# ships a version-matched `chromium` + `chromium-driver` pair for both amd64 and
# arm64, so we use those. Playwright's Chromium (driven by the MCP wrapper above)
# is left untouched. Both `chrome` and `chromedriver` are exposed on PATH via
# /usr/local/bin so tools find them without extra configuration.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends chromium chromium-driver; \
    rm -rf /var/lib/apt/lists/*; \
    ln -sf "$(command -v chromium)" /usr/local/bin/chrome; \
    ln -sf "$(command -v chromedriver)" /usr/local/bin/chromedriver; \
    chrome --version; \
    chromedriver --version

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
# Kept as a separate file: Apple Containers rejects Dockerfiles > 16 KiB
# (apple/container#735), so the script cannot live inline as a heredoc.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /work
# HOME is set dynamically in entrypoint based on HOST_HOME

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["claude"]
