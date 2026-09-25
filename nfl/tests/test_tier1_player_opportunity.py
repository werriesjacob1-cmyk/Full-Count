"""Tier 1 workstream B (F2, F3, F8, F9): synthetic known-answer, leakage and UNKNOWN tests."""
import os
import tempfile
import unittest
from pathlib import Path

from nfl.research.tier1 import contract
from nfl.research.tier1 import player_opportunity_challenger as C
from nfl.research.tier1 import player_opportunity_evaluate as E
from nfl.research.tier1 import player_opportunity_features as F
from nfl.research.tier1 import player_opportunity_live as L

U = contract.UNKNOWN


def _snap(season, week, team, pid, snaps, pfr="x"):
    return {"season": season, "week": week, "team": team, "player_id": pid, "offense_snaps": float(snaps),
            "pfr_player_id": pfr, "position": "WR", "game_id": f"{season}_{week:02d}_{team}_Z",
            "opponent": "Z"}


def _pw(season, week, pid, team="A", position="WR", targets=0, receptions=0, yards=0, air=0,
        carries=0, rush=0, game=None):
    return {"player_id": pid, "player_name": pid, "position": position, "season": season, "week": week,
            "season_type": "REG", "game_id": game or f"{season}_{week:02d}_{team}_Z", "team": team,
            "opponent_team": "Z", "targets": float(targets), "receptions": float(receptions),
            "receiving_yards": float(yards), "receiving_tds": 0.0, "receiving_air_yards": float(air),
            "target_share": 0.0, "air_yards_share": 0.0, "wopr": 0.0, "carries": float(carries),
            "rushing_yards": float(rush), "rushing_tds": 0.0, "attempts": 0.0, "completions": 0.0,
            "passing_yards": 0.0, "passing_tds": 0.0}


class HistoryAndSnapTests(unittest.TestCase):
    def _hist(self, shares, extra=()):
        rows = []
        for i, sh in enumerate(shares, start=1):
            rows.append(_snap(2020, i, "A", "P", sh * 100))
            rows.append(_snap(2020, i, "A", "QB", 100))
        rows.extend(extra)
        return F.build_snap_history(rows)

    def test_known_answers(self):
        h = self._hist([0.2, 0.3, 0.4, 0.6, 0.8, 0.9])
        f = F.snap_features(h, "P", 2020, 7)
        self.assertAlmostEqual(f["snap_share_last3"], (0.6 + 0.8 + 0.9) / 3)
        self.assertAlmostEqual(f["snap_share_last5"], (0.3 + 0.4 + 0.6 + 0.8 + 0.9) / 5)
        self.assertAlmostEqual(f["snap_share_trend_last3_minus_prior"], (0.6 + 0.8 + 0.9) / 3 - 0.3)
        self.assertAlmostEqual(f["snap_share_season_to_date"], sum([0.2, 0.3, 0.4, 0.6, 0.8, 0.9]) / 6)
        self.assertEqual(f["snap_games_prior_n"], 6)

    def test_future_and_same_week_games_do_not_leak(self):
        h1 = self._hist([0.2, 0.3, 0.4, 0.6])
        future = [_snap(2020, 5, "A", "P", 100), _snap(2020, 5, "A", "QB", 100),
                  _snap(2021, 1, "A", "P", 10), _snap(2021, 1, "A", "QB", 100)]
        h2 = self._hist([0.2, 0.3, 0.4, 0.6], extra=future)
        self.assertEqual(F.snap_features(h1, "P", 2020, 5), F.snap_features(h2, "P", 2020, 5))
        # ...but the same future game IS visible one week later (the history is real).
        self.assertNotEqual(F.snap_features(h2, "P", 2020, 6)["snap_share_last3"],
                            F.snap_features(h1, "P", 2020, 6)["snap_share_last3"])

    def test_unknown_when_short_history_and_unmatched_quarantined(self):
        h = self._hist([0.5, 0.5], extra=[_snap(2020, 1, "A", None, 200)])
        f = F.snap_features(h, "P", 2020, 3)
        self.assertEqual(f["snap_share_last3"], U)
        self.assertEqual(f["snap_share_trend_last3_minus_prior"], U)
        # the unmatched (None) row still sets the team denominator (max snaps) -> 100/200
        self.assertAlmostEqual(F.snap_features(h, "P", 2020, 2)["snap_share_season_to_date"], 0.5 * 100 / 200)
        diag = F.snap_join_diagnostics([_snap(2020, 1, "A", None, 5), _snap(2020, 1, "A", "P", 5)])
        self.assertEqual(diag["all_offense"]["unmatched"], 1)
        self.assertAlmostEqual(diag["all_offense"]["unmatched_rate"], 0.5)

    def test_prior_asserts_strictness(self):
        h = F.History()
        h.add("k", 2020, 3, 1)
        h.finalize()
        self.assertEqual(h.prior("k", 2020, 3), [])
        self.assertEqual(h.prior("k", 2020, 4), [1])


class TargetFeatureTests(unittest.TestCase):
    def setUp(self):
        rows = [
            _pw(2020, 1, "P", targets=5, receptions=4, yards=40, air=50),
            _pw(2020, 1, "Q", targets=5, receptions=2, yards=30, air=100),
            _pw(2020, 2, "P", targets=10, receptions=6, yards=80, air=100),
            _pw(2020, 2, "Q", targets=10, receptions=5, yards=50, air=100),
        ]
        self.rows = rows
        self.ph, self.th = F.build_usage_histories(rows)

    def test_known_answers(self):
        f = F.target_features(self.ph, self.th, "P", "A", 2020, 3)
        self.assertAlmostEqual(f["target_share_last5"], 15 / 30)
        self.assertAlmostEqual(f["adot_last5"], 150 / 15)
        self.assertAlmostEqual(f["yards_per_target_last5"], 120 / 15)
        self.assertAlmostEqual(f["catch_rate_last5"], 10 / 15)
        self.assertAlmostEqual(f["team_targets_per_game_last5"], (10 + 20) / 2)
        self.assertEqual(f["route_share"], U)
        self.assertEqual(f["carry_share_last5"], U)  # team carries 0 -> UNKNOWN, never 0

    def test_leakage_future_game(self):
        before = F.target_features(self.ph, self.th, "P", "A", 2020, 2)
        ph, th = F.build_usage_histories(self.rows + [_pw(2020, 2, "Z9", targets=99)])
        self.assertEqual(F.target_features(ph, th, "P", "A", 2020, 2), before)
        self.assertAlmostEqual(before["target_share_last5"], 0.5)

    def test_unknown_without_targets(self):
        ph, th = F.build_usage_histories([_pw(2020, 1, "R", position="RB", carries=10, rush=40)])
        f = F.target_features(ph, th, "R", "A", 2020, 2)
        self.assertEqual(f["adot_last5"], U)
        self.assertEqual(f["target_share_last5"], U)  # team targets 0 -> UNKNOWN, not 0
        self.assertEqual(C.f3_estimate(f, "receptions", {"c0": 0.7, "c1": 0.0}), U)
        row = contract.feature_row("F3_TARGET_AIR_YARDS", season=2020, week=2, game_id="g", team="A",
                                   gsis_id="R", features=f, source_ids=["S"])
        self.assertEqual(row["features"]["adot_last5"], U)


class InjuryTests(unittest.TestCase):
    def _rows(self):
        base = dict(season=2020, team="A", position="WR", primary_injury="Knee", date_modified=U)
        return [
            {**base, "week": 3, "player_id": "P", "report_status": "QUESTIONABLE", "practice_status": "LIMITED"},
            {**base, "week": 3, "player_id": "O", "report_status": "OUT", "practice_status": "FULL"},
            {**base, "week": 2, "player_id": "R", "report_status": "OUT", "practice_status": "DNP"},
            {**base, "week": 3, "player_id": "D", "report_status": "OUT", "practice_status": "DNP"},
            {**base, "week": 3, "player_id": "D", "report_status": "QUESTIONABLE", "practice_status": "DNP"},
        ]

    def test_states(self):
        idx = F.InjuryIndex(self._rows())
        q = F.injury_features(idx, "P", "A", 2020, 3)
        self.assertEqual(F.injury_category(q), "QUESTIONABLE|LIMITED")
        self.assertEqual(F.injury_features(idx, "O", "A", 2020, 3)["contradiction"], "OUT_WITH_FULL_PRACTICE")
        healthy = F.injury_features(idx, "H", "A", 2020, 3)
        self.assertEqual(F.injury_category(healthy), F.NOT_LISTED)
        self.assertEqual(F.injury_category(F.injury_features(idx, "R", "A", 2020, 3)), "RETURNING_FROM_OUT")
        dup = F.injury_features(idx, "D", "A", 2020, 3)
        self.assertIn("DUPLICATE_ROWS_CONFLICT", dup["contradiction"])
        self.assertEqual(F.injury_category(dup), U)
        not_covered = F.injury_features(idx, "P", "B", 2020, 3)
        self.assertEqual(not_covered["report_status"], F.UNKNOWN_TEAM_WEEK)
        self.assertEqual(F.injury_category(not_covered), U)
        self.assertEqual(idx.team_listed(2020, 3, "A", ("OUT",)), ["O"])

    def test_live_week_without_final_report_is_unknown(self):
        rows = [dict(season=2026, week=3, team="A", player_id="P", position="WR", report_status=F.NONE_DESIGNATED,
                     practice_status="DNP", primary_injury="Hip", date_modified=U)]
        finals = L.final_report_team_weeks(rows, 2026, 3)
        self.assertEqual(finals, set())
        idx = F.InjuryIndex(rows, final_report_team_weeks=finals)
        f = F.injury_features(idx, "P", "A", 2026, 3)
        self.assertEqual(f["report_status"], F.UNKNOWN_NOT_FILED)
        self.assertEqual(f["practice_status"], "DNP")
        self.assertEqual(F.injury_features(idx, "X", "A", 2026, 3)["report_status"], F.UNKNOWN_NOT_FILED)
        self.assertEqual(C.f8_multiplier({"category": F.injury_category(f)}, {"QUESTIONABLE|DNP": 0.5}), 1.0)
        rows.append({**rows[0], "player_id": "Q", "report_status": "OUT"})
        self.assertEqual(L.final_report_team_weeks(rows, 2026, 3), {(2026, 3, "A")})

    def test_loader_normalizes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "injuries_2019.csv"
            p.write_text(
                "season,game_type,team,week,gsis_id,position,full_name,first_name,last_name,report_primary_injury,"
                "report_secondary_injury,report_status,practice_primary_injury,practice_secondary_injury,"
                "practice_status,date_modified\n"
                "2019,REG,OAK,1,00-1,WR,a,a,a,Knee,,Out,Knee,,Did Not Participate In Practice,2019-09-06T12:00:00Z\n"
                "2019,REG,OAK,1,,WR,a,a,a,,,,,,,\n"
                "2019,WC,OAK,18,00-2,WR,a,a,a,,,,,,,\n"
                "2019,REG,SD,1,00-3,WR,a,a,a,,,,Ankle,,\"\n    \",\n")
            rows = F.load_injury_rows(Path(d), [2019])
        self.assertEqual([r["player_id"] for r in rows], ["00-1", "00-3"])
        self.assertEqual(rows[0]["team"], "LV")
        self.assertEqual(rows[0]["practice_status"], "DNP")
        self.assertEqual(rows[1]["report_status"], F.NONE_DESIGNATED)
        self.assertEqual(rows[1]["practice_status"], F.UNKNOWN_PRACTICE)
        self.assertEqual(rows[1]["primary_injury"], "Ankle")


class RedistributionTests(unittest.TestCase):
    def _recips(self, overlap=1.0):
        return [{"player_id": "W2", "group": "WR", "share": 0.2, "overlap": {"W1": overlap}},
                {"player_id": "W3", "group": "WR", "share": 0.1, "overlap": {"W1": overlap}},
                {"player_id": "T1", "group": "TE", "share": 0.1, "overlap": {"W1": overlap}}]

    def test_mass_balance_proportional_and_hierarchy(self):
        res = F.redistribute([{"player_id": "W1", "group": "WR", "vacated_share": 0.3}], self._recips(),
                             retention=0.6, same_position_weight=0.75)
        g = res["gains"]
        self.assertAlmostEqual(g["W2"], 0.6 * 0.3 * 0.75 * (2 / 3))
        self.assertAlmostEqual(g["W3"], 0.6 * 0.3 * 0.75 * (1 / 3))
        self.assertAlmostEqual(g["T1"], 0.6 * 0.3 * 0.25)
        self.assertAlmostEqual(res["allocated"], 0.18)
        self.assertAlmostEqual(res["retained_budget"], 0.18)
        self.assertEqual(res["over_allocation"], 0.0)

    def test_never_all_to_one_player(self):
        res = F.redistribute([{"player_id": "W1", "group": "WR", "vacated_share": 0.4}],
                             [{"player_id": "W2", "group": "WR", "share": 0.2, "overlap": {"W1": 1.0}}],
                             retention=1.0, same_position_weight=1.0)
        self.assertAlmostEqual(res["gains"]["W2"], 0.5 * 0.4)
        self.assertAlmostEqual(res["unallocated_residual"], 0.2)

    def test_overlap_and_retention_zero(self):
        a = [{"player_id": "W1", "group": "WR", "vacated_share": 0.3}]
        self.assertEqual(sum(F.redistribute(a, self._recips(0.0), retention=1.0,
                                             same_position_weight=0.5)["gains"].values()), 0.0)
        self.assertEqual(sum(F.redistribute(a, self._recips(), retention=0.0,
                                            same_position_weight=0.5)["gains"].values()), 0.0)
        unknown = [{"player_id": "W1", "group": "WR", "vacated_share": U}]
        self.assertEqual(F.redistribute(unknown, self._recips(), retention=1.0,
                                        same_position_weight=0.5)["retained_budget"], 0.0)


class ConsumerTests(unittest.TestCase):
    def test_f2_scenarios_and_multiplier(self):
        snap = {"snap_share_last3": 0.9, "snap_share_last5": 0.6, "snap_share_trend_last3_minus_prior": 0.2}
        self.assertEqual(C.f2_scenario(snap, True, False), "RISE_TEAMMATE_OUT_NOW")
        self.assertEqual(C.f2_scenario(snap, False, True), "RISE_AFTER_RECENT_TEAMMATE_ABSENCE")
        self.assertEqual(C.f2_scenario(snap, False, False), "RISE_NO_TEAMMATE_EVENT")
        self.assertEqual(C.f2_scenario({**snap, "snap_share_trend_last3_minus_prior": -0.15}, 0, 0), "FALL")
        self.assertEqual(C.f2_scenario({**snap, "snap_share_trend_last3_minus_prior": 0.149}, 1, 0), "STABLE")
        self.assertEqual(C.f2_scenario({**snap, "snap_share_last3": U}, True, False), U)
        f = {**snap, "scenario": "RISE_TEAMMATE_OUT_NOW"}
        self.assertAlmostEqual(C.f2_multiplier(f, {"RISE_TEAMMATE_OUT_NOW": 0.5}), 1.5 ** 0.5)
        self.assertEqual(C.f2_multiplier({**f, "scenario": "STABLE"}, {"STABLE": 0.0}), 1.0)
        self.assertEqual(C.f2_multiplier({**f, "snap_share_last3": 3.0}, {"RISE_TEAMMATE_OUT_NOW": 1.0}), 2.0)

    def test_f3_known_answer(self):
        f = {"target_share_last5": 0.25, "team_targets_per_game_last5": 32.0, "targets_last5": 20.0,
             "adot_last5": 10.0, "receptions_last5": 14.0, "receiving_yards_last5": 200.0}
        p = {"c0": 0.8, "c1": -0.01, "y0": 6.0, "y1": 0.2}
        cr = (14 + 20 * 0.7) / 40
        self.assertAlmostEqual(C.f3_estimate(f, "receptions", p), 8 * cr)
        ypt = (200 + 20 * 8.0) / 40
        self.assertAlmostEqual(C.f3_estimate(f, "receiving_yards", p), 8 * ypt)
        self.assertEqual(C.f3_estimate(f, "rushing_yards", p), U)

    def _dataset(self):
        scored = [{"season": 2020, "week": 5, "game_id": "g1", "player_id": "P", "team": "A", "actual": 5.0,
                   "b0": 4.0, "role": 6.0, "position": "WR", "opponent_team": "Z"},
                  {"season": 2020, "week": 5, "game_id": "g1", "player_id": "Q", "team": "A", "actual": 2.0,
                   "b0": 2.0, "role": 3.0, "position": "WR", "opponent_team": "Z"},
                  {"season": 2020, "week": 5, "game_id": "g1", "player_id": "N", "team": "A", "actual": 2.0,
                   "b0": None, "role": 3.0, "position": "WR", "opponent_team": "Z"}]
        feats = {}
        for pid, scen in (("P", "RISE_TEAMMATE_OUT_NOW"), ("Q", U)):
            feats[(2020, 5, "g1", pid)] = {
                "F2_SNAP_SHARE_ROLE": {"features": {"scenario": scen, "snap_share_last3": 0.8,
                                                    "snap_share_last5": 0.4}},
                "F3_TARGET_AIR_YARDS": {"features": {"target_share_last5": U, "team_targets_per_game_last5": 30.0,
                                                     "targets_last5": 0.0, "adot_last5": U}},
                "F8_INJURY_PRACTICE": {"features": {"category": "QUESTIONABLE|LIMITED" if pid == "P" else U}},
                "F9_ABSENCE_REDISTRIBUTION": {"features": {}}}
        ds = C.Dataset(player_weeks=[], provenance={}, scored={"receptions": scored}, k={"receptions": 0.9})
        ds.features = feats
        ds.f9_team_weeks = {(2020, 5, "A"): {
            "absent": [{"player_id": "X", "position": "WR", "target_share": 0.2, "carry_share": 0.0}],
            "recipients": [{"player_id": "P", "position": "WR", "target_share": 0.2, "carry_share": 0.0,
                            "overlap": {"X": 1.0}}]}}
        params = {"k": {"receptions": 0.9}, "F3": {"w_receptions": 0.5},
                  "F2": {"receptions": {"alphas": {"RISE_TEAMMATE_OUT_NOW": 1.0}, "broad_alpha": 0.0}},
                  "F8": {"receptions": {"multipliers": {"QUESTIONABLE|LIMITED": 0.9}}},
                  "F9": {"receptions": {"retention": 0.5, "same_position_weight": 1.0}}}
        return ds, params

    def test_predict_fallback_and_attribution(self):
        ds, params = self._dataset()
        preds, changed, fb = C.predict(ds, "receptions", params, "ALL")
        kp, kq = (2020, 5, "g1", "P"), (2020, 5, "g1", "Q")
        self.assertNotIn((2020, 5, "g1", "N"), preds)  # no B0 -> not scored, never fabricated
        self.assertAlmostEqual(preds[kq], 0.9 * 2.0)  # every input UNKNOWN -> exactly the scale base
        gain = min(0.5 * 0.2 * 1.0 * 1.0, 0.5 * 0.2)
        self.assertAlmostEqual(preds[kp], 0.9 * 4.0 * 2.0 * 0.9 * (0.2 + gain) / 0.2)
        self.assertEqual(sorted(changed[kp]), ["F2_SNAP_SHARE_ROLE", "F8_INJURY_PRACTICE",
                                               "F9_ABSENCE_REDISTRIBUTION"])
        self.assertEqual(changed[kq], [])
        self.assertEqual(fb["F3_UNKNOWN_TO_SCALE_BASE"], 2)
        self.assertEqual(fb["F2_UNKNOWN_TO_SCALE_BASE"], 1)
        only, changed_only, _ = C.predict(ds, "receptions", params, "F8_INJURY_PRACTICE")
        self.assertAlmostEqual(only[kp], 0.9 * 4.0 * 0.9)
        self.assertEqual(changed_only[kp], ["F8_INJURY_PRACTICE"])
        scen, _, _ = C.predict(ds, "receptions", params, "F2_SNAP_SHARE_ROLE", f2_only_scenario="FALL")
        self.assertAlmostEqual(scen[kp], 0.9 * 4.0)


class LiveAndReportTests(unittest.TestCase):
    def test_live_b0_matches_rule(self):
        rows = [_pw(2026, w, "P", targets=t, receptions=r) for w, t, r in
                ((1, 5, 3), (2, 0, 0), (3, 4, 2), (4, 6, 5), (5, 3, 1))]
        self.assertAlmostEqual(L.live_b0(rows, "receptions", 2026, 5)["P"], (3 + 2 + 5) / 3)
        self.assertNotIn("P", L.live_b0(rows, "receptions", 2026, 4))  # only 2 role games before week 4

    def test_retrieved_at_is_utc_z(self):
        with tempfile.NamedTemporaryFile() as h:
            os.utime(h.name, (1790000000, 1790000000))
            self.assertEqual(L.retrieved_at(Path(h.name)), "2026-09-21T14:13:20Z")

    def test_market_verdict_rule(self):
        def ev(h_ci, f_delta):
            return {"vs_scale_control": {"partitions": {
                "DEV_2016_2022": {"paired_delta_mean": -0.1},
                "HOLDOUT_2023_2025": {"paired_delta_mean": -0.1, "paired_delta_ci95": h_ci},
                "FRESH_2026": {"paired_delta_mean": f_delta, "paired_delta_ci95": None, "n_matched": 5}}}}
        self.assertEqual(E.market_verdict(ev([-0.2, -0.01], -0.1))["verdict"], "VALIDATED_CRITERION_MET")
        self.assertEqual(E.market_verdict(ev([-0.2, -0.01], 0.01))["verdict"], "HOLDOUT_SUPPORTED_FRESH_WORSE")
        self.assertEqual(E.market_verdict(ev([-0.2, 0.0], -0.1))["verdict"], "NO_HOLDOUT_BENEFIT_VS_SCALE")


if __name__ == "__main__":
    unittest.main()
