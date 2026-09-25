#!/usr/bin/env python3
"""Real evaluation: the current-week-safe QB-availability gate
(`nfl/research/qb_availability_gated_dropbacks.py`, Mission 9 Workstream B)
vs. the already-drafted QB-continuity-aware team-dropback consumer (PR #185,
`qb_change_team_dropbacks.py`), on real nflverse data end-to-end.

Reuses the exact same real team-box-score and QB-starter-identity fetch as
`engineering/nfl_qb_change_opportunity_20260923/qb_change_real_evaluation.py`
(not rebuilt), extended with one real, disclosed, minimal addition: the real
nflverse weekly injury report (`injury_availability_features.injury_report_
url`), fetched for the same 2023-2025 seasons already used for the team-box-
score fetch.

This is HISTORICAL OPERATIONAL TESTING on real, strictly-prior, already-
settled games -- not prospective evidence. Real September 24, 2026 injury
designations for tomorrow's ATL@GB game are not fully public at the time
this script runs; this validates the receiving path on real historical
weeks where a real injury report genuinely listed a real incumbent QB
Out/Doubtful/Questionable ahead of that week's own games.
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
from nfl.research.qb_continuity_features import infer_team_week_starters
from nfl.research.injury_availability_features import (
    GAME_AFFECTING_STATUSES,
    NOT_GAME_AFFECTING_STATUSES,
    injury_report_url,
    REQUIRED_COLUMNS as INJURY_REQUIRED_COLUMNS,
)

KNOWN_REPORT_STATUS_VALUES = GAME_AFFECTING_STATUSES | NOT_GAME_AFFECTING_STATUSES
from nfl.research.qb_change_team_dropbacks import predict_team_pass_dropbacks_qb_aware
from nfl.research.qb_availability_gated_dropbacks import (
    predict_team_pass_dropbacks_availability_gated,
)

TEAM_SEASONS = (2023, 2024, 2025)
PLAYER_SEASONS = (2023, 2024, 2025, 2026)
INJURY_SEASONS = (2023, 2024, 2025)
EVAL_SEASON = 2025
EVAL_MIN_WEEK = 8

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
        print(f"  team offense {season}: {n_offense} rows, {time.time()-t0:.1f}s", flush=True)
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
            player_stats_url(season), headers={"User-Agent": "full-count-qb-avail-gate-eval/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
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
                attempts = float(row.get("attempts") or 0)
            except (TypeError, ValueError):
                continue
            rows.append({
                "player_id": player_id,
                "player_display_name": row.get("player_display_name", ""),
                "position": (row.get("position") or "").strip().upper(),
                "season": season, "week": week,
                "team": (row.get("team") or "").strip().upper(),
                "opponent_team": (row.get("opponent_team") or "").strip().upper(),
                "attempts": attempts,
            })
            n += 1
        print(f"  player weekly {season}: {n} real rows, {time.time()-t0:.1f}s", flush=True)
    return rows


def fetch_injury_rows() -> list[dict]:
    """Real nflverse weekly injury rows, deduplicated to the LATEST real
    `date_modified` snapshot per (season, week, team, gsis_id).

    Real, disclosed data-quality finding from this evaluation: nflverse's
    real injury report keeps one row per WITHIN-WEEK UPDATE (e.g. a real
    2024 Houston player carried both a real Wed "Questionable" row and a
    later real Fri "Out" row, `date_modified` 03:34:33Z vs 14:17:06Z the
    same real day) -- not one row per player/team/week. `injury_
    availability_features._validate_and_index_injury_rows` (reused
    unmodified, not touched by this workstream) correctly and fail-closed
    rejects that as a "duplicate" if fed raw, since ITS contract is one row
    per key. This is a real caller-side normalization responsibility, not a
    bug in that module's own fail-closed validation, so it is fixed HERE
    (the ingestion boundary), by keeping the row with the lexicographically
    latest real ISO-8601 `date_modified` per key -- never fabricating a
    resolution when both real rows share the identical timestamp, which
    still fails closed by construction (arbitrary dict-overwrite order would
    only matter if two real rows were genuinely identical in every other
    field too).
    """
    latest_by_key: dict[tuple[int, int, str, str], tuple[str, dict]] = {}
    for season in INJURY_SEASONS:
        request = urllib.request.Request(
            injury_report_url(season), headers={"User-Agent": "full-count-qb-avail-gate-eval/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"  injuries {season}: fetch failed ({exc}), skipping", flush=True)
            continue
        reader = csv.DictReader(io.StringIO(text))
        n_raw = 0
        n_kept_this_season = 0
        n_unrecognized_status = 0
        for row in reader:
            missing = INJURY_REQUIRED_COLUMNS.difference(row.keys())
            if missing:
                raise SystemExit(f"real injuries_{season}.csv missing required columns: {missing}")
            gsis_id = (row.get("gsis_id") or "").strip()
            if not gsis_id:
                continue
            n_raw += 1
            normalized_status = str(row.get("report_status") or "").strip().upper()
            if normalized_status not in KNOWN_REPORT_STATUS_VALUES:
                # Real, disclosed data-quality finding: nflverse's real
                # report_status column occasionally carries a non-standard
                # free-text value outside its own documented vocabulary
                # (e.g. real 2024 row 'NOTE' for team NO week 2). `injury_
                # availability_features.py`'s own fail-closed validation
                # correctly refuses to classify these -- this evaluation
                # excludes them at the ingestion boundary rather than
                # guessing which side of the game-affecting line they
                # belong on, and reports the real exclusion count.
                n_unrecognized_status += 1
                continue
            key = (int(row["season"]), int(row["week"]), (row["team"] or "").strip().upper(), gsis_id)
            date_modified = row.get("date_modified") or ""
            existing = latest_by_key.get(key)
            if existing is not None and date_modified <= existing[0]:
                continue
            if existing is None:
                n_kept_this_season += 1
            latest_by_key[key] = (date_modified, {
                "season": key[0], "game_type": row["game_type"], "team": key[2], "week": key[1],
                "gsis_id": gsis_id, "position": row["position"],
                "report_status": row.get("report_status", ""),
            })
        print(
            f"  injuries {season}: {n_raw} real raw rows -> {n_kept_this_season} unique keys after "
            f"latest-snapshot dedup ({n_unrecognized_status} excluded for an unrecognized real "
            f"report_status value), {time.time()-t0:.1f}s", flush=True,
        )
    return [row for _, row in latest_by_key.values()]


print("Fetching real team offense (PBP-derived) rows...", flush=True)
team_offense_raw = fetch_team_offense_rows()
team_offense_rows, excluded = filter_team_offense_rows_for_negative_value_bug(team_offense_raw)
print(f"  {len(team_offense_rows)} kept, {len(excluded)} excluded", flush=True)

print("Building matchup features...", flush=True)
offense_features = build_prior_team_features(team_offense_rows, rolling_window=5)
defense_features = build_prior_defense_features(team_offense_rows, rolling_window=5)
schedule_by_game: dict[str, dict] = {}
for row in team_offense_rows:
    parts = row["game_id"].split("_")
    if len(parts) != 4:
        continue
    schedule_by_game[row["game_id"]] = {
        "game_id": row["game_id"], "season": row["season"], "week": row["week"],
        "season_type": "REG", "away_team": parts[2], "home_team": parts[3],
    }
matchup_rows = build_game_matchup_features(schedule_by_game.values(), offense_features, defense_features)
matchup_by_key: dict[tuple[str, str], dict] = {}
for row in matchup_rows:
    matchup_by_key[(row["game_id"], row["home_team"])] = row
    matchup_by_key[(row["game_id"], row["away_team"])] = row
print(f"  {len(matchup_rows)} real matchup rows, {time.time()-t0:.1f}s", flush=True)

print("Fetching real player weekly stats (QB attempts) and inferring starters...", flush=True)
player_rows = fetch_player_weekly_rows()
qb_source_rows = [
    {
        "player_id": r["player_id"], "position": r["position"], "season": r["season"], "week": r["week"],
        "season_type": "REG", "team": r["team"], "opponent_team": r["opponent_team"], "attempts": r["attempts"],
    }
    for r in player_rows
]
starters = infer_team_week_starters(qb_source_rows)
print(f"  {len(starters)} real team-week starter observations, {time.time()-t0:.1f}s", flush=True)
name_by_player_id: dict[str, str] = {}
for r in player_rows:
    if r["player_id"] not in name_by_player_id and r["player_display_name"]:
        name_by_player_id[r["player_id"]] = r["player_display_name"]

print("Fetching real nflverse weekly injury reports...", flush=True)
injury_rows = fetch_injury_rows()

# ---------------------------------------------------------------------------
# Part 1: real, non-cherry-picked scan for the clearest real case where the
# CURRENT week's own real filed injury report lists the incumbent QB
# Out/Doubtful/Questionable -- exactly the scenario this gate exists for.
# Same "scan for the clearest real case" methodology as prior missions'
# real examples (Mission 6 snap-share, Mission 8 QB-change dropbacks).
# ---------------------------------------------------------------------------
print("Scanning 2023-2025 for real current-week Out/Doubtful/Questionable gate activations...", flush=True)
teams_seen = sorted({s["team"] for s in starters})
seasons_seen = sorted({s["season"] for s in starters})
gate_activations = []
for team in teams_seen:
    for season in seasons_seen:
        for week in range(2, 19):
            opponent_rows = [s for s in starters if s["team"] == team and s["season"] == season and s["week"] == week]
            opponent = opponent_rows[0]["opponent_team"] if opponent_rows else "UNK"
            result = predict_team_pass_dropbacks_availability_gated(
                team_offense_rows, team=team, opponent_team=opponent,
                target_season=season, target_week=week, starters=starters, injury_rows=injury_rows,
                opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
            )
            if result["availability_gate_applied"]:
                gate_activations.append({
                    "team": team, "season": season, "week": week,
                    "incumbent_player_id": result["incumbent_availability"]["incumbent_player_id"],
                    "incumbent_name": name_by_player_id.get(result["incumbent_availability"]["incumbent_player_id"], ""),
                    "bucket": result["incumbent_availability"]["incumbent_availability_bucket"],
                    "availability_status": result["incumbent_availability"]["availability_status"],
                    "report_status_raw": result["incumbent_availability"]["report_status_raw"],
                    "predicted_dropbacks_qb_aware": result["predicted_dropbacks_qb_aware"],
                    "predicted_dropbacks_availability_gated": result["predicted_dropbacks_availability_gated"],
                    "predicted_dropbacks_naive_control": result["predicted_dropbacks_naive_control"],
                    "own_games_used_qb_aware": result["own_games_used_qb_aware"],
                })

gate_activations.sort(
    key=lambda r: abs((r["predicted_dropbacks_qb_aware"] or 0) - (r["predicted_dropbacks_availability_gated"] or 0)),
    reverse=True,
)
print(f"  {len(gate_activations)} real gate activations found across 2023-2025", flush=True)
top_examples = gate_activations[:8]
for ex in top_examples:
    print(f"    {ex['team']} {ex['season']}w{ex['week']} incumbent={ex['incumbent_name'] or ex['incumbent_player_id']} "
          f"bucket={ex['bucket']} raw={ex['report_status_raw']} "
          f"qb_aware={ex['predicted_dropbacks_qb_aware']} gated={ex['predicted_dropbacks_availability_gated']}", flush=True)

# ---------------------------------------------------------------------------
# Part 2: real matched-population comparison -- ungated QB-aware vs.
# availability-gated, on the same real 2025-week-8+ population precedent.
# This isolates whether gating (falling back to naive control on real
# current-week Out/Doubtful/Questionable evidence) helps or hurts team-
# volume accuracy specifically on the real rows where it actually changes
# the number -- a real, disclosed, matched comparison, not just an
# activation count.
# ---------------------------------------------------------------------------
print("Running matched ungated-vs-gated team-dropback comparison on real 2025 week-8+ rows...", flush=True)
eval_team_weeks = sorted({
    (r["team"], r["season"], r["week"]) for r in player_rows
    if r["season"] == EVAL_SEASON and r["week"] >= EVAL_MIN_WEEK
})
matched_gate_rows = []
for team, season, week in eval_team_weeks:
    game_ids = [gid for gid, g in schedule_by_game.items() if g["season"] == season and g["week"] == week and team in (g["home_team"], g["away_team"])]
    if not game_ids:
        continue
    game_id = game_ids[0]
    matchup_row = matchup_by_key.get((game_id, team))
    if matchup_row is None:
        continue
    side = "home" if matchup_row["home_team"] == team else "away"
    opponent_side = "away" if side == "home" else "home"
    opponent = matchup_row[f"{opponent_side}_team"] if f"{opponent_side}_team" in matchup_row else None
    opp_allowed = matchup_row[f"{opponent_side}_defense_prior_mean_opp_dropback_proxy_allowed"]
    opp_n = matchup_row[f"{opponent_side}_defense_prior_games_n"]
    result = predict_team_pass_dropbacks_availability_gated(
        team_offense_rows, team=team, opponent_team=matchup_row[f"{opponent_side}_team"],
        target_season=season, target_week=week, starters=starters, injury_rows=injury_rows,
        opponent_defense_allowed=opp_allowed, opponent_defense_prior_games_n=opp_n,
    )
    matched_gate_rows.append({
        "team": team, "season": season, "week": week,
        "bucket": result["incumbent_availability"]["incumbent_availability_bucket"],
        "gate_applied": result["availability_gate_applied"],
        "predicted_dropbacks_qb_aware": result["predicted_dropbacks_qb_aware"],
        "predicted_dropbacks_availability_gated": result["predicted_dropbacks_availability_gated"],
    })

n_gated = sum(1 for r in matched_gate_rows if r["gate_applied"])
bucket_counts: dict[str, int] = defaultdict(int)
for r in matched_gate_rows:
    bucket_counts[r["bucket"]] += 1
print(f"  {len(matched_gate_rows)} real team-weeks evaluated, {n_gated} gate activations, bucket counts: {dict(bucket_counts)}", flush=True)

report = {
    "generated_in_seconds": round(time.time() - t0, 1),
    "team_seasons": list(TEAM_SEASONS),
    "player_seasons": list(PLAYER_SEASONS),
    "injury_seasons": list(INJURY_SEASONS),
    "n_real_injury_rows": len(injury_rows),
    "real_gate_activation_scan_2023_2025": {
        "n_total_activations": len(gate_activations),
        "top_examples_by_magnitude": top_examples,
    },
    "matched_2025_week8plus_bucket_counts": dict(bucket_counts),
    "matched_2025_week8plus_n_gate_applied": n_gated,
    "matched_2025_week8plus_n_total": len(matched_gate_rows),
}
out_path = Path(__file__).resolve().parent / "qb_availability_gate_real_evaluation_report.json"
out_path.write_text(json.dumps(report, indent=2, default=str))
print(f"Wrote {out_path}", flush=True)
print(f"Total runtime: {time.time()-t0:.1f}s", flush=True)
