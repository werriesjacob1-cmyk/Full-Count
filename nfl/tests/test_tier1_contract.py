"""Tier 1 shared contract + harness: synthetic known-answer tests."""
import unittest

from nfl.research.tier1 import contract as C
from nfl.research.tier1 import harness as H


def _row(**over):
    base = dict(season=2024, week=5, game_id="2024_05_A_B", team="A", gsis_id="00-1",
                features={"x": 1.0, "y": C.UNKNOWN}, source_ids=["NFLVERSE_WEEKLY_STATS"])
    base.update(over)
    return base


class ContractTests(unittest.TestCase):
    def test_valid_row_and_unknown(self):
        row = C.feature_row("F3_TARGET_AIR_YARDS", **_row())
        self.assertTrue(C.is_unknown(row["features"]["y"]))

    def test_none_and_nan_rejected(self):
        for bad in (None, float("nan"), float("inf")):
            with self.assertRaises(C.FeatureContractError):
                C.feature_row("F3_TARGET_AIR_YARDS", **_row(features={"x": bad}))

    def test_unknown_factor_and_missing_source(self):
        with self.assertRaises(C.FeatureContractError):
            C.feature_row("F99", **_row())
        with self.assertRaises(C.FeatureContractError):
            C.feature_row("F1_GAME_CONTEXT", **_row(source_ids=[]))

    def test_live_row_after_cutoff_rejected(self):
        row = C.feature_row("F8_INJURY_PRACTICE", **_row(information_cutoff="2026-09-24T23:00:00Z"))
        C.validate_feature_row(row, prediction_cutoff="2026-09-24T23:30:00Z")
        with self.assertRaises(C.FeatureContractError):
            C.validate_feature_row(row, prediction_cutoff="2026-09-24T22:00:00Z")
        hist = C.feature_row("F8_INJURY_PRACTICE", **_row())
        with self.assertRaises(C.FeatureContractError):
            C.validate_feature_row(hist, prediction_cutoff="2026-09-24T22:00:00Z")

    def test_strictly_prior(self):
        C.assert_strictly_prior([(2024, 4), (2023, 17)], (2024, 5))
        with self.assertRaises(C.FeatureContractError):
            C.assert_strictly_prior([(2024, 5)], (2024, 5))

    def test_status_record_requires_consumer_and_evaluation(self):
        with self.assertRaises(C.FeatureContractError):
            C.status_record("F1_GAME_CONTEXT", milestone="BUILT", consumer=None, evidence="x")
        with self.assertRaises(C.FeatureContractError):
            C.status_record("F1_GAME_CONTEXT", milestone="VALIDATED", consumer="c", evidence="x")
        C.status_record("F1_GAME_CONTEXT", milestone="BLOCKED", consumer=None, evidence="x",
                        blockers=["no source"])


def _player_rows():
    rows = []
    for week in range(1, 11):
        for pid, rec in (("p1", 5.0), ("p2", 2.0)):
            rows.append({"player_id": pid, "season": 2020, "week": week, "season_type": "REG",
                         "game_id": f"g{week}", "team": "A", "opponent_team": "B",
                         "position": "WR", "targets": rec + 1, "receptions": rec,
                         "receiving_yards": 10 * rec, "receiving_tds": 0.0,
                         "receiving_air_yards": 0.0, "target_share": 0.0,
                         "air_yards_share": 0.0, "wopr": 0.0, "carries": 0.0,
                         "rushing_yards": 0.0, "rushing_tds": 0.0, "attempts": 0.0,
                         "completions": 0.0, "passing_yards": 0.0, "passing_tds": 0.0})
    return rows


class HarnessTests(unittest.TestCase):
    def test_b0_needs_three_prior_appearances_and_is_prior_only(self):
        scored = H.b0_rolling_mean(_player_rows(), "receptions")
        p1 = [r for r in scored if r["player_id"] == "p1"]
        self.assertEqual([r["b0"] for r in p1[:4]], [None, None, None, 5.0])

    def test_matched_paired_scoring_and_activation(self):
        scored = H.b0_rolling_mean(_player_rows(), "receptions")
        perfect = {H.row_key(r): r["actual"] for r in scored if r["b0"] is not None}
        res = H.evaluate(scored, perfect, "receptions",
                         partitions={"DEV_2016_2022": (2016, 2022)})["partitions"]["DEV_2016_2022"]
        self.assertEqual(res["n_matched"], 14)
        self.assertAlmostEqual(res["paired_delta_mean"], 0.0)  # constant players: B0 already exact
        self.assertEqual(res["activation_share"], 0.0)

    def test_missing_challenger_rows_are_counted_not_scored(self):
        scored = H.b0_rolling_mean(_player_rows(), "receptions")
        res = H.evaluate(scored, {}, "receptions",
                         partitions={"DEV_2016_2022": (2016, 2022)})["partitions"]["DEV_2016_2022"]
        self.assertEqual(res["n_matched"], 0)
        self.assertEqual(res["n_b0_without_challenger"], 14)

    def test_scale_control_fits_on_dev_only(self):
        scored = [{"season": 2020, "week": 1, "game_id": "g", "player_id": str(i),
                   "b0": 10.0, "actual": 8.0} for i in range(20)]
        self.assertAlmostEqual(H.fit_scale_control(scored, "receptions"), 0.8)

    def test_binary_b0_is_smoothed_never_zero(self):
        rows = [dict(r, rushing_tds=0.0, receiving_tds=0.0, carries=3.0) for r in _player_rows()]
        scored = H.b0_rolling_mean(rows, "anytime_td")
        b0s = [r["b0"] for r in scored if r["b0"] is not None]
        self.assertTrue(b0s and all(0.0 < b < 1.0 for b in b0s))
        self.assertAlmostEqual(b0s[-1], 0.5 / 6.0)

    def test_binary_market_uses_log_loss(self):
        scored = [{"season": 2020, "week": 1, "game_id": f"g{i}", "player_id": "p",
                   "b0": 0.5, "actual": float(i % 2)} for i in range(4)]
        res = H.evaluate(scored, {H.row_key(r): 0.5 for r in scored}, "anytime_td",
                         partitions={"DEV_2016_2022": (2016, 2022)})
        self.assertEqual(res["primary_metric"], "log_loss")


if __name__ == "__main__":
    unittest.main()
