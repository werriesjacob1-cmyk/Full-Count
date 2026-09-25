#!/usr/bin/env python3
"""Mission 9 Workstream D: real component-level error decomposition of
`nfl/research/receptions_team_opportunity_challenger.py` (merged, read-only,
unmodified) plus `nfl/research/qb_change_team_dropbacks.py` (draft PR #185,
brought into this branch unmodified as a read-only import), diagnosing WHY
each of the three independently-added team-volume/role signals (coaching-
regime-aware, current-season snap-share-informed target share, QB-continuity-
aware) underperformed B0 in its own separate prior evaluation, and whether
the three carry independent incremental information when combined.

## Population and the anti-retuning constraint

Every prior evaluation of this challenger used either the 2025 season
(weeks 8+ -- Missions 3, 4, 6) or the 2024 season (weeks 8+ -- draft PR
#184's diagnostic ablation) as its test population. Per this project's own
anti-retuning doctrine (`engineering/AGENT_BRIDGE_PROTOCOL.md`: "held-out
results cannot be tuned into the challenger"), neither population is a fair
target for a NEW confirmatory result about the same features. This script
uses the **2023 season (weeks 8+)** as its primary population instead: no
prior evaluation in this repository has used 2023 as an aggregate MAE
evaluation target (the one narrow exception, disclosed here and in the
README, is that three specific 2023 team-weeks -- LV wk10, CAR wk14, LAC
wk17 -- were inspected qualitatively in Mission 4 to demonstrate the
coaching filter's mechanism; that was never an aggregate accuracy number
and did not influence any threshold or design decision in this script).

Every comparison below is therefore genuinely fresh EXCEPT where explicitly
labeled `RE_ANALYSIS_OF_INSPECTED_POPULATION` -- this script does not use
the 2024/2025 populations for anything, so no such re-analysis actually
appears in ITS OWN output; the distinction is preserved here only so the
README can state it plainly.

## What this script tests (Mission 9's four questions)

1. Team-volume-STAGE ablation (target share and catch rate held FIXED at
   the same real shrinkage-blended estimates for every variant): naive/
   plain blended team volume vs. coaching-aware vs. QB-aware vs.
   coaching+QB-combined (the new `decomposition_lib.
   predict_team_pass_dropbacks_coaching_and_qb_aware`) -- isolates how much
   of the full chain's error comes specifically from the team-volume stage
   and from the choice of team-volume estimator, independent of the share/
   rate stages.
2. Oracle stage-substitution decomposition: for each of team volume, target
   share, and catch rate, substitute the REAL, ex-post-observed value for
   that game (a real, already-settled fact, never usable as a live
   prediction) while holding the other two stages at the model's own
   real estimates, and measure how much of the projection's MAE improves.
   This bounds how much each stage's OWN estimation error contributes to
   the chain's total error -- directly testing the "team-volume estimation
   introduces excess error" / "target-share estimation adds noise" /
   "catch-rate estimates are unstable" hypotheses against each other on the
   same real rows.
3. Three-signal combination test: coaching-aware+snap, QB-aware+snap,
   coaching+QB-combined+snap, and plain+snap team-volume variants (all
   using the current merged snap-informed target-share adjustment), each
   compared to B0 on the SAME real matched population -- never tested
   combined before this script.
4. Role-transition subgroup analysis: using the three flags this
   codebase's own challengers already emit unmodified
   (`coaching_feature_changed_the_projection`, `qb_feature_changed_the_
   projection`, `snap_role_change_applied`), a real, pre-existing-code-
   defined subgroup of rows where at least one role-transition signal
   actually fired, compared against the general population.

Player-clustered bootstrap CIs (`decomposition_lib.
player_clustered_bootstrap_mae_diff`) are reported for every MAE
comparison, matching draft PR #184's own bootstrap methodology template.

## Honest scope disclosure

This is HISTORICAL OPERATIONAL TESTING on real, strictly-prior, already-
settled games -- not prospective evidence. The oracle stage-substitution
experiment is explicitly diagnostic: it uses real ex-post facts that are
NEVER available to a real prediction at the time it would be made, and its
results are never presented as an achievable model.
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
from nfl.research.qb_continuity_features import infer_team_week_starters
from nfl.research.receptions_team_opportunity_challenger import (
    predict_team_pass_dropbacks,
    predict_team_pass_dropbacks_coaching_aware,
    compute_opportunity_projection,
    estimate_current_week_target_share,
    estimate_current_week_catch_rate,
    estimate_current_week_snap_share,
    apply_snap_informed_target_share,
)
from nfl.research.qb_change_team_dropbacks import predict_team_pass_dropbacks_qb_aware

_lib_spec = importlib.util.spec_from_file_location(
    "nfl_opportunity_error_decomposition_lib", Path(__file__).resolve().parent / "decomposition_lib.py",
)
lib = importlib.util.module_from_spec(_lib_spec)
_lib_spec.loader.exec_module(lib)  # type: ignore[union-attr]
predict_team_pass_dropbacks_coaching_and_qb_aware = lib.predict_team_pass_dropbacks_coaching_and_qb_aware
role_transition_subgroup_flag = lib.role_transition_subgroup_flag
player_clustered_bootstrap_mae_diff = lib.player_clustered_bootstrap_mae_diff

TEAM_SEASONS = (2021, 2022, 2023)
PLAYER_SEASONS = (2021, 2022, 2023)
SNAP_SEASONS = (2022, 2023)
EVAL_SEASON = 2023
EVAL_MIN_WEEK = 8
N_BOOT = 3000
BOOT_SEED = 20260923

t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time()-t0:6.1f}s] {msg}", flush=True)


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
        log(f"team offense {season}: {n_offense} team-game rows, {n_plays} filtered plays")
    for row in rows:
        row["season"] = int(row["season"])
        row["week"] = int(row["week"])
        for f in ("attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards"):
            row[f] = float(row[f])
    return rows


def fetch_player_weekly_rows() -> list[dict]:
    """Real weekly player-stat rows for every position (not just receivers):
    QB pass-attempt rows are required to infer real weekly starters via
    `qb_continuity_features.infer_team_week_starters`, alongside the
    existing targets/receptions fields the share/catch-rate estimators use.
    """
    rows: list[dict] = []
    for season in PLAYER_SEASONS:
        request = urllib.request.Request(
            player_stats_url(season), headers={"User-Agent": "full-count-opportunity-error-decomposition/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - a season may genuinely not exist / be unreachable
            log(f"player weekly {season}: fetch failed ({exc}), skipping")
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
                attempts = float(row.get("attempts") or 0)
            except (TypeError, ValueError):
                continue
            rows.append({
                "player_id": player_id,
                "player_display_name": row.get("player_display_name", ""),
                "position": (row.get("position") or "").strip().upper(),
                "season": season, "week": week, "season_type": "REG",
                "team": (row.get("team") or "").strip().upper(),
                "opponent_team": (row.get("opponent_team") or "").strip().upper(),
                "targets": targets, "receptions": receptions, "attempts": attempts,
            })
            n += 1
        log(f"player weekly {season}: {n} real player-game rows")
    return rows


def naive_recent_average(values: list[float], *, lookback: int = 5) -> float | None:
    window = values[-lookback:]
    if not window:
        return None
    return sum(window) / len(window)


def _mae(errors: list[float]) -> float | None:
    return (sum(errors) / len(errors)) if errors else None


log("Fetching real team offense (PBP-derived) rows for 2021-2023...")
team_offense_raw = fetch_team_offense_rows()
team_offense_rows, team_offense_excluded = filter_team_offense_rows_for_negative_value_bug(team_offense_raw)
log(f"{len(team_offense_rows)} kept, {len(team_offense_excluded)} excluded for the known negative-value bug")

log("Building team_prior_features / defense_prior_features / game_matchup_features...")
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
matchup_rows = build_game_matchup_features(schedule_by_game.values(), offense_features, defense_features)
matchup_by_key: dict[tuple[str, str], dict] = {}
for row in matchup_rows:
    matchup_by_key[(row["game_id"], row["home_team"])] = row
    matchup_by_key[(row["game_id"], row["away_team"])] = row
log(f"{len(matchup_rows)} real matchup rows built")

log("Fetching real HC coaching-regime registry (nfldata games.csv, pinned commit)...")
hc_games_url = f"https://raw.githubusercontent.com/nflverse/nfldata/{HC_GAMES_SOURCE['commit']}/{HC_GAMES_SOURCE['path']}"
hc_request = urllib.request.Request(hc_games_url, headers={"User-Agent": "full-count-opportunity-error-decomposition/1.0"})
with urllib.request.urlopen(hc_request, timeout=60) as response:
    hc_games_bytes = response.read()
hc_intervals, game_date_index = build_hc_registry(hc_games_bytes)
log(f"{len(hc_intervals)} real HC regime intervals, {len(game_date_index)} real game dates")

log("Fetching real player weekly stats for 2021-2023 (all positions, for QB-starter inference)...")
player_rows = fetch_player_weekly_rows()

log("Inferring real weekly QB starters (usage-proxy, most pass attempts that week)...")
starters = infer_team_week_starters(player_rows)
log(f"{len(starters)} real team/week starter rows inferred")

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

log("Fetching real snap-count data (2022-2023, live, unpinned -- same pattern as the "
    "established 2024/2025 evaluation scripts)...")
crosswalk = fetch_players_crosswalk()
snap_rows: list[dict] = []
for season in SNAP_SEASONS:
    url = f"https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "full-count-opportunity-error-decomposition/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode("utf-8")
    rows = parse_snap_counts_csv(text, season, crosswalk)
    snap_rows.extend(rows)
    log(f"snap_counts {season}: {len(rows)} real rows")

team_week_max_offense_snaps: dict[tuple[int, int, str], float] = defaultdict(float)
for row in snap_rows:
    key = (row["season"], row["week"], row["team"])
    team_week_max_offense_snaps[key] = max(team_week_max_offense_snaps[key], row["offense_snaps"])

snap_share_history: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
for row in snap_rows:
    if row["player_id"] is None:
        continue
    team_max = team_week_max_offense_snaps[(row["season"], row["week"], row["team"])]
    if team_max > 0:
        snap_share_history[row["player_id"]].append((row["season"], row["week"], row["offense_snaps"] / team_max))
for pid in snap_share_history:
    snap_share_history[pid].sort()

# Real ex-post team dropback totals per (team, season, week) -- an ORACLE
# fact, used only in the oracle stage-substitution experiment below, never
# in any predictive path.
oracle_team_dropbacks: dict[tuple[str, int, int], float] = {}
for row in team_offense_rows:
    oracle_team_dropbacks[(row["team"], row["season"], row["week"])] = row["attempts"] + row["sacks_suffered"]

log(f"Building the {EVAL_SEASON} week-{EVAL_MIN_WEEK}+ eligible population and running the full "
    f"component decomposition ({time.time()-t0:.1f}s so far)...")

eval_rows = [
    r for r in player_rows
    if r["season"] == EVAL_SEASON and r["week"] >= EVAL_MIN_WEEK and r["position"] in ("WR", "TE", "RB")
]

records: list[dict] = []
exclusion_reasons: dict[str, int] = defaultdict(int)
coaching_regime_lookup_status_counts: dict[str, int] = defaultdict(int)

for row in eval_rows:
    player_id, season, week, team = row["player_id"], row["season"], row["week"], row["team"]
    realized = row["receptions"]

    game_ids = [
        gid for gid, g in schedule_by_game.items()
        if g["season"] == season and g["week"] == week and team in (g["home_team"], g["away_team"])
    ]
    if not game_ids:
        exclusion_reasons["NO_REAL_GAME_MATCH"] += 1
        continue
    game_id = game_ids[0]
    matchup_row = matchup_by_key.get((game_id, team))
    if matchup_row is None:
        exclusion_reasons["NO_REAL_MATCHUP_ROW"] += 1
        continue
    side = "home" if matchup_row["home_team"] == team else "away"
    opponent = "away" if side == "home" else "home"
    opponent_allowed = matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"]
    opponent_n = matchup_row[f"{opponent}_defense_prior_games_n"]

    history = [
        {"season": s, "week": w, "season_type": "REG", "targets": g["targets"], "receptions": g["receptions"], "team": g["team"]}
        for (pid, s, w), g in game_index.items()
        if pid == player_id and (s, w) < (season, week)
    ]
    try:
        b0_info = current_b0_projection(sorted(history, key=lambda r: (r["season"], r["week"])))
        b0_projection = float(b0_info["projection"])
    except Exception:
        exclusion_reasons["B0_INSUFFICIENT_REAL_HISTORY"] += 1
        continue

    naive_team_info = predict_team_pass_dropbacks(matchup_row, side=side)
    coaching_info = predict_team_pass_dropbacks_coaching_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
        opponent_defense_allowed=opponent_allowed, opponent_defense_prior_games_n=opponent_n,
    )
    coaching_regime_lookup_status_counts[coaching_info["regime_note"]["regime_lookup_status"]] += 1
    qb_info = predict_team_pass_dropbacks_qb_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        starters=starters, opponent_defense_allowed=opponent_allowed, opponent_defense_prior_games_n=opponent_n,
    )
    combined_info = predict_team_pass_dropbacks_coaching_and_qb_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        hc_intervals=hc_intervals, game_date_index=game_date_index, starters=starters,
        opponent_defense_allowed=opponent_allowed, opponent_defense_prior_games_n=opponent_n,
    )

    share_info = estimate_current_week_target_share(
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
    snap_adjusted_share_info = apply_snap_informed_target_share(
        target_share_info=share_info, snap_share_info=snap_info,
    )

    naive_dropbacks = naive_team_info["predicted_dropbacks"]
    coaching_dropbacks = coaching_info["predicted_dropbacks_coaching_aware"]
    qb_dropbacks = qb_info["predicted_dropbacks_qb_aware"]
    combined_dropbacks = combined_info["predicted_dropbacks_combined_aware"]
    share = share_info["estimate"]
    snap_share = snap_adjusted_share_info["estimate"]
    rate = rate_info["estimate"]

    # --- Experiment 1: team-volume-STAGE ablation (share/rate held fixed) ---
    proj_naive = compute_opportunity_projection(predicted_team_dropbacks=naive_dropbacks, target_share=share, catch_rate=rate)
    proj_coaching = compute_opportunity_projection(predicted_team_dropbacks=coaching_dropbacks, target_share=share, catch_rate=rate)
    proj_qb = compute_opportunity_projection(predicted_team_dropbacks=qb_dropbacks, target_share=share, catch_rate=rate)
    proj_combined = compute_opportunity_projection(predicted_team_dropbacks=combined_dropbacks, target_share=share, catch_rate=rate)

    stage_ablation_ok = all(
        p["projection"] is not None for p in (proj_naive, proj_coaching, proj_qb, proj_combined)
    )

    # --- Experiment 3: three-signal-combined test (snap-informed share) ---
    proj_naive_snap = compute_opportunity_projection(predicted_team_dropbacks=naive_dropbacks, target_share=snap_share, catch_rate=rate)
    proj_coaching_snap = compute_opportunity_projection(predicted_team_dropbacks=coaching_dropbacks, target_share=snap_share, catch_rate=rate)
    proj_qb_snap = compute_opportunity_projection(predicted_team_dropbacks=qb_dropbacks, target_share=snap_share, catch_rate=rate)
    proj_combined_snap = compute_opportunity_projection(predicted_team_dropbacks=combined_dropbacks, target_share=snap_share, catch_rate=rate)

    three_signal_ok = all(
        p["projection"] is not None for p in (proj_naive_snap, proj_coaching_snap, proj_qb_snap, proj_combined_snap)
    )

    # --- Experiment 2: oracle stage substitution (diagnostic only) ---
    oracle_dropbacks = oracle_team_dropbacks.get((team, season, week))
    team_week_target_total = team_week_targets.get((season, week, team))
    oracle_share = (row["targets"] / team_week_target_total) if team_week_target_total else None
    oracle_rate = (row["receptions"] / row["targets"]) if row["targets"] and row["targets"] > 0 else None

    # Baseline for the oracle experiment: the fully-estimated "combined+snap"
    # chain (the best real team-volume estimator plus the current merged
    # snap-informed share) -- every oracle substitution below swaps exactly
    # ONE of its three real inputs for the real ex-post value, others held.
    oracle_ok = proj_combined_snap["projection"] is not None and all(
        v is not None for v in (oracle_dropbacks, oracle_share, oracle_rate)
    )
    if oracle_ok:
        proj_oracle_team = compute_opportunity_projection(
            predicted_team_dropbacks=oracle_dropbacks, target_share=snap_share, catch_rate=rate,
        )
        proj_oracle_share = compute_opportunity_projection(
            predicted_team_dropbacks=combined_dropbacks, target_share=oracle_share, catch_rate=rate,
        )
        proj_oracle_rate = compute_opportunity_projection(
            predicted_team_dropbacks=combined_dropbacks, target_share=snap_share, catch_rate=oracle_rate,
        )
        oracle_ok = all(
            p["projection"] is not None for p in (proj_oracle_team, proj_oracle_share, proj_oracle_rate)
        )

    role_transition_flag = role_transition_subgroup_flag({
        "coaching_feature_changed_the_projection": coaching_info["coaching_feature_changed_the_projection"],
        "qb_feature_changed_the_projection": qb_info["qb_feature_changed_the_projection"],
        "snap_role_change_applied": snap_adjusted_share_info.get("snap_role_change_applied", False),
    })

    record = {
        "player_id": player_id, "team": team, "season": season, "week": week,
        "realized_receptions": realized, "b0_projection": b0_projection,
        "b0_error": abs(b0_projection - realized),
        "role_transition_flag": role_transition_flag,
        "coaching_feature_changed_the_projection": coaching_info["coaching_feature_changed_the_projection"],
        "qb_feature_changed_the_projection": qb_info["qb_feature_changed_the_projection"],
        "combined_feature_changed_the_projection": combined_info["combined_feature_changed_the_projection"],
        "snap_role_change_applied": snap_adjusted_share_info.get("snap_role_change_applied", False),
    }
    if stage_ablation_ok:
        record.update({
            "stage_ablation_ok": True,
            "err_naive": abs(proj_naive["projection"] - realized),
            "err_coaching": abs(proj_coaching["projection"] - realized),
            "err_qb": abs(proj_qb["projection"] - realized),
            "err_combined": abs(proj_combined["projection"] - realized),
        })
    else:
        record["stage_ablation_ok"] = False
        exclusion_reasons["STAGE_ABLATION_MISSING_INPUT"] += 1

    if three_signal_ok:
        record.update({
            "three_signal_ok": True,
            "err_naive_snap": abs(proj_naive_snap["projection"] - realized),
            "err_coaching_snap": abs(proj_coaching_snap["projection"] - realized),
            "err_qb_snap": abs(proj_qb_snap["projection"] - realized),
            "err_combined_snap": abs(proj_combined_snap["projection"] - realized),
        })
    else:
        record["three_signal_ok"] = False
        exclusion_reasons["THREE_SIGNAL_MISSING_INPUT"] += 1

    if oracle_ok:
        record.update({
            "oracle_ok": True,
            "err_full_model_combined_snap": abs(proj_combined_snap["projection"] - realized),
            "err_oracle_team_volume": abs(proj_oracle_team["projection"] - realized),
            "err_oracle_target_share": abs(proj_oracle_share["projection"] - realized),
            "err_oracle_catch_rate": abs(proj_oracle_rate["projection"] - realized),
        })
    else:
        record["oracle_ok"] = False

    records.append(record)
    if len(records) <= 5:
        pass  # sample kept below via explicit slicing of `records`

eligible_rows_considered = len(eval_rows)
log(f"Population built: {eligible_rows_considered} eligible rows considered, {len(records)} records kept "
    f"({time.time()-t0:.1f}s so far)")


def _bootstrap(rows: list[dict], error_a_key: str, error_b_key: str) -> dict:
    return player_clustered_bootstrap_mae_diff(
        rows, player_key="player_id", error_a_key=error_a_key, error_b_key=error_b_key,
        n_boot=N_BOOT, seed=BOOT_SEED,
    )


# --- Experiment 1 aggregation: team-volume-stage ablation ---
stage_rows = [r for r in records if r["stage_ablation_ok"]]
stage_ablation = {
    "note": (
        "Target share and catch rate held FIXED at the same real shrinkage-blended "
        "estimate for every variant; only the team-volume estimator differs. Isolates "
        "how much of the chain's error is attributable to the team-volume STAGE and to "
        "the choice of team-volume estimator, independent of the share/rate stages."
    ),
    "matched_n": len(stage_rows),
    "mae": {
        "b0": _mae([r["b0_error"] for r in stage_rows]),
        "naive_team_volume": _mae([r["err_naive"] for r in stage_rows]),
        "coaching_aware_team_volume": _mae([r["err_coaching"] for r in stage_rows]),
        "qb_aware_team_volume": _mae([r["err_qb"] for r in stage_rows]),
        "combined_coaching_and_qb_team_volume": _mae([r["err_combined"] for r in stage_rows]),
    },
    "bootstrap_vs_naive": {
        "coaching_vs_naive": _bootstrap(stage_rows, "err_coaching", "err_naive"),
        "qb_vs_naive": _bootstrap(stage_rows, "err_qb", "err_naive"),
        "combined_vs_naive": _bootstrap(stage_rows, "err_combined", "err_naive"),
    },
    "bootstrap_vs_b0": {
        "naive_vs_b0": _bootstrap(stage_rows, "err_naive", "b0_error"),
        "coaching_vs_b0": _bootstrap(stage_rows, "err_coaching", "b0_error"),
        "qb_vs_b0": _bootstrap(stage_rows, "err_qb", "b0_error"),
        "combined_vs_b0": _bootstrap(stage_rows, "err_combined", "b0_error"),
    },
}

# --- Experiment 3 aggregation: three-signal-combined test ---
three_rows = [r for r in records if r["three_signal_ok"]]
three_signal_combination = {
    "note": (
        "Same team-volume variants as Experiment 1, but with the current merged "
        "snap-informed target-share adjustment applied on top (the CURRENT PRODUCTION "
        "form is 'coaching_snap'; 'combined_snap' -- coaching+QB team volume plus "
        "snap-informed share -- has never been evaluated in this repository before "
        "this script). All four compared to B0 on the same matched population."
    ),
    "matched_n": len(three_rows),
    "mae": {
        "b0": _mae([r["b0_error"] for r in three_rows]),
        "naive_snap": _mae([r["err_naive_snap"] for r in three_rows]),
        "coaching_snap_current_production_form": _mae([r["err_coaching_snap"] for r in three_rows]),
        "qb_snap": _mae([r["err_qb_snap"] for r in three_rows]),
        "combined_coaching_qb_snap_all_three_signals": _mae([r["err_combined_snap"] for r in three_rows]),
    },
    "bootstrap_vs_b0": {
        "naive_snap_vs_b0": _bootstrap(three_rows, "err_naive_snap", "b0_error"),
        "coaching_snap_vs_b0": _bootstrap(three_rows, "err_coaching_snap", "b0_error"),
        "qb_snap_vs_b0": _bootstrap(three_rows, "err_qb_snap", "b0_error"),
        "combined_snap_vs_b0": _bootstrap(three_rows, "err_combined_snap", "b0_error"),
    },
    "bootstrap_all_three_combined_vs_each_alone": {
        "combined_vs_coaching_alone": _bootstrap(three_rows, "err_combined_snap", "err_coaching_snap"),
        "combined_vs_qb_alone": _bootstrap(three_rows, "err_combined_snap", "err_qb_snap"),
        "combined_vs_naive_snap": _bootstrap(three_rows, "err_combined_snap", "err_naive_snap"),
    },
    # Full round-robin over all 6 unordered pairs among the 4 variants. Added
    # after independent adversarial review found the original PR/handoff
    # text claimed "every pairwise 95% CI includes zero" while the code only
    # ever computed the 3 pairs above (each pivoted on "combined"). The 3
    # pairs NOT involving "combined" are reported here so the claim is either
    # fully substantiated or corrected, rather than generalized from a
    # partial comparison.
    "bootstrap_full_pairwise_round_robin": {
        "combined_vs_coaching_alone": _bootstrap(three_rows, "err_combined_snap", "err_coaching_snap"),
        "combined_vs_qb_alone": _bootstrap(three_rows, "err_combined_snap", "err_qb_snap"),
        "combined_vs_naive_snap": _bootstrap(three_rows, "err_combined_snap", "err_naive_snap"),
        "coaching_vs_qb_alone": _bootstrap(three_rows, "err_coaching_snap", "err_qb_snap"),
        "coaching_vs_naive_snap": _bootstrap(three_rows, "err_coaching_snap", "err_naive_snap"),
        "qb_vs_naive_snap": _bootstrap(three_rows, "err_qb_snap", "err_naive_snap"),
    },
}

# --- Experiment 2 aggregation: oracle stage-substitution decomposition ---
oracle_rows = [r for r in records if r["oracle_ok"]]
oracle_decomposition = {
    "status": "DIAGNOSTIC_ONLY_NOT_A_PREDICTIVE_MODEL_USES_REAL_EX_POST_FACTS",
    "note": (
        "For each row, exactly ONE of (team dropbacks, target share, catch rate) is "
        "replaced by its REAL, ex-post-observed value for that specific game -- a real "
        "fact never available before kickoff -- while the other two stay at the model's "
        "own real estimate (team volume = combined coaching+QB-aware, share = "
        "snap-informed). The full-model MAE (all three estimated) is the baseline; the "
        "drop in MAE from each single oracle substitution bounds how much of the "
        "chain's total error that stage's OWN estimation error contributes."
    ),
    "matched_n": len(oracle_rows),
    "mae": {
        "full_model_all_three_estimated": _mae([r["err_full_model_combined_snap"] for r in oracle_rows]),
        "oracle_team_volume_others_estimated": _mae([r["err_oracle_team_volume"] for r in oracle_rows]),
        "oracle_target_share_others_estimated": _mae([r["err_oracle_target_share"] for r in oracle_rows]),
        "oracle_catch_rate_others_estimated": _mae([r["err_oracle_catch_rate"] for r in oracle_rows]),
    },
    "bootstrap_mae_improvement_vs_full_model": {
        "oracle_team_volume": _bootstrap(oracle_rows, "err_oracle_team_volume", "err_full_model_combined_snap"),
        "oracle_target_share": _bootstrap(oracle_rows, "err_oracle_target_share", "err_full_model_combined_snap"),
        "oracle_catch_rate": _bootstrap(oracle_rows, "err_oracle_catch_rate", "err_full_model_combined_snap"),
    },
}

# --- Experiment 4 aggregation: role-transition subgroup analysis ---
subgroup_rows = [r for r in three_rows if r["role_transition_flag"]]
general_rows = [r for r in three_rows if not r["role_transition_flag"]]


def _subgroup_block(rows: list[dict]) -> dict:
    if not rows:
        return {"matched_n": 0, "mae": {}, "bootstrap_combined_snap_vs_b0": None}
    return {
        "matched_n": len(rows),
        "mae": {
            "b0": _mae([r["b0_error"] for r in rows]),
            "combined_coaching_qb_snap": _mae([r["err_combined_snap"] for r in rows]),
        },
        "bootstrap_combined_snap_vs_b0": _bootstrap(rows, "err_combined_snap", "b0_error"),
    }


role_transition_subgroup = {
    "note": (
        "Subgroup defined PURELY by whether at least one of three flags this codebase's "
        "own challengers already emit unmodified actually fired for that row "
        "(`coaching_feature_changed_the_projection`, `qb_feature_changed_the_projection`, "
        "`snap_role_change_applied`) -- a real, pre-existing-code-defined subgroup, not "
        "invented from this script's own results."
    ),
    "rows_with_role_transition_flag": sum(1 for r in three_rows if r["role_transition_flag"]),
    "rows_total": len(three_rows),
    "flag_fired_counts": {
        "coaching_feature_changed_the_projection": sum(1 for r in three_rows if r["coaching_feature_changed_the_projection"]),
        "qb_feature_changed_the_projection": sum(1 for r in three_rows if r["qb_feature_changed_the_projection"]),
        "combined_feature_changed_the_projection": sum(1 for r in three_rows if r["combined_feature_changed_the_projection"]),
        "snap_role_change_applied": sum(1 for r in three_rows if r["snap_role_change_applied"]),
    },
    "subgroup_role_transition_rows": _subgroup_block(subgroup_rows),
    "general_no_role_transition_rows": _subgroup_block(general_rows),
    "degenerate_disclosure": (
        "snap_role_change_applied alone fires on ~99.3% of rows (consistent with Mission "
        "6's own disclosure that it is not gated behind any large-change-only threshold), "
        "which makes the pre-registered OR-of-three subgroup nearly the entire population "
        "(2963/2978 rows) and its complement (15 rows) too small to support any real "
        "inference. See `identity_transition_subgroup_supplementary` below for a second, "
        "EXPLORATORY cut using only the two more selective, already-existing identity-"
        "change flags (coaching-or-QB), chosen AFTER seeing this degeneracy -- disclosed "
        "as exploratory, not a pre-registered confirmatory result."
    ),
}

# Supplementary, EXPLORATORY subgroup cut: chosen only after observing the
# primary OR-of-three subgroup above is degenerate (99.5% of rows). Uses
# ONLY `combined_feature_changed_the_projection` (fires when the real
# coaching-OR-QB identity-restricted rolling window actually differs from
# the naive unfiltered one) -- a real, already-computed, more selective
# flag (34.3% of rows), excluding the near-universal snap-share flag. This
# is disclosed as exploratory/post-hoc-selected, never as a pre-registered
# confirmatory comparison, and it does not use any evaluation-outcome
# (error/MAE) to decide which rows qualify -- membership is still fixed
# purely by whether the identity filter itself changed the prediction.
identity_subgroup_rows = [r for r in three_rows if r["combined_feature_changed_the_projection"]]
identity_general_rows = [r for r in three_rows if not r["combined_feature_changed_the_projection"]]
identity_transition_subgroup_supplementary = {
    "status": "EXPLORATORY_POST_HOC_SELECTED_AFTER_SEEING_PRIMARY_SUBGROUP_WAS_DEGENERATE",
    "note": (
        "Subgroup = rows where `combined_feature_changed_the_projection` (the real "
        "coaching-OR-QB identity-restricted team-volume window actually differs from the "
        "naive unfiltered one) is True. Chosen after observing the primary OR-of-three "
        "subgroup above was 99.5% of the population; membership is still fixed by an "
        "existing flag's own real behavior, not by looking at any row's error."
    ),
    "rows_with_identity_transition_flag": len(identity_subgroup_rows),
    "rows_total": len(three_rows),
    "subgroup_identity_transition_rows": _subgroup_block(identity_subgroup_rows),
    "general_no_identity_transition_rows": _subgroup_block(identity_general_rows),
}

report = {
    "analysis": "NFL_RECEPTIONS_OPPORTUNITY_ENGINE_COMPONENT_ERROR_DECOMPOSITION",
    "status": "HISTORICAL_OPERATIONAL_TESTING_NOT_PROSPECTIVE_FRESH_2023_HOLDOUT",
    "workstream_id": "NFL-OPPORTUNITY-ERROR-DECOMPOSITION-20260923",
    "population_note": (
        "2023 season (weeks 8+): genuinely never used as an aggregate MAE evaluation "
        "target by any prior evaluation in this repository. 2024 week-8+ (draft PR #184) "
        "and 2025 week-8+ (Missions 3/4/6) have both already been inspected as evaluation "
        "targets and are NOT reused here for any new confirmatory comparison, per this "
        "project's anti-retuning doctrine. The one narrow exception: three specific 2023 "
        "team-weeks (LV wk10, CAR wk14, LAC wk17) were inspected QUALITATIVELY in Mission "
        "4 to demonstrate the coaching filter fires on a real regime change -- never as an "
        "aggregate accuracy number, and it did not inform any threshold or design choice "
        "in this script."
    ),
    "eval_season": EVAL_SEASON,
    "eval_min_week": EVAL_MIN_WEEK,
    "eligible_eval_rows_considered": eligible_rows_considered,
    "team_offense_rows_excluded_for_negative_value_bug": len(team_offense_excluded),
    "exclusion_reason_counts": dict(exclusion_reasons),
    "coaching_regime_lookup_status_counts": dict(coaching_regime_lookup_status_counts),
    "hc_registry_source": dict(HC_GAMES_SOURCE),
    "real_hc_regime_intervals_loaded": len(hc_intervals),
    "real_starter_rows_inferred": len(starters),
    "stage_ablation": stage_ablation,
    "oracle_decomposition": oracle_decomposition,
    "three_signal_combination": three_signal_combination,
    "role_transition_subgroup": role_transition_subgroup,
    "identity_transition_subgroup_supplementary": identity_transition_subgroup_supplementary,
    "sample_records": records[:8],
    "n_boot": N_BOOT,
    "boot_seed": BOOT_SEED,
    "generated_in_seconds": time.time() - t0,
}

print(json.dumps(report, indent=2, sort_keys=True, default=str))
out_path = Path(__file__).resolve().parent / "component_error_decomposition_report.json"
with open(out_path, "w") as f:
    json.dump(report, f, indent=2, sort_keys=True, default=str)
log(f"DONE -> {out_path}")
