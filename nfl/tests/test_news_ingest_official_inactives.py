#!/usr/bin/env python3
"""Tier-A News Brain ingestion tests: official inactive report -> claims.

Uses the same fixture-body style as `nfl/tests/test_official_inactives_source.py`
so this test never depends on live network access; the one real live-network
capture is a separate, manually-run evidence step (see
`nfl/intelligence/news_ingest_official_inactives.py`'s module docstring and
the engineering handoff entry for this workstream).
"""
from __future__ import annotations

import unittest

from nfl.archive.provenance import CHECKED_AND_FOUND, SOURCE_FAILED, Fetched
from nfl.intelligence.news_claim_ledger import validate_claims
from nfl.intelligence.news_ingest_official_inactives import (
    claims_from_parsed_report,
    ingest_capture,
)
from nfl.normalize import official_inactives

REPORT_BODY = b"""
<html><body>
<h1>Chiefs at Broncos Inactives</h1>
<h3>KANSAS CITY CHIEFS</h3>
<ul>
  <li>WR <a href="/players/example-receiver">Example Receiver</a> (ankle)</li>
  <li>OL <a href="/players/example-tackle">Example Tackle</a></li>
</ul>
<h3>DENVER BRONCOS</h3>
<ul>
  <li>QB <a href="/players/example-third-qb">Example Third QB</a> (Emergency third QB)</li>
</ul>
</body></html>
"""


class ClaimsFromParsedReportTests(unittest.TestCase):
    def setUp(self):
        self.parsed = official_inactives.parse_report(REPORT_BODY)

    def test_produces_one_valid_claim_per_player(self):
        claims = claims_from_parsed_report(
            self.parsed,
            source_id="official_nfl",
            source_url="https://www.nfl.com/news/week-3-inactives-kc-at-den",
            observed_at="2026-09-21T16:35:00Z",
        )
        self.assertEqual(len(claims), 3)
        summary = validate_claims(claims)
        self.assertEqual(summary["claim_count"], 3)
        self.assertEqual(summary["by_tier"], {"A": 3})
        self.assertEqual(summary["by_claim_type"], {"AVAILABILITY": 3})
        self.assertEqual(summary["by_team"], {"KC": 2, "DEN": 1})

    def test_team_and_concerns_teams_resolve(self):
        claims = claims_from_parsed_report(
            self.parsed,
            source_id="official_nfl",
            source_url="https://www.nfl.com/news/week-3-inactives-kc-at-den",
            observed_at="2026-09-21T16:35:00Z",
        )
        kc_claim = next(c for c in claims if c["player"]["player_name"] == "Example Receiver")
        self.assertEqual(kc_claim["team"], "KC")
        self.assertEqual(sorted(kc_claim["concerns_teams"]), ["DEN", "KC"])
        self.assertTrue(kc_claim["direct_observation"])
        self.assertEqual(kc_claim["source_tier"], "A")
        self.assertEqual(kc_claim["evidence_class"], "OFFICIAL_EVENT")
        self.assertIsNone(kc_claim["postgame_of_game_id"])

    def test_note_and_emergency_qb_flow_into_summary(self):
        claims = claims_from_parsed_report(
            self.parsed,
            source_id="official_nfl",
            source_url="https://www.nfl.com/news/week-3-inactives-kc-at-den",
            observed_at="2026-09-21T16:35:00Z",
        )
        qb_claim = next(c for c in claims if c["player"]["player_name"] == "Example Third QB")
        self.assertIn("Emergency third QB", qb_claim["content_summary"])

    def test_deterministic_claim_ids_across_repeated_ingestion(self):
        first = claims_from_parsed_report(
            self.parsed, source_id="official_nfl",
            source_url="https://www.nfl.com/news/week-3-inactives-kc-at-den",
            observed_at="2026-09-21T16:35:00Z",
        )
        second = claims_from_parsed_report(
            self.parsed, source_id="official_nfl",
            source_url="https://www.nfl.com/news/week-3-inactives-kc-at-den",
            observed_at="2026-09-21T18:00:00Z",  # a later re-observation
        )
        self.assertEqual(
            sorted(c["claim_id"] for c in first),
            sorted(c["claim_id"] for c in second),
        )


class IngestCaptureTests(unittest.TestCase):
    def _capture_records(self):
        index = Fetched(
            source_id="official_nfl", artifact="inactives",
            url="https://www.nfl.com/inactives/", outcome=CHECKED_AND_FOUND,
            body=b"<html><a href='/news/week-3-inactives-kc-at-den'>x</a></html>",
        )
        report = Fetched(
            source_id="official_nfl", artifact="inactive_report_abc123",
            url="https://www.nfl.com/news/week-3-inactives-kc-at-den",
            outcome=CHECKED_AND_FOUND, body=REPORT_BODY,
        )
        return [index, report]

    def test_ingest_capture_produces_validated_claims(self):
        result = ingest_capture(self._capture_records())
        self.assertEqual(result["reports_seen"], 1)
        self.assertEqual(result["reports_parsed"], 1)
        self.assertEqual(result["claim_count"], 3)
        self.assertEqual(result["parse_failures"], [])
        self.assertEqual(result["teams_with_at_least_one_claim"], ["DEN", "KC"])
        validate_claims(result["claims"])  # re-validate the whole batch

    def test_non_report_and_failed_records_are_ignored_not_fabricated(self):
        records = self._capture_records()
        records.append(Fetched(
            source_id="official_nfl", artifact="inactive_report_broken",
            url="https://www.nfl.com/news/broken", outcome=SOURCE_FAILED,
            failure_reason="HTTP 503",
        ))
        result = ingest_capture(records)
        self.assertEqual(result["reports_seen"], 2)
        self.assertEqual(result["reports_parsed"], 1)  # the failed one contributes nothing
        self.assertEqual(result["claim_count"], 3)

    def test_unparseable_report_is_a_disclosed_failure_not_a_silent_drop(self):
        records = self._capture_records()
        records.append(Fetched(
            source_id="official_nfl", artifact="inactive_report_notreally",
            url="https://www.nfl.com/news/not-an-inactive-report",
            outcome=CHECKED_AND_FOUND, body=b"<html><h1>Some other article</h1></html>",
        ))
        result = ingest_capture(records)
        self.assertEqual(result["reports_seen"], 2)
        self.assertEqual(result["reports_parsed"], 1)
        self.assertEqual(len(result["parse_failures"]), 1)
        self.assertEqual(
            result["parse_failures"][0]["url"],
            "https://www.nfl.com/news/not-an-inactive-report",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
