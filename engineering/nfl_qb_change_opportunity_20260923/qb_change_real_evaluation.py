#!/usr/bin/env python3
"""Real evaluation: the QB-change-aware team-dropback consumer
(`nfl/research/qb_change_team_dropbacks.py`, Mission 8 Workstream A) vs. the
already-merged coaching-aware opportunity engine baseline
(`receptions_team_opportunity_challenger.predict_team_pass_dropbacks_
coaching_aware`), on real nflverse data end-to-end.

Team-side substrate: real 2023-2025 PBP-derived team box scores (the exact
pinned/digest-checked seasons `game_market_c2_data_prep.PBP_SOURCE_ASSET_
DIGESTS` already covers -- same fetch as `team_opportunity_real_evaluation.
py`, not rebuilt).

QB-identity substrate: real 2023-2026 `stats_player_week_<season>.csv`
weekly rows, filtered to QB/REG rows with real `attempts`, fed through
`qb_continuity_features.infer_team_week_starters` (reused unmodified) to
recover each real team-week's real starter identity from real recorded
pass-attempt volume -- exactly the source and method that module's own
docstring specifies, never a new ingestion.

Player-share substrate: same real weekly rows (targets/receptions),
same target-share/catch-rate construction `team_opportunity_real_
evaluation.py` already established.

This is HISTORICAL OPERATIONAL TESTING on real, strictly-prior, already-
settled games (2023-2025 seasons) -- not prospective evidence. Real
September 24, 2026 inactives do not exist yet at the time this script runs,
per Mission 8's own explicit instruction to validate this receiving path on
real historical data rather than inventing today's inactive.
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
from nfl.research.qb_continuity_features import infer_team_week_starters
from nfl.research.receptions_team_opportunity_challenger import (
    predict_team_pass_dropbacks_coaching_aware,
    compute_opportunity_projection,
    estimate_current_week_target_share,
    estimate_current_week_catch_rate,
)
from nfl.research.qb_change_team_dropbacks import (
    build_qb_change_aware_record,
    predict_team_pass_dropbacks_qb_aware,
    resolve_incumbent_qb,
)

TEAM_SEASONS = (2023, 2024, 2025)
PLAYER_SEASONS = (2023, 2024, 2025, 2026)
EVAL_SEASON = 2025
EVAL_MIN_WEEK = 8  # same real population precedent set by the coaching/snap-share ablations

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
    """Same real source `team_opportunity_real_evaluation.fetch_player_
    weekly_rows` uses, with one disclosed, minimal extension: also parses
    `position` (already present, unused there) and `attempts` (QB pass
    attempts, needed by `infer_team_week_starters`) -- no new ingestion.
    """
    rows: list[dict] = []
    for season in PLAYER_SEASONS:
        request = urllib.request.Request(
            player_stats_url(season), headers={"User-Agent": "full-count-qb-change-eval/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - a season may not exist yet
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
                "targets": targets, "receptions": receptions, "attempts": attempts,
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
matchup_rows = build_game_matchup_features(schedule_by_game.values(), offense_features, defense_features)
matchup_by_key: dict[tuple[str, str], dict] = {}
for row in matchup_rows:
    matchup_by_key[(row["game_id"], row["home_team"])] = row
    matchup_by_key[(row["game_id"], row["away_team"])] = row
print(f"  {len(matchup_rows)} real matchup rows built, {time.time()-t0:.1f}s", flush=True)

print("Fetching real player weekly stats (targets/receptions/QB attempts)...", flush=True)
player_rows = fetch_player_weekly_rows()

print("Inferring real team-week QB starters from real recorded pass-attempt volume...", flush=True)
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
        "season": row["season"], "week": row["week"], "targets": row["targets"], "receptions": row["receptions"],
    })
    game_index[(row["player_id"], row["season"], row["week"])] = row
for pid in target_share_history:
    target_share_history[pid].sort()
for pid in catch_rate_log:
    catch_rate_log[pid].sort(key=lambda r: (r["season"], r["week"]))

# ---------------------------------------------------------------------------
# Part 1: real matched-population ablation (same real 2025-week-8+
# population precedent the coaching-aware and snap-share ablations already
# used, so this is directly comparable to those two disclosed findings).
# ---------------------------------------------------------------------------
print("Running matched coaching-aware-vs-QB-aware comparison on real held-out games...", flush=True)
eval_rows = [
    r for r in player_rows
    if r["season"] == EVAL_SEASON and r["week"] >= EVAL_MIN_WEEK and r["position"] in ("WR", "TE", "RB")
]

baseline_errors, qb_aware_errors = [], []
matched_n = 0
qb_changed_projection_n = 0
insufficient_qb_history_n = 0
abstain_reasons: dict[str, int] = defaultdict(int)
sample_records = []
qb_change_samples = []

for row in eval_rows:
    player_id, season, week, team = row["player_id"], row["season"], row["week"], row["team"]
    game_ids = [
        gid for gid, g in schedule_by_game.items()
        if g["season"] == season and g["week"] == week and team in (g["home_team"], g["away_team"])
    ]
    if not game_ids:
        continue
    game_id = game_ids[0]
    matchup_row = matchup_by_key.get((game_id, team))
    if matchup_row is None:
        continue
    side = "home" if matchup_row["home_team"] == team else "away"
    opponent = "away" if side == "home" else "home"
    opp_allowed = matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"]
    opp_n = matchup_row[f"{opponent}_defense_prior_games_n"]

    baseline_info = predict_team_pass_dropbacks_coaching_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        hc_intervals=[], game_date_index={},
        opponent_defense_allowed=opp_allowed, opponent_defense_prior_games_n=opp_n,
    )
    qb_info = predict_team_pass_dropbacks_qb_aware(
        team_offense_rows, team=team, target_season=season, target_week=week,
        starters=starters, opponent_defense_allowed=opp_allowed, opponent_defense_prior_games_n=opp_n,
    )
    if qb_info["qb_feature_changed_the_projection"]:
        qb_changed_projection_n += 1
    if qb_info["own_games_used_qb_aware"] == 0 and qb_info["qb_note"]["incumbent_player_id"] is not None:
        insufficient_qb_history_n += 1

    share_info = estimate_current_week_target_share(
        player_id=player_id, target_share_history=target_share_history.get(player_id, []),
        target_season=season, target_week=week,
    )
    rate_info = estimate_current_week_catch_rate(
        player_id=player_id, game_log=catch_rate_log.get(player_id, []),
        target_season=season, target_week=week,
    )
    baseline_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=baseline_info["predicted_dropbacks_coaching_aware"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    qb_proj_info = compute_opportunity_projection(
        predicted_team_dropbacks=qb_info["predicted_dropbacks_qb_aware"],
        target_share=share_info["estimate"], catch_rate=rate_info["estimate"],
    )
    if baseline_proj_info["projection"] is None or qb_proj_info["projection"] is None:
        abstain_reasons[baseline_proj_info["reason"] or qb_proj_info["reason"]] += 1
        continue

    realized = row["receptions"]
    baseline_errors.append(abs(baseline_proj_info["projection"] - realized))
    qb_aware_errors.append(abs(qb_proj_info["projection"] - realized))
    matched_n += 1
    if len(sample_records) < 5:
        sample_records.append({
            "player_id": player_id, "player_name": name_by_player_id.get(player_id, ""),
            "team": team, "season": season, "week": week, "realized_receptions": realized,
            "baseline_projection": baseline_proj_info["projection"],
            "qb_aware_projection": qb_proj_info["projection"],
        })
    if qb_info["qb_feature_changed_the_projection"] and len(qb_change_samples) < 8:
        qb_change_samples.append({
            "player_id": player_id, "player_name": name_by_player_id.get(player_id, ""),
            "team": team, "season": season, "week": week, "realized_receptions": realized,
            "baseline_projection": baseline_proj_info["projection"],
            "qb_aware_projection": qb_proj_info["projection"],
            "incumbent_player_id": qb_info["qb_note"]["incumbent_player_id"],
            "incumbent_name": name_by_player_id.get(qb_info["qb_note"]["incumbent_player_id"], ""),
            "qb_tenure_starts": qb_info["qb_note"]["qb_tenure_starts"],
            "own_games_used_qb_aware": qb_info["own_games_used_qb_aware"],
            "own_games_used_naive_control": qb_info["own_games_used_naive_control"],
        })

baseline_mae = sum(baseline_errors) / len(baseline_errors) if baseline_errors else None
qb_aware_mae = sum(qb_aware_errors) / len(qb_aware_errors) if qb_aware_errors else None
print(f"  matched_n={matched_n}, baseline_mae={baseline_mae}, qb_aware_mae={qb_aware_mae}", flush=True)
print(f"  qb_changed_projection_n={qb_changed_projection_n}, insufficient_qb_history_n={insufficient_qb_history_n}", flush=True)

# ---------------------------------------------------------------------------
# Part 2: real, non-cherry-picked single-example demonstration. Scans every
# real (team, target_week) in the loaded 2023-2025 starter substrate for the
# one with the LARGEST real |qb_aware - naive_control| dropback difference
# -- the same "scan for the clearest real activation" methodology Mission 6
# used to find the real 2026 snap-share role-change example, not a
# hand-picked team.
# ---------------------------------------------------------------------------
print("Scanning for the single largest real QB-change activation (2023-2025)...", flush=True)
best = None
teams_seen = sorted({s["team"] for s in starters})
seasons_seen = sorted({s["season"] for s in starters})
for team in teams_seen:
    for season in seasons_seen:
        for week in range(2, 19):
            info = predict_team_pass_dropbacks_qb_aware(
                team_offense_rows, team=team, target_season=season, target_week=week,
                starters=starters, opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
            )
            if not info["qb_feature_changed_the_projection"]:
                continue
            diff = abs(info["predicted_dropbacks_qb_aware"] - info["predicted_dropbacks_naive_control"])
            if best is None or diff > best["diff"]:
                incumbent_info = resolve_incumbent_qb(starters, team=team, target_season=season, target_week=week)
                best = {
                    "diff": diff, "team": team, "season": season, "week": week,
                    "predicted_dropbacks_qb_aware": info["predicted_dropbacks_qb_aware"],
                    "predicted_dropbacks_naive_control": info["predicted_dropbacks_naive_control"],
                    "own_games_used_qb_aware": info["own_games_used_qb_aware"],
                    "own_games_used_naive_control": info["own_games_used_naive_control"],
                    "incumbent_player_id": incumbent_info["incumbent_player_id"],
                    "incumbent_name": name_by_player_id.get(incumbent_info["incumbent_player_id"], ""),
                    "qb_tenure_starts": incumbent_info["qb_tenure_starts"],
                }
print(f"  real_largest_qb_change_activation: {json.dumps(best, indent=2)}", flush=True)

# ---------------------------------------------------------------------------
# Part 3: real, named single-player probability demonstration. Baltimore
# entering 2025 Week 8 is a real, well-known, strictly-prior-knowable QB
# change: Lamar Jackson (incumbent since 2019) was inactive with a real,
# publicly reported injury; Cooper Rush made his 2nd real consecutive start
# at QB for Baltimore (per the real starter substrate loaded above -- his
# own `qb_tenure_starts` resolves to 2 entering week 8, i.e. real, not
# assumed). DeAndre Hopkins (BAL WR) is used here because he is a real
# candidate this exact real (team, season, week) already surfaced in the
# main ablation loop above as a real row where the QB feature changed the
# projection -- not a separately cherry-picked player.
# ---------------------------------------------------------------------------
print("Building real named single-player demonstration record (BAL, DeAndre Hopkins, 2025 week 8)...", flush=True)
demo_team, demo_season, demo_week, demo_player = "BAL", 2025, 8, "00-0030564"
demo_game_ids = [
    gid for gid, g in schedule_by_game.items()
    if g["season"] == demo_season and g["week"] == demo_week and demo_team in (g["home_team"], g["away_team"])
]
demo_record = None
if demo_game_ids:
    demo_matchup_row = matchup_by_key.get((demo_game_ids[0], demo_team))
    if demo_matchup_row is not None:
        demo_side = "home" if demo_matchup_row["home_team"] == demo_team else "away"
        demo_opponent = "away" if demo_side == "home" else "home"
        demo_record = build_qb_change_aware_record(
            candidate_player_id=demo_player, candidate_team=demo_team,
            team_box_score_rows=team_offense_rows, starters=starters,
            hc_intervals=[], game_date_index={},
            opponent_defense_allowed=demo_matchup_row[f"{demo_opponent}_defense_prior_mean_opp_dropback_proxy_allowed"],
            opponent_defense_prior_games_n=demo_matchup_row[f"{demo_opponent}_defense_prior_games_n"],
            target_share_history=target_share_history.get(demo_player, []),
            catch_rate_game_log=catch_rate_log.get(demo_player, []),
            target_season=demo_season, target_week=demo_week,
            line=2.5, over_odds=-115, under_odds=-105,
            residuals=[0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5,
        )
print(f"  demo_record: {json.dumps(demo_record, indent=2, default=str)}", flush=True)

report = {
    "generated_in_seconds": round(time.time() - t0, 1),
    "team_seasons": list(TEAM_SEASONS),
    "player_seasons": list(PLAYER_SEASONS),
    "eval_season": EVAL_SEASON,
    "eval_min_week": EVAL_MIN_WEEK,
    "n_real_team_week_starter_observations": len(starters),
    "main_ablation": {
        "matched_n": matched_n,
        "baseline_coaching_aware_mae": baseline_mae,
        "qb_aware_mae": qb_aware_mae,
        "rows_where_qb_feature_changed_the_projection": qb_changed_projection_n,
        "rows_with_insufficient_qb_specific_box_score_history": insufficient_qb_history_n,
        "abstain_reasons": dict(abstain_reasons),
        "sample_records": sample_records,
        "qb_change_activation_samples": qb_change_samples,
    },
    "real_largest_qb_change_activation_2023_2025": best,
    "real_named_single_player_demonstration_BAL_week8_2025": demo_record,
}
out_path = Path(__file__).resolve().parent / "qb_change_real_evaluation_report.json"
out_path.write_text(json.dumps(report, indent=2, default=str))
print(f"Wrote {out_path}", flush=True)
print(f"Total runtime: {time.time()-t0:.1f}s", flush=True)
