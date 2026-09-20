#!/usr/bin/env python3
"""Research-only parallel News Brain vs. existing-pipeline eligibility check.

Per Jacob's "SUPERCLAUDE — NEXT EXECUTION PRIORITIES" (Issue #91, Priority 2):
Sunday's live receptions/passing-yards boards must keep using their EXISTING
official-inactive-evidence path (`nfl.normalize.official_inactives.parse_report`
-> `nfl.normalize.inactive_roster_binding.bind_report` ->
`nfl.normalize.pregame_availability.evaluate_candidate`) as the sole
authoritative gate. This module does not call, import from, or get imported
by any live workflow -- it is a read-only, offline comparison tool that runs
the SAME real official-inactive-report evidence through both:

  (a) the existing pipeline's identity/binding step (`bind_report`), and
  (b) the merged News Brain pipeline's identity/binding step
      (`claims_from_parsed_report` + `bind_claims_to_roster`),

and separately compares the two pipelines' independently-written pregame
temporal-safety checks (`pregame_availability._current_report` vs.
`news_claim_ledger.claim_eligible_for_game`) on shared timestamp scenarios.

Both identity paths ultimately call the SAME underlying
`inactive_roster_binding.bind_player` function, so by construction they must
agree on any single player's binding outcome given identical
(team, player_name, listed_position) input -- the real question this module
answers is whether the two pipelines actually FEED that shared function
identical facts end to end, not whether the function itself could disagree
with itself.

Explicit limitation, stated here rather than glossed over: this module does
NOT compare full game-COVERAGE completeness (whether both teams of a game
have a report) or canonical game-id binding. News Brain's own game-id
binding (`news_claim_ledger_game_binding_audit.resolve_game_id_by_team_pair_and_date`)
remains a separate, unwired, read-only enrichment step (a disclosed design
decision carried over from PR #151/#155/#160) -- `claim_eligible_for_game`
itself only enforces TIMING and the postgame-same-game guard, never team or
game coverage. That responsibility lives entirely in the existing pipeline
today. Do not read agreement on the checks below as proof News Brain could
already replace the existing gate.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nfl.intelligence.news_claim_ledger import claim_eligible_for_game, validate_claims
from nfl.intelligence.news_claim_ledger_player_identity_audit import bind_claims_to_roster
from nfl.intelligence.news_ingest_official_inactives import claims_from_parsed_report
from nfl.normalize.inactive_roster_binding import bind_report
from nfl.normalize.official_inactives import parse_report
from nfl.normalize.pregame_availability import _current_report


def compare_identity_and_binding(
    parsed_report: Mapping[str, Any],
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    source_url: str,
    observed_at: str,
    season: int,
) -> dict[str, Any]:
    """Run one already-parsed inactive report through both pipelines' identity
    path and report where the resulting (team, player_name, binding_status,
    gsis_id) facts agree or disagree.

    `parsed_report` must be the real output shape of
    `nfl.normalize.official_inactives.parse_report` (or, for live use, the
    actual return value of calling it on real archived bytes -- see
    `run_live_parallel_check` below). This function does not re-parse HTML;
    `parse_report` is a separately owned, separately tested module.
    """
    existing_bound = bind_report(parsed_report, roster_rows, season=season)

    claims = claims_from_parsed_report(
        parsed_report, source_id=source_id, source_url=source_url, observed_at=observed_at
    )
    validate_claims(claims)
    nb_bound = bind_claims_to_roster(claims, roster_rows, season=season)

    existing_identities = set()
    for team_block in existing_bound["teams"]:
        for p in team_block["players"]:
            existing_identities.add(
                (team_block.get("team"), p.get("player_name"), p.get("binding_status"), p.get("gsis_id"))
            )

    nb_identities = {
        (row.get("team"), row.get("player_name"), row.get("binding_status"), row.get("gsis_id"))
        for row in nb_bound["rows"]
    }

    return {
        "report_title": parsed_report.get("report_title"),
        "existing_pipeline": {
            "player_count": existing_bound["player_count"],
            "bound_count": existing_bound["bound_player_count"],
            "unresolved_count": existing_bound["unresolved_player_count"],
        },
        "news_brain_pipeline": {
            "claim_count": nb_bound["claim_count"],
            "bound_count": nb_bound["bound_count"],
            "ambiguous_count": nb_bound["ambiguous_count"],
            "unmatched_count": nb_bound["unmatched_count"],
        },
        "identity_tuples_match": existing_identities == nb_identities,
        "identity_tuples_only_in_existing": sorted(t for t in (existing_identities - nb_identities)),
        "identity_tuples_only_in_news_brain": sorted(t for t in (nb_identities - existing_identities)),
        "claims": claims,
        "existing_bound": existing_bound,
        "news_brain_bound": nb_bound,
    }


def compare_temporal_safety(
    *,
    report_published_at: str,
    report_observed_at: str,
    event_open_date: str,
    claim: Mapping[str, Any],
    game_id: str,
) -> dict[str, Any]:
    """Compare the existing pipeline's `_current_report` pregame-safety verdict
    against News Brain's `claim_eligible_for_game` verdict on the SAME
    published/observed/kickoff timestamps.

    Both are real, unedited functions from their owning modules -- this does
    not reimplement either check. `_current_report` is a module-private
    helper (leading underscore); it is imported directly here for read-only
    verification, the same way this session's prior audits have read real
    "private" behavior rather than re-deriving it from the public docstring.

    Note the real asymmetry disclosed in this module's own docstring:
    `claim_eligible_for_game` never checks team/game coverage, only timing
    and the postgame-same-game guard, so `agree` here means "the two
    pipelines' TIMING verdicts match" -- not "News Brain's overall
    eligibility conclusion matches the existing pipeline's overall
    availability_status".
    """
    existing_report = {
        "report_published_at": report_published_at,
        "report_observed_at": report_observed_at,
    }
    existing_candidate = {"event_open_date": event_open_date}
    existing_pass = bool(_current_report(existing_report, existing_candidate))

    nb_claim = dict(claim)
    nb_claim["published_at"] = report_published_at
    nb_claim["observed_at"] = report_observed_at
    nb_verdict = claim_eligible_for_game(nb_claim, game_id=game_id, kickoff_at=event_open_date)

    return {
        "existing_pipeline_timing_pass": existing_pass,
        "news_brain_eligible": bool(nb_verdict["eligible"]),
        "news_brain_reason": nb_verdict["reason"],
        "timing_verdicts_agree": existing_pass == bool(nb_verdict["eligible"]),
    }


def run_live_parallel_check(
    body: bytes,
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    source_url: str,
    observed_at: str,
    season: int,
) -> dict[str, Any]:
    """Live-evidence entry point: parses real archived inactive-report bytes
    once (via the real, unedited `parse_report`) and runs the identity
    comparison above. Intended to be invoked manually (or by a future,
    separately-authorized workflow step) against a real `official_nfl.capture()`
    result once Sunday's per-game reports are published -- not exercised by
    the network-free test suite, which passes an already-parsed structure to
    `compare_identity_and_binding` directly.
    """
    parsed_report = parse_report(body)
    return compare_identity_and_binding(
        parsed_report,
        roster_rows,
        source_id=source_id,
        source_url=source_url,
        observed_at=observed_at,
        season=season,
    )


__all__ = [
    "compare_identity_and_binding",
    "compare_temporal_safety",
    "run_live_parallel_check",
]
