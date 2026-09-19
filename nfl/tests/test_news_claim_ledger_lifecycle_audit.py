#!/usr/bin/env python3
"""Audit items 3 and 4: duplicate-claim detection across independent
ingestion runs, and correction/retraction resolution into a "current
claims" view.

Uses PR #146's own real `claims_from_parsed_report` (cherry-picked
unmodified onto this audit branch) against a real-shaped parsed report
(same structure `official_inactives.parse_report` produces) to reproduce
two INDEPENDENT ingestion runs of the identical underlying fact, exactly
as PR #146's own `test_deterministic_claim_ids_across_repeated_ingestion`
does, then tests genuinely NEW behavior (merging, correction resolution)
this audit built on top.
"""
import unittest

from nfl.intelligence.news_ingest_official_inactives import claims_from_parsed_report
from nfl.intelligence.news_claim_ledger import NewsClaimLedgerError, validate_claim
from nfl.intelligence.news_claim_ledger_lifecycle_audit import (
    current_claims,
    merge_claims_by_id,
)

PARSED_REPORT = {
    "report_title": "Week 2 Thursday night inactives: Detroit Lions at Buffalo Bills",
    "report_published_at": "2026-09-17T22:51:40.379Z",
    "teams": [
        {
            "source_team_label": "LIONS",
            "players": [
                {
                    "player_name": "Blake Miller",
                    "listed_position": "OT",
                    "source_player_href": "/players/blake-miller-3",
                    "source_player_slug": "blake-miller-3",
                    "note": None,
                },
            ],
        },
        {
            "source_team_label": "BILLS",
            "players": [
                {
                    "player_name": "Ed Oliver",
                    "listed_position": "DT",
                    "source_player_href": "/players/ed-oliver",
                    "source_player_slug": "ed-oliver",
                    "note": None,
                },
            ],
        },
    ],
}


def _ingest(observed_at: str) -> list[dict]:
    return claims_from_parsed_report(
        PARSED_REPORT,
        source_id="official_nfl",
        source_url="https://www.nfl.com/news/week-2-thursday-night-inactives-detroit-lions-at-buffalo-bills",
        observed_at=observed_at,
    )


class DuplicateDetectionAcrossIndependentRunsTests(unittest.TestCase):
    def test_two_independent_runs_produce_the_same_claim_ids(self):
        run1 = _ingest("2026-09-19T15:30:00Z")
        run2 = _ingest("2026-09-19T18:45:00Z")
        self.assertEqual(
            sorted(c["claim_id"] for c in run1),
            sorted(c["claim_id"] for c in run2),
        )
        # observed_at legitimately differs -- these are two real, distinct
        # observations of the same fact, not byte-identical records.
        self.assertNotEqual(run1[0]["observed_at"], run2[0]["observed_at"])

    def test_merging_two_runs_yields_no_duplicate_rows_in_the_ledger(self):
        run1 = _ingest("2026-09-19T15:30:00Z")
        run2 = _ingest("2026-09-19T18:45:00Z")
        result = merge_claims_by_id(run1, run2)
        self.assertEqual(result["total_claim_count"], 2)  # not 4
        self.assertEqual(result["new_claim_count"], 0)
        self.assertEqual(result["duplicate_claim_count"], 2)
        # The kept record is the FIRST (existing) observation's observed_at.
        for claim in result["claims"]:
            self.assertEqual(claim["observed_at"], "2026-09-19T15:30:00Z")

    def test_a_third_independent_run_against_an_empty_ledger_is_all_new(self):
        run1 = _ingest("2026-09-19T15:30:00Z")
        result = merge_claims_by_id([], run1)
        self.assertEqual(result["new_claim_count"], 2)
        self.assertEqual(result["duplicate_claim_count"], 0)

    def test_a_genuine_content_change_under_the_same_claim_id_fails_closed(self):
        # If the SAME deterministic claim_id ever carries different content
        # (a real integrity violation, e.g. a hash-input change), the merge
        # must refuse to silently pick one side.
        run1 = _ingest("2026-09-19T15:30:00Z")
        corrupted = [dict(run1[0])]
        corrupted[0]["content_summary"] = "SOMETHING ELSE ENTIRELY"
        with self.assertRaises(NewsClaimLedgerError):
            merge_claims_by_id(run1, corrupted)


class CorrectionResolutionTests(unittest.TestCase):
    def _claim(self, **overrides) -> dict:
        base = dict(_ingest("2026-09-19T15:30:00Z")[0])
        base.update(overrides)
        return base

    def test_no_corrections_means_every_claim_is_current(self):
        run1 = _ingest("2026-09-19T15:30:00Z")
        result = current_claims(run1)
        self.assertEqual(result["current_count"], 2)
        self.assertEqual(result["superseded_count"], 0)

    def test_a_correction_marks_the_original_superseded_not_current(self):
        original = self._claim(claim_id="nc_original_claim")
        correction = self._claim(
            claim_id="nc_correction_claim",
            correction_of="nc_original_claim",
            content_summary="CORRECTED: Blake Miller is actually QUESTIONABLE, not inactive",
        )
        result = current_claims([original, correction])
        self.assertEqual(result["current_count"], 1)
        self.assertEqual(result["current"][0]["claim_id"], "nc_correction_claim")
        self.assertEqual(result["superseded_count"], 1)
        self.assertEqual(result["superseded"][0]["claim_id"], "nc_original_claim")

    def test_an_orphan_correction_of_reference_fails_closed(self):
        # correction_of points at a claim_id that does not exist in this
        # population -- PR #146's schema validates correction_of is a
        # string or None, but does NOT itself validate the reference
        # resolves to a real claim. This audit's current_claims does.
        dangling = self._claim(
            claim_id="nc_dangling_correction",
            correction_of="nc_this_claim_id_does_not_exist_anywhere",
        )
        with self.assertRaises(NewsClaimLedgerError):
            current_claims([dangling])

    def test_pr146_schema_itself_does_not_reject_an_orphan_correction_of(self):
        # Disclosed gap: validate_claim (PR #146's own function, unedited)
        # happily accepts a correction_of referencing a nonexistent claim,
        # because it only checks the field's TYPE, not its referential
        # integrity. This is exactly the gap current_claims() above closes
        # as a separate, composed function -- PR #146 itself has no
        # "current claims" query and no referential check.
        dangling = self._claim(
            claim_id="nc_dangling_correction_2",
            correction_of="nc_also_does_not_exist",
        )
        validate_claim(dangling)  # does not raise -- the real, disclosed gap


if __name__ == "__main__":
    unittest.main()
