#!/usr/bin/env python3
"""Frozen prior-only SHRINK50 baseline for NFL team pass attempts.

Research status
---------------
This is the simplest team-volume challenger that survived:
- 2023-2025 diagnostic screening against the 5-game rolling baseline, and
- a preregistered untouched 2019 holdout.

It is NOT a production pick model. It does not fetch data, infer starters,
read sportsbook lines, or publish anything.

Frozen formula
--------------
B0 = mean official team pass attempts over the team's prior up-to-5 REG games,
     requiring at least 3.
L  = mean official team pass attempts across league REG team-games strictly
     prior to the prediction week.

prediction = 0.50 * B0 + 0.50 * L

The caller is responsible for supplying PRIOR-ONLY histories. This module
deliberately has no clock, schedule, or network access.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any


TEAM_WINDOW = 5
MIN_HISTORY = 3
SHRINK_WEIGHT = 0.50


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric {label}: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {label}: {value!r}")
    if out < 0:
        raise ValueError(f"negative {label}: {out}")
    return out


def _validated(values: Sequence[Any], label: str) -> list[float]:
    return [
        _finite_nonnegative(value, f"{label} value")
        for value in values
    ]


def predict_from_prior(
    team_history: Sequence[Any],
    league_prior_attempts: Sequence[Any],
) -> dict[str, float | int]:
    """Return the exact frozen SHRINK50 prediction from prior-only inputs.

    team_history may contain more than five games; only the most recent
    five are used. At least three are required.

    league_prior_attempts is the caller-supplied strictly-prior league-wide
    REG team-game population. It must be non-empty.
    """
    team_values = _validated(team_history, "team history")
    if len(team_values) < MIN_HISTORY:
        raise ValueError(
            f"team history requires at least {MIN_HISTORY} prior games"
        )
    team_values = team_values[-TEAM_WINDOW:]

    league_values = _validated(
        league_prior_attempts,
        "league prior",
    )
    if not league_values:
        raise ValueError("league prior requires at least one observation")

    team_mean = statistics.fmean(team_values)
    league_prior_mean = statistics.fmean(league_values)
    prediction = (
        (1.0 - SHRINK_WEIGHT) * team_mean
        + SHRINK_WEIGHT * league_prior_mean
    )

    if not math.isfinite(prediction) or prediction < 0:
        raise ValueError("invalid SHRINK50 prediction")

    return {
        "prediction": prediction,
        "team_mean": team_mean,
        "league_prior_mean": league_prior_mean,
        "team_history_used": len(team_values),
        "league_history_used": len(league_values),
        "shrink_weight": SHRINK_WEIGHT,
    }
