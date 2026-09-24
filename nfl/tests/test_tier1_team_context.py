"""Workstream C (F1/F5/F6/F7/F10) unit tests: known answers, leakage, UNKNOWN."""
from __future__ import annotations

import math
import unittest
from datetime import datetime, timezone

from nfl.research.tier1 import harness
from nfl.research.tier1 import team_context_challenger as tcc
from nfl.research.tier1.contract import UNKNOWN, FeatureContractError, validate_feature_row
from nfl.research.tier1.team_context_data import (haversine_km, kickoff_utc, norm_team,
                                                  stadium_roof_types, surface_type,
                                                  team_game_context)
from nfl.research.tier1.team_context_features import (contract_rows, defense_prior_features,
                                                      shrunk_index, team_prior_features)
from nfl.research.tier1.team_context_weather import mos_features, mos_runtime_for

COORDS = {"Lambeau Field": {"lat": 44.5014, "lon": -88.0622},
          "Mercedes-Benz Stadium": {"lat": 33.7554, "lon": -84.4008},
          "Lumen Field": {"lat": 47.5952, "lon": -122.3316},
          "Lucas Oil Stadium": {"lat": 39.7601, "lon": -86.1639}}


def tg(team, opp, season, week, **kw):
    row = {"game_id": f"{season}_{week:02d}_{team}_{opp}", "team": team, "opponent": opp,
           "season": season, "week": week, "season_type": "REG", "plays": 60.0, "dropbacks": 35.0,
           "pass_attempts": 33.0, "completions": 22.0, "pass_yards": 240.0, "proe_sum": 0.0,
           "proe_n": 60.0, "neutral_plays": 30.0, "neutral_dropbacks": 15.0,
           "neutral_sec_sum": 900.0, "neutral_sec_n": 30.0}
    row.update(kw)
    return row


def sched(game_id, season, week, away, home, *, spread="4.5", total="42.5", ar="7", hr="7",
          stadium="Lambeau Field", roof="outdoors", surface="grass", gameday="2025-09-14",
          gametime="13:00", location="Home", completed=True):
    return {"game_id": game_id, "season": season, "week": week, "game_type": "REG",
            "gameday": gameday, "gametime": gametime, "weekday": "Sunday", "away_team": away,
            "home_team": home, "location": location, "away_score": "10" if completed else "",
            "home_score": "20" if completed else "",
            "away_rest": float(ar) if ar else UNKNOWN, "home_rest": float(hr) if hr else UNKNOWN,
            "spread_line": float(spread) if spread else UNKNOWN,
            "total_line": float(total) if total else UNKNOWN, "roof": roof, "surface": surface,
            "stadium_id": "X", "stadium": stadium, "completed": completed}


class TeamPriorFeatureTests(unittest.TestCase):
    def setUp(self):
        self.games = [tg("GB", "CHI", 2025, w, dropbacks=30.0 + w, proe_sum=6.0 * (w % 2),
                         pass_yards=200.0 + 10 * w) for w in range(1, 8)]

    def test_known_answer_uses_only_prior_games(self):
        f = team_prior_features(self.games, [("t", "GB", 2025, 6)])[("t", "GB")]
        self.assertEqual(f["prior_games_n"], 5)
        self.assertAlmostEqual(f["base_dropbacks_pg"], statistics_mean([31, 32, 33, 34, 35]))
        self.assertAlmostEqual(f["base_pass_yards_pg"], 230.0)
        self.assertAlmostEqual(f["f5_proe"], (6 * 3) / (60 * 5))
        self.assertAlmostEqual(f["f5_neutral_dropback_rate"], 0.5)
        self.assertAlmostEqual(f["f5_neutral_sec_per_play"], 30.0)

    def test_leakage_future_and_same_week_games_do_not_move_feature(self):
        before = team_prior_features(self.games, [("t", "GB", 2025, 6)])[("t", "GB")]
        mutated = [dict(g) for g in self.games]
        for g in mutated:
            if g["week"] >= 6:
                g["dropbacks"], g["proe_sum"] = 999.0, 999.0
        after = team_prior_features(mutated, [("t", "GB", 2025, 6)])[("t", "GB")]
        self.assertEqual(before, after)
        mutated[4]["dropbacks"] = 999.0  # week 5 is prior -> must move
        moved = team_prior_features(mutated, [("t", "GB", 2025, 6)])[("t", "GB")]
        self.assertNotEqual(before["base_dropbacks_pg"], moved["base_dropbacks_pg"])

    def test_window_and_unknown_below_min_games(self):
        f = team_prior_features(self.games, [("t", "GB", 2025, 4)])[("t", "GB")]
        self.assertEqual(f["prior_games_n"], 3)
        self.assertEqual(f["base_dropbacks_pg"], UNKNOWN)
        self.assertEqual(f["f5_proe"], UNKNOWN)
        w = team_prior_features(self.games, [("t", "GB", 2025, 8)], window=2)[("t", "GB")]
        self.assertEqual(w["prior_games_n"], 2)

    def test_post_season_rows_do_not_enter_reg_history(self):
        games = self.games + [tg("GB", "CHI", 2024, 20, season_type="POST", dropbacks=500.0)]
        f = team_prior_features(games, [("t", "GB", 2025, 6)])[("t", "GB")]
        self.assertAlmostEqual(f["base_dropbacks_pg"], 33.0)


def statistics_mean(xs):
    return sum(xs) / len(xs)


class DefenseTests(unittest.TestCase):
    def _data(self):
        games, pos = [], []
        # offense OFF plays DEF in week 6; OFF's prior weeks 1-5 vs others, WR receptions 10/game
        for w in range(1, 8):
            opp = "DEF" if w == 6 else f"O{w}"
            games.append(tg("OFF", opp, 2025, w))
            pos.append({"game_id": f"2025_{w:02d}_OFF_{opp}", "team": "OFF", "opponent": opp,
                        "season": 2025, "week": w, "season_type": "REG", "pos": "WR",
                        "targets": 20.0, "receptions": 15.0 if w == 6 else 10.0, "yards": 150.0,
                        "epa": 1.0})
        # DEF's own offense rows so the defense has an 'opponent' mapping
        return games, pos

    def test_adjusted_allowed_known_answer_and_leakage(self):
        games, pos = self._data()
        f = defense_prior_features(games, pos, [("x", "DEF", 2025, 7)])[("x", "DEF")]
        self.assertEqual(f["def_prior_games_n"], 1)
        self.assertEqual(f["f10_WR_receptions_allowed_pg"], 15.0)
        self.assertEqual(f["f10_WR_receptions_adj_allowed_sum"], 15.0)
        self.assertEqual(f["f10_WR_receptions_adj_expected_sum"], 10.0)
        # target week 6 (the game itself) must not see week 6
        g6 = defense_prior_features(games, pos, [("x", "DEF", 2025, 6)])[("x", "DEF")]
        self.assertEqual(g6["def_prior_games_n"], 0)
        self.assertEqual(g6["f10_WR_receptions_allowed_pg"], UNKNOWN)
        self.assertEqual(g6["f10_WR_receptions_adj_allowed_sum"], UNKNOWN)

    def test_shrunk_index(self):
        self.assertAlmostEqual(shrunk_index(15.0, 10.0, 1, 0), 1.5)
        self.assertAlmostEqual(shrunk_index(15.0, 10.0, 1, 1), 25.0 / 20.0)
        self.assertLess(abs(shrunk_index(15.0, 10.0, 1, 1e6) - 1.0), 1e-4)
        self.assertEqual(shrunk_index(UNKNOWN, 10.0, 1, 1), UNKNOWN)
        self.assertEqual(shrunk_index(1.0, 0.0, 1, 1), UNKNOWN)


class ContextTests(unittest.TestCase):
    def test_implied_totals_sign_and_rest(self):
        s = [sched("2025_03_ATL_GB", 2025, 3, "ATL", "GB", spread="4.5", total="42.5", ar="4", hr="14",
                   gameday="2025-09-18", gametime="20:15")]
        ctx = team_game_context(s, COORDS)
        home, away = ctx[("2025_03_ATL_GB", "GB")], ctx[("2025_03_ATL_GB", "ATL")]
        self.assertAlmostEqual(home["f1_implied_team_total_closing"], 23.5)
        self.assertAlmostEqual(away["f1_implied_team_total_closing"], 19.0)
        self.assertAlmostEqual(home["f1_team_margin_closing"], 4.5)
        self.assertAlmostEqual(away["f1_team_margin_closing"], -4.5)
        self.assertTrue(away["f7_short_week"])
        self.assertFalse(away["f7_post_bye"])
        self.assertTrue(home["f7_post_bye"])
        self.assertEqual(away["f7_rest_diff"], -10.0)
        self.assertEqual(home["f7_travel_km"], 0.0)
        self.assertEqual(away["f7_travel_km"], UNKNOWN)  # ATL has no home game in this schedule

    def test_missing_lines_are_unknown(self):
        ctx = team_game_context([sched("g", 2025, 1, "ATL", "GB", spread="", total="")], COORDS)
        self.assertEqual(ctx[("g", "GB")]["f1_implied_team_total_closing"], UNKNOWN)

    def test_west_to_east_early_and_timezones(self):
        s = [sched("h", 2025, 1, "X1", "SEA", stadium="Lumen Field", gameday="2025-09-07"),
             sched("g", 2025, 2, "SEA", "GB", gameday="2025-09-14", gametime="13:00")]
        ctx = team_game_context(s, COORDS)
        row = ctx[("g", "SEA")]
        self.assertEqual(row["f7_tz_shift_east_hours"], 2.0)
        self.assertTrue(row["f7_west_to_east_early"])
        self.assertAlmostEqual(row["f7_body_clock_kickoff_hour"], 10.0)
        self.assertAlmostEqual(row["f7_travel_km"], round(haversine_km((47.5952, -122.3316), (44.5014, -88.0622)), 1))
        self.assertFalse(row["f7_international"])

    def test_roof_semantics(self):
        s = [sched("a", 2025, 1, "X", "IND", stadium="Lucas Oil Stadium", roof="closed"),
             sched("b", 2025, 2, "X", "IND", stadium="Lucas Oil Stadium", roof=""),
             sched("c", 2025, 1, "X", "GB", roof="outdoors"),
             sched("d", 2026, 1, "X", "ATL", stadium="Mercedes-Benz Stadium", roof="dome", completed=False)]
        roof = stadium_roof_types(s)
        self.assertEqual(roof["Lucas Oil Stadium"], "retractable")
        self.assertEqual(roof["Lambeau Field"], "outdoors")
        self.assertNotIn("Mercedes-Benz Stadium", roof)  # unplayed rows are not evidence
        ctx = team_game_context(s, COORDS)
        self.assertEqual(ctx[("d", "ATL")]["f6_roof_type"], UNKNOWN)
        self.assertEqual(surface_type("a_turf"), "turf")
        self.assertEqual(surface_type("grass "), "grass")
        self.assertEqual(surface_type(""), UNKNOWN)

    def test_kickoff_is_eastern(self):
        self.assertEqual(kickoff_utc("2026-09-24", "20:15"), datetime(2026, 9, 25, 0, 15, tzinfo=timezone.utc))
        self.assertEqual(norm_team("OAK"), "LV")


class ConsumerTests(unittest.TestCase):
    def rec(self, **kw):
        base = {"key": (2020, 5, "g", "p"), "season": 2020, "week": 5, "game_id": "g", "team": "GB",
                "b0": 4.0, "actual": 5.0, "position": "WR", "hist_y": 30.0,
                "E": {"VOLUME_BASE": 33.0, "F1": 36.0, "F5": 30.0}, "f10": (15.0, 10.0, 1),
                "f10_reason": None}
        base.update(kw)
        return base

    def test_multiplier_known_answers(self):
        mult, why = tcc.multipliers([self.rec()], "F1", 1.0, 0.0, 1)
        self.assertAlmostEqual(mult[0], 36.0 / 30.0)
        self.assertIsNone(why[0])
        mult, _ = tcc.multipliers([self.rec()], "F10", 0.0, 1.0, 0)
        self.assertAlmostEqual(mult[0], 1.5)
        mult, _ = tcc.multipliers([self.rec(E={"VOLUME_BASE": 300.0})], "VOLUME_BASE", 1.0, 0.0, 0)
        self.assertAlmostEqual(mult[0], tcc.RATIO_CLIP[1])

    def test_unknown_falls_back_to_scale_base_and_is_recorded(self):
        mult, why = tcc.multipliers([self.rec(hist_y=UNKNOWN)], "F1", 1.0, 0.0, 1)
        self.assertEqual(mult[0], 1.0)
        self.assertEqual(why[0], "TEAM_VOLUME_UNKNOWN")
        mult, why = tcc.multipliers([self.rec(f10=UNKNOWN, f10_reason="POSITION_NOT_COVERED")],
                                    "F10", 0.0, 1.0, 1)
        self.assertEqual((mult[0], why[0]), (1.0, "POSITION_NOT_COVERED"))
        preds, fb = tcc.predict_config([self.rec(hist_y=UNKNOWN)], "F1",
                                       {"alpha": 1.0, "beta": 0.0, "pseudo_games": 1}, 0.9)
        self.assertAlmostEqual(preds[(2020, 5, "g", "p")], 3.6)

    def test_market_flag_and_attribution(self):
        self.assertTrue(tcc.uses_market_input("ALL"))
        self.assertTrue(tcc.uses_market_input("F1"))
        self.assertFalse(tcc.uses_market_input("ALL_NO_MARKET"))
        rec = self.rec(E={"VOLUME_BASE": 33.0, "F1": 36.0})
        att = tcc.attribution_rows([rec], "F1", {"alpha": 1.0, "beta": 0.0, "pseudo_games": 1})
        c = att[rec["key"]]
        self.assertAlmostEqual(c["F1"], math.log(36 / 30) - math.log(33 / 30))
        self.assertAlmostEqual(c["VOLUME_BASE"], math.log(33 / 30))
        self.assertIn("F1_GAME_CONTEXT", tcc.changed_by(att)[rec["key"]])

    def test_fit_config_recovers_exponent(self):
        recs = []
        for i in range(200):
            e = 20.0 + (i % 20)
            recs.append(self.rec(key=(2018, 1, str(i), "p"), season=2018, b0=4.0, hist_y=30.0,
                                 E={"VOLUME_BASE": e}, actual=4.0 * (e / 30.0)))
        p = tcc.fit_config(recs, "VOLUME_BASE", 1.0)
        self.assertAlmostEqual(p["alpha"], 1.0)
        self.assertLess(p["dev_mae"], 1e-9)

    def test_b0_windows_match_harness(self):
        rows = []
        for w in range(1, 9):
            rows.append({"player_id": "p", "season": 2020, "week": w, "season_type": "REG",
                         "game_id": f"g{w}", "team": "OAK", "opponent_team": "X", "position": "WR",
                         "targets": float(w % 3), "receptions": float(w % 3), "receiving_yards": 10.0 * w})
        scored = harness.b0_rolling_mean(rows, "receiving_yards")
        wins = tcc.b0_windows(rows, "receiving_yards")
        self.assertGreater(tcc.check_b0_parity(scored, wins, rows, "receiving_yards"), 0)
        self.assertTrue(all(t == "LV" for w in wins.values() for _g, t in w))


class WeatherTests(unittest.TestCase):
    def test_runtime_rule_and_features(self):
        self.assertEqual(mos_runtime_for("2026-09-24"), "2026-09-23 12:00")
        rt = "2026-09-23 12:00"
        rows = [{"runtime": rt, "ftime": "2026-09-24 21:00", "tmp": 60, "wsp": 6, "p06": None},
                {"runtime": rt, "ftime": "2026-09-25 00:00", "tmp": 62, "wsp": 10, "p06": 4.0},
                {"runtime": rt, "ftime": "2026-09-25 03:00", "tmp": 58, "wsp": 8, "p06": None},
                {"runtime": rt, "ftime": "2026-09-25 06:00", "tmp": 50, "wsp": 20, "p06": 30.0},
                {"runtime": "2026-09-24 12:00", "ftime": "2026-09-25 00:00", "wsp": 99, "p06": 99}]
        f = mos_features(rows, datetime(2026, 9, 25, 0, 15, tzinfo=timezone.utc), rt)
        self.assertAlmostEqual(f["f6_fcst_wind_kt"], 9.0)   # 00Z and 03Z only
        self.assertEqual(f["f6_fcst_pop6"], 30.0)           # 06Z ends the 6 h after kickoff
        self.assertAlmostEqual(f["f6_fcst_lead_hours"], 36.25)
        empty = mos_features([], datetime(2026, 9, 25, tzinfo=timezone.utc), rt)
        self.assertEqual(empty["f6_fcst_wind_kt"], UNKNOWN)


class ContractRowTests(unittest.TestCase):
    def test_rows_validate_and_flag_market_input(self):
        s = [sched("g", 2025, 2, "ATL", "GB")]
        ctx = team_game_context(s, COORDS)
        src = {k: [f"src_{k}"] for k in ("F1", "F5", "F6", "F7", "F10")}
        rows = contract_rows(ctx, {}, {}, [("g", "GB")], source_ids=src)
        self.assertEqual(len(rows), 5)
        f1 = [r for r in rows if r["factor_id"] == "F1_GAME_CONTEXT"][0]
        self.assertTrue(f1["features"]["uses_market_input"])
        self.assertEqual(f1["features"]["f1_label"], "CLOSING_LINE_PROXY_RETROSPECTIVE")
        f5 = [r for r in rows if r["factor_id"] == "F5_PASS_TENDENCY_PACE"][0]
        self.assertEqual(f5["features"]["f5_proe"], UNKNOWN)
        for r in rows:
            validate_feature_row(r)
        live = contract_rows(ctx, {}, {}, [("g", "GB")], source_ids=src,
                             information_cutoff="2026-09-24T21:00:00Z", f1_override={})
        with self.assertRaises(FeatureContractError):
            validate_feature_row(live[0], prediction_cutoff="2026-09-24T20:00:00Z")


if __name__ == "__main__":
    unittest.main()
