#!/usr/bin/env python3
"""B2-QB component-aware rushing-attempt challenger.

Scientific hypothesis
---------------------
Current B1 rescales the QB's entire rolling rushing-attempt history by a
current-team carry-volume factor. That treats three different processes as if
team rushing volume drove all of them equally:

- designed QB rushes: plausibly play-calling / offensive run-volume sensitive
- scrambles: largely player tendency + pressure/coverage response
- kneels: game-state / clock-management artifacts

B2-QB is a deliberately minimal structural ablation:

    predicted QB carries
      = rolling scrambles
      + rolling kneels
      + rolling designed rushes * B1 team-window scale

There are no fitted weights and no tuned thresholds. The experiment asks
whether restricting B1's rescaling to the football process it can plausibly
represent repairs the QB-rushing damage.

Research only. This module does not fetch data, score sportsbook lines, select
picks, publish, or promote anything.
"""
from __future__ import annotations

import math


def _nonnegative(value: float, name: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric B2-QB input: {name}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite B2-QB input: {name}")
    if value < 0.0:
        raise ValueError(f"negative B2-QB input: {name}")
    return value


def predict_attempts(
    *,
    rolling_designed_rush_attempts: float,
    rolling_scramble_attempts: float,
    rolling_kneel_attempts: float,
    team_window_scale: float,
) -> float:
    """Predict QB carries using process-specific treatment.

    Only designed rushing receives the B1 current-team volume rescaling.
    Scrambles and kneels retain the player's own prior rolling means.
    """
    designed = _nonnegative(
        rolling_designed_rush_attempts,
        "rolling_designed_rush_attempts",
    )
    scrambles = _nonnegative(
        rolling_scramble_attempts,
        "rolling_scramble_attempts",
    )
    kneels = _nonnegative(
        rolling_kneel_attempts,
        "rolling_kneel_attempts",
    )
    scale = _nonnegative(team_window_scale, "team_window_scale")

    prediction = designed * scale + scrambles + kneels
    if not math.isfinite(prediction):
        raise ValueError("non-finite B2-QB prediction")
    return prediction
