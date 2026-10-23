#!/usr/bin/env python3
"""FC-ACC-019 tests: odds bins, the per-row no-vig rule (FC-ACC-017/018 double-devig regression),
per-market grading, ranking constraints and the diagnostic control."""
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analysis as A  # noqa: E402
import candidates as C  # noqa: E402


class Bins(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(A.odds_bin(200), "L+ (+200 or longer)")
        self.assertEqual(A.odds_bin(199), "P (+100..+199)")
        self.assertEqual(A.odds_bin(100), "P (+100..+199)")
        self.assertEqual(A.odds_bin(-100), "E (-100..-149)")
        self.assertEqual(A.odds_bin(-149), "E (-100..-149)")
        self.assertEqual(A.odds_bin(-150), "M (-150..-199)")
        self.assertEqual(A.odds_bin(-299), "F (-200..-299)")
        self.assertEqual(A.odds_bin(-300), "X (-300 or shorter)")


class NoVig(unittest.TestCase):
    def test_raw_market_implied_is_devigged_once(self):
        nv, basis = C.no_vig({"market_odds": -150, "market_implied": 0.6})
        self.assertAlmostEqual(nv, 0.6 / 1.08)
        self.assertEqual(basis, "assumed_hold")

    def test_recorded_two_sided_fair_is_not_devigged_again(self):
        # regression: FC-ACC-017/018 divided an already-fair two-sided value by 1.08
        nv, basis = C.no_vig({"market_odds": -118, "market_implied": 0.5061, "market_fair_method": "exact_two_sided"})
        self.assertAlmostEqual(nv, 0.5061)
        self.assertNotEqual(basis, "assumed_hold")
        nv2, _ = C.no_vig({"market_odds": -120, "market_implied": 0.5146})   # fair recorded without a method field
        self.assertAlmostEqual(nv2, 0.5146)


class Grading(unittest.TestCase):
    def test_markets(self):
        bat = {(1, 10): {"plateAppearances": 4, "hits": 2, "doubles": 1, "triples": 0, "homeRuns": 1, "runs": 1, "rbi": 2,
                         "totalBases": 6, "stolenBases": 0}}
        pit = {(1, 20): {"gamesStarted": 1, "strikeOuts": 6, "outs": 17}, (1, 21): {"gamesStarted": 0}}
        g = lambda stat, pid, n: A.grade({"game_pk": 1, "player_id": pid, "stat": stat, "needs": n}, bat, pit)  # noqa: E731
        self.assertEqual(g("singles", 10, 1), 0.0)              # 2 hits = 1 double + 1 HR -> 0 singles
        self.assertEqual(g("total_bases", 10, 2), 1.0)
        self.assertEqual(g("hits_runs_rbis", 10, 5), 1.0)
        self.assertEqual(g("stolen_base", 10, 1), 0.0)
        self.assertEqual(g("strikeouts", 20, 6), 1.0)
        self.assertEqual(g("pitcher_outs", 20, 18), 0.0)
        self.assertIsNone(g("strikeouts", 21, 4))
        self.assertIsNone(g("hits", 11, 1))


class Ranking(unittest.TestCase):
    def test_constraints_and_counts(self):
        rows = [{"slate": "2026-09-01", "game_pk": i // 4, "player_id": i // 2, "id": str(i), "american": -150,
                 "raw_implied": 0.6, "no_vig": 0.55 - i / 1000, "model": 0.6, "stat": "hits", "no_vig_basis": "assumed_hold", "y": float(i % 2)} for i in range(16)]
        r = A.ranking(rows, np.random.default_rng(1), k=3)
        for v in r.values():
            self.assertEqual(v["n"] + v["shortfall"], 3)


class Control(unittest.TestCase):
    def test_fit_recovers_coefficients(self):
        rng = np.random.default_rng(2)
        x1, x2 = rng.normal(size=20000), rng.normal(size=20000)
        y = (rng.random(20000) < 1 / (1 + np.exp(-(0.2 + 1.0 * x1 + 0.5 * x2)))).astype(float)
        w = A.fit(np.column_stack([np.ones_like(x1), x1, x2]), y)
        self.assertAlmostEqual(w[1], 1.0, delta=0.06)
        self.assertAlmostEqual(w[2], 0.5, delta=0.06)


if __name__ == "__main__":
    unittest.main()
