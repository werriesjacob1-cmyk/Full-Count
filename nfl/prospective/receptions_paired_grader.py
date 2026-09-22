#!/usr/bin/env python3
"""Point-in-time-safe postgame paired grading: B0 vs. the frozen NB
challenger, evaluated on the identical real outcome, for every sealed
`receptions_challenger_snapshot` record whose game has gone final.

Reuses the real, unmodified `nfl.research.box_score_outcomes.
outcome_for_candidate` for the actual stat value -- never invents an
outcome. The OVER/UNDER/PUSH convention mirrors `player_prop_grader.py`'s
own documented PRIMARY-market rule exactly (strictly better than the line
= that side; equality = PUSH) -- not reimplemented settlement logic, just
the same real convention applied directly, since this module compares two
PROBABILITIES against one real outcome rather than grading a single
betting side (so `player_prop_grader.grade_player_prop_market`'s stricter
live-market preconditions -- pregame timing, market open/closed status --
don't apply to a re-derived paired-model sealed snapshot being graded
after the fact).

This module does NOT determine whether a game has gone final -- exactly
like `grade_player_prop_board.py`'s own established pattern, that is the
caller's responsibility (only pass `player_outcomes` built from a
genuinely final box score). Never grades a record still `QUARANTINED`
(the eligibility gate itself already said this candidate wasn't a clean
pregame call, so scoring it would manufacture a fair test that never
existed). Never promotes, compares to, or changes B0's own live decision.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from nfl.research.box_score_outcomes import BoxScoreOutcomeError, outcome_for_candidate


class PairedGradeError(ValueError):
    """Raised on malformed input. Never silently substitutes a guess."""


def _clip(p: float, eps: float = 1e-9) -> float:
    return min(max(p, eps), 1.0 - eps)


def grade_paired_receptions_record(
    sealed_record: Mapping[str, Any],
    player_outcomes: Mapping[str, Mapping[str, float]],
) -> dict[str, Any] | None:
    """Grade one sealed `receptions_challenger_snapshot` record against one
    real, final box score's outcome for that player/game.

    Returns `None` -- not a fabricated result -- whenever there is no fair
    test for either model:
      - `decision_status != "SHADOW_ONLY"`: the record was `QUARANTINED`
        by the real eligibility gate, so it was never a clean pregame call.
      - the player did not appear in the final box score (DNP/scratch).
      - the real stat value equals the line exactly (a push -- no side won).
    """
    if not isinstance(sealed_record, Mapping):
        raise PairedGradeError("sealed_record must be a mapping")
    if sealed_record.get("decision_status") != "SHADOW_ONLY":
        return None

    b0_score = sealed_record.get("b0_score") or {}
    challenger_comparison = sealed_record.get("challenger_comparison") or {}
    challenger = challenger_comparison.get("challenger") or {}
    if "model_over_probability" not in b0_score or "over" not in challenger:
        raise PairedGradeError(
            "sealed_record is missing real b0_score/challenger probabilities "
            "-- refusing to grade a record that isn't a genuine sealed pair"
        )

    try:
        outcome = outcome_for_candidate(sealed_record, player_outcomes)
    except BoxScoreOutcomeError as exc:
        raise PairedGradeError(f"cannot determine real outcome: {exc}") from exc
    if not outcome["appeared"]:
        return None

    stat_value = float(outcome["stat_value"])
    line = float(sealed_record["line"])
    if stat_value == line:
        return None
    actual_over = stat_value > line

    b0_over = float(b0_score["model_over_probability"])
    challenger_over = float(challenger["over"])
    if not (0.0 <= b0_over <= 1.0) or not (0.0 <= challenger_over <= 1.0):
        raise PairedGradeError("model probabilities must each be in [0, 1]")

    def proper_score(p_over: float) -> dict[str, float]:
        p_actual = _clip(p_over if actual_over else 1.0 - p_over)
        return {"brier": (1.0 - p_actual) ** 2, "log_loss": -math.log(p_actual)}

    return {
        "event_id": str(sealed_record["event_id"]),
        "gsis_id": str(sealed_record["gsis_id"]),
        "player_name": sealed_record.get("player_name"),
        "line": line,
        "stat_value": stat_value,
        "actual_side": "OVER" if actual_over else "UNDER",
        "b0_over_probability": b0_over,
        "challenger_over_probability": challenger_over,
        "b0": proper_score(b0_over),
        "challenger": proper_score(challenger_over),
    }


def summarize_paired_grades(graded: Sequence[dict[str, Any] | None]) -> dict[str, Any]:
    """Aggregate proper-scoring summaries for both models across every
    gradeable record. Matched volume by construction: both models are
    always scored against the identical real-outcome population produced
    by `grade_paired_receptions_record` -- never a separately-selected
    subset for either side, so there is no volume-comparability question
    to resolve here.
    """
    rows = [g for g in graded if g is not None]
    n = len(rows)
    if n == 0:
        return {"n": 0, "b0": None, "challenger": None}

    def mean_of(model_key: str, metric_key: str) -> float:
        return sum(g[model_key][metric_key] for g in rows) / n

    b0_better = sum(1 for g in rows if g["b0"]["brier"] < g["challenger"]["brier"])
    challenger_better = sum(1 for g in rows if g["challenger"]["brier"] < g["b0"]["brier"])

    return {
        "n": n,
        "b0": {
            "mean_brier": mean_of("b0", "brier"),
            "mean_log_loss": mean_of("b0", "log_loss"),
        },
        "challenger": {
            "mean_brier": mean_of("challenger", "brier"),
            "mean_log_loss": mean_of("challenger", "log_loss"),
        },
        "b0_better_brier_count": b0_better,
        "challenger_better_brier_count": challenger_better,
        "tied_brier_count": n - b0_better - challenger_better,
    }


__all__ = [
    "PairedGradeError",
    "grade_paired_receptions_record",
    "summarize_paired_grades",
]
