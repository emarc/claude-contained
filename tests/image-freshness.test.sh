#!/usr/bin/env bash
# Source-hash label checks: a stale image is reported after a rebuild and at
# launch. Runs both launchers with a stubbed runtime, without a real one.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

for launcher in claude-contained claude-docked; do
  runtime=container
  [[ "$launcher" == claude-docked ]] && runtime=docker
  fixture="$test_root/$launcher"
  mkdir -p "$fixture/context" "$fixture/project"
  for file in Dockerfile install-rust-tools.sh entrypoint.sh codex-guard.py; do
    printf '%s v1\n' "$file" > "$fixture/context/$file"
  done
  env HOME="$fixture" CLAUDE_CONTAINED_LIB_ONLY=1 bash -s -- "$repo/$launcher" "$fixture" "$runtime" <<'SUITE'
set -euo pipefail
launcher="$1"
fixture="$2"
runtime="$3"
source "$launcher" "$fixture/project"
sources="$fixture/context"
unset CLAUDE_CONTAINED_IMAGE

# The hash is stable, covers every source file, and includes the optional
# custom-packages.txt once it exists.
hash="$(image_source_hash "$sources")"
[[ "$hash" =~ ^[0-9a-f]{16}$ ]]
[[ "$(image_source_hash "$sources")" == "$hash" ]]
printf 'changed\n' >> "$sources/entrypoint.sh"
changed="$(image_source_hash "$sources")"
[[ "$changed" != "$hash" ]]
printf 'jq\n' > "$sources/custom-packages.txt"
[[ "$(image_source_hash "$sources")" != "$changed" ]]
rm "$sources/custom-packages.txt"
hash="$changed"

# Stub the runtime: inspect prints $FAKE_INSPECT, or fails when it is empty.
eval "${runtime}() { [[ \"\$1 \$2\" == 'image inspect' && -n \"\${FAKE_INSPECT:-}\" ]] || return 1; printf '%s' \"\$FAKE_INSPECT\"; }"
# Not "$context": check_image_freshness has a local of that name, and bash scopes dynamically.
find_build_context() { printf '%s\n' "$sources"; }
apple_json() { printf '[{"name":"docker.io/library/claude-contained:latest","variants":[{"config":{"config":{"Labels":{"io.claude-contained.source":"%s"}}}}]}]' "$1"; }
docker_json() { printf '[{"Id":"sha256:1","Config":{"Labels":{"io.claude-contained.source":"%s"}}}]' "$1"; }

# The label is found in either runtime's inspect layout.
FAKE_INSPECT="$(apple_json abc)"
[[ "$(image_source_label claude-contained:latest)" == abc ]]
FAKE_INSPECT="$(docker_json def)"
[[ "$(image_source_label claude-contained:latest)" == def ]]
FAKE_INSPECT='[{"Config":{"Labels":null}}]'
[[ -z "$(image_source_label claude-contained:latest)" ]]
FAKE_INSPECT=""
if image_source_label claude-contained:latest >/dev/null; then exit 1; fi

# After a rebuild: a match passes, a shadowing older image fails loudly.
FAKE_INSPECT="$(apple_json "$hash")"
verify_rebuilt_image "$hash" 2> "$fixture/out"
[[ "$(cat "$fixture/out")" == *"up to date"* ]]
FAKE_INSPECT="$(apple_json 0000000000000000)"
if verify_rebuilt_image "$hash" 2> "$fixture/out"; then exit 1; fi
[[ "$(cat "$fixture/out")" == *"resolves to a different image"* ]]
[[ "$(cat "$fixture/out")" == *"$runtime image "* ]]

# At launch: silent when current, warns when stale, notes an unlabeled image,
# and stays quiet for a missing image or an image override.
FAKE_INSPECT="$(docker_json "$hash")"
check_image_freshness claude-contained:latest 2> "$fixture/out"
[[ ! -s "$fixture/out" ]]
FAKE_INSPECT="$(docker_json 0000000000000000)"
check_image_freshness claude-contained:latest 2> "$fixture/out"
[[ "$(cat "$fixture/out")" == *"built from other sources than this checkout"* ]]
FAKE_INSPECT="$(docker_json unknown)"
check_image_freshness claude-contained:latest 2> "$fixture/out"
[[ "$(cat "$fixture/out")" == *"has no source label"* ]]
FAKE_INSPECT=""
check_image_freshness claude-contained:latest 2> "$fixture/out"
[[ ! -s "$fixture/out" ]]
FAKE_INSPECT="$(docker_json 0000000000000000)"
CLAUDE_CONTAINED_IMAGE=custom:latest check_image_freshness custom:latest 2> "$fixture/out"
[[ ! -s "$fixture/out" ]]
SUITE
  echo "PASS: $launcher image freshness"
done
