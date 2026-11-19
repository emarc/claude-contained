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

check() {  # expected paths, expected tool args, then launcher args
  local want_paths="$1" want_args="$2"
  shift 2
  (
    source "$launcher" "$@"
    got_paths="${paths[*]}"
    got_args="${tool_args[*]+"${tool_args[*]}"}"
    if [[ "$got_paths" != "$want_paths" || "$got_args" != "$want_args" ]]; then
      echo "FAIL: $* -> paths=[$got_paths] tool_args=[$got_args]" >&2
      exit 1
    fi
  )
}

check "." "--resume abc" --resume abc
check "." "--resume abc" . --resume abc
check ". ../other" "--model sonnet -p hi" . ../other --model sonnet -p hi
check ". ../other" "--model sonnet" . ../other -- --model sonnet
check "." "--model x -- foo" --model x -- foo
check "." "" .
SUITE
  echo "PASS: $launcher tool args"
done
