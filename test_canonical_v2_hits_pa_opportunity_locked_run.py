#!/usr/bin/env python3
"""Tests for the locked canonical-v2 Hits opportunity experiment."""
from __future__ import annotations

import unittest

from backtest import canonical_v2_hits_pa_opportunity_locked_run as exp


def c(day, ident, current, rank, outcome):
    return {
        "candidate_key": (day, 100, ident, "hits", 0.5, None),
        "date": day,
        "current_prob": current,
        "rank_prob": rank,
        "outcome": outcome,
    }


class DateMatchedEqualVolumeTests(unittest.TestCase):
    def test_preserves_champion_count_on_every_date(self):
        rows = [
            c("2026-04-01", 1, .70, .40, 1),
            c("2026-04-01", 2, .65, .30, 0),
            c("2026-04-01", 3, .55, .90, 1),
            c("2026-04-01", 4, .50, .80, 1),
            c("2026-04-02", 5, .62, .20, 0),
            c("2026-04-02", 6, .59, .95, 1),
        ]
        result = exp.date_matched_compare(rows)
        self.assertEqual(result["selected_n_current"], 3)
        self.assertEqual(result["selected_n_challenger"], 3)
        self.assertEqual(result["selection_count_mismatches"], [])
        by_day = {r["date"]: r for r in result["date_summaries"]}
        self.assertEqual(by_day["2026-04-01"]["current_n"], 2)
        self.assertEqual(by_day["2026-04-01"]["challenger_n"], 2)
        self.assertEqual(by_day["2026-04-02"]["current_n"], 1)
        self.assertEqual(by_day["2026-04-02"]["challenger_n"], 1)
        self.assertEqual(result["added_n"], result["removed_n"])

    def test_tied_challenger_scores_defer_to_current_probability(self):
        rows = [
            c("2026-04-01", 1, .70, .50, 1),
            c("2026-04-01", 2, .65, .50, 0),
            c("2026-04-01", 3, .40, .50, 1),
        ]
        result = exp.date_matched_compare(rows)
        self.assertEqual(result["selected_n_current"], 2)
        self.assertEqual(result["overlap_n"], 2)
        self.assertEqual(result["added_n"], 0)
        self.assertEqual(result["removed_n"], 0)

    def test_duplicate_semantic_candidate_fails_closed(self):
        row = c("2026-04-01", 1, .70, .50, 1)
        with self.assertRaises(exp.ExperimentError):
            exp.date_matched_compare([row, dict(row)])

    def test_date_cluster_bootstrap_is_deterministic(self):
        summaries = [
            {
                "current_n": 10, "challenger_n": 10,
                "current_hits": 5, "challenger_hits": 7,
            },
            {
                "current_n": 10, "challenger_n": 10,
                "current_hits": 6, "challenger_hits": 6,
            },
            {
                "current_n": 10, "challenger_n": 10,
                "current_hits": 4, "challenger_hits": 6,
            },
        ]
        a = exp.date_cluster_bootstrap(summaries, reps=200, seed=7)
        b = exp.date_cluster_bootstrap(summaries, reps=200, seed=7)
        self.assertEqual(a, b)


class PromotionVerdictTests(unittest.TestCase):
    @staticmethod
    def base(delta):
        return {
            "hit_rate_delta": delta,
            "added_vs_removed": {"z": 2.2},
            "date_cluster_bootstrap": {"delta_95pct": [0.002, 0.03]},
            "season_phase_added_removed": {},
            "selection_count_mismatches": [],
        }

    def test_all_locked_criteria_only_earns_shadow(self):
        r = exp.promotion_verdict(self.base(0.001), self.base(0.01))
        self.assertEqual(r["verdict"], "EARNS_PROSPECTIVE_SHADOW")
        self.assertFalse(r["production_promotion_authorized"])

    def test_secondary_cannot_rescue_negative_primary(self):
        r = exp.promotion_verdict(self.base(0.001), self.base(-0.001))
        self.assertEqual(r["verdict"], "CLOSED")
        self.assertTrue(any("not positive" in x for x in r["reasons"]))

    def test_phase_reversal_closes(self):
        y2026 = self.base(0.01)
        y2026["season_phase_added_removed"] = {
            "mid_season_may_jul": {
                "added_n": 250, "removed_n": 250,
                "added_hit_rate": .55, "removed_hit_rate": .60,
            }
        }
        r = exp.promotion_verdict(self.base(0.001), y2026)
        self.assertEqual(r["verdict"], "CLOSED")
        self.assertTrue(any("phase reversal" in x for x in r["reasons"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
