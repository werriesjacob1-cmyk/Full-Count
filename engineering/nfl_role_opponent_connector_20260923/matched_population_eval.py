#!/usr/bin/env python3
"""Workstream B1: reconstruct the committee-vs-baselines comparison on the
IDENTICAL matched (event, player) population for every predictor, closing
the real gap PR #176's independent review found (committee n=449 vs
baselines n=441 -- not a matched-volume comparison).

Root cause (confirmed by reading the code): predict_committee_model starts
from predict_no_adjustment's own dict, then ADDS an absorption term for
every teammate the model has real learned features for -- including
teammates predict_no_adjustment itself excluded (no own prior share to
report). This means the committee model's own predicted-player population
is a STRICT SUPERSET of every baseline's population, not a different
population -- so a fair matched comparison should restrict ALL FIVE
predictors to exactly the players every one of them actually predicted.
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from nfl.research.role_intelligence_data_prep import (
    fetch_players_crosswalk, fetch_season_bundle, build_player_game_usage_rows,
)
from nfl.research.role_intelligence_features import (
    build_role_state_rows, build_teammate_absence_trigger_events,
    build_replacement_candidate_rows, build_player_dimension_history,
)
from nfl.research.role_intelligence_baselines import BASELINE_PREDICTORS
from nfl.research.role_regime_redistribution import (
    build_hc_registry, attach_hc_regime_to_events, train_committee_model,
    predict_committee_model, CHALLENGER_TRAIN_SEASONS, CHALLENGER_HELD_OUT_SEASONS,
    CHALLENGER_NAME,
)
from nfl.research.coach_regime_registry import HC_GAMES_SOURCE

t0 = time.time()
crosswalk = fetch_players_crosswalk()

req = urllib.request.Request(
    f"https://raw.githubusercontent.com/{HC_GAMES_SOURCE['repository'].split('github.com/')[1]}/"
    f"{HC_GAMES_SOURCE['commit']}/{HC_GAMES_SOURCE['path']}",
    headers={"User-Agent": "full-count-role-intelligence-research/1.0"},
)
games_csv_bytes = urllib.request.urlopen(req, timeout=60).read()
intervals, game_date_index = build_hc_registry(games_csv_bytes)

TRAIN_SEASONS = sorted(CHALLENGER_TRAIN_SEASONS)
HELD_OUT_SEASONS = sorted(CHALLENGER_HELD_OUT_SEASONS)
ALL_SEASONS = TRAIN_SEASONS + HELD_OUT_SEASONS

all_usage_rows, all_injury_rows = [], []
for season in ALL_SEASONS:
    bundle = fetch_season_bundle(season, crosswalk)
    usage_rows = build_player_game_usage_rows(
        bundle["weekly_rows"], bundle["snap_rows"], bundle["depth_rows"],
        bundle["injury_rows"], bundle["pbp_player_rows"], bundle["pbp_team_totals"],
    )
    all_usage_rows.extend(usage_rows)
    all_injury_rows.extend(bundle["injury_rows"])
print(f"usage rows: {len(all_usage_rows)}, {time.time()-t0:.1f}s", flush=True)

role_state_rows = build_role_state_rows(all_usage_rows)
events = build_teammate_absence_trigger_events(all_usage_rows, all_injury_rows)
events_with_regime = attach_hc_regime_to_events(events, intervals, game_date_index)
candidates = build_replacement_candidate_rows(all_usage_rows, events)
history = build_player_dimension_history(role_state_rows)

model = train_committee_model(
    events_with_regime, candidates, all_usage_rows, "target_share",
    train_seasons=CHALLENGER_TRAIN_SEASONS,
)
print(f"trained: n={model['n_training_examples']}", flush=True)

DIMENSION = "target_share"
RELEVANT_EVENT_TYPES = {"WR_ABSENCE"}  # target_share relevant events per role_intelligence_baselines

usage_index = {(r["season"], r["week"], r["team"], r["player_id"]): r for r in all_usage_rows}


def realized_share(season, week, team, player_id):
    row = usage_index.get((season, week, team, player_id))
    if row is None:
        return None
    from nfl.research.role_intelligence_features import compute_dimension_shares
    value = compute_dimension_shares(row)[DIMENSION]
    return value if isinstance(value, (int, float)) else None


candidates_by_event = {}
for c in candidates:
    key = (c["season"], c["week"], c["team"], c["removed_player_id"])
    candidates_by_event.setdefault(key, []).append(c)

held_out_events = [e for e in events_with_regime if e["season"] in CHALLENGER_HELD_OUT_SEASONS and e["event_type"] in RELEVANT_EVENT_TYPES]

predictors = dict(BASELINE_PREDICTORS)
predictors[CHALLENGER_NAME] = lambda event, teammates, hist, dim: predict_committee_model(event, teammates, hist, dim, model)

# Step 1: compute each predictor's raw predicted-player population per event.
predictions_by_predictor_event = {name: {} for name in predictors}
for event in held_out_events:
    key = (event["season"], event["week"], event["team"], event["removed_player_id"])
    teammates = candidates_by_event.get(key, [])
    if not teammates:
        continue
    for name, fn in predictors.items():
        predictions_by_predictor_event[name][key] = fn(event, teammates, history, DIMENSION)

# Step 2: matched population = for each event, only players ALL predictors predicted.
matched_errors = {name: [] for name in predictors}
raw_errors = {name: [] for name in predictors}
matched_event_player_count = 0
for event in held_out_events:
    key = (event["season"], event["week"], event["team"], event["removed_player_id"])
    per_predictor_players = [set(predictions_by_predictor_event[name].get(key, {}).keys()) for name in predictors]
    if not all(per_predictor_players):
        continue
    matched_players = set.intersection(*per_predictor_players)
    for name in predictors:
        preds = predictions_by_predictor_event[name][key]
        for player_id, predicted in preds.items():
            realized = realized_share(event["season"], event["week"], event["team"], player_id)
            if realized is None:
                continue
            err = abs(predicted - realized)
            raw_errors[name].append(err)
            if player_id in matched_players:
                matched_errors[name].append(err)
    matched_event_player_count += len(matched_players)

report = {"raw": {}, "matched": {}}
for name in predictors:
    raw = raw_errors[name]
    matched = matched_errors[name]
    report["raw"][name] = {"n": len(raw), "mae": sum(raw) / len(raw) if raw else None}
    report["matched"][name] = {"n": len(matched), "mae": sum(matched) / len(matched) if matched else None}

print(json.dumps(report, indent=2, sort_keys=True))
out_path = __import__("pathlib").Path(__file__).resolve().parent / "matched_population_report.json"
with open(out_path, "w") as f:
    json.dump(report, f, indent=2, sort_keys=True)
print(f"DONE in {time.time()-t0:.1f}s", flush=True)
