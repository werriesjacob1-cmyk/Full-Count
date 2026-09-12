#!/usr/bin/env python3
"""Conservative B0 passing-yards shadow scorer for prospective NFL research.

This module is NOT a public pick selector. It:
- computes the frozen B0 rolling passing-yards projection from prior appearances,
- converts two-sided American odds to de-vigged market probabilities,
- maps the model/line gap through a historical B0 residual distribution,
- emits research direction and edge while hard-coding SHADOW_ONLY status.

Any future public promotion requires separate prospective evidence and an
explicit selector/promotion decision.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any


MIN_HISTORY = 3
WINDOW = 5


def _finite(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric {label}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {label}")
    return out


def role_continuity(
    current_team: str,
    prior_appearances: Sequence[dict],
) -> dict[str, Any]:
    """Fail closed when the QB's current team differs from his latest prior team.

    This is an eligibility/quarantine diagnostic, not a replacement model.
    Earlier research showed team-change corrections were unstable, so the
    conservative launch behavior is to preserve the observation but refuse to
    clear it as SHADOW_ONLY when team identity changed.
    """
    current = str(current_team or "").strip().upper()
    if not current:
        raise ValueError("current_team is required")

    rows = list(prior_appearances)
    if not rows:
        return {
            "role_continuity_status": "UNKNOWN_NO_HISTORY",
            "role_continuity_gate_pass": False,
            "prior_team": None,
        }

    prior_team = str(rows[-1].get("team") or "").strip().upper()
    if not prior_team:
        return {
            "role_continuity_status": "UNKNOWN_PRIOR_TEAM",
            "role_continuity_gate_pass": False,
            "prior_team": None,
        }

    if prior_team != current:
        return {
            "role_continuity_status": "TEAM_CHANGE_ROLE_UNCERTAINTY",
            "role_continuity_gate_pass": False,
            "prior_team": prior_team,
        }

    return {
        "role_continuity_status": "ROLE_CONTINUITY_CONFIRMED",
        "role_continuity_gate_pass": True,
        "prior_team": prior_team,
    }


def american_implied_probability(odds: int | float) -> float:
    """Return raw implied probability from non-zero American odds."""
    o = _finite(odds, "American odds")
    if o == 0:
        raise ValueError("American odds cannot be zero")
    if o > 0:
        return 100.0 / (o + 100.0)
    return (-o) / ((-o) + 100.0)


def devig_two_sided(
    over_odds: int | float,
    under_odds: int | float,
) -> dict[str, float]:
    """Normalize two raw implied probabilities to a 100% two-sided market."""
    over_raw = american_implied_probability(over_odds)
    under_raw = american_implied_probability(under_odds)
    total = over_raw + under_raw
    if total <= 0:
        raise ValueError("invalid two-sided hold")
    return {
        "over": over_raw / total,
        "under": under_raw / total,
        "raw_over": over_raw,
        "raw_under": under_raw,
        "hold": total - 1.0,
    }


def current_b0_projection(
    prior_appearances: Sequence[dict],
) -> dict[str, float | int]:
    """Compute frozen B0 from caller-supplied strictly prior appearances.

    The caller owns chronology. Only the last five supplied appearances are
    used, matching the historical B0 rolling-window contract.
    """
    rows = list(prior_appearances)
    if len(rows) < MIN_HISTORY:
        raise ValueError("B0 requires at least three prior appearances")
    rows = rows[-WINDOW:]

    yards = [_finite(r.get("passing_yards"), "passing_yards") for r in rows]
    attempts = [_finite(r.get("attempts"), "attempts") for r in rows]

    rolling_attempts = statistics.fmean(attempts)
    if rolling_attempts <= 0:
        raise ValueError("B0 requires positive prior passing attempt role")

    projection = statistics.fmean(yards)
    if projection < 0:
        raise ValueError("negative passing-yards projection")

    return {
        "projection": projection,
        "rolling_attempts": rolling_attempts,
        "history_n_used": len(rows),
    }


def empirical_side_probabilities(
    *,
    projection: float,
    line: float,
    residuals: Sequence[float],
) -> dict[str, float | int]:
    """Estimate side probability from historical B0 residuals.

    Residual convention:
        residual = actual - projection

    OVER occurs when residual > (line - projection).
    NFL passing-yard prop lines are normally half-yard values, but this function
    still handles equality conservatively as neither an OVER nor an UNDER
    observation. Add-one/Laplace smoothing prevents 0%/100% estimates.
    """
    p = _finite(projection, "projection")
    l = _finite(line, "line")
    values = [_finite(v, "residual") for v in residuals]
    if not values:
        raise ValueError("at least one residual is required")

    threshold = l - p
    over_n = sum(v > threshold for v in values)
    under_n = sum(v < threshold for v in values)
    push_n = len(values) - over_n - under_n

    over_effective = over_n + 0.5 * push_n
    n = len(values)
    over = (over_effective + 1.0) / (n + 2.0)
    under = 1.0 - over

    return {
        "over": over,
        "under": under,
        "residual_n": n,
        "threshold": threshold,
        "over_observations": over_n,
        "under_observations": under_n,
        "push_observations": push_n,
    }


def score_shadow_candidate(
    *,
    projection: float,
    line: float,
    over_odds: int | float,
    under_odds: int | float,
    residuals: Sequence[float],
) -> dict[str, Any]:
    """Score one candidate for research capture without publishing a PLAY."""
    model = empirical_side_probabilities(
        projection=projection,
        line=line,
        residuals=residuals,
    )
    market = devig_two_sided(over_odds, under_odds)

    over_edge = float(model["over"]) - market["over"]
    under_edge = float(model["under"]) - market["under"]

    if over_edge > under_edge:
        direction = "OVER"
        edge = over_edge
    elif under_edge > over_edge:
        direction = "UNDER"
        edge = under_edge
    else:
        direction = "NEUTRAL"
        edge = 0.0

    return {
        "decision_status": "SHADOW_ONLY",
        "research_direction": direction,
        "research_edge": edge,
        "projection": _finite(projection, "projection"),
        "line": _finite(line, "line"),
        "line_gap": _finite(projection, "projection") - _finite(line, "line"),
        "model_over_probability": float(model["over"]),
        "model_under_probability": float(model["under"]),
        "market_fair_over_probability": market["over"],
        "market_fair_under_probability": market["under"],
        "market_hold": market["hold"],
        "residual_n": int(model["residual_n"]),
        "probability_method": "pooled_B0_empirical_residuals_laplace",
    }
