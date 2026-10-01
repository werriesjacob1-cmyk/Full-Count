#!/bin/bash
# Rebuild the WEEK-4 Tier 1 seal environment on a fresh (or intact) container.
#   git clone --depth 1 --branch claude/nfl-seal-inputs-2026w04 https://github.com/werriesjacob1-cmyk/Full-Count /tmp/claude-0/seal_inputs4
#   /tmp/claude-0/seal_inputs4/w04/recover_w04.sh
# Fails closed on any hash mismatch. Prints "W04 ENVIRONMENT OK" only on success.
set -eu
URL=https://github.com/werriesjacob1-cmyk/Full-Count
W=/tmp/claude-0/full-count-worktrees
S=/tmp/claude-0/nfl_tier1_shared
HERE=$(cd "$(dirname "$0")" && pwd)
(cd "$HERE" && sha256sum -c SHA256SUMS)

# 0. Python packages pinned in MANIFEST.json (a fresh container lacks numpy/pandas;
#    found by the 2026-10-01 fresh-container drill, branch claude/nfl-seal-drill-2026w04).
python3 -c "import numpy, pandas, requests" 2>/dev/null || pip install -q numpy==2.4.6 pandas==3.0.5 requests==2.34.2

# 1. players.csv: upstream drifted after 2026-09-25 (4dd70f32... pinned, 5b6f22c2... served
#    on 2026-10-01). Place the pinned copy first so the week-3 restore accepts it.
mkdir -p /tmp/claude-0/nfl_tier1_B
cp "$HERE/players.csv" /tmp/claude-0/nfl_tier1_B/players.csv

# 2. Week-3 recovery (everything else: historical nflverse files, bundle, caches, tier1/cov clones).
"$HERE/../recover_environment.sh"

# 3. Overlay the week-4 current-season pins.
mkdir -p "$S/pbp" "$S/snap_counts"
cp "$HERE/stats_player_week_2026.csv" "$S/stats_player_week_2026.csv"
cp "$HERE/play_by_play_2026.csv.gz" "$S/pbp/play_by_play_2026.csv.gz"
cp "$HERE/snap_counts_2026.csv" "$S/snap_counts/snap_counts_2026.csv"
check () { [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ] || { echo "MISMATCH $1"; exit 5; }; }
check "$S/stats_player_week_2026.csv" e293e213908f746db982cd9112f5016125a42eeeec16c4773b35c6cc5edf6327
check "$S/pbp/play_by_play_2026.csv.gz" 321433f8c3cab61e7577f49dc2f22f8216de8a5eb84baa432f1cf3d60d0b705a
check "$S/snap_counts/snap_counts_2026.csv" c5868527b1052ae572b2d8f34a7bb777675a5e762ceaeb25f72f10d1c59846d5
check "$S/schedules/games.csv" 7fdc123e11cf224b97d120980a4cd18171147f6db6d4c3b193f27406b4856c4e
check /tmp/claude-0/nfl_tier1_B/players.csv 4dd70f328f31b0bb7cbf043412298d5a325863e27b8f2eeea22c9e925c808dee

# 4. Week-4 builder worktree (branch tip; must contain builder commit with PINNED_BY_WEEK[4]).
if [ ! -d $W/tier1w4/.git ] && [ ! -f $W/tier1w4/.git ]; then
  git clone -q --filter=blob:none --no-checkout --branch claude/nfl-tier1-seal-2026w04 $URL $W/tier1w4
  git -C $W/tier1w4 sparse-checkout set --no-cone /nfl/ /engineering/nfl_tier1_status_20260924/ /engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json
  git -C $W/tier1w4 checkout -q claude/nfl-tier1-seal-2026w04
fi
grep -q 'PINNED_BY_WEEK' $W/tier1w4/engineering/nfl_tier1_status_20260924/seal/build_week_seal.py || { echo "tier1w4 builder lacks per-week pins"; exit 6; }
[ -z "$(git -C $W/tier1w4 status --porcelain)" ] || { echo "tier1w4 not clean"; exit 4; }
echo "tier1w4 OK at $(git -C $W/tier1w4 rev-parse --short HEAD)"
echo "W04 ENVIRONMENT OK"
