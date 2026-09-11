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


def _empty_metrics(stat: str) -> dict:
    return {
        "stat": stat,
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
