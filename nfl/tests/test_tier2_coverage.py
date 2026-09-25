"""Tier 2 F11/F12 coverage research: focused known-answer and integrity tests."""
import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from nfl.research.tier2 import coverage_challenger as C
from nfl.research.tier2 import coverage_data as D
from nfl.research.tier2 import coverage_features as F


def drop(season, defteam, man_zone, players, target=None, complete=False, yards=0.0,
         posteam="A", week=1, season_type="REG", family="COVER_3"):
    return {"season": season, "week": week, "season_type": season_type, "game_id": f"g{season}{week}",
            "play_id": "1", "posteam": posteam, "defteam": defteam, "man_zone": man_zone,
            "family": family, "family_raw": family, "offense_players": players,
            "target": target, "complete": complete, "yards": yards}


POS = {"wr1": "WR", "wr2": "WR", "te1": "TE", "qb": "QB"}


def season_rows(season, n_man=150, n_zone=150, defteam="D"):
    rows = []
    for i in range(n_man):   # wr1 targeted on 20% of man snaps, wr2 on 5%
        tgt = "wr1" if i % 5 == 0 else ("wr2" if i % 20 == 1 else None)
        rows.append(drop(season, defteam, "MAN", ["wr1", "wr2", "te1", "qb"], tgt, True, 10.0))
    for i in range(n_zone):  # wr1 targeted on 10% of zone snaps
        tgt = "wr1" if i % 10 == 0 else None
        rows.append(drop(season, defteam, "ZONE", ["wr1", "wr2", "te1", "qb"], tgt, True, 10.0))
    return rows


class DataLayerTests(unittest.TestCase):
    def test_participation_labels_unknown_other_and_join(self):
        with tempfile.TemporaryDirectory() as d:
            par = Path(d) / "p.csv"
            with par.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["nflverse_game_id", "play_id", "defense_man_zone_type", "defense_coverage_type", "offense_players"])
                w.writerow(["2023_01_A_B", "10", "MAN_COVERAGE", "COVER_1", "p1;p2"])
                w.writerow(["2023_01_A_B", "11", "", "", "p1;p2"])
                w.writerow(["2023_01_A_B", "12", "ZONE_COVERAGE", "COMBO", "p1"])
                w.writerow(["2023_01_A_B", "13", "ZONE_COVERAGE", "COVER_3", "p1"])
            pbp = Path(d) / "pbp.csv.gz"
            cols = ["game_id", "play_id", "week", "season_type", "posteam", "defteam", "qb_dropback",
                    "pass_attempt", "sack", "qb_spike", "two_point_attempt", "receiver_player_id",
                    "complete_pass", "yards_gained"]
            with gzip.open(pbp, "wt", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                w.writerow(["2023_01_A_B", "10.0", "1", "REG", "OAK", "B", "1", "1", "0", "0", "0", "p1", "1", "12"])
                w.writerow(["2023_01_A_B", "11.0", "1", "REG", "OAK", "B", "1", "1", "0", "0", "0", "NA", "0", "0"])
                w.writerow(["2023_01_A_B", "12.0", "1", "REG", "OAK", "B", "1", "1", "1", "0", "0", "", "0", "-7"])  # sack
                w.writerow(["2023_01_A_B", "13.0", "1", "REG", "OAK", "B", "1", "1", "0", "0", "1", "p1", "1", "2"])  # 2pt
            rows = D.build_dropbacks(2023, pbp, par)
        self.assertEqual([r["play_id"] for r in rows], ["10", "11"])
        self.assertEqual(rows[0]["man_zone"], "MAN")
        self.assertEqual(rows[0]["posteam"], "LV")          # relocation alias
        self.assertEqual(rows[1]["man_zone"], D.UNKNOWN)    # blank label stays UNKNOWN
        self.assertIsNone(rows[1]["target"])
        self.assertEqual(D.FAMILIES.get("COMBO", D.OTHER_FAMILY), D.OTHER_FAMILY)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        rows = season_rows(2020) + season_rows(2021)
        self.tables = F.SeasonTables(rows, POS)
        self.avail = {2020, 2021, 2022}

    def test_denominator_is_onfield_not_targets(self):
        cell = self.tables.player[(2021, "wr2")]["ZONE"]
        self.assertEqual(cell[0], 150)   # on field every zone dropback
        self.assertEqual(cell[1], 0)     # never targeted vs zone

    def test_strictly_prior_window_ignores_target_season(self):
        before = F.receiver_profile(self.tables, "wr1", "WR", 2022, self.avail)
        leaked = F.SeasonTables(season_rows(2020) + season_rows(2021)
                                + [drop(2022, "D", "MAN", ["wr1"], "wr1")] * 500, POS)
        after = F.receiver_profile(leaked, "wr1", "WR", 2022, self.avail)
        self.assertEqual(before, after)

    def test_man_rate_above_zone_rate_with_shrinkage_toward_position(self):
        p = F.receiver_profile(self.tables, "wr1", "WR", 2022, self.avail)
        self.assertEqual(p["status"], "OK")
        man, zone = p["bins"]["MAN"]["target_rate"], p["bins"]["ZONE"]["target_rate"]
        self.assertGreater(man, zone)
        self.assertLess(man, 0.20)       # shrunk from raw 0.20 toward the WR average
        self.assertAlmostEqual(p["exposure_man_share"], 0.5)

    def test_insufficient_sample_abstains(self):
        small = F.SeasonTables(season_rows(2021, 20, 20), POS)
        self.assertEqual(F.receiver_profile(small, "wr1", "WR", 2022, self.avail)["status"],
                         "INSUFFICIENT_RECEIVER_COVERAGE_SAMPLE")
        self.assertEqual(F.defense_profile(small, "D", 2022, self.avail, False)["status"],
                         "INSUFFICIENT_DEFENSE_COVERAGE_SAMPLE")

    def test_hc_change_increases_shrinkage_and_dc_unknown(self):
        rows = season_rows(2021, 300, 100, "HEAVYMAN") + season_rows(2021, 100, 300, "HEAVYZONE")
        t = F.SeasonTables(rows, POS)
        same = F.defense_profile(t, "HEAVYMAN", 2022, self.avail, False)
        changed = F.defense_profile(t, "HEAVYMAN", 2022, self.avail, True)
        unknown = F.defense_profile(t, "HEAVYMAN", 2022, self.avail, None)
        self.assertGreater(same["man_share"], changed["man_share"])
        self.assertGreater(changed["man_share"], same["league_man_share"])
        self.assertEqual(unknown["regime"], "REGIME_UNKNOWN")
        self.assertEqual(same["dc_identity"], D.UNKNOWN)

    def test_no_prior_season(self):
        self.assertEqual(F.defense_profile(self.tables, "D", 2019, self.avail, False)["status"],
                         "NO_PRIOR_SEASON")


class ChallengerTests(unittest.TestCase):
    def setUp(self):
        rows = season_rows(2021, 300, 300) + season_rows(2021, 300, 100, "MANTEAM")
        self.t = F.SeasonTables(rows, POS)
        self.rec = F.receiver_profile(self.t, "wr1", "WR", 2022, {2021})

    def test_ratio_one_when_opponent_mix_equals_faced(self):
        d = {"status": "OK", "man_share": self.rec["exposure_man_share"], "league_man_share": 0.5}
        ratio, why = C.matchup_ratio("COMBINED", "receptions", self.rec, None, d, 0.5)
        self.assertEqual(why, "OK")
        self.assertAlmostEqual(ratio, 1.0)

    def test_man_heavy_opponent_raises_man_winning_receiver(self):
        d = F.defense_profile(self.t, "MANTEAM", 2022, {2021}, False)
        ratio, _ = C.matchup_ratio("COMBINED", "receptions", self.rec, None, d, 0.5)
        self.assertGreater(ratio, 1.0)
        self.assertLessEqual(ratio, C.RATIO_HI)

    def test_missing_side_falls_back_exactly_to_scale_control(self):
        ratio, why = C.matchup_ratio("COMBINED", "receptions", self.rec, None,
                                     {"status": "INSUFFICIENT_DEFENSE_COVERAGE_SAMPLE"}, 0.5)
        self.assertIsNone(ratio)
        self.assertEqual(why, "INSUFFICIENT_DEFENSE_COVERAGE_SAMPLE")
        self.assertEqual(C.predict(4.0, 0.87, ratio, 1.0), 0.87 * 4.0)
        self.assertEqual(C.predict(4.0, 0.87, 1.2, 0.0), 0.87 * 4.0)   # alpha 0 == baseline

    def test_ratio_clipped_and_deterministic(self):
        d = {"status": "OK", "man_share": 1.0, "league_man_share": 0.5}
        a = C.matchup_ratio("COMBINED", "receiving_yards", self.rec, None, d, 0.5)
        b = C.matchup_ratio("COMBINED", "receiving_yards", self.rec, None, d, 0.5)
        self.assertEqual(a, b)
        self.assertTrue(C.RATIO_LO <= a[0] <= C.RATIO_HI)


class HcChangeMapTests(unittest.TestCase):
    def test_hc_change_detection(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engineering" / "nfl_tier2_coverage_20260925"))
        import evaluate_coverage as E
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "games.csv"
            with p.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["season", "week", "game_type", "home_team", "away_team", "home_coach", "away_coach"])
                w.writerow([2021, 18, "REG", "OAK", "X", "Coach A", "Coach X"])
                w.writerow([2022, 1, "REG", "LV", "X", "Coach B", "Coach X"])
            m = E.hc_change_map(p)
        self.assertTrue(m[(2022, "LV")])
        self.assertFalse(m[(2022, "X")])
        self.assertIsNone(m[(2021, "LV")])


if __name__ == "__main__":
    unittest.main()
