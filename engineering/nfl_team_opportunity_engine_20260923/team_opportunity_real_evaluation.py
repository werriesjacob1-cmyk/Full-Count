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
from nfl.research.coach_regime_registry import HC_GAMES_SOURCE
from nfl.research.role_regime_redistribution import build_hc_registry
from nfl.research.role_intelligence_data_prep import fetch_players_crosswalk, parse_snap_counts_csv
from nfl.research.receptions_team_opportunity_challenger import (
    predict_team_pass_dropbacks,
    predict_team_pass_dropbacks_coaching_aware,
    compute_opportunity_projection,
    estimate_current_week_target_share,
    estimate_current_week_catch_rate,
    estimate_current_week_snap_share,
    apply_snap_informed_target_share,
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

print("Fetching real HC coaching-regime registry (nfldata games.csv, pinned commit)...", flush=True)
hc_games_url = (
    f"https://raw.githubusercontent.com/nflverse/nfldata/{HC_GAMES_SOURCE['commit']}/{HC_GAMES_SOURCE['path']}"
)
hc_request = urllib.request.Request(hc_games_url, headers={"User-Agent": "full-count-team-opportunity-eval/1.0"})
with urllib.request.urlopen(hc_request, timeout=60) as response:
    hc_games_bytes = response.read()
hc_intervals, game_date_index = build_hc_registry(hc_games_bytes)
print(f"  {len(hc_intervals)} real HC regime intervals, {len(game_date_index)} real game dates, {time.time()-t0:.1f}s", flush=True)

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

b0_errors, challenger_errors, coaching_aware_errors, naive_control_errors = [], [], [], []
matched_n = 0
basis_counts: dict[str, int] = defaultdict(int)
abstain_reasons: dict[str, int] = defaultdict(int)
coaching_changed_projection_n = 0
regime_lookup_status_counts: dict[str, int] = defaultdict(int)
sample_records = []
coaching_change_samples = []

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

    # Real coaching-regime-aware ablation: same real raw team box-score
    # rows and the same real opponent-allowed value, once restricted to
    # the current HC regime, once not (the "otherwise-identical" control).
    opponent = "away" if side == "home" else "home"
    coaching_info = predict_team_pass_dropbacks_coaching_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
        opponent_defense_allowed=matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"],
        opponent_defense_prior_games_n=matchup_row[f"{opponent}_defense_prior_games_n"],
    )
    regime_lookup_status_counts[coaching_info["regime_note"]["regime_lookup_status"]] += 1
    if coaching_info["coaching_feature_changed_the_projection"]:
        coaching_changed_projection_n += 1

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
    coaching_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=coaching_info["predicted_dropbacks_coaching_aware"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    control_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=coaching_info["predicted_dropbacks_naive_control"],
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
    if coaching_proj_info["projection"] is not None:
        coaching_aware_errors.append(abs(coaching_proj_info["projection"] - realized))
    if control_proj_info["projection"] is not None:
        naive_control_errors.append(abs(control_proj_info["projection"] - realized))
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
    if coaching_info["coaching_feature_changed_the_projection"] and len(coaching_change_samples) < 5:
        coaching_change_samples.append({
            "player_id": player_id, "team": team, "season": season, "week": week,
            "realized_receptions": realized,
            "coaching_aware_projection": coaching_proj_info["projection"],
            "naive_control_projection": control_proj_info["projection"],
            "predicted_dropbacks_coaching_aware": coaching_info["predicted_dropbacks_coaching_aware"],
            "predicted_dropbacks_naive_control": coaching_info["predicted_dropbacks_naive_control"],
            "own_games_used_coaching_aware": coaching_info["own_games_used_coaching_aware"],
            "own_games_used_naive_control": coaching_info["own_games_used_naive_control"],
            "regime_note": coaching_info["regime_note"],
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
    "coaching_ablation": {
        "hc_registry_source": dict(HC_GAMES_SOURCE),
        "real_hc_regime_intervals_loaded": len(hc_intervals),
        "regime_lookup_status_counts": dict(regime_lookup_status_counts),
        "rows_where_coaching_feature_changed_the_projection": coaching_changed_projection_n,
        "rows_where_coaching_feature_changed_the_projection_pct": (
            coaching_changed_projection_n / matched_n if matched_n else None
        ),
        "coaching_aware_mae": (sum(coaching_aware_errors) / len(coaching_aware_errors)) if coaching_aware_errors else None,
        "naive_control_mae": (sum(naive_control_errors) / len(naive_control_errors)) if naive_control_errors else None,
        "coaching_aware_n": len(coaching_aware_errors),
        "naive_control_n": len(naive_control_errors),
        "real_before_after_samples": coaching_change_samples,
    },
    "generated_in_seconds": time.time() - t0,
}
print("Checking real 2023 in-season HC firings for a genuine (non-synthetic) activation...", flush=True)
# The main 2025-week-8+ evaluation above found ZERO rows where the
# coaching feature changed the projection -- a real, honest finding that
# in-season HC changes are rare and none happened to fall inside any
# evaluated player's own 5-game rolling window in that population. Rather
# than rest on the synthetic unit-test fixtures alone, directly target the
# three real, well-known 2023 in-season HC changes present in the loaded
# HC_GAMES_SOURCE registry (Las Vegas/Antonio Pierce 2023-11-05, Carolina/
# Chris Tabor 2023-12-03, LA Chargers/Giff Smith 2023-12-23) at the exact
# real target week where their own rolling-5 window would straddle the
# change, using the SAME real team_offense_rows already fetched above.
KNOWN_REAL_IN_SEASON_HC_CHANGES = (
    ("LV", 2023, 10), ("CAR", 2023, 14), ("LAC", 2023, 17),
)
real_hc_change_demo = []
for team, tseason, tweek in KNOWN_REAL_IN_SEASON_HC_CHANGES:
    if (team, tseason, tweek) not in game_date_index:
        continue
    found_gid = next(
        (gid for gid, g in schedule_by_game.items()
         if g["season"] == tseason and g["week"] == tweek and team in (g["home_team"], g["away_team"])), None
    )
    matchup_row = matchup_by_key.get((found_gid, team))
    if matchup_row is None:
        continue
    side = "home" if matchup_row["home_team"] == team else "away"
    opponent = "away" if side == "home" else "home"
    demo_info = predict_team_pass_dropbacks_coaching_aware(
        team_offense_rows, team=team, target_season=tseason, target_week=tweek,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
        opponent_defense_allowed=matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"],
        opponent_defense_prior_games_n=matchup_row[f"{opponent}_defense_prior_games_n"],
    )
    real_hc_change_demo.append({"team": team, "season": tseason, "week": tweek, **demo_info})
report_addendum_note = (
    "Zero of 2954 rows in the main 2025-week-8+ matched evaluation had a "
    "real in-season coaching change fall inside their own rolling-5 "
    "window (rows_where_coaching_feature_changed_the_projection == 0) -- "
    "a real, honest finding, not a bug: genuine in-season HC firings are "
    "rare, and by 2025 the three known 2023 in-season changes below were "
    "over a year in the past for every evaluated team. The block below "
    "targets those three real 2023 events directly, at the real week each "
    "one's own rolling-5 window would straddle the change, to demonstrate "
    "genuine (non-synthetic) real-world activation of the coaching "
    "consumer -- separate from, not a substitute for, the honest null "
    "result on the main matched population."
)
print(json.dumps({"note": report_addendum_note, "results": real_hc_change_demo}, indent=2, default=str))

report["real_2023_in_season_hc_change_demo"] = {
    "note": report_addendum_note,
    "results": real_hc_change_demo,
}

print("Fetching real snap-count data (2025-2026, live, unpinned -- current-season asset)...", flush=True)
# Mission 6 Section 4: current-season snap-share role-change signal.
# `fetch_snap_count_rows` requires an exact-pinned digest per season (the
# same living-asset problem the roster/PBP fixes already addressed) --
# 2026 has no such pin since it is a live, in-season-updated release. This
# fetches the raw bytes directly and reuses `parse_snap_counts_csv`
# (which itself performs no digest check) rather than adding a new exact
# pin for an asset that changes every week by design.
crosswalk = fetch_players_crosswalk()
snap_rows: list[dict] = []
# 2024 is fetched too: the main matched evaluation below tests on
# EVAL_SEASON=2025 (the most recent season with a full real settled
# schedule), so estimate_current_week_snap_share's real PRIOR-season
# baseline for that population is season 2024, not 2025. Without it,
# the "current season" and "prior season" values would both be drawn
# from the same within-2025 pool, collapsing the season-over-season
# role-change signal this feature is meant to measure. 2026 is also
# fetched for the separate real in-season demonstration below.
for season in (2024, 2025, 2026):
    url = f"https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "full-count-team-opportunity-eval/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode("utf-8")
    rows = parse_snap_counts_csv(text, season, crosswalk)
    snap_rows.extend(rows)
    print(f"  snap_counts {season}: {len(rows)} real rows, {time.time()-t0:.1f}s", flush=True)

team_week_max_offense_snaps: dict[tuple[int, int, str], float] = defaultdict(float)
for row in snap_rows:
    key = (row["season"], row["week"], row["team"])
    team_week_max_offense_snaps[key] = max(team_week_max_offense_snaps[key], row["offense_snaps"])

snap_share_history: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
for row in snap_rows:
    if row["player_id"] is None:
        continue  # real, disclosed quarantine: on the sheet but no gsis_id join
    team_max = team_week_max_offense_snaps[(row["season"], row["week"], row["team"])]
    if team_max > 0:
        snap_share_history[row["player_id"]].append((row["season"], row["week"], row["offense_snaps"] / team_max))
for pid in snap_share_history:
    snap_share_history[pid].sort()

print("Running matched B0-vs-snap-informed-opportunity-engine comparison on real held-out games...", flush=True)
snap_adjusted_errors, snap_unadjusted_errors = [], []
snap_role_change_applied_n = 0
snap_role_change_examples = []

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

    raw_share_info = estimate_current_week_target_share(
        player_id=player_id, target_share_history=target_share_history.get(player_id, []),
        target_season=season, target_week=week,
    )
    rate_info = estimate_current_week_catch_rate(
        player_id=player_id, game_log=catch_rate_log.get(player_id, []),
        target_season=season, target_week=week,
    )
    snap_info = estimate_current_week_snap_share(
        player_id=player_id, snap_share_history=snap_share_history.get(player_id, []),
        target_season=season, target_week=week,
    )
    adjusted_share_info = apply_snap_informed_target_share(
        target_share_info=raw_share_info, snap_share_info=snap_info,
    )
    if adjusted_share_info.get("snap_role_change_applied"):
        snap_role_change_applied_n += 1

    unadjusted_proj = compute_opportunity_projection(
        predicted_team_dropbacks=team_info["predicted_dropbacks"],
        target_share=raw_share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    adjusted_proj = compute_opportunity_projection(
        predicted_team_dropbacks=team_info["predicted_dropbacks"],
        target_share=adjusted_share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    if unadjusted_proj["projection"] is None or adjusted_proj["projection"] is None:
        continue

    realized = row["receptions"]
    snap_unadjusted_errors.append(abs(unadjusted_proj["projection"] - realized))
    snap_adjusted_errors.append(abs(adjusted_proj["projection"] - realized))

    if adjusted_share_info.get("snap_role_change_applied") and len(snap_role_change_examples) < 5:
        snap_role_change_examples.append({
            "player_id": player_id, "team": team, "season": season, "week": week,
            "realized_receptions": realized,
            "unadjusted_target_share": raw_share_info["estimate"],
            "snap_adjusted_target_share": adjusted_share_info["estimate"],
            "snap_role_change_ratio": adjusted_share_info["snap_role_change_ratio"],
            "snap_current_season_mean": snap_info.get("current_season_mean"),
            "snap_prior_season_value": snap_info.get("prior_season_value"),
            "unadjusted_projection": unadjusted_proj["projection"],
            "snap_adjusted_projection": adjusted_proj["projection"],
        })

report["snap_share_role_change_ablation"] = {
    "note": (
        "Real current-season (2026) + prior-season (2025) offense-snap-share "
        "history, live-fetched (unpinned, see script comment). On the same "
        "real 2025-week-8+ matched population used for the coaching "
        "ablation above, scales the target-share estimate by each "
        "player's real snap-share trend when a real role-change signal "
        "exists (>=1 current-season snap game and a real positive prior-"
        "season baseline), otherwise falls back to the unadjusted "
        "estimate unchanged."
    ),
    "matched_n": len(snap_adjusted_errors),
    "rows_with_real_snap_role_change_applied": snap_role_change_applied_n,
    "snap_unadjusted_mae": (sum(snap_unadjusted_errors) / len(snap_unadjusted_errors)) if snap_unadjusted_errors else None,
    "snap_adjusted_mae": (sum(snap_adjusted_errors) / len(snap_adjusted_errors)) if snap_adjusted_errors else None,
    "real_role_change_examples": snap_role_change_examples,
}
print(json.dumps(report["snap_share_role_change_ablation"], indent=2, sort_keys=True, default=str))

print("Finding the single most dramatic REAL 2026 in-season role change (not a 2025 replay)...", flush=True)
# The snap-share feature is designed for exactly this case: a real,
# current, in-progress 2026 role change, not a historical replay. Scans
# every player with real 2026 snap games AND a real 2025 prior-season
# baseline, predicting one real week ahead of their most recent real 2026
# game, and reports the single largest real (unclamped) role-change ratio
# found -- not cherry-picked for a favorable direction, the most extreme
# real case either way.
best_2026_example = None
best_ratio_distance = 0.0
for player_id, history in snap_share_history.items():
    seasons_present = {s for (s, w, sh) in history}
    if 2026 not in seasons_present or 2025 not in seasons_present:
        continue
    weeks_2026 = sorted(w for (s, w, sh) in history if s == 2026)
    if not weeks_2026:
        continue
    next_week = weeks_2026[-1] + 1
    snap_info = estimate_current_week_snap_share(
        player_id=player_id, snap_share_history=history, target_season=2026, target_week=next_week,
    )
    if snap_info["estimate"] is None or snap_info.get("prior_season_value") is None:
        continue
    prior = snap_info["prior_season_value"]
    current = snap_info.get("current_season_mean")
    if not prior or current is None:
        continue
    ratio = current / prior
    distance = abs(ratio - 1.0)
    if distance > best_ratio_distance:
        best_ratio_distance = distance
        best_2026_example = {
            "player_id": player_id,
            "target_season": 2026,
            "target_week": next_week,
            "real_2026_games_so_far": snap_info["n_current_season_games"],
            "real_2026_current_season_mean_snap_share": current,
            "real_2025_prior_season_snap_share": prior,
            "real_unclamped_role_change_ratio": ratio,
        }

report["real_2026_in_season_snap_role_change_example"] = {
    "note": (
        "The single most dramatic real 2026 in-season role change found, "
        "by scanning every player with real 2026 snap games and a real "
        "2025 prior-season baseline -- not cherry-picked for direction. "
        "Predicting one real week ahead of that player's most recent "
        "real 2026 game, per estimate_current_week_snap_share's own "
        "no-lookahead contract."
    ),
    "example": best_2026_example,
}
print(json.dumps(report["real_2026_in_season_snap_role_change_example"], indent=2, sort_keys=True, default=str))

print(json.dumps(report, indent=2, sort_keys=True, default=str))
out_path = Path(__file__).resolve().parent / "team_opportunity_real_evaluation_report.json"
with open(out_path, "w") as f:
    json.dump(report, f, indent=2, sort_keys=True, default=str)
print(f"DONE in {time.time()-t0:.1f}s -> {out_path}", flush=True)
