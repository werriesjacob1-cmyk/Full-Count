#!/usr/bin/env python3
"""Tests for QB rushing process decomposition from nflverse play-by-play."""
import unittest

from nfl.research import qb_rushing_components as qr


class QBRushingClassification(unittest.TestCase):
    def test_kneel_is_its_own_process_and_preserves_negative_yards(self):
        row = {
            "rush_attempt": "1",
            "qb_kneel": "1",
            "qb_scramble": "0",
            "two_point_attempt": "0",
            "yards_gained": "-1",
        }
        obs = qr.classify_qb_rush(row, is_qb=True)
        self.assertEqual(obs["component"], "kneel")
        self.assertEqual(obs["yards"], -1.0)

    def test_scramble_is_not_designed_rush(self):
        row = {
            "rush_attempt": 1,
            "qb_kneel": 0,
            "qb_scramble": 1,
            "two_point_attempt": 0,
            "yards_gained": 9,
        }
        self.assertEqual(
            qr.classify_qb_rush(row, is_qb=True)["component"],
            "scramble",
        )

    def test_remaining_qb_carry_is_designed_rush(self):
        row = {
            "rush_attempt": 1,
            "qb_kneel": 0,
            "qb_scramble": 0,
            "two_point_attempt": 0,
            "yards_gained": 4,
        }
        self.assertEqual(
            qr.classify_qb_rush(row, is_qb=True)["component"],
            "designed_rush",
        )

    def test_non_qb_rush_is_out_of_scope(self):
        row = {
            "rush_attempt": 1,
            "qb_kneel": 0,
            "qb_scramble": 0,
            "two_point_attempt": 0,
            "yards_gained": 5,
        }
        self.assertIsNone(qr.classify_qb_rush(row, is_qb=False))

    def test_two_point_attempt_is_not_an_official_carry(self):
        row = {
            "rush_attempt": 1,
            "qb_kneel": 0,
            "qb_scramble": 0,
            "two_point_attempt": 1,
            "yards_gained": 2,
        }
        self.assertIsNone(qr.classify_qb_rush(row, is_qb=True))

    def test_contradictory_kneel_and_scramble_fails_closed(self):
        row = {
            "rush_attempt": 1,
            "qb_kneel": 1,
            "qb_scramble": 1,
            "two_point_attempt": 0,
            "yards_gained": 0,
        }
        with self.assertRaisesRegex(ValueError, "kneel.*scramble"):
            qr.classify_qb_rush(row, is_qb=True)

    def test_aggregate_keeps_components_separate(self):
        observations = [
            {"component": "designed_rush", "yards": 7.0},
            {"component": "scramble", "yards": 11.0},
            {"component": "kneel", "yards": -2.0},
        ]
        out = qr.aggregate_components(observations)
        self.assertEqual(out["carries"], 3)
        self.assertEqual(out["rushing_yards"], 16.0)
        self.assertEqual(out["designed_rush_attempts"], 1)
        self.assertEqual(out["scramble_attempts"], 1)
        self.assertEqual(out["kneel_attempts"], 1)
        self.assertEqual(out["kneel_yards"], -2.0)

    def test_malformed_numeric_yards_fails_closed(self):
        row = {
            "rush_attempt": 1,
            "qb_kneel": 0,
            "qb_scramble": 0,
            "two_point_attempt": 0,
            "yards_gained": "not-a-number",
        }
        with self.assertRaisesRegex(ValueError, "yards_gained"):
            qr.classify_qb_rush(row, is_qb=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
