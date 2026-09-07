#!/usr/bin/env bash
# Rust lint/format helpers that common `just check` style targets expect:
#   typos                        spell check       (crate-ci/typos)
#   taplo                        TOML format/lint  (tamasfe/taplo)
#   cargo-dylint + dylint-link   custom lints      (trailofbits/dylint)
#   cargo-nextest                test runner       (nextest-rs/nextest)
#
# typos and taplo ship prebuilt static binaries, so they are downloaded instead
# of compiled. dylint has no prebuilt releases and must be built from source
# (several minutes); it also needs the rustc-dev / llvm-tools-preview rustup
# components, since lint libraries link against the compiler internals.
#
# Lives outside the Dockerfile because Apple Containers rejects Dockerfiles over
# 16 KiB (apple/container#735).
set -euo pipefail

# Without this, `set -e` aborts with no output at all when a lookup or extract
# step fails (an empty `grep -oP` is silent), leaving an unexplained build error
trap 'status=$?; echo "install-rust-tools.sh: FAILED at line ${LINENO} (exit ${status})" >&2' ERR

TYPOS_VERSION="${TYPOS_VERSION:-latest}"
TAPLO_VERSION="${TAPLO_VERSION:-latest}"
DYLINT_VERSION="${DYLINT_VERSION:-latest}"
NEXTEST_VERSION="${NEXTEST_VERSION:-latest}"

# Used when the GitHub API cannot be reached or rate-limits the build
TYPOS_FALLBACK=1.49.0
TAPLO_FALLBACK=0.10.0

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  arm64) RUST_ARCH="aarch64" ;;
  amd64) RUST_ARCH="x86_64" ;;
  *)     echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

# latest_tag <repo> <fallback>: never fails the build over an API hiccup
latest_tag() {
  local tag
  tag="$(curl -fsSL "https://api.github.com/repos/$1/releases/latest" \
    | grep -oP '"tag_name":\s*"\K[^"]+' || true)"
  if [ -z "$tag" ]; then
    echo "warning: could not resolve the latest $1 release; using $2" >&2
    tag="$2"
  fi
  printf '%s\n' "$tag"
}

# ---- typos (prebuilt musl tarball) -----------------------------------------
echo "Resolving typos version"
if [ "$TYPOS_VERSION" = "latest" ]; then
  TYPOS_VERSION="$(latest_tag crate-ci/typos "$TYPOS_FALLBACK")"
fi
TYPOS_VERSION="${TYPOS_VERSION#v}"
echo "Installing typos v${TYPOS_VERSION}"
URL="https://github.com/crate-ci/typos/releases/download/v${TYPOS_VERSION}/typos-v${TYPOS_VERSION}-${RUST_ARCH}-unknown-linux-musl.tar.gz"
mkdir -p /tmp/typos
curl -fL "$URL" -o /tmp/typos.tar.gz
tar -xzf /tmp/typos.tar.gz -C /tmp/typos
typos_bin="$(find /tmp/typos -type f -name typos -print -quit)"
if [ -z "$typos_bin" ]; then
  echo "error: no typos binary in $URL" >&2
  ls -lR /tmp/typos >&2
  exit 1
fi
install -m 0755 "$typos_bin" /usr/local/bin/typos
rm -rf /tmp/typos.tar.gz /tmp/typos

# ---- taplo (prebuilt gzipped binary) ---------------------------------------
echo "Resolving taplo version"
if [ "$TAPLO_VERSION" = "latest" ]; then
  TAPLO_VERSION="$(latest_tag tamasfe/taplo "$TAPLO_FALLBACK")"
fi
TAPLO_VERSION="${TAPLO_VERSION#v}"
echo "Installing taplo ${TAPLO_VERSION}"
URL="https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-${RUST_ARCH}.gz"
curl -fL "$URL" -o /tmp/taplo.gz
gunzip -c /tmp/taplo.gz > /tmp/taplo
install -m 0755 /tmp/taplo /usr/local/bin/taplo
rm -f /tmp/taplo.gz /tmp/taplo

# ---- cargo-nextest (prebuilt; get.nexte.st redirects to the release asset) --
echo "Installing cargo-nextest ${NEXTEST_VERSION}"
case "$RUST_ARCH" in
  aarch64) NEXTEST_PLATFORM="linux-arm" ;;
  *)       NEXTEST_PLATFORM="linux" ;;
esac
curl -fL "https://get.nexte.st/${NEXTEST_VERSION}/${NEXTEST_PLATFORM}" -o /tmp/nextest.tar.gz
mkdir -p /tmp/nextest
tar -xzf /tmp/nextest.tar.gz -C /tmp/nextest
install -m 0755 /tmp/nextest/cargo-nextest /usr/local/bin/cargo-nextest
rm -rf /tmp/nextest.tar.gz /tmp/nextest

# ---- dylint (built from source) --------------------------------------------
echo "Installing cargo-dylint ${DYLINT_VERSION}"
rustup component add rustc-dev llvm-tools-preview
if [ "$DYLINT_VERSION" = "latest" ]; then
  cargo install --locked cargo-dylint dylint-link
else
  cargo install --locked --version "${DYLINT_VERSION#v}" cargo-dylint dylint-link
fi

# Keep the shared toolchain usable by the dynamic-UID dev user (see Dockerfile)
chmod -R a+rwX "$CARGO_HOME"
chmod -R a+rwX "$RUSTUP_HOME"

typos --version
taplo --version
cargo nextest --version
cargo dylint --version
