#!/usr/bin/env python3
"""NEVER POOL SPORTS. No metric may blend MLB and NFL rows. Enforcement 4.

WHY THIS IS A HARD REFUSAL AND NOT A WARNING. Full Count already has the
cautionary result on file. Pooled across 15 MLB markets, cross-market AUC read
0.748 [0.721, 0.777] — which looks like real skill and is not. It was BASE-RATE
SEPARATION: the model was distinguishing easy markets from hard ones, while
within-market ranking skill was null (AUC 0.492 [0.461, 0.521]). Pooling
manufactured an impressive number out of heterogeneous base rates.

Mixing two SPORTS is that same error with a much larger gap. NFL and MLB prop
markets have different base rates, different market efficiency, different
sample sizes, and different dependence structure. A combined hit rate would move
mostly with the MLB/NFL mix of the sample rather than with anything about
accuracy, and it would look best exactly when the mix was most lopsided.

So a pooled performance number is not a slightly-worse metric. It is a
misleading one, and the only safe handling is to refuse to compute it.

ADMINISTRATIVE OPERATIONS ARE DIFFERENT, AND THE DIFFERENCE MUST BE EXPLICIT.
Counting rows, listing dates, checking for duplicate ids, verifying an estate is
readable — none of these assert anything about accuracy, and all of them
legitimately span sports. Those callers pass `administrative=True` and must say
why. The flag exists so that pooling is always a visible, justified decision at
the call site instead of an accident.

WHERE THIS IS AND IS NOT WIRED IN, STATED PLAINLY. It guards NFL-side and
future shared metric code. It is deliberately NOT wired into MLB's existing
accuracy paths (accuracy_lab.py, eval_lib.py, model_health_report.py,
backtest/), because doing so would edit frozen MLB production and would require
golden-file byte-parity proof for no present benefit: there are no NFL rows
anywhere yet, so there is nothing today that MLB code could pool with. Wiring it
into MLB is a later decision for Jacob, and it should happen BEFORE the first
NFL row exists, not after.
"""
from __future__ import annotations

from typing import Iterable, Optional

MLB = "mlb"
NFL = "nfl"
KNOWN_SPORTS = frozenset((MLB, NFL))


class PooledSportsError(ValueError):
    """A performance metric was asked to span more than one sport."""


class UnknownSportError(ValueError):
    """A row's sport could not be determined, so it cannot be partitioned."""


def sport_of(row) -> str:
    """Determine a row's sport from its identity namespace.

    Reads the canonical id rather than a separate `sport` field, because a
    `sport` field can disagree with the identity while a namespace cannot: MLB
    ids are `fc2:…` and NFL ids are `fcnfl1:…`, and those literals are the same
    thing the identity itself is built from.

    An explicit `sport` key is honoured when present AND consistent. A row whose
    declared sport contradicts its identity namespace raises, because silently
    trusting either one is how mislabelled rows enter a metric.
    """
    if not isinstance(row, dict):
        raise UnknownSportError(f"row is not a mapping: {type(row).__name__}")

    identity = row.get("id")
    from_identity: Optional[str] = None
    if isinstance(identity, str) and identity:
        if identity.startswith("fcnfl1:"):
            from_identity = NFL
        elif identity.startswith("fc2:"):
            from_identity = MLB

    declared = row.get("sport")
    if isinstance(declared, str) and declared:
        declared = declared.strip().lower()
        if declared not in KNOWN_SPORTS:
            raise UnknownSportError(f"unknown declared sport {declared!r}")
        if from_identity and declared != from_identity:
            raise UnknownSportError(
                f"row declares sport {declared!r} but its id {identity!r} is in "
                f"the {from_identity!r} identity namespace. A mislabelled row "
                "must not be silently assigned to either."
            )
        return declared

    if from_identity:
        return from_identity
    raise UnknownSportError(
        f"cannot determine the sport of row with id {identity!r}. A row whose "
        "sport is unknown must not be counted in any per-sport metric — "
        "missing evidence is never a pass."
    )


def sports_present(rows: Iterable) -> set:
    return {sport_of(row) for row in rows}


def assert_single_sport(
    rows: Iterable,
    metric_name: str,
    administrative: bool = False,
    reason: str = "",
) -> Optional[str]:
    """Gate a multi-row calculation. Returns the single sport, or None if empty.

    Call this at the top of ANY function that computes a hit rate, accuracy,
    AUC, calibration figure, edge, or ROI over a collection of rows.

    `administrative=True` permits spanning sports for operations that assert
    nothing about performance (counting, listing, integrity checks). It requires
    a `reason`, so the justification is recorded at the call site rather than
    inferred later from the flag alone.
    """
    rows = list(rows)
    if administrative:
        if not reason.strip():
            raise ValueError(
                f"{metric_name}: administrative=True requires a reason naming "
                "the non-performance operation being performed. Pooling sports "
                "must always be a stated decision."
            )
        return None
    present = sports_present(rows)
    if len(present) > 1:
        raise PooledSportsError(
            f"{metric_name} was given rows from {sorted(present)}. A performance "
            "metric may never blend sports: the result would track the sample's "
            "sport mix rather than accuracy. Full Count has this exact failure "
            "on record — pooled cross-market AUC read 0.748 while within-market "
            "ranking skill was null at 0.492, because pooling measured base-rate "
            "separation. Compute per sport and report separately. If this "
            "genuinely is not a performance calculation, pass "
            "administrative=True with a reason."
        )
    return next(iter(present)) if present else None


def partition_by_sport(rows: Iterable) -> dict:
    """Split rows per sport so each can be measured on its own terms."""
    out: dict[str, list] = {}
    for row in rows:
        out.setdefault(sport_of(row), []).append(row)
    return out
