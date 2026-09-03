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
LOG="$STATE_DIR/run.log"

log() { printf '%s autosave: %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$LOG"; }

# ------------------------------------------------------------------ refusals --
branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)"
if [ -z "$branch" ]; then
  log "REFUSED: detached HEAD in $ROOT (a pinned run -- never snapshot or move it)"
  exit 0
fi
case "$branch" in
  main|master)
    log "REFUSED: on '$branch' in $ROOT (protected branch)"
    exit 0
    ;;
esac

head_sha="$(git rev-parse HEAD 2>/dev/null)" || exit 0
ref="refs/heads/fc-autosave/$branch"

# ------------------------------------------------------------ deny patterns --
# Case-insensitive basename/path patterns that must NEVER be snapshotted, even
# if git does not ignore them. `git add -A` would happily stage all of these.
is_sensitive() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    # environment and secret material
    .env|.env.*|*/.env|*/.env.*)                       return 0 ;;
    *.pem|*.key|*.p12|*.pfx|*.jks|*.keystore)          return 0 ;;
    *id_rsa*|*id_dsa*|*id_ecdsa*|*id_ed25519*)         return 0 ;;
    *.ppk|*known_hosts|*authorized_keys)               return 0 ;;
    # credentials / auth / session / token files
    *secret*|*secrets*|*credential*|*credentials*)     return 0 ;;
    *token*|*apikey*|*api_key*|*passwd*|*password*)    return 0 ;;
    *session.json|*auth.json|*.netrc|*.npmrc|*.pypirc) return 0 ;;
    *service-account*.json|*gcloud*.json)              return 0 ;;
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
    *) return 1 ;;
  esac
}

MAX_BYTES=${FC_AUTOSAVE_MAX_BYTES:-1048576}   # 1 MiB per file

# ------------------------------------------------------------ scratch index --
# Everything below stages into a throwaway index. The real index is never
# opened, so a concurrent `git add` in this worktree cannot race us and we
# cannot corrupt the user's staging area.
tmpindex="$(mktemp "${TMPDIR:-/tmp}/fc-autosave-index.XXXXXX")" || exit 0
cleanup() { rm -f "$tmpindex" "$tmpindex.lock" 2>/dev/null; }
trap cleanup EXIT INT TERM

export GIT_INDEX_FILE="$tmpindex"
git read-tree HEAD 2>/dev/null || { log "read-tree failed"; exit 0; }

added=0; skipped_big=0; skipped_secret=0; skipped_bulk=0; removed=0

# --porcelain respects .gitignore for untracked files and reports staged,
# unstaged, and untracked changes in one pass. -z survives spaces in paths.
while IFS= read -r -d '' entry; do
  status="${entry:0:2}"
  path="${entry:3}"
  # Renames report "old -> new"; -z splits them into two records, and the
  # second record is the new path, which the next iteration handles.
  case "$status" in
    R*|C*) path="${path%% -> *}" ;;
  esac
  [ -n "$path" ] || continue

  if [ ! -e "$path" ]; then
    git update-index --force-remove -- "$path" 2>/dev/null && removed=$((removed + 1))
    continue
  fi
  [ -f "$path" ] || continue          # skip dirs, sockets, fifos

  if is_sensitive "$path"; then
    skipped_secret=$((skipped_secret + 1))
    log "  SKIP sensitive: $path"
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

  blob="$(git hash-object -w -- "$path" 2>/dev/null)" || continue
  mode=100644
  [ -x "$path" ] && mode=100755
  git update-index --add --cacheinfo "$mode,$blob,$path" 2>/dev/null \
    && added=$((added + 1))
done < <(git status --porcelain -z 2>/dev/null)

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

if push_out="$(git push -q origin "$ref:$ref" 2>&1)"; then
  log "  pushed $ref (durable on origin)"
else
  log "  push FAILED for $ref -- snapshot is LOCAL ONLY at $ref and will NOT survive container loss"
  [ -n "$push_out" ] && log "    git said: $(printf '%s' "$push_out" | head -3 | tr '\n' ' ')"
fi
exit 0
