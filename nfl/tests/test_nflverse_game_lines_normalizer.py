#!/usr/bin/env python3
import unittest

from nfl.normalize.nflverse_game_lines import normalize_rows


COMMIT = "8ed09b2fe3ea42332b2249a995737e13dd931ff3"
DIGEST = "a" * 64
ACQUIRED = "2026-09-15T12:00:00-05:00"


def row(**changes):
    value = {
        "game_id": "2025_01_DEN_KC", "season": "2025", "game_type": "REG",
        "week": "1", "gameday": "2025-09-01", "away_team": "DEN",
        "home_team": "KC", "away_score": "20", "home_score": "24",
        "result": "4", "total": "44", "spread_line": "2.5",
        "away_spread_odds": "-112", "home_spread_odds": "-108",
        "total_line": "43.5", "under_odds": "-110", "over_odds": "-110",
    }
    value.update(changes)
    return value


def normalize(*rows):
    return normalize_rows(rows, source_commit=COMMIT, source_file_sha256=DIGEST, acquired_at=ACQUIRED)


class NflverseGameLineNormalizerTests(unittest.TestCase):
    def test_normalizes_spread_total_outcomes_and_provenance(self):
        result = normalize(row())
        self.assertEqual(result["stats"], {"normalized": 1, "excluded": 0, "spread_prices_complete": 1, "total_prices_complete": 1})
        got = result["normalized"][0]
        self.assertEqual(got["source_class"], "NFLVERSE_SCHEDULE_UNKNOWN_BOOK")
        self.assertEqual((got["away_handicap"], got["home_handicap"]), (2.5, -2.5))
        self.assertEqual(got["spread_outcome"], "HOME_COVER")
        self.assertEqual(got["total_outcome"], "OVER")
        self.assertEqual(got["source_acquired_at"], "2026-09-15T17:00:00Z")
        self.assertFalse(got["book_specific_eligible"])
        self.assertFalse(got["line_movement_eligible"])

    def test_pushes_are_explicit(self):
        got = normalize(row(result="3", home_score="23", total="43", spread_line="3", total_line="43"))["normalized"][0]
        self.assertEqual(got["spread_outcome"], "PUSH")
        self.assertEqual(got["total_outcome"], "PUSH")

    def test_unsettled_row_is_retained_as_exclusion(self):
        result = normalize(row(away_score="", home_score="", result="", total=""))
        self.assertEqual(result["normalized"], [])
        self.assertEqual(result["excluded"][0]["reason"], "UNSETTLED_GAME")

    def test_outcome_inconsistency_fails_closed(self):
        result = normalize(row(result="5"))
        self.assertEqual(result["excluded"][0]["reason"], "OUTCOME_INCONSISTENT")

    def test_duplicate_game_id_is_excluded(self):
        result = normalize(row(), row())
        self.assertEqual(result["stats"]["normalized"], 1)
        self.assertEqual(result["excluded"][0]["reason"], "DUPLICATE_GAME_ID")

    def test_missing_prices_do_not_invent_prices_or_drop_outcome(self):
        got = normalize(row(away_spread_odds="", home_spread_odds="", under_odds="", over_odds=""))["normalized"][0]
        self.assertFalse(got["spread_prices_complete"])
        self.assertFalse(got["total_prices_complete"])
        self.assertIsNone(got["away_spread_odds"])
        self.assertEqual(got["spread_outcome"], "HOME_COVER")

    def test_invalid_team_or_market_line_is_excluded(self):
        self.assertEqual(normalize(row(home_team="DEN"))["excluded"][0]["reason"], "INVALID_TEAM_IDENTITY")
        self.assertEqual(normalize(row(total_line="0"))["excluded"][0]["reason"], "INVALID_MARKET_LINE")

    def test_invalid_temporal_identity_is_excluded(self):
        self.assertEqual(normalize(row(gameday="tomorrow"))["excluded"][0]["reason"], "INVALID_TEMPORAL_IDENTITY")
        self.assertEqual(normalize(row(week=""))["excluded"][0]["reason"], "INVALID_TEMPORAL_IDENTITY")

    def test_provenance_contract_rejects_bad_values(self):
        with self.assertRaisesRegex(ValueError, "source_commit"):
            normalize_rows([row()], source_commit="main", source_file_sha256=DIGEST, acquired_at=ACQUIRED)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            normalize_rows([row()], source_commit=COMMIT, source_file_sha256="bad", acquired_at=ACQUIRED)
        with self.assertRaisesRegex(ValueError, "timezone"):
            normalize_rows([row()], source_commit=COMMIT, source_file_sha256=DIGEST, acquired_at="2026-09-15T12:00:00")


if __name__ == "__main__":
    unittest.main()
