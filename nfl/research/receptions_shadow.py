#!/usr/bin/env python3
"""Conservative B0 receptions shadow scorer for prospective NFL research.

This module is NOT a public pick selector. It mirrors
`passing_yards_shadow.py`'s structure and math exactly where the concept is
generic, and adapts only what is genuinely receptions-specific:

- computes the frozen B0 rolling receptions projection from prior
  appearances,
- converts two-sided American odds to de-vigged market probabilities,
- maps the model/line gap through a historical B0 residual distribution,
- emits research direction and edge while hard-coding SHADOW_ONLY status.

Any future public promotion requires separate prospective evidence and an
explicit selector/promotion decision.

Two design choices worth recording explicitly:

1. Market math reuse. `american_implied_probability` and `devig_two_sided`
   are pure American-odds math with zero passing-yards- or receptions-
   specific logic. This module imports them directly from
   `passing_yards_shadow` rather than duplicating them, because duplicating
   verbatim math across two modules is a worse long-term outcome than one
   import edge between two research modules in the same package -- a bug fix
   to the de-vig math should not need to be applied twice. The task scope for
   this change explicitly excludes editing `passing_yards_shadow.py`, so
   extracting a third, market-generic module (e.g. `market_math.py`) is not
   done here; if a third market needs this math, that extraction is the
   right next step and should not be blocked on this PR.
2. No role-continuity/team-change quarantine analog. `passing_yards_shadow`'s
   `role_continuity` exists because prior QB research found team-change
   corrections unstable enough to justify a fail-closed diagnostic. The same
   question was investigated here empirically on the pinned 1999-2025
   receiver corpus: comparing B0's absolute error in prior-appearance pairs
   where the player's team this week matches his most recent prior team
   (n=95,353, MAE 1.464) against pairs where it does not (n=2,462, MAE
   1.277), a team change did NOT show the meaningfully worse pattern that
   would justify hard-coding a quarantine gate -- if anything the raw
   comparison ran slightly the other way, most plausibly because traded
   receivers skew toward lower target-share/lower-volume roles rather than
   because continuity itself drives the gap (this comparison does not
   control for volume, so it is suggestive, not conclusive). Given no
   test-scope challenger and no evidence pointing the same direction as the
   QB case, no quarantine function is added here. This is a considered
   omission, not an oversight: a future study that controls for role/volume
   before and after a trade would be the right way to revisit it, and is out
   of scope for this B0 build.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

from nfl.research.passing_yards_shadow import (  # noqa: F401 (re-exported)
    american_implied_probability,
    devig_two_sided,
)


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


def current_b0_projection(
    prior_appearances: Sequence[dict],
) -> dict[str, float | int]:
    """Compute frozen B0 from caller-supplied strictly prior appearances.

    The caller owns chronology. Only the last five supplied appearances are
    used, matching the historical B0 rolling-window contract.

    Opportunity/role is `effective_targets = max(targets, receptions)` per
    appearance, not raw `targets` alone -- see
    `receptions_baseline_research`'s module docstring for the confirmed
    2003-2008 nflverse targets-column coverage gap that makes the max()
    necessary. A completed reception is definitional proof of a target, so
    this is a safe, monotonic fallback rather than a data-quality workaround.
    """
    rows = list(prior_appearances)
    if len(rows) < MIN_HISTORY:
        raise ValueError("B0 requires at least three prior appearances")
    rows = rows[-WINDOW:]

    receptions = [_finite(r.get("receptions"), "receptions") for r in rows]
    targets = [_finite(r.get("targets"), "targets") for r in rows]
    effective_targets = [max(t, r) for t, r in zip(targets, receptions)]

    rolling_role = statistics.fmean(effective_targets)
    if rolling_role <= 0:
        raise ValueError("B0 requires positive prior receiving opportunity role")

    projection = statistics.fmean(receptions)
    if projection < 0:
        raise ValueError("negative receptions projection")

    return {
        "projection": projection,
        "rolling_effective_targets": rolling_role,
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

    OVER occurs when residual > (line - projection). NFL receptions prop
    lines are normally half-value thresholds, but this function still
    handles equality conservatively as neither an OVER nor an UNDER
    observation. Add-one/Laplace smoothing prevents 0%/100% estimates.

    Identical math/shape to `passing_yards_shadow.empirical_side_probabilities`.
    Reproduced here rather than imported: unlike the two-sided de-vig helpers,
    this function is the model-side half of the B0/shadow contract this
    module owns end to end (paired with `current_b0_projection`,
    `score_shadow_candidate`, and the receptions-specific residual
    generation a caller builds from `receptions_baseline_research`'s output),
    so keeping it local mirrors passing_yards_shadow's own self-contained
    shape instead of importing one function out of a matched trio.
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
