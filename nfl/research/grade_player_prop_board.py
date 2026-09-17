#!/usr/bin/env python3
"""Grade a sealed pregame player-prop board against a final box score.

Pure orchestration: takes the already-loaded ``candidates.bound`` list from
one of PR #123's captured board JSON files (`nfl-live-player-prop-board.json`,
produced by `.github/workflows/nfl-live-player-prop-board-manual.yml`) and
the `player_outcomes` mapping from `box_score_outcomes.build_player_outcomes`,
and grades every BOUND candidate through `player_prop_grader`.

No I/O here deliberately: downloading the board artifact and fetching
nflverse's weekly stats file are both a few lines of glue code done by the
caller (or a thin workflow step), kept separate so this module -- the part
with actual grading logic worth getting right -- can be tested without any
network access.

PRIMARY markets have no posted "pick" to grade a specific side against: this
repo does not select bets for any player-prop family (see
nfl/docs/PLAYER_PROP_SETTLEMENT_SPEC.md, "What 'graded' means"). Grading the
OVER side by convention still fully describes the market's outcome -- a
reader who cares about UNDER reads a MISS as "went under" -- so this reports
what happened, not a bet result.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from nfl.prospective.player_prop_grader import (
    PRIMARY_MARKETS,
    PlayerPropGradeError,
    grade_player_prop_market,
)
from nfl.research.box_score_outcomes import BoxScoreOutcomeError, outcome_for_candidate

DEFAULT_PRIMARY_SIDE = "OVER"


def grade_bound_candidates(
    bound_candidates: Sequence[Mapping[str, Any]],
    player_outcomes: Mapping[str, Mapping[str, float]],
    *,
    primary_side: str = DEFAULT_PRIMARY_SIDE,
) -> dict[str, Any]:
    """Grade every BOUND candidate from a captured board against final outcomes.

    Candidates whose binding_status is not BOUND are skipped (nothing to
    grade -- they were never a real market observation to begin with).
    A candidate that fails to grade (unsupported market, malformed market
    record, gsis_id/event_id mismatch) is recorded in `errors`, never
    silently dropped and never allowed to crash the whole run.
    """
    if primary_side not in {"OVER", "UNDER"}:
        raise ValueError(f"primary_side must be OVER or UNDER, got {primary_side!r}")

    graded: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for candidate in bound_candidates:
        if not isinstance(candidate, Mapping):
            errors.append({"error": "candidate is not a mapping"})
            continue
        if candidate.get("binding_status") != "BOUND":
            continue

        try:
            outcome = outcome_for_candidate(candidate, player_outcomes)
            kwargs = {}
            if candidate.get("market") in PRIMARY_MARKETS:
                kwargs["side"] = primary_side
            result = grade_player_prop_market(candidate, outcome, **kwargs)
        except (BoxScoreOutcomeError, PlayerPropGradeError) as exc:
            errors.append({
                "market_id": candidate.get("market_id"),
                "market": candidate.get("market"),
                "player_name": candidate.get("player_name"),
                "gsis_id": candidate.get("gsis_id"),
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        graded.append({
            **result,
            "player_name": candidate.get("player_name"),
            "team": candidate.get("team"),
            "market_name": candidate.get("market_name"),
        })

    settlement_counts = Counter(row["settlement"] for row in graded)
    market_counts = Counter(row["canonical_market"] for row in graded)

    return {
        "primary_side_convention": primary_side,
        "graded_count": len(graded),
        "error_count": len(errors),
        "settlement_counts": dict(settlement_counts),
        "market_counts": dict(market_counts),
        "graded": graded,
        "errors": errors,
    }
