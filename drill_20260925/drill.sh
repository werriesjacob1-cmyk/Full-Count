#!/bin/bash
# Recovery drill: runs inside a private mount namespace where /tmp/claude-0 is an EMPTY directory.
set -u
exec > /var/tmp/drill_out/drill.log 2>&1
URL=https://github.com/werriesjacob1-cmyk/Full-Count
mount --bind /var/tmp/drill_root /tmp/claude-0 || exit 90
echo "== isolation: contents of /tmp/claude-0 before restore:"; ls -A /tmp/claude-0; echo "(end)"
date -u +"start %FT%TZ"
W=/tmp/claude-0/full-count-worktrees
mkdir -p $W
# 1. recovery artifact from the remote only
git clone -q --depth 1 --branch claude/nfl-seal-inputs-20260925 $URL /tmp/claude-0/seal_inputs || exit 91
git -C /tmp/claude-0/seal_inputs log --oneline -1
(cd /tmp/claude-0/seal_inputs && sha256sum -c SHA256SUMS) || exit 92
python3 /tmp/claude-0/seal_inputs/restore.py --root / --report /var/tmp/drill_out/restore_report.json; RC=$?
echo "restore rc=$RC"; [ $RC -eq 0 ] || exit 93
# 2. fresh checkouts at the exact frozen SHAs (partial clone, sparse)
for spec in "tier1 e05e02c592 /nfl/ /engineering/nfl_tier1_status_20260924/ /engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json" \
            "cov f4fb17aa0b /nfl/ /engineering/nfl_tier2_coverage_20260925/ /engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"; do
  set -- $spec; name=$1; sha=$2; shift 2
  git clone -q --filter=blob:none --no-checkout $URL $W/$name || exit 94
  git -C $W/$name sparse-checkout set --no-cone "$@"
  git -C $W/$name checkout -q --detach $sha || exit 95
  echo "$name at $(git -C $W/$name rev-parse --short HEAD)"
done
# 3. negative tests: builder must refuse a missing or mismatched pinned input
S=/tmp/claude-0/nfl_tier1_shared/stats_player_week_2026.csv
cd $W/tier1
mv $S $S.hold
PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py --week 3 --label NEG_MISSING --exclude 2026_03_ATL_GB --no-capture --dry-run > /var/tmp/drill_out/neg_missing.log 2>&1; echo "neg missing rc=$?"
curl -sSfL -o $S https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv; echo "current upstream stats sha $(sha256sum $S | cut -c1-12)"
PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py --week 3 --label NEG_UPSTREAM --exclude 2026_03_ATL_GB --no-capture --dry-run > /var/tmp/drill_out/neg_upstream.log 2>&1; echo "neg upstream rc=$?"
rm -f $S; mv $S.hold $S; echo "restored stats sha $(sha256sum $S | cut -c1-12)"
# 4. the Saturday entry point, same flags as the trigger, but --dry-run (REHEARSAL label, output outside the repo)
PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py --week 3 --label sun_mon_REHEARSAL --exclude 2026_03_ATL_GB --refresh-injuries --dry-run; echo "builder rc=$?"
D=/tmp/claude-0/seal_dry/2026_w03_sun_mon_REHEARSAL
G=$(python3 -c "import json;print(','.join(json.load(open('$D/seal.json'))['games']))")
cd $W/cov && PYTHONPATH=.:engineering/nfl_tier2_coverage_20260925 python3 engineering/nfl_tier2_coverage_20260925/live_coverage.py --season 2026 --week 3 --games $G --out $D/exploratory/coverage_exploratory.json; echo "coverage rc=$?"
(cd $D/exploratory && sha256sum coverage_exploratory.json > SHA256SUMS)
cp -r $D /var/tmp/drill_out/
date -u +"end %FT%TZ"
