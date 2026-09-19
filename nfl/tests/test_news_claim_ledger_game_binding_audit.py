#!/usr/bin/env python3
"""Audit item 1: canonical game-id binding for the News Brain claim ledger.

Fixture: `nfl/tests/fixtures/games_csv_audit_subset.json`, a 9-row real
subset of the exact nflverse/nfldata `data/games.csv` commit already pinned
in this repo (`coach_regime_registry.HC_GAMES_SOURCE` /
`game_market_b0_research.PINNED_SCHEDULE_SOURCE`), independently re-fetched
live on 2026-09-19 (full file: 2,177,838 bytes, sha256
26332ae5...b96d188 -- byte-for-byte and digest-identical to the existing
pin). It includes the exact real BUF/DET Week 2 2026 row PR #146's real
claims concern, five real decoy same-week games, a real same-season rematch
pair (GB/MIN), and one real 1999 game with a divergent `old_game_id`.
"""
import json
import unittest
from pathlib import Path

from nfl.research.coach_regime_registry import HC_GAMES_SOURCE
from nfl.intelligence.news_claim_ledger_game_binding_audit import (
    GameBindingAuditError,
    published_at_to_et_date,
    resolve_game_id_by_team_pair_and_date,
    team_pair_date_uniqueness_report,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "games_csv_audit_subset.json").read_text()
)
ROWS = FIXTURE["rows"]


class SourcePinCrossCheckTests(unittest.TestCase):
    def test_independent_live_fetch_matches_the_existing_in_repo_pin(self):
        # This audit re-fetched the SAME commit independently; the digest
        # recorded in the fixture provenance must match the pin this repo
        # already trusts elsewhere, proving it is the same real file, not a
        # different one that happens to share a commit hash string.
        prov = FIXTURE["provenance"]
        self.assertEqual(prov["commit"], HC_GAMES_SOURCE["commit"])
        self.assertEqual(prov["full_file_bytes"], HC_GAMES_SOURCE["bytes"])
        self.assertEqual(prov["full_file_sha256"], HC_GAMES_SOURCE["sha256"])


class RealGameResolutionTests(unittest.TestCase):
    def test_real_buf_det_week2_report_resolves_to_the_real_game_id(self):
        result = resolve_game_id_by_team_pair_and_date(
            ["BUF", "DET"], "2026-09-17", ROWS
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["game_id"], "2026_02_DET_BUF")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["season"], "2026")
        self.assertEqual(result["week"], "2")

    def test_real_report_published_at_converts_to_the_correct_et_date(self):
        # The real captured claim's published_at, matched against the real
        # gameday.
        et_date = published_at_to_et_date("2026-09-17T22:51:40.379Z")
        self.assertEqual(et_date, "2026-09-17")
        result = resolve_game_id_by_team_pair_and_date(
            ["DET", "BUF"], et_date, ROWS  # order-independence of the pair
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["game_id"], "2026_02_DET_BUF")

    def test_wrong_date_does_not_spuriously_match_a_different_weeks_game(self):
        result = resolve_game_id_by_team_pair_and_date(
            ["BUF", "DET"], "2026-09-20", ROWS
        )
        self.assertFalse(result["resolved"])
        self.assertEqual(result["match_count"], 0)
        self.assertEqual(result["reason"], "NO_UNIQUE_SCHEDULE_MATCH")

    def test_decoy_same_week_games_do_not_collide_with_each_other(self):
        for game_id, teams, date in [
            ("2026_02_CAR_ATL", ["CAR", "ATL"], "2026-09-20"),
            ("2026_02_NO_BAL", ["NO", "BAL"], "2026-09-20"),
        ]:
            with self.subTest(game_id=game_id):
                result = resolve_game_id_by_team_pair_and_date(teams, date, ROWS)
                self.assertTrue(result["resolved"])
                self.assertEqual(result["game_id"], game_id)

    def test_real_rematch_pair_resolves_to_two_different_real_game_ids(self):
        week1 = resolve_game_id_by_team_pair_and_date(
            ["GB", "MIN"], "2026-09-13", ROWS
        )
        week10 = resolve_game_id_by_team_pair_and_date(
            ["GB", "MIN"], "2026-11-15", ROWS
        )
        self.assertEqual(week1["game_id"], "2026_01_GB_MIN")
        self.assertEqual(week10["game_id"], "2026_10_MIN_GB")
        self.assertNotEqual(week1["game_id"], week10["game_id"])

    def test_no_team_pair_fails_closed_without_guessing(self):
        result = resolve_game_id_by_team_pair_and_date(None, "2026-09-17", ROWS)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["reason"], "NO_TEAM_PAIR")

    def test_injected_real_ambiguity_fails_closed_not_guessed(self):
        # Construct a genuine collision by duplicating the real BUF/DET row
        # under a different game_id, on purpose, to prove the join refuses
        # to guess between two real candidates rather than picking one.
        collided = list(ROWS) + [{**ROWS[0], "game_id": "FAKE_DUPLICATE_FOR_TEST"}]
        result = resolve_game_id_by_team_pair_and_date(["BUF", "DET"], "2026-09-17", collided)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["match_count"], 2)


class UtcToEtDateBoundaryTests(unittest.TestCase):
    def test_naive_utc_truncation_would_be_wrong_near_midnight(self):
        # Constructed boundary fixture (not a real report): a timestamp at
        # 2026-01-02T02:30:00Z is 2026-01-01 21:30 America/New_York (EST,
        # UTC-5) -- a different calendar date than naive UTC truncation
        # would read. This is a code-correctness check on date-conversion
        # math, not a claim about any real game or report.
        et_date = published_at_to_et_date("2026-01-02T02:30:00Z")
        self.assertEqual(et_date, "2026-01-01")
        self.assertNotEqual(et_date, "2026-01-02")  # naive-truncation's wrong answer

    def test_malformed_timestamp_fails_closed(self):
        with self.assertRaises(GameBindingAuditError):
            published_at_to_et_date("not-a-timestamp")
        with self.assertRaises(GameBindingAuditError):
            published_at_to_et_date("2026-09-17")  # no timezone


class UniquenessReportTests(unittest.TestCase):
    def test_real_fixture_rows_have_zero_collisions(self):
        report = team_pair_date_uniqueness_report(ROWS)
        self.assertEqual(report["total_rows"], len(ROWS))
        self.assertEqual(report["collision_count"], 0)

    def test_report_surfaces_an_injected_collision(self):
        collided = list(ROWS) + [{**ROWS[0], "game_id": "FAKE_DUPLICATE_FOR_TEST"}]
        report = team_pair_date_uniqueness_report(collided)
        self.assertEqual(report["collision_count"], 1)


if __name__ == "__main__":
    unittest.main()
