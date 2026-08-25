#!/usr/bin/env python3
"""test_prospective_reporting.py -- coverage for
backtest/prospective_reporting.py, Priority 7 of the
restart-safety-mission directive (2026-08-25). Synthetic fixtures only --
this session's own real live-logged funnel data was lost to the same
container restarts that wiped the canonical backfill, so there is no real
data to test against yet.

    /tmp/mlbvenv/bin/python3 test_prospective_reporting.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import prospective_reporting as pr


def record(candidate_id="2026-08-25:1:100:hits:1", qc_status="confirmed_lineup",
           hit_probability=0.65, n_alt_lines=0, alt_lines=None, blocking_gate=None,
           gates=None, qc_reason=None):
    return {
        "identity": {"candidate_id": candidate_id},
        "prediction": {"hit_probability": hit_probability},
        "decision": {
            "quality_control_status": qc_status, "quality_control_reason": qc_reason,
            "n_alt_lines": n_alt_lines, "alt_lines": alt_lines or [],
            "blocking_gate": blocking_gate, "gates": gates or {},
        },
    }


def outcome(candidate_id, grade="hit"):
    return {"candidate_id": candidate_id, "grade": grade}


class SlateSummaryTests(unittest.TestCase):
    def test_counts_by_qc_status(self):
        records = [record(qc_status="confirmed_lineup"), record(qc_status="rejected"),
                   record(qc_status="rejected")]
        summary = pr.slate_summary(records)
        self.assertEqual(summary["n_total_candidates"], 3)
        self.assertEqual(summary["by_quality_control_status"]["rejected"], 2)

    def test_counts_multi_alt_line_candidates(self):
        records = [record(n_alt_lines=3), record(n_alt_lines=1), record(n_alt_lines=0)]
        summary = pr.slate_summary(records)
        self.assertEqual(summary["n_with_multiple_alt_lines"], 1)


class JoinOutcomesTests(unittest.TestCase):
    def test_joins_by_candidate_id(self):
        records = [record(candidate_id="a"), record(candidate_id="b")]
        outcomes = [outcome("a", "hit")]
        joined = pr.join_outcomes(records, outcomes)
        self.assertEqual(joined["a"][1]["grade"], "hit")
        self.assertIsNone(joined["b"][1])  # no outcome yet, not a crash


class HighestProbabilityRejectedTests(unittest.TestCase):
    def test_only_rejected_included_sorted_descending(self):
        records = [
            record(candidate_id="a", qc_status="rejected", hit_probability=0.55),
            record(candidate_id="b", qc_status="rejected", hit_probability=0.75),
            record(candidate_id="c", qc_status="confirmed_lineup", hit_probability=0.90),
        ]
        result = pr.highest_probability_rejected(records, [], n=10)
        self.assertEqual([r["candidate_id"] for r in result], ["b", "a"])  # c excluded

    def test_n_limits_result_size(self):
        records = [record(candidate_id=str(i), qc_status="rejected", hit_probability=0.5 + i * 0.01)
                   for i in range(20)]
        result = pr.highest_probability_rejected(records, [], n=5)
        self.assertEqual(len(result), 5)

    def test_includes_outcome_when_graded(self):
        records = [record(candidate_id="a", qc_status="rejected", hit_probability=0.70)]
        result = pr.highest_probability_rejected(records, [outcome("a", "miss")])
        self.assertEqual(result[0]["grade"], "miss")


class AlternateLineWinnerComparisonTests(unittest.TestCase):
    def test_board_line_was_the_best_option(self):
        records = [record(hit_probability=0.70,
                           alt_lines=[{"prob": 0.70}, {"prob": 0.55}])]
        result = pr.alternate_line_winner_comparison(records)
        self.assertTrue(result[0]["board_was_highest_prob_option"])

    def test_board_line_was_not_the_best_option(self):
        records = [record(hit_probability=0.55,
                           alt_lines=[{"prob": 0.70}, {"prob": 0.55}])]
        result = pr.alternate_line_winner_comparison(records)
        self.assertFalse(result[0]["board_was_highest_prob_option"])

    def test_single_line_candidates_excluded(self):
        records = [record(alt_lines=[{"prob": 0.60}])]  # only 1 alt line
        result = pr.alternate_line_winner_comparison(records)
        self.assertEqual(result, [])


class GateFailureCountsTests(unittest.TestCase):
    def test_tally_by_blocking_gate(self):
        records = [record(blocking_gate="evidence_ok"), record(blocking_gate="evidence_ok"),
                   record(blocking_gate="lineup_ok"), record(blocking_gate=None)]
        counts = pr.gate_failure_counts(records)
        self.assertEqual(counts["evidence_ok"], 2)
        self.assertEqual(counts["lineup_ok"], 1)
        self.assertEqual(counts[None], 1)


class GateRegretTests(unittest.TestCase):
    def test_only_single_gate_failures_counted(self):
        records = [
            # blocked ONLY by evidence_ok (every other gate True)
            record(candidate_id="a", gates={"has_prob": True, "meets_prob_floor": True,
                                             "evidence_ok": False, "lineup_ok": True}),
            # blocked by TWO gates -- must be excluded from regret attribution
            record(candidate_id="b", gates={"has_prob": True, "meets_prob_floor": False,
                                             "evidence_ok": False, "lineup_ok": True}),
        ]
        outcomes = [outcome("a", "hit"), outcome("b", "miss")]
        result = pr.gate_regret(records, outcomes)
        self.assertEqual(result["evidence_ok"]["n_blocked_solely_by_this_gate"], 1)
        self.assertNotIn("meets_prob_floor", result)  # b excluded, not attributed anywhere

    def test_hit_rate_computed_only_over_graded(self):
        records = [
            record(candidate_id="a", gates={"has_prob": True, "evidence_ok": False}),
            record(candidate_id="b", gates={"has_prob": True, "evidence_ok": False}),
        ]
        outcomes = [outcome("a", "hit")]  # b never graded
        result = pr.gate_regret(records, outcomes)
        self.assertEqual(result["evidence_ok"]["n_blocked_solely_by_this_gate"], 2)
        self.assertEqual(result["evidence_ok"]["n_graded"], 1)
        self.assertEqual(result["evidence_ok"]["hit_rate"], 1.0)

    def test_no_gates_dict_excluded_not_a_crash(self):
        records = [record(candidate_id="a", gates={})]
        result = pr.gate_regret(records, [])
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
