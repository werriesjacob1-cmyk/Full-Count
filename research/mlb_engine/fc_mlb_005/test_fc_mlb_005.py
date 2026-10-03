"""FC-MLB-005 focused point-in-time tests (synthetic data; no network, no 2026 outcome reads)."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_mlb_005 as M  # noqa: E402

X, F = M.X, M.F


def toy_pa4(hit_on_0503=1, xba_on_0503=0.5):
    """Team AAA fields: 05-01 one game; 05-02 a DOUBLEHEADER (games 21, 22); 05-03 one game."""
    spec = [("2025-05-01", 1, 0.3, 1), ("2025-05-01", 1, 0.2, 0),
            ("2025-05-02", 21, 0.9, 1), ("2025-05-02", 21, 0.8, 1),       # doubleheader game 1
            ("2025-05-02", 22, 0.1, 0),                                    # doubleheader game 2
            ("2025-05-03", 3, xba_on_0503, hit_on_0503)]
    rows = [dict(season=2025, game_date=d, game_pk=g, at_bat_number=i, pitcher=7, stand="R", fld_team="AAA",
                 vs_starter=1, one=1, hit=h, type="X", xba=x) for i, (d, g, x, h) in enumerate(spec)]
    rows.append(dict(season=2025, game_date="2025-04-30", game_pk=0, at_bat_number=99, pitcher=8, stand="R",
                     fld_team="BBB", vs_starter=1, one=1, hit=0, type="X", xba=0.3))   # league history
    pa = pd.DataFrame(rows)
    return X.expected_hits(pa.drop(columns=["type", "xba"]), pa[["game_pk", "at_bat_number", "type", "xba"]])


def dteam_at(pa4, targets):
    T4 = X.tables4(pa4)
    r = X.attach4(targets.assign(season=2025, pitcher=7, stand="R", fld_team="AAA"), T4, "pitcher")
    return r, np.asarray(X.dteam(r, M.KD))


TG = pd.DataFrame({"game_pk": [21, 22, 3, 4], "game_date": ["2025-05-02", "2025-05-02", "2025-05-03", "2025-05-04"]})


class PointInTime(unittest.TestCase):
    def test_doubleheader_game2_does_not_see_game1(self):
        r, d = dteam_at(toy_pa4(), TG)
        self.assertEqual(r["d_bip"].tolist(), [2, 2, 5, 6])           # 05-02 games both see only 05-01
        self.assertEqual(d[0], d[1])
        np.testing.assert_allclose(r["d_h_bip"].tolist(), [1, 1, 3, 4])

    def test_same_day_and_future_outcomes_do_not_move_features(self):
        _, base = dteam_at(toy_pa4(), TG)
        _, pert = dteam_at(toy_pa4(hit_on_0503=0, xba_on_0503=0.01), TG)
        np.testing.assert_allclose(base[:3], pert[:3])               # through 05-03 unchanged by 05-03 outcomes
        self.assertNotAlmostEqual(base[3], pert[3])                   # 05-04 does see 05-03

    def test_no_history_is_league_neutral_shrinkage(self):
        r, d = dteam_at(toy_pa4(), pd.DataFrame({"game_pk": [1], "game_date": ["2025-05-01"]}))
        self.assertEqual(int(r["d_bip"].iloc[0]), 0)
        lb = r["c4_h_bip"] / r["c4_bip"]; lx = r["c4_xh_bip"] / r["c4_bip"]
        self.assertAlmostEqual(float(d[0]), float(F.logit(lb.iloc[0]) - F.logit(lx.iloc[0])))


class Design(unittest.TestCase):
    def test_only_change_is_dteam(self):
        self.assertEqual(M.D1_COLS, M.D0_COLS + ["Dteam"])
        self.assertEqual(M.D0_COLS, ["Lb", "Lp", "same_hand"])

    def test_kd_reused_from_fc_mlb_004(self):
        with open(M.F004_FIT) as fh:
            self.assertEqual(json.load(fh)["kd"], M.KD)
        self.assertEqual(F.sha256_file(M.F004_FIT), M.F004_FIT_SHA)

    def test_home_team_is_first_pa_fielder(self):
        pa = pd.DataFrame({"game_pk": [5, 5, 5], "at_bat_number": [3, 1, 2], "fld_team": ["AWY", "HOM", "HOM"]})
        self.assertEqual(M.home_teams(pa), {5: "HOM"})

    def test_verdict_rule(self):
        self.assertEqual(M.verdict_of({"diff_pp": 1.2, "ci95_pp": [0.1, 2.0]}), "IMPROVES")
        self.assertEqual(M.verdict_of({"diff_pp": 0.9, "ci95_pp": [0.1, 2.0]}), "INCONCLUSIVE")
        self.assertEqual(M.verdict_of({"diff_pp": 0.3, "ci95_pp": [-1.0, 1.5]}), "NO_GAIN")
        self.assertEqual(M.verdict_of({"diff_pp": -1.5, "ci95_pp": [-2.5, -0.1]}), "WORSE")
        self.assertEqual(M.verdict_of({"diff_pp": 0.8, "ci95_pp": [-0.5, 2.0]}), "INCONCLUSIVE")


class FidelityGate(unittest.TestCase):
    def test_gate(self):
        ref = pd.DataFrame({"game_pk": [1, 2], "batter": [10, 20], "CH0": [0.6, 0.7], "P0": [0.61, 0.71]})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.csv.gz")
            ref.to_csv(path, index=False, compression="gzip")
            sha = F.sha256_file(path)
            ok = ref.rename(columns={"P0": "D0"})
            self.assertEqual(M.fidelity_gate(ok, path, sha)["rows"], 2)
            for bad in (ok.assign(D0=[0.61, 0.72]), ok.iloc[:1], ok.assign(CH0=[0.6, 0.70001])):
                with self.assertRaises(SystemExit):
                    M.fidelity_gate(bad, path, sha)
            with self.assertRaises(SystemExit):
                M.fidelity_gate(ok, path, "0" * 64)


if __name__ == "__main__":
    unittest.main()
