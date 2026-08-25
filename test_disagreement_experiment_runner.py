#!/usr/bin/env python3
"""test_disagreement_experiment_runner.py -- coverage for
backtest/disagreement_experiment_runner.py's OWN orchestration logic
(data_audit, component_dependency_map, reproduction_check,
two_proportion_z, promotion_verdict) -- NOT re-testing the wrapped
modules (disagreement_decomposition.py, disagreement_challenger_model.py
already have their own test suites). Synthetic fixtures only.

Per backtest/disagreement_experiment_protocol.md's own instruction, these
tests validate METHODOLOGY, not a desired outcome -- none of them assert
a specific pp gap or a specific promotion verdict on real data.

    /tmp/mlbvenv/bin/python3 test_disagreement_experiment_runner.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import disagreement_experiment_runner as der


def row(**overrides):
    r = {
        "date": "2024-05-14", "prop_type": "hits_runs_rbis",
        "predicted_prob": 0.62, "outcome": 1, "code_git_sha": "abc123",
        "cat_matchup": 60.0, "cat_recent_form": 55.0,
        "cat_baseline_skill": 70.0, "cat_context": 40.0, "cat_environment": 50.0,
        "score": 65.0,
    }
    r.update(overrides)
    return r


class DataAuditTests(unittest.TestCase):
    def test_counts_and_date_range(self):
        rows = [row(date="2024-04-01"), row(date="2024-04-05")]
        audit = der.data_audit(rows)
        self.assertEqual(audit["n_rows_graded"], 2)
        self.assertEqual(audit["date_range"], ["2024-04-01", "2024-04-05"])

    def test_single_regime_true_for_one_sha(self):
        rows = [row(code_git_sha="abc"), row(code_git_sha="abc")]
        audit = der.data_audit(rows)
        self.assertTrue(audit["single_regime"])

    def test_single_regime_false_for_mixed_sha(self):
        rows = [row(code_git_sha="abc"), row(code_git_sha="xyz")]
        audit = der.data_audit(rows)
        self.assertFalse(audit["single_regime"])

    def test_ungraded_rows_excluded(self):
        rows = [row(outcome=1), row()]
        rows[1].pop("outcome")
        audit = der.data_audit(rows)
        self.assertEqual(audit["n_rows_graded"], 1)


class ComponentDependencyMapTests(unittest.TestCase):
    def test_constant_field_detected(self):
        rows = [row(cat_environment=50.0, cat_baseline_skill=v) for v in range(20, 90, 5)]
        deps = der.component_dependency_map(rows)
        self.assertIn("cat_environment", deps["hits_runs_rbis"]["constant_fields"])
        self.assertNotIn("cat_baseline_skill", deps["hits_runs_rbis"]["constant_fields"])

    def test_perfectly_correlated_fields_show_correlation_near_one(self):
        rows = [row(cat_context=v, score=v) for v in range(10, 90, 5)]
        deps = der.component_dependency_map(rows)
        corr = deps["hits_runs_rbis"]["pairwise_correlations"]["cat_context x score"]
        self.assertGreater(corr, 0.99)

    def test_only_cat_markets_processed(self):
        rows = [row(prop_type="hits_runs_rbis")]
        deps = der.component_dependency_map(rows)
        self.assertEqual(set(deps.keys()), {"hits", "hits_runs_rbis"})


class TwoProportionZTests(unittest.TestCase):
    def test_large_gap_large_n_gives_large_z(self):
        z = der.two_proportion_z(1000, 0.70, 1000, 0.50)
        self.assertGreater(z, 5)

    def test_no_gap_gives_z_near_zero(self):
        z = der.two_proportion_z(1000, 0.60, 1000, 0.60)
        self.assertAlmostEqual(z, 0.0, places=3)

    def test_zero_n_returns_none(self):
        self.assertIsNone(der.two_proportion_z(0, 0.5, 100, 0.5))

    def test_degenerate_pooled_proportion_returns_none(self):
        self.assertIsNone(der.two_proportion_z(100, 1.0, 100, 1.0))


class ReproductionCheckTests(unittest.TestCase):
    def _decomp_report(self, hits_runs_rbis_rates, hits_rates=None):
        return {
            "per_market": {
                "hits_runs_rbis": {"pooled_by_conflict_tier": {
                    t: {"hit_rate": r} for t, r in hits_runs_rbis_rates.items()}},
                "hits": {"pooled_by_conflict_tier": {
                    t: {"hit_rate": r} for t, r in (hits_rates or der.PRE_RESTART_REFERENCE_POOLED["hits"]).items()}},
            }
        }

    def test_matching_rates_within_tolerance_is_reproduced(self):
        report = self._decomp_report(dict(der.PRE_RESTART_REFERENCE_POOLED["hits_runs_rbis"]))
        result = der.reproduction_check(report)
        self.assertEqual(result["per_market"]["hits_runs_rbis"]["verdict"], "REPRODUCED")

    def test_diverged_rates_but_ordering_holds_is_partial(self):
        rates = {"high_empirical_low_context": 0.40, "balanced": 0.60, "high_context_low_empirical": 0.80}
        report = self._decomp_report(rates)
        result = der.reproduction_check(report)
        self.assertEqual(result["per_market"]["hits_runs_rbis"]["verdict"], "PARTIALLY_REPRODUCED")

    def test_ordering_reversed_is_not_reproduced(self):
        rates = {"high_empirical_low_context": 0.80, "balanced": 0.60, "high_context_low_empirical": 0.40}
        report = self._decomp_report(rates)
        result = der.reproduction_check(report)
        self.assertEqual(result["per_market"]["hits_runs_rbis"]["verdict"], "NOT_REPRODUCED")

    def test_overall_is_the_weakest_market_result(self):
        rates = {"high_empirical_low_context": 0.80, "balanced": 0.60, "high_context_low_empirical": 0.40}
        report = self._decomp_report(rates)  # hits_runs_rbis fails; hits (default) reproduces
        result = der.reproduction_check(report)
        self.assertEqual(result["overall"], "NOT_REPRODUCED")


class PromotionVerdictTests(unittest.TestCase):
    def _result(self, current_rate, challenger_rate, added_n, added_rate, removed_n, removed_rate, year_stability=None):
        return {
            "equal_volume": {
                "current_hit_rate": current_rate, "challenger_hit_rate": challenger_rate,
                "n_added_by_challenger": added_n, "added_hit_rate": added_rate,
                "n_removed_by_challenger": removed_n, "removed_hit_rate": removed_rate,
            },
            "z": der.two_proportion_z(added_n, added_rate, removed_n, removed_rate) if added_n and removed_n else None,
            "year_stability": year_stability or {},
        }

    def test_strong_clean_win_earns_shadow(self):
        year_stability = {y: {"added_n": 500, "added_hits": 350, "added_hit_rate": 0.70,
                               "removed_n": 500, "removed_hits": 250, "removed_hit_rate": 0.50}
                           for y in ("2024", "2025", "2026")}
        result = self._result(0.60, 0.65, 1500, 0.70, 1500, 0.50, year_stability)
        verdicts = der.promotion_verdict({"hits_runs_rbis": result})
        self.assertEqual(verdicts["hits_runs_rbis"]["verdict"], "EARNS_SHADOW")

    def test_noise_level_gain_is_closed(self):
        result = self._result(0.60, 0.6022, 4000, 0.625, 4000, 0.6175)
        verdicts = der.promotion_verdict({"hits_runs_rbis": result})
        self.assertEqual(verdicts["hits_runs_rbis"]["verdict"], "CLOSED")
        self.assertTrue(any("z=" in r for r in verdicts["hits_runs_rbis"]["reasons"]))

    def test_negative_net_gain_is_closed(self):
        result = self._result(0.60, 0.55, 500, 0.50, 500, 0.60)
        verdicts = der.promotion_verdict({"hits_runs_rbis": result})
        self.assertEqual(verdicts["hits_runs_rbis"]["verdict"], "CLOSED")

    def test_inconsistent_year_direction_blocks_promotion_even_with_good_pooled_z(self):
        # Pooled numbers look great, but only 1/3 years actually shows added>removed.
        year_stability = {
            "2024": {"added_n": 500, "added_hit_rate": 0.80, "removed_n": 500, "removed_hit_rate": 0.40},
            "2025": {"added_n": 500, "added_hit_rate": 0.40, "removed_n": 500, "removed_hit_rate": 0.80},
            "2026": {"added_n": 500, "added_hit_rate": 0.40, "removed_n": 500, "removed_hit_rate": 0.80},
        }
        result = self._result(0.60, 0.65, 1500, 0.70, 1500, 0.50, year_stability)
        verdicts = der.promotion_verdict({"hits_runs_rbis": result})
        self.assertEqual(verdicts["hits_runs_rbis"]["verdict"], "CLOSED")

    def test_markets_verdicted_independently(self):
        strong = self._result(0.60, 0.70, 1500, 0.75, 1500, 0.45)
        weak = self._result(0.60, 0.601, 100, 0.61, 100, 0.60)
        verdicts = der.promotion_verdict({"hits_runs_rbis": strong, "hits": weak})
        self.assertEqual(verdicts["hits_runs_rbis"]["verdict"], "EARNS_SHADOW")
        self.assertEqual(verdicts["hits"]["verdict"], "CLOSED")


class RunFullExperimentTests(unittest.TestCase):
    def test_missing_file_returns_error_not_a_crash(self):
        report = der.run_full_experiment("/tmp/does-not-exist-disagreement-runner.jsonl")
        self.assertIn("error", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
