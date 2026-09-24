#!/usr/bin/env python3
"""Shared, frozen evaluation core for Mission 10's target-share work.

`build_rows(eval_season, min_week)` returns one record per real,
outcome-complete WR/TE/RB player-game in the target season (week >=
min_week), carrying every pregame component the comparison needs plus the
realized outcome. It is the SAME function for the exploratory diagnosis and
the locked holdout, so the holdout cannot quietly use different plumbing.

Population contract (identical to every prior opportunity-engine
evaluation in this repo, disclosed rather than changed): a candidate is a
player with a real stats row in the target game. nflverse weekly stats omit
players with no recorded stat, so a player who was active but never
targeted/carried is absent from both his history and the target population.
B0 and every challenger are scored on exactly the same rows.

Every pregame input uses only rows strictly before the target
(season, week). Target-game targets, receptions, snaps, or team totals are
read ONLY into the `realized_*` fields.
"""
from __future__ import annotations

import bisect
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_cache import load_player_weeks, load_snap_rows, load_team_offense  # noqa: E402

from nfl.research.defense_prior_features import build_prior_defense_features  # noqa: E402
from nfl.research.game_matchup_features import build_game_matchup_features  # noqa: E402
from nfl.research.receptions_shadow import current_b0_projection  # noqa: E402
from nfl.research.pregame_target_share import (  # noqa: E402
    team_targets_per_dropback,
    unit_consistent_expected_receptions,
)
from nfl.research.receptions_team_opportunity_challenger import (  # noqa: E402
    apply_snap_informed_target_share,
    compute_opportunity_projection,
    estimate_current_week_snap_share,
    estimate_current_week_catch_rate,
    estimate_current_week_target_share,
    predict_team_pass_dropbacks,
)
from nfl.research.team_prior_features import build_prior_team_features  # noqa: E402

POSITIONS = ("WR", "TE", "RB")


def load_context(seasons):
    """Load real player-week and team-offense rows for `seasons` and build
    the lookup indexes every estimator needs."""
    player_rows, team_rows = [], []
    provenance = {}
    for season in seasons:
        p = load_player_weeks(season)
        player_rows.extend(p["rows"])
        provenance[f"player_weeks_{season}"] = {"url": p["source_url"], "rows_sha256": p["rows_sha256"]}
        if season <= 2025:
            t = load_team_offense(season)
            team_rows.extend(t["rows"])
            provenance[f"team_offense_{season}"] = {"source": t["source"], "rows_sha256": t["rows_sha256"]}

    team_targets = defaultdict(float)
    for r in player_rows:
        team_targets[(r["season"], r["week"], r["team"])] += r["targets"]

    history_by_player = defaultdict(list)
    for r in player_rows:
        history_by_player[r["player_id"]].append(r)
    for rows in history_by_player.values():
        rows.sort(key=lambda r: (r["season"], r["week"]))
    history_keys = {pid: [(r["season"], r["week"]) for r in rows] for pid, rows in history_by_player.items()}

    dropbacks = {}
    for r in team_rows:
        dropbacks[(r["season"], r["week"], r["team"])] = r["attempts"] + r["sacks_suffered"]
    team_games = defaultdict(list)
    for (season, week, team), db in dropbacks.items():
        tt = team_targets.get((season, week, team))
        if tt:
            team_games[team].append((season, week, db, tt))
    for games in team_games.values():
        games.sort()

    snap_history = defaultdict(list)
    team_week_max_snaps = defaultdict(float)
    snap_rows = []
    for season in seasons:
        if season <= 2025:
            sp = load_snap_rows(season)
            snap_rows.extend(sp["rows"])
            provenance[f"snap_counts_{season}"] = {"source": sp["source"], "rows_sha256": sp["rows_sha256"]}
    for r in snap_rows:
        key = (r["season"], r["week"], r["team"])
        team_week_max_snaps[key] = max(team_week_max_snaps[key], r["offense_snaps"])
    for r in snap_rows:
        team_max = team_week_max_snaps[(r["season"], r["week"], r["team"])]
        if r["player_id"] and team_max > 0:
            snap_history[r["player_id"]].append((r["season"], r["week"], r["offense_snaps"] / team_max))
    for rows in snap_history.values():
        rows.sort()

    offense = build_prior_team_features(team_rows, rolling_window=5)
    defense = build_prior_defense_features(team_rows, rolling_window=5)
    schedule = {}
    for r in team_rows:
        parts = r["game_id"].split("_")
        if len(parts) == 4:
            schedule[r["game_id"]] = {
                "game_id": r["game_id"], "season": r["season"], "week": r["week"],
                "season_type": "REG", "away_team": parts[2], "home_team": parts[3],
            }
    matchup = {}
    for row in build_game_matchup_features(schedule.values(), offense, defense):
        matchup[(row["game_id"], row["home_team"])] = (row, "home")
        matchup[(row["game_id"], row["away_team"])] = (row, "away")

    return {
        "player_rows": player_rows, "team_targets": team_targets,
        "history_by_player": history_by_player, "history_keys": history_keys,
        "team_games": team_games, "matchup": matchup, "provenance": provenance,
        "snap_history": snap_history,
    }


def prior_history(ctx, player_id, season, week):
    rows = ctx["history_by_player"].get(player_id, [])
    i = bisect.bisect_left(ctx["history_keys"].get(player_id, []), (season, week))
    return rows[:i]


def prior_team_games(ctx, team, season, week, n):
    games = ctx["team_games"].get(team, [])
    i = bisect.bisect_left(games, (season, week, float("-inf"), float("-inf")))
    return games[max(0, i - n):i]


def build_rows(ctx, eval_season, min_week):
    """Real, outcome-complete candidate records with all pregame components."""
    out = []
    abstain = defaultdict(int)
    for r in ctx["player_rows"]:
        if r["season"] != eval_season or r["week"] < min_week or r["position"] not in POSITIONS:
            continue
        season, week, team = r["season"], r["week"], r["team"]
        realized_team_targets = ctx["team_targets"].get((season, week, team), 0.0)
        if realized_team_targets <= 0:
            abstain["NO_REAL_TEAM_TARGETS_IN_TARGET_GAME"] += 1
            continue
        m = ctx["matchup"].get((r["game_id"], team))
        if m is None:
            abstain["NO_MATCHUP_ROW"] += 1
            continue
        matchup_row, side = m
        history = prior_history(ctx, r["player_id"], season, week)
        try:
            b0 = current_b0_projection(history)["projection"]
        except ValueError:
            abstain["B0_INSUFFICIENT_HISTORY"] += 1
            continue

        share_history = [
            (h["season"], h["week"], h["targets"] / ctx["team_targets"][(h["season"], h["week"], h["team"])])
            for h in history if ctx["team_targets"].get((h["season"], h["week"], h["team"]), 0) > 0
        ]
        existing_share = estimate_current_week_target_share(
            player_id=r["player_id"], target_share_history=share_history,
            target_season=season, target_week=week,
        )
        catch = estimate_current_week_catch_rate(
            player_id=r["player_id"], game_log=history, target_season=season, target_week=week,
        )
        volume = predict_team_pass_dropbacks(matchup_row, side=side)
        existing = compute_opportunity_projection(
            predicted_team_dropbacks=volume["predicted_dropbacks"],
            target_share=existing_share["estimate"], catch_rate=catch["estimate"],
        )
        if existing["projection"] is None:
            abstain["EXISTING_ENGINE_" + existing["reason"]] += 1
            continue

        # Existing FULL engine: same volume/catch, snap-informed share
        # (merged Mission 6 factor, reused unmodified).
        snap_info = estimate_current_week_snap_share(
            player_id=r["player_id"], snap_share_history=ctx["snap_history"].get(r["player_id"], []),
            target_season=season, target_week=week,
        )
        snap_share = apply_snap_informed_target_share(target_share_info=existing_share, snap_share_info=snap_info)
        full = compute_opportunity_projection(
            predicted_team_dropbacks=volume["predicted_dropbacks"],
            target_share=snap_share["estimate"], catch_rate=catch["estimate"],
        )

        # Mission 10 challenger: identical inputs, one change -- the share is
        # applied to predicted team TARGETS (team's own strictly-prior
        # targets-per-dropback ratio), not to team dropbacks.
        tpd = team_targets_per_dropback(
            ctx["team_games"].get(team, []), target_season=season, target_week=week,
        )
        challenger = unit_consistent_expected_receptions(
            predicted_team_dropbacks=volume["predicted_dropbacks"],
            targets_per_dropback=tpd["ratio"],
            target_share=existing_share["estimate"], catch_rate=catch["estimate"],
        )
        if full["projection"] is None or challenger["projection"] is None:
            abstain["FULL_OR_CHALLENGER_" + str(full["reason"] or challenger["reason"])] += 1
            continue

        out.append({
            "player_id": r["player_id"], "player_name": r["player_name"], "position": r["position"],
            "season": season, "week": week, "game_id": r["game_id"], "team": team,
            "history": history,
            "share_history": share_history,
            "b0_projection": b0,
            "existing_share": existing_share["estimate"],
            "catch_rate": catch["estimate"],
            "predicted_dropbacks": volume["predicted_dropbacks"],
            "existing_projection": existing["projection"],
            "full_engine_projection": full["projection"],
            "snap_role_change_applied": snap_share.get("snap_role_change_applied", False),
            "targets_per_dropback": tpd["ratio"],
            "targets_per_dropback_games_used": tpd["games_used"],
            "challenger_projection": challenger["projection"],
            "challenger_expected_targets": challenger["expected_targets"],
            "prior_team_games": prior_team_games(ctx, team, season, week, 8),
            "realized_receptions": r["receptions"],
            "realized_targets": r["targets"],
            "realized_team_targets": realized_team_targets,
            "realized_share": r["targets"] / realized_team_targets,
        })
    return out, dict(abstain)
