#!/usr/bin/env python3
"""test_canonical_baseline_report.py -- coverage for
backtest/canonical_baseline_report.py, the control-baseline script prepared
2026-08-25 ahead of rows_canonical.jsonl existing. Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_canonical_baseline_report.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import canonical_baseline_report as cbr


def row(**overrides):
    r = {
        "date": "2026-05-14", "game_pk": 1, "player_id": 1, "player_name": "A",
        "prop_type": "hits", "line": 0.5, "needs": 1, "predicted_prob": 0.66,
        "outcome": 1, "actual": 2, "fair_test": True,
    }
    r.update(overrides)
    return r


class SeasonPhaseTests(unittest.TestCase):
    def test_april_is_early_season(self):
        self.assertEqual(cbr.season_phase("2026-04-10"), "early_season_april")

    def test_may_jul_is_mid_season(self):
        for d in ("2026-05-01", "2026-06-15", "2026-07-31"):
            self.assertEqual(cbr.season_phase(d), "mid_season_may_jul")

    def test_september_is_stretch_run(self):
        self.assertEqual(cbr.season_phase("2026-09-15"), "stretch_run_sep_oct")

    def test_malformed_date_is_unknown_not_a_crash(self):
        self.assertEqual(cbr.season_phase("not-a-date"), "unknown")
        self.assertEqual(cbr.season_phase(None), "unknown")


class ProbBucketTests(unittest.TestCase):
    def test_buckets_at_the_configured_width(self):
        self.assertEqual(cbr.prob_bucket(0.66), "0.65-0.70")
        self.assertEqual(cbr.prob_bucket(0.60), "0.60-0.65")

    def test_none_probability_returns_none_not_a_fabricated_bucket(self):
        self.assertIsNone(cbr.prob_bucket(None))


class SampleBucketTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(cbr.sample_bucket(10), "n<30")
        self.assertEqual(cbr.sample_bucket(30), "30<=n<100")
        self.assertEqual(cbr.sample_bucket(100), "100<=n<300")
        self.assertEqual(cbr.sample_bucket(300), "n>=300")

    def test_none_is_explicitly_unknown(self):
        self.assertEqual(cbr.sample_bucket(None), "unknown")


class BuildReportTests(unittest.TestCase):
    def test_coverage_counts_are_correct(self):
        rows = [row(date="2026-04-01"), row(date="2026-04-02", outcome=0)]
        report = cbr.build_report(rows, n_malformed=2)
        self.assertEqual(report["coverage"]["n_rows_total"], 2)
        self.assertEqual(report["coverage"]["n_malformed_lines_skipped"], 2)
        self.assertEqual(report["coverage"]["n_dates"], 2)
        self.assertEqual(report["coverage"]["date_range"], ["2026-04-01", "2026-04-02"])
        self.assertEqual(report["coverage"]["n_graded_rows"], 2)

    def test_market_hit_rate_computed_correctly(self):
        rows = [row(prop_type="hits", outcome=1), row(prop_type="hits", outcome=0),
                row(prop_type="total_bases", outcome=1)]
        report = cbr.build_report(rows, n_malformed=0)
        self.assertEqual(report["markets"]["hits"]["n_graded"], 2)
        self.assertEqual(report["markets"]["hits"]["hit_rate"], 0.5)
        self.assertEqual(report["markets"]["total_bases"]["hit_rate"], 1.0)

    def test_rows_missing_outcome_are_not_counted_as_graded(self):
        rows = [row(outcome=1), row()]
        rows[1].pop("outcome")
        report = cbr.build_report(rows, n_malformed=0)
        self.assertEqual(report["coverage"]["n_graded_rows"], 1)
        self.assertEqual(report["coverage"]["n_rows_missing_outcome_field"], 1)

    def test_probability_bucket_hit_rate(self):
        rows = [row(predicted_prob=0.66, outcome=1), row(predicted_prob=0.68, outcome=0)]
        report = cbr.build_report(rows, n_malformed=0)
        self.assertEqual(report["probability_buckets_reconstructed"]["0.65-0.70"]["n_graded"], 2)
        self.assertEqual(report["probability_buckets_reconstructed"]["0.65-0.70"]["hit_rate"], 0.5)

    def test_reliability_breakdown_only_uses_rows_that_actually_have_it(self):
        rows = [row(reliability="A", outcome=1), row(reliability=None, outcome=0)]
        report = cbr.build_report(rows, n_malformed=0)
        rel = report["evidence"]["reliability_OBSERVED_only_present_on_apply_policy_rows"]
        self.assertEqual(rel["n_rows_with_reliability_field"], 1)
        self.assertEqual(rel["by_grade"]["A"]["hit_rate"], 1.0)

    def test_selection_like_population_uses_real_status_when_present(self):
        rows = [row(recommendation_status="lean", outcome=1),
                row(recommendation_status="neutral", outcome=0)]
        report = cbr.build_report(rows, n_malformed=0)
        pop = report["selection_like_population"]
        self.assertIn("OBSERVED recommendation_status", pop["source"])
        self.assertEqual(pop["by_status"]["lean"]["hit_rate"], 1.0)

    def test_selection_like_population_falls_back_to_labeled_proxy_when_absent(self):
        rows = [row(predicted_prob=0.70, outcome=1), row(predicted_prob=0.50, outcome=0)]
        report = cbr.build_report(rows, n_malformed=0)
        pop = report["selection_like_population"]
        self.assertIn("RECONSTRUCTED proxy", pop["source"])
        self.assertEqual(pop["n_graded"], 1)  # only the 0.70 row clears MIN_LINE_PROB=0.60

    def test_year_and_season_phase_breakdowns_present(self):
        rows = [row(date="2026-04-01", outcome=1), row(date="2026-09-01", outcome=0)]
        report = cbr.build_report(rows, n_malformed=0)
        self.assertIn("2026", report["time_reconstructed"]["by_year"])
        self.assertIn("early_season_april", report["time_reconstructed"]["by_season_phase"])
        self.assertIn("stretch_run_sep_oct", report["time_reconstructed"]["by_season_phase"])

    def test_empty_rows_do_not_crash(self):
        report = cbr.build_report([], n_malformed=0)
        self.assertEqual(report["coverage"]["n_rows_total"], 0)
        self.assertEqual(report["coverage"]["date_range"], [None, None])


class LoadRowsTests(unittest.TestCase):
    def test_malformed_lines_are_counted_and_skipped_not_fatal(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"date": "2026-04-01"}\n')
            f.write("not valid json\n")
            f.write('{"date": "2026-04-02"}\n')
            path = f.name
        try:
            rows, n_malformed = cbr.load_rows(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(n_malformed, 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
