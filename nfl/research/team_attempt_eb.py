#!/usr/bin/env python3
"""Prior-only empirical-Bayes baseline for NFL team pass attempts.

This module is intentionally small and pure. It does not fetch data, inspect
future outcomes, score player props, or publish picks.

For a set of team histories available before a prediction week:
1. compute each team's mean official pass attempts,
2. estimate the league grand mean across team means,
3. estimate latent between-team variance as observed variance of team means
   minus average sampling variance of those means, floored at zero,
4. shrink each team's own mean toward the league grand mean by

       weight = tau^2 / (tau^2 + s_team^2 / n)

This is a normal-normal empirical-Bayes shrinkage rule with all hyperparameters
estimated from prior-only team histories.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


_EPS = 1e-12


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


def _history(values: Sequence[Any]) -> list[float]:
    out = [
        _finite_nonnegative(value, "attempt history value")
        for value in values
    ]
    if len(out) < 2:
        raise ValueError("attempt history requires at least two values")
    return out


def _sample_variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("sample variance requires at least two values")
    mean = statistics.fmean(values)
    return sum((x - mean) ** 2 for x in values) / (len(values) - 1)


def estimate_hyperprior(
    team_histories: Mapping[str, Sequence[Any]],
    *,
    min_teams: int = 24,
) -> dict[str, float | int]:
    """Estimate prior-only league shrinkage hyperparameters.

    Teams are weighted equally at the hyperprior level. Each team contributes
    one historical mean and one estimated sampling variance of that mean.
    """
    try:
        min_teams = int(min_teams)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_teams must be an integer") from exc
    if min_teams < 2:
        raise ValueError("min_teams must be at least 2")

    stats: list[tuple[float, float]] = []
    for raw_team, values in team_histories.items():
        team = str(raw_team or "").strip()
        if not team:
            raise ValueError("team histories contain an empty team")
        hist = _history(values)
        mean = statistics.fmean(hist)
        sampling_variance = _sample_variance(hist) / len(hist)
        stats.append((mean, sampling_variance))

    if len(stats) < min_teams:
        raise ValueError(
            f"insufficient teams for hyperprior: {len(stats)} < {min_teams}"
        )

    means = [x[0] for x in stats]
    sampling_variances = [x[1] for x in stats]

    grand_mean = statistics.fmean(means)
    observed_between_variance = _sample_variance(means)
    mean_sampling_variance = statistics.fmean(sampling_variances)
    tau2 = max(0.0, observed_between_variance - mean_sampling_variance)

    return {
        "teams": len(stats),
        "grand_mean": grand_mean,
        "observed_between_variance": observed_between_variance,
        "mean_sampling_variance": mean_sampling_variance,
        "tau2": tau2,
    }


def predict_from_history(
    history: Sequence[Any],
    *,
    grand_mean: Any,
    tau2: Any,
) -> dict[str, float]:
    """Shrink one team's historical attempt mean toward the league mean."""
    hist = _history(history)
    league_mean = _finite_nonnegative(grand_mean, "grand_mean")
    latent_variance = _finite_nonnegative(tau2, "tau2")

    team_mean = statistics.fmean(hist)
    sample_variance = _sample_variance(hist)
    sampling_variance = sample_variance / len(hist)

    if latent_variance <= _EPS:
        weight = 0.0
    elif sampling_variance <= _EPS:
        weight = 1.0
    else:
        weight = latent_variance / (latent_variance + sampling_variance)

    if not (0.0 <= weight <= 1.0):
        raise ValueError(f"invalid empirical-Bayes weight: {weight}")

    prediction = league_mean + weight * (team_mean - league_mean)
    if not math.isfinite(prediction) or prediction < 0:
        raise ValueError("invalid empirical-Bayes prediction")

    return {
        "prediction": prediction,
        "weight": weight,
        "team_mean": team_mean,
        "sample_variance": sample_variance,
        "sampling_variance": sampling_variance,
        "grand_mean": league_mean,
        "tau2": latent_variance,
    }
