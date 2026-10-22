#!/usr/bin/env python3
"""FC-ACC-015 integrity tests (synthetic, no network):
  * tie-averaged top-n wins allocate the boundary tie block fractionally, equal the exact
    count when there are no ties;
  * selection() keeps identical per-date counts, net = challenger - baseline = added - removed;
  * prior_slot uses lineups strictly before the date (same-date lineup never enters);
  * the variant drop sets remove exactly the declared hindsight-risk inputs;
  * a frozen model JSON reproduces the fitted Design/coef predictions exactly."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit as A  # noqa: E402


class Integrity(unittest.TestCase):
    def test_tie_average(self):
        p = np.array([0.9, 0.8, 0.8, 0.8, 0.1])
        y = np.array([1, 1, 0, 0, 1], float)
        idx = np.arange(5)
        w, tied = A.tie_avg_wins(idx, p, y, 2)        # 0.9 + one of three tied 0.8 rows
        self.assertAlmostEqual(w, 1 + 1 / 3)
        self.assertTrue(tied)
        w2, tied2 = A.tie_avg_wins(idx, np.array([0.9, 0.8, 0.7, 0.6, 0.1]), y, 2)
        self.assertEqual(w2, 2.0)
        self.assertFalse(tied2)

    def test_selection_counts_and_net(self):
        rng = np.random.default_rng(1)
        rows = [{"date": d, "player_id": i} for d in ("2025-05-01", "2025-05-02", "2025-05-03") for i in range(30)]
        y = rng.integers(0, 2, len(rows)).astype(float)
        pb, pc = np.round(rng.random(len(rows)), 1), rng.random(len(rows))
        e = A.selection(pb, pc, y, rows, A.frac_count(0.1), np.random.default_rng(2), tie_reps=20)
        self.assertEqual(e["selected_per_arm"], 9)
        self.assertEqual(e["added"], e["removed"])
        self.assertEqual(e["net_wins"], e["added_wins"] - e["removed_wins"])
        self.assertEqual(e["challenger_wins"] - e["baseline_wins"], e["net_wins"])
        f = A.selection(pb, pc, y, rows, A.floor_count(0.95), np.random.default_rng(2))
        self.assertEqual(f["selected_per_arm"], int((pb >= 0.95).sum()))

    def test_player_ci_preserves_equal_counts(self):
        # 40 players added (10 picks, 6 wins each) and 40 removed (10 picks, 6 wins each):
        # identical hit rates, so every resample's equal-N net must be exactly 0.
        contrib = {i: [10, 6, 0, 0] for i in range(40)}
        contrib.update({100 + i: [0, 0, 10, 6] for i in range(40)})
        lo, hi = A.player_ci(contrib, 400, np.random.default_rng(4))
        self.assertAlmostEqual(lo, 0.0, places=9)
        self.assertAlmostEqual(hi, 0.0, places=9)

    def test_prior_slot_strictly_prior(self):
        sched = {"1": {"date": "2025-05-01", "home_lineup": [7] + list(range(100, 108)), "away_lineup": []},
                 "2": {"date": "2025-05-02", "home_lineup": list(range(100, 108)) + [7], "away_lineup": []}}
        rows = [{"date": "2025-05-02", "player_id": 7, "f": {}}, {"date": "2025-05-01", "player_id": 7, "f": {}}]
        A.add_prior_slot(rows, sched)
        self.assertEqual(rows[0]["f"]["prior_slot"], 1.0)   # only the 05-01 slot, not the same-day slot 9
        self.assertIsNone(rows[1]["f"]["prior_slot"])

    def test_variant_drop_sets(self):
        keys = ["wx_temp", "wx_roof", "opp_sp_k", "match_same_hand", "match_switch", "opp_slot",
                "sig_woba_behind", "skill_hit", "park_hit", "opp_team_rpg", "sig_platoon"]
        drop5 = A.BATTER_VARIANTS["V5_SAMEDAY_STRICT"][0]
        self.assertEqual({k for k in keys if drop5(k)},
                         {"wx_temp", "wx_roof", "opp_sp_k", "match_same_hand", "opp_slot", "sig_woba_behind", "sig_platoon"})
        self.assertFalse(any(A.BATTER_VARIANTS["V0_ORIGINAL"][0](k) for k in keys))
        self.assertFalse(A.BATTER_VARIANTS["V6_SAMEDAY_STRICT_NO_BASE"][1])
        self.assertEqual({k for k in ["wx_temp", "match_lineup_k", "match_team_k", "skill_k"]
                          if A.K_VARIANTS["K3_SAMEDAY_STRICT"][0](k)}, {"wx_temp", "match_lineup_k"})

    def test_frozen_model_reproduces(self):
        rng = np.random.default_rng(3)
        rows = [{"f": {"a": float(rng.normal()), "b": (None if i % 7 == 0 else float(rng.normal()))}} for i in range(500)]
        y = (rng.random(500) < 0.5).astype(float)
        D = A.T.Design(rows, ["a", "b"])
        w = A.T.fit_logistic(D.X(rows), y, 1.0)
        fm = A.frozen_model(D, w)
        med, mu, sd, coef = map(np.array, (fm["median"], fm["mu"], fm["sd"], fm["coef"]))
        x = np.array([[np.nan if r["f"][k] is None else r["f"][k] for k in fm["keys"]] for r in rows])
        miss = [fm["keys"].index(k) for k in fm["missing_indicator_for"]]
        z = (np.hstack([np.where(np.isnan(x), med, x), np.isnan(x[:, miss]).astype(float)]) - mu) / sd
        p = 1 / (1 + np.exp(-(np.hstack([np.ones((500, 1)), z]) @ coef)))
        self.assertTrue(np.allclose(p, A.T.sig(D.X(rows) @ w), atol=1e-12))


if __name__ == "__main__":
    unittest.main()
