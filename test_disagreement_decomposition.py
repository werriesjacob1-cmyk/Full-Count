#!/usr/bin/env python3
"""test_disagreement_decomposition.py -- coverage for
backtest/disagreement_decomposition.py, Priority 1/2/3 of the
model/context disagreement phase (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_disagreement_decomposition.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import disagreement_decomposition as dd


def row(**overrides):
    r = {
        "date": "2024-05-14", "prop_type": "hits_runs_rbis",
        "predicted_prob": 0.62, "outcome": 1,
        "cat_baseline_skill": 70.0, "cat_context": 40.0,  # conflict = +30, Weston-like
    }
    r.update(overrides)
    return r


class BaselineContextConflictTests(unittest.TestCase):
    def test_computed_correctly(self):
        self.assertEqual(dd.baseline_context_conflict(row()), 30.0)

    def test_none_when_either_missing(self):
        self.assertIsNone(dd.baseline_context_conflict(row(cat_baseline_skill=None)))
        self.assertIsNone(dd.baseline_context_conflict(row(cat_context=None)))


class ConflictTierTests(unittest.TestCase):
    def test_high_empirical_low_context(self):
        self.assertEqual(dd.conflict_tier(25.0), "high_empirical_low_context")
        self.assertEqual(dd.conflict_tier(20.0), "high_empirical_low_context")

    def test_high_context_low_empirical(self):
        self.assertEqual(dd.conflict_tier(-25.0), "high_context_low_empirical")
        self.assertEqual(dd.conflict_tier(-20.0), "high_context_low_empirical")

    def test_balanced(self):
        self.assertEqual(dd.conflict_tier(0.0), "balanced")
        self.assertEqual(dd.conflict_tier(19.9), "balanced")

    def test_none_passthrough(self):
        self.assertIsNone(dd.conflict_tier(None))


class SameProbabilityBucketConflictTestTests(unittest.TestCase):
    def test_splits_by_bucket_then_tier(self):
        rows = [
            row(predicted_prob=0.62, cat_baseline_skill=70.0, cat_context=40.0, outcome=0),  # Weston-like
            row(predicted_prob=0.62, cat_baseline_skill=40.0, cat_context=70.0, outcome=1),  # opposite
        ]
        report = dd.same_probability_bucket_conflict_test(rows, "hits_runs_rbis")
        cell = report["0.60-0.65"]
        self.assertEqual(cell["high_empirical_low_context"]["hit_rate"], 0.0)
        self.assertEqual(cell["high_context_low_empirical"]["hit_rate"], 1.0)

    def test_only_target_market_included(self):
        rows = [row(prop_type="home_run", predicted_prob=0.62)]
        report = dd.same_probability_bucket_conflict_test(rows, "hits_runs_rbis")
        self.assertEqual(report, {})


class YearStabilityOfConflictTests(unittest.TestCase):
    def test_splits_by_year(self):
        rows = [row(date="2024-05-01"), row(date="2025-05-01")]
        report = dd.year_stability_of_conflict(rows, "hits_runs_rbis")
        self.assertIn("2024", report)
        self.assertIn("2025", report)


class BuildReportTests(unittest.TestCase):
    def test_only_cat_markets_analyzed(self):
        report = dd.build_report([row()])
        self.assertEqual(set(report["per_market"].keys()), {"hits", "hits_runs_rbis"})

    def test_rows_missing_components_excluded(self):
        rows = [row(cat_baseline_skill=None)]
        report = dd.build_report(rows)
        self.assertEqual(report["per_market"]["hits_runs_rbis"]["n_rows_with_components"], 0)

    def test_ungraded_rows_excluded(self):
        rows = [row(outcome=1), row()]
        rows[1].pop("outcome")
        report = dd.build_report(rows)
        self.assertEqual(report["per_market"]["hits_runs_rbis"]["n_rows_with_components"], 1)

    def test_empty_input_does_not_crash(self):
        report = dd.build_report([])
        self.assertEqual(report["per_market"]["hits"]["n_rows_with_components"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
