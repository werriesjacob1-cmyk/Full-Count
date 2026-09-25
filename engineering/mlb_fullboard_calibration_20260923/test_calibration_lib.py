"""Tests for calibration_lib.py's reusable bucketing / cluster-bootstrap
functions. These are pure-function unit tests against synthetic fixtures --
they do not read any real board_freeze artifact (that integration is
exercised by running analyze_fullboard_calibration.py itself against the
real committed files, per the README)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibration_lib as cl  # noqa: E402


def _row(predicted, hit, cluster):
    return {"predicted": predicted, "hit": hit, "cluster": cluster}


class MakeQuantileBucketsTest(unittest.TestCase):
    def test_splits_into_requested_number_of_roughly_equal_buckets(self):
        rows = [_row(i / 10.0, 0, f"g{i}") for i in range(10)]
        buckets = cl.make_quantile_buckets(rows, 5)
        self.assertEqual(len(buckets), 5)
        for b in buckets:
            self.assertEqual(len(b), 2)

    def test_never_fabricates_more_buckets_than_real_records(self):
        rows = [_row(0.1, 0, "g1"), _row(0.9, 1, "g2")]
        buckets = cl.make_quantile_buckets(rows, 10)
        self.assertEqual(len(buckets), 2)
        self.assertEqual(sum(len(b) for b in buckets), 2)

    def test_buckets_are_ordered_ascending_by_predicted(self):
        rows = [_row(0.9, 1, "a"), _row(0.1, 0, "b"), _row(0.5, 0, "c")]
        buckets = cl.make_quantile_buckets(rows, 3)
        means = [b[0]["predicted"] for b in buckets]
        self.assertEqual(means, sorted(means))

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(cl.make_quantile_buckets([], 5), [])

    def test_rejects_nonpositive_bucket_count(self):
        with self.assertRaises(ValueError):
            cl.make_quantile_buckets([_row(0.5, 1, "a")], 0)


class SummarizeBucketTest(unittest.TestCase):
    def test_perfectly_calibrated_bucket_has_zero_gap(self):
        # 10 rows at predicted=0.3, exactly 3 hits -> realized == predicted.
        rows = [_row(0.3, 1 if i < 3 else 0, f"g{i}") for i in range(10)]
        summary = cl.summarize_bucket(rows)
        self.assertEqual(summary["n"], 10)
        self.assertAlmostEqual(summary["mean_predicted"], 0.3)
        self.assertAlmostEqual(summary["realized_hit_rate"], 0.3)
        self.assertAlmostEqual(summary["calibration_gap"], 0.0)

    def test_overconfident_bucket_has_negative_gap(self):
        # predicted 0.8 on average, but nobody actually hit.
        rows = [_row(0.8, 0, f"g{i}") for i in range(5)]
        summary = cl.summarize_bucket(rows)
        self.assertAlmostEqual(summary["calibration_gap"], -0.8)

    def test_reports_distinct_game_count_not_row_count(self):
        rows = [_row(0.5, 1, "g1"), _row(0.5, 0, "g1"), _row(0.5, 1, "g2")]
        summary = cl.summarize_bucket(rows)
        self.assertEqual(summary["n"], 3)
        self.assertEqual(summary["n_games"], 2)


class ClusterBootstrapCiTest(unittest.TestCase):
    def test_deterministic_with_fixed_seed(self):
        rows = [_row(0.5, i % 2, f"g{i % 5}") for i in range(30)]
        a = cl.cluster_bootstrap_ci(rows, cl.mean_hit_rate, seed=42, n_boot=500)
        b = cl.cluster_bootstrap_ci(rows, cl.mean_hit_rate, seed=42, n_boot=500)
        self.assertEqual(a["ci_low"], b["ci_low"])
        self.assertEqual(a["ci_high"], b["ci_high"])

    def test_interval_brackets_the_point_estimate(self):
        rows = [_row(0.5, i % 3 == 0, f"g{i % 6}") for i in range(60)]
        result = cl.cluster_bootstrap_ci(rows, cl.mean_hit_rate, n_boot=1000)
        self.assertLessEqual(result["ci_low"], result["point_estimate"])
        self.assertGreaterEqual(result["ci_high"], result["point_estimate"])

    def test_refuses_false_precision_with_fewer_than_two_clusters(self):
        rows = [_row(0.5, 1, "only_game"), _row(0.5, 0, "only_game")]
        result = cl.cluster_bootstrap_ci(rows, cl.mean_hit_rate)
        self.assertIsNone(result["ci_low"])
        self.assertIsNone(result["ci_high"])
        self.assertIsNotNone(result["note"])

    def test_empty_input_is_handled_without_raising(self):
        result = cl.cluster_bootstrap_ci([], cl.mean_hit_rate)
        self.assertIsNone(result["point_estimate"])
        self.assertIsNone(result["ci_low"])


class ClusterBootstrapGroupGapDiffCiTest(unittest.TestCase):
    def test_zero_difference_when_groups_are_identically_calibrated(self):
        rows = []
        for i in range(20):
            game = f"g{i % 10}"
            group = i < 10
            rows.append({"predicted": 0.5, "hit": i % 2, "cluster": game, "group": group})
        result = cl.cluster_bootstrap_group_gap_diff_ci(rows, "group", True, False, n_boot=500)
        self.assertAlmostEqual(result["point_estimate"], 0.0, places=6)

    def test_detects_a_real_planted_gap_difference(self):
        rows = []
        for i in range(10):
            game = f"g{i}"
            # Group A (selected): predicted 0.9, always misses -> gap -0.9.
            rows.append({"predicted": 0.9, "hit": 0, "cluster": game, "group": True})
            # Group B (rest): predicted 0.5, hits half the time -> gap ~0.
            rows.append({"predicted": 0.5, "hit": 1 if i % 2 == 0 else 0, "cluster": game, "group": False})
        result = cl.cluster_bootstrap_group_gap_diff_ci(rows, "group", True, False, n_boot=1000)
        self.assertLess(result["point_estimate"], -0.5)
        # A real, planted, large gap with 10 clusters should not straddle 0.
        self.assertLess(result["ci_high"], 0.0)

    def test_undefined_when_one_group_is_empty(self):
        rows = [dict(_row(0.5, 1, "g1"), group=True), dict(_row(0.5, 0, "g2"), group=True)]
        result = cl.cluster_bootstrap_group_gap_diff_ci(rows, "group", True, False)
        self.assertIsNone(result["point_estimate"])
        self.assertIsNotNone(result["note"])

    def test_no_interval_with_fewer_than_two_clusters_in_a_group(self):
        rows = [
            {"predicted": 0.5, "hit": 1, "cluster": "g1", "group": True},
            {"predicted": 0.5, "hit": 0, "cluster": "g1", "group": True},
            {"predicted": 0.5, "hit": 1, "cluster": "g2", "group": False},
            {"predicted": 0.5, "hit": 0, "cluster": "g3", "group": False},
        ]
        result = cl.cluster_bootstrap_group_gap_diff_ci(rows, "group", True, False)
        self.assertIsNotNone(result["point_estimate"])
        self.assertIsNone(result["ci_low"])
        self.assertIsNotNone(result["note"])


if __name__ == "__main__":
    unittest.main()
