#!/usr/bin/env python3
"""Tests for nfl.prospective.receptions_paired_grader."""
from __future__ import annotations

import unittest

from nfl.prospective.receptions_challenger_snapshot import build_challenger_snapshot_record
from nfl.prospective.receptions_paired_grader import (
    PairedGradeError,
    grade_paired_receptions_record,
    summarize_paired_grades,
)
from nfl.research.receptions_frozen_challenger import compare_b0_vs_frozen_challenger
from nfl.research.receptions_shadow import score_shadow_candidate

REAL_SHAPE_RESIDUALS = [0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5


def _real_b0_score(projection=4.2, line=3.5):
    return score_shadow_candidate(
        projection=projection, line=line, over_odds=-115, under_odds=-105,
        residuals=REAL_SHAPE_RESIDUALS,
    )


def _real_comparison(b0_score, projection=4.2, line=3.5):
    return compare_b0_vs_frozen_challenger(
        projection=projection, line=line,
        b0_over=b0_score["model_over_probability"],
        b0_under=b0_score["model_under_probability"],
        over_price=-115, under_price=-105,
    )


def _sealed_record(*, decision_status="SHADOW_ONLY", event_id="e1", gsis_id="00-0039918",
                    line=3.5, projection=4.2):
    b0_score = _real_b0_score(projection=projection, line=line)
    comparison = _real_comparison(b0_score, projection=projection, line=line)
    return build_challenger_snapshot_record(
        event_id=event_id, market_id="m1", gsis_id=gsis_id,
        player_name="Test Player", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
        line=line, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
        availability_status="NOT_LISTED_INACTIVE", decision_status=decision_status,
        b0_score=b0_score, challenger_comparison=comparison,
        challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
    )


class GradePairedReceptionsRecordTests(unittest.TestCase):
    def test_real_over_outcome_scores_both_models_via_proper_scoring(self):
        record = _sealed_record(line=3.5)
        outcomes = {"00-0039918": {"receptions": 6.0}}
        result = grade_paired_receptions_record(record, outcomes)
        self.assertIsNotNone(result)
        self.assertEqual(result["actual_side"], "OVER")
        self.assertEqual(result["stat_value"], 6.0)
        for model in ("b0", "challenger"):
            self.assertGreaterEqual(result[model]["brier"], 0.0)
            self.assertLessEqual(result[model]["brier"], 1.0)
            self.assertGreater(result[model]["log_loss"], 0.0)

    def test_real_under_outcome_penalizes_an_overconfident_over_probability(self):
        record = _sealed_record(line=3.5)
        outcomes = {"00-0039918": {"receptions": 1.0}}
        result = grade_paired_receptions_record(record, outcomes)
        self.assertEqual(result["actual_side"], "UNDER")
        # model_over_probability was well above 0.5 for this projection/line,
        # so scoring against a real UNDER outcome must produce a real,
        # non-trivial penalty rather than a near-zero score.
        self.assertGreater(result["b0"]["brier"], 0.1)

    def test_quarantined_record_is_never_graded(self):
        record = _sealed_record(decision_status="QUARANTINED")
        outcomes = {"00-0039918": {"receptions": 6.0}}
        self.assertIsNone(grade_paired_receptions_record(record, outcomes))

    def test_dnp_player_is_never_graded_a_fabricated_result(self):
        record = _sealed_record()
        outcomes = {}  # player did not appear in the final box score at all
        self.assertIsNone(grade_paired_receptions_record(record, outcomes))

    def test_exact_push_has_no_fair_test_for_either_model(self):
        record = _sealed_record(line=3.5)
        outcomes = {"00-0039918": {"receptions": 3.5}}
        self.assertIsNone(grade_paired_receptions_record(record, outcomes))

    def test_rejects_a_record_missing_real_probability_shape(self):
        with self.assertRaises(PairedGradeError):
            grade_paired_receptions_record(
                {"decision_status": "SHADOW_ONLY", "b0_score": {}, "challenger_comparison": {}},
                {},
            )

    def test_rejects_a_non_mapping_record(self):
        with self.assertRaises(PairedGradeError):
            grade_paired_receptions_record("not a record", {})


class SummarizePairedGradesTests(unittest.TestCase):
    def test_empty_input_reports_honestly_rather_than_fabricating_a_rate(self):
        self.assertEqual(summarize_paired_grades([]), {"n": 0, "b0": None, "challenger": None})

    def test_none_entries_are_excluded_from_the_summary(self):
        record = _sealed_record(line=3.5)
        graded = grade_paired_receptions_record(record, {"00-0039918": {"receptions": 6.0}})
        summary = summarize_paired_grades([graded, None, None])
        self.assertEqual(summary["n"], 1)

    def test_matched_volume_and_win_counts_are_internally_consistent(self):
        rows = []
        for line, actual in ((3.5, 6.0), (2.5, 0.0), (4.5, 5.0)):
            record = _sealed_record(line=line, event_id=f"e-{line}")
            rows.append(grade_paired_receptions_record(record, {"00-0039918": {"receptions": actual}}))
        summary = summarize_paired_grades(rows)
        self.assertEqual(summary["n"], 3)
        self.assertEqual(
            summary["b0_better_brier_count"]
            + summary["challenger_better_brier_count"]
            + summary["tied_brier_count"],
            3,
        )
        self.assertIn("mean_brier", summary["b0"])
        self.assertIn("mean_log_loss", summary["challenger"])


if __name__ == "__main__":
    unittest.main()
