#!/bin/bash
# Rebuild the seal environment on a fresh container (the Saturday trigger's step 0).
# Safe on an intact container too: restore.py keeps matching files and only verifies them.
set -eu
URL=https://github.com/werriesjacob1-cmyk/Full-Count
W=/tmp/claude-0/full-count-worktrees
HERE=$(cd "$(dirname "$0")" && pwd)
(cd "$HERE" && sha256sum -c SHA256SUMS)
python3 "$HERE/restore.py" --root / --report /tmp/claude-0/restore_report.json
mkdir -p $W
clone () {  # name sha branch paths...
  local name=$1 sha=$2 branch=$3; shift 3
  if [ ! -d $W/$name/.git ] && [ ! -f $W/$name/.git ]; then
    git clone -q --filter=blob:none --no-checkout $URL $W/$name
    git -C $W/$name sparse-checkout set --no-cone "$@"
    git -C $W/$name checkout -q -B "$branch" "$sha"
  fi
  [ "$(git -C $W/$name rev-parse HEAD)" = "$(git -C $W/$name rev-parse "$sha^{commit}")" ] || { echo "$name not at $sha"; exit 3; }
  [ -z "$(git -C $W/$name status --porcelain)" ] || { echo "$name not clean"; exit 4; }
  echo "$name OK at $sha"
}
clone tier1 e05e02c592 claude/nfl-tier1-seal-2026w03 /nfl/ /engineering/nfl_tier1_status_20260924/ /engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json
clone cov f4fb17aa0b claude/nfl-tier2-coverage-20260925 /nfl/ /engineering/nfl_tier2_coverage_20260925/ /engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json
