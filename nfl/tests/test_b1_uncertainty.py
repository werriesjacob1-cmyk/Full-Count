#!/usr/bin/env python3
"""Tests for clustered paired uncertainty on B1-vs-B0 deltas."""
import unittest

from nfl.research import b1_uncertainty as u


class ClusterBootstrap(unittest.TestCase):
    def test_constant_negative_delta_has_constant_negative_ci(self):
        rows = [
            {"week": 1, "delta": -1.0},
            {"week": 1, "delta": -1.0},
            {"week": 2, "delta": -1.0},
            {"week": 3, "delta": -1.0},
        ]
        r = u.cluster_bootstrap_mean_ci(
            rows, cluster_field="week", value_field="delta",
            reps=500, seed=7,
        )
        self.assertEqual(r["n_rows"], 4)
        self.assertEqual(r["n_clusters"], 3)
        self.assertEqual(r["observed_mean"], -1.0)
        self.assertEqual(r["ci_low"], -1.0)
        self.assertEqual(r["ci_high"], -1.0)
        self.assertEqual(r["bootstrap_fraction_below_zero"], 1.0)

    def test_seed_is_deterministic(self):
        rows = [
            {"week": 1, "delta": -2.0},
            {"week": 1, "delta": -1.0},
            {"week": 2, "delta": 2.0},
            {"week": 3, "delta": 0.5},
        ]
        a = u.cluster_bootstrap_mean_ci(
            rows, cluster_field="week", value_field="delta",
            reps=1000, seed=42,
        )
        b = u.cluster_bootstrap_mean_ci(
            rows, cluster_field="week", value_field="delta",
            reps=1000, seed=42,
        )
        self.assertEqual(a, b)

    def test_cluster_resampling_preserves_whole_cluster_rows(self):
        rows = [
            {"week": 1, "delta": -10.0},
            {"week": 1, "delta": -10.0},
            {"week": 2, "delta": 10.0},
        ]
        r = u.cluster_bootstrap_mean_ci(
            rows, cluster_field="week", value_field="delta",
            reps=1000, seed=11,
        )
        self.assertAlmostEqual(r["observed_mean"], -10 / 3)
        self.assertEqual(r["n_clusters"], 2)

    def test_fewer_than_two_clusters_is_not_valid_uncertainty(self):
        with self.assertRaisesRegex(ValueError, "at least two clusters"):
            u.cluster_bootstrap_mean_ci(
                [{"week": 1, "delta": -1.0}],
                cluster_field="week", value_field="delta",
                reps=100, seed=1,
            )

    def test_nonfinite_value_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            u.cluster_bootstrap_mean_ci(
                [
                    {"week": 1, "delta": -1.0},
                    {"week": 2, "delta": float("nan")},
                ],
                cluster_field="week", value_field="delta",
                reps=100, seed=1,
            )


class Percentile(unittest.TestCase):
    def test_percentile_endpoints_and_midpoint(self):
        vals = [0.0, 10.0, 20.0]
        self.assertEqual(u.percentile(vals, 0.0), 0.0)
        self.assertEqual(u.percentile(vals, 1.0), 20.0)
        self.assertEqual(u.percentile(vals, 0.5), 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
