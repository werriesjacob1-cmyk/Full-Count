#!/usr/bin/env python3
"""`NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-20260919`: repair tests for the
real, confirmed gaps PR #151 (`NFL-NEWS-CLAIM-LEDGER-AUDIT-20260919`) found
in draft PR #146's News Brain claim ledger.

Covers, each with an explicit before/after or a real end-to-end path:

1. `claim_id` collision fix -- both directions (unchanged re-fetch still
   collides; a genuine position revision no longer does).
2. `merge_claims_by_id`/`current_claims` promoted into `news_claim_ledger.py`
   as canonical API, and a real, wired ledger-persistence entry point
   (`news_ingest_official_inactives.ingest_and_merge`) exercised at the
   `Fetched`-record level (not just on hand-built claim dicts), including a
   correction claim causing the original to disappear from `current_claims`.
3. Narrow contradiction detection for a silently dropped player between two
   report revisions, exercised through the new
   `news_ingest_official_inactives.detect_revision_contradictions` wrapper.
4. `ingest_capture`'s `fetch_failures` field, combined with `parse_failures`
   and real successful claims in one mixed batch.

This file does not re-derive PR #151's own six audit items (game-id binding,
player-identity binding, temporal-safety fixtures) -- those are covered by
`test_news_claim_ledger_game_binding_audit.py`,
`test_news_claim_ledger_player_identity_audit.py`, and
`test_news_claim_ledger_temporal_adversarial.py`, unmodified and still
passing.
"""
from __future__ import annotations

import unittest

from nfl.archive.provenance import CHECKED_AND_FOUND, SOURCE_FAILED, Fetched
from nfl.intelligence.news_claim_ledger import (
    NewsClaimLedgerError,
    current_claims,
    detect_dropped_availability_contradictions,
    merge_claims_by_id,
    validate_claim,
)
from nfl.intelligence.news_claim_ledger_lifecycle_audit import (
    current_claims as current_claims_via_shim,
    merge_claims_by_id as merge_claims_by_id_via_shim,
)
from nfl.intelligence.news_ingest_official_inactives import (
    claims_from_parsed_report,
    detect_revision_contradictions,
    ingest_and_merge,
    ingest_capture,
)
from nfl.normalize import official_inactives


def _record(artifact: str, url: str, body: bytes, *, observed_at: str | None = None) -> Fetched:
    kwargs = {}
    if observed_at is not None:
        kwargs["observed_at"] = observed_at
    return Fetched(
        source_id="official_nfl",
        artifact=artifact,
        url=url,
        outcome=CHECKED_AND_FOUND,
        body=body,
        **kwargs,
    )


def _report_body(position: str, note: str | None = None) -> bytes:
    note_text = f" ({note})" if note else ""
    return (
        "<html><body><h1>Week 2 audit-fixture inactives: Team A at Team B</h1>"
        "<h3>TEAM A</h3><ul>"
        f'<li>{position} <a href="/players/repair-fixture-player">Repair Fixture Player</a>{note_text}</li>'
        "</ul></body></html>"
    ).encode("utf-8")


class ClaimIdCollisionFixBothDirectionsTests(unittest.TestCase):
    """Fix #1, proven at the real ingestion entry point
    (`claims_from_parsed_report`), not merely on `make_claim_id` in
    isolation."""

    def test_unchanged_report_refetched_twice_still_collides_to_the_same_id(self):
        parsed = official_inactives.parse_report(_report_body("OT"))
        first = claims_from_parsed_report(
            parsed, source_id="official_nfl",
            source_url="https://www.nfl.com/news/repair-fixture",
            observed_at="2026-09-19T15:00:00Z",
        )
        second = claims_from_parsed_report(
            parsed, source_id="official_nfl",
            source_url="https://www.nfl.com/news/repair-fixture",
            observed_at="2026-09-19T18:00:00Z",  # later re-observation
        )
        self.assertEqual(first[0]["claim_id"], second[0]["claim_id"])
        self.assertNotEqual(first[0]["observed_at"], second[0]["observed_at"])

    def test_a_genuine_position_revision_no_longer_collides(self):
        parsed_v1 = official_inactives.parse_report(_report_body("OT"))
        parsed_v2 = official_inactives.parse_report(_report_body("G"))
        c1 = claims_from_parsed_report(
            parsed_v1, source_id="official_nfl",
            source_url="https://www.nfl.com/news/repair-fixture-2",
            observed_at="2026-09-19T15:00:00Z",
        )[0]
        c2 = claims_from_parsed_report(
            parsed_v2, source_id="official_nfl",
            source_url="https://www.nfl.com/news/repair-fixture-2",
            observed_at="2026-09-19T18:00:00Z",
        )[0]
        # Real before/after evidence: different ids, different position,
        # different summary -- no silent collision.
        self.assertNotEqual(c1["claim_id"], c2["claim_id"])
        self.assertEqual(c1["player"]["listed_position"], "OT")
        self.assertEqual(c2["player"]["listed_position"], "G")
        self.assertNotEqual(c1["content_summary"], c2["content_summary"])


class LedgerPromotionAndBackwardCompatTests(unittest.TestCase):
    def test_canonical_and_shim_are_the_same_function_objects(self):
        self.assertIs(merge_claims_by_id, merge_claims_by_id_via_shim)
        self.assertIs(current_claims, current_claims_via_shim)


class IngestAndMergeRealEntryPointTests(unittest.TestCase):
    """Fix #2, exercised through the real `Fetched`-record wiring in
    `news_ingest_official_inactives.ingest_and_merge`, not only on
    hand-built claim dicts."""

    def test_two_independent_capture_runs_of_an_unchanged_report_merge_without_duplication(self):
        body = _report_body("OT")
        run1 = [_record("inactive_report_x", "https://www.nfl.com/news/repair-merge", body,
                         observed_at="2026-09-19T15:00:00Z")]
        run2 = [_record("inactive_report_x", "https://www.nfl.com/news/repair-merge", body,
                         observed_at="2026-09-19T18:00:00Z")]

        first = ingest_and_merge(run1)
        self.assertEqual(first["merge"]["new_claim_count"], 1)
        self.assertEqual(first["current"]["current_count"], 1)

        second = ingest_and_merge(run2, existing_claims=first["merge"]["claims"])
        self.assertEqual(second["merge"]["total_claim_count"], 1)  # not 2
        self.assertEqual(second["merge"]["new_claim_count"], 0)
        self.assertEqual(second["merge"]["duplicate_claim_count"], 1)
        self.assertEqual(second["current"]["current_count"], 1)

    def test_a_correction_claim_causes_the_original_to_disappear_from_current_claims(self):
        body = _report_body("OT")
        run1 = [_record("inactive_report_x", "https://www.nfl.com/news/repair-correction", body,
                         observed_at="2026-09-19T15:00:00Z")]
        first = ingest_and_merge(run1)
        original = first["merge"]["claims"][0]
        self.assertEqual(first["current"]["current_count"], 1)

        # A real correction claim: same shape as a genuine claim, but a NEW
        # claim_id (per fix #1's own boundary, a materially different claim
        # must never reuse the original's id) with correction_of set.
        correction = dict(original)
        correction["claim_id"] = original["claim_id"] + "_correction"
        correction["content_summary"] = "CORRECTED: Repair Fixture Player is QUESTIONABLE, not inactive"
        correction["correction_of"] = original["claim_id"]
        validate_claim(correction)  # the correction is itself a valid claim

        merged = merge_claims_by_id(first["merge"]["claims"], [correction])
        self.assertEqual(merged["total_claim_count"], 2)  # append-only: both kept
        result = current_claims(merged["claims"])
        self.assertEqual(result["current_count"], 1)
        self.assertEqual(result["current"][0]["claim_id"], correction["claim_id"])
        self.assertEqual(result["superseded_count"], 1)
        self.assertEqual(result["superseded"][0]["claim_id"], original["claim_id"])
        # Append-only: the original record itself still exists, untouched,
        # in the merged population -- it is filtered from the CURRENT view,
        # never deleted or rewritten.
        original_ids = [c["claim_id"] for c in merged["claims"]]
        self.assertIn(original["claim_id"], original_ids)

    def test_a_genuine_position_revision_is_new_not_a_rejected_duplicate(self):
        # Before fix #1, this would have raised NewsClaimLedgerError from
        # merge_claims_by_id (same id, different content). After the fix, a
        # position revision is correctly treated as a new, distinct claim.
        run1 = [_record("inactive_report_x", "https://www.nfl.com/news/repair-position", _report_body("OT"),
                         observed_at="2026-09-19T15:00:00Z")]
        run2 = [_record("inactive_report_x", "https://www.nfl.com/news/repair-position", _report_body("G"),
                         observed_at="2026-09-19T18:00:00Z")]
        first = ingest_and_merge(run1)
        second = ingest_and_merge(run2, existing_claims=first["merge"]["claims"])
        self.assertEqual(second["merge"]["new_claim_count"], 1)
        self.assertEqual(second["merge"]["total_claim_count"], 2)
        self.assertEqual(second["current"]["current_count"], 2)  # both stand until a real correction_of links them


class ContradictionDetectionRealWiringTests(unittest.TestCase):
    """Fix #4, exercised through the new
    `news_ingest_official_inactives.detect_revision_contradictions`
    Fetched-record wrapper."""

    def test_a_player_dropped_between_two_real_ingest_capture_runs_is_detected(self):
        report_v1_body = (
            "<html><body><h1>Week 2 audit-fixture inactives: Team A at Team B</h1>"
            '<h3>TEAM A</h3><ul><li>WR <a href="/players/dropped-fixture-player">'
            "Dropped Fixture Player</a></li></ul></body></html>"
        ).encode("utf-8")
        report_v2_body = (
            "<html><body><h1>Week 2 audit-fixture inactives: Team A at Team B</h1>"
            "<h3>TEAM A</h3><ul></ul></body></html>"
        ).encode("utf-8")
        previous_records = [_record(
            "inactive_report_dropped", "https://www.nfl.com/news/repair-dropped",
            report_v1_body, observed_at="2026-09-19T15:00:00Z",
        )]
        current_records = [_record(
            "inactive_report_dropped", "https://www.nfl.com/news/repair-dropped",
            report_v2_body, observed_at="2026-09-19T18:00:00Z",
        )]

        result = detect_revision_contradictions(
            previous_records, current_records, noted_at="2026-09-19T18:00:00Z",
        )
        self.assertEqual(result["previous"]["claim_count"], 1)
        self.assertEqual(result["current"]["claim_count"], 0)
        self.assertEqual(result["contradictions"]["contradiction_count"], 1)

        amended = result["contradictions"]["results"][0]["amended_claim"]
        original_claim_id = result["previous"]["claims"][0]["claim_id"]
        self.assertEqual(amended["claim_id"], original_claim_id)
        self.assertEqual(amended["contradictions"][0]["relation"], "CONTRADICT")
        self.assertEqual(amended["resolution"]["outcome"], "REFUTED")
        self.assertEqual(amended["corrected_at"], "2026-09-19T18:00:00Z")

    def test_an_unrelated_player_appearing_elsewhere_does_not_suppress_the_contradiction(self):
        # A subtler correctness check: the presence of a DIFFERENT player in
        # the later revision must not be mistaken for the original player
        # still being listed.
        previous = claims_from_parsed_report(
            official_inactives.parse_report(_report_body("OT")),
            source_id="official_nfl", source_url="https://www.nfl.com/news/repair-unrelated",
            observed_at="2026-09-19T15:00:00Z",
        )
        other_player_body = (
            "<html><body><h1>Week 2 audit-fixture inactives: Team A at Team B</h1>"
            '<h3>TEAM A</h3><ul><li>WR <a href="/players/someone-else">Someone Else</a></li>'
            "</ul></body></html>"
        ).encode("utf-8")
        current = claims_from_parsed_report(
            official_inactives.parse_report(other_player_body),
            source_id="official_nfl", source_url="https://www.nfl.com/news/repair-unrelated",
            observed_at="2026-09-19T18:00:00Z",
        )
        result = detect_dropped_availability_contradictions(
            previous, current, noted_at="2026-09-19T18:00:00Z",
        )
        self.assertEqual(result["contradiction_count"], 1)
        self.assertEqual(
            result["results"][0]["original_claim_id"], previous[0]["claim_id"]
        )

    def test_a_player_still_present_unchanged_produces_no_contradiction(self):
        previous = claims_from_parsed_report(
            official_inactives.parse_report(_report_body("OT")),
            source_id="official_nfl", source_url="https://www.nfl.com/news/repair-unchanged",
            observed_at="2026-09-19T15:00:00Z",
        )
        current = claims_from_parsed_report(
            official_inactives.parse_report(_report_body("OT")),
            source_id="official_nfl", source_url="https://www.nfl.com/news/repair-unchanged",
            observed_at="2026-09-19T18:00:00Z",
        )
        result = detect_dropped_availability_contradictions(
            previous, current, noted_at="2026-09-19T18:00:00Z",
        )
        self.assertEqual(result["contradiction_count"], 0)

    def test_malformed_noted_at_fails_closed(self):
        with self.assertRaises(NewsClaimLedgerError):
            detect_dropped_availability_contradictions([], [], noted_at="not-a-timestamp")


class IngestCaptureFetchFailureMixedBatchTests(unittest.TestCase):
    """Fix #5, in a single batch alongside a real parse failure and a real
    successful claim, proving the three outcomes are each distinguishable
    from one another."""

    def test_fetch_failure_parse_failure_and_success_are_all_distinguishable_in_one_batch(self):
        good = _record(
            "inactive_report_good", "https://www.nfl.com/news/repair-good",
            _report_body("OT"),
        )
        unparseable = _record(
            "inactive_report_bad_bytes", "https://www.nfl.com/news/repair-unparseable",
            b"<html><body>not a real inactive report shape</body></html>",
        )
        fetch_failed = Fetched(
            source_id="official_nfl",
            artifact="inactive_report_failed",
            url="https://www.nfl.com/news/repair-fetch-failed",
            outcome=SOURCE_FAILED,
            failure_reason="HTTP 500",
            body=None,
        )
        result = ingest_capture([good, unparseable, fetch_failed])
        self.assertEqual(result["reports_seen"], 3)
        self.assertEqual(result["reports_parsed"], 1)
        self.assertEqual(result["claim_count"], 1)
        self.assertEqual(len(result["parse_failures"]), 1)
        self.assertEqual(result["parse_failures"][0]["url"], unparseable.url)
        self.assertEqual(len(result["fetch_failures"]), 1)
        self.assertEqual(result["fetch_failures"][0]["url"], fetch_failed.url)
        self.assertEqual(result["fetch_failures"][0]["outcome"], SOURCE_FAILED)
        self.assertEqual(result["fetch_failures"][0]["reason"], "HTTP 500")


if __name__ == "__main__":
    unittest.main()
