#!/usr/bin/env python3
"""test_disagreement_challenger_model.py -- coverage for
backtest/disagreement_challenger_model.py, Priority 4/5 of the
model/context disagreement phase (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_disagreement_challenger_model.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import disagreement_challenger_model as dcm


def row(**overrides):
    r = {
        "date": "2024-05-14", "prop_type": "hits_runs_rbis",
        "predicted_prob": 0.62, "outcome": 1,
        "cat_baseline_skill": 70.0, "cat_context": 40.0,  # conflict +30, Weston-like
    }
    r.update(overrides)
    return r


class FitBucketTierHitRateTests(unittest.TestCase):
    def test_cell_below_min_n_falls_back_to_bucket(self):
        rows = [row(player_id=i) for i in range(5)]
        cell_rate, bucket_rate = dcm.fit_bucket_tier_hit_rate(rows, min_cell_n=200)
        self.assertEqual(cell_rate, {})
        self.assertIn("0.60-0.65", bucket_rate)

    def test_cell_at_min_n_is_fit(self):
        rows = [row(player_id=i) for i in range(250)]
        cell_rate, bucket_rate = dcm.fit_bucket_tier_hit_rate(rows, min_cell_n=200)
        self.assertIn(("0.60-0.65", "high_empirical_low_context"), cell_rate)
        self.assertEqual(cell_rate[("0.60-0.65", "high_empirical_low_context")], 1.0)

    def test_bucket_rate_pools_all_tiers(self):
        rows = [row(cat_baseline_skill=70.0, cat_context=40.0, outcome=1),
                row(cat_baseline_skill=40.0, cat_context=70.0, outcome=0)]
        _, bucket_rate = dcm.fit_bucket_tier_hit_rate(rows, min_cell_n=200)
        self.assertEqual(bucket_rate["0.60-0.65"], 0.5)


class ChallengerProbabilityTests(unittest.TestCase):
    def test_uses_cell_when_available(self):
        cell_rate = {("0.60-0.65", "high_empirical_low_context"): 0.55}
        bucket_rate = {"0.60-0.65": 0.65}
        p = dcm.challenger_probability(row(), cell_rate, bucket_rate)
        self.assertEqual(p, 0.55)

    def test_falls_back_to_bucket_when_cell_missing(self):
        cell_rate = {}
        bucket_rate = {"0.60-0.65": 0.65}
        p = dcm.challenger_probability(row(), cell_rate, bucket_rate)
        self.assertEqual(p, 0.65)

    def test_none_when_bucket_undetermined(self):
        cell_rate, bucket_rate = {}, {}
        p = dcm.challenger_probability(row(predicted_prob=None), cell_rate, bucket_rate)
        self.assertIsNone(p)


class BuildReportTests(unittest.TestCase):
    def test_train_holdout_split_by_year(self):
        train = [row(date="2024-05-01", player_id=i) for i in range(250)]
        holdout = [row(date="2026-05-01", player_id=i) for i in range(50)]
        report = dcm.build_report(train + holdout, "hits_runs_rbis")
        self.assertEqual(report["n_train_rows"], 250)
        self.assertEqual(report["n_holdout_rows_with_challenger_prob"], 50)

    def test_other_market_excluded(self):
        rows = [row(prop_type="home_run")]
        report = dcm.build_report(rows, "hits_runs_rbis")
        self.assertEqual(report["n_train_rows"], 0)

    def test_empty_input_does_not_crash(self):
        report = dcm.build_report([], "hits_runs_rbis")
        self.assertEqual(report["n_train_rows"], 0)
        self.assertEqual(report["n_holdout_rows_with_challenger_prob"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
