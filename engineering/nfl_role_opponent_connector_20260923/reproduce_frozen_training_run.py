#!/usr/bin/env python3
"""One-off, real-data training run to produce a FROZEN committee model
snapshot for target_share, using the exact predeclared 2012-2021 train
window role_regime_redistribution.py already documents. Output is a JSON
file with the trained weights + full provenance, to be embedded as a
frozen constant (mirroring FROZEN_NB_FIT's pattern in
receptions_frozen_challenger.py).
"""
import hashlib
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/user/Full-Count")

from nfl.research.role_intelligence_data_prep import (
    fetch_players_crosswalk, fetch_season_bundle, build_player_game_usage_rows,
)
from nfl.research.role_intelligence_features import (
    build_role_state_rows, build_teammate_absence_trigger_events,
    build_replacement_candidate_rows,
)
from nfl.research.role_regime_redistribution import (
    build_hc_registry, attach_hc_regime_to_events, train_committee_model,
    CHALLENGER_TRAIN_SEASONS, CHALLENGER_HELD_OUT_SEASONS,
    evaluate_challenger_vs_baselines,
)
from nfl.research.coach_regime_registry import HC_GAMES_SOURCE

t0 = time.time()

print("fetching players crosswalk...", flush=True)
crosswalk = fetch_players_crosswalk()
print(f"crosswalk: {len(crosswalk)} entries, {time.time()-t0:.1f}s", flush=True)

print("fetching HC games.csv...", flush=True)
req = urllib.request.Request(
    f"https://raw.githubusercontent.com/{HC_GAMES_SOURCE['repository'].split('github.com/')[1]}/"
    f"{HC_GAMES_SOURCE['commit']}/{HC_GAMES_SOURCE['path']}",
    headers={"User-Agent": "full-count-role-intelligence-research/1.0"},
)
games_csv_bytes = urllib.request.urlopen(req, timeout=60).read()
got = {"bytes": len(games_csv_bytes), "sha256": hashlib.sha256(games_csv_bytes).hexdigest()}
print(f"games.csv: {got}", flush=True)
assert got["bytes"] == HC_GAMES_SOURCE["bytes"], f"games.csv drift: {got}"
assert got["sha256"] == HC_GAMES_SOURCE["sha256"], f"games.csv drift: {got}"
intervals, game_date_index = build_hc_registry(games_csv_bytes)
print(f"HC registry built, {time.time()-t0:.1f}s total", flush=True)

TRAIN_SEASONS = sorted(CHALLENGER_TRAIN_SEASONS)
HELD_OUT_SEASONS = sorted(CHALLENGER_HELD_OUT_SEASONS)
ALL_SEASONS = TRAIN_SEASONS + HELD_OUT_SEASONS

all_usage_rows = []
all_injury_rows = []
for season in ALL_SEASONS:
    ts = time.time()
    bundle = fetch_season_bundle(season, crosswalk)
    usage_rows = build_player_game_usage_rows(
        bundle["weekly_rows"], bundle["snap_rows"], bundle["depth_rows"],
        bundle["injury_rows"], bundle["pbp_player_rows"], bundle["pbp_team_totals"],
    )
    all_usage_rows.extend(usage_rows)
    all_injury_rows.extend(bundle["injury_rows"])
    print(f"season {season}: {len(usage_rows)} WR/RB usage rows, {time.time()-ts:.1f}s", flush=True)

print(f"total usage rows: {len(all_usage_rows)}, {time.time()-t0:.1f}s total", flush=True)

role_state_rows = build_role_state_rows(all_usage_rows)
print(f"role_state_rows: {len(role_state_rows)}", flush=True)

events = build_teammate_absence_trigger_events(all_usage_rows, all_injury_rows)
print(f"trigger events: {len(events)}", flush=True)

events_with_regime = attach_hc_regime_to_events(events, intervals, game_date_index)

candidates = build_replacement_candidate_rows(all_usage_rows, events)
print(f"candidate rows: {len(candidates)}", flush=True)

model = train_committee_model(
    events_with_regime, candidates, all_usage_rows, "target_share",
    train_seasons=CHALLENGER_TRAIN_SEASONS,
)
print(f"trained model: n_training_examples={model['n_training_examples']}, "
      f"by_bucket={model['n_training_examples_by_bucket']}", flush=True)

held_out_report = evaluate_challenger_vs_baselines(
    events_with_regime, candidates, role_state_rows, all_usage_rows, "target_share",
    model, held_out_seasons=CHALLENGER_HELD_OUT_SEASONS,
)
print("held-out comparison:", flush=True)
for name, result in held_out_report.items():
    print(f"  {name}: n={result['n']} mae={result['mae']}", flush=True)

out = {
    "model": model,
    "held_out_report": {
        name: {"n": r["n"], "mae": r["mae"]} for name, r in held_out_report.items()
    },
    "n_events_total": len(events),
    "n_events_with_hc_resolved": sum(1 for e in events_with_regime if e["hc_status"] == "RESOLVED"),
    "n_usage_rows": len(all_usage_rows),
    "n_candidates": len(candidates),
    "train_seasons": TRAIN_SEASONS,
    "held_out_seasons": HELD_OUT_SEASONS,
    "players_crosswalk_digest": {
        "bytes": len(open("/tmp/players_live_check.csv", "rb").read()),
    },
    "games_csv_digest": got,
    "elapsed_seconds": time.time() - t0,
}
with open("/tmp/claude-0/-home-user-Full-Count/46a4218b-3a66-52d9-8838-d59c25a73d68/scratchpad/frozen_committee_target_share.json", "w") as f:
    json.dump(out, f, indent=2, sort_keys=True)
print(f"DONE in {time.time()-t0:.1f}s, wrote frozen_committee_target_share.json", flush=True)
