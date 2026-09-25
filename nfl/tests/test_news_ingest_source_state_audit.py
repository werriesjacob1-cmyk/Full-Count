#!/usr/bin/env python3
"""Audit item 6: missing/contradictory source states in Tier-A ingestion.

Uses the REAL `nfl.archive.provenance.Fetched` envelope contract and REAL
outcome enum values (`SOURCE_FAILED`, `CHECKED_AND_FOUND`) -- not fabricated
page content -- to simulate what `ingest_capture` does with a genuine
non-`CHECKED_AND_FOUND` fetch outcome, and constructs two independently
parsed, differently-shaped (but structurally real) inactive-report
snapshots to test whether same-day contradictions between them are
represented anywhere in the schema.
"""
import unittest

from nfl.archive.provenance import CHECKED_AND_FOUND, SOURCE_FAILED, Fetched
from nfl.intelligence.news_ingest_official_inactives import (
    claims_from_parsed_report,
    ingest_capture,
)


class NonCheckedAndFoundOutcomeTests(unittest.TestCase):
    """Item 6a: a real fetch-failure outcome shape."""

    def test_source_failed_record_produces_no_claim(self):
        records = [
            Fetched(
                source_id="official_nfl",
                artifact="inactive_report_f325517d96ff6eec",
                url="https://www.nfl.com/news/week-2-thursday-night-inactives-detroit-lions-at-buffalo-bills",
                outcome=SOURCE_FAILED,
                failure_reason="HTTP 503 Service Unavailable",
                body=None,
            ),
        ]
        result = ingest_capture(records)
        self.assertEqual(result["claim_count"], 0)
        self.assertEqual(result["reports_parsed"], 0)

    def test_source_failed_is_now_distinguishable_from_a_genuinely_empty_index(self):
        # PR #151 originally proved this AS a gap: ingest_capture's return
        # shape counted `reports_seen` (every inactive_report_* artifact
        # regardless of outcome) and `reports_parsed` (only successfully
        # parsed ones), and separately listed `parse_failures` for
        # CHECKED_AND_FOUND records that failed to PARSE -- but a record
        # that never got bytes at all (SOURCE_FAILED, NOT_CHECKED,
        # UNAVAILABLE_BY_POLICY, ...) fell into neither list, showing up
        # only as a silent gap between reports_seen and reports_parsed with
        # no reason recorded anywhere.
        #
        # FIXED by `NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-20260919`:
        # ingest_capture now populates a separate `fetch_failures` field for
        # exactly this case, distinct from `parse_failures` (reserved for
        # bytes that WERE fetched but failed to parse).
        failed = Fetched(
            source_id="official_nfl",
            artifact="inactive_report_abc123",
            url="https://www.nfl.com/news/some-real-looking-report-url",
            outcome=SOURCE_FAILED,
            failure_reason="connection reset by peer",
            body=None,
        )
        result = ingest_capture([failed])
        self.assertEqual(result["reports_seen"], 1)
        self.assertEqual(result["reports_parsed"], 0)
        # parse_failures correctly stays empty -- this was never a PARSE
        # failure, it never had bytes to parse in the first place.
        self.assertEqual(result["parse_failures"], [])
        # The fix: a real fetch failure is now explicitly recorded, with its
        # real outcome and failure reason, not silently absent.
        self.assertEqual(len(result["fetch_failures"]), 1)
        self.assertEqual(result["fetch_failures"][0]["outcome"], SOURCE_FAILED)
        self.assertEqual(
            result["fetch_failures"][0]["reason"], "connection reset by peer"
        )
        self.assertEqual(
            result["fetch_failures"][0]["url"],
            "https://www.nfl.com/news/some-real-looking-report-url",
        )

    def test_mixed_outcomes_only_the_checked_and_found_record_yields_claims(self):
        good_body = _real_shaped_report_body(["Real Player One"])
        records = [
            Fetched(
                source_id="official_nfl",
                artifact="inactive_report_good",
                url="https://www.nfl.com/news/report-a",
                outcome=CHECKED_AND_FOUND,
                body=good_body,
            ),
            Fetched(
                source_id="official_nfl",
                artifact="inactive_report_bad",
                url="https://www.nfl.com/news/report-b",
                outcome=SOURCE_FAILED,
                failure_reason="timeout",
                body=None,
            ),
        ]
        result = ingest_capture(records)
        self.assertEqual(result["reports_seen"], 2)
        self.assertEqual(result["reports_parsed"], 1)
        self.assertEqual(result["claim_count"], 1)


def _real_shaped_report_body(team_a_players: list[str]) -> bytes:
    """Minimal but structurally real inactive-report HTML shape (same tags
    `official_inactives.parse_report` actually looks for), used only to
    exercise the ingestion pipeline end to end -- not a claim about any
    real game or player.
    """
    items = "".join(
        f'<li>OT <a href="/players/{name.lower().replace(" ", "-")}">{name}</a></li>'
        for name in team_a_players
    )
    return (
        "<html><body><h1>Week 99 audit-fixture inactives: Team A at Team B</h1>"
        f"<h3>TEAM A</h3><ul>{items}</ul>"
        "</body></html>"
    ).encode("utf-8")


class ContradictorySameDayReportsTests(unittest.TestCase):
    """Item 6b: one report lists a player inactive; a later same-day
    revision of the SAME article no longer mentions him. Does the schema
    represent that contradiction anywhere, automatically?
    """

    def test_a_player_dropped_from_a_later_revision_produces_no_linking_claim(self):
        report_v1 = {
            "report_title": "Audit-fixture inactives: Team A at Team B",
            "report_published_at": "2026-09-17T22:00:00Z",
            "teams": [
                {
                    "source_team_label": "TEAM A",
                    "players": [
                        {
                            "player_name": "Contested Player",
                            "listed_position": "WR",
                            "source_player_href": "/players/contested-player",
                            "source_player_slug": "contested-player",
                            "note": None,
                        },
                    ],
                },
            ],
        }
        report_v2 = {
            **report_v1,
            "report_published_at": "2026-09-17T22:40:00Z",  # same-day revision
            "teams": [{"source_team_label": "TEAM A", "players": []}],
        }

        claims_v1 = claims_from_parsed_report(
            report_v1,
            source_id="official_nfl",
            source_url="https://www.nfl.com/news/audit-fixture-report",
            observed_at="2026-09-17T22:05:00Z",
        )
        claims_v2 = claims_from_parsed_report(
            report_v2,
            source_id="official_nfl",
            source_url="https://www.nfl.com/news/audit-fixture-report",
            observed_at="2026-09-17T22:45:00Z",
        )

        self.assertEqual(len(claims_v1), 1)
        self.assertEqual(len(claims_v2), 0)  # player silently absent, not "cleared"

        # This is still the correct baseline: `claims_from_parsed_report`
        # alone is a pure per-call function and never reaches back into an
        # earlier revision's claims -- nothing links v2's silence back to
        # v1's claim automatically. v1's own contradictions/resolution
        # fields stay unchanged and empty here.
        original_claim = claims_v1[0]
        self.assertEqual(original_claim["contradictions"], [])
        self.assertIsNone(original_claim["resolution"])
        self.assertIsNone(original_claim["corrected_at"])

        # FIXED by `NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-20260919`: a
        # caller that explicitly compares the two revisions with the new
        # `detect_dropped_availability_contradictions` gets an explicit
        # signal instead of silence -- an amended copy of the original claim
        # with `contradictions`/`resolution`/`corrected_at` populated. This
        # narrow function requires the caller to opt in and compare two
        # scoped claim lists; it does not retroactively change the raw
        # `claims_v1` list computed above.
        from nfl.intelligence.news_claim_ledger import (
            detect_dropped_availability_contradictions,
        )
        contradiction_result = detect_dropped_availability_contradictions(
            claims_v1, claims_v2, noted_at="2026-09-17T22:45:00Z",
        )
        self.assertEqual(contradiction_result["contradiction_count"], 1)
        amended = contradiction_result["results"][0]["amended_claim"]
        self.assertEqual(amended["claim_id"], original_claim["claim_id"])
        self.assertEqual(len(amended["contradictions"]), 1)
        self.assertEqual(amended["contradictions"][0]["relation"], "CONTRADICT")
        self.assertEqual(amended["resolution"]["outcome"], "REFUTED")
        self.assertEqual(amended["resolution"]["resolved"], True)
        self.assertEqual(amended["corrected_at"], "2026-09-17T22:45:00Z")
        # The original, unamended record in claims_v1 is untouched --
        # append-only history is preserved; only the returned copy carries
        # the new annotation.
        self.assertEqual(claims_v1[0]["contradictions"], [])

    def test_two_reports_disagreeing_on_position_create_two_distinct_claims(self):
        # A subtler contradiction: the SAME player, same team, same report
        # URL, but a differently-parsed listed_position across two
        # revisions (e.g. a correction from "questionable" bucket noise --
        # constructed here purely as a position-string difference to keep
        # the fixture simple).
        #
        # FIXED by `NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-20260919`: this
        # test originally proved (PR #151) that claim_id incorporated the
        # player's href (stable) but NOT listed_position, so this collided
        # to the SAME claim_id with silently different content -- a sharper
        # finding than a simple appear/disappear case, and the root enabler
        # of `merge_claims_by_id` rejecting every real position correction
        # as a fabricated integrity violation. `claims_from_parsed_report`
        # now includes `listed_position` in the claim_id hash, so a genuine
        # position revision produces a genuinely DIFFERENT claim_id instead
        # of colliding. Re-ingesting an UNCHANGED report still produces the
        # SAME claim_id -- see
        # `test_news_ingest_official_inactives.test_deterministic_claim_ids_across_repeated_ingestion`,
        # which is unmodified and still passes.
        base_player = {
            "player_name": "Same Player",
            "source_player_href": "/players/same-player",
            "source_player_slug": "same-player",
            "note": None,
        }
        report_v1 = {
            "report_title": "Audit-fixture inactives: Team A at Team B",
            "report_published_at": "2026-09-17T22:00:00Z",
            "teams": [{"source_team_label": "TEAM A", "players": [
                {**base_player, "listed_position": "WR"},
            ]}],
        }
        report_v2 = {
            **report_v1,
            "teams": [{"source_team_label": "TEAM A", "players": [
                {**base_player, "listed_position": "TE"},
            ]}],
        }
        c1 = claims_from_parsed_report(
            report_v1, source_id="official_nfl",
            source_url="https://www.nfl.com/news/audit-fixture-report-2",
            observed_at="2026-09-17T22:05:00Z",
        )[0]
        c2 = claims_from_parsed_report(
            report_v2, source_id="official_nfl",
            source_url="https://www.nfl.com/news/audit-fixture-report-2",
            observed_at="2026-09-17T22:45:00Z",
        )[0]
        # A genuine position revision now produces a genuinely different
        # claim_id -- two independent, unlinked claims, exactly as a real
        # content revision should be represented -- rather than colliding
        # under one id with silently different content.
        self.assertNotEqual(c1["claim_id"], c2["claim_id"])
        self.assertNotEqual(c1["content_summary"], c2["content_summary"])
        self.assertNotEqual(c1["player"]["listed_position"], c2["player"]["listed_position"])
        # merge_claims_by_id (nfl.intelligence.news_claim_ledger) now treats
        # these as two legitimately distinct, non-conflicting claims when
        # merged together -- no integrity violation, because they no longer
        # share an id.
        from nfl.intelligence.news_claim_ledger import merge_claims_by_id
        merge_result = merge_claims_by_id([c1], [c2])
        self.assertEqual(merge_result["total_claim_count"], 2)
        self.assertEqual(merge_result["new_claim_count"], 1)


if __name__ == "__main__":
    unittest.main()
