#!/usr/bin/env bash
# Container-only mounts that shadow host paths holding macOS binaries:
# ~/.vaadin/jdk (the Vaadin Dev Loop ranks every JDK there without checking its
# OS) and the Playwright browser cache. Runs both launchers without a runtime.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

for launcher in claude-contained claude-docked; do
  fixture="$test_root/$launcher"
  mkdir -p "$fixture/project" "$fixture/.vaadin/jdk/jbrsdk-21-osx-aarch64/Contents/Home"
  env HOME="$fixture" CLAUDE_CONTAINED_LIB_ONLY=1 bash -s -- "$repo/$launcher" "$fixture" <<'SUITE'
set -euo pipefail
launcher="$1"
fixture="$2"
source "$launcher" "$fixture/project"
platform="linux-$(container_arch)"
[[ "$platform" == linux-aarch64 || "$platform" == linux-x86_64 ]]

# The host's JDKs are masked by an arch-tagged dir, left untouched on the host.
container_argv=()
add_platform_state_mounts "$fixture"
[[ ${#container_argv[@]} -eq 4 ]]
[[ "${container_argv[1]}" == "type=bind,src=$fixture/.claude-contained/vaadin-jdk-$platform,dst=$fixture/.vaadin/jdk" ]]
[[ "${container_argv[3]}" == "type=bind,src=$fixture/.claude-contained/ms-playwright-$platform,dst=/ms-playwright" ]]
[[ -d "$fixture/.claude-contained/vaadin-jdk-$platform" ]]
[[ -d "$fixture/.claude-contained/ms-playwright-$platform" ]]
[[ -d "$fixture/.vaadin/jdk/jbrsdk-21-osx-aarch64/Contents/Home" ]]

# A missing ~/.vaadin/jdk gets an empty mountpoint.
rm -rf "$fixture/.vaadin/jdk"
container_argv=()
add_platform_state_mounts "$fixture"
[[ -d "$fixture/.vaadin/jdk" ]]
[[ "${container_argv[1]}" == *",dst=$fixture/.vaadin/jdk" ]]

# Never mount over a symlink (it would resolve inside the container); warn and
# still persist the browsers.
rmdir "$fixture/.vaadin/jdk"
mkdir -p "$fixture/elsewhere"
ln -s "$fixture/elsewhere" "$fixture/.vaadin/jdk"
container_argv=()
add_platform_state_mounts "$fixture" 2> "$fixture/error"
[[ ${#container_argv[@]} -eq 2 ]]
[[ "${container_argv[1]}" == *",dst=/ms-playwright" ]]
[[ "$(cat "$fixture/error")" == *"~/.vaadin/jdk is not a directory"* ]]
SUITE
  echo "PASS: $launcher platform state mounts"
done
