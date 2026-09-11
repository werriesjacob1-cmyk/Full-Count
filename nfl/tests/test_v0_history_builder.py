#!/usr/bin/env python3
"""Tests for the NFL V0 historical research dataset builder.

The scientific contract is more important than the convenience:
- current-week outcomes must NEVER appear in current-week features
- history must roll forward only after the prediction row is emitted
- player identity is stable GSIS/player_id, never display name
- source schema drift fails closed instead of fabricating missing stats
"""
import unittest

from nfl.research import nflverse_history as nh


class UrlContract(unittest.TestCase):
    def test_player_stats_url_is_nflverse_release_csv(self):
        self.assertEqual(
            nh.player_stats_url(2025),
            "https://github.com/nflverse/nflverse-data/releases/download/"
            "stats_player/stats_player_week_2025.csv",
        )


class PriorOnlyFeatures(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "player_id": "00-TEST",
                "player_display_name": "Test Player",
                "position": "WR",
                "season": "2025", "week": "1", "season_type": "REG",
                "team": "AAA", "opponent_team": "BBB",
                "targets": "4", "receptions": "3", "receiving_yards": "30",
                "receiving_tds": "0", "carries": "0", "rushing_yards": "0",
                "rushing_tds": "0", "attempts": "0", "completions": "0",
                "passing_yards": "0", "passing_tds": "0",
            },
            {
                "player_id": "00-TEST",
                "player_display_name": "Test Player",
                "position": "WR",
                "season": "2025", "week": "2", "season_type": "REG",
                "team": "AAA", "opponent_team": "CCC",
                "targets": "10", "receptions": "8", "receiving_yards": "120",
                "receiving_tds": "1", "carries": "0", "rushing_yards": "0",
                "rushing_tds": "0", "attempts": "0", "completions": "0",
                "passing_yards": "0", "passing_tds": "0",
            },
            {
                "player_id": "00-TEST",
                "player_display_name": "Test Player",
                "position": "WR",
                "season": "2025", "week": "3", "season_type": "REG",
                "team": "AAA", "opponent_team": "DDD",
                "targets": "7", "receptions": "5", "receiving_yards": "70",
                "receiving_tds": "0", "carries": "0", "rushing_yards": "0",
                "rushing_tds": "0", "attempts": "0", "completions": "0",
                "passing_yards": "0", "passing_tds": "0",
            },
        ]

    def test_week_one_has_zero_prior_games_not_self_information(self):
        built = nh.build_prior_only_rows(self.rows, rolling_window=2)
        w1 = built[0]
        self.assertEqual(w1["history_n"], 0)
        self.assertIsNone(w1["features"]["rolling_receiving_yards"])
        self.assertEqual(w1["target"]["receiving_yards"], 30.0)

    def test_week_two_feature_uses_week_one_only(self):
        built = nh.build_prior_only_rows(self.rows, rolling_window=2)
        w2 = built[1]
        self.assertEqual(w2["history_n"], 1)
        self.assertEqual(w2["features"]["rolling_targets"], 4.0)
        self.assertEqual(w2["features"]["rolling_receptions"], 3.0)
        self.assertEqual(w2["features"]["rolling_receiving_yards"], 30.0)
        self.assertNotEqual(w2["features"]["rolling_receiving_yards"], 120.0)

    def test_week_three_uses_last_two_prior_games_only(self):
        built = nh.build_prior_only_rows(self.rows, rolling_window=2)
        w3 = built[2]
        self.assertEqual(w3["history_n"], 2)
        self.assertEqual(w3["features"]["rolling_targets"], 7.0)
        self.assertEqual(w3["features"]["rolling_receptions"], 5.5)
        self.assertEqual(w3["features"]["rolling_receiving_yards"], 75.0)
        self.assertEqual(w3["target"]["receiving_yards"], 70.0)

    def test_rows_are_sorted_chronologically_before_features(self):
        built = nh.build_prior_only_rows(
            [self.rows[2], self.rows[0], self.rows[1]], rolling_window=2
        )
        self.assertEqual([r["week"] for r in built], [1, 2, 3])
        self.assertEqual(built[2]["features"]["rolling_receiving_yards"], 75.0)

    def test_name_change_does_not_break_player_history(self):
        rows = [dict(self.rows[0]), dict(self.rows[1])]
        rows[1]["player_display_name"] = "T. Player"
        built = nh.build_prior_only_rows(rows, rolling_window=5)
        self.assertEqual(built[1]["history_n"], 1)
        self.assertEqual(built[1]["features"]["rolling_targets"], 4.0)

    def test_missing_required_source_column_fails_closed(self):
        broken = [dict(self.rows[0])]
        del broken[0]["targets"]
        with self.assertRaisesRegex(ValueError, "missing required nflverse columns"):
            nh.build_prior_only_rows(broken)


class EmptyIdentitySourceRows(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "player_id": "",
            "player_display_name": "",
            "position": "",
            "season": "2025", "week": "22", "season_type": "POST",
            "team": "", "opponent_team": "",
            "targets": "0", "receptions": "0", "receiving_yards": "0",
            "receiving_tds": "0", "carries": "0", "rushing_yards": "0",
            "rushing_tds": "0", "attempts": "0", "completions": "0",
            "passing_yards": "0", "passing_tds": "0",
        }
        row.update(overrides)
        return row

    def test_blank_structural_zero_row_is_skipped(self):
        built = nh.build_prior_only_rows([self._row()])
        self.assertEqual(built, [])

    def test_blank_id_with_real_offense_fails_hard(self):
        with self.assertRaisesRegex(ValueError, "empty player_id row carries offense"):
            nh.build_prior_only_rows([
                self._row(targets="1", receptions="1", receiving_yards="7")
            ])


class SeasonBoundary(unittest.TestCase):
    def test_prior_season_history_is_allowed_but_future_season_is_not(self):
        rows = [
            {
                "player_id": "P", "player_display_name": "P", "position": "RB",
                "season": "2024", "week": "18", "season_type": "REG",
                "team": "A", "opponent_team": "B",
                "targets": "2", "receptions": "2", "receiving_yards": "20",
                "receiving_tds": "0", "carries": "15", "rushing_yards": "75",
                "rushing_tds": "1", "attempts": "0", "completions": "0",
                "passing_yards": "0", "passing_tds": "0",
            },
            {
                "player_id": "P", "player_display_name": "P", "position": "RB",
                "season": "2025", "week": "1", "season_type": "REG",
                "team": "A", "opponent_team": "C",
                "targets": "3", "receptions": "2", "receiving_yards": "10",
                "receiving_tds": "0", "carries": "20", "rushing_yards": "100",
                "rushing_tds": "0", "attempts": "0", "completions": "0",
                "passing_yards": "0", "passing_tds": "0",
            },
        ]
        built = nh.build_prior_only_rows(rows, rolling_window=5)
        self.assertEqual(built[1]["history_n"], 1)
        self.assertEqual(built[1]["features"]["rolling_carries"], 15.0)
        self.assertEqual(built[1]["target"]["rushing_yards"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
