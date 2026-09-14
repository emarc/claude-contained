#!/usr/bin/env bash
#
# End-to-end tests for worktree auto-locking against a real git repo.
#
# Drives maybe_offer_worktree_locks() with a linked worktree that is "hidden"
# (outside the mounted roots) and asserts that:
#   - the hidden worktree is actually locked with our owner token,
#   - cleanup removes the owner and unlocks it again,
#   - a pre-existing STALE mutex does NOT prevent locking (the reported bug:
#     the offer used to bail out and launch the container unprotected).
#
# Scripts are sourced with CLAUDE_CONTAINED_LIB_ONLY=1 so only the helpers run.
#
# Usage: tests/worktree-offer.test.sh
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root_dir="$(dirname "$here")"

worktree_is_locked() { # worktree_is_locked <main-repo> <wt-path>
  git -C "$1" worktree list --porcelain 2>/dev/null \
    | awk -v p="$2" '
        $1=="worktree"{cur=$2}
        $1=="locked" && cur==p {found=1}
        END{exit found?0:1}'
}

suite() {
  set +e
  local target="$1"
  local fails=0
  local main wt lock_dir reason

  _check() { # _check "description" <rc-that-should-be-0>
    if [[ "$2" -eq 0 ]]; then
      echo "  PASS: $1"
    else
      echo "  FAIL: $1"
      fails=$((fails + 1))
    fi
  }

  main="$(mktemp -d)/repo"
  mkdir -p "$main"
  main="$(cd "$main" && pwd -P)"
  git init -q "$main"
  git -C "$main" -c user.email=t@example.com -c user.name=test commit -q --allow-empty -m init
  wt="$(mktemp -d)/hidden-wt"
  wt="$(cd "$(dirname "$wt")" && pwd -P)/hidden-wt"
  git -C "$main" worktree add -q --detach "$wt" >/dev/null 2>&1

  # The launcher would mount the main repo (so .git/worktrees is visible) but
  # NOT the linked worktree -> it is hidden and prune-able from inside.
  mounted_roots=("$main")
  container_name="aic-test-0000"
  auto_worktree_lock_repo=""
  auto_locked_worktrees=()
  WORKTREE_CLEANUP_DONE=0
  WORKTREE_LOCK_MUTEX_DIR=""

  # --- Scenario A: normal offer locks the hidden worktree ---
  # NB: a here-string (not a pipe) so the function runs in this shell and its
  # global bookkeeping (auto_locked_worktrees) survives for cleanup.
  maybe_offer_worktree_locks "$main" "$container_name" <<<"Y" >/dev/null 2>&1
  worktree_is_locked "$main" "$wt"; _check "offer locks the hidden worktree" $?
  reason="$(git -C "$main" worktree list --porcelain | awk -v p="$wt" '$1=="worktree"{c=$2} $1=="locked" && c==p {sub(/^locked /,""); print; exit}')"
  [[ "$reason" == cc-autolocked-by:* ]]; _check "lock reason carries cc-autolocked-by owner token" $?

  cleanup_auto_worktree_locks
  rc=$?
  worktree_is_locked "$main" "$wt"; rc=$?; [[ $rc -ne 0 ]]; _check "cleanup unlocks the worktree (last owner gone)" $?

  # --- Scenario B: a STALE mutex must not block locking (regression) ---
  lock_dir="${main}/.git/claude-contained-worktree-locks.lock"
  ( exit 0 ) & dead=$!; wait "$dead" 2>/dev/null
  mkdir -p "$lock_dir"; printf '%s %s\n' "$dead" "$(date +%s)" > "${lock_dir}/owner"

  auto_worktree_lock_repo=""
  auto_locked_worktrees=()
  WORKTREE_CLEANUP_DONE=0
  WORKTREE_LOCK_MUTEX_DIR=""
  maybe_offer_worktree_locks "$main" "$container_name" <<<"Y" >/dev/null 2>&1
  worktree_is_locked "$main" "$wt"; _check "offer locks worktree despite a stale mutex" $?
  [[ ! -e "$lock_dir" ]]; _check "stale mutex was reclaimed and released" $?

  cleanup_auto_worktree_locks >/dev/null 2>&1

  git -C "$main" worktree remove --force "$wt" >/dev/null 2>&1
  rm -rf "$(dirname "$main")" "$(dirname "$wt")"
  return "$fails"
}

total_fail=0
for target in claude-contained claude-docked; do
  echo "== ${target} =="
  (
    export CLAUDE_CONTAINED_LIB_ONLY=1
    # shellcheck disable=SC1090
    source "${repo_root_dir}/${target}" . >/dev/null 2>&1
    set +e
    suite "$target"
  )
  total_fail=$((total_fail + $?))
done

echo
if [[ $total_fail -ne 0 ]]; then
  echo "FAILED: ${total_fail} assertion(s) failed."
  exit 1
fi
echo "All worktree-offer tests passed."
