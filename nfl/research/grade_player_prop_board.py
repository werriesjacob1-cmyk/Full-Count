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

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from nfl.prospective.player_prop_grader import (
    PRIMARY_MARKETS,
    PlayerPropGradeError,
    grade_player_prop_market,
)
from nfl.research.box_score_outcomes import BoxScoreOutcomeError, outcome_for_candidate

DEFAULT_PRIMARY_SIDE = "OVER"


class PlayerPropBoardIntegrityError(ValueError):
    """The captured board cannot be proven to be sealed pregame evidence."""


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _instant(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PlayerPropBoardIntegrityError(f"{field} must be an ISO-8601 instant") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise PlayerPropBoardIntegrityError(f"{field} must be timezone-aware")
    return result


def _validate_board_body(board: Mapping[str, Any]) -> None:
    if board.get("analysis") != "NFL_LIVE_PLAYER_PROP_BOARD_CAPTURE":
        raise PlayerPropBoardIntegrityError("unexpected board analysis")
    if board.get("status") != "RESEARCH_ONLY_NO_PUBLICATION":
        raise PlayerPropBoardIntegrityError("board must remain research-only")
    if board.get("grading_status") != "PREGAME_CAPTURE_NOT_GRADED":
        raise PlayerPropBoardIntegrityError("board must be an ungraded pregame capture")

    event = board.get("event")
    candidates = board.get("candidates")
    identity = board.get("identity")
    if not isinstance(event, Mapping) or not isinstance(candidates, Mapping):
        raise PlayerPropBoardIntegrityError("board event and candidates must be mappings")
    if not isinstance(identity, Mapping):
        raise PlayerPropBoardIntegrityError("board identity must be a mapping")

    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        raise PlayerPropBoardIntegrityError("board event_id is required")
    sealed_at = _instant(board.get("created_at"), "created_at")
    kickoff = _instant(event.get("open_date"), "event.open_date")
    if sealed_at >= kickoff:
        raise PlayerPropBoardIntegrityError("board seal must strictly precede kickoff")

    roster_sha = str(identity.get("roster_sha256") or "").strip().lower()
    if len(roster_sha) != 64 or any(ch not in "0123456789abcdef" for ch in roster_sha):
        raise PlayerPropBoardIntegrityError("identity.roster_sha256 must be 64 lowercase hex characters")

    bound = candidates.get("bound")
    unmapped = candidates.get("unmapped")
    if not isinstance(bound, list) or not isinstance(unmapped, list):
        raise PlayerPropBoardIntegrityError("bound and unmapped candidates must be lists")
    if candidates.get("bound_count") != len(bound):
        raise PlayerPropBoardIntegrityError("bound_count does not match sealed population")
    if candidates.get("unmapped_count") != len(unmapped):
        raise PlayerPropBoardIntegrityError("unmapped_count does not match sealed population")
    if candidates.get("raw_normalized_count") != len(bound) + len(unmapped):
        raise PlayerPropBoardIntegrityError("raw_normalized_count does not match sealed population")

    for index, row in enumerate(bound):
        if not isinstance(row, Mapping):
            raise PlayerPropBoardIntegrityError(f"bound[{index}] must be a mapping")
        if str(row.get("event_id") or "").strip() != event_id:
            raise PlayerPropBoardIntegrityError(f"bound[{index}] event_id mismatch")
        captured_at = _instant(row.get("captured_at"), f"bound[{index}].captured_at")
        row_kickoff = _instant(
            row.get("event_open_date") or row.get("market_time"),
            f"bound[{index}].event_open_date",
        )
        if captured_at > sealed_at:
            raise PlayerPropBoardIntegrityError(f"bound[{index}] was captured after the seal")
        if sealed_at >= row_kickoff:
            raise PlayerPropBoardIntegrityError(f"bound[{index}] seal must strictly precede kickoff")


def seal_player_prop_board(board: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deterministically seal a newly captured research board."""
    if not isinstance(board, Mapping):
        raise PlayerPropBoardIntegrityError("board must be a mapping")
    body = dict(board)
    if body.pop("board_sha256", None) is not None:
        raise PlayerPropBoardIntegrityError("refusing to reseal a board with an existing digest")
    _validate_board_body(body)
    return {**body, "board_sha256": _canonical_sha256(body)}


def verify_player_prop_board(board: Mapping[str, Any]) -> str:
    """Verify canonical bytes, sealed population, identity, and pregame timing."""
    if not isinstance(board, Mapping):
        raise PlayerPropBoardIntegrityError("board must be a mapping")
    expected = str(board.get("board_sha256") or "").strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise PlayerPropBoardIntegrityError("board_sha256 must be 64 lowercase hex characters")
    body = dict(board)
    body.pop("board_sha256", None)
    actual = _canonical_sha256(body)
    if actual != expected:
        raise PlayerPropBoardIntegrityError(
            f"board hash mismatch: expected {expected}, recomputed {actual}"
        )
    _validate_board_body(body)
    return actual


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


def grade_player_prop_board(
    board: Mapping[str, Any],
    player_outcomes: Mapping[str, Mapping[str, float]],
    *,
    primary_side: str = DEFAULT_PRIMARY_SIDE,
) -> dict[str, Any]:
    """Verify one complete sealed board before grading its fixed population."""
    board_sha = verify_player_prop_board(board)
    result = grade_bound_candidates(
        board["candidates"]["bound"],
        player_outcomes,
        primary_side=primary_side,
    )
    return {
        "source_board_sha256": board_sha,
        "event_id": str(board["event"]["event_id"]),
        "captured_board_created_at": board["created_at"],
        **result,
    }
