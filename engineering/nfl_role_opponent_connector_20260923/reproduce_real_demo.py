#!/usr/bin/env python3
"""Real end-to-end demonstration: pick one genuine held-out-season WR
absence event, from real nflverse data, and show REAL SOURCE -> FEATURE ->
OPPORTUNITY DELTA -> FROZEN PREDICTION for one real teammate candidate,
using the exact same code path build_role_adjusted_challenger_record uses
in production (receptions_shadow.current_b0_projection for the B0 side,
FROZEN_COMMITTEE_MODEL for the role-adjustment side)."""
import csv
import io
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/user/Full-Count")

from nfl.research.nflverse_history import player_stats_url
from nfl.research.role_intelligence_data_prep import fetch_players_crosswalk, fetch_season_bundle, build_player_game_usage_rows
from nfl.research.role_intelligence_features import (
    build_role_state_rows, build_teammate_absence_trigger_events,
    build_replacement_candidate_rows, build_player_dimension_history,
    most_recent_prior_share,
)
from nfl.research.receptions_shadow import current_b0_projection
from nfl.research.receptions_role_adjusted_challenger import (
    build_role_adjusted_challenger_record, FROZEN_COMMITTEE_MODEL,
)

t0 = time.time()
crosswalk = fetch_players_crosswalk()

HELD_OUT_SEASONS = (2022, 2023, 2024, 2025)
all_usage_rows = []
all_injury_rows = []
for season in HELD_OUT_SEASONS:
    bundle = fetch_season_bundle(season, crosswalk)
    usage_rows = build_player_game_usage_rows(
        bundle["weekly_rows"], bundle["snap_rows"], bundle["depth_rows"],
        bundle["injury_rows"], bundle["pbp_player_rows"], bundle["pbp_team_totals"],
    )
    all_usage_rows.extend(usage_rows)
    all_injury_rows.extend(bundle["injury_rows"])
print(f"fetched {len(all_usage_rows)} held-out usage rows, {time.time()-t0:.1f}s", flush=True)

# role_intelligence_data_prep's weekly-stats parse deliberately drops
# `receptions` (only targets/carries feed role-share denominators) --
# fetch the real receptions column directly from the same real CSV for
# this demonstration's B0 input.
receptions_by_key = {}
for season in HELD_OUT_SEASONS:
    data = urllib.request.urlopen(player_stats_url(season), timeout=120).read()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    for row in reader:
        if (row.get("season_type") or "").strip().upper() != "REG":
            continue
        pid = (row.get("player_id") or "").strip()
        if not pid:
            continue
        try:
            week = int(row["week"])
            receptions = float(row.get("receptions") or 0)
        except (TypeError, ValueError):
            continue
        receptions_by_key[(season, week, pid)] = receptions
print(f"fetched real receptions column, {len(receptions_by_key)} rows, {time.time()-t0:.1f}s", flush=True)

role_state_rows = build_role_state_rows(all_usage_rows)
events = build_teammate_absence_trigger_events(all_usage_rows, all_injury_rows)
wr_events = [e for e in events if e["event_type"] == "WR_ABSENCE"]
candidates = build_replacement_candidate_rows(all_usage_rows, events)
history = build_player_dimension_history(role_state_rows)

usage_by_key = {(r["season"], r["week"], r["team"], r["player_id"]): r for r in all_usage_rows}

# Pick the first WR_ABSENCE event that has >=2 real teammate candidates and
# a real teammate with >=3 prior appearances (so current_b0_projection can
# actually run on his real receptions history) and a real prior target share.
chosen = None
for event in wr_events:
    teammates = [c for c in candidates if c["event_type"] == event["event_type"]
                 and c["season"] == event["season"] and c["week"] == event["week"]
                 and c["team"] == event["team"] and c["removed_player_id"] == event["removed_player_id"]]
    for teammate in teammates:
        pid = teammate["candidate_player_id"]
        prior_games = [g for g in all_usage_rows if g["player_id"] == pid
                       and (g["season"], g["week"]) < (event["season"], event["week"])]
        prior_games.sort(key=lambda g: (g["season"], g["week"]))
        prior_games = prior_games[-5:]
        has_real_receptions = all(
            (g["season"], g["week"], pid) in receptions_by_key for g in prior_games
        )
        if len(prior_games) >= 3 and teammate["candidate_prior_target_share_mean_last5"] and has_real_receptions:
            chosen = (event, teammates, teammate, prior_games)
            break
    if chosen:
        break

assert chosen, "no qualifying real demonstration event found"
event, teammates, teammate, prior_games = chosen
pid = teammate["candidate_player_id"]
player_name = usage_by_key.get((prior_games[-1]["season"], prior_games[-1]["week"], event["team"], pid), {}).get("player_display_name", pid)

prior_games_with_receptions = [
    {**g, "receptions": receptions_by_key[(g["season"], g["week"], pid)]} for g in prior_games
]
b0 = current_b0_projection(prior_games_with_receptions)

record = build_role_adjusted_challenger_record(
    b0_projection=b0["projection"], line=b0["projection"] - 0.5,
    over_odds=-115, under_odds=-105, residuals=[0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5,
    event=event, teammates=teammates, history=history,
    candidate_player_id=pid,
    candidate_own_prior_target_share=teammate["candidate_prior_target_share_mean_last5"],
)

removed_name = None
for g in all_usage_rows:
    if g["player_id"] == event["removed_player_id"] and g["team"] == event["team"]:
        removed_name = g["player_display_name"]
        break

demo = {
    "real_event": {
        "season": event["season"], "week": event["week"], "team": event["team"],
        "event_type": event["event_type"],
        "removed_player_id": event["removed_player_id"],
        "removed_player_name": removed_name,
        "pregame_injury_status": event["pregame_injury_status"],
        "trigger_source": event["trigger_source"],
    },
    "candidate": {
        "player_id": pid,
        "player_name": player_name,
        "prior_games_used_for_b0": [
            {"season": g["season"], "week": g["week"], "receptions": g["receptions"], "targets": g["targets"]}
            for g in prior_games_with_receptions
        ],
    },
    "b0_projection": b0,
    "role_adjusted_record": record,
}
with open("/tmp/claude-0/-home-user-Full-Count/46a4218b-3a66-52d9-8838-d59c25a73d68/scratchpad/real_demo_output.json", "w") as f:
    json.dump(demo, f, indent=2, sort_keys=True, default=str)

print(json.dumps(demo, indent=2, sort_keys=True, default=str))
print(f"\nDONE in {time.time()-t0:.1f}s", flush=True)
