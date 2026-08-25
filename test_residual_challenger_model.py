#!/usr/bin/env python3
"""test_residual_challenger_model.py -- coverage for
backtest/residual_challenger_model.py, Priority 4 of the residual-
opportunity phase (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_residual_challenger_model.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import residual_challenger_model as rcm


def row(**overrides):
    r = {
        "date": "2024-05-14", "game_pk": 1, "player_id": 100,
        "prop_type": "hits", "predicted_prob": 0.62, "outcome": 1,
        "actual_pa": 4,
        "signals": {"lineup_slot": 100.0, "days_rest": 0, "getaway_day": 0.0},
    }
    r.update(overrides)
    return r


class JointKeyTests(unittest.TestCase):
    def test_key_built_from_order_days_rest_getaway(self):
        key = rcm.joint_key(row())
        self.assertEqual(key, (1, "0_days_rest", "not_getaway_day"))

    def test_missing_order_returns_none(self):
        self.assertIsNone(rcm.joint_key(row(signals={})))

    def test_missing_days_rest_returns_none(self):
        self.assertIsNone(rcm.joint_key(row(signals={"lineup_slot": 100.0, "getaway_day": 0.0})))

    def test_missing_getaway_day_returns_none(self):
        self.assertIsNone(rcm.joint_key(row(signals={"lineup_slot": 100.0, "days_rest": 0})))


class FitJointPaDistributionTests(unittest.TestCase):
    def test_cell_below_min_n_is_dropped(self):
        rows = [row(player_id=i) for i in range(5)]  # only 5 rows, below MIN_CELL_N
        dist = rcm.fit_joint_pa_distribution(rows, min_cell_n=200)
        self.assertEqual(dist, {})

    def test_cell_at_or_above_min_n_is_fit(self):
        rows = [row(player_id=i) for i in range(250)]
        dist = rcm.fit_joint_pa_distribution(rows, min_cell_n=200)
        key = (1, "0_days_rest", "not_getaway_day")
        self.assertIn(key, dist)
        self.assertEqual(dist[key]["_n"], 250)
        self.assertAlmostEqual(dist[key]["4"], 1.0, places=5)


class ChallengerProbabilityJointTests(unittest.TestCase):
    def test_uses_joint_cell_when_available(self):
        key = (1, "0_days_rest", "not_getaway_day")
        joint_dist = {key: {"3": 1.0, "_n": 250}}
        order_dist = {1: {"4": 1.0, "_n": 250}}
        hit_rate = {"3": 0.3, "4": 0.7}
        p = rcm.challenger_probability_joint(row(), joint_dist, order_dist, hit_rate)
        self.assertAlmostEqual(p, 0.3, places=5)  # uses joint cell's "3", not order's "4"

    def test_falls_back_to_order_when_joint_cell_missing(self):
        joint_dist = {}  # no joint cells fit at all
        order_dist = {1: {"4": 1.0, "_n": 250}}
        hit_rate = {"4": 0.7}
        p = rcm.challenger_probability_joint(row(), joint_dist, order_dist, hit_rate)
        self.assertAlmostEqual(p, 0.7, places=5)

    def test_falls_back_when_days_rest_missing(self):
        joint_dist = {(1, "0_days_rest", "not_getaway_day"): {"3": 1.0, "_n": 250}}
        order_dist = {1: {"4": 1.0, "_n": 250}}
        hit_rate = {"3": 0.3, "4": 0.7}
        r = row(signals={"lineup_slot": 100.0, "getaway_day": 0.0})  # no days_rest -> joint_key None
        p = rcm.challenger_probability_joint(r, joint_dist, order_dist, hit_rate)
        self.assertAlmostEqual(p, 0.7, places=5)  # order fallback


class BuildReportTests(unittest.TestCase):
    def test_reports_joint_vs_fallback_usage(self):
        train = [row(date="2024-05-01", player_id=i) for i in range(250)]
        holdout = [row(date="2026-05-01", player_id=i) for i in range(50)]
        report = rcm.build_report(train + holdout, market="hits")
        usage = report["n_holdout_rows_using_joint_cell_vs_order_fallback"]
        self.assertEqual(usage["joint_cell"] + usage["order_fallback"],
                          report["n_holdout_market_rows_with_challenger_prob"])

    def test_empty_input_does_not_crash(self):
        report = rcm.build_report([], market="hits")
        self.assertEqual(report["n_train_player_games"], 0)
        self.assertEqual(report["n_holdout_market_rows_with_challenger_prob"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
