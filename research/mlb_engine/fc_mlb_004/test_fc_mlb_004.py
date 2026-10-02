"""FC-MLB-004 unit tests (synthetic data only; no network, no 2026 outcome reads)."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_mlb_004 as X  # noqa: E402


def toy_pa():
    rows = []
    for ab, (d, pit, stand, typ, xba, hit, fld) in enumerate([
            ("2025-05-01", 1, "R", "X", 0.9, 1, "AAA"),     # BIP with xBA
            ("2025-05-01", 1, "R", "S", None, 0, "AAA"),    # strikeout: xH 0
            ("2025-05-01", 1, "R", "X", None, 1, "AAA"),    # BIP missing xBA: realized hit
            ("2025-05-02", 1, "R", "B", None, 0, "AAA"),    # walk: xH 0
            ("2025-05-02", 1, "R", "X", 0.1, 0, "AAA"),
            ("2025-05-03", 1, "R", "X", 0.5, 1, "AAA")]):
        rows.append(dict(season=2025, game_date=d, game_pk=int(d[-1]), at_bat_number=ab, pitcher=pit, stand=stand,
                         fld_team=fld, vs_starter=1, hit=hit, one=1, _type=typ, _xba=xba))
    pa = pd.DataFrame(rows)
    contact = pa[["game_pk", "at_bat_number", "_type", "_xba"]].rename(columns={"_type": "type", "_xba": "xba"})
    return pa.drop(columns=["_type", "_xba"]), contact


class ExpectedHits(unittest.TestCase):
    def test_xh_rules(self):
        pa, c = toy_pa()
        p = X.expected_hits(pa, c)
        self.assertEqual(p["bip"].tolist(), [1, 0, 1, 0, 1, 1])
        np.testing.assert_allclose(p["xh"], [0.9, 0.0, 1.0, 0.0, 0.1, 0.5])
        np.testing.assert_allclose(p["xh_bip"], p["xh"])          # non-BIP rows contribute 0
        self.assertEqual(p["h_bip"].tolist(), [1, 0, 1, 0, 0, 1])

    def test_point_in_time(self):
        pa, c = toy_pa()
        T4 = X.tables4(X.expected_hits(pa, c))
        tgt = pd.DataFrame({"season": [2025] * 3, "game_date": ["2025-05-01", "2025-05-02", "2025-05-03"],
                            "pitcher": [1] * 3, "stand": ["R"] * 3, "fld_team": ["AAA"] * 3})
        r = X.attach4(tgt, T4, "pitcher").sort_values("game_date")
        self.assertEqual(r["px_one"].tolist(), [0, 3, 5])             # strictly before the game date
        np.testing.assert_allclose(r["px_xh"], [0.0, 1.9, 2.0])
        self.assertEqual(r["d_bip"].tolist(), [0, 2, 3])

    def test_lpx_shrinks_to_league(self):
        r = pd.DataFrame({"c4_xh": [200.0], "c4_one": [1000.0], "px_xh": [0.0], "px_one": [0.0]})
        self.assertAlmostEqual(float(np.asarray(X.lpx(r, 150))[0]), float(X.F.logit(0.2)))
        r2 = r.assign(px_xh=[30.0], px_one=[100.0])
        v = float(X.F.expit(np.asarray(X.lpx(r2, 100))[0]))
        self.assertAlmostEqual(v, (30 + 100 * 0.2) / 200)

    def test_dteam_zero_without_history_when_league_neutral(self):
        r = pd.DataFrame({"c4_h_bip": [30.0], "c4_xh_bip": [30.0], "c4_bip": [100.0],
                          "d_h_bip": [0.0], "d_xh_bip": [0.0], "d_bip": [0.0]})
        self.assertAlmostEqual(float(np.asarray(X.dteam(r, 500))[0]), 0.0)
        good_defense = r.assign(d_h_bip=[200.0], d_xh_bip=[300.0], d_bip=[1000.0])
        self.assertLess(float(np.asarray(X.dteam(good_defense, 500))[0]), 0.0)


class FidelityGate(unittest.TestCase):
    def test_gate_refuses_mismatch(self):
        import tempfile
        ref = pd.DataFrame({"game_pk": [1, 2], "batter": [10, 20], "CH0": [0.6, 0.7], "CH1a": [0.61, 0.71]})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.csv.gz")
            ref.to_csv(path, index=False, compression="gzip")
            old = X.F002_PRED_SHA
            X.F002_PRED_SHA = X.F.sha256_file(path)
            try:
                ok = ref.rename(columns={"CH1a": "P0"})
                self.assertEqual(X.fidelity_gate(ok, path)["rows"], 2)
                with self.assertRaises(SystemExit):
                    X.fidelity_gate(ok.assign(P0=[0.61, 0.72]), path)
                with self.assertRaises(SystemExit):
                    X.fidelity_gate(ok.iloc[:1], path)
            finally:
                X.F002_PRED_SHA = old


if __name__ == "__main__":
    unittest.main()
