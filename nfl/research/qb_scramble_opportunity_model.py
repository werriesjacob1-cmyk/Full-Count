#!/usr/bin/env python3
"""B3 QB rush-attempt challenger: dropback-aware scramble opportunity.

Rationale:
- designed QB runs are called run plays -> leave on player rolling history
- scrambles originate from pass/dropback plays -> rescale by team dropback
  opportunity, not team carry volume
- kneels are clock-management events -> leave on player rolling history

No coefficients are fitted. No thresholds are tuned.
"""
from __future__ import annotations

import math


def _nonnegative(value: float, name: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric B3 input: {name}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite B3 input: {name}")
    if value < 0:
        raise ValueError(f"negative B3 input: {name}")
    return value


def predict_attempts(
    *,
    rolling_designed_rush_attempts: float,
    rolling_scramble_attempts: float,
    rolling_kneel_attempts: float,
    dropback_window_scale: float,
) -> float:
    designed = _nonnegative(
        rolling_designed_rush_attempts, "rolling_designed_rush_attempts"
    )
    scrambles = _nonnegative(
        rolling_scramble_attempts, "rolling_scramble_attempts"
    )
    kneels = _nonnegative(
        rolling_kneel_attempts, "rolling_kneel_attempts"
    )
    scale = _nonnegative(dropback_window_scale, "dropback_window_scale")
    prediction = designed + kneels + scrambles * scale
    if not math.isfinite(prediction):
        raise ValueError("non-finite B3 prediction")
    return prediction
