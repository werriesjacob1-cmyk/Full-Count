#!/usr/bin/env python3
"""Frozen NEGATIVE_BINOMIAL_POOLED challenger for the receptions market.

Closes the exact gap this repo's own self-validating `market_registry.json`
already flagged (`receptions_alt` entry: `"model": null`) between the merged
outcome-distribution research (PR #159, `receptions_outcome_distribution.py`)
and a real, directly comparable challenger-vs-B0 probability pair for a real
line. This module does NOT wire into `receptions_shadow.py` or any live
workflow -- it is a standalone, research-only scoring library, matching the
"smallest reliable implementation that preserves existing model and market
boundaries" instruction rather than assuming a single function belongs
inside the live-board module.

## Why a frozen, pre-registered fit rather than fitting live per call

Every prior audit/repair in this project (role-data, receptions PMF/ladder)
was independently caught overclaiming or under-specifying something when a
result depended on code path or timing. Freezing `FROZEN_NB_FIT` here as an
explicit, documented, one-time, reproducible constant -- rather than
re-fitting inside a live scoring call -- means every candidate scored with
it is scored against the IDENTICAL frozen model, the same discipline B0
itself already follows with its own frozen rolling-window contract.

`FROZEN_NB_FIT` is not fit inside this module. It is the reproducible
result of running the already-merged, unmodified
`receptions_outcome_distribution.fit_negative_binomial_alpha` against the
exact pinned 1999-2025 nflverse corpus (audit manifest
`engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`,
train partition `season <= 2022`) -- independently reproduced on
2026-09-20 by live re-fetching all 27 season files and verifying each one
byte-for-byte AND SHA-256-identical to the pinned digest before fitting
(27/27 verified). The resulting `n=85,670` training rows and n=85,720
total scored training rows exactly match PR #159's own already-reported,
independently-reviewed figures -- not a new, unverified number.

No model/selector/public-pick promotion. `RESEARCH_ONLY_NOT_PROMOTED`
throughout. No historical sportsbook price is fabricated anywhere in this
module -- `over_price`/`under_price` are always caller-supplied real
values, exactly matching `receptions_alt_ladder.py`'s own established
convention, or omitted entirely.
"""
from __future__ import annotations

from typing import Any

from nfl.research.receptions_outcome_distribution import negative_binomial_pmf

FROZEN_NB_FIT: dict[str, Any] = {
    "distribution_family": "NEGATIVE_BINOMIAL_POOLED",
    "alpha": 0.09323867966867905,
    "n_train_rows": 85670,
    "n_train_rows_scored_total": 85720,
    "n_held_out_rows": 12095,
    "train_partition": "season <= 2022",
    "held_out_partition": "2023 <= season <= 2025",
    "source_audit_manifest": (
        "engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"
    ),
    "fit_method": (
        "receptions_outcome_distribution.fit_negative_binomial_alpha "
        "(pooled, no opportunity bucketing)"
    ),
    "fit_reproduced_at": "2026-09-20",
    "status": "RESEARCH_ONLY_NOT_PROMOTED",
}

# NB2's support is technically unbounded; a real receptions total essentially
# never exceeds this in the pinned corpus (WR100-plus outlier seasons still
# top out well under 25 receptions in a single game). Summing pmf mass out
# to this bound keeps over+under+push within float tolerance of 1 without
# an unbounded loop.
MAX_SUPPORT = 60


class FrozenChallengerError(ValueError):
    """Raised on malformed input. Never silently substitutes a guess."""


def negative_binomial_side_probabilities(
    *, projection: float, line: float, alpha: float | None = None
) -> dict[str, Any]:
    """Frozen-NB over/under/push probabilities for one real (projection, line)
    pair, in the SAME output shape as
    `receptions_shadow.empirical_side_probabilities` (`over`/`under`) plus an
    explicit `push` field, so a caller can diff this against B0's own output
    for the identical real candidate.

    `alpha` defaults to the frozen fit above; a caller may override it only
    for research/sensitivity purposes -- doing so leaves `FROZEN_NB_FIT`
    itself untouched, so overriding never silently changes what "the frozen
    challenger" means going forward.
    """
    if not isinstance(projection, (int, float)) or projection <= 0:
        raise FrozenChallengerError(f"projection must be positive: {projection!r}")
    if not isinstance(line, (int, float)) or line < 0:
        raise FrozenChallengerError(f"line must be nonnegative: {line!r}")
    used_alpha = FROZEN_NB_FIT["alpha"] if alpha is None else alpha
    if not isinstance(used_alpha, (int, float)) or used_alpha <= 0:
        raise FrozenChallengerError(f"alpha must be positive: {used_alpha!r}")

    under_mass = 0.0
    push_mass = 0.0
    for k in range(0, MAX_SUPPORT + 1):
        p_k = negative_binomial_pmf(k, float(projection), float(used_alpha))
        if k < line:
            under_mass += p_k
        elif k == line:
            push_mass += p_k
    over_mass = max(0.0, 1.0 - under_mass - push_mass)

    return {
        "over": over_mass,
        "under": under_mass,
        "push": push_mass,
        "distribution_family": FROZEN_NB_FIT["distribution_family"],
        "alpha_used": used_alpha,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }


def compare_b0_vs_frozen_challenger(
    *,
    projection: float,
    line: float,
    b0_over: float,
    b0_under: float,
    over_price: Any = None,
    under_price: Any = None,
) -> dict[str, Any]:
    """Side-by-side comparison record for one real candidate: B0's own
    already-computed over/under (caller supplies these, e.g. from
    `receptions_shadow.empirical_side_probabilities`) against the frozen NB
    challenger computed here for the IDENTICAL (projection, line).

    Never computes B0's own probability itself -- reuses the caller's real
    number rather than re-deriving a second, possibly-divergent copy of B0's
    logic. EV is attached only when a caller supplies a real price; no price
    is ever invented. Every EV result carries `evidence_status=
    "UNVALIDATED_RESEARCH"` via `alternate_line_evaluation.expected_value_
    from_probability`, so it is never presented as validated.
    """
    if not (0.0 <= b0_over <= 1.0) or not (0.0 <= b0_under <= 1.0):
        raise FrozenChallengerError("b0_over/b0_under must each be in [0, 1]")

    challenger = negative_binomial_side_probabilities(projection=projection, line=line)

    result: dict[str, Any] = {
        "projection": projection,
        "line": line,
        "b0": {"over": b0_over, "under": b0_under},
        "challenger": dict(challenger),
        "absolute_over_probability_gap": abs(b0_over - challenger["over"]),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }

    if over_price is not None or under_price is not None:
        from nfl.research.alternate_line_evaluation import expected_value_from_probability

        if over_price is not None:
            result["challenger"]["over_ev"] = expected_value_from_probability(
                challenger["over"], over_price, evidence_status="UNVALIDATED_RESEARCH"
            )
        if under_price is not None:
            result["challenger"]["under_ev"] = expected_value_from_probability(
                challenger["under"], under_price, evidence_status="UNVALIDATED_RESEARCH"
            )

    return result


__all__ = [
    "FROZEN_NB_FIT",
    "FrozenChallengerError",
    "negative_binomial_side_probabilities",
    "compare_b0_vs_frozen_challenger",
]
