#!/usr/bin/env bash
# Back up uncommitted work in THE CURRENT WORKTREE ONLY, without disturbing
# its real index, HEAD, or working tree, and without ever touching any
# other worktree.
#
# WHY THIS EXISTS. Agents in this project have twice been killed mid-task by
# a session limit. Both times the instruction "commit after each item" was
# either absent or unhonoured, because a process that gets killed cannot run
# a commit on the way out -- an instruction in a prompt is a request, not a
# guarantee. The first incident cost nothing (recovered from the worktree by
# hand); the second left 2143 uncommitted lines that only survived because
# the worktree outlived the agent and someone went looking for it.
#
# REWRITE HISTORY (2026-08-26, Part 11). The original version of this script
# `cd`ed to a single hardcoded checkout, enumerated EVERY worktree via
# `git worktree list`, and ran `git add -A && commit && push` against each
# one -- including worktrees this session was never asked to touch (P0,
# research, UX, another agent's in-progress work). That is a real cross-
# worktree mutation boundary violation, confirmed in practice: a single
# invocation from this worktree committed and pushed a file sitting in an
# unrelated worktree. This version fixes that at the root: it resolves only
# `git rev-parse --show-toplevel` (the current worktree) and never calls
# `git worktree list` at all. There is no "exclude P0/research/UX" logic
# because there is no code path that could ever reach them.
#
# WHAT IT DOES. Snapshots current tracked modifications + safe untracked
# source files into a throwaway commit object (via a temporary index +
# `write-tree`/`commit-tree`, never the real index) and pushes that object
# to a dedicated `refs/heads/autosave/<branch>` ref. The real branch's HEAD,
# the real staging area, and the real working tree are never touched by this
# script -- a crash mid-run leaves the agent's actual state exactly as it
# was. `main`/`master` and any detached HEAD are refused outright (log only,
# no commit, no push) so this can never land an automatic commit on the
# branch other agents/CI treat as ground truth.
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "$(date -u +%H:%M:%S) autosave: not inside a git worktree, skipping"; exit 0; }
cd "$ROOT" || exit 0

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || {
  echo "$(date -u +%H:%M:%S) autosave: detached HEAD in $ROOT, refusing to auto-commit (no mutation)"
  exit 0
}
case "$branch" in
  main|master)
    echo "$(date -u +%H:%M:%S) autosave: on $branch in $ROOT, refusing to auto-commit (no mutation)"
    exit 0
    ;;
esac

# Nothing to snapshot -> exit before touching anything.
git diff --quiet && git diff --cached --quiet \
  && [ -z "$(git ls-files --others --exclude-standard)" ] && exit 0

# Sensitive filenames -- fail closed regardless of .gitignore state.
is_sensitive() {
  case "$(basename -- "$1")" in
    .env|.env.*|*.pem|*.key|credentials*|secrets*|token*|auth*|private-key*) return 0 ;;
    *) return 1 ;;
  esac
}

# Known generated/operational paths for THIS repo -- never autosaved even
# when small, even when already tracked. Regenerable output is cheap to
# lose; the point of autosave is protecting unsaved SOURCE.
is_generated() {
  case "$1" in
    docs/data.json|docs/live.json|data/props/*|output/*|results/grades_*.json|results/history.json|dashboard/fullcount_board.html|backtest/*.jsonl|backtest/*.jsonl.state.json|backtest/.cache/*|.claude/.autosave-state/*|*.log) return 0 ;;
    *) return 1 ;;
  esac
}

TMP_INDEX="$(mktemp)"
cleanup() { rm -f "$TMP_INDEX"; }
trap cleanup EXIT
export GIT_INDEX_FILE="$TMP_INDEX"
git read-tree HEAD 2>/dev/null   # seed the TEMP index from HEAD; real index untouched

staged_any=0

# NOTE: each file list is captured via command substitution ($(...)) BEFORE
# looping, not streamed via `< <(git ...)`/`| while`. `git diff`/`git ls-files`
# can themselves refresh/touch the index as a side effect; running one of
# them concurrently with the `git add` calls that consume its output (which
# is exactly what process substitution and pipes do -- both sides run at the
# same time) races two git processes over the same temp-index lock. That
# race was caught empirically: it made `git add` fail with a stale-lock
# error that `2>/dev/null` was silently swallowing, so tracked-file changes
# were dropped from the snapshot without any visible failure. Capturing to a
# variable first forces the producing command to fully exit -- and release
# its lock -- before any `git add` runs.

# Modified/deleted tracked files.
modified_files="$(git diff --name-only HEAD -- .)"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  is_sensitive "$f" && { echo "$(date -u +%H:%M:%S)   skipped sensitive $f"; continue; }
  is_generated "$f" && continue
  git add -- "$f" 2>/dev/null && staged_any=1
done <<< "$modified_files"

# New (untracked, non-ignored) files -- source only, same size gate as before.
untracked_files="$(git ls-files --others --exclude-standard)"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  is_sensitive "$f" && { echo "$(date -u +%H:%M:%S)   skipped sensitive $f"; continue; }
  is_generated "$f" && continue
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt 1048576 ]; then
    echo "$(date -u +%H:%M:%S)   skipped $f ($((sz/1024))KB — regenerable, not source)"
    continue
  fi
  git add -- "$f" 2>/dev/null && staged_any=1
done <<< "$untracked_files"

if [ "$staged_any" -eq 0 ] || git diff --cached --quiet; then
  exit 0
fi

tree="$(git write-tree)" || exit 0
parent="$(git rev-parse HEAD)" || exit 0
n="$(git diff --cached --numstat | wc -l | tr -d ' ')"
commit="$(git -c user.name=autosave -c user.email=actions@github.com \
  commit-tree "$tree" -p "$parent" -m "[autosave] snapshot of $branch ($n files)

Committed automatically by .claude/worktree-autosave.sh, not by the agent.
Lives on refs/heads/autosave/$branch, NOT on $branch itself -- the real
branch HEAD was never advanced. Safe to inspect/cherry-pick/discard.")" || exit 0

ref="refs/heads/autosave/$(printf '%s' "$branch" | tr '/' '-')"
if git push -q --force origin "$commit:$ref" 2>/dev/null; then
  echo "$(date -u +%H:%M:%S) autosaved $n file(s) from $branch -> $ref ($commit)"
else
  echo "$(date -u +%H:%M:%S) autosave commit $commit created locally for $branch, push to $ref failed (local object is safe; nothing on disk was mutated)"
fi
