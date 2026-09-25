"""Workstream D (F4_RED_ZONE + touchdown_opportunity_challenger): synthetic tests."""
import math
import unittest

from nfl.research.tier1 import contract as C
from nfl.research.tier1 import touchdown_consumer as TC
from nfl.research.tier1 import touchdown_features as TF


def play(**over):
    base = {"game_id": "2020_01_AWY_HOM", "season": "2020", "week": "1", "season_type": "REG",
            "posteam": "HOM", "defteam": "AWY", "home_team": "HOM", "play_type": "run",
            "down": "1", "yardline_100": "30", "pass_attempt": "0", "rush_attempt": "1",
            "two_point_attempt": "0", "extra_point_attempt": "0", "qb_kneel": "0",
            "qb_spike": "0", "receiver_player_id": "", "rusher_player_id": "RB1",
            "rush_touchdown": "0", "pass_touchdown": "0", "td_player_id": "", "penalty": "0",
            "play_deleted": "0", "fixed_drive": "1", "fixed_drive_result": "Punt"}
    base.update(over)
    return base


def weekly(pid, week, *, team="HOM", pos="RB", carries=1.0, targets=0.0, season=2020,
           game_id=None, opp="AWY"):
    return {"player_id": pid, "player_name": pid, "position": pos, "season": season,
            "week": week, "season_type": "REG",
            "game_id": game_id or f"{season}_{week:02d}_AWY_HOM", "team": team,
            "opponent_team": opp, "carries": carries, "targets": targets,
            "receptions": 0.0, "rushing_tds": 0.0, "receiving_tds": 0.0}


class ZoneAndExclusionTests(unittest.TestCase):
    def test_zone_boundaries(self):
        for yl, zone in ((1, "Z5"), (5, "Z5"), (6, "Z10"), (10, "Z10"), (11, "Z20"),
                         (20, "Z20"), (21, "OUT"), (99, "OUT")):
            self.assertEqual(TF.zone_of(yl), zone, yl)

    def test_exclusion_rules_and_td_credit(self):
        plays = [
            play(yardline_100="3", rush_touchdown="1", td_player_id="RB1",
                 fixed_drive="1", fixed_drive_result="Touchdown"),                  # counted, TD
            play(yardline_100="2", two_point_attempt="1", rush_touchdown="0"),     # 2-pt: excluded
            play(play_type="no_play", pass_attempt="1", rush_attempt="0",
                 receiver_player_id="WR1", rusher_player_id="", yardline_100="8"),  # nullified
            play(play_type="qb_kneel", rusher_player_id="QB1", qb_kneel="1"),      # kneel
            play(play_type="pass", pass_attempt="1", rush_attempt="0",
                 receiver_player_id="WR1", rusher_player_id="", yardline_100="15"),  # tgt Z20
            play(play_type="pass", pass_attempt="1", rush_attempt="0",
                 receiver_player_id="", rusher_player_id="", yardline_100="12"),   # sack
            play(play_type="pass", pass_attempt="1", rush_attempt="0", penalty="1",
                 receiver_player_id="WR1", rusher_player_id="", yardline_100="40"),  # stands
            play(play_deleted="1", yardline_100="1"),                              # deleted
            play(yardline_100="", rusher_player_id="RB1"),                        # no yardline
        ]
        parsed = TF.parse_plays(plays)
        d = parsed.diagnostics
        self.assertEqual(d["excluded_two_point_attempt"], 1)
        self.assertEqual(d["excluded_no_play_nullified_or_admin"], 1)
        self.assertEqual(d["excluded_kneel_or_spike"], 1)
        self.assertEqual(d["excluded_no_intended_player"], 1)
        self.assertEqual(d["excluded_play_deleted"], 1)
        self.assertEqual(d["excluded_missing_yardline"], 1)
        self.assertEqual(d["counted_play_with_accepted_penalty"], 1)
        rb = parsed.player_games[("2020_01_AWY_HOM", "RB1")]
        self.assertEqual(rb["counts"]["car_Z5"], 1)          # 2-pt from the 2 NOT counted
        self.assertEqual(rb["tds"]["car_Z5"], 1)
        self.assertEqual(sum(rb["counts"].values()), 1)
        wr = parsed.player_games[("2020_01_AWY_HOM", "WR1")]
        self.assertEqual(wr["counts"]["tgt_Z20"], 1)
        self.assertEqual(wr["counts"]["tgt_OUT"], 1)
        self.assertEqual(wr["counts"]["tgt_Z10"], 0)         # nullified target NOT counted
        team = parsed.team_games[("2020_01_AWY_HOM", "HOM")]
        self.assertEqual(team["counts"]["car_Z5"], 1)
        self.assertEqual(team["counts"]["tgt_Z20"], 1)

    def test_td_credited_only_to_the_scorer(self):
        parsed = TF.parse_plays([play(play_type="pass", pass_attempt="1", rush_attempt="0",
                                      receiver_player_id="WR1", rusher_player_id="",
                                      yardline_100="4", pass_touchdown="1",
                                      td_player_id="OTHER")])
        self.assertEqual(parsed.player_games[("2020_01_AWY_HOM", "WR1")]["tds"]["tgt_Z5"], 0)

    def test_red_zone_trip_ignores_pat_spot(self):
        plays = [
            # drive 1: long TD from the 40, PAT spotted at the 15 -> NOT a trip
            play(fixed_drive="1", yardline_100="40", rush_touchdown="1", td_player_id="RB1",
                 fixed_drive_result="Touchdown"),
            play(fixed_drive="1", play_type="extra_point", yardline_100="15", rush_attempt="0",
                 rusher_player_id="", extra_point_attempt="1", fixed_drive_result="Touchdown"),
            play(fixed_drive="1", play_type="pass", yardline_100="2", two_point_attempt="1",
                 fixed_drive_result="Touchdown"),
            # drive 2: reaches the 18, field goal -> trip, not converted
            play(fixed_drive="2", yardline_100="30", fixed_drive_result="Field goal"),
            play(fixed_drive="2", yardline_100="18", fixed_drive_result="Field goal"),
            play(fixed_drive="2", play_type="field_goal", yardline_100="18", rush_attempt="0",
                 rusher_player_id="", fixed_drive_result="Field goal"),
            # drive 3: ball spotted at the 9 on a no_play (penalty), then TD -> trip, converted
            play(fixed_drive="3", play_type="no_play", yardline_100="9", rush_attempt="0",
                 rusher_player_id="", fixed_drive_result="Touchdown"),
            play(fixed_drive="3", yardline_100="24", rush_touchdown="1", td_player_id="RB1",
                 fixed_drive_result="Touchdown"),
        ]
        team = TF.parse_plays(plays).team_games[("2020_01_AWY_HOM", "HOM")]
        self.assertEqual(team["drives"], 3)
        self.assertEqual(team["rz_trips"], 2)
        self.assertEqual(team["rz_trip_tds"], 1)

    def test_team_codes_canonical(self):
        parsed = TF.parse_plays([play(game_id="2017_01_KC_OAK", posteam="OAK", defteam="KC",
                                      home_team="OAK")])
        self.assertIn(("2017_01_KC_OAK", "LV"), parsed.team_games)
        self.assertEqual(TF.canonical_team("SD"), "LAC")
        self.assertEqual(TF.canonical_team("STL"), "LA")


def _two_week_world(week1_rb1_z5=2):
    plays = [play(yardline_100="3", rusher_player_id="RB1") for _ in range(week1_rb1_z5)]
    plays += [play(yardline_100="30", rusher_player_id="RB2")]
    plays += [play(game_id="2020_02_AWY_HOM", week="2", yardline_100="1",
                   rusher_player_id="RB1") for _ in range(5)]
    plays += [play(game_id="2020_03_AWY_HOM", week="3", yardline_100="1",
                   rusher_player_id="RB1") for _ in range(9)]
    parsed = TF.parse_plays(plays)
    rows = [weekly("RB1", 1, carries=float(week1_rb1_z5)), weekly("RB2", 1),
            weekly("RB1", 2, carries=5.0), weekly("RB1", 3, carries=9.0)]
    return rows, parsed


class FeatureWalkTests(unittest.TestCase):
    def test_known_answer_shrinkage(self):
        rows, parsed = _two_week_world()
        feats, _ = TF.build_features([TF.Request(2020, 2, "2020_02_AWY_HOM", "HOM", "RB1", "RB")],
                                     rows, parsed)
        f = feats[(2020, 2, "2020_02_AWY_HOM", "RB1")]["features"]
        # positional mean after week 1: 2 RB apps, 2 car_Z5 -> 1.0/game
        self.assertAlmostEqual(f["L8_pg_car_Z5"], (2 + 3 * 1.0) / (1 + 3))
        # raw share 2/2 = 1.0; positional share 2 / (2 + 2) = 0.5
        self.assertAlmostEqual(f["L8_share_car_Z5"], (1 * 1.0 + 3 * 0.5) / (1 + 3))
        self.assertEqual(f["L8_raw_car_Z5"], 2.0)
        self.assertAlmostEqual(f["pos_frac_car_Z5"], 2 / 3)
        self.assertEqual(f["L8_n_games"], 1.0)
        self.assertEqual(f["STD_n_games"], 1.0)

    def test_no_leakage_from_target_or_future_weeks(self):
        req = [TF.Request(2020, 2, "2020_02_AWY_HOM", "HOM", "RB1", "RB")]
        rows, parsed = _two_week_world()
        base = TF.build_features(req, rows, parsed)[0]
        rows2, parsed2 = _two_week_world()
        for key in list(parsed2.player_games):
            if key[0] != "2020_01_AWY_HOM":
                parsed2.player_games[key]["counts"]["car_Z5"] += 50
        for key in list(parsed2.team_games):
            if key[0] != "2020_01_AWY_HOM":
                parsed2.team_games[key]["rz_trips"] += 50
        self.assertEqual(TF.build_features(req, rows2, parsed2)[0], base)
        # ...but a PRIOR-week change does move it (the test can fail).
        rows3, parsed3 = _two_week_world(week1_rb1_z5=3)
        self.assertNotEqual(TF.build_features(req, rows3, parsed3)[0], base)

    def test_first_week_is_unknown_and_consumer_falls_back(self):
        rows, parsed = _two_week_world()
        req = [TF.Request(2020, 1, "2020_01_AWY_HOM", "HOM", "RB1", "RB")]
        feats, _ = TF.build_features(req, rows, parsed)
        f = feats[(2020, 1, "2020_01_AWY_HOM", "RB1")]["features"]
        self.assertTrue(C.is_unknown(f["L8_pg_car_Z5"]))
        self.assertTrue(C.is_unknown(f["team_L8_rz_trips_pg"]))
        scored = [{"season": 2020, "week": 1, "game_id": "2020_01_AWY_HOM", "player_id": "RB1",
                   "b0": 0.2, "actual": 1.0},
                  {"season": 2020, "week": 1, "game_id": "2020_01_AWY_HOM", "player_id": "RB1x",
                   "b0": None, "actual": 0.0}]
        feats[(2020, 1, "2020_01_AWY_HOM", "RB1x")] = feats[(2020, 1, "2020_01_AWY_HOM", "RB1")]
        for variant in TC.VARIANTS:
            preds, lams, counts = TC.predict(feats, scored, variant, implied={})
            self.assertEqual(preds, {(2020, 1, "2020_01_AWY_HOM", "RB1"): 0.2}, variant)
            self.assertEqual(lams, {})
            self.assertEqual(counts, {"model": 0, "fallback_b0": 1, "no_prediction": 1})

    def test_live_requests_only_current_team_players(self):
        rows = [weekly("A", 1, season=2026, team="ATL"), weekly("B", 1, season=2026, team="GB"),
                weekly("C", 1, season=2026, team="ATL"), weekly("C", 2, season=2026, team="NO"),
                weekly("D", 17, season=2025, team="GB"), weekly("E", 3, season=2026, team="GB")]
        reqs = TF.live_requests(rows, target_season=2026, target_week=3,
                                game_id="2026_03_ATL_GB", teams=("ATL", "GB"))
        self.assertEqual([r.player_id for r in reqs], ["A", "B"])


class ConsumerTests(unittest.TestCase):
    def test_poisson_links(self):
        self.assertEqual(TC.p_anytime(0.0), 0.0)
        self.assertAlmostEqual(TC.p_anytime(1.0), 1 - math.exp(-1))
        self.assertAlmostEqual(TC.p_two_plus(1.0), 1 - 2 * math.exp(-1))
        for lam in (0.0, 1e-4, 0.05, 0.3, 1.0, 3.0, 10.0):
            self.assertLessEqual(TC.p_two_plus(lam), TC.p_anytime(lam))
            self.assertGreaterEqual(TC.p_two_plus(lam), 0.0)

    def _features(self):
        f = {"position_group": "RB", "team_L8_rz_td_per_trip": 0.6, "league_rz_td_per_trip": 0.5}
        for b in TF.BUCKETS:
            f[f"L8_pg_{b}"] = 1.0 if b == "car_Z5" else 0.0
            f[f"L8_share_{b}"] = 0.5 if b == "car_Z5" else 0.0
            f[f"team_L8_plays_pg_{b}"] = 4.0
            f[f"L8_raw_{b}"] = 2.0 if b == "car_Z5" else 0.0
            f[f"pos_frac_{b}"] = 0.25
        return f

    def test_known_lambdas(self):
        f = self._features()
        P = TC.PARAMS
        self.assertAlmostEqual(TC.raw_lambda(f, "rz_share_x_team", P), 2.0 * P["conv"]["RB"]["car_Z5"])
        self.assertAlmostEqual(TC.raw_lambda(f, "rz_direct", P), 1.0 * P["conv"]["RB"]["car_Z5"])
        self.assertAlmostEqual(TC.raw_lambda(f, "volume_only", P), 1.0 * P["conv_all"]["RB"]["car"])
        self.assertAlmostEqual(TC.raw_lambda(f, "rz_team_conv", P),
                               2.0 * P["conv"]["RB"]["car_Z5"] * 1.2)
        vol, rz = TC.raw_lambda(f, "volume_only", P), TC.raw_lambda(f, "rz_share_x_team", P)
        self.assertAlmostEqual(TC.raw_lambda(f, "rz_blend", {**P, "w_blend": 0.0}), vol)
        self.assertAlmostEqual(TC.raw_lambda(f, "rz_blend", {**P, "w_blend": 1.0}), rz)
        w = P["w_blend"]
        self.assertAlmostEqual(TC.raw_lambda(f, "rz_blend", P), vol ** (1 - w) * rz ** w)
        self.assertIsNone(TC.raw_lambda(f, "closing_line_proxy", P, None))
        self.assertAlmostEqual(TC.raw_lambda(f, "closing_line_proxy", P, 1.0), vol ** (1 - w) * rz ** w)

    def test_unknown_input_never_zero(self):
        f = self._features()
        f["team_L8_plays_pg_tgt_OUT"] = C.UNKNOWN
        self.assertIsNone(TC.raw_lambda(f, "rz_share_x_team", TC.PARAMS))
        self.assertIsNone(TC.raw_lambda(f, "rz_blend", TC.PARAMS))
        self.assertIsNotNone(TC.raw_lambda(f, "volume_only", TC.PARAMS))

    def test_params_are_declared(self):
        P = TC.PARAMS
        self.assertEqual(P["fit_partition"], "DEV_2016_2022")
        self.assertEqual(TC.PRIMARY, "rz_blend")
        for pos in TC.POSITIONS:
            self.assertEqual(set(P["conv"][pos]), set(TF.BUCKETS))
        self.assertEqual(set(P["scale_c"]), set(TC.VARIANTS))
        self.assertTrue(0.0 <= P["w_blend"] <= 1.0)
        # Goal-line conversion must dominate outside-the-20 conversion.
        for pos in ("RB", "WR", "TE"):
            self.assertGreater(P["conv"][pos]["car_Z5"], 10 * P["conv"][pos]["car_OUT"])
            self.assertGreater(P["conv"][pos]["tgt_Z5"], 10 * P["conv"][pos]["tgt_OUT"])

    def test_live_row_timestamp_contract(self):
        from nfl.research.tier1 import touchdown_evaluate as E
        row = C.feature_row(TF.FACTOR_ID, season=2026, week=3, game_id="2026_03_ATL_GB",
                            team="GB", gsis_id="00-1", features={"x": 1.0},
                            source_ids=[TF.SOURCE_PBP], information_cutoff=E.LIVE_INFORMATION_CUTOFF)
        C.validate_feature_row(row, prediction_cutoff=E.LIVE["kickoff_utc"])
        with self.assertRaises(C.FeatureContractError):
            C.validate_feature_row(row, prediction_cutoff="2026-09-24T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
