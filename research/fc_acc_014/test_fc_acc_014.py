#!/usr/bin/env python3
"""FC-ACC-014 integrity tests (synthetic, no network):
  * Prefix.query is strictly prior: same-date and later rows never enter a feature;
  * window queries respect [after, before);
  * Design imputes with TRAIN medians only (test values never move the imputation or scaling);
  * equal_n keeps identical per-date counts for both arms and its net = added wins - removed wins;
  * the logistic fitter recovers a known coefficient."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FT  # noqa: E402
import tournament as T  # noqa: E402


class Integrity(unittest.TestCase):
    def test_prefix_is_strictly_prior(self):
        p = FT.Prefix(["x"])
        for o, v in ((10, 1), (11, 2), (11, 4), (12, 8)):
            p.add("k", o, [v])
        p.freeze()
        self.assertEqual(p.query("k", 11)[0], 1)          # the same-date rows (ord 11) are excluded
        self.assertEqual(p.query("k", 12)[0], 7)
        self.assertEqual(p.query("k", 13, 11)[0], 14)     # window [11, 13)
        self.assertEqual(p.query("missing", 99)[0], 0)

    def test_design_uses_train_statistics_only(self):
        tr = [{"f": {"a": 1.0, "b": None}}, {"f": {"a": 3.0, "b": 2.0}}]
        te = [{"f": {"a": 1000.0, "b": None}}]
        D = T.Design(tr, ["a", "b"])
        self.assertEqual(D.med.tolist(), [2.0, 2.0])
        X1 = D.X(te)
        D2 = T.Design(tr, ["a", "b"])                     # unaffected by having transformed test rows
        self.assertTrue(np.allclose(D2.mu, D.mu) and np.allclose(D2.sd, D.sd))
        self.assertEqual(X1.shape[1], 1 + 2 + 1)          # intercept, a, b, b__missing

    def test_equal_n_counts_and_net(self):
        rows = [{"date": d, "player_id": i} for d in ("2025-05-01", "2025-05-02") for i in range(20)]
        rng = np.random.default_rng(1)
        y = rng.integers(0, 2, len(rows)).astype(float)
        pb, pc = rng.random(len(rows)), rng.random(len(rows))
        e = T.equal_n(pb, pc, y, rows, 0.10, np.random.default_rng(2))
        self.assertEqual(e["selected_per_arm"], 4)
        self.assertEqual(e["added"], e["removed"])
        self.assertEqual(e["net_wins"], e["added_wins"] - e["removed_wins"])
        self.assertEqual(e["challenger_wins"] - e["baseline_wins"], e["net_wins"])

    def test_logistic_recovers_coefficient(self):
        rng = np.random.default_rng(3)
        x = rng.normal(size=20000)
        y = (rng.random(20000) < T.sig(0.5 + 1.2 * x)).astype(float)
        w = T.fit_logistic(np.column_stack([np.ones_like(x), x]), y)
        self.assertAlmostEqual(w[1], 1.2, delta=0.06)
        self.assertAlmostEqual(w[0], 0.5, delta=0.05)


if __name__ == "__main__":
    unittest.main()
