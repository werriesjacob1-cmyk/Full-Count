#!/usr/bin/env python3
"""test_residual_opportunity_decomposition.py -- coverage for
backtest/residual_opportunity_decomposition.py, Priority 1+2 of the
residual-opportunity phase (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_residual_opportunity_decomposition.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import residual_opportunity_decomposition as rod


def row(**overrides):
    r = {
        "date": "2024-05-14", "game_pk": 1, "player_id": 100,
        "prop_type": "hits", "predicted_prob": 0.62, "outcome": 1,
        "actual_pa": 4, "signals": {"lineup_slot": 100.0},  # order 1
    }
    r.update(overrides)
    return r


class OrderMeanPaTests(unittest.TestCase):
    def test_mean_computed_per_order(self):
        rows = [row(actual_pa=3), row(actual_pa=5, player_id=200)]
        means = rod.order_mean_pa(rows)
        self.assertEqual(means[1], 4.0)

    def test_rows_without_order_or_pa_excluded(self):
        rows = [row(signals={}), row(actual_pa=None, player_id=300)]
        means = rod.order_mean_pa(rows)
        self.assertEqual(means, {})


class ResidualPaTests(unittest.TestCase):
    def test_residual_computed_correctly(self):
        means = {1: 4.0}
        self.assertEqual(rod.residual_pa(row(actual_pa=6), means), 2.0)
        self.assertEqual(rod.residual_pa(row(actual_pa=2), means), -2.0)

    def test_unknown_order_returns_none(self):
        means = {1: 4.0}
        self.assertIsNone(rod.residual_pa(row(signals={"lineup_slot": 0.0}), means))  # order 9


class IsShortfallTests(unittest.TestCase):
    def test_shortfall_true_below_margin(self):
        means = {1: 4.0}
        self.assertTrue(rod.is_shortfall(row(actual_pa=2), means))  # residual -2.0 <= -1.0

    def test_shortfall_false_above_margin(self):
        means = {1: 4.0}
        self.assertFalse(rod.is_shortfall(row(actual_pa=4), means))  # residual 0.0

    def test_boundary_exactly_at_margin_is_shortfall(self):
        means = {1: 4.0}
        self.assertTrue(rod.is_shortfall(row(actual_pa=3), means))  # residual -1.0 <= -1.0


class PredictorGroupFunctionTests(unittest.TestCase):
    def test_getaway_day_groups(self):
        # generate_picks.py:1891 stores -2 when it IS a getaway day, 0
        # otherwise -- not a 0/1 flag. Real bug caught here: an earlier
        # version checked `v >= 0.5`, which silently matched zero real rows.
        self.assertEqual(rod._getaway_day_group({"getaway_day": -2.0}), "getaway_day")
        self.assertEqual(rod._getaway_day_group({"getaway_day": 0.0}), "not_getaway_day")
        self.assertIsNone(rod._getaway_day_group({}))

    def test_days_rest_groups(self):
        self.assertEqual(rod._days_rest_group({"days_rest": 0}), "0_days_rest")
        self.assertEqual(rod._days_rest_group({"days_rest": 1}), "1_day_rest")
        self.assertEqual(rod._days_rest_group({"days_rest": 3}), "2-3_days_rest")
        self.assertEqual(rod._days_rest_group({"days_rest": 7}), "4plus_days_rest")

    def test_series_game_groups(self):
        self.assertEqual(rod._series_game_group({"series_game": 1}), "series_game_1")
        self.assertEqual(rod._series_game_group({"series_game": 2}), "series_game_2")
        self.assertEqual(rod._series_game_group({"series_game": 4}), "series_game_3plus")

    def test_consecutive_games_group_presence_only(self):
        self.assertEqual(rod._consecutive_games_group({"consecutive_games": 11}),
                          "10plus_consecutive_games")
        self.assertEqual(rod._consecutive_games_group({}), "no_fatigue_flag")


class ShortfallRateByPredictorSameOrderTests(unittest.TestCase):
    def test_splits_by_order_then_group(self):
        rows = [
            row(actual_pa=6, signals={"lineup_slot": 100.0, "getaway_day": 0.0}),  # order 1, no shortfall
            row(actual_pa=2, signals={"lineup_slot": 100.0, "getaway_day": -2.0}, player_id=200),  # order 1, shortfall
        ]
        means = {1: 4.0}
        report = rod.shortfall_rate_by_predictor_same_order(rows, means, rod._getaway_day_group)
        self.assertEqual(report[1]["not_getaway_day"]["shortfall_rate"], 0.0)
        self.assertEqual(report[1]["getaway_day"]["shortfall_rate"], 1.0)


class ShortfallRateByPredictorSameProbabilityBucketTests(unittest.TestCase):
    def test_splits_by_bucket_then_group(self):
        rows = [
            row(predicted_prob=0.62, actual_pa=6, signals={"lineup_slot": 100.0, "getaway_day": 0.0}),
            row(predicted_prob=0.62, actual_pa=2, signals={"lineup_slot": 100.0, "getaway_day": -2.0}, player_id=200),
        ]
        means = {1: 4.0}
        report = rod.shortfall_rate_by_predictor_same_probability_bucket(rows, means, rod._getaway_day_group)
        self.assertIn("0.60-0.65", report)


class YearStabilityOfPredictorTests(unittest.TestCase):
    def test_splits_by_year_and_group(self):
        rows = [
            row(date="2024-05-01", actual_pa=6, signals={"lineup_slot": 100.0, "getaway_day": 0.0}),
            row(date="2025-05-01", actual_pa=2, signals={"lineup_slot": 100.0, "getaway_day": -2.0}, player_id=200),
        ]
        means = {1: 4.0}
        report = rod.year_stability_of_predictor(rows, means, rod._getaway_day_group,
                                                   "not_getaway_day", "getaway_day")
        self.assertIn("2024", report)
        self.assertIn("2025", report)


class BuildReportTests(unittest.TestCase):
    def test_report_has_all_predictors(self):
        rows = [row(signals={"lineup_slot": 100.0, "getaway_day": -2.0, "days_rest": 1,
                              "series_game": 2, "consecutive_games": 11})]
        report = rod.build_report(rows)
        self.assertEqual(set(report["predictors"].keys()),
                          {"getaway_day", "days_rest", "series_game", "consecutive_games"})

    def test_empty_input_does_not_crash(self):
        report = rod.build_report([])
        self.assertEqual(report["n_player_games"], 0)

    def test_only_hitter_markets_included(self):
        rows = [row(prop_type="strikeouts")]
        report = rod.build_report(rows)
        self.assertEqual(report["n_player_games"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
