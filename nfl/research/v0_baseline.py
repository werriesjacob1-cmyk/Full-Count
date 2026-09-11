#!/usr/bin/env python3
"""V0 NFL rolling-history baseline evaluator.

B0 is deliberately unsophisticated. It establishes a reproducible floor:
predict the next player-game outcome from the rolling mean of PRIOR games only.

If future coaching/opportunity/matchup models cannot beat this floor out of
sample, they have not earned their complexity.

This module consumes rows produced by nflverse_history.build_prior_only_rows().
It does not fetch data, fit weights, use sportsbook prices, or select picks.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Any


SUPPORTED_STATS = frozenset({
    "attempts",
    "carries",
    "receptions",
    "passing_yards",
    "rushing_yards",
    "receiving_yards",
})

FIRST_WAVE_MARKETS = {
    "pass_attempts": "attempts",
    "rush_attempts": "carries",
    "receptions": "receptions",
    "passing_yards": "passing_yards",
    "rushing_yards": "rushing_yards",
    "receiving_yards": "receiving_yards",
}


# Historical sportsbook eligibility is not reconstructable from weekly box-score
# data alone. V0 therefore uses a deliberately conservative, PRE-GAME-AVAILABLE
# proxy: plausible position for the market + positive PRIOR rolling opportunity.
# This prevents irrelevant structural zeroes from manufacturing low error.
ELIGIBLE_POSITIONS = {
    "attempts": frozenset({"QB"}),
    "passing_yards": frozenset({"QB"}),
    "carries": frozenset({"QB", "RB", "FB", "WR", "TE"}),
    "rushing_yards": frozenset({"QB", "RB", "FB", "WR", "TE"}),
    "receptions": frozenset({"RB", "FB", "WR", "TE"}),
    "receiving_yards": frozenset({"RB", "FB", "WR", "TE"}),
}

ROLE_FEATURE = {
    "attempts": "rolling_attempts",
    "passing_yards": "rolling_attempts",
    "carries": "rolling_carries",
    "rushing_yards": "rolling_carries",
    "receptions": "rolling_targets",
    "receiving_yards": "rolling_targets",
}


def _market_eligible(row: Mapping[str, Any], stat: str) -> bool:
    """Conservative pregame proxy for whether this player belongs in the market.

    Crucially, this function never inspects the CURRENT target. A new role that
    appears for the first time will therefore be missed by B0 rather than
    discovered with hindsight. Later prospective FanDuel capture can replace
    this proxy with the actual posted eligible population.
    """
    position = str(row.get("position") or "").strip().upper()
    if position not in ELIGIBLE_POSITIONS[stat]:
        return False
    features = row.get("features") or {}
    role_value = features.get(ROLE_FEATURE[stat])
    if role_value is None:
        return False
    try:
        role_value = float(role_value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(role_value) and role_value > 0.0


def _empty_metrics(stat: str) -> dict:
    return {
        "stat": stat,
        "eligibility": {
            "positions": sorted(ELIGIBLE_POSITIONS[stat]),
            "prior_role_feature": ROLE_FEATURE[stat],
            "prior_role_rule": "> 0",
        },
        "n": 0,
        "mae": None,
        "rmse": None,
        "bias": None,
        "mean_prediction": None,
        "mean_actual": None,
    }


def evaluate_stat(
    rows: Iterable[Mapping[str, Any]],
    stat: str,
    *,
    test_season: int,
    test_season_type: str = "REG",
    min_history: int = 1,
) -> dict:
    """Evaluate one rolling-mean outcome on one chronological holdout season.

    Error sign convention:
        error = prediction - actual
    so positive bias means the baseline over-predicted.

    Missing rolling features are EXCLUDED. They are never replaced with the
    current target, zero, a league mean, or another favorable value.
    """
    if stat not in SUPPORTED_STATS:
        raise ValueError(f"unsupported V0 stat: {stat}")
    if min_history < 0:
        raise ValueError("min_history must be >= 0")

    feature_name = f"rolling_{stat}"
    pairs = []

    for row in rows:
        try:
            season = int(row.get("season"))
        except (TypeError, ValueError):
            continue
        if season != int(test_season):
            continue
        if str(row.get("season_type")) != str(test_season_type):
            continue

        try:
            history_n = int(row.get("history_n", 0))
        except (TypeError, ValueError):
            continue
        if history_n < min_history:
            continue
        if not _market_eligible(row, stat):
            continue

        features = row.get("features") or {}
        target = row.get("target") or {}
        prediction = features.get(feature_name)
        actual = target.get(stat)

        if prediction is None or actual is None:
            continue

        try:
            prediction = float(prediction)
            actual = float(actual)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(prediction) and math.isfinite(actual)):
            continue

        pairs.append((prediction, actual))

    if not pairs:
        return _empty_metrics(stat)

    errors = [prediction - actual for prediction, actual in pairs]
    abs_errors = [abs(error) for error in errors]
    squared_errors = [error * error for error in errors]
    n = len(pairs)

    return {
        "stat": stat,
        "eligibility": {
            "positions": sorted(ELIGIBLE_POSITIONS[stat]),
            "prior_role_feature": ROLE_FEATURE[stat],
            "prior_role_rule": "> 0",
        },
        "n": n,
        "mae": sum(abs_errors) / n,
        "rmse": math.sqrt(sum(squared_errors) / n),
        "bias": sum(errors) / n,
        "mean_prediction": sum(p for p, _ in pairs) / n,
        "mean_actual": sum(a for _, a in pairs) / n,
    }


def evaluate_first_wave(
    rows: Iterable[Mapping[str, Any]],
    *,
    test_season: int,
    test_season_type: str = "REG",
    min_history: int = 1,
) -> dict:
    """Evaluate the six V0 opportunity/outcome markets separately."""
    materialized = list(rows)
    return {
        "test_season": int(test_season),
        "test_season_type": str(test_season_type),
        "min_history": int(min_history),
        "markets": {
            market: evaluate_stat(
                materialized,
                stat,
                test_season=test_season,
                test_season_type=test_season_type,
                min_history=min_history,
            )
            for market, stat in FIRST_WAVE_MARKETS.items()
        },
    }
