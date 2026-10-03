"""FC-MLB-006 tests (synthetic data; no network, no 2026 outcome reads)."""
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_mlb_006 as W  # noqa: E402

F = W.F


def toy_pa(perturb_0503=False):
    """Batter 1 vs pitcher 7: 05-01 one game; 05-02 a DOUBLEHEADER (games 21, 22); 05-03 one game."""
    spec = [("2025-05-01", 1, "single"), ("2025-05-01", 1, "strikeout"),
            ("2025-05-02", 21, "home_run"), ("2025-05-02", 21, "walk"),           # doubleheader game 1
            ("2025-05-02", 22, "field_out"),                                       # doubleheader game 2
            ("2025-05-03", 3, "strikeout" if perturb_0503 else "double")]
    rows = [dict(season=2025, game_date=d, game_pk=g, at_bat_number=i, batter=1, pitcher=7, stand="R",
                 fld_team="AAA", vs_starter=1, one=1, events=e) for i, (d, g, e) in enumerate(spec)]
    rows.append(dict(season=2025, game_date="2025-04-30", game_pk=0, at_bat_number=99, batter=2, pitcher=8, stand="R",
                     fld_team="BBB", vs_starter=0, one=1, events="hit_by_pitch"))
    pa = pd.DataFrame(rows)
    pa["hit"] = pa["events"].isin(["single", "double", "triple", "home_run"]).astype(int)
    return W.event_table(pa)


TG = pd.DataFrame({"season": 2025, "game_pk": [21, 22, 3, 4], "batter": 1, "pitcher": 7, "stand": "R", "fld_team": "AAA",
                   "game_date": ["2025-05-02", "2025-05-02", "2025-05-03", "2025-05-04"]})


class Events(unittest.TestCase):
    def test_map_exhaustive_and_exclusive(self):
        pa = pd.read_parquet(os.path.join(F.DATA, "pa.parquet"), columns=["events", "hit"])
        e = W.event_table(pa.assign(one=1))
        self.assertTrue((e[W.ECOLS].sum(axis=1) == 1).all())
        self.assertEqual(set(W.EVENT_MAP.values()), set(W.CATS))

    def test_unmapped_event_stops(self):
        with self.assertRaises(SystemExit):
            W.event_table(pd.DataFrame({"events": ["single", "mystery"], "hit": [1, 0]}))


class PointInTime(unittest.TestCase):
    def test_doubleheader_and_same_day_exclusion(self):
        r = W.attach6(TG, W.tables6(toy_pa()), "pitcher").sort_values(["game_date", "game_pk"])
        self.assertEqual(r["b6_one"].tolist(), [2, 2, 5, 6])          # both 05-02 games see only 05-01
        self.assertEqual(r["b6_e_HR"].tolist(), [0, 0, 1, 1])         # game-1 HR invisible to game 2
        self.assertEqual(r["p6_e_K"].tolist(), [1, 1, 1, 1])

    def test_same_day_and_future_perturbation_invariance(self):
        a = W.attach6(TG, W.tables6(toy_pa()), "pitcher").sort_values(["game_date", "game_pk"])
        b = W.attach6(TG, W.tables6(toy_pa(perturb_0503=True)), "pitcher").sort_values(["game_date", "game_pk"])
        cols = [c for c in a.columns if c.startswith(("b6_", "p6_", "l6_"))]
        pd.testing.assert_frame_equal(a[cols].iloc[:3].reset_index(drop=True), b[cols].iloc[:3].reset_index(drop=True))
        self.assertNotEqual(a["b6_e_K"].iloc[3], b["b6_e_K"].iloc[3])


class Softmax(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(0)
        self.n = 400
        self.Lb, self.Lp = rng.normal(0, .3, (self.n, W.NC)), rng.normal(0, .3, (self.n, W.NC))
        self.sh = rng.integers(0, 2, self.n).astype(float)
        self.theta = rng.normal(0, .5, 4 * W.NC)
        self.Y = np.eye(8)[rng.integers(0, 8, self.n)]

    def test_sum_to_one_and_range(self):
        Q = W.probs(self.theta, self.Lb * 50, self.Lp * 50, self.sh)
        self.assertLess(np.abs(Q.sum(1) - 1).max(), 1e-12)
        self.assertTrue(((Q >= 0) & (Q <= 1)).all() and np.isfinite(Q).all())

    def test_analytic_gradient(self):
        f0, g = W.nll_grad(self.theta, self.Lb, self.Lp, self.sh, self.Y)
        eps = 1e-6
        num = np.array([(W.nll_grad(self.theta + eps * e, self.Lb, self.Lp, self.sh, self.Y)[0]
                         - W.nll_grad(self.theta - eps * e, self.Lb, self.Lp, self.sh, self.Y)[0]) / (2 * eps)
                        for e in np.eye(len(self.theta))])
        np.testing.assert_allclose(g, num, atol=1e-7)

    def test_generalized_log5_identity(self):
        L = np.array([.15, .045, .004, .03, .08, .01, .22, .461])
        rb = np.array([.18, .05, .002, .04, .06, .01, .15, .508])
        rp = np.array([.13, .04, .004, .025, .10, .01, .28, .411])
        lo = lambda r: np.log(r[:7] / r[7]) - np.log(L[:7] / L[7])            # noqa: E731
        th = np.concatenate([np.log(L[:7] / L[7]), np.ones(7), np.ones(7), np.zeros(7)])
        q = W.probs(th, lo(rb)[None], lo(rp)[None], np.zeros(1))[0]
        expect = rb * rp / L
        np.testing.assert_allclose(q, expect / expect.sum(), rtol=1e-12)

    def test_fit_recovers_parameters(self):
        rng = np.random.default_rng(1)
        n = 40000
        Lb, Lp = rng.normal(0, .4, (n, W.NC)), rng.normal(0, .4, (n, W.NC))
        sh = rng.integers(0, 2, n).astype(float)
        true = np.concatenate([rng.normal(-1, .3, W.NC), rng.uniform(.5, 1.2, W.NC), rng.uniform(.3, 1, W.NC),
                               rng.normal(0, .1, W.NC)])
        Q = W.probs(true, Lb, Lp, sh)
        Y = np.eye(8)[(Q.cumsum(1) > rng.random(n)[:, None]).argmax(1)]
        est, conv = W.fit_softmax(Lb, Lp, sh, Y, Y.mean(0))
        self.assertLess(conv["max_abs_grad"], 1e-5)
        np.testing.assert_allclose(est, true, atol=0.12)

    def test_theta_roundtrip(self):
        np.testing.assert_array_equal(W.theta_from(W.theta_dict(self.theta)), self.theta)


class PriorAndAggregation(unittest.TestCase):
    def test_mom_recovers_beta_binomial_k(self):
        rng = np.random.default_rng(2)
        N = rng.integers(200, 700, 3000).astype(float)
        counts = np.zeros((3000, 8))
        for j, (p, k) in enumerate([(.15, 300), (.05, 600), (.004, 2000), (.03, 400), (.08, 150), (.01, 1000), (.22, 80), (.456, 4999)]):
            counts[:, j] = rng.binomial(N.astype(int), rng.beta(p * k, (1 - p) * k, 3000))
        est = W.mom_k(counts, N)
        for c, k in (("1B", 300), ("BB", 150), ("K", 80)):
            self.assertLess(abs(np.log(est[c] / k)), 0.35, (c, est[c], k))
        self.assertTrue(all(W.K_MIN <= v <= W.K_MAX for v in est.values()))

    def test_aggregation_equals_champion_binomial(self):
        sys.path.insert(0, F.CHAMP)
        import prop_probability as pp
        h, n, lg = np.array([.22, .25, .3]), np.array([3.9, 4.62, 4.0]), np.array([.6, .6, .6])
        raw, blended = W.aggregate(h, n, lg, pp)
        np.testing.assert_allclose(raw, [pp.p_at_least_hits(1, {0: 1 - a, 1: a}, b) for a, b in zip(h, n)])
        self.assertAlmostEqual(raw[2], 1 - (1 - .3) ** 4)
        lo, hi = 1 - (1 - .25) ** 4, 1 - (1 - .25) ** 5
        self.assertAlmostEqual(raw[1], .38 * lo + .62 * hi)
        np.testing.assert_allclose(blended, .5 * lg + .5 * raw)


class Gate(unittest.TestCase):
    def test_fidelity_gate_refuses(self):
        ref = pd.DataFrame({"game_pk": [1, 2], "batter": [10, 20], "CH0": [0.6, 0.7], "P0": [0.61, 0.71]})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.csv.gz")
            ref.to_csv(path, index=False, compression="gzip")
            sha = F.sha256_file(path)
            ok = ref.rename(columns={"P0": "D0"})
            self.assertEqual(W.M5.fidelity_gate(ok, path, sha)["rows"], 2)
            for bad in (ok.assign(D0=[0.61, 0.72]), ok.iloc[:1]):
                with self.assertRaises(SystemExit):
                    W.M5.fidelity_gate(bad, path, sha)

    def test_verdict_rule(self):
        v = W.verdict_of
        self.assertEqual(v({"diff_pp": 1.2, "ci95_pp": [0.1, 2.0]}), "IMPROVES")
        self.assertEqual(v({"diff_pp": 0.3, "ci95_pp": [-1.0, 1.5]}), "NO_GAIN")
        self.assertEqual(v({"diff_pp": -1.5, "ci95_pp": [-2.5, -0.1]}), "WORSE")
        self.assertEqual(v({"diff_pp": 0.8, "ci95_pp": [-0.5, 2.0]}), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
