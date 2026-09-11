#!/usr/bin/env python3
"""Frozen NFL team pass-attempt shrinkage challenger.

Scientific provenance
---------------------
A preregistered diagnostic screen on 2023-2025 selected exactly 50% shrinkage
of the frozen five-game team pass-attempt mean toward the strictly prior
league-wide regular-season pass-attempt mean.

This module intentionally exposes no tunable shrinkage parameter. Any future
change to the weight requires a new preregistered experiment.
"""
from __future__ import annotations

import math

SHRINK_WEIGHT = 0.50


def _finite_nonnegative(value: float, name: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric {name}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite {name}")
    if value < 0:
        raise ValueError(f"negative {name}")
    return value


def predict_team_attempts(
    *,
    rolling_team_attempts: float,
    strictly_prior_league_mean: float,
) -> float:
    """Return the frozen 50/50 shrinkage prediction for team pass attempts."""
    rolling = _finite_nonnegative(
        rolling_team_attempts,
        "rolling_team_attempts",
    )
    league = _finite_nonnegative(
        strictly_prior_league_mean,
        "strictly_prior_league_mean",
    )

    prediction = (
        (1.0 - SHRINK_WEIGHT) * rolling
        + SHRINK_WEIGHT * league
    )
    if not math.isfinite(prediction):
        raise ValueError("non-finite shrinkage prediction")
    return prediction
