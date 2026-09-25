"""Parity diagnostic: WS-C neutral dropback rate vs pbp_prior_tendencies (reuse check)."""
import json
from pathlib import Path

import pandas as pd

from nfl.research.pbp_prior_tendencies import build_prior_pbp_tendencies
from nfl.research.tier1.team_context_data import TEAM_GAME_FIELDS, read_csv
from nfl.research.tier1.team_context_features import team_prior_features

cols = ["game_id", "play_id", "season", "week", "season_type", "posteam", "defteam", "down",
        "half_seconds_remaining", "wp", "qb_dropback", "rush_attempt", "qb_kneel", "qb_spike",
        "two_point_attempt"]
d = pd.read_csv("/tmp/claude-0/nfl_tier1_shared/pbp/play_by_play_2025.csv.gz", usecols=cols, low_memory=False)
d = d[d.season_type == "REG"]
for c in ["qb_dropback", "rush_attempt", "qb_kneel", "qb_spike", "two_point_attempt"]:
    d[c] = d[c].fillna(0).astype(int)
sc = d[(d.qb_dropback == 1) | (d.rush_attempt == 1)]
info = {"scrimmage_rows": len(sc), "down_na": int(sc.down.isna().sum()),
        "two_point": int(sc.two_point_attempt.sum()), "wp_na": int(sc.wp.isna().sum())}
src = d[~(((d.qb_dropback == 1) | (d.rush_attempt == 1)) & (d.two_point_attempt == 1))]
try:
    out = build_prior_pbp_tendencies(src.to_dict("records"), rolling_window=10)
except Exception as exc:  # record, do not hide
    info["reuse_failed"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(info))
    raise SystemExit
cache = Path("/tmp/claude-0/nfl_tier1_c/cache")
team = []
for s in (2024, 2025):
    team += read_csv(next(cache.glob(f"team_{s}_*.csv")), TEAM_GAME_FIELDS)
tgt = [(o["game_id"], o["team"], o["season"], o["week"]) for o in out if o["week"] >= 12]
mine = team_prior_features(team, tgt)
diffs = []
for o in out:
    if o["week"] < 12 or o["prior_games_n"] != 10:
        continue
    m = mine[(o["game_id"], o["team"])]
    diffs.append(abs(m["f5_neutral_dropback_rate"] - o["prior_neutral_dropback_rate_v1"]))
info.update({"compared": len(diffs), "max_abs_diff": max(diffs), "mean_abs_diff": sum(diffs) / len(diffs)})
print(json.dumps(info))
