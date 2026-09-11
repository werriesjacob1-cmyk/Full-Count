#!/usr/bin/env python3
"""B2-ROLE pass-attempt challenger.

Scientific premise
------------------
The rejected B1 model used a five-appearance ratio-of-sums QB share whenever
the QB's appearance window differed from the current team's game window.
A preregistered signal audit found that the QB's MOST RECENT prior-appearance
share predicts current share better on those discontinuity rows in 2023,
2024, and 2025.

B2-ROLE changes only that mechanism:
- ALIGNED history: preserve frozen B0 exactly.
- MISSED_GAMES / TEAM_CHANGE / WINDOW_LENGTH_MISMATCH:
    current-team prior-5 pass-attempt mean * last prior-appearance QB share.

There are no fitted coefficients and no tuned thresholds.
"""
from __future__ import annotations

import math


DISCONTINUITY_CATEGORIES = frozenset({
    "MISSED_GAMES",
    "TEAM_CHANGE",
    "WINDOW_LENGTH_MISMATCH",
})


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


def predict_attempts(
    *,
    b0_prediction: float,
    projected_team_pass_attempts: float,
    last_attempt_share: float,
    alignment_category: str,
) -> float:
    """Return one B2-ROLE pass-attempt prediction."""
    base = _finite_nonnegative(b0_prediction, "b0_prediction")
    team_attempts = _finite_nonnegative(
        projected_team_pass_attempts,
        "projected_team_pass_attempts",
    )

    try:
        share = float(last_attempt_share)
    except (TypeError, ValueError) as exc:
        raise ValueError("non-numeric last_attempt_share") from exc
    if not math.isfinite(share):
        raise ValueError("non-finite last_attempt_share")
    if share < 0.0 or share > 1.0:
        raise ValueError("last_attempt_share outside [0, 1]")

    category = str(alignment_category or "").strip().upper()
    if category == "ALIGNED":
        return base
    if category == "NO_HISTORY":
        raise ValueError("NO_HISTORY is not eligible for B2-ROLE")
    if category not in DISCONTINUITY_CATEGORIES:
        raise ValueError(f"unsupported alignment category: {category!r}")

    prediction = team_attempts * share
    if not math.isfinite(prediction):
        raise ValueError("non-finite B2-ROLE prediction")
    return prediction
