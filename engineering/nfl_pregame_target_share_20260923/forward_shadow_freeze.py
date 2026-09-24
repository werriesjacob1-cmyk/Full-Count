#!/usr/bin/env python3
"""FROZEN FORWARD SHADOW: 2026 week 3 pregame receptions predictions.

Research-only. Not a pick, not published, never fed to B0 or any live
selector. It freezes B0, the existing unadjusted chain, and the locked C1
challenger for every eligible WR/TE/RB on a week-3 team, using ONLY data
published before week 3's first kickoff (2026-09-25 00:15 UTC). The
artifact's SHA-256 and the commit that adds it are the tamper evidence.
Grading happens later, only after games are FINAL, in a separate script.

Source note (quantified, see forward_sources.validate_against_pbp): 2026
has no digest-pinned PBP, so team offense rows for BOTH 2025 and 2026 are
derived from real nflverse weekly player stats, which is one consistent
source. On 2025 this sits 2.54 dropbacks per team-game below pinned PBP.
B0 does not use it at all. C1 is nearly invariant to it, because dropbacks
appear in both its volume prediction and its targets-per-dropback
denominator. The unadjusted control is not invariant.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from data_cache import load_player_weeks  # noqa: E402
from forward_sources import load_schedule, player_derived_team_offense  # noqa: E402
from stats_lib import POISSON_LINES, poisson_prob_over  # noqa: E402

from nfl.research.defense_prior_features import build_prior_defense_features  # noqa: E402
from nfl.research.game_matchup_features import build_game_matchup_features  # noqa: E402
from nfl.research.pregame_target_share import (  # noqa: E402
    team_targets_per_dropback,
    unit_consistent_expected_receptions,
)
from nfl.research.receptions_shadow import current_b0_projection  # noqa: E402
from nfl.research.receptions_team_opportunity_challenger import (  # noqa: E402
    compute_opportunity_projection,
    estimate_current_week_catch_rate,
    estimate_current_week_target_share,
    predict_team_pass_dropbacks,
)
from nfl.research.team_prior_features import build_prior_team_features  # noqa: E402

TARGET_SEASON, TARGET_WEEK = 2026, 3
FIRST_KICKOFF_UTC = "2026-09-25T00:15:00Z"
POSITIONS = ("WR", "TE", "RB")

t0 = time.time()
frozen_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
if frozen_at >= FIRST_KICKOFF_UTC:
    raise SystemExit(f"refusing to freeze at {frozen_at}: at/after first week-3 kickoff {FIRST_KICKOFF_UTC}")

player_2025 = load_player_weeks(2025)
player_2026 = load_player_weeks(2026, refresh=True)
player_rows = [r for r in player_2025["rows"] + player_2026["rows"] if (r["season"], r["week"]) < (TARGET_SEASON, TARGET_WEEK)]
if any(r["season"] == TARGET_SEASON and r["week"] >= TARGET_WEEK for r in player_2026["rows"]):
    raise SystemExit("week-3 stats already present in the live file; this would not be a pregame freeze")

team_src = {s: player_derived_team_offense(s) for s in (2025, 2026)}
team_rows = [r for s in team_src.values() for r in s["rows"] if (r["season"], r["week"]) < (TARGET_SEASON, TARGET_WEEK)]
schedule = load_schedule(TARGET_SEASON)
week_games = [g for g in schedule["games"] if g["week"] == TARGET_WEEK]

team_targets = defaultdict(float)
for r in player_rows:
    team_targets[(r["season"], r["week"], r["team"])] += r["targets"]
team_games = defaultdict(list)
for r in team_rows:
    tt = team_targets.get((r["season"], r["week"], r["team"]))
    if tt:
        team_games[r["team"]].append((r["season"], r["week"], r["attempts"] + r["sacks_suffered"], tt))

# The matchup builder needs a feature row for each unplayed week-3 game, and
# the team/defense builders only emit one for a game that has a team row.
# Both builders build a game's features from history BEFORE appending that
# game's own row (team_prior_features.py `built.append(feature_row)` then
# `history.append(row)`; defense_prior_features.py the same), and no row
# after week 3 exists, so these placeholder values can never reach any
# feature. They are 1.0, not 0, because the builder divides by play volume.
# They are added ONLY to the feature-builder input, never to team_games,
# team_targets, or any other input.
placeholder_rows = []
for g in week_games:
    for team, opp in ((g["home_team"], g["away_team"]), (g["away_team"], g["home_team"])):
        placeholder_rows.append({"game_id": g["game_id"], "season": TARGET_SEASON, "week": TARGET_WEEK,
                                 "season_type": "REG", "team": team, "opponent_team": opp,
                                 **{f: 1.0 for f in ("attempts", "passing_yards", "sacks_suffered",
                                                     "passing_epa", "carries", "rushing_yards")}})
offense = build_prior_team_features(team_rows + placeholder_rows, rolling_window=5)
defense = build_prior_defense_features(team_rows + placeholder_rows, rolling_window=5)
assert not any(r["season"] == TARGET_SEASON and r["week"] >= TARGET_WEEK for r in team_rows)
past_schedule = {
    r["game_id"]: {"game_id": r["game_id"], "season": r["season"], "week": r["week"], "season_type": "REG",
                   "away_team": r["game_id"].split("_")[2], "home_team": r["game_id"].split("_")[3]}
    for r in team_rows
}
target_schedule = [{"game_id": g["game_id"], "season": TARGET_SEASON, "week": TARGET_WEEK, "season_type": "REG",
                    "away_team": g["away_team"], "home_team": g["home_team"]} for g in week_games]
matchup = {}
for row in build_game_matchup_features(list(past_schedule.values()) + target_schedule, offense, defense):
    if row["season"] == TARGET_SEASON and row["week"] == TARGET_WEEK:
        matchup[row["home_team"]] = (row, "home")
        matchup[row["away_team"]] = (row, "away")

history_by_player = defaultdict(list)
for r in player_rows:
    history_by_player[r["player_id"]].append(r)
game_by_team = {}
for g in week_games:
    game_by_team[g["home_team"]] = g
    game_by_team[g["away_team"]] = g

candidates, abstain = [], defaultdict(int)
for pid, hist in history_by_player.items():
    hist.sort(key=lambda r: (r["season"], r["week"]))
    last = hist[-1]
    if last["season"] != TARGET_SEASON or last["position"] not in POSITIONS:
        continue
    team = last["team"]
    game = game_by_team.get(team)
    if game is None:
        abstain["TEAM_NOT_PLAYING_WEEK_3"] += 1
        continue
    try:
        b0 = current_b0_projection(hist)["projection"]
    except ValueError:
        abstain["B0_INSUFFICIENT_HISTORY"] += 1
        continue
    if team not in matchup:
        abstain["NO_MATCHUP_ROW"] += 1
        continue
    mrow, side = matchup[team]
    share_history = [(h["season"], h["week"], h["targets"] / team_targets[(h["season"], h["week"], h["team"])])
                     for h in hist if team_targets.get((h["season"], h["week"], h["team"]), 0) > 0]
    share = estimate_current_week_target_share(player_id=pid, target_share_history=share_history,
                                               target_season=TARGET_SEASON, target_week=TARGET_WEEK)
    catch = estimate_current_week_catch_rate(player_id=pid, game_log=hist,
                                             target_season=TARGET_SEASON, target_week=TARGET_WEEK)
    volume = predict_team_pass_dropbacks(mrow, side=side)
    unadjusted = compute_opportunity_projection(predicted_team_dropbacks=volume["predicted_dropbacks"],
                                                target_share=share["estimate"], catch_rate=catch["estimate"])
    tpd = team_targets_per_dropback(team_games.get(team, []), target_season=TARGET_SEASON, target_week=TARGET_WEEK)
    c1 = unit_consistent_expected_receptions(predicted_team_dropbacks=volume["predicted_dropbacks"],
                                             targets_per_dropback=tpd["ratio"], target_share=share["estimate"],
                                             catch_rate=catch["estimate"])
    if c1["projection"] is None or unadjusted["projection"] is None:
        abstain["CHAIN_" + str(c1["reason"] or unadjusted["reason"])] += 1
        continue
    candidates.append({
        "player_id": pid, "player_name": last["player_name"], "position": last["position"], "team": team,
        "game_id": game["game_id"], "gameday_local": game["gameday"], "gametime_local_et": game["gametime"],
        "prior_appearances_used": len(hist),
        "b0_projection": round(b0, 6),
        "existing_unadjusted_projection": round(unadjusted["projection"], 6),
        "c1_projection": round(c1["projection"], 6),
        "c1_expected_targets": round(c1["expected_targets"], 6),
        "inputs": {
            "predicted_team_dropbacks": volume["predicted_dropbacks"], "volume_basis": volume["basis"],
            "targets_per_dropback": tpd["ratio"], "targets_per_dropback_games_used": tpd["games_used"],
            "target_share": share["estimate"], "target_share_basis": share["basis"],
            "catch_rate": catch["estimate"],
        },
        "poisson_prob_over": {
            "b0": {str(L): round(poisson_prob_over(L, b0), 6) for L in POISSON_LINES},
            "c1": {str(L): round(poisson_prob_over(L, c1["projection"]), 6) for L in POISSON_LINES},
        },
    })

candidates.sort(key=lambda c: (c["game_id"], c["team"], -c["c1_projection"]))
code_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
artifact = {
    "status": "RESEARCH_ONLY_FROZEN_FORWARD_SHADOW_NOT_A_PICK",
    "target": {"season": TARGET_SEASON, "week": TARGET_WEEK, "first_kickoff_utc": FIRST_KICKOFF_UTC},
    "frozen_at_utc": frozen_at,
    "code_commit_at_freeze": code_sha,
    "preregistration": "engineering/nfl_pregame_target_share_20260923/PREREGISTRATION.md",
    "sources": {
        "player_weeks_2025": {k: player_2025[k] for k in ("source_url", "fetched_at_utc", "rows_sha256")},
        "player_weeks_2026": {k: player_2026[k] for k in ("source_url", "fetched_at_utc", "rows_sha256")},
        "team_offense_player_derived": {s: {k: v[k] for k in ("source_url", "fetched_at_utc", "raw_sha256")} for s, v in team_src.items()},
        "schedule": {k: schedule[k] for k in ("source_url", "fetched_at_utc", "raw_sha256")},
    },
    "n_candidates": len(candidates), "abstain": dict(abstain),
    "known_limits": [
        "No real sportsbook line is attached here; grading joins real captured offers only if they exist.",
        "Candidates are frozen before inactives; a player who does not play is VOID at grading, never a miss.",
        "Team offense rows are player-derived (see module docstring), not digest-pinned PBP.",
    ],
    "candidates": candidates,
}
body = json.dumps(artifact, indent=2, sort_keys=True)
out = HERE / "forward_shadow_2026_wk3.json"
out.write_text(body)
(HERE / "forward_shadow_2026_wk3.sha256").write_text(hashlib.sha256(body.encode()).hexdigest() + "  forward_shadow_2026_wk3.json\n")
print(f"frozen {len(candidates)} candidates at {frozen_at}, abstain={dict(abstain)}, sha256={hashlib.sha256(body.encode()).hexdigest()[:16]}, {time.time()-t0:.1f}s")
