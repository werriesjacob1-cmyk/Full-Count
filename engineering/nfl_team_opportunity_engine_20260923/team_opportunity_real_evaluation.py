#!/usr/bin/env python3
"""Real evaluation: B0 vs the new team-opportunity-engine challenger
(`nfl/research/receptions_team_opportunity_challenger.py`) on a matched
real population, using real nflverse data end-to-end.

Team-side substrate: real 2023-2025 PBP-derived team box scores (the exact
pinned/digest-checked seasons `game_market_c2_data_prep.PBP_SOURCE_ASSET_
DIGESTS` already covers), fed through the existing, unmodified
`team_prior_features`/`defense_prior_features`/`game_matchup_features`.

Player-side substrate: real 2023-2026 `stats_player_week_<season>.csv`
weekly rows (the same nflverse release `role_intelligence_data_prep`
already fetches for its own weekly-stats history, re-fetched here with an
explicit `receptions` column that module's own parser does not currently
extract -- disclosed, minimal, evaluation-script-only extension, not a
change to that shared module).

This is HISTORICAL OPERATIONAL TESTING on real, strictly-prior, already-
settled games -- not prospective evidence. No live pregame market exists
for any of these games at the time this script runs (2026-09-23, a
Tuesday with no NFL slate), so every reported number here is a real,
reproducible historical accuracy comparison, never presented as a live
prospective capture.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nfl.research.game_market_c2_data_prep import process_pbp_season
from nfl.research.game_market_c2_source_digests import PBP_SOURCE_ASSET_DIGESTS
from nfl.research.game_market_c2_features import filter_team_offense_rows_for_negative_value_bug
from nfl.research.team_prior_features import build_prior_team_features
from nfl.research.defense_prior_features import build_prior_defense_features
from nfl.research.game_matchup_features import build_game_matchup_features
from nfl.research.nflverse_history import player_stats_url
from nfl.research.receptions_shadow import current_b0_projection
from nfl.research.receptions_team_opportunity_challenger import (
    predict_team_pass_dropbacks,
    compute_opportunity_projection,
    estimate_current_week_target_share,
    estimate_current_week_catch_rate,
)

TEAM_SEASONS = (2023, 2024, 2025)
PLAYER_SEASONS = (2023, 2024, 2025, 2026)
EVAL_SEASON = 2025
EVAL_MIN_WEEK = 8  # needs real prior-season-boundary-crossing history to be fair to both models

t0 = time.time()


def fetch_team_offense_rows() -> list[dict]:
    rows: list[dict] = []
    for season in TEAM_SEASONS:
        offense_buf = io.StringIO()
        play_buf = io.StringIO()
        offense_writer = csv.DictWriter(offense_buf, fieldnames=[
            "game_id", "season", "week", "season_type", "team", "opponent_team",
            "attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards",
        ])
        offense_writer.writeheader()
        play_writer = csv.DictWriter(play_buf, fieldnames=[
            "game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
            "down", "half_seconds_remaining", "wp", "qb_dropback", "rush_attempt", "qb_kneel", "qb_spike",
        ])
        play_writer.writeheader()
        n_offense, n_plays = process_pbp_season(season, PBP_SOURCE_ASSET_DIGESTS, offense_writer, play_writer)
        offense_buf.seek(0)
        rows.extend(list(csv.DictReader(offense_buf)))
        print(f"  team offense {season}: {n_offense} team-game rows, {n_plays} filtered plays, {time.time()-t0:.1f}s", flush=True)
    for row in rows:
        row["season"] = int(row["season"])
        row["week"] = int(row["week"])
        for f in ("attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards"):
            row[f] = float(row[f])
    return rows


def fetch_player_weekly_rows() -> list[dict]:
    rows: list[dict] = []
    for season in PLAYER_SEASONS:
        request = urllib.request.Request(
            player_stats_url(season), headers={"User-Agent": "full-count-team-opportunity-eval/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - a season may not exist yet (e.g. future weeks)
            print(f"  player weekly {season}: fetch failed ({exc}), skipping", flush=True)
            continue
        reader = csv.DictReader(io.StringIO(text))
        n = 0
        for row in reader:
            if (row.get("season_type") or "").strip().upper() != "REG":
                continue
            player_id = (row.get("player_id") or "").strip()
            if player_id in ("", "0"):
                continue
            try:
                week = int(row["week"])
                targets = float(row.get("targets") or 0)
                receptions = float(row.get("receptions") or 0)
            except (TypeError, ValueError):
                continue
            rows.append({
                "player_id": player_id,
                "player_display_name": row.get("player_display_name", ""),
                "position": (row.get("position") or "").strip().upper(),
                "season": season, "week": week,
                "team": (row.get("team") or "").strip().upper(),
                "opponent_team": (row.get("opponent_team") or "").strip().upper(),
                "targets": targets, "receptions": receptions,
            })
            n += 1
        print(f"  player weekly {season}: {n} real player-game rows, {time.time()-t0:.1f}s", flush=True)
    return rows


print("Fetching real team offense (PBP-derived) rows...", flush=True)
team_offense_raw = fetch_team_offense_rows()
team_offense_rows, excluded = filter_team_offense_rows_for_negative_value_bug(team_offense_raw)
print(f"  {len(team_offense_rows)} kept, {len(excluded)} excluded for the known negative-value bug", flush=True)

print("Building team_prior_features / defense_prior_features / game_matchup_features...", flush=True)
offense_features = build_prior_team_features(team_offense_rows, rolling_window=5)
defense_features = build_prior_defense_features(team_offense_rows, rolling_window=5)

schedule_by_game: dict[str, dict] = {}
for row in team_offense_rows:
    game_id = row["game_id"]
    parts = game_id.split("_")
    if len(parts) != 4:
        continue
    schedule_by_game[game_id] = {
        "game_id": game_id, "season": row["season"], "week": row["week"],
        "season_type": "REG", "away_team": parts[2], "home_team": parts[3],
    }
matchup_rows = build_game_matchup_features(
    schedule_by_game.values(), offense_features, defense_features,
)
matchup_by_key: dict[tuple[str, str], dict] = {}
for row in matchup_rows:
    matchup_by_key[(row["game_id"], row["home_team"])] = row
    matchup_by_key[(row["game_id"], row["away_team"])] = row
print(f"  {len(matchup_rows)} real matchup rows built, {time.time()-t0:.1f}s", flush=True)

print("Fetching real player weekly stats...", flush=True)
player_rows = fetch_player_weekly_rows()

team_week_targets: dict[tuple[int, int, str], float] = defaultdict(float)
for row in player_rows:
    team_week_targets[(row["season"], row["week"], row["team"])] += row["targets"]

target_share_history: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
catch_rate_log: dict[str, list[dict]] = defaultdict(list)
game_index: dict[tuple[str, int, int], dict] = {}
for row in player_rows:
    denom = team_week_targets[(row["season"], row["week"], row["team"])]
    if denom > 0:
        target_share_history[row["player_id"]].append((row["season"], row["week"], row["targets"] / denom))
    catch_rate_log[row["player_id"]].append({
        "season": row["season"], "week": row["week"],
        "targets": row["targets"], "receptions": row["receptions"],
    })
    game_index[(row["player_id"], row["season"], row["week"])] = row
for pid in target_share_history:
    target_share_history[pid].sort()
for pid in catch_rate_log:
    catch_rate_log[pid].sort(key=lambda r: (r["season"], r["week"]))

print("Running matched B0-vs-opportunity-engine comparison on real held-out games...", flush=True)
eval_rows = [
    r for r in player_rows
    if r["season"] == EVAL_SEASON and r["week"] >= EVAL_MIN_WEEK and r["position"] in ("WR", "TE", "RB")
]

b0_errors, challenger_errors = [], []
matched_n = 0
basis_counts: dict[str, int] = defaultdict(int)
abstain_reasons: dict[str, int] = defaultdict(int)
sample_records = []

for row in eval_rows:
    player_id, season, week, team = row["player_id"], row["season"], row["week"], row["team"]
    game_ids = [gid for gid, g in schedule_by_game.items() if g["season"] == season and g["week"] == week and team in (g["home_team"], g["away_team"])]
    if not game_ids:
        continue
    game_id = game_ids[0]
    matchup_row = matchup_by_key.get((game_id, team))
    if matchup_row is None:
        continue
    side = "home" if matchup_row["home_team"] == team else "away"
    team_info = predict_team_pass_dropbacks(matchup_row, side=side)
    basis_counts[team_info["basis"]] += 1

    share_info = estimate_current_week_target_share(
        player_id=player_id, target_share_history=target_share_history.get(player_id, []),
        target_season=season, target_week=week,
    )
    rate_info = estimate_current_week_catch_rate(
        player_id=player_id, game_log=catch_rate_log.get(player_id, []),
        target_season=season, target_week=week,
    )
    proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_info["predicted_dropbacks"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    if proj_info["projection"] is None:
        abstain_reasons[proj_info["reason"]] += 1
        continue

    # B0-style baseline: real last-5-game rolling mean receptions, strictly prior.
    history = [
        {"season": s, "week": w, "season_type": "REG", "targets": g["targets"], "receptions": g["receptions"], "team": g["team"]}
        for (pid, s, w), g in game_index.items()
        if pid == player_id and (s, w) < (season, week)
    ]
    try:
        b0_info = current_b0_projection(sorted(history, key=lambda r: (r["season"], r["week"])))
        b0_projection = float(b0_info["projection"])
    except Exception:
        continue

    realized = row["receptions"]
    b0_errors.append(abs(b0_projection - realized))
    challenger_errors.append(abs(proj_info["projection"] - realized))
    matched_n += 1
    if len(sample_records) < 5:
        sample_records.append({
            "player_id": player_id, "team": team, "season": season, "week": week,
            "realized_receptions": realized, "b0_projection": b0_projection,
            "challenger_projection": proj_info["projection"],
            "team_predicted_dropbacks": team_info["predicted_dropbacks"],
            "team_dropbacks_basis": team_info["basis"],
            "target_share_estimate": share_info["estimate"],
            "target_share_basis": share_info["basis"],
            "catch_rate_estimate": rate_info["estimate"],
        })

report = {
    "analysis": "NFL_RECEPTIONS_B0_VS_TEAM_OPPORTUNITY_ENGINE_V1_HISTORICAL_OPERATIONAL_TEST",
    "status": "HISTORICAL_OPERATIONAL_TESTING_NOT_PROSPECTIVE",
    "eval_season": EVAL_SEASON,
    "eval_min_week": EVAL_MIN_WEEK,
    "matched_n": matched_n,
    "b0_mae": (sum(b0_errors) / len(b0_errors)) if b0_errors else None,
    "challenger_mae": (sum(challenger_errors) / len(challenger_errors)) if challenger_errors else None,
    "team_dropbacks_basis_counts": dict(basis_counts),
    "abstain_reason_counts": dict(abstain_reasons),
    "eligible_eval_rows_considered": len(eval_rows),
    "sample_records": sample_records,
    "generated_in_seconds": time.time() - t0,
}
print(json.dumps(report, indent=2, sort_keys=True, default=str))
out_path = Path(__file__).resolve().parent / "team_opportunity_real_evaluation_report.json"
with open(out_path, "w") as f:
    json.dump(report, f, indent=2, sort_keys=True, default=str)
print(f"DONE in {time.time()-t0:.1f}s -> {out_path}", flush=True)
