#!/usr/bin/env python3
"""Read-only audit of PR #146's disclosed `player.gsis_id: None` gap.

PR #146 correctly never infers a GSIS id from name alone: every real
captured claim carries `player.gsis_id: None`, and nothing in
`news_ingest_official_inactives.claims_from_parsed_report` looks at a
roster at all. This module tests, WITHOUT editing that file, whether the
already-existing `nfl.normalize.inactive_roster_binding.bind_player` --
already used by the receptions/passing-yards live-shadow workflows against
a real pinned nflverse `roster_2026.csv` release asset -- can safely
resolve those claims' players to durable GSIS ids as a downstream,
optional enrichment step.

`bind_player` itself is untouched; this module only adapts a
`news_claim_ledger` claim's `player` sub-mapping into the shape
`bind_player` expects, and summarizes the real match/ambiguous/unmatched
counts a caller would see.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nfl.normalize.inactive_roster_binding import bind_player


def bind_claims_to_roster(
    claims: Sequence[Mapping[str, Any]],
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
) -> dict[str, Any]:
    """Run each claim's `player` through the real roster binder.

    A claim's own `team` field (already an nflverse abbreviation, e.g.
    "BUF") is passed as the source team label -- `team_abbr` maps an
    abbreviation to itself, so this is not a new alias path, just reuse of
    the existing identity function with an already-resolved team.

    Returns one row per claim (never drops a claim silently) plus the real
    aggregate counts: BOUND (unambiguous real GSIS id), AMBIGUOUS_PLAYER
    (more than one same-name/team/position candidate), and every other
    `bind_player` status folded into UNMATCHED.
    """
    rows: list[dict[str, Any]] = []
    counts = {"BOUND": 0, "AMBIGUOUS_PLAYER": 0, "UNMATCHED": 0}

    for claim in claims:
        team = claim.get("team")
        player = claim.get("player") or {}
        if team is None:
            result = {
                "binding_status": "UNMATCHED",
                "reason": "CLAIM_HAS_NO_RESOLVED_TEAM",
                "gsis_id": None,
            }
        else:
            result = bind_player(team, player, roster_rows, season=season)
        status = result.get("binding_status")
        bucket = status if status in ("BOUND", "AMBIGUOUS_PLAYER") else "UNMATCHED"
        counts[bucket] += 1
        rows.append({
            "claim_id": claim.get("claim_id"),
            "team": team,
            "player_name": player.get("player_name"),
            "binding_status": status,
            "gsis_id": result.get("gsis_id"),
            "candidate_count": result.get("candidate_count"),
        })

    return {
        "claim_count": len(claims),
        "rows": rows,
        "bound_count": counts["BOUND"],
        "ambiguous_count": counts["AMBIGUOUS_PLAYER"],
        "unmatched_count": counts["UNMATCHED"],
    }
