#!/usr/bin/env python3
"""Real component-level diagnostic ablation of `nfl/research/receptions_
team_opportunity_challenger.py` on a GENUINELY NEW, never-before-used-in-
any-evaluation-this-session holdout: 2024 season, weeks 8+.

## Why 2024, not 2025

Every prior evaluation of this challenger (Mission 3's `team_opportunity_
real_evaluation.py`, Mission 4's coaching-consumer re-run, Mission 6's
snap-share ablation) used the 2025 season (weeks 8+) as its test
population -- that population has been repeatedly inspected while building
and re-checking these exact features, so it can no longer serve as a fair
holdout for a new hypothesis about the same features (this project's own
anti-retuning doctrine, `engineering/AGENT_BRIDGE_PROTOCOL.md`: "held-out
results cannot be tuned into the challenger"). This script uses 2024 weeks
8+ instead -- a season never used in any of this project's opportunity-
engine evaluations before this script -- as the genuinely fresh holdout.

## What this diagnoses

Directly follows SUPERCHAD's own suggestion (Issue #91 comment
`5799901415`, echoed in the `NFL-MISSION7-PARALLEL-20260923` AGENT CLAIM):
"diagnose why added opportunity and snap-share transformations worsen 2025
matched MAE ... and test whether role signals have value specifically
under evidenced role-change situations rather than force entire-population
additive effects." This script builds, on ONE matched 2024 population:

1. B0 (real last-5-game rolling mean) -- the baseline everything below is
   compared against.
2. Team-volume-only baseline: real team dropbacks x a NAIVE (unshrunk,
   simple recent-average) target share x a naive catch rate -- isolates
   whether the team-volume signal alone, combined with the simplest
   possible player share, beats B0.
3. Full opportunity engine, current merged form, unmodified: coaching-aware
   dropbacks x shrinkage-blended target share x shrinkage-blended catch
   rate.
4. Coaching-adjustment isolated: coaching-aware team dropbacks vs. the
   naive-control (unfiltered) team dropbacks, player side held fixed.
5. Snap-share adjustment, UNIVERSAL (current merged form): applied to
   every eligible row with no threshold gate, exactly as `apply_snap_
   informed_target_share` currently works.
6. Snap-share adjustment, GATED (the new hypothesis under test): applied
   ONLY when the real unclamped role-change ratio is more extreme than a
   pre-declared threshold band, via the new `gated_snap_informed_target_
   share` helper (`./gating.py`, tested in `nfl/tests/test_opportunity_
   ablation_2024_gating.py`) -- tested at two candidate bands, `[0.7,
   1.4]` and `[0.5, 2.0]`.

Every real fetch below reuses the EXACT SAME pattern already established
by `engineering/nfl_team_opportunity_engine_20260923/
team_opportunity_real_evaluation.py` (read in full before writing this
script) -- nothing here reinvents that fetch/build logic, only the target
season and the added ablation components are new.

## Honest scope disclosure

This is HISTORICAL OPERATIONAL TESTING on real, strictly-prior, already-
settled 2024 games -- not prospective evidence, and not a re-run of the
2025 finding. It is a genuinely different real population from every prior
evaluation in this repo. No retuning against this result is performed by
this script or by the author of this script -- whatever the real numbers
say, they are reported as-is in the README and this script's own JSON
output.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

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

_gating_spec = importlib.util.spec_from_file_location(
    "opportunity_ablation_2024_gating", Path(__file__).resolve().parent / "gating.py",
)
_gating_mod = importlib.util.module_from_spec(_gating_spec)
_gating_spec.loader.exec_module(_gating_mod)  # type: ignore[union-attr]
gated_snap_informed_target_share = _gating_mod.gated_snap_informed_target_share

TEAM_SEASONS = (2022, 2023, 2024)
PLAYER_SEASONS = (2022, 2023, 2024)
SNAP_SEASONS = (2023, 2024)
EVAL_SEASON = 2024
EVAL_MIN_WEEK = 8  # matches the established 2025 evaluation's own boundary
GATE_THRESHOLDS = [
    {"label": "gate_0.7_1.4", "gate_low": 0.7, "gate_high": 1.4},
    {"label": "gate_0.5_2.0", "gate_low": 0.5, "gate_high": 2.0},
]
NAIVE_LOOKBACK_GAMES = 5  # same window B0 itself uses, for a fair "simplest possible" comparison

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
            player_stats_url(season), headers={"User-Agent": "full-count-opportunity-ablation-2024/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - a season may genuinely not exist / be unreachable
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


def naive_recent_average(values: list[float], *, lookback: int = NAIVE_LOOKBACK_GAMES) -> float | None:
    """The simplest possible real recent-average estimate: a plain,
    unshrunk mean of the player's own last `lookback` real prior values
    (target share or per-game catch rate), no season-boundary blending at
    all. `values` must already be chronologically ordered and strictly
    prior to the target week by the caller. Returns `None` (never a
    fabricated value) when no real prior value exists."""
    window = values[-lookback:]
    if not window:
        return None
    return sum(window) / len(window)


print("Fetching real team offense (PBP-derived) rows for 2022-2024...", flush=True)
team_offense_raw = fetch_team_offense_rows()
team_offense_rows, team_offense_excluded = filter_team_offense_rows_for_negative_value_bug(team_offense_raw)
print(f"  {len(team_offense_rows)} kept, {len(team_offense_excluded)} excluded for the known negative-value bug", flush=True)

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
hc_request = urllib.request.Request(hc_games_url, headers={"User-Agent": "full-count-opportunity-ablation-2024/1.0"})
with urllib.request.urlopen(hc_request, timeout=60) as response:
    hc_games_bytes = response.read()
hc_intervals, game_date_index = build_hc_registry(hc_games_bytes)
print(f"  {len(hc_intervals)} real HC regime intervals, {len(game_date_index)} real game dates, {time.time()-t0:.1f}s", flush=True)

print("Fetching real player weekly stats for 2022-2024...", flush=True)
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

print("Fetching real snap-count data (2023-2024, live, unpinned -- same pattern as the "
      "existing 2025 evaluation script, not the digest-gated fetch_snap_count_rows)...", flush=True)
crosswalk = fetch_players_crosswalk()
snap_rows: list[dict] = []
for season in SNAP_SEASONS:
    url = f"https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "full-count-opportunity-ablation-2024/1.0"})
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

print(f"Building the 2024 week-8+ eligible population and running the full component ablation "
      f"({time.time()-t0:.1f}s so far)...", flush=True)
eval_rows = [
    r for r in player_rows
    if r["season"] == EVAL_SEASON and r["week"] >= EVAL_MIN_WEEK and r["position"] in ("WR", "TE", "RB")
]

# --- Group 1: core comparison (B0 vs team-volume-only-naive vs full engine) ---
core_b0_errors, core_team_volume_only_errors, core_full_engine_errors = [], [], []
core_matched_n = 0
core_exclusion_reasons: dict[str, int] = defaultdict(int)

# --- Group 2: coaching ablation (coaching-aware vs naive-control team volume) ---
coaching_aware_errors, naive_control_errors = [], []
coaching_matched_n = 0
coaching_changed_projection_n = 0
regime_lookup_status_counts: dict[str, int] = defaultdict(int)

# --- Group 3: snap-share ablation (unadjusted vs universal vs gated thresholds) ---
snap_unadjusted_errors, snap_universal_errors = [], []
snap_gated_errors: dict[str, list[float]] = {g["label"]: [] for g in GATE_THRESHOLDS}
snap_gated_in_n: dict[str, int] = {g["label"]: 0 for g in GATE_THRESHOLDS}
snap_matched_n = 0
snap_universal_applied_n = 0

eligible_rows_considered = len(eval_rows)
sample_records = []
gated_change_samples: dict[str, list[dict]] = {g["label"]: [] for g in GATE_THRESHOLDS}

for row in eval_rows:
    player_id, season, week, team = row["player_id"], row["season"], row["week"], row["team"]
    realized = row["receptions"]

    game_ids = [
        gid for gid, g in schedule_by_game.items()
        if g["season"] == season and g["week"] == week and team in (g["home_team"], g["away_team"])
    ]
    if not game_ids:
        core_exclusion_reasons["NO_REAL_GAME_MATCH"] += 1
        continue
    game_id = game_ids[0]
    matchup_row = matchup_by_key.get((game_id, team))
    if matchup_row is None:
        core_exclusion_reasons["NO_REAL_MATCHUP_ROW"] += 1
        continue
    side = "home" if matchup_row["home_team"] == team else "away"
    opponent = "away" if side == "home" else "home"

    # Real B0: last-5-game rolling mean, strictly prior.
    history = [
        {"season": s, "week": w, "season_type": "REG", "targets": g["targets"], "receptions": g["receptions"], "team": g["team"]}
        for (pid, s, w), g in game_index.items()
        if pid == player_id and (s, w) < (season, week)
    ]
    try:
        b0_info = current_b0_projection(sorted(history, key=lambda r: (r["season"], r["week"])))
        b0_projection = float(b0_info["projection"])
        b0_ok = True
    except Exception:
        b0_projection = None
        b0_ok = False

    # Real team-volume prediction (plain, non-coaching-filtered blend).
    team_info = predict_team_pass_dropbacks(matchup_row, side=side)

    # Real coaching-aware vs naive-control team-volume prediction (raw rows).
    coaching_info = predict_team_pass_dropbacks_coaching_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
        opponent_defense_allowed=matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"],
        opponent_defense_prior_games_n=matchup_row[f"{opponent}_defense_prior_games_n"],
    )
    regime_lookup_status_counts[coaching_info["regime_note"]["regime_lookup_status"]] += 1
    if coaching_info["coaching_feature_changed_the_projection"]:
        coaching_changed_projection_n += 1

    # Real shrinkage-blended share/rate (the current merged form).
    share_info = estimate_current_week_target_share(
        player_id=player_id, target_share_history=target_share_history.get(player_id, []),
        target_season=season, target_week=week,
    )
    rate_info = estimate_current_week_catch_rate(
        player_id=player_id, game_log=catch_rate_log.get(player_id, []),
        target_season=season, target_week=week,
    )

    # Real naive (unshrunk, simple recent-average) share/rate.
    prior_target_shares_only = [
        share for (s, w, share) in target_share_history.get(player_id, []) if (s, w) < (season, week)
    ]
    naive_share = naive_recent_average(prior_target_shares_only)
    prior_catch_rate_games = [
        g for g in catch_rate_log.get(player_id, []) if (g["season"], g["week"]) < (season, week)
    ]
    prior_catch_rates_only = [
        g["receptions"] / g["targets"] for g in prior_catch_rate_games if g.get("targets") and g["targets"] > 0
    ]
    naive_rate = naive_recent_average(prior_catch_rates_only)

    # --- Group 1: core comparison ---
    full_engine_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=coaching_info["predicted_dropbacks_coaching_aware"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    team_volume_only_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_info["predicted_dropbacks"],
        target_share=naive_share, catch_rate=naive_rate,
    )
    if not b0_ok:
        core_exclusion_reasons["B0_INSUFFICIENT_REAL_HISTORY"] += 1
    elif full_engine_proj_info["projection"] is None:
        core_exclusion_reasons[f"FULL_ENGINE_{full_engine_proj_info['reason']}"] += 1
    elif team_volume_only_proj_info["projection"] is None:
        core_exclusion_reasons[f"TEAM_VOLUME_ONLY_{team_volume_only_proj_info['reason']}"] += 1
    else:
        core_b0_errors.append(abs(b0_projection - realized))
        core_team_volume_only_errors.append(abs(team_volume_only_proj_info["projection"] - realized))
        core_full_engine_errors.append(abs(full_engine_proj_info["projection"] - realized))
        core_matched_n += 1
        if len(sample_records) < 5:
            sample_records.append({
                "player_id": player_id, "team": team, "season": season, "week": week,
                "realized_receptions": realized, "b0_projection": b0_projection,
                "team_volume_only_projection": team_volume_only_proj_info["projection"],
                "full_engine_projection": full_engine_proj_info["projection"],
                "team_dropbacks_plain": team_info["predicted_dropbacks"],
                "naive_target_share": naive_share, "naive_catch_rate": naive_rate,
                "shrunk_target_share": share_info["estimate"], "shrunk_catch_rate": rate_info["estimate"],
            })

    # --- Group 2: coaching ablation (player side held fixed at the shrunk estimates) ---
    naive_control_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=coaching_info["predicted_dropbacks_naive_control"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    if full_engine_proj_info["projection"] is not None and naive_control_proj_info["projection"] is not None:
        coaching_aware_errors.append(abs(full_engine_proj_info["projection"] - realized))
        naive_control_errors.append(abs(naive_control_proj_info["projection"] - realized))
        coaching_matched_n += 1

    # --- Group 3: snap-share ablation (plain team dropbacks + shrunk share/rate, per the
    # established 2025 evaluation's own comparison shape) ---
    snap_info = estimate_current_week_snap_share(
        player_id=player_id, snap_share_history=snap_share_history.get(player_id, []),
        target_season=season, target_week=week,
    )
    universal_share_info = apply_snap_informed_target_share(
        target_share_info=share_info, snap_share_info=snap_info,
    )
    snap_unadjusted_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_info["predicted_dropbacks"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    snap_universal_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_info["predicted_dropbacks"],
        target_share=universal_share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    gated_proj_infos: dict[str, dict] = {}
    gated_share_infos: dict[str, dict] = {}
    for g in GATE_THRESHOLDS:
        gated_share_info = gated_snap_informed_target_share(
            target_share_info=share_info, snap_share_info=snap_info,
            gate_low=g["gate_low"], gate_high=g["gate_high"],
        )
        gated_share_infos[g["label"]] = gated_share_info
        gated_proj_infos[g["label"]] = compute_opportunity_projection(
            predicted_team_dropbacks=team_info["predicted_dropbacks"],
            target_share=gated_share_info["estimate"], catch_rate=rate_info["estimate"],
        )

    all_snap_ok = (
        snap_unadjusted_proj_info["projection"] is not None
        and snap_universal_proj_info["projection"] is not None
        and all(gated_proj_infos[g["label"]]["projection"] is not None for g in GATE_THRESHOLDS)
    )
    if all_snap_ok:
        snap_unadjusted_errors.append(abs(snap_unadjusted_proj_info["projection"] - realized))
        snap_universal_errors.append(abs(snap_universal_proj_info["projection"] - realized))
        snap_matched_n += 1
        if universal_share_info.get("snap_role_change_applied"):
            snap_universal_applied_n += 1
        for g in GATE_THRESHOLDS:
            label = g["label"]
            snap_gated_errors[label].append(abs(gated_proj_infos[label]["projection"] - realized))
            if gated_share_infos[label].get("gate_triggered"):
                snap_gated_in_n[label] += 1
                if len(gated_change_samples[label]) < 5:
                    gated_change_samples[label].append({
                        "player_id": player_id, "team": team, "season": season, "week": week,
                        "realized_receptions": realized,
                        "gate_raw_ratio": gated_share_infos[label]["gate_raw_ratio"],
                        "unadjusted_target_share": share_info["estimate"],
                        "gated_target_share": gated_share_infos[label]["estimate"],
                        "unadjusted_projection": snap_unadjusted_proj_info["projection"],
                        "gated_projection": gated_proj_infos[label]["projection"],
                    })


def _mae(errors: list[float]) -> float | None:
    return (sum(errors) / len(errors)) if errors else None


report = {
    "analysis": "NFL_RECEPTIONS_OPPORTUNITY_ENGINE_COMPONENT_ABLATION_2024_HOLDOUT",
    "status": "HISTORICAL_OPERATIONAL_TESTING_NOT_PROSPECTIVE_FRESH_2024_HOLDOUT_NEVER_PREVIOUSLY_EVALUATED",
    "workstream_id": "NFL-OPPORTUNITY-ABLATION-2024-HOLDOUT-20260923",
    "note": (
        "The 2025 season (weeks 8+) has been the test population for every prior "
        "evaluation of this challenger (Missions 3, 4, 6) and is therefore no longer "
        "a fair holdout for a new hypothesis about the same features. This script "
        "uses the 2024 season (weeks 8+) instead -- genuinely never inspected by any "
        "prior evaluation in this repository -- per this project's own anti-retuning "
        "doctrine. Numbers here are a real, different population from the established "
        "2025 findings and must not be conflated with them."
    ),
    "eval_season": EVAL_SEASON,
    "eval_min_week": EVAL_MIN_WEEK,
    "eligible_eval_rows_considered": eligible_rows_considered,
    "team_offense_rows_excluded_for_negative_value_bug": len(team_offense_excluded),
    "core_ablation": {
        "note": (
            "B0 vs. a team-volume-only baseline (real team dropbacks x naive, unshrunk "
            "recent-average target share/catch rate) vs. the full current opportunity "
            "engine (coaching-aware dropbacks x shrinkage-blended share/rate), all on "
            "the SAME matched row set."
        ),
        "matched_n": core_matched_n,
        "b0_mae": _mae(core_b0_errors),
        "team_volume_only_naive_share_mae": _mae(core_team_volume_only_errors),
        "full_opportunity_engine_mae": _mae(core_full_engine_errors),
        "exclusion_reason_counts": dict(core_exclusion_reasons),
    },
    "coaching_ablation": {
        "note": (
            "Real coaching-aware team-dropback prediction (the value the full engine "
            "actually uses) vs. the naive-control (unfiltered) team-dropback "
            "prediction, player-side inputs held fixed at the shrunk share/rate."
        ),
        "hc_registry_source": dict(HC_GAMES_SOURCE),
        "real_hc_regime_intervals_loaded": len(hc_intervals),
        "regime_lookup_status_counts": dict(regime_lookup_status_counts),
        "rows_where_coaching_feature_changed_the_projection": coaching_changed_projection_n,
        "rows_where_coaching_feature_changed_the_projection_pct": (
            coaching_changed_projection_n / eligible_rows_considered if eligible_rows_considered else None
        ),
        "matched_n": coaching_matched_n,
        "coaching_aware_mae": _mae(coaching_aware_errors),
        "naive_control_mae": _mae(naive_control_errors),
    },
    "snap_share_ablation": {
        "note": (
            "Real plain team dropbacks x shrunk target share/catch rate as the "
            "'unadjusted' control, compared against the UNIVERSAL snap-share "
            "adjustment (current merged form, no gate) and two GATED thresholds "
            "(the new hypothesis under test) -- all on the same matched row set, "
            "since a gated row that doesn't trigger falls back to the identical "
            "unadjusted projection by construction."
        ),
        "matched_n": snap_matched_n,
        "unadjusted_mae": _mae(snap_unadjusted_errors),
        "universal_adjusted_mae": _mae(snap_universal_errors),
        "rows_with_universal_adjustment_applied": snap_universal_applied_n,
        "gated_thresholds": [
            {
                "label": g["label"],
                "gate_low": g["gate_low"],
                "gate_high": g["gate_high"],
                "mae": _mae(snap_gated_errors[g["label"]]),
                "rows_gated_in": snap_gated_in_n[g["label"]],
                "rows_left_unadjusted": snap_matched_n - snap_gated_in_n[g["label"]],
                "real_gated_in_examples": gated_change_samples[g["label"]],
            }
            for g in GATE_THRESHOLDS
        ],
    },
    "sample_records": sample_records,
    "generated_in_seconds": time.time() - t0,
}

print(json.dumps(report, indent=2, sort_keys=True, default=str))
out_path = Path(__file__).resolve().parent / "component_ablation_2024_report.json"
with open(out_path, "w") as f:
    json.dump(report, f, indent=2, sort_keys=True, default=str)
print(f"DONE in {time.time()-t0:.1f}s -> {out_path}", flush=True)
