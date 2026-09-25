#!/usr/bin/env python3
"""Contracts for the passing-yards alternate-line ladder research module."""
import unittest

from nfl.research.alternate_line_evaluation import AlternateLineEvaluationError
from nfl.research.passing_yards_alt_ladder import (
    PassingYardsAltLadderError,
    build_passing_yards_ladder_record,
    compare_empirical_vs_normal,
    evaluate_ladder_with_prices,
    ladder_probabilities,
    normal_ladder_probabilities,
)
from nfl.research.receptions_alt_ladder import ReceptionsAltLadderError
from nfl.research.receptions_outcome_distribution import EmpiricalResidualPool


def build_pool(values):
    return EmpiricalResidualPool(values)


class LadderProbabilitiesTests(unittest.TestCase):
    def setUp(self):
        # A real-shaped residual pool around a QB with projection 250.0:
        # symmetric-ish outcomes from 150 to 350 with more mass near center.
        self.pool_values = (
            [-100.0] * 20
            + [-50.0] * 60
            + [-25.0] * 120
            + [0.0] * 200
            + [25.0] * 120
            + [50.0] * 60
            + [100.0] * 20
        )
        self.pool = build_pool(self.pool_values)

    def test_over_probabilities_are_monotonically_non_increasing(self):
        result = ladder_probabilities(
            projection=250.0,
            thresholds=[174.5, 199.5, 224.5, 249.5, 274.5, 299.5, 324.5],
            residual_pool=self.pool,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier, later)
        self.assertGreater(overs[0], overs[-1])

    def test_thresholds_supplied_out_of_order_still_verify_correctly(self):
        result = ladder_probabilities(
            projection=250.0, thresholds=[299.5, 199.5, 249.5], residual_pool=self.pool,
        )
        thresholds_in_result = [rung["threshold"] for rung in result["rungs"]]
        self.assertEqual(thresholds_in_result, sorted(thresholds_in_result))

    def test_every_rung_sums_to_one_within_tolerance(self):
        result = ladder_probabilities(
            projection=250.0, thresholds=[199.5, 224.5, 249.5, 274.5], residual_pool=self.pool,
        )
        for rung in result["rungs"]:
            self.assertAlmostEqual(rung["over"] + rung["under"] + rung["push"], 1.0, places=9)

    def test_exact_push_is_detected_at_an_integer_threshold(self):
        # threshold 250 with projection 250 -> gap 0.0, which the pool has
        # 200 real observations exactly equal to.
        result = ladder_probabilities(projection=250.0, thresholds=[250.0], residual_pool=self.pool)
        rung = result["rungs"][0]
        self.assertEqual(rung["push_observations"], 200)
        self.assertGreater(rung["push"], 0.0)

    def test_half_point_threshold_has_zero_push_by_construction(self):
        # No real residual in this pool lands on a non-integer gap, so a
        # standard half-point sportsbook line has push probability at the
        # Laplace floor only (matching receptions_alt_ladder's own
        # convention of never forcing a rung to exact zero).
        result = ladder_probabilities(projection=250.0, thresholds=[249.5], residual_pool=self.pool)
        rung = result["rungs"][0]
        self.assertEqual(rung["push_observations"], 0)

    def test_empty_thresholds_raise(self):
        with self.assertRaises(ReceptionsAltLadderError):
            ladder_probabilities(projection=250.0, thresholds=[], residual_pool=self.pool)

    def test_probability_zero_or_below_is_near_zero_for_a_real_passing_role_qb(self):
        # Unlike receptions, a real passing-role QB population essentially
        # never throws for <= 0 yards -- confirming the module docstring's
        # disclosed expectation, not merely asserting a tautology.
        result = ladder_probabilities(projection=250.0, thresholds=[249.5], residual_pool=self.pool)
        self.assertFalse(result["zero_mass_is_material"])
        self.assertLess(result["probability_zero_or_below"], 0.01)

    def test_accepts_a_plain_sequence_not_only_a_prebuilt_pool(self):
        result = ladder_probabilities(projection=250.0, thresholds=[249.5], residual_pool=self.pool_values)
        self.assertEqual(result["residual_pool_n"], len(self.pool_values))


class NormalLadderProbabilitiesTests(unittest.TestCase):
    def test_symmetric_normal_gives_half_over_half_under_at_the_mean(self):
        # A non-integer threshold placed exactly at the mean has z=0, so a
        # symmetric Normal must split over/under exactly 50/50.
        result = normal_ladder_probabilities(
            projection=250.5, thresholds=[250.5], mean_residual=0.0, std=40.0,
        )
        rung = result["rungs"][0]
        self.assertAlmostEqual(rung["over"], rung["under"], places=9)
        self.assertAlmostEqual(rung["over"], 0.5, places=9)

    def test_over_probabilities_are_monotonically_non_increasing(self):
        result = normal_ladder_probabilities(
            projection=250.0,
            thresholds=[199.5, 224.5, 249.5, 274.5, 299.5],
            mean_residual=0.0,
            std=45.0,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier, later)

    def test_every_rung_sums_to_one_within_tolerance(self):
        result = normal_ladder_probabilities(
            projection=250.0, thresholds=[199.5, 249.5, 299.5], mean_residual=5.0, std=50.0,
        )
        for rung in result["rungs"]:
            self.assertAlmostEqual(rung["over"] + rung["under"] + rung["push"], 1.0, places=9)

    def test_integer_threshold_has_nonzero_push_mass(self):
        result = normal_ladder_probabilities(
            projection=250.0, thresholds=[250.0], mean_residual=0.0, std=45.0,
        )
        self.assertGreater(result["rungs"][0]["push"], 0.0)

    def test_non_integer_threshold_has_zero_push_mass(self):
        result = normal_ladder_probabilities(
            projection=250.0, thresholds=[250.5], mean_residual=0.0, std=45.0,
        )
        self.assertEqual(result["rungs"][0]["push"], 0.0)

    def test_non_positive_std_raises(self):
        with self.assertRaises(PassingYardsAltLadderError):
            normal_ladder_probabilities(projection=250.0, thresholds=[249.5], mean_residual=0.0, std=0.0)

    def test_mean_residual_shifts_the_center(self):
        # A positive mean bias (model underprojects on average historically)
        # must shift more real mass above a fixed threshold, not less.
        unbiased = normal_ladder_probabilities(
            projection=250.0, thresholds=[249.5], mean_residual=0.0, std=45.0,
        )
        biased_up = normal_ladder_probabilities(
            projection=250.0, thresholds=[249.5], mean_residual=20.0, std=45.0,
        )
        self.assertGreater(biased_up["rungs"][0]["over"], unbiased["rungs"][0]["over"])


class CompareEmpiricalVsNormalTests(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool(
            [-80.0] * 30 + [-40.0] * 60 + [0.0] * 100 + [40.0] * 60 + [80.0] * 30
        )

    def test_scores_both_methods_at_the_identical_thresholds(self):
        result = compare_empirical_vs_normal(
            projection=250.0,
            thresholds=[199.5, 249.5, 299.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
        )
        empirical_thresholds = [r["threshold"] for r in result["empirical"]["rungs"]]
        normal_thresholds = [r["threshold"] for r in result["normal_control"]["rungs"]]
        self.assertEqual(empirical_thresholds, normal_thresholds)
        self.assertEqual(len(result["rung_gaps"]), 3)

    def test_rung_gaps_are_non_negative_and_real(self):
        result = compare_empirical_vs_normal(
            projection=250.0,
            thresholds=[249.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
        )
        gap = result["rung_gaps"][0]
        self.assertGreaterEqual(gap["absolute_over_probability_gap"], 0.0)

    def test_different_std_changes_the_gap_proving_normal_control_is_live_not_a_stub(self):
        # Same empirical pool and thresholds, only `std` changes -- the gap
        # must change too, proving the Normal control's own parameters are
        # actually consumed by the comparison, not ignored.
        tight = compare_empirical_vs_normal(
            projection=250.0, thresholds=[249.5], residual_pool=self.pool, mean_residual=0.0, std=10.0,
        )
        wide = compare_empirical_vs_normal(
            projection=250.0, thresholds=[249.5], residual_pool=self.pool, mean_residual=0.0, std=200.0,
        )
        self.assertNotEqual(
            tight["rung_gaps"][0]["absolute_over_probability_gap"],
            wide["rung_gaps"][0]["absolute_over_probability_gap"],
        )


class EvaluateLadderWithPricesTests(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool([-40.0] * 30 + [-20.0] * 40 + [0.0] * 60 + [20.0] * 40 + [40.0] * 30)

    def test_wires_through_alternate_line_evaluation_functions_exactly(self):
        from nfl.research.alternate_line_evaluation import (
            breakeven_probability,
            expected_value_from_probability,
            price_bucket,
        )

        result = evaluate_ladder_with_prices(
            projection=250.0,
            priced_rungs=[{"threshold": 249.5, "over_odds": -120, "under_odds": 105}],
            residual_pool=self.pool,
        )
        rung = result["priced_rungs"][0]
        expected_over_ev = expected_value_from_probability(
            rung["model_over_probability"], -120, evidence_status="UNVALIDATED_RESEARCH"
        )
        self.assertEqual(rung["over_price"]["price_bucket"], price_bucket(-120))
        self.assertEqual(rung["over_price"]["breakeven_probability"], breakeven_probability(-120))
        self.assertAlmostEqual(
            rung["over_price"]["expected_value_units_per_unit_staked"],
            expected_over_ev["expected_value_units_per_unit_staked"],
        )
        self.assertTrue(rung["over_price"]["expected_value_is_provisional"])

    def test_never_fabricates_a_missing_price(self):
        result = evaluate_ladder_with_prices(
            projection=250.0,
            priced_rungs=[{"threshold": 249.5, "over_odds": -120}],
            residual_pool=self.pool,
        )
        rung = result["priced_rungs"][0]
        self.assertIsNotNone(rung["over_price"])
        self.assertIsNone(rung["under_price"])

    def test_invalid_odds_propagate_the_underlying_error_not_a_reimplementation(self):
        with self.assertRaises(AlternateLineEvaluationError):
            evaluate_ladder_with_prices(
                projection=250.0,
                priced_rungs=[{"threshold": 249.5, "over_odds": 50}],
                residual_pool=self.pool,
            )

    def test_empty_priced_rungs_raises(self):
        with self.assertRaises(PassingYardsAltLadderError):
            evaluate_ladder_with_prices(projection=250.0, priced_rungs=[], residual_pool=self.pool)


class BuildPassingYardsLadderRecordTests(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool([-40.0] * 30 + [-20.0] * 40 + [0.0] * 60 + [20.0] * 40 + [40.0] * 30)
        self.appearances = [
            {"passing_yards": 240.0, "attempts": 32, "team": "GB"},
            {"passing_yards": 260.0, "attempts": 34, "team": "GB"},
            {"passing_yards": 255.0, "attempts": 31, "team": "GB"},
        ]

    def test_end_to_end_record_uses_the_real_b0_projection(self):
        record = build_passing_yards_ladder_record(
            player_id="qb-1",
            prior_appearances=self.appearances,
            thresholds=[224.5, 249.5, 274.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
        )
        self.assertEqual(record["status"], "RESEARCH_ONLY_NOT_PROMOTED")
        expected_projection = sum(a["passing_yards"] for a in self.appearances) / 3.0
        self.assertAlmostEqual(record["b0"]["projection"], expected_projection)
        self.assertAlmostEqual(record["comparison"]["projection"], expected_projection)

    def test_abstains_on_insufficient_history_rather_than_raising(self):
        record = build_passing_yards_ladder_record(
            player_id="qb-2",
            prior_appearances=self.appearances[:2],  # only 2, B0 needs >= 3
            thresholds=[249.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
        )
        self.assertEqual(record["status"], "ABSTAIN_INSUFFICIENT_B0_HISTORY")
        self.assertNotIn("comparison", record)

    def test_zero_attempt_history_abstains_rather_than_projecting_from_no_role(self):
        no_role = [
            {"passing_yards": 0.0, "attempts": 0, "team": "GB"},
            {"passing_yards": 0.0, "attempts": 0, "team": "GB"},
            {"passing_yards": 0.0, "attempts": 0, "team": "GB"},
        ]
        record = build_passing_yards_ladder_record(
            player_id="qb-3",
            prior_appearances=no_role,
            thresholds=[249.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
        )
        self.assertEqual(record["status"], "ABSTAIN_INSUFFICIENT_B0_HISTORY")

    def test_attaches_priced_empirical_only_when_priced_rungs_supplied(self):
        with_prices = build_passing_yards_ladder_record(
            player_id="qb-1",
            prior_appearances=self.appearances,
            thresholds=[249.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
            priced_rungs=[{"threshold": 249.5, "over_odds": -110, "under_odds": -110}],
        )
        self.assertIn("priced_empirical", with_prices)

        without_prices = build_passing_yards_ladder_record(
            player_id="qb-1",
            prior_appearances=self.appearances,
            thresholds=[249.5],
            residual_pool=self.pool,
            mean_residual=0.0,
            std=45.0,
        )
        self.assertNotIn("priced_empirical", without_prices)

    def test_different_prior_appearances_produce_a_different_real_record(self):
        # Proves the record-builder actually consumes real prior history,
        # not a fixed/stubbed projection.
        higher_appearances = [
            {"passing_yards": 340.0, "attempts": 40, "team": "GB"},
            {"passing_yards": 360.0, "attempts": 41, "team": "GB"},
            {"passing_yards": 355.0, "attempts": 39, "team": "GB"},
        ]
        low_record = build_passing_yards_ladder_record(
            player_id="qb-1", prior_appearances=self.appearances, thresholds=[249.5],
            residual_pool=self.pool, mean_residual=0.0, std=45.0,
        )
        high_record = build_passing_yards_ladder_record(
            player_id="qb-1", prior_appearances=higher_appearances, thresholds=[249.5],
            residual_pool=self.pool, mean_residual=0.0, std=45.0,
        )
        self.assertNotAlmostEqual(
            low_record["comparison"]["empirical"]["rungs"][0]["over"],
            high_record["comparison"]["empirical"]["rungs"][0]["over"],
        )


if __name__ == "__main__":
    unittest.main()
