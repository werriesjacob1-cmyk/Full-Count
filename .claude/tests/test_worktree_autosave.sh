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

echo "== 1b. refuses deployment, evidence and snapshot branch classes =="
# Autosave can never WRITE these refs -- the destination is always
# fc-autosave/<branch> -- but pushing a snapshot of an evidence branch's
# working state creates a second, non-canonical copy of the record under a
# name that reads as authoritative. That is the thing being refused.
for protected in gh-pages prediction-ledger/publication-events \
                 prospective/hits_pa_v1 evidence/anything canonical/rows \
                 fc-autosave/work; do
  git checkout -q -B "$protected" main
  echo "dirty" > protected-change.txt
  before="$(snap_state)"
  run_autosave
  check "no ref created on $protected" \
        "$(git rev-parse --verify --quiet "refs/heads/fc-autosave/$protected" || echo none)" "none"
  check "state untouched on $protected" "$(snap_state)" "$before"
  rm -f protected-change.txt
  git checkout -q main
  git branch -q -D "$protected"
done

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

echo "== 10. the credential shapes a filename DENYLIST misses =="
# Every one of these was demonstrated by an independent security review to
# reach origin with no log line at all, because a denylist of filenames can
# only enumerate the shapes someone thought of. The allowlist inverts that.
git checkout -q work
mkdir -p .docker .kube
for f in .dockercfg .docker/config.json .kube/config kubeconfig prod.kubeconfig \
         terraform.tfstate prod.tfvars AuthKey_ABC123.p8 server.crt id.jwt \
         serviceAccountKey.json deploy_key secring.gpg client.ovpn .my.cnf \
         .rclone.conf .envrc production.env anthropic.conf; do
  mkdir -p "$(dirname "$f")" 2>/dev/null
  printf 'CREDENTIAL-MATERIAL\n' > "$f"
done
run_autosave
ref=refs/heads/fc-autosave/work
leaked=""
for f in .dockercfg .docker/config.json .kube/config kubeconfig prod.kubeconfig \
         terraform.tfstate prod.tfvars AuthKey_ABC123.p8 server.crt id.jwt \
         serviceAccountKey.json deploy_key secring.gpg client.ovpn .my.cnf \
         .rclone.conf .envrc production.env anthropic.conf; do
  git cat-file -e "$ref:$f" 2>/dev/null && leaked="$leaked $f"
done
check "no credential-shaped file reached the snapshot" "${leaked:-none}" "none"
remote_leaked=""
for f in .dockercfg .kube/config serviceAccountKey.json deploy_key; do
  git --git-dir="$SANDBOX/origin.git" cat-file -e "$ref:$f" 2>/dev/null \
    && remote_leaked="$remote_leaked $f"
done
check "and none reached ORIGIN" "${remote_leaked:-none}" "none"
rm -rf .docker .kube
rm -f .dockercfg kubeconfig prod.kubeconfig terraform.tfstate prod.tfvars \
      AuthKey_ABC123.p8 server.crt id.jwt serviceAccountKey.json deploy_key \
      secring.gpg client.ovpn .my.cnf .rclone.conf .envrc production.env \
      anthropic.conf

echo "== 11. credential CONTENT under an innocent name =="
# The decisive case: `env > notes.txt` produces a .txt the allowlist accepts,
# and no filename rule can catch it. This environment really does carry
# AWS_SECRET_ACCESS_KEY and GITHUB_TOKEN in the process environment.
printf 'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIfake/K7MDENGfake+EXAMPLEKEY\n' > notes.txt
printf -- '-----BEGIN RSA PRIVATE KEY-----\nMIIfake\n' > architecture.md
printf 'token: ghp_0123456789abcdefghijklmnopqrstuvwx\n' > config.yaml
run_autosave
check "env dump under a .txt name is refused"  "$(git cat-file -e $ref:notes.txt 2>/dev/null && echo LEAKED || echo blocked)" "blocked"
check "private key inside a .md is refused"    "$(git cat-file -e $ref:architecture.md 2>/dev/null && echo LEAKED || echo blocked)" "blocked"
check "a GitHub token inside a .yaml is refused" "$(git cat-file -e $ref:config.yaml 2>/dev/null && echo LEAKED || echo blocked)" "blocked"
rm -f notes.txt architecture.md config.yaml

echo "== 12. a NEW UNTRACKED DIRECTORY is actually backed up =="
# git status --porcelain collapses an untracked directory to `src/`, so every
# file inside was dropped with no log line while the script reported success.
# That is the precise failure this script exists to prevent, reported as a win.
mkdir -p src/newfeature
echo "print('work in progress')" > src/newfeature/main.py
echo "notes" > src/newfeature/README.md
run_autosave
check "file in a new untracked dir is snapshotted" \
      "$(git cat-file -p $ref:src/newfeature/main.py 2>/dev/null)" "print('work in progress')"
check "and so is its sibling" \
      "$(git cat-file -e $ref:src/newfeature/README.md 2>/dev/null && echo yes || echo no)" "yes"
rm -rf src

echo "== 13. an included file is LOGGED, not only an excluded one =="
echo "traceable" > traceable.py
run_autosave
check "the log names the file it included" \
      "$(grep -c 'include: traceable.py' "$(git rev-parse --absolute-git-dir)/fc-autosave/run.log")" "1"
rm -f traceable.py

echo "== 14. a diverged remote does not silently end durability =="
# Refusing to force is correct. Stopping there was not: the next snapshot
# re-parents on the LOCAL ref, so the chain could never re-converge, and the
# only evidence was a log file inside .git/ that nothing surfaces.
run_autosave
# A TRUE divergence, not a rewind: origin's ref must carry a commit that is
# not in the local ref's history, or the next push is just a fast-forward and
# nothing is being tested. Built with commit-tree so it shares no history.
orphan="$(git commit-tree "$(git rev-parse HEAD^{tree})" -m "someone else's snapshot" </dev/null)"
git push -q --force origin "$orphan:$ref"
origin_before="$(git --git-dir="$SANDBOX/origin.git" rev-parse "$ref")"
echo "after divergence" > diverged.py
run_autosave
recovered="$(git --git-dir="$SANDBOX/origin.git" for-each-ref \
             --format='%(refname)' 'refs/heads/fc-autosave/work-*' | head -1)"
check "a diverged push rolls to an alternate ref on origin" \
      "$([ -n "$recovered" ] && echo recovered || echo lost)" "recovered"
check "and the diverged original on origin was NOT overwritten" \
      "$(git --git-dir="$SANDBOX/origin.git" rev-parse "$ref")" "$origin_before"
rm -f diverged.py

# WHERE SNAPSHOTS ACTUALLY GO RIGHT NOW. Check 14 deliberately diverges the
# remote, after which the script records an alternate ref and stays on it -- so
# a later check that hardcodes fc-autosave/work inspects an EMPTY ref and
# passes no matter what the script did. Two checks below were vacuous for
# exactly that reason until the guard they test was disabled and they passed
# anyway. A test that survives removing the thing it tests is not a test.
live_ref() {
  cat "$(git rev-parse --absolute-git-dir)/fc-autosave/alt-ref" 2>/dev/null \
    || echo "refs/heads/fc-autosave/$(git symbolic-ref --quiet --short HEAD)"
}

echo "== 15. a SYMLINK is never dereferenced =="
# One command was a total bypass of every other rule: [ -f ] follows the link
# and `git hash-object -w` dereferences it, so every path check ran against the
# link NAME while the target's bytes were committed.
git checkout -q work
mkdir -p "$SANDBOX/outside"
printf 'aws_secret_access_key = wJalrXUtnFEMIfake/K7MDENGfake\n' > "$SANDBOX/outside/credentials"
# The second link's target is ORDINARY text, so the content scan cannot be what
# stops it -- only refusing to dereference can. That is what isolates the guard.
printf 'just some ordinary prose, nothing secret here at all\n' > "$SANDBOX/outside/plain.txt"
ln -sf "$SANDBOX/outside/credentials" notes.txt
ln -sf "$SANDBOX/outside/plain.txt" innocent.py
run_autosave
ref="$(live_ref)"
check "a symlink to credentials is not committed" \
      "$(git cat-file -e $ref:notes.txt 2>/dev/null && echo LEAKED || echo blocked)" "blocked"
check "a symlink to ORDINARY content is refused too (the guard, not the scan)" \
      "$(git cat-file -e $ref:innocent.py 2>/dev/null && echo DEREFERENCED || echo blocked)" "blocked"
rm -f notes.txt innocent.py

echo "== 16. the credential shapes the first regex list missed =="
# Every one of these reached ORIGIN through the first content scan: the sk-
# pattern forbade further hyphens, the grep was case-sensitive, and only the
# first 64 KiB was read.
# PAYLOADS ARE ASSEMBLED AT RUNTIME, never written as literals. GitHub push
# protection scans the diff itself and rejected an earlier version of this file
# over the fake Stripe key below -- correctly, by shape. A test fixture that
# cannot be committed is not a fixture, and asking for an unblock to land a
# string that looks like a live key is the wrong instinct entirely.
P="$(printf 'abcdefghijklmnopqrstuvwxyz')"
Q="$(printf '0123456789')"
i=0
for payload in \
  "sk-proj-$P$Q" \
  "sk-ant-api03-$P$Q" \
  "sk""_live_$P$Q" \
  "SG.${P}abcdefg.${P}${Q}${P}${Q}abcdefghijk" \
  "npm""_$P$Q" \
  "pypi""-AgEIcHlwaS5vcmc$P" \
  "glpat""-${P}${Q}" \
  "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig" \
  "postgres://admin:hunter2@db.internal:5432/prod" \
  "aws_secret_access_key = wJalrXUtnFEMIfake/K7MDENGfake"; do
  i=$((i + 1))
  printf '%s\n' "$payload" > "shape$i.txt"
done
{ head -c 100000 /dev/zero | tr '\0' 'a'; printf '\n%s_%s%s\n' "gh""p" "$P" "$Q"; } > padded.txt
run_autosave
ref="$(live_ref)"
leaked=""
for n in $(seq 1 10); do
  git cat-file -e "$ref:shape$n.txt" 2>/dev/null && leaked="$leaked shape$n"
done
git cat-file -e "$ref:padded.txt" 2>/dev/null && leaked="$leaked padded"
check "no credential shape reached the snapshot" "${leaked:-none}" "none"
rm -f shape*.txt padded.txt

echo "== 17. recovery does not burn a new origin ref every run =="
before_refs=$(git --git-dir="$SANDBOX/origin.git" for-each-ref \
              --format='%(refname)' 'refs/heads/fc-autosave/*' | grep -c . || true)
for round in 1 2 3 4; do
  echo "round $round" > "spam$round.py"
  run_autosave
done
after_refs=$(git --git-dir="$SANDBOX/origin.git" for-each-ref \
             --format='%(refname)' 'refs/heads/fc-autosave/*' | grep -c . || true)
check "four more snapshots allocate at most one new ref" \
      "$([ $((after_refs - before_refs)) -le 1 ] && echo bounded || echo "spammed:$before_refs->$after_refs")" "bounded"
check "and the snapshots still reach origin" \
      "$(git --git-dir="$SANDBOX/origin.git" cat-file -e "$(live_ref):spam4.py" 2>/dev/null && echo durable || echo lost)" "durable"
rm -f spam1.py spam2.py spam3.py spam4.py

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]
