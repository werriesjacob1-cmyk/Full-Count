#!/usr/bin/env python3
"""Contracts for the research-only plus-money / alternate-line evaluation foundation."""
import unittest

from nfl.research.alternate_line_evaluation import (
    AlternateLineEvaluationError,
    UnsupportedMarketError,
    breakeven_probability,
    decimal_payout_per_unit,
    expected_value_from_probability,
    price_bucket,
    require_supported_market,
    settle_selection,
    summarize_price_aware_performance,
)


class PriceBucketTests(unittest.TestCase):
    def test_plus_money_bucket(self):
        self.assertEqual(price_bucket(150), "PLUS_MONEY")

    def test_minus_money_bucket(self):
        self.assertEqual(price_bucket(-150), "MINUS_MONEY")

    def test_boundary_plus_100_is_plus_money(self):
        self.assertEqual(price_bucket(100), "PLUS_MONEY")

    def test_boundary_minus_100_is_minus_money(self):
        self.assertEqual(price_bucket(-100), "MINUS_MONEY")

    def test_zero_odds_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            price_bucket(0)

    def test_odds_inside_dead_zone_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            price_bucket(50)
        with self.assertRaises(AlternateLineEvaluationError):
            price_bucket(-50)


class BreakevenProbabilityTests(unittest.TestCase):
    def test_even_money_is_half(self):
        self.assertAlmostEqual(breakeven_probability(-100), 0.5)
        self.assertAlmostEqual(breakeven_probability(100), 0.5)

    def test_plus_150_breakeven(self):
        self.assertAlmostEqual(breakeven_probability(150), 0.4)

    def test_minus_200_breakeven(self):
        self.assertAlmostEqual(breakeven_probability(-200), 2 / 3)


class DecimalPayoutTests(unittest.TestCase):
    def test_plus_100_pays_double(self):
        self.assertAlmostEqual(decimal_payout_per_unit(100), 2.0)

    def test_plus_200_pays_triple(self):
        self.assertAlmostEqual(decimal_payout_per_unit(200), 3.0)

    def test_minus_200_pays_1_5x(self):
        self.assertAlmostEqual(decimal_payout_per_unit(-200), 1.5)


class SettleSelectionTests(unittest.TestCase):
    def test_hit_at_plus_150_profits_1_5_units(self):
        self.assertAlmostEqual(settle_selection(150, "HIT"), 1.5)

    def test_hit_at_minus_110_profits_correctly(self):
        self.assertAlmostEqual(settle_selection(-110, "hit"), 100 / 110)

    def test_miss_loses_one_unit(self):
        self.assertAlmostEqual(settle_selection(-110, "MISS"), -1.0)

    def test_push_is_zero(self):
        self.assertEqual(settle_selection(-110, "PUSH"), 0.0)

    def test_void_is_zero(self):
        self.assertEqual(settle_selection(150, "VOID"), 0.0)

    def test_unknown_outcome_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            settle_selection(-110, "CANCELLED")


class SummarizePriceAwarePerformanceTests(unittest.TestCase):
    def test_mixed_ledger_aggregates_correctly(self):
        records = [
            {"odds": 150, "outcome": "HIT"},
            {"odds": -110, "outcome": "MISS"},
            {"odds": -200, "outcome": "PUSH"},
            {"odds": 120, "outcome": "VOID"},
        ]
        out = summarize_price_aware_performance(records)
        self.assertEqual(out["n_records"], 4)
        self.assertEqual(out["outcome_counts"]["HIT"], 1)
        self.assertEqual(out["outcome_counts"]["MISS"], 1)
        self.assertEqual(out["outcome_counts"]["PUSH"], 1)
        self.assertEqual(out["outcome_counts"]["VOID"], 1)
        self.assertEqual(out["graded_units"], 2)
        self.assertAlmostEqual(out["hit_rate_on_graded_units"], 0.5)
        self.assertAlmostEqual(out["total_profit_units"], 1.5 - 1.0)
        self.assertAlmostEqual(out["roi_per_graded_unit"], (1.5 - 1.0) / 2)
        self.assertEqual(out["price_bucket_counts"]["PLUS_MONEY"], 2)
        self.assertEqual(out["price_bucket_counts"]["MINUS_MONEY"], 2)

    def test_empty_ledger_reports_none_for_rates_not_zero(self):
        out = summarize_price_aware_performance([])
        self.assertEqual(out["n_records"], 0)
        self.assertIsNone(out["hit_rate_on_graded_units"])
        self.assertIsNone(out["roi_per_graded_unit"])

    def test_unknown_outcome_in_ledger_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            summarize_price_aware_performance([{"odds": -110, "outcome": "WIN"}])

    def test_non_mapping_record_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            summarize_price_aware_performance(["not-a-dict"])


class ExpectedValueFromProbabilityTests(unittest.TestCase):
    def test_edge_positive_when_probability_exceeds_breakeven(self):
        out = expected_value_from_probability(
            0.5, 150, evidence_status="UNVALIDATED_RESEARCH"
        )
        self.assertAlmostEqual(out["breakeven_probability"], 0.4)
        self.assertAlmostEqual(out["edge_vs_breakeven"], 0.1)
        self.assertGreater(out["expected_value_units_per_unit_staked"], 0)

    def test_unvalidated_research_is_always_provisional(self):
        out = expected_value_from_probability(
            0.9, -110, evidence_status="UNVALIDATED_RESEARCH"
        )
        self.assertTrue(out["expected_value_is_provisional"])

    def test_prospectively_validated_is_not_provisional(self):
        out = expected_value_from_probability(
            0.5, 150, evidence_status="PROSPECTIVELY_VALIDATED"
        )
        self.assertFalse(out["expected_value_is_provisional"])

    def test_unknown_evidence_status_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            expected_value_from_probability(0.5, 150, evidence_status="TRUST_ME")

    def test_probability_out_of_range_fails_closed(self):
        with self.assertRaises(AlternateLineEvaluationError):
            expected_value_from_probability(1.5, 150, evidence_status="UNVALIDATED_RESEARCH")


class RequireSupportedMarketTests(unittest.TestCase):
    def test_status_meeting_floor_passes(self):
        require_supported_market("receptions", "TESTED", minimum_status="TESTED")

    def test_status_above_floor_passes(self):
        require_supported_market("passing_yards", "LIVE_SHADOW", minimum_status="TESTED")

    def test_status_below_floor_fails_closed(self):
        with self.assertRaises(UnsupportedMarketError):
            require_supported_market("kicker_special_teams", "UNSUPPORTED", minimum_status="TESTED")

    def test_unknown_status_fails_closed(self):
        with self.assertRaises(UnsupportedMarketError):
            require_supported_market("passing_yards", "PROMOTED")


if __name__ == "__main__":
    unittest.main()
