#!/usr/bin/env bash
#
# Adversarial regression test for .claude/worktree-autosave.sh.
#
# Builds a throwaway git repo with a fake "origin" on local disk (so the push
# path is exercised for real without touching GitHub), then asserts every
# safety property the script claims. Exits non-zero on any failure.
#
#   bash .claude/tests/test_worktree_autosave.sh

set -u
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/worktree-autosave.sh"
[ -x "$SCRIPT" ] || { echo "FATAL: $SCRIPT not executable"; exit 1; }

SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/fc-autosave-test.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
pass=0; fail=0
ok()   { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  FAIL %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }

# ---------------------------------------------------------------- fixtures --
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git init -q --bare "$SANDBOX/origin.git"
git init -q -b main "$SANDBOX/wt"
cd "$SANDBOX/wt"
git remote add origin "$SANDBOX/origin.git"
echo base > tracked.txt
git add -A && git commit -q -m base
git push -q origin main

run_autosave() { ( cd "$SANDBOX/wt" && bash "$SCRIPT" >/dev/null 2>&1 ); }
snap_state()   { ( cd "$SANDBOX/wt" && printf '%s|%s|%s' \
                    "$(git rev-parse HEAD)" \
                    "$(git status --porcelain | sha256sum | cut -d' ' -f1)" \
                    "$(git rev-parse --verify --quiet HEAD 2>/dev/null)" ); }

echo "== 1. refuses main =="
before="$(snap_state)"
run_autosave
check "no ref created on main" "$(git rev-parse --verify --quiet refs/heads/fc-autosave/main || echo none)" "none"
check "state untouched on main" "$(snap_state)" "$before"

echo "== 2. refuses detached HEAD =="
git checkout -q --detach
echo x > detached-change.txt
run_autosave
n_refs=$(git for-each-ref refs/heads/fc-autosave/ --format='%(refname)' | grep -c . || true)
check "no snapshot ref while detached" "$n_refs" "0"
rm -f detached-change.txt
git checkout -q main

echo "== 3. normal branch: snapshots, filters, and pushes =="
git checkout -q -b work
echo "modified"        >  tracked.txt
echo "new source"      >  newfile.py
echo "SECRET=abc123"   >  .env
echo "-----BEGIN KEY-----" > deploy.pem
echo "tok"             >  session.json
head -c 2000000 /dev/urandom > big.bin
mkdir -p backtest/canonical_runs/run-1/checkpoints
echo '{"row":1}'       >  backtest/canonical_runs/run-1/checkpoints/2024-04-01.jsonl
before_head="$(git rev-parse HEAD)"
before_status="$(git status --porcelain | sha256sum)"
before_index="$(git diff --cached --stat | sha256sum)"
run_autosave
ref=refs/heads/fc-autosave/work
snap="$(git rev-parse --verify --quiet $ref || echo none)"
[ "$snap" != none ] && ok "snapshot ref created" || bad "snapshot ref created"

files="$(git ls-tree -r --name-only $ref 2>/dev/null)"
echo "$files" | grep -qx 'newfile.py'   && ok "included newfile.py"        || bad "included newfile.py"
echo "$files" | grep -qx '.env'         && bad "EXCLUDED .env"             || ok "EXCLUDED .env"
echo "$files" | grep -qx 'deploy.pem'   && bad "EXCLUDED deploy.pem"       || ok "EXCLUDED deploy.pem"
echo "$files" | grep -qx 'session.json' && bad "EXCLUDED session.json"     || ok "EXCLUDED session.json"
echo "$files" | grep -qx 'big.bin'      && bad "EXCLUDED oversized big.bin"|| ok "EXCLUDED oversized big.bin"
echo "$files" | grep -q  'canonical_runs' && bad "EXCLUDED canonical rows" || ok "EXCLUDED canonical rows"
check "tracked.txt content snapshotted" "$(git show $ref:tracked.txt 2>/dev/null)" "modified"

echo "== 4. caller state is byte-identical =="
check "HEAD unchanged"   "$(git rev-parse HEAD)"                "$before_head"
check "status unchanged" "$(git status --porcelain | sha256sum)" "$before_status"
check "index unchanged"  "$(git diff --cached --stat | sha256sum)" "$before_index"
check "working branch not advanced" "$(git rev-parse work)"     "$before_head"

echo "== 5. secret is absent from the pushed remote, not merely from the ref =="
remote_files="$(git --git-dir="$SANDBOX/origin.git" ls-tree -r --name-only $ref 2>/dev/null || true)"
[ -n "$remote_files" ] && ok "ref reached origin" || bad "ref reached origin"
echo "$remote_files" | grep -qx '.env' && bad "no .env on origin" || ok "no .env on origin"

echo "== 6. idempotent: no second snapshot when nothing changed =="
first="$(git rev-parse $ref)"
run_autosave
check "ref unchanged on no-op rerun" "$(git rev-parse $ref)" "$first"

echo "== 7. second snapshot fast-forwards (no force-push needed) =="
echo "more" >> newfile.py
run_autosave
second="$(git rev-parse $ref)"
[ "$second" != "$first" ] && ok "new snapshot created" || bad "new snapshot created"
git merge-base --is-ancestor "$first" "$second" && ok "fast-forward chain preserved" || bad "fast-forward chain preserved"
check "origin advanced too" "$(git --git-dir="$SANDBOX/origin.git" rev-parse $ref)" "$second"

echo "== 8. deletions are captured =="
git rm -q --cached tracked.txt >/dev/null 2>&1; rm -f tracked.txt
run_autosave
git ls-tree -r --name-only $ref | grep -qx 'tracked.txt' && bad "deletion captured" || ok "deletion captured"

echo "== 9. only the current worktree is touched =="
git worktree add -q "$SANDBOX/other" -b other main 2>/dev/null
( cd "$SANDBOX/other" && echo untouched > other.txt )
other_before="$(cd "$SANDBOX/other" && git status --porcelain | sha256sum)"
run_autosave
other_after="$(cd "$SANDBOX/other" && git status --porcelain | sha256sum)"
check "sibling worktree untouched" "$other_after" "$other_before"
check "no ref for sibling branch" "$(git rev-parse --verify --quiet refs/heads/fc-autosave/other || echo none)" "none"

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]
