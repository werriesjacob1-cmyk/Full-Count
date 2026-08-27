#!/usr/bin/env bash
#
# Resume a canonical backfill from remote durable state, in a fresh container.
#
# Needs nothing but a clone of the repository.
#
#   bash backtest/resume_canonical.sh <run_id>       # resume this exact run
#   FC_CANONICAL_RUN_ID=<run_id> bash backtest/resume_canonical.sh
#   FC_RESUME_DRY_RUN=1 bash backtest/resume_canonical.sh <run_id>  # report only
#
# An exact run id is mandatory. "Newest durable run" is not a safe recovery
# identity: proof/test runs can be newer than the real long-running artifact.
#
# ARCHITECTURE, and the bug that shaped it
# ----------------------------------------
# Found by running this script end to end rather than dry-running it: a run
# pinned to a SHA that PREDATES the durability work has no --resume-from-remote
# flag, so invoking the pinned CLI with it dies on "unrecognized arguments".
# That is a real chicken-and-egg -- identity demands the pinned SHA, but the
# resume tooling may postdate it.
#
# The separation that resolves it: RESTORING BYTES IS REGIME-NEUTRAL. It copies
# checksum-verified checkpoints onto disk and changes no scientific output, so
# it runs from whatever checkout has the durability module. Only GENERATION
# must execute at the pinned SHA, because only generation produces rows.
#
# Resuming at a different SHA is never done here. It would need
# --allow-sha-drift and would make the artifact mixed-regime, requiring a formal
# equivalence proof and an overlap replay before it could be called canonical.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

START=${FC_CANONICAL_START:-2024-04-01}
END=${FC_CANONICAL_END:-2026-08-25}
WANT_RUN="${1:-${FC_CANONICAL_RUN_ID:-}}"
[ -n "$WANT_RUN" ] || {
  echo "FATAL: an exact canonical run_id is required."
  echo "       Refusing to guess from the newest durable run."
  exit 2
}

say() { printf '%s  %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }

say "fetching the durable checkpoint branch"
python3 -c "
import sys; sys.path.insert(0,'.')
import backtest.canonical_durability as cd
r = cd.fetch_durable_branch()
print('  fetch:', r)
sys.exit(0 if r.get('ok') else 1)
" || { echo "could not reach the durable branch"; exit 1; }

read -r RUN_ID CODE_SHA DATES <<<"$(python3 -c "
import sys; sys.path.insert(0,'.')
import backtest.canonical_durability as cd
want = '$WANT_RUN'
runs = [r for r in cd.discover_durable_runs() if r['run_id'] == want]
if len(runs) != 1:
    sys.exit(1)
r = runs[0]
print(r['run_id'], r['code_git_sha'], r['dates'])
")" || { echo "no unique durable run found matching '$WANT_RUN'"; exit 1; }

[ -n "${RUN_ID:-}" ] || { echo "no durable run found"; exit 1; }
say "run        : $RUN_ID"
say "pinned SHA : $CODE_SHA"
say "durable    : $DATES date(s) already safe on the remote"

# Idempotence / single-owner gate. If this exact run is already generating in
# this container, recovery is a no-op. Do not spawn a second process and then
# mistake the original PID for the child we just launched.
EXISTING_PID="$(pgrep -f "backtest/canonical_run.py.*--run-id[ =]$RUN_ID" | head -1 || true)"
if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
  say "already running: pid $EXISTING_PID -- recovery is a no-op"
  exit 0
fi

if ! git cat-file -e "${CODE_SHA}^{commit}" 2>/dev/null; then
  say "pinned SHA not present locally; fetching"
  git fetch -q origin "$CODE_SHA" 2>/dev/null || git fetch -q --all 2>/dev/null
fi
git cat-file -e "${CODE_SHA}^{commit}" 2>/dev/null || {
  echo "FATAL: pinned SHA $CODE_SHA is unreachable. Resuming at another SHA"
  echo "       would silently mix generation regimes, so this script stops."
  exit 1
}

WT="${FC_RESUME_WORKTREE:-/home/user/fullcount-canonical-run}"
if [ -d "$WT" ]; then
  CUR="$(git -C "$WT" rev-parse HEAD 2>/dev/null || echo none)"
  [ "$CUR" != "$CODE_SHA" ] && WT="${WT}-$(echo "$CODE_SHA" | cut -c1-8)"
fi
if [ ! -d "$WT" ]; then
  # Clear registrations whose directory no longer exists. After a container
  # death or a failed attempt, git can still hold a worktree entry for a path
  # that is gone, and `worktree add` then refuses with "missing but already
  # registered". Pruning is safe: it only drops entries with no directory.
  git worktree prune >/dev/null 2>&1
  say "creating pinned detached worktree at $WT"
  if ! git worktree add --detach "$WT" "$CODE_SHA" >/tmp/fc_wt_add.log 2>&1; then
    echo "FATAL: could not create worktree at $WT"
    sed 's/^/       /' /tmp/fc_wt_add.log
    exit 1
  fi
fi

if [ -n "${FC_RESUME_DRY_RUN:-}" ]; then
  say "DRY RUN -- would resume $RUN_ID at $CODE_SHA in $WT"
  exit 0
fi

RUN_DIR="$WT/backtest/canonical_runs/$RUN_ID"
say "restoring durable checkpoints into $RUN_DIR (from THIS checkout, not the pinned one)"
python3 -c "
import sys, os; sys.path.insert(0,'.')
import backtest.canonical_durability as cd
run_dir = '$RUN_DIR'
os.makedirs(os.path.join(run_dir,'checkpoints'), exist_ok=True)
rep = cd.restore_from_durable(run_dir, '$RUN_ID')
print('  restored %d, already present %d, failed %d' % (
    len(rep['restored']), len(rep['skipped_present']), len(rep['failed'])))
for f in rep['failed']:
    print('  FAILED', f)
sys.exit(1 if rep['failed'] else 0)
" || { echo "restore failed -- refusing to generate on top of an unverified base"; exit 1; }

cd "$WT" || exit 1
mkdir -p backtest/canonical_runs/logs
LOG="backtest/canonical_runs/logs/resume-$(date -u +%Y%m%dT%H%M%SZ).log"

# Only pass flags the PINNED code understands. An older SHA still generates
# correct rows; it just cannot push them durably itself, so we push for it.
HELP="$(python3 backtest/canonical_run.py --help 2>&1 || true)"
EXTRA=""
PINNED_CAN_PUSH=0
case "$HELP" in
  *--durable-every-dates*) EXTRA="--durable-every-dates 10 --durable-every-seconds 900"; PINNED_CAN_PUSH=1 ;;
esac
case "$HELP" in *--cache-mode*) EXTRA="$EXTRA --cache-mode frozen_cache" ;; esac
[ "$PINNED_CAN_PUSH" -eq 0 ] && say "NOTE: pinned SHA predates durable push; this script pushes after the invocation"

say "generating at the pinned SHA; log -> $WT/$LOG"
nohup python3 -u backtest/canonical_run.py \
    --start "$START" --end "$END" --run-id "$RUN_ID" \
    --no-weather --sleep 1.0 $EXTRA > "$LOG" 2>&1 &
BGPID=$!
sleep 6

# Verify the process WE launched, not any process that happens to match the run
# id. The old generic pgrep could report an already-existing owner as "resumed"
# even when the new child failed immediately on the run lock.
if kill -0 "$BGPID" 2>/dev/null; then
  CMDLINE="$(tr '\000' ' ' < "/proc/$BGPID/cmdline" 2>/dev/null || true)"
  case "$CMDLINE" in
    *"backtest/canonical_run.py"*"--run-id $RUN_ID"*)
      say "resumed: pid $BGPID starttime $(awk '{print $22}' "/proc/$BGPID/stat" 2>/dev/null) boot_id $(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
      exit 0
      ;;
    *)
      say "FAILED: launched pid $BGPID does not match the intended canonical command"
      kill "$BGPID" 2>/dev/null || true
      exit 1
      ;;
  esac
fi

wait "$BGPID" 2>/dev/null
RC=$?
if [ "$RC" -eq 0 ]; then
  say "invocation completed immediately (nothing left to generate)"
  tail -4 "$LOG"
  if [ "$PINNED_CAN_PUSH" -eq 0 ]; then
    say "pushing durably on the pinned run's behalf"
    ( cd "$REPO" && python3 -c "
import sys; sys.path.insert(0,'.')
import backtest.canonical_run as cr, backtest.canonical_durability as cd
mf = cr.load_manifest('$RUN_DIR')
print(' ', cd.push_durable_checkpoint('$RUN_DIR', mf, environment=cd.environment_identity()))
" )
  fi
  exit 0
fi

say "FAILED (rc=$RC) -- see $WT/$LOG"
tail -20 "$LOG"
exit 1
