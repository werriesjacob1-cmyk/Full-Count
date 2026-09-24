"""Synthetic-fixture tests for the estimand code in analysis.py (known answers).

Run: python3 -m unittest engineering/mlb_selection_overconfidence_20260924/test_analysis.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analysis as A  # noqa: E402


def row(market, p, y, game="g1", date="2026-09-01", pid="p1"):
    return {"market": market, "p": p, "y": y, "game_pk": game, "date": date, "player_id": pid}


def rows_with_rate(market, p, n, n_hit, game_prefix="g", date="2026-09-01"):
    return [row(market, p, 1 if i < n_hit else 0, game=f"{game_prefix}{i}", date=date)
            for i in range(n)]


class TestBands(unittest.TestCase):
    def test_band_edges(self):
        self.assertEqual(A.band_of(0.60), "[0.60,0.65)")
        self.assertEqual(A.band_of(0.5999), "[0.45,0.60)")
        self.assertEqual(A.band_of(0.75), "[0.75,1.00]")
        self.assertEqual(A.band_of(1.0), "[0.75,1.00]")
        self.assertEqual(A.band_of(0.0), "[0.00,0.30)")
        self.assertIsNone(A.band_of(None))


class TestDecompose(unittest.TestCase):
    def test_known_answer(self):
        # Reference: 20 rows at p=0.62, 10 hits -> g_ref = 0.50 - 0.62 = -0.12
        ref = rows_with_rate("hits", 0.62, 20, 10, "r")
        # Selected: 10 rows at p=0.62, 3 hits -> G = 0.30 - 0.62 = -0.32
        sel = rows_with_rate("hits", 0.62, 10, 3, "s")
        asg = A.assign_cells(sel, ref, min_ref=15)
        self.assertTrue(all(a == ("mb", "hits", "[0.60,0.65)") for a in asg))
        d = A.decompose(sel, ref, asg)
        self.assertEqual(d["n"], 10)
        self.assertAlmostEqual(d["G"], -0.32)
        self.assertAlmostEqual(d["W"], -0.12)
        self.assertAlmostEqual(d["S_sel"], -0.20)

    def test_no_selection_effect_gives_zero(self):
        ref = rows_with_rate("strikeouts", 0.70, 40, 28, "r")   # perfectly calibrated
        sel = rows_with_rate("strikeouts", 0.70, 20, 14, "s")   # same rate
        d = A.decompose(sel, ref, A.assign_cells(sel, ref))
        self.assertAlmostEqual(d["W"], 0.0)
        self.assertAlmostEqual(d["S_sel"], 0.0)

    def test_within_band_p_adjustment(self):
        # Reference at p=0.61 hitting 0.61 (calibrated). Selected at p=0.64
        # hitting 0.64 -> no shortfall beyond the curve even though the raw
        # selected rate differs from the reference rate.
        ref = rows_with_rate("hits", 0.61, 100, 61, "r")
        sel = rows_with_rate("hits", 0.64, 100, 64, "s")
        d = A.decompose(sel, ref, A.assign_cells(sel, ref))
        self.assertAlmostEqual(d["S_sel"], 0.0, places=9)

    def test_fallback_levels_and_unmatched(self):
        ref = (rows_with_rate("hits", 0.62, 10, 5, "a") +      # (hits, 0.60-0.65): only 10 -> too few
               rows_with_rate("hits", 0.72, 10, 5, "b") +      # hits p>=0.60 pooled: 20 -> level mh
               rows_with_rate("outs", 0.66, 16, 8, "c"))       # (outs, band) 16 -> level mb
        sel = [row("hits", 0.63, 1), row("outs", 0.67, 0), row("hr", 0.10, 0)]
        asg = A.assign_cells(sel, ref, min_ref=15)
        self.assertEqual(asg[0], ("mh", "hits", "p>=0.60"))
        self.assertEqual(asg[1], ("mb", "outs", "[0.65,0.70)"))
        self.assertIsNone(asg[2])
        # all-markets band fallback
        ref2 = rows_with_rate("x", 0.62, 8, 4, "d") + rows_with_rate("y", 0.61, 8, 4, "e")
        asg2 = A.assign_cells([row("z", 0.64, 1)], ref2, min_ref=15)
        self.assertEqual(asg2[0], ("ab", "*", "[0.60,0.65)"))

    def test_unsettled_rows_excluded(self):
        ref = rows_with_rate("hits", 0.62, 20, 10, "r")
        sel = rows_with_rate("hits", 0.62, 10, 3, "s") + [row("hits", 0.62, None, game="u")]
        d = A.decompose(sel, ref, A.assign_cells(sel, ref))
        self.assertEqual(d["n"], 10)


class TestBootstrap(unittest.TestCase):
    def test_deterministic_and_insufficient(self):
        rows = rows_with_rate("hits", 0.6, 30, 15, "g")
        a = A.cluster_bootstrap(rows, A.gap, A.game_cluster, b=200, seed=1)
        b = A.cluster_bootstrap(rows, A.gap, A.game_cluster, b=200, seed=1)
        self.assertEqual(a["ci95"], b["ci95"])
        one = [row("hits", 0.6, 1, game="g"), row("hits", 0.6, 0, game="g")]
        r = A.cluster_bootstrap(one, A.gap, A.game_cluster, b=50)
        self.assertIsNone(r["ci95"])
        self.assertEqual(r["n_clusters"], 1)

    def test_joint_bootstrap_detects_planted_selection_effect(self):
        # Reference calibrated at 0.65; selected at 0.65 hitting 0.40 over
        # many independent clusters -> S_sel ~ -0.25 with CI below 0, W ~ 0.
        ref = rows_with_rate("hits", 0.65, 400, 260, "r")
        sel = rows_with_rate("hits", 0.65, 200, 80, "s")
        res = A.joint_decompose_bootstrap(sel, ref, A.game_cluster, b=400, seed=7)
        self.assertAlmostEqual(res["point"]["S_sel"], -0.25, places=9)
        self.assertLess(res["ci95"]["S_sel"][1], 0)
        self.assertLess(res["ci95"]["W"][0], 0)
        self.assertGreater(res["ci95"]["W"][1], 0)

    def test_joint_bootstrap_world_model_only(self):
        # Both sets hit 0.50 at p=0.65 -> W=-0.15 (CI below 0), S_sel ~ 0.
        ref = rows_with_rate("hits", 0.65, 400, 200, "r")
        sel = rows_with_rate("hits", 0.65, 200, 100, "s")
        res = A.joint_decompose_bootstrap(sel, ref, A.game_cluster, b=400, seed=7)
        self.assertAlmostEqual(res["point"]["W"], -0.15, places=9)
        self.assertAlmostEqual(res["point"]["S_sel"], 0.0, places=9)
        self.assertLess(res["ci95"]["W"][1], 0)
        self.assertLess(res["ci95"]["S_sel"][0], 0)
        self.assertGreater(res["ci95"]["S_sel"][1], 0)


class TestDecisionRule(unittest.TestCase):
    def _res(self, S, W, s_ci, w_ci):
        return {"point": {"n": 300, "G": S + W, "W": W, "S_sel": S},
                "ci95": {"S_sel": s_ci, "W": w_ci, "G": [0, 0]}}

    def test_h2_over_h1(self):
        prim = self._res(-0.10, -0.01, [-0.15, -0.05], [-0.04, 0.02])
        dbm = self._res(-0.05, 0.0, [-0.2, 0.1], [-0.05, 0.05])
        d = A.decide(prim, dbm, 400, 50, 0.9)
        self.assertTrue(d["H2_supported"])
        self.assertFalse(d["H2_strong"])
        self.assertEqual(d["verdict"], "H2 over H1 (selection-induced)")

    def test_h1_over_h2(self):
        prim = self._res(-0.01, -0.10, [-0.06, 0.04], [-0.14, -0.06])
        dbm = self._res(-0.01, -0.1, [-0.2, 0.1], [-0.2, 0.0])
        d = A.decide(prim, dbm, 400, 50, 0.9)
        self.assertEqual(d["verdict"], "H1 over H2 (world-model error)")

    def test_insufficient_and_coverage(self):
        prim = self._res(-0.10, 0.0, [-0.15, -0.05], [-0.04, 0.02])
        dbm = self._res(-0.05, 0.0, [-0.2, 0.1], [-0.05, 0.05])
        self.assertEqual(A.decide(prim, dbm, 150, 50, 0.9)["verdict"], "inconclusive (insufficient n)")
        self.assertFalse(A.decide(prim, dbm, 400, 50, 0.5)["H2_supported"])

    def test_inconclusive(self):
        prim = self._res(-0.03, -0.03, [-0.10, 0.04], [-0.08, 0.02])
        dbm = self._res(-0.05, 0.0, [-0.2, 0.1], [-0.05, 0.05])
        self.assertEqual(A.decide(prim, dbm, 400, 50, 0.9)["verdict"],
                         "inconclusive (neither mechanism demonstrated)")


if __name__ == "__main__":
    unittest.main()
