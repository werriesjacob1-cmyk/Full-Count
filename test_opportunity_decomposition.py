#!/usr/bin/env python3
"""test_opportunity_decomposition.py -- coverage for
backtest/opportunity_decomposition.py, Priority 1 of the opportunity-
modeling phase (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_opportunity_decomposition.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import opportunity_decomposition as od


def row(**overrides):
    r = {
        "date": "2026-05-14", "prop_type": "hits", "predicted_prob": 0.62,
        "outcome": 1, "actual_pa": 4, "signals": {"lineup_slot": 100.0},  # order 1
    }
    r.update(overrides)
    return r


class DeriveBattingOrderTests(unittest.TestCase):
    def test_leadoff_scales_to_order_1(self):
        # scale(10-1,1,9) = scale(9,1,9) = 100
        self.assertEqual(od.derive_batting_order(100.0), 1)

    def test_ninth_scales_to_order_9(self):
        # scale(10-9,1,9) = scale(1,1,9) = 0
        self.assertEqual(od.derive_batting_order(0.0), 9)

    def test_middle_of_lineup(self):
        # order=4: scale(6,1,9) = 100*(6-1)/8 = 62.5
        self.assertEqual(od.derive_batting_order(62.5), 4)

    def test_none_returns_none(self):
        self.assertIsNone(od.derive_batting_order(None))

    def test_out_of_range_returns_none_not_a_crash(self):
        self.assertIsNone(od.derive_batting_order(-500.0))
        self.assertIsNone(od.derive_batting_order(9999.0))


class OrderTierTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(od.order_tier(1), "top_1_3")
        self.assertEqual(od.order_tier(3), "top_1_3")
        self.assertEqual(od.order_tier(4), "mid_4_6")
        self.assertEqual(od.order_tier(6), "mid_4_6")
        self.assertEqual(od.order_tier(7), "bottom_7_9")
        self.assertEqual(od.order_tier(9), "bottom_7_9")

    def test_none_is_unknown(self):
        self.assertEqual(od.order_tier(None), "unknown")


class PaBucketFineTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(od.pa_bucket_fine(0), "0")
        self.assertEqual(od.pa_bucket_fine(3), "3")
        self.assertEqual(od.pa_bucket_fine(6), "6+")
        self.assertEqual(od.pa_bucket_fine(8), "6+")

    def test_none_is_unknown(self):
        self.assertEqual(od.pa_bucket_fine(None), "unknown")


class MarketPaCollapseTableTests(unittest.TestCase):
    def test_only_hitter_markets_included(self):
        rows = [row(prop_type="hits"), row(prop_type="strikeouts")]
        table = od.market_pa_collapse_table(rows)
        self.assertIn("hits", table)
        self.assertNotIn("strikeouts", table)

    def test_hit_rate_per_pa_bucket_correct(self):
        rows = [row(actual_pa=1, outcome=1), row(actual_pa=1, outcome=0),
                row(actual_pa=5, outcome=1)]
        table = od.market_pa_collapse_table(rows)
        self.assertEqual(table["hits"]["1"]["hit_rate"], 0.5)
        self.assertEqual(table["hits"]["5"]["hit_rate"], 1.0)


class BattingOrderOpportunityTableTests(unittest.TestCase):
    def test_order_and_avg_pa_computed(self):
        rows = [row(signals={"lineup_slot": 100.0}, actual_pa=5, outcome=1),  # order 1
                row(signals={"lineup_slot": 0.0}, actual_pa=2, outcome=0)]    # order 9
        table = od.batting_order_opportunity_table(rows)
        self.assertEqual(table[1]["avg_actual_pa"], 5.0)
        self.assertEqual(table[1]["hit_rate"], 1.0)
        self.assertEqual(table[9]["avg_actual_pa"], 2.0)
        self.assertEqual(table[9]["hit_rate"], 0.0)

    def test_rows_without_lineup_slot_excluded(self):
        rows = [row(signals={}), row(signals={"lineup_slot": 100.0})]
        table = od.batting_order_opportunity_table(rows)
        self.assertEqual(sum(v["n"] for v in table.values()), 1)


class ControlledOrderTierByProbabilityBucketTests(unittest.TestCase):
    def test_splits_by_bucket_then_tier(self):
        rows = [
            row(predicted_prob=0.62, signals={"lineup_slot": 100.0}, outcome=1),  # top tier
            row(predicted_prob=0.62, signals={"lineup_slot": 0.0}, outcome=0),    # bottom tier
        ]
        report = od.controlled_order_tier_by_probability_bucket(rows)
        cell = report["0.60-0.65"]
        self.assertEqual(cell["top_1_3"]["hit_rate"], 1.0)
        self.assertEqual(cell["bottom_7_9"]["hit_rate"], 0.0)

    def test_market_filter_restricts_rows(self):
        rows = [row(prop_type="hits", predicted_prob=0.62),
                row(prop_type="total_bases", predicted_prob=0.62)]
        report = od.controlled_order_tier_by_probability_bucket(rows, markets={"hits"})
        n_total = sum(v["n"] for v in report["0.60-0.65"].values())
        self.assertEqual(n_total, 1)

    def test_non_hitter_market_excluded_by_default(self):
        rows = [row(prop_type="strikeouts", predicted_prob=0.62)]
        report = od.controlled_order_tier_by_probability_bucket(rows)
        self.assertEqual(report, {})


class YearStabilityTests(unittest.TestCase):
    def test_only_top_and_bottom_tiers_kept(self):
        rows = [row(date="2025-05-01", signals={"lineup_slot": 100.0}, outcome=1),
                row(date="2025-05-01", signals={"lineup_slot": 62.5}, outcome=0)]  # mid tier
        report = od.year_stability_of_order_effect(rows)
        self.assertIn("top_1_3", report["2025"])
        self.assertNotIn("mid_4_6", report["2025"])

    def test_splits_by_year(self):
        rows = [row(date="2024-05-01", signals={"lineup_slot": 100.0}, outcome=1),
                row(date="2025-05-01", signals={"lineup_slot": 0.0}, outcome=0)]
        report = od.year_stability_of_order_effect(rows)
        self.assertIn("2024", report)
        self.assertIn("2025", report)


class BuildReportTests(unittest.TestCase):
    def test_unavailable_fields_explicitly_listed(self):
        report = od.build_report([row()])
        self.assertIn("confirmed_vs_assumed_lineup (LIVE/registry-only field, never on backtest rows)",
                       report["unavailable_from_canonical_rows"])

    def test_ungraded_rows_excluded(self):
        rows = [row(outcome=1), row()]
        rows[1].pop("outcome")
        report = od.build_report(rows)
        self.assertEqual(report["n_graded_rows_total"], 1)

    def test_empty_input_does_not_crash(self):
        report = od.build_report([])
        self.assertEqual(report["n_graded_rows_total"], 0)
        self.assertEqual(report["market_pa_collapse_table"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
