#!/usr/bin/env python3
"""test_prob_subgroup_trust_report.py -- coverage for
backtest/prob_subgroup_trust_report.py, Priority 6's first real accuracy
study (same-nominal-probability subgroup trustworthiness). Synthetic
fixtures only.

    /tmp/mlbvenv/bin/python3 test_prob_subgroup_trust_report.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import prob_subgroup_trust_report as pstr


def row(**overrides):
    r = {
        "date": "2026-05-14", "prop_type": "hits", "predicted_prob": 0.62,
        "outcome": 1, "fair_test": True, "actual_pa": 4,
    }
    r.update(overrides)
    return r


class PaBucketTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(pstr.pa_bucket(0), "0-1_pa")
        self.assertEqual(pstr.pa_bucket(1), "0-1_pa")
        self.assertEqual(pstr.pa_bucket(2), "2-3_pa")
        self.assertEqual(pstr.pa_bucket(4), "4_pa")
        self.assertEqual(pstr.pa_bucket(6), "5plus_pa")

    def test_none_is_unknown_not_a_crash(self):
        self.assertEqual(pstr.pa_bucket(None), "unknown")


class BuildSubgroupReportTests(unittest.TestCase):
    def test_groups_by_probability_bucket_first(self):
        rows = [row(predicted_prob=0.62), row(predicted_prob=0.91, outcome=0)]
        report = pstr.build_subgroup_report(rows)
        self.assertIn("0.60-0.65", report["by_probability_bucket"])
        self.assertIn("0.90-0.95", report["by_probability_bucket"])

    def test_overall_rate_within_bucket_is_correct(self):
        rows = [row(predicted_prob=0.62, outcome=1), row(predicted_prob=0.63, outcome=0)]
        report = pstr.build_subgroup_report(rows)
        b = report["by_probability_bucket"]["0.60-0.65"]
        self.assertEqual(b["overall_n"], 2)
        self.assertEqual(b["overall_hit_rate"], 0.5)

    def test_market_axis_breaks_down_within_bucket(self):
        rows = [row(prop_type="hits", outcome=1), row(prop_type="strikeouts", outcome=0)]
        report = pstr.build_subgroup_report(rows)
        axes = report["by_probability_bucket"]["0.60-0.65"]["axes"]
        self.assertEqual(axes["market"]["hits"]["hit_rate"], 1.0)
        self.assertEqual(axes["market"]["strikeouts"]["hit_rate"], 0.0)

    def test_fair_test_axis_present(self):
        rows = [row(fair_test=True, outcome=1), row(fair_test=False, outcome=0)]
        report = pstr.build_subgroup_report(rows)
        axes = report["by_probability_bucket"]["0.60-0.65"]["axes"]
        self.assertIn(True, axes["fair_test"])
        self.assertIn(False, axes["fair_test"])

    def test_flags_a_materially_divergent_subgroup(self):
        # bucket overall rate 50%, but "singles" market subgroup is 100%
        # with plenty of volume -- should be flagged.
        rows = ([row(prop_type="singles", outcome=1) for _ in range(250)] +
                [row(prop_type="hits", outcome=0) for _ in range(250)])
        report = pstr.build_subgroup_report(rows)
        flagged = report["flagged_divergent_subgroups"]
        singles_flags = [f for f in flagged if f["axis"] == "market" and f["subgroup"] == "singles"]
        self.assertEqual(len(singles_flags), 1)
        self.assertEqual(singles_flags[0]["n"], 250)
        self.assertAlmostEqual(singles_flags[0]["delta"], 0.5, places=2)

    def test_does_not_flag_below_min_n(self):
        # Same 100%-vs-0% divergence, but too few rows to flag.
        rows = ([row(prop_type="singles", outcome=1) for _ in range(50)] +
                [row(prop_type="hits", outcome=0) for _ in range(50)])
        report = pstr.build_subgroup_report(rows)
        singles_flags = [f for f in report["flagged_divergent_subgroups"]
                          if f["axis"] == "market" and f["subgroup"] == "singles"]
        self.assertEqual(len(singles_flags), 0)

    def test_does_not_flag_small_deltas(self):
        rows = ([row(prop_type="singles", outcome=1) for _ in range(150)] +
                [row(prop_type="singles", outcome=0) for _ in range(140)] +
                [row(prop_type="hits", outcome=1) for _ in range(150)] +
                [row(prop_type="hits", outcome=0) for _ in range(140)])
        report = pstr.build_subgroup_report(rows)
        self.assertEqual(report["flagged_divergent_subgroups"], [])

    def test_flags_sorted_by_largest_absolute_delta_first(self):
        rows = ([row(prop_type="a", outcome=1) for _ in range(300)] +
                [row(prop_type="b", outcome=0) for _ in range(300)] +
                [row(prop_type="c", outcome=1) for _ in range(210)] +
                [row(prop_type="d", outcome=0) for _ in range(210)])
        # overall rate across all 1020 rows in this bucket: (300+210)/1020 ~= 0.5
        report = pstr.build_subgroup_report(rows)
        flagged = report["flagged_divergent_subgroups"]
        self.assertTrue(len(flagged) >= 2)
        deltas = [abs(f["delta"]) for f in flagged]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_empty_input_does_not_crash(self):
        report = pstr.build_subgroup_report([])
        self.assertEqual(report["by_probability_bucket"], {})
        self.assertEqual(report["flagged_divergent_subgroups"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
