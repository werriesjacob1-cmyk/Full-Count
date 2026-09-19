#!/usr/bin/env python3
"""WR/RB role-state rows, trigger events, and replacement candidates.

Consumes `role_intelligence_data_prep.build_player_game_usage_rows` output
(one merged record per season/week/team/WR-or-RB-player) and builds the
contract's primary grain: `target_game x team x player x role_dimension`,
with strictly-prior features on one side and postgame realized labels kept
structurally separate on the other. See
`engineering/NFL_ROLE_CHANGE_HISTORICAL_DATASET_CONTRACT_2026-09-18.md`.

## Role dimensions and their real eligible populations

Every dimension below is computed only where a real source covers it; every
other cell is the literal string `"UNKNOWN_..."`, never a fabricated number.

- `target_share`, `carry_share`: `stats_player_week`. Eligible whenever the
  team-week has a positive `team_targets`/`team_carries` denominator (see
  `role_intelligence_data_prep` module docstring on why the substrate's
  build window is bounded to 2012-2025 even though these two alone could
  extend to 1999).
- `offense_snap_share`: `snap_counts`, eligible only when this player has an
  `offense_snaps` row AND the team has a positive `team_offense_snaps`
  (`max(offense_snaps)` across the roster, see data_prep). `snap_counts`'
  own 2012 asset is a real, disclosed empty file (see
  `game_market_c2_data_prep.py`), so 2012 is UNKNOWN for this dimension.
- `red_zone_opportunity_share`, `goal_line_carry_share`: PBP-derived,
  eligible whenever the team-game has a positive opportunity count in that
  category (a team with zero red-zone snaps that game makes every player's
  share UNKNOWN, not a fabricated zero-over-zero).
- `third_down_snap_share`, `two_minute_snap_share`: PBP-derived
  TARGET+CARRY OPPORTUNITY proxies, not literal snap-participation shares
  (this build ingests no participation/NGS/FTN source -- see
  `THIRD_DOWN_TWO_MINUTE_ARE_OPPORTUNITY_PROXIES`). Same eligibility rule.
- `route_share`: never computed in this build (`ROUTE_SHARE_UNAVAILABLE`).

## Point-in-time safety

`build_role_state_rows` emits, for each role dimension, a `target` block
(this game's realized share, postgame-only) and a `features` block (rolling
statistics built strictly from games that were appended to that player's
history BEFORE this row was emitted -- the same append-after-emit ordering
`nflverse_history.build_prior_only_rows` uses). `features` never reads this
row's own counts. `nfl/tests/test_role_intelligence_features.py` asserts
this directly by mutating a row's target-game usage counts after building
the dataset and re-deriving the same features from the mutated history,
confirming no feature value changes -- a target-game leakage test, not a
schema check.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

ROLE_DIMENSIONS = (
    "offense_snap_share",
    "route_share",
    "target_share",
    "carry_share",
    "red_zone_opportunity_share",
    "goal_line_carry_share",
    "third_down_snap_share",
    "two_minute_snap_share",
)

ROUTE_SHARE_UNAVAILABLE = "UNKNOWN_NO_SOURCE_INGESTED"
THIRD_DOWN_TWO_MINUTE_ARE_OPPORTUNITY_PROXIES = (
    "third_down_snap_share and two_minute_snap_share are PBP target+carry "
    "opportunity-count proxies, not literal snap-participation shares; this "
    "build ingests no 2016+ participation/NGS source."
)

UNKNOWN_NO_NUMERATOR_SOURCE = "UNKNOWN_NO_NUMERATOR_SOURCE"
UNKNOWN_ZERO_DENOMINATOR = "UNKNOWN_ZERO_OR_MISSING_TEAM_DENOMINATOR"

ROLLING_HORIZONS = ("previous_game", "last_3", "last_5", "last_8")

# A prior-game-over-game jump/drop this large in a single dimension's share
# (in share points, 0-1 scale) marks "games_since_large_usage_change".
# Chosen as a generic, pre-declared, round threshold -- not fit to this data.
LARGE_USAGE_CHANGE_THRESHOLD = 0.15


def _share(numerator: float | None, denominator: float | None) -> float | str:
    if numerator is None:
        return UNKNOWN_NO_NUMERATOR_SOURCE
    if denominator is None or denominator <= 0:
        return UNKNOWN_ZERO_DENOMINATOR
    return numerator / denominator


def compute_dimension_shares(usage_row: dict[str, Any]) -> dict[str, float | str]:
    """Return this single game's realized share for every role dimension."""
    return {
        "offense_snap_share": _share(usage_row["offense_snaps"], usage_row["team_offense_snaps"]),
        "route_share": ROUTE_SHARE_UNAVAILABLE,
        "target_share": _share(usage_row["targets"], usage_row["team_targets"]),
        "carry_share": _share(usage_row["carries"], usage_row["team_carries"]),
        "red_zone_opportunity_share": _share(
            (usage_row["red_zone_targets"] or 0) + (usage_row["red_zone_carries"] or 0),
            usage_row["team_red_zone_opportunities"],
        ),
        "goal_line_carry_share": _share(
            usage_row["goal_line_carries"], usage_row["team_goal_line_carries"]
        ),
        "third_down_snap_share": _share(
            (usage_row["third_down_targets"] or 0) + (usage_row["third_down_carries"] or 0),
            usage_row["team_third_down_opportunities"],
        ),
        "two_minute_snap_share": _share(
            (usage_row["two_minute_targets"] or 0) + (usage_row["two_minute_carries"] or 0),
            usage_row["team_two_minute_opportunities"],
        ),
    }


def _numeric_history(history: list[dict[str, float | str]], dimension: str) -> list[float]:
    return [h[dimension] for h in history if isinstance(h[dimension], (int, float))]


def _mean(values: list[float]) -> float | str:
    return (sum(values) / len(values)) if values else "UNKNOWN_NO_PRIOR_HISTORY"


def _prior_rolling_features(
    history: list[dict[str, Any]], dimension: str
) -> dict[str, float | str]:
    """Strictly-prior rolling-window features for one dimension.

    `history` must contain ONLY games already appended before this call --
    the caller (`build_role_state_rows`) is responsible for append-after-
    emit ordering, exactly like `nflverse_history.build_prior_only_rows`.
    """
    values = _numeric_history(history, dimension)
    out: dict[str, float | str] = {
        "previous_game": values[-1] if values else "UNKNOWN_NO_PRIOR_HISTORY",
        "last_3": _mean(values[-3:]),
        "last_5": _mean(values[-5:]),
        "last_8": _mean(values[-8:]),
        "career_mean": _mean(values),
        "history_n": len(values),
    }
    return out


def _season_scoped_means(
    history: list[dict[str, Any]], dimension: str, season: int
) -> dict[str, float | str]:
    current_season_values = [
        h[dimension] for h in history if h["season"] == season and isinstance(h[dimension], (int, float))
    ]
    trailing_season_values = [
        h[dimension] for h in history if h["season"] == season - 1 and isinstance(h[dimension], (int, float))
    ]
    trailing_2_season_values = [
        h[dimension]
        for h in history
        if h["season"] in (season - 1, season - 2) and isinstance(h[dimension], (int, float))
    ]
    return {
        "current_season_mean": _mean(current_season_values),
        "trailing_season_mean": _mean(trailing_season_values),
        "trailing_2_season_mean": _mean(trailing_2_season_values),
    }


def _games_since_large_change(history: list[dict[str, Any]], dimension: str) -> int | str:
    """Count of consecutive most-recent prior games with no large game-over-game jump.

    Purely backward-looking: compares each pair of *already-prior* games,
    never the target game. Returns `"UNKNOWN_INSUFFICIENT_HISTORY"` with
    fewer than 2 prior games (a jump needs two points to compare).
    """
    values = _numeric_history(history, dimension)
    if len(values) < 2:
        return "UNKNOWN_INSUFFICIENT_HISTORY"
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if abs(values[i] - values[i - 1]) > LARGE_USAGE_CHANGE_THRESHOLD:
            break
        count += 1
    return count


def build_role_state_rows(usage_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the primary `target_game x team x player x role_dimension` rows.

    `usage_rows` must be `role_intelligence_data_prep.build_player_game_usage_rows`
    output (or an equivalent). Rows are grouped by `player_id`, ordered by
    (season, week), and each dimension's `features` are built from that
    player's history strictly before this row is appended -- see module
    docstring on point-in-time safety.
    """
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usage_rows:
        by_player[row["player_id"]].append(row)

    output: list[dict[str, Any]] = []
    for player_id, games in by_player.items():
        games_sorted = sorted(games, key=lambda r: (r["season"], r["week"]))
        history: list[dict[str, Any]] = []
        prior_team: str | None = None
        games_with_current_team = 0

        for game in games_sorted:
            realized_shares = compute_dimension_shares(game)
            if game["team"] == prior_team:
                games_with_current_team += 1
            else:
                games_with_current_team = 0
            prior_team = game["team"]

            for dimension in ROLE_DIMENSIONS:
                features: dict[str, Any] = {
                    "games_with_current_team": games_with_current_team,
                    # Real, disclosed future dependencies -- not fabricated.
                    "current_coach_regime": "UNKNOWN_COACH_REGISTRY_NOT_YET_BUILT",
                    "games_with_current_qb": "UNKNOWN_QB_JOIN_NOT_BUILT_THIS_TASK",
                    "games_with_current_playcaller": "UNKNOWN_COACH_REGISTRY_NOT_YET_BUILT",
                }
                if dimension == "route_share":
                    features["rolling"] = "UNKNOWN_NO_SOURCE_INGESTED"
                else:
                    features.update(_prior_rolling_features(history, dimension))
                    features.update(_season_scoped_means(history, dimension, game["season"]))
                    features["games_since_large_usage_change"] = _games_since_large_change(history, dimension)

                output.append({
                    "season": game["season"],
                    "week": game["week"],
                    "team": game["team"],
                    "opponent_team": game["opponent_team"],
                    "player_id": player_id,
                    "player_display_name": game["player_display_name"],
                    "position": game["position"],
                    "role_dimension": dimension,
                    "features": features,
                    "target": {"realized_share": realized_shares[dimension]},
                })

            # Append AFTER emitting this game's rows -- the no-lookahead
            # invariant. Moving this above the loop leaks the target game's
            # own realized share into its own "previous_game"/rolling/season
            # features.
            history.append({"season": game["season"], "week": game["week"], **realized_shares})

    output.sort(key=lambda r: (r["season"], r["week"], r["team"], r["player_id"], r["role_dimension"]))
    return output


# --------------------------------------------------------------------------
# Teammate absence / trigger events (WR: top route-share*; RB: top carry-share)
#
# *route_share has no source in this build, so the WR "top recent usage"
# ranking uses target_share instead, disclosed via `wr_ranking_dimension`.
# --------------------------------------------------------------------------

GAME_AFFECTING_INJURY_STATUSES = frozenset({"OUT", "DOUBTFUL"})


# How many of a team's own most recent games a WR/RB can miss (any reason)
# before he stops being considered that team's "top recent usage player" for
# ranking purposes. Needs to be generous enough to span a real multi-week
# injury absence (the exact case this trigger exists to catch -- e.g. a
# 5-6 week IR stint) while still eventually aging out a player who has
# genuinely left the team. Chosen as a round, pre-declared number, not fit
# to this data.
ROSTER_RECENCY_WINDOW_TEAM_GAMES = 10


def _top_usage_player_per_team_week(
    usage_rows: list[dict[str, Any]], position: str, ranking_dimension: str
) -> dict[tuple[int, int, str], str]:
    """Return {(season, week, team): player_id} of the STRICTLY-PRIOR top-usage player.

    "Top recent usage" is ranked by that player's own prior-game history
    mean for `ranking_dimension` as of the game immediately before
    (season, week) -- never by this week's own realized usage. Critically,
    a player who is OUT for (season, week) has no usage ROW for that game
    (he did not play), so ranking must consider every player who has
    recently appeared for this team, not only players with a row in the
    target week itself -- otherwise an injured top player could never be
    detected as "the top player, now missing," which is exactly the event
    this function exists to find.
    """
    position_rows = [r for r in usage_rows if r["position"] == position]
    team_week_keys = sorted({(r["season"], r["week"], r["team"]) for r in position_rows})
    rows_by_team_week: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in position_rows:
        rows_by_team_week[(row["season"], row["week"], row["team"])].append(row)

    running_mean: dict[str, float] = {}
    running_n: dict[str, int] = {}
    # Most recent team-game index (a global counter over that team's own
    # games) each player was actually observed playing for that team.
    last_seen_team_game_index: dict[tuple[str, str], int] = {}
    team_game_counter: dict[str, int] = defaultdict(int)
    roster_by_team: dict[str, set] = defaultdict(set)

    top_by_team_week: dict[tuple[int, int, str], str] = {}

    for season, week, team in team_week_keys:
        team_game_counter[team] += 1
        current_index = team_game_counter[team]

        # Rank using ONLY information known before this week's games.
        candidates = [
            player_id for player_id in roster_by_team[team]
            if player_id in running_mean
            and current_index - last_seen_team_game_index[(team, player_id)] <= ROSTER_RECENCY_WINDOW_TEAM_GAMES
        ]
        if candidates:
            best_player_id = max(candidates, key=lambda pid: running_mean[pid])
            top_by_team_week[(season, week, team)] = best_player_id

        # Update roster/running-mean/last-seen AFTER ranking, using this
        # week's own realized rows.
        for row in rows_by_team_week[(season, week, team)]:
            player_id = row["player_id"]
            roster_by_team[team].add(player_id)
            last_seen_team_game_index[(team, player_id)] = current_index
            realized = compute_dimension_shares(row)[ranking_dimension]
            if isinstance(realized, (int, float)):
                n = running_n.get(player_id, 0)
                prev = running_mean.get(player_id, 0.0)
                running_mean[player_id] = (prev * n + realized) / (n + 1)
                running_n[player_id] = n + 1

    return top_by_team_week


def build_teammate_absence_trigger_events(
    usage_rows: list[dict[str, Any]],
    injury_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """WR/RB top-usage-player-unavailable trigger events, pregame-safe only.

    Event existence comes ONLY from the pregame weekly injury report
    (`OUT`/`DOUBTFUL`, filed before that week's games -- see
    `injury_availability_features.py`) for the player who was the team's
    top-usage WR (by target_share) or RB (by carry_share) as of strictly
    prior games. Per the contract's "On/off event construction" section,
    this never reads target-game usage to decide whether the event exists;
    `realized_target_game_redistribution` is attached separately, for
    evaluation only, and is documented as such.
    """
    injury_index: dict[tuple[int, int, str, str], str] = {
        (row["season"], row["week"], row["team"], row["player_id"]): row["report_status"]
        for row in injury_rows
    }
    usage_by_key = {
        (r["season"], r["week"], r["team"], r["player_id"]): r for r in usage_rows
    }

    events: list[dict[str, Any]] = []
    for position, ranking_dimension, event_type in (
        ("WR", "target_share", "WR_ABSENCE"),
        ("RB", "carry_share", "RB_ABSENCE"),
    ):
        top_by_team_week = _top_usage_player_per_team_week(usage_rows, position, ranking_dimension)
        for (season, week, team), player_id in top_by_team_week.items():
            status = injury_index.get((season, week, team, player_id))
            if status not in GAME_AFFECTING_INJURY_STATUSES:
                continue
            usage_row = usage_by_key.get((season, week, team, player_id))
            realized_shares = compute_dimension_shares(usage_row) if usage_row else None
            events.append({
                "event_type": event_type,
                "season": season,
                "week": week,
                "team": team,
                "removed_player_id": player_id,
                "removed_player_ranking_dimension": ranking_dimension,
                "pregame_injury_status": status,
                "trigger_source": "NFLVERSE_WEEKLY_INJURY_REPORT_PREGAME",
                "target_game_usage_used_to_construct_event": False,
                # Postgame-only, evaluation use, structurally separate from
                # the trigger's own (pregame) existence condition.
                "realized_target_game_shares_of_removed_player": realized_shares,
            })

    events.sort(key=lambda e: (e["season"], e["week"], e["team"], e["event_type"]))
    return events


# --------------------------------------------------------------------------
# Candidate replacement rows
# --------------------------------------------------------------------------

def build_replacement_candidate_rows(
    usage_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """For each trigger event, list teammates at the same position as candidates.

    Candidate features are strictly prior (that teammate's OWN role-state
    history as of the game before the event), per the contract's "Candidate
    replacement features" section. Depth rank and prior shares are read from
    this event's own team/week usage rows, which is pregame-legitimate
    roster/usage information distinct from the removed player's own
    target-game outcome.
    """
    by_team_week_position: dict[tuple[int, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in usage_rows:
        by_team_week_position[(row["season"], row["week"], row["team"], row["position"])].append(row)

    by_player_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(usage_rows, key=lambda r: (r["player_id"], r["season"], r["week"])):
        by_player_history[row["player_id"]].append(row)

    candidates: list[dict[str, Any]] = []
    for event in events:
        position = "WR" if event["event_type"] == "WR_ABSENCE" else "RB"
        teammates = by_team_week_position.get(
            (event["season"], event["week"], event["team"], position), []
        )
        for teammate in teammates:
            if teammate["player_id"] == event["removed_player_id"]:
                continue
            prior_games = [
                g for g in by_player_history[teammate["player_id"]]
                if (g["season"], g["week"]) < (event["season"], event["week"])
            ]
            prior_shares = [compute_dimension_shares(g) for g in prior_games[-5:]]
            prior_target_shares = [s["target_share"] for s in prior_shares if isinstance(s["target_share"], (int, float))]
            prior_carry_shares = [s["carry_share"] for s in prior_shares if isinstance(s["carry_share"], (int, float))]
            candidates.append({
                "event_type": event["event_type"],
                "season": event["season"],
                "week": event["week"],
                "team": event["team"],
                "removed_player_id": event["removed_player_id"],
                "candidate_player_id": teammate["player_id"],
                "candidate_position": teammate["position"],
                "candidate_depth_team": teammate["depth_team"],
                "candidate_prior_target_share_mean_last5": _mean(prior_target_shares),
                "candidate_prior_carry_share_mean_last5": _mean(prior_carry_shares),
                "candidate_prior_games_n": len(prior_games),
            })
    candidates.sort(key=lambda c: (c["season"], c["week"], c["team"], c["candidate_player_id"]))
    return candidates


# --------------------------------------------------------------------------
# Mass-balance diagnostics
# --------------------------------------------------------------------------

def build_player_dimension_history(role_state_rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[tuple[int, int, float]]]:
    """Map (player_id, role_dimension) -> sorted [(season, week, realized_share), ...].

    Built from REALIZED labels only (`target.realized_share`), numeric
    values only. This is the lookup baselines and mass-balance diagnostics
    use to find a player's most-recent-prior share as of an event -- built
    this way (rather than reading a role-state row at the event's own week)
    because a player who is genuinely absent that week has NO role-state row
    for that week at all (see `_top_usage_player_per_team_week`'s docstring):
    the "prior share as of the event" has to come from his last row BEFORE
    the event, wherever in his history that falls.
    """
    history: dict[tuple[str, str], list[tuple[int, int, float]]] = defaultdict(list)
    for row in role_state_rows:
        realized = row["target"]["realized_share"]
        if isinstance(realized, (int, float)):
            history[(row["player_id"], row["role_dimension"])].append(
                (row["season"], row["week"], realized)
            )
    for values in history.values():
        values.sort()
    return dict(history)


def prior_shares_before(
    history_list: list[tuple[int, int, float]], season: int, week: int, n: int | None = None
) -> list[float]:
    """Realized shares strictly before (season, week), optionally only the last `n`."""
    values = [share for (s, w, share) in history_list if (s, w) < (season, week)]
    return values[-n:] if n is not None else values


def most_recent_prior_share(history_list: list[tuple[int, int, float]], season: int, week: int) -> float | None:
    values = prior_shares_before(history_list, season, week, n=1)
    return values[0] if values else None


def compute_mass_balance_diagnostics(
    events: list[dict[str, Any]],
    player_dimension_history: dict[tuple[str, str], list[tuple[int, int, float]]],
    predicted_by_event: dict[tuple, dict[str, float]],
    dimension: str,
) -> dict[str, Any]:
    """Per the contract's "Mass-balance labels": conservation of the removed opportunity.

    For each event, the removed player's own strictly-prior share
    (`previous_game`) is the "opportunity budget that disappeared." A
    baseline's predicted NET increase across the teammates it actually
    predicted for (predicted share minus that teammate's own strictly-prior
    share, summed) should not exceed that budget -- doing so is exactly the
    contract's "no role model may improve one player's projection by
    creating impossible team totals." `over_allocation_error` is the excess
    above the removed budget; `unallocated_residual` is the shortfall below
    it (e.g. `NO_ADJUSTMENT` always has a full residual, since it assigns
    none of the removed share to anyone).
    """
    diagnostics = []
    for event in events:
        event_key = (event["season"], event["week"], event["team"], event["removed_player_id"])
        predictions = predicted_by_event.get(event_key)
        if not predictions:
            continue
        removed_history = player_dimension_history.get((event["removed_player_id"], dimension), [])
        removed_prior = most_recent_prior_share(removed_history, event["season"], event["week"])
        if removed_prior is None:
            continue
        net_increase = 0.0
        for player_id, predicted in predictions.items():
            teammate_history = player_dimension_history.get((player_id, dimension), [])
            teammate_prior = most_recent_prior_share(teammate_history, event["season"], event["week"])
            teammate_prior = teammate_prior if teammate_prior is not None else 0.0
            net_increase += predicted - teammate_prior
        diagnostics.append({
            "season": event["season"], "week": event["week"], "team": event["team"],
            "event_type": event["event_type"],
            "removed_opportunity_budget": removed_prior,
            "predicted_net_increase_to_teammates": net_increase,
            "unallocated_residual": max(0.0, removed_prior - net_increase),
            "over_allocation_error": max(0.0, net_increase - removed_prior),
        })

    if not diagnostics:
        return {"n_events": 0}
    mean_residual = sum(d["unallocated_residual"] for d in diagnostics) / len(diagnostics)
    mean_over_allocation = sum(d["over_allocation_error"] for d in diagnostics) / len(diagnostics)
    return {
        "n_events": len(diagnostics),
        "mean_unallocated_residual": mean_residual,
        "mean_over_allocation_error": mean_over_allocation,
        "rows": diagnostics,
    }



__all__ = [
    "ROLE_DIMENSIONS",
    "ROUTE_SHARE_UNAVAILABLE",
    "THIRD_DOWN_TWO_MINUTE_ARE_OPPORTUNITY_PROXIES",
    "UNKNOWN_NO_NUMERATOR_SOURCE",
    "UNKNOWN_ZERO_DENOMINATOR",
    "GAME_AFFECTING_INJURY_STATUSES",
    "compute_dimension_shares",
    "build_role_state_rows",
    "build_teammate_absence_trigger_events",
    "build_replacement_candidate_rows",
    "build_player_dimension_history",
    "prior_shares_before",
    "most_recent_prior_share",
    "compute_mass_balance_diagnostics",
]
