#!/usr/bin/env python3
"""Tier-A News Brain ingestion: official NFL.com inactive reports -> claims.

Reuses the existing, tested capture and parse infrastructure rather than
building a new fetcher:

    nfl.archive.sources.official_nfl.capture()   -- real live HTTP fetch
    nfl.normalize.official_inactives.parse_report() -- HTML -> structured rows
    nfl.normalize.inactive_roster_binding.team_abbr() -- team label normalizer

This module adds exactly one new step: turning each parsed, source-local
inactive-report player row into one real, validated
`nfl.intelligence.news_claim_ledger` AVAILABILITY claim. It does not edit or
re-implement any of the modules it imports.

Per the design doc, an official inactive report is Tier A ("official
inactive list") and its assertion is a direct official published fact, so
each resulting claim is `evidence_class=OFFICIAL_EVENT`,
`source_tier="A"`, `direct_observation=True`. These are per-game reports
published roughly 90 minutes before kickoff (the same convention already
documented in `nfl/normalize/pregame_availability.py`), so they are
legitimately pregame evidence for the game they concern -- never
`postgame_of_game_id`.

Canonical game-id binding (season/week/home-vs-away, not just "two teams
listed") is NOT attempted here -- `official_inactives.parse_report` itself
already documents `canonical_game_id: None` as an intentional, downstream
concern, and this pass does not add a schedule join. `concerns_teams`
records the two team abbreviations found in the report when both resolve;
`concerns_game_id` stays `None`. This is a disclosed gap, not a fabricated
identity.

`claim_id` collision fix (`NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-20260919`,
repairing a real gap PR #151 proved): the id hash below now incorporates the
player's `listed_position` in addition to `source_player_href`. Before this
fix, two differently-parsed revisions of the identical report/player (e.g. a
position correction from "OT" to "G") collided to the SAME `claim_id` with
silently different `content_summary`/`listed_position` -- proven directly by
`test_two_reports_disagreeing_on_position_create_two_unlinked_claims` in
`nfl/tests/test_news_ingest_source_state_audit.py`, now updated to prove the
fix instead. Re-ingesting the SAME unchanged report (identical position)
still produces the SAME `claim_id` -- PR #146's own idempotency test
(`test_deterministic_claim_ids_across_repeated_ingestion`) is unmodified and
still passes.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nfl.archive.provenance import CHECKED_AND_FOUND, Fetched
from nfl.normalize import official_inactives
from nfl.normalize.inactive_roster_binding import team_abbr
from nfl.intelligence.news_claim_ledger import (
    LEDGER_SCHEMA_VERSION,
    current_claims,
    detect_dropped_availability_contradictions,
    make_claim_id,
    merge_claims_by_id,
    validate_claim,
)

REPORTER = {
    "name": "NFL.com",
    "outlet": "NFL.com",
    "role": "official_league_publication",
}


def _team_pair(parsed_report: Mapping[str, Any]) -> tuple[str, str] | None:
    teams = []
    for block in parsed_report.get("teams") or []:
        abbr = team_abbr(block.get("source_team_label"))
        if abbr:
            teams.append(abbr)
    if len(teams) == 2 and teams[0] != teams[1]:
        return tuple(sorted(teams))  # type: ignore[return-value]
    return None


def claims_from_parsed_report(
    parsed_report: Mapping[str, Any],
    *,
    source_id: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    """Turn one already-parsed inactive report into validated claim records.

    Every returned claim has already passed `validate_claim`; a malformed
    result here is a bug in this function, not something callers must guard
    against separately.
    """
    concerns_teams = _team_pair(parsed_report)
    claims: list[dict[str, Any]] = []

    for block in parsed_report.get("teams") or []:
        source_team_label = block.get("source_team_label")
        team = team_abbr(source_team_label)
        for player in block.get("players") or []:
            href = str(player.get("source_player_href") or "")
            claim_id = make_claim_id(
                source_id,
                source_url,
                "AVAILABILITY",
                href or player.get("player_name"),
                player.get("listed_position"),
            )
            note = player.get("note")
            summary = (
                f"{player.get('player_name')} ({player.get('listed_position')}) "
                f"listed inactive by {source_team_label} per official NFL.com "
                "inactive report"
            )
            if note:
                summary += f" -- note: {note}"

            claim = {
                "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                "claim_id": claim_id,
                "source_id": source_id,
                "source_tier": "A",
                "evidence_class": "OFFICIAL_EVENT",
                "claim_type": "AVAILABILITY",
                "direct_observation": True,
                "reporter": dict(REPORTER),
                "team": team,
                "player": {
                    "player_name": str(player.get("player_name") or "").strip() or None,
                    "gsis_id": None,
                    "source_player_href": player.get("source_player_href"),
                    "source_player_slug": player.get("source_player_slug"),
                    "listed_position": player.get("listed_position"),
                },
                "concerns_game_id": None,
                "concerns_teams": list(concerns_teams) if concerns_teams else None,
                "postgame_of_game_id": None,
                "published_at": parsed_report.get("report_published_at"),
                "observed_at": observed_at,
                "effective_from": None,
                "effective_until": None,
                "corrected_at": None,
                "content_summary": summary,
                "corroborations": [],
                "contradictions": [],
                "correction_of": None,
                "resolution": None,
            }
            validate_claim(claim)
            claims.append(claim)

    return claims


def ingest_capture(records: Sequence[Fetched]) -> dict[str, Any]:
    """Ingest one `official_nfl.capture()` result into claim records.

    Only records for discovered per-game inactive-report artifacts
    (`inactive_report_*`) that were actually fetched (`CHECKED_AND_FOUND`)
    are eligible. A report whose bytes could not be parsed as an inactive
    article is recorded as a disclosed parse failure, never silently
    dropped and never treated as "nobody inactive."

    Fetch-failure signaling fix (`NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-
    20260919`, repairing a real gap PR #151 proved): a record whose outcome
    is anything other than `CHECKED_AND_FOUND` (e.g. `SOURCE_FAILED`,
    `NOT_CHECKED`, `UNAVAILABLE_BY_POLICY`, ...) is now recorded in
    `fetch_failures`, distinct from `parse_failures` (which is reserved for
    bytes that WERE fetched but could not be parsed as an inactive report).
    Before this fix, a real fetch failure was silently invisible: it only
    showed up as a gap between `reports_seen` and `reports_parsed`, with no
    field anywhere recording why -- indistinguishable from "the source had
    nothing to report."
    """
    claims: list[dict[str, Any]] = []
    reports_parsed = 0
    parse_failures: list[dict[str, str]] = []
    fetch_failures: list[dict[str, str]] = []
    teams_seen: set[str] = set()
    unresolved_team_players = 0

    for record in records:
        if not record.artifact.startswith("inactive_report_"):
            continue
        if record.outcome != CHECKED_AND_FOUND or not record.body:
            fetch_failures.append({
                "url": record.url,
                "artifact": record.artifact,
                "outcome": record.outcome,
                "reason": record.failure_reason
                or f"non-CHECKED_AND_FOUND outcome with no usable body: {record.outcome}",
            })
            continue
        try:
            parsed = official_inactives.parse_report(record.body)
        except ValueError as exc:
            parse_failures.append({"url": record.url, "reason": str(exc)})
            continue

        reports_parsed += 1
        report_claims = claims_from_parsed_report(
            parsed,
            source_id=record.source_id,
            source_url=record.url,
            observed_at=record.observed_at,
        )
        for claim in report_claims:
            if claim["team"]:
                teams_seen.add(claim["team"])
            else:
                unresolved_team_players += 1
        claims.extend(report_claims)

    return {
        "claims": claims,
        "reports_seen": sum(
            1 for r in records if r.artifact.startswith("inactive_report_")
        ),
        "reports_parsed": reports_parsed,
        "parse_failures": parse_failures,
        "fetch_failures": fetch_failures,
        "claim_count": len(claims),
        "teams_with_at_least_one_claim": sorted(teams_seen),
        "unresolved_team_player_count": unresolved_team_players,
    }


def ingest_and_merge(
    records: Sequence[Fetched],
    existing_claims: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Real, tested wiring for fix #2 (ledger merge/correction wiring): run
    Tier-A ingestion, keyed-merge the freshly ingested claims into an
    existing claim population (a second capture run's claims against the
    first's persisted set, or an empty ledger on the very first run), and
    return the resulting current (non-superseded) view.

    This is the actual code path proving two capture runs' claims merge
    without duplication and that a correction removes its target from the
    current view -- not merely a claim that `merge_claims_by_id`/
    `current_claims` exist somewhere unwired. Callers own persisting
    `result["merge"]["claims"]` as the next run's `existing_claims`.
    """
    capture_result = ingest_capture(records)
    merge_result = merge_claims_by_id(existing_claims, capture_result["claims"])
    current_result = current_claims(merge_result["claims"])
    return {
        "capture": capture_result,
        "merge": merge_result,
        "current": current_result,
    }


def detect_revision_contradictions(
    previous_records: Sequence[Fetched],
    current_records: Sequence[Fetched],
    *,
    noted_at: str,
) -> dict[str, Any]:
    """Real wiring for fix #4 (contradiction detection): ingest two
    successive captures -- e.g. two observations of the same evolving
    official inactive report -- and detect any `AVAILABILITY` claim whose
    player silently disappears between them.

    Narrowly scoped to exactly the case PR #151 demonstrated unfixed; see
    `nfl.intelligence.news_claim_ledger.detect_dropped_availability_contradictions`
    for the detection logic and its scope limits.
    """
    previous_result = ingest_capture(previous_records)
    current_result = ingest_capture(current_records)
    contradiction_result = detect_dropped_availability_contradictions(
        previous_result["claims"], current_result["claims"], noted_at=noted_at,
    )
    return {
        "previous": previous_result,
        "current": current_result,
        "contradictions": contradiction_result,
    }
