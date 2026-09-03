#!/usr/bin/env bash
#
# Full Count worktree autosave.
#
# THREAT MODEL (revised 2026-08-27 after a real loss)
# ---------------------------------------------------
# The original version of this script protected against process death inside a
# living container. That is not the failure that actually happened.
#
# On 2026-08-27 an idle session's container was reclaimed. The entire filesystem
# went with it -- including .git, including every local ref. A snapshot written
# only to a local ref rescued exactly nothing. Roughly 90 minutes of work and an
# in-progress canonical row artifact were destroyed.
#
# So the threat model is now: TOTAL CONTAINER LOSS. Durability means "the bytes
# are on the GitHub remote", and nothing weaker counts.
#
# DESIGN
# ------
#   * Snapshots go to refs/heads/fc-autosave/<branch> -- a real branch namespace.
#     Custom namespaces such as refs/fc-autosave/* are REJECTED by this host's
#     git credentials with HTTP 403 at the RPC. A --dry-run of that push
#     misleadingly succeeds, so the dry run is not a valid check.
#   * Each snapshot is parented on the PREVIOUS snapshot, so the ref always
#     fast-forwards and a plain `git push` suffices. No force-push, ever.
#   * Remote push is ON by default. Set FC_AUTOSAVE_PUSH=0 to disable.
#   * The working branch, HEAD, the real index, and the working tree are never
#     touched. All staging happens in a scratch GIT_INDEX_FILE.
#   * Sensitive and oversized files are excluded and the exclusions are LOGGED,
#     never silent.
#
# It is a backup, not a commit. Work is still uncommitted on the real branch and
# still needs a real commit.

set -u

# ---------------------------------------------------------------- discovery --
# Resolve the worktree dynamically. No hard-coded paths: this script runs in
# whichever worktree the session is actually in, and there are many.
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$ROOT" ] || exit 0
cd "$ROOT" || exit 0

# State lives under the GIT DIR, not the working tree. Writing the log into the
# worktree would create untracked files, which (a) shows up in `git status` and
# (b) makes the harness Stop hook exit 2 and wedge the end of every session.
# Putting it here means the script leaves `git status --porcelain` genuinely
# byte-identical without depending on anyone remembering to .gitignore it.
GIT_DIR_ABS="$(git rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
STATE_DIR="$GIT_DIR_ABS/fc-autosave"
mkdir -p "$STATE_DIR" 2>/dev/null

# ONE RUN AT A TIME. The hook's rate limiter is a non-atomic check-then-set, and
# parallel tool calls in a single turn fire simultaneous PostToolUse hooks -- a
# review admitted two runs into one 180s window routinely. Concurrent runs then
# collide on the remote ref lock, and the loser misreads "cannot lock ref" as
# divergence and permanently migrates the durability ref to an alternate. mkdir
# is atomic on every POSIX filesystem, which is the point of using it here.
LOCK_DIR="$STATE_DIR/lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # A lock older than 10 minutes is a crashed run, not a live one.
  if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
    rmdir "$LOCK_DIR" 2>/dev/null && mkdir "$LOCK_DIR" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
# ONE trap, installed HERE -- immediately after the lock is taken, because
# every refusal path between here and the staging loop exits, and a lock leaked
# by an early exit would silently disable autosave for the rest of the session.
# It must also be the ONLY `trap ... EXIT` in this file: a second one does not
# compose, it replaces, which is exactly how the lock first leaked.
cleanup() {
  [ -n "${tmpindex:-}" ] && rm -f "$tmpindex" "$tmpindex.lock" 2>/dev/null
  rmdir "$LOCK_DIR" 2>/dev/null
  return 0
}
trap cleanup EXIT INT TERM
LOG="$STATE_DIR/run.log"

log() { printf '%s autosave: %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$LOG"; }

# ------------------------------------------------------------------ refusals --
branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)"
if [ -z "$branch" ]; then
  log "REFUSED: detached HEAD in $ROOT (a pinned run -- never snapshot or move it)"
  exit 0
fi
# PROTECTED BRANCH CLASSES. The destination ref is always
# refs/heads/fc-autosave/<branch>, so autosave can never WRITE any of these --
# but it would still PUBLISH a snapshot of their working state to origin under
# a name that reads as authoritative. For evidence and deployment branches that
# is the problem: a pushed fc-autosave/prediction-ledger-* ref is a second,
# non-canonical copy of evidence, and FULL COUNT's whole discipline is that
# evidence estates are never pooled or duplicated into look-alike namespaces.
# Container-loss protection is not worth manufacturing a shadow copy of the
# record, so these refuse outright and the operator commits deliberately.
# MATCHED CASE-INSENSITIVELY AND ON PREFIX. The first version matched the
# literal lowercase name and required a slash, so a review pushed snapshots of
# `Main`, `MASTER`, `GH-Pages`, `main.bak`, `release/main`, `prospective2` and
# -- worst -- `prediction-ledger-2026`, a hyphen where the guard expected a
# slash. That last one publishes precisely the "second, non-canonical copy of
# the record" this block exists to prevent. Git refnames are case-sensitive, so
# `Main` and `main` are genuinely different branches; that is exactly why the
# guard must be case-INsensitive, since the risk is a human-plausible variant,
# not an exact string.
lower_branch="$(printf '%s' "$branch" | tr '[:upper:]' '[:lower:]')"
case "$lower_branch" in
  main|master|gh-pages|main.*|master.*|*/main|*/master|*/gh-pages)
    log "REFUSED: on '$branch' in $ROOT (protected/deployment branch)"
    exit 0
    ;;
  prediction-ledger*|prospective*|evidence*|canonical*|*/prediction-ledger*|*/prospective*|*/evidence*|*/canonical*)
    log "REFUSED: on '$branch' in $ROOT (scientific evidence branch -- a \
pushed snapshot would be a second, non-canonical copy of the record)"
    exit 0
    ;;
  fc-autosave*|*/fc-autosave*)
    log "REFUSED: on '$branch' in $ROOT (already a snapshot ref -- never \
snapshot a snapshot)"
    exit 0
    ;;
esac

head_sha="$(git rev-parse HEAD 2>/dev/null)" || exit 0
ref="refs/heads/fc-autosave/$branch"

# ONCE WE HAVE ROLLED TO AN ALTERNATE, STAY ON IT. The first recovery re-derived
# the alternate every run and its "is this taken?" guard consulted
# refs/remotes/origin/<alt> -- which `git push` itself updates. So each run saw
# the previous alternate as taken and burned the next one: a review measured 19
# junk branches on origin in about an hour at the 180s cadence, ending in
# permanent silent durability loss. That is the failure the recovery exists to
# prevent, reached by a different road.
alt_state="$STATE_DIR/alt-ref"
if [ -s "$alt_state" ]; then
  stored="$(cat "$alt_state" 2>/dev/null)"
  case "$stored" in
    refs/heads/fc-autosave/*) ref="$stored" ;;
  esac
fi

# ------------------------------------------------------------ deny patterns --
# Case-insensitive basename/path patterns that must NEVER be snapshotted, even
# if git does not ignore them. `git add -A` would happily stage all of these.
is_sensitive() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    # environment and secret material
    # Templates are meant to be committed and contain no secret by definition;
    # they were being skipped, which silently froze an edited .env.example at
    # its old blob. Checked BEFORE the .env family below.
    *.example|*.sample|*.template|*.dist)              return 1 ;;
    .env|.env.*|*/.env|*/.env.*)                       return 0 ;;
    *.pem|*.key|*.p12|*.pfx|*.jks|*.keystore)          return 0 ;;
    *id_rsa*|*id_dsa*|*id_ecdsa*|*id_ed25519*)         return 0 ;;
    *.ppk|*known_hosts|*authorized_keys)               return 0 ;;
    # credentials / auth / session / token files
    *secret*|*secrets*|*credential*|*credentials*)     return 0 ;;
    *token*|*apikey*|*api_key*|*passwd*|*password*)    return 0 ;;
    *session.json|*auth.json|*.netrc|*.npmrc|*.pypirc) return 0 ;;
    *service-account*.json|*gcloud*.json)              return 0 ;;
    # Shapes an independent review pushed past the old list. These matter
    # BECAUSE they carry allowlisted extensions (.json, .conf, .crt), so the
    # allowlist alone waves them through -- serviceAccountKey.json is the
    # standard Firebase/GCP private-key filename and the old pattern used a
    # hyphen, which that name does not contain.
    *serviceaccount*|*service_account*)                return 0 ;;
    .docker/*|*/.docker/*|.dockercfg|*/.dockercfg)     return 0 ;;
    .kube/*|*/.kube/*|*kubeconfig*)                    return 0 ;;
    *.tfstate|*.tfstate.*|*.tfvars|*.tfvars.*)         return 0 ;;
    *.p8|*.jwt|*.gpg|*.asc|*.ovpn|*.kdbx)              return 0 ;;
    *.crt|*.cer|*.der|*.csr)                           return 0 ;;
    *deploy_key*|*deploy-key*|*.pub)                   return 0 ;;
    .envrc|*/.envrc|.my.cnf|*/.my.cnf)                 return 0 ;;
    .rclone.conf|*/.rclone.conf|*.kubeconfig)          return 0 ;;
    *.htpasswd|*cookies.txt|*.pgpass)                  return 0 ;;
    # sensitive local config
    .git-credentials|*/.git-credentials)               return 0 ;;
    .aws/*|*/.aws/*|.ssh/*|*/.ssh/*)                   return 0 ;;
    .claude/settings.local.json)                       return 0 ;;
    *) return 1 ;;
  esac
}

# Bulk generated artifacts. These belong in their own durable channel (the
# canonical checkpoint mechanism), not smuggled into a code snapshot -- a
# canonical row artifact is hundreds of MB and would make the ref unpushable.
is_bulk_artifact() {
  case "$1" in
    backtest/canonical_runs/*|*/backtest/canonical_runs/*) return 0 ;;
    backtest/canonical/*|*/backtest/canonical/*)           return 0 ;;
    *.jsonl|*.parquet|*.feather|*.arrow|*.db|*.sqlite*)    return 0 ;;
    *.pkl|*.pickle|*.joblib|*.npy|*.npz)                   return 0 ;;
    *.tar|*.tar.gz|*.tgz|*.zip|*.7z|*.gz|*.bz2|*.xz)       return 0 ;;
    .pybaseball/*|*/.pybaseball/*)                         return 0 ;;
    # Dependency and build trees. --untracked-files=all enumerates these when
    # .gitignore happens not to name them; a review measured 4000 untracked
    # files taking 73.7s, longer than the 180s cadence leaves room for, so runs
    # would overlap. None of it is work worth recovering.
    node_modules/*|*/node_modules/*)                       return 0 ;;
    .venv/*|*/.venv/*|venv/*|*/venv/*)                     return 0 ;;
    site-packages/*|*/site-packages/*)                     return 0 ;;
    build/*|*/build/*|dist/*|*/dist/*|target/*|*/target/*) return 0 ;;
    .next/*|*/.next/*|.tox/*|*/.tox/*)                     return 0 ;;
    .mypy_cache/*|*/.mypy_cache/*)                         return 0 ;;
    .pytest_cache/*|*/.pytest_cache/*)                     return 0 ;;
    __pycache__/*|*/__pycache__/*|*.pyc)                   return 0 ;;
    *) return 1 ;;
  esac
}

# ════════════════════════════════════════════════════════════════════════
# THE ALLOWLIST. This is the primary gate; is_sensitive() below it is now a
# second line of defence rather than the only one.
#
# WHY IT WAS INVERTED. An independent security review demonstrated BY
# EXECUTION that the filename denylist alone let 18 credential-shaped files
# reach origin with no log line at all: .dockercfg, .docker/config.json,
# .kube/config, kubeconfig, terraform.tfstate, AuthKey_*.p8, server.crt,
# id.jwt, serviceAccountKey.json (the standard Firebase name -- the deny
# pattern used a hyphen), deploy_key and kubeconfig (no dot, so *.key and
# *.p12 could not match), secring.gpg, client.ovpn, .my.cnf, .rclone.conf,
# .envrc, production.env, and more. A denylist of filenames can only ever
# enumerate the shapes someone thought of; the set of credential filenames is
# open-ended, and a miss was completely silent.
#
# An allowlist inverts that: an unrecognised file is NOT snapshotted, and the
# failure mode becomes "your backup missed a file" instead of "your
# credentials are on a public remote".
#
# A file is eligible if EITHER
#   (a) git already tracks it -- the repository has already accepted it, so
#       snapshotting the working copy leaks nothing new; or
#   (b) its extension/basename is on the source-and-docs list below.
# Everything eligible then still has to pass is_sensitive(), is_bulk_artifact(),
# the size cap, and a CONTENT scan.
is_allowed_kind() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    *.py|*.pyi|*.ipynb)                                    return 0 ;;
    *.js|*.mjs|*.cjs|*.ts|*.tsx|*.jsx)                     return 0 ;;
    *.html|*.htm|*.css|*.scss|*.svg)                       return 0 ;;
    *.sh|*.bash|*.zsh|*.mk|makefile|*/makefile)            return 0 ;;
    *.md|*.markdown|*.rst|*.txt)                           return 0 ;;
    *.json|*.yaml|*.yml|*.toml|*.ini|*.cfg)                return 0 ;;
    *.sql|*.csv|*.tsv)                                     return 0 ;;
    *.gitignore|.gitignore|*.gitattributes|.gitattributes) return 0 ;;
    # Templates are source: .env.example exists to be committed, and skipping
    # it silently froze an edited one at its old blob.
    *.example|*.sample|*.template|*.dist)                  return 0 ;;
    *) return 1 ;;
  esac
}

# CONTENT, not just the name. The reviewer's point that this environment
# carries AWS_SECRET_ACCESS_KEY / GITHUB_TOKEN / GH_TOKEN in the process
# environment is the decisive one: an ordinary `env > notes.txt` produces a
# .txt file that the allowlist accepts and no filename rule could catch.
# Cheap: only the first 64 KiB is read, and only for files already eligible.
looks_like_secret_content() {
  # BEST EFFORT, AND SAID SO OUT LOUD. A round-3 review got 11 shapes past the
  # previous version -- base64/hex/URL-encoded tokens, a value split across two
  # lines, `credential:` (a name the alternation lacked), an opaque 32-char key
  # with no name context at all, and UTF-16 whose NUL interleaving defeats any
  # byte regex. A content regex cannot be made complete, so this is one layer;
  # the allowlist, the symlink and hardlink guards, and the pinned push
  # destination are the others, and none of them relies on this one.

  # UTF-16 / UTF-32 are unscannable by a byte-oriented grep. Refuse rather than
  # pretend: a BOM here means "I cannot inspect this", not "this is clean".
  if head -c 4 -- "$1" 2>/dev/null | LC_ALL=C grep -qP '^(\xff\xfe|\xfe\xff)' 2>/dev/null; then
    return 0
  fi

  # TIER 1 -- shapes that are credentials wherever they appear.
  if LC_ALL=C grep -qiE \
      -e '-----begin [a-z ]*private key-----' \
      -e '\b(akia|asia)[0-9a-z]{16}\b' \
      -e '\bgh[pousr]_[a-z0-9]{20,}' \
      -e '\bgithub_pat_[a-z0-9_]{20,}' \
      -e '\bglpat-[a-z0-9_-]{16,}' \
      -e '\bxox[baprs]-[a-z0-9-]{10,}' \
      -e '\bsk[-_][a-z0-9_-]{16,}' \
      -e '\b(pk|rk)_live_[a-z0-9]{16,}' \
      -e '\bsg\.[a-z0-9_-]{20,}\.[a-z0-9_-]{20,}' \
      -e '\bnpm_[a-z0-9]{20,}' \
      -e '\bpypi-[a-z0-9_-]{16,}' \
      -e '\baiza[0-9a-z_-]{30,}' \
      -e '\bya29\.[a-z0-9_-]{20,}' \
      -e '\bey[a-z0-9_-]{10,}\.ey[a-z0-9_-]{10,}\.' \
      -e '://[^/[:space:]:]+:[^/[:space:]@]+@' \
      -- "$1" 2>/dev/null; then
    return 0
  fi

  # TIER 2 -- a secret-ish NAME assigned a literal that looks like a credential.
  # `credential`, `passphrase` and `bearer` were missing. NOT a bare `auth*`:
  # that matched `authoritative =` in grade_results.py and
  # `settlement_authority:` across the live tests -- core domain vocabulary
  # here -- so it is spelled out as authorization / auth_token / auth_key. Lines that
  # merely REFERENCE a secret are excluded, which is what keeps the false
  # positives at 2 of 1872 tracked files rather than 14.
  if LC_ALL=C grep -iE \
      '(secret|token|passwd|password|passphrase|credential|bearer|authorization|auth[_-]?token|auth[_-]?key|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret)[a-z0-9_]*[[:space:]]*[=:][[:space:]]*["'"'"']?[a-z0-9/+=_$-]{12,}' \
      -- "$1" 2>/dev/null \
      | LC_ALL=C grep -qvE '\$\{\{|\$\(|\$[A-Za-z_]|os\.environ|getenv|process\.env|secrets\.|env\.|input\(|prompt|placeholder|example|xxxx|\.\.\.' \
      2>/dev/null; then
    return 0
  fi

  # TIER 3 -- a secret-ish NAME on one line with its value on the NEXT. JSON and
  # YAML wrap constantly, and a line-based grep cannot see it.
  if LC_ALL=C grep -iA1 -E \
      '(secret|token|passwd|password|passphrase|credential|bearer|api[_-]?key|access[_-]?key|private[_-]?key)[a-z0-9_]*["'"'"']?[[:space:]]*[=:][[:space:]]*$' \
      -- "$1" 2>/dev/null \
      | LC_ALL=C grep -qE '^[[:space:]]*["'"'"']?[A-Za-z0-9/+=_-]{12,}' 2>/dev/null; then
    return 0
  fi

  # NO ENTROPY TIER, AND THAT IS A MEASURED DECISION, not an oversight.
  # A review recommended flagging high-entropy opaque tokens, since an encoded
  # or context-free secret has no name and no prefix. I implemented it and
  # measured it against this repository: a 24+ character mixed-case-with-digits
  # run flagged 25 of 1872 tracked files, INCLUDING generate_picks.py,
  # mlb_sources.py, grade_results.py and mlb_daily.py -- the four files most
  # likely to be mid-edit when a container dies. Long identifiers, base64
  # assets, minified JS and hashes are indistinguishable from keys at this
  # level of analysis.
  #
  # Silently not backing up the core of the product is a worse outcome than the
  # risk it removes, so the tier was dropped rather than shipped. THE HONEST
  # CONSEQUENCE, stated rather than buried: an opaque secret with no name
  # context and no recognisable prefix -- `OPAQUE_KEY_MATERIAL_9f3a...` in a
  # .txt, or a base64-wrapped token -- is NOT caught by content inspection.
  # What covers it instead is structural: the allowlist, the symlink and
  # hardlink guards, and above all the pinned push destination, which bounds
  # where a snapshot can go even when its contents are not understood.

  return 1
}

MAX_BYTES=${FC_AUTOSAVE_MAX_BYTES:-1048576}   # 1 MiB per file
# A ceiling on how many paths one run stages. Beyond this the tree is a data
# directory, not a working tree, and hashing it every 180s starves the session.
# Stopping loudly beats a run that never finishes.
MAX_FILES=${FC_AUTOSAVE_MAX_FILES:-1500}

# ------------------------------------------------------------ scratch index --
# Everything below stages into a throwaway index. The real index is never
# opened, so a concurrent `git add` in this worktree cannot race us and we
# cannot corrupt the user's staging area.
tmpindex="$(mktemp "${TMPDIR:-/tmp}/fc-autosave-index.XXXXXX")" || exit 0
# (cleanup + trap are installed above, at lock acquisition.)

export GIT_INDEX_FILE="$tmpindex"
git read-tree HEAD 2>/dev/null || { log "read-tree failed"; exit 0; }

added=0; skipped_big=0; skipped_secret=0; skipped_bulk=0; removed=0
skipped_kind=0; skipped_content=0; skipped_nonfile=0

# --porcelain respects .gitignore for untracked files and reports staged,
# unstaged, and untracked changes in one pass. -z survives spaces in paths.
while IFS= read -r -d '' entry; do
  status="${entry:0:2}"
  path="${entry:3}"
  # Renames under -z emit "R  <new>\0<old>\0": the SECOND record is the OLD
  # path and carries NO status prefix, so ${entry:3} would chop three characters
  # off it. Unreachable today -- the scratch index is seeded from HEAD, so
  # status never reports R/C -- but it is a landmine if the index handling ever
  # changes, so the shape is handled explicitly rather than described wrongly.
  case "$status" in
    R*|C*) path="${path%% -> *}" ;;
  esac
  [ -n "$path" ] || continue

  if [ ! -e "$path" ]; then
    git update-index --force-remove -- "$path" 2>/dev/null && removed=$((removed + 1))
    continue
  fi
  # A NEW UNTRACKED DIRECTORY used to vanish here. Without
  # --untracked-files=all, `git status --porcelain` collapses it to `src/`,
  # `[ -f ]` rejected the directory, and every file inside was dropped with no
  # log line while the script still reported success -- so work in a
  # newly-created directory was never backed up at all. That is the exact
  # failure this script exists to prevent, and it was reported as a success.
  # SYMLINKS ARE NEVER FOLLOWED. This was a total bypass of every other rule:
  # `[ -f ]` follows the link and `git hash-object -w` dereferences it, so
  # every path-based check ran against the LINK NAME while the TARGET's bytes
  # were committed. A security review demonstrated it in one command --
  # `ln -s ~/.aws/credentials notes.txt` -- and real AWS credentials reached
  # origin as a mode-100644 blob. A snapshot of a working tree has no business
  # reading through a link out of the tree at all.
  if [ -L "$path" ]; then
    skipped_secret=$((skipped_secret + 1))
    log "  SKIP symlink (never dereferenced): $path"
    continue
  fi
  # HARDLINKS TOO. A hardlink is indistinguishable from a regular file -- `-L`
  # is false, `-f` is true, the name can be anything -- so `ln /root/key.dat
  # notes.txt` walked straight past the symlink guard and its bytes reached
  # origin. Link count > 1 is the tell. Legitimate source files in a working
  # tree are effectively never hardlinked, so the false-positive cost is
  # negligible against reading key material out of the tree.
  links="$(stat -c %h -- "$path" 2>/dev/null || echo 1)"
  if [ "${links:-1}" -gt 1 ] 2>/dev/null; then
    skipped_secret=$((skipped_secret + 1))
    log "  SKIP hardlink (link count $links -- may alias a file outside the tree): $path"
    continue
  fi
  if [ ! -f "$path" ]; then
    skipped_nonfile=$((skipped_nonfile + 1))
    log "  SKIP not a regular file: $path"
    continue
  fi

  # ALLOWLIST FIRST. Tracked files are already in the repository's history, so
  # snapshotting the working copy discloses nothing the repo does not have.
  if ! git ls-files --error-unmatch -- "$path" >/dev/null 2>&1 \
     && ! is_allowed_kind "$path"; then
    skipped_kind=$((skipped_kind + 1))
    log "  SKIP untracked file of a kind not on the allowlist: $path"
    continue
  fi

  # A TRACKED FILE THAT IS ALSO GITIGNORED is still enumerated, and .gitignore
  # bounds untracked paths only. Honour the ignore rule for both.
  if git check-ignore -q -- "$path" 2>/dev/null; then
    skipped_kind=$((skipped_kind + 1))
    log "  SKIP gitignored: $path"
    continue
  fi
  if is_sensitive "$path"; then
    skipped_secret=$((skipped_secret + 1))
    # ABSENCE, NOT STALENESS. The scratch index is seeded from HEAD, so simply
    # skipping a TRACKED file leaves its old committed blob in the snapshot --
    # the backup then looks complete while silently holding superseded content.
    # Removing it makes the gap visible, which is the honest failure mode.
    if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
      git update-index --force-remove -- "$path" 2>/dev/null
      log "  SKIP sensitive (tracked -- REMOVED from snapshot, not left stale): $path"
    else
      log "  SKIP sensitive: $path"
    fi
    continue
  fi
  if is_bulk_artifact "$path"; then
    skipped_bulk=$((skipped_bulk + 1))
    log "  SKIP bulk artifact: $path"
    continue
  fi
  size=$(wc -c < "$path" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_BYTES" ]; then
    skipped_big=$((skipped_big + 1))
    log "  SKIP oversized ($size bytes > $MAX_BYTES): $path"
    continue
  fi

  if looks_like_secret_content "$path"; then
    skipped_content=$((skipped_content + 1))
    log "  SKIP credential-shaped CONTENT: $path"
    continue
  fi

  blob="$(git hash-object -w -- "$path" 2>/dev/null)" || continue
  mode=100644
  [ -x "$path" ] && mode=100755
  if [ "$added" -ge "$MAX_FILES" ]; then
    log "  STOPPED: $MAX_FILES files staged, ceiling reached. THIS SNAPSHOT IS"
    log "    INCOMPLETE -- commit deliberately, or raise FC_AUTOSAVE_MAX_FILES."
    break
  fi

  # LOG WHAT WENT IN, not only what was kept out. The previous version logged
  # exclusions only, so a file that silently passed every filter left no trace
  # anywhere -- which is precisely how the 18 credential files reached origin
  # unnoticed. An operator can now diff intent against the log.
  git update-index --add --cacheinfo "$mode,$blob,$path" 2>/dev/null \
    && { added=$((added + 1)); log "  include: $path"; }
done < <(git status --porcelain -z --untracked-files=all 2>/dev/null)

if [ "$added" -eq 0 ] && [ "$removed" -eq 0 ]; then
  log "nothing to snapshot on '$branch'"
  exit 0
fi

tree="$(git write-tree 2>/dev/null)" || { log "write-tree failed"; exit 0; }

# ------------------------------------------------------------------ parent --
# Parent on the PREVIOUS snapshot when one exists. That makes $ref a
# fast-forward-only chain, so a plain push works and we never need --force.
parent="$(git rev-parse --verify --quiet "$ref" 2>/dev/null || true)"
if [ -n "$parent" ]; then
  prev_tree="$(git rev-parse --verify --quiet "$parent^{tree}" 2>/dev/null || true)"
  if [ "$prev_tree" = "$tree" ]; then
    log "no change since last snapshot on '$branch'"
    exit 0
  fi
  parent_args="-p $parent"
else
  parent_args="-p $head_sha"
fi

commit="$(git commit-tree "$tree" $parent_args -m "autosave: $branch @ $(date -u +%Y-%m-%dT%H:%M:%SZ)

Working-tree snapshot taken by .claude/worktree-autosave.sh.

  worktree     : $ROOT
  branch       : $branch
  real HEAD    : $head_sha
  files staged : $added modified/new, $removed removed
  skipped      : $skipped_big oversized, $skipped_secret sensitive, $skipped_bulk bulk artifact

This commit lives ONLY on $ref. It does not touch HEAD, the working branch, the
index, or the working tree. It is a backup, not a commit -- the work is still
uncommitted on '$branch' and still needs a real commit." 2>/dev/null)" \
  || { log "commit-tree failed"; exit 0; }

# Local ref FIRST, so a push failure can never mean the snapshot was lost.
git update-ref "$ref" "$commit" 2>/dev/null || { log "update-ref failed"; exit 0; }
log "snapshotted $added file(s) (+$removed removed) of '$branch' -> $ref ($(echo "$commit" | cut -c1-12))"
[ "$skipped_big"    -gt 0 ] && log "  $skipped_big oversized file(s) skipped"
[ "$skipped_secret" -gt 0 ] && log "  $skipped_secret sensitive file(s) skipped"
[ "$skipped_bulk"   -gt 0 ] && log "  $skipped_bulk bulk artifact(s) skipped"

# -------------------------------------------------------------------- push --
# ON BY DEFAULT. A local ref does not survive container reclamation, which is
# the failure this script exists to prevent.
if [ "${FC_AUTOSAVE_PUSH:-1}" = "0" ]; then
  log "  push disabled by FC_AUTOSAVE_PUSH=0 (snapshot is LOCAL ONLY and will not survive container loss)"
  exit 0
fi

# THE DESTINATION IS PINNED ON FIRST USE AND CHECKED EVERY RUN.
# A review showed one un-prompted `git remote set-url origin /tmp/evil.git`
# converts this hook into a continuous exfiltration channel that re-fires every
# 180s, logging "pushed ... (durable on origin)" as if nothing were wrong. No
# deny/ask rule covers `git remote set-url`, and hooks bypass those rules
# anyway, so the script has to check for itself.
#
# Trust-on-first-use, not a hardcoded URL: this must work in any clone and in
# test sandboxes, and the threat being closed is the remote CHANGING under a
# session that has already been running. Stated honestly: if the remote is
# already hostile the very first time this runs, TOFU cannot help -- that is a
# real limit, and it is why the resolved URL is logged on every push rather
# than the word "origin", so a human reading the log sees where data went.
origin_url="$(git remote get-url origin 2>/dev/null || echo '')"
if [ -z "$origin_url" ]; then
  log "  no origin remote -- snapshot is LOCAL ONLY at $ref"
  exit 0
fi
pin_file="$STATE_DIR/origin-url"
if [ -s "$pin_file" ]; then
  pinned="$(cat "$pin_file" 2>/dev/null)"
  if [ "$origin_url" != "$pinned" ]; then
    log "  REFUSED TO PUSH: origin CHANGED since this worktree was first"
    log "    snapshotted. pinned='$pinned' now='$origin_url'"
    log "    Snapshot is LOCAL ONLY at $ref. If the change is legitimate,"
    log "    delete $pin_file deliberately."
    printf 'origin changed: pinned=%s now=%s\n' "$pinned" "$origin_url" \
      > "$STATE_DIR/NOT-DURABLE" 2>/dev/null
    exit 0
  fi
else
  printf '%s\n' "$origin_url" > "$pin_file" 2>/dev/null
  log "  pinned origin for this worktree: $origin_url"
fi

if push_out="$(git push -q origin "$ref:$ref" 2>&1)"; then
  log "  pushed $ref -> $origin_url (durable)"
else
  # DIVERGENCE RECOVERY. Refusing to force is correct, but the previous
  # version stopped there -- and because the next snapshot re-parents on the
  # LOCAL ref, the chain could never re-converge. Every later run logged
  # "push FAILED ... LOCAL ONLY" into a file inside .git/ that nothing
  # surfaces, while the hook discarded stdout and stderr. The end state was
  # silent, permanent loss of durability: exactly the failure that cost 90
  # minutes of work on 2026-08-27, reached quietly instead of loudly.
  #
  # So: roll to a fresh, unambiguous ref rather than force or give up. The
  # diverged remote ref is left exactly as it is -- it may be someone else's
  # snapshot, and overwriting it is the thing we refuse to do.
  # TRANSIENT CONTENTION IS NOT DIVERGENCE. "cannot lock ref" means another
  # push holds the lock right now; rolling to an alternate for that is how the
  # ref migration bug reappears. Only a genuine non-fast-forward rolls over.
  if ! printf '%s' "$push_out" | grep -qiE 'non-fast-forward|fetch first|rejected.*(fetch|behind)|stale info'; then
    log "  push failed for $ref but NOT as a divergence -- leaving the ref as is"
    log "    git said: $(printf '%s' "$push_out" | head -3 | tr '\n' ' ')"
    exit 0
  fi
  log "  push REJECTED for $ref (diverged from origin) -- not forcing"
  [ -n "$push_out" ] && log "    git said: $(printf '%s' "$push_out" | head -3 | tr '\n' ' ')"
  base="refs/heads/fc-autosave/$branch"
  n=2
  while [ "$n" -le 20 ]; do
    alt="${base}-$n"
    # Never replace a local ref whose tip is not an ancestor of this commit:
    # branch `work-2` has its own snapshot chain at fc-autosave/work-2, and
    # discarding it to recover branch `work` trades one lost backup for another.
    existing="$(git rev-parse --verify --quiet "$alt" 2>/dev/null || true)"
    if [ -n "$existing" ] && ! git merge-base --is-ancestor "$existing" "$commit" 2>/dev/null; then
      n=$((n + 1)); continue
    fi
    # PUSH FIRST, then record. The previous order created a local ref for every
    # attempt, so exhaustion left 19 junk local refs behind having pushed
    # nothing at all.
    if git push -q origin "$commit:$alt" 2>/dev/null \
       && git update-ref "$alt" "$commit" 2>/dev/null; then
      printf '%s\n' "$alt" > "$alt_state" 2>/dev/null
      log "  RECOVERED: pushed to $alt instead (durable on origin); future"
      log "    snapshots of '$branch' continue on $alt"
      exit 0
    fi
    n=$((n + 1))
  done
  log "  push FAILED for $ref and for every -2..-20 alternate -- snapshot is LOCAL ONLY and will NOT survive container loss"
  # A message in a log nothing reads is not a warning. SessionStart surfaces
  # this file, so the operator learns at the next session that durability is off.
  printf 'autosave durability FAILED at %s: no pushable ref for %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$branch" > "$STATE_DIR/NOT-DURABLE" 2>/dev/null
fi
exit 0
