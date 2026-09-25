#!/usr/bin/env python3
"""Four predeclared baselines for the WR/RB role-intelligence substrate.

Per the dataset contract's "Baselines" section, exactly these four (and
nothing further -- no `HIERARCHICAL_ROLE_MODEL`):

1. `NO_ADJUSTMENT` -- predict the removed player's own teammates keep their
   own strictly-prior share unchanged (i.e. the absence changes nothing).
2. `PROPORTIONAL_TEAMMATE_REDISTRIBUTION` -- the removed player's own
   strictly-prior share is redistributed across teammates at the same
   position in proportion to each teammate's own strictly-prior share.
3. `DEPTH_CHART_NEXT_MAN` -- the removed player's entire prior share goes to
   the single teammate one rank below him on the real pregame depth chart.
4. `RECENT_USAGE_NEXT_MAN` -- the removed player's entire prior share goes to
   whichever teammate had the single highest strictly-prior recent-usage
   mean (last-5), independent of depth-chart rank.

All four predict ONLY for teammates of the removed player at his position,
for the (season, week, team) of a `build_teammate_absence_trigger_events`
event, and read ONLY strictly-prior information -- each teammate's own
last-5-game mean and the removed player's own most-recent-prior share, both
from `role_intelligence_features.build_player_dimension_history` -- never
target-game usage of anyone. Evaluation (`evaluate_baselines`) is the only
place realized (target-game) shares are read, and it reads them only to
score predictions already made, never to build them.

Every "prior share" lookup here uses `most_recent_prior_share`/
`prior_shares_before` against a player's REALIZED-label history, not a
role-state row's own `features` looked up at the event's exact week. A
player who is genuinely absent for an event has NO role-state row at all
for that week (he did not play), so a lookup keyed on that exact week would
silently find nothing for the one player who matters most -- this was a
real bug caught during this build's own end-to-end run against real
2012-2025 data (it collapsed three of the four baselines to
`NO_ADJUSTMENT`'s predictions; see `ENGINEERING_HANDOFF.md`).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from nfl.research.role_intelligence_features import (
    build_player_dimension_history,
    compute_dimension_shares,
    compute_mass_balance_diagnostics,
    most_recent_prior_share,
    prior_shares_before,
)

NO_ADJUSTMENT = "NO_ADJUSTMENT"
PROPORTIONAL_TEAMMATE_REDISTRIBUTION = "PROPORTIONAL_TEAMMATE_REDISTRIBUTION"
DEPTH_CHART_NEXT_MAN = "DEPTH_CHART_NEXT_MAN"
RECENT_USAGE_NEXT_MAN = "RECENT_USAGE_NEXT_MAN"
BASELINE_NAMES = (
    NO_ADJUSTMENT,
    PROPORTIONAL_TEAMMATE_REDISTRIBUTION,
    DEPTH_CHART_NEXT_MAN,
    RECENT_USAGE_NEXT_MAN,
)


def _teammate_prior_share_last5_mean(
    history: dict, event: dict[str, Any], player_id: str, dimension: str
) -> float | None:
    values = prior_shares_before(history.get((player_id, dimension), []), event["season"], event["week"], n=5)
    return (sum(values) / len(values)) if values else None


def _removed_player_prior_share(history: dict, event: dict[str, Any], dimension: str) -> float | None:
    """The removed player's own most-recent-prior share as of this event.

    Found by scanning that player's own realized-label history for the last
    entry strictly before the event's (season, week) -- not by requiring a
    role-state row AT the event's own week, which a genuinely absent player
    will not have (he did not play that week).
    """
    return most_recent_prior_share(history.get((event["removed_player_id"], dimension), []), event["season"], event["week"])


def predict_no_adjustment(
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict,
    dimension: str,
) -> dict[str, float]:
    """Every teammate keeps his own strictly-prior share; nothing moves."""
    predictions = {}
    for teammate in teammates:
        prior = _teammate_prior_share_last5_mean(history, event, teammate["candidate_player_id"], dimension)
        if prior is not None:
            predictions[teammate["candidate_player_id"]] = prior
    return predictions


def predict_proportional_redistribution(
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict,
    dimension: str,
) -> dict[str, float]:
    """Removed player's own prior share split across teammates by their own prior-share weight."""
    removed_prior = _removed_player_prior_share(history, event, dimension)
    teammate_priors = {}
    for teammate in teammates:
        prior = _teammate_prior_share_last5_mean(history, event, teammate["candidate_player_id"], dimension)
        if prior is not None:
            teammate_priors[teammate["candidate_player_id"]] = prior

    predictions = dict(teammate_priors)
    total_weight = sum(teammate_priors.values())
    if removed_prior is not None and total_weight > 0:
        for player_id, prior in teammate_priors.items():
            predictions[player_id] = prior + removed_prior * (prior / total_weight)
    return predictions


def predict_depth_chart_next_man(
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict,
    dimension: str,
) -> dict[str, float]:
    """All of the removed player's prior share goes to the next-lowest depth-chart rank."""
    predictions = predict_no_adjustment(event, teammates, history, dimension)
    removed_prior = _removed_player_prior_share(history, event, dimension)
    ranked = [t for t in teammates if isinstance(t.get("candidate_depth_team"), int)]
    if not ranked or removed_prior is None:
        return predictions
    next_man = min(ranked, key=lambda t: t["candidate_depth_team"])
    predictions[next_man["candidate_player_id"]] = predictions.get(next_man["candidate_player_id"], 0.0) + removed_prior
    return predictions


def predict_recent_usage_next_man(
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict,
    dimension: str,
) -> dict[str, float]:
    """All of the removed player's prior share goes to the highest last-5-usage teammate."""
    predictions = predict_no_adjustment(event, teammates, history, dimension)
    removed_prior = _removed_player_prior_share(history, event, dimension)
    best_player_id, best_value = None, None
    for teammate in teammates:
        prior = predictions.get(teammate["candidate_player_id"])
        if prior is not None and (best_value is None or prior > best_value):
            best_value, best_player_id = prior, teammate["candidate_player_id"]
    if best_player_id is None or removed_prior is None:
        return predictions
    predictions[best_player_id] = predictions.get(best_player_id, 0.0) + removed_prior
    return predictions


BASELINE_PREDICTORS = {
    NO_ADJUSTMENT: predict_no_adjustment,
    PROPORTIONAL_TEAMMATE_REDISTRIBUTION: predict_proportional_redistribution,
    DEPTH_CHART_NEXT_MAN: predict_depth_chart_next_man,
    RECENT_USAGE_NEXT_MAN: predict_recent_usage_next_man,
}


def _realized_share(usage_index: dict, season: int, week: int, team: str, player_id: str, dimension: str):
    row = usage_index.get((season, week, team, player_id))
    if row is None:
        return None
    value = compute_dimension_shares(row)[dimension]
    return value if isinstance(value, (int, float)) else None


def _era_bucket(season: int) -> str:
    return "2012-2018_pre_ftn_era" if season <= 2018 else "2019-2025_recent_era"


def _season_half_bucket(week: int) -> str:
    return "early_season_weeks_1_9" if week <= 9 else "late_season_weeks_10_plus"


# Which trigger-event population is relevant to each role dimension, per the
# contract's "First evaluation populations" section: WR absence evaluates
# remaining-WR usage (route/target-flavored dimensions); RB absence
# evaluates replacement-RB usage (carry/goal-line-flavored dimensions).
# Third-down/two-minute/red-zone opportunity and offense-snap share are
# shared across both position groups' role state, so both event types are
# relevant. `route_share` has no source and is never evaluated.
DIMENSION_RELEVANT_EVENT_TYPES = {
    "target_share": frozenset({"WR_ABSENCE"}),
    "carry_share": frozenset({"RB_ABSENCE"}),
    "goal_line_carry_share": frozenset({"RB_ABSENCE"}),
    "offense_snap_share": frozenset({"WR_ABSENCE", "RB_ABSENCE"}),
    "red_zone_opportunity_share": frozenset({"WR_ABSENCE", "RB_ABSENCE"}),
    "third_down_snap_share": frozenset({"WR_ABSENCE", "RB_ABSENCE"}),
    "two_minute_snap_share": frozenset({"WR_ABSENCE", "RB_ABSENCE"}),
    "route_share": frozenset(),
}


def evaluate_baselines(
    events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    role_state_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
) -> dict[str, Any]:
    """Score every baseline's teammate-share predictions against realized shares.

    Returns per-baseline MAE overall, by season, by event type (WR vs RB
    absence), by early/late season half, by source-coverage era, and
    mass-balance diagnostics
    (`role_intelligence_features.compute_mass_balance_diagnostics`).
    Realized shares are read here, in evaluation only -- never inside a
    `predict_*` function above.
    """
    relevant_event_types = DIMENSION_RELEVANT_EVENT_TYPES.get(dimension, frozenset())
    events = [e for e in events if e["event_type"] in relevant_event_types]

    history = build_player_dimension_history(role_state_rows)
    usage_index = {(r["season"], r["week"], r["team"], r["player_id"]): r for r in usage_rows}
    candidates_by_event: dict[tuple, list] = defaultdict(list)
    for candidate in candidates:
        key = (candidate["season"], candidate["week"], candidate["team"], candidate["removed_player_id"])
        candidates_by_event[key].append(candidate)

    results: dict[str, Any] = {}
    for baseline_name, predictor in BASELINE_PREDICTORS.items():
        errors = []
        predicted_by_event: dict[tuple, dict[str, float]] = {}
        for event in events:
            key = (event["season"], event["week"], event["team"], event["removed_player_id"])
            teammates = candidates_by_event.get(key, [])
            if not teammates:
                continue
            predictions = predictor(event, teammates, history, dimension)
            predicted_by_event[key] = predictions
            for player_id, predicted in predictions.items():
                realized = _realized_share(usage_index, event["season"], event["week"], event["team"], player_id, dimension)
                if realized is None:
                    continue
                errors.append({
                    "season": event["season"], "week": event["week"], "event_type": event["event_type"],
                    "abs_error": abs(predicted - realized),
                })

        if not errors:
            results[baseline_name] = {"n": 0, "mae": None}
            continue

        mae = sum(e["abs_error"] for e in errors) / len(errors)
        by_season: dict[int, list[float]] = defaultdict(list)
        by_event_type: dict[str, list[float]] = defaultdict(list)
        by_era: dict[str, list[float]] = defaultdict(list)
        by_season_half: dict[str, list[float]] = defaultdict(list)
        for e in errors:
            by_season[e["season"]].append(e["abs_error"])
            by_event_type[e["event_type"]].append(e["abs_error"])
            by_era[_era_bucket(e["season"])].append(e["abs_error"])
            by_season_half[_season_half_bucket(e["week"])].append(e["abs_error"])

        mass_balance = compute_mass_balance_diagnostics(events, history, predicted_by_event, dimension)

        results[baseline_name] = {
            "n": len(errors),
            "mae": mae,
            "mae_by_season": {season: sum(v) / len(v) for season, v in sorted(by_season.items())},
            "mae_by_event_type": {k: sum(v) / len(v) for k, v in by_event_type.items()},
            "mae_by_era": {k: sum(v) / len(v) for k, v in by_era.items()},
            "mae_by_season_half": {k: sum(v) / len(v) for k, v in by_season_half.items()},
            "mass_balance": mass_balance,
        }
    return results


__all__ = [
    "NO_ADJUSTMENT",
    "PROPORTIONAL_TEAMMATE_REDISTRIBUTION",
    "DEPTH_CHART_NEXT_MAN",
    "RECENT_USAGE_NEXT_MAN",
    "BASELINE_NAMES",
    "BASELINE_PREDICTORS",
    "DIMENSION_RELEVANT_EVENT_TYPES",
    "predict_no_adjustment",
    "predict_proportional_redistribution",
    "predict_depth_chart_next_man",
    "predict_recent_usage_next_man",
    "evaluate_baselines",
]
