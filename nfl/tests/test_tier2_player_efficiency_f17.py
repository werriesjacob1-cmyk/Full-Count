"""F17 advanced receiving efficiency: adversarial known-answer tests."""
import csv
import gzip
import random
import tempfile
import unittest
from pathlib import Path

from nfl.research.tier1 import harness as H
from nfl.research.tier2 import player_efficiency_f17 as M
from nfl.research.tier2 import player_efficiency_f17_data as D


def wk(pid, season, week, targets, yards, rec=None, stype="REG"):
    return {"player_id": pid, "season": season, "week": week, "season_type": stype, "game_id": f"{season}_{week:02d}_{pid}",
            "team": "A", "opponent_team": "B", "position": "WR", "targets": targets,
            "receptions": targets if rec is None else rec, "receiving_yards": yards}


def game(pid, season, week, T, Y, TX=None, YX=None, XY=None, stype="REG"):
    TX = T if TX is None else TX
    return {"player_id": pid, "season": season, "week": week, "season_type": stype, "game_id": f"g{season}{week}",
            "T": T, "Y": Y, "TX": TX, "YX": Y if YX is None else YX, "XY": 7.0 * TX if XY is None else XY,
            "CX": 0.6 * TX, "CP": 0.6 * TX, "YOE_C": 0.0, "NC": 0.6 * TX}


class B0Tests(unittest.TestCase):
    def test_b0_matches_harness_rule_on_role_rows(self):
        rng = random.Random(7)
        rows = []
        for pid in ("p1", "p2", "p3"):
            for s in (2020, 2021):
                for w in range(1, 18):
                    t = rng.choice([0, 0, 1, 3, 5, 8])
                    rows.append(wk(pid, s, w, t, float(rng.randint(-3, 90)) if t else 0.0))
        rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"], r["player_id"]))
        idx = M.B0Index(rows)
        n = 0
        for s in H.b0_rolling_mean(rows, "receiving_yards"):
            st = idx.state(s["player_id"], s["season"], s["week"])
            if s["b0"] is None:
                self.assertIsNone(st)
            else:
                self.assertAlmostEqual(st["b0"], s["b0"])
                self.assertAlmostEqual(st["tgt5"] * st["ypt5"], st["b0"])   # exact factorisation
                n += 1
        self.assertGreater(n, 50)

    def test_b0_ignores_same_and_later_weeks(self):
        rows = [wk("p", 2021, w, 5, 50.0) for w in (1, 2, 3)] + [wk("p", 2021, 4, 10, 500.0)]
        self.assertAlmostEqual(M.B0Index(rows).state("p", 2021, 4)["b0"], 50.0)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        g = [game("x", 2020, w, 100, 1000.0, XY=700.0) for w in (1,)]            # position prior season
        g += [game("p", 2021, w, 6, 60.0, XY=42.0) for w in range(1, 10)]           # 10 ypt, 7 expected
        self.pos = {"x": "WR", "p": "WR"}
        self.idx = M.EfficiencyIndex(g, self.pos)

    def test_strictly_prior_and_prior_season_position_mean(self):
        f = self.idx.features("p", "WR", 2021, 5)
        leaked = M.EfficiencyIndex([game("x", 2020, 1, 100, 1000.0, XY=700.0)]
                                   + [game("p", 2021, w, 6, 60.0, XY=42.0) for w in range(1, 10)]
                                   + [game("p", 2021, 5, 50, 5000.0, XY=10.0)], self.pos)   # same-week game
        self.assertEqual(f, leaked.features("p", "WR", 2021, 5))
        self.assertAlmostEqual(f["position_prior"]["ypt"], 10.0)                 # 2020 only
        self.assertEqual(self.idx.features("p", "WR", 2020, 5)["status"], "NO_POSITION_PRIOR")

    def test_skill_and_depth_known_answer(self):
        f = self.idx.features("p", "WR", 2021, 5)                               # 4 prior games, 24 targets
        self.assertAlmostEqual(f["skill"], (240.0 - 168.0) / (24 + M.K_S))
        self.assertAlmostEqual(f["xypt_recent"], (168.0 + M.K_X * 7.0) / (24 + M.K_X))
        self.assertAlmostEqual(f["ypt_simple"], (240.0 + M.K_S * 10.0) / (24 + M.K_S))

    def test_old_seasons_outside_window_ignored(self):
        idx = M.EfficiencyIndex([game("x", 2020, 1, 100, 1000.0, XY=700.0), game("p", 2017, 1, 50, 900.0)], self.pos)
        self.assertEqual(idx.features("p", "WR", 2021, 1)["status"], "NO_PRIOR_TARGETS")


class PredictTests(unittest.TestCase):
    def test_w0_is_exactly_scale_control_and_fallbacks(self):
        b = {"b0": 50.0, "tgt5": 5.0, "ypt5": 10.0}
        f = {"status": "OK", "xypt_recent": 8.0, "skill": 0.5, "ypt_simple": 9.0}
        for mode in M.MODES:
            self.assertAlmostEqual(M.predict(b, f, mode, 0.8, 0.0)[0], 0.8 * 50.0)
        self.assertAlmostEqual(M.predict(b, f, "F17_FULL", 0.8, 1.0)[0], 0.8 * 5.0 * 8.5)
        self.assertEqual(M.predict(b, {"status": "NO_PRIOR_TARGETS"}, "F17_FULL", 0.8, 1.0), (40.0, "NO_PRIOR_TARGETS"))
        self.assertEqual(M.predict({**b, "ypt5": None}, f, "F17_FULL", 0.8, 1.0), (40.0, "B0_WINDOW_HAS_NO_TARGETS"))


class DataTests(unittest.TestCase):
    def test_parse_exclusions_and_expectation_like_for_like(self):
        cols = ["game_id", "season", "week", "season_type", "pass_attempt", "sack", "qb_spike", "two_point_attempt",
                "receiver_player_id", "complete_pass", "yards_gained", "cp", "air_yards", "xyac_mean_yardage",
                "yards_after_catch"]
        plays = [["g", 2024, 1, "REG", 1, 0, 0, 0, "r", 1, 20, 0.5, 10, 4, 10],     # counted, expected 7
                 ["g", 2024, 1, "REG", 1, 0, 0, 0, "r", 0, 0, 0.5, 10, 4, "NA"],     # incomplete -> 0 yards
                 ["g", 2024, 1, "REG", 1, 0, 0, 0, "r", 1, 5, "NA", 2, "NA", 3],     # no expectation: T/Y only
                 ["g", 2024, 1, "REG", 1, 1, 0, 0, "r", 0, -7, 0.5, 0, 0, "NA"],     # sack
                 ["g", 2024, 1, "REG", 1, 0, 1, 0, "r", 0, 0, 0.5, 0, 0, "NA"],      # spike
                 ["g", 2024, 1, "REG", 1, 0, 0, 1, "r", 1, 2, 0.5, 2, 0, 0]]         # two-point try
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pbp.csv.gz"
            with gzip.open(p, "wt", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                w.writerows(plays)
            (g,) = D.game_targets(p)
        self.assertEqual((g["T"], g["Y"], g["TX"], g["YX"]), (3, 25.0, 2, 20.0))
        self.assertAlmostEqual(g["XY"], 14.0)
        self.assertAlmostEqual(g["YOE_C"], 6.0)

    def test_snap_population_filters(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap_counts_2024.csv"
            with p.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["game_id", "season", "game_type", "week", "pfr_player_id", "position", "team", "opponent",
                            "offense_snaps"])
                w.writerow(["g1", 2024, "REG", 1, "AbcX00", "WR", "A", "B", 30])
                w.writerow(["g1", 2024, "REG", 1, "ZzzZ00", "WR", "A", "B", 30])   # unmapped
                w.writerow(["g1", 2024, "REG", 1, "DefY00", "TE", "A", "B", 0])    # no offensive snap
                w.writerow(["g2", 2024, "POST", 19, "AbcX00", "WR", "A", "B", 30])  # postseason
                w.writerow(["g1", 2024, "REG", 1, "QbbB00", "QB", "A", "B", 60])   # not a receiver position
            rows, diag = D.snap_population(Path(d), [2024], {"AbcX00": "00-1", "DefY00": "00-2", "QbbB00": "00-3"})
        self.assertEqual([r["player_id"] for r in rows], ["00-1"])
        self.assertEqual(diag, {"rows": 1, "unmapped_pfr_id": 1, "zero_offense_snaps": 1})


if __name__ == "__main__":
    unittest.main()
