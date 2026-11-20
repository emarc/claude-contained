#!/usr/bin/env bash
# Directory arguments vs tool arguments: an option the launcher doesn't know
# (e.g. --resume ID) starts the tool arguments instead of being mounted as a
# path. Runs both launchers without a runtime.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

for launcher in claude-contained claude-docked; do
  fixture="$test_root/$launcher"
  mkdir -p "$fixture/project" "$fixture/other"
  env HOME="$fixture" CLAUDE_CONTAINED_LIB_ONLY=1 bash -s -- "$repo/$launcher" "$fixture" <<'SUITE'
set -euo pipefail
launcher="$1"
fixture="$2"
cd "$fixture/project"

check() {  # expected paths, tool args, launcher state, then launcher args
  local want_paths="$1" want_args="$2" want_state="$3"
  shift 3
  (
    source "$launcher" "$@"
    got_paths="${paths[*]}"
    got_args="${tool_args[*]+"${tool_args[*]}"}"
    got_state="tool=$tool yolo=$yolo_mode ports=${port_maps[*]+"${port_maps[*]}"}"
    if [[ "$got_paths" != "$want_paths" || "$got_args" != "$want_args" || "$got_state" != "$want_state" ]]; then
      echo "FAIL: $* -> paths=[$got_paths] tool_args=[$got_args] $got_state" >&2
      exit 1
    fi
  )
}

plain="tool=claude yolo=0 ports="

# Unrecognised options start the tool arguments.
check "." "--resume abc" "$plain" --resume abc
check "." "--resume abc" "$plain" . --resume abc
check ". ../other" "--model sonnet -p hi" "$plain" . ../other --model sonnet -p hi
check ". ../other" "--model sonnet" "$plain" . ../other -- --model sonnet
check "." "--model x -- foo" "$plain" --model x -- foo
check "." "" "$plain" .

# Launcher options after a directory are still the launcher's.
check "." "" "tool=claude yolo=1 ports=" . -y
check "." "" "tool=codex yolo=0 ports=" . -t codex
check "." "" "tool=claude yolo=0 ports=8080:8080" . -p 8080:8080
check ". ../other" "--resume abc" "tool=claude yolo=1 ports=" . -y ../other --resume abc

# Aliased use (claude=claude-contained --yolo): tool flags after the first
# unrecognised option stay with the tool, even when the launcher has one too.
check "." "--resume abc" "tool=claude yolo=1 ports=" --yolo --resume abc
check "." "--resume abc -p hi -y" "tool=claude yolo=1 ports=" --yolo --resume abc -p hi -y
check "." "-p hi" "tool=claude yolo=1 ports=" --yolo -- -p hi
SUITE
  echo "PASS: $launcher tool args"
done
