#!/usr/bin/env python3
"""test_candidate_funnel_logger.py -- coverage for
backtest/candidate_funnel_logger.py, the prospective full-candidate research
log built 2026-08-25. Enforces every safety-contract claim in that module's
own docstring: read-only over candidates, alt-line preservation, outcome
kept strictly separate from pregame features, deterministic dedup.

    /tmp/mlbvenv/bin/python3 test_candidate_funnel_logger.py
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import candidate_funnel_logger as cfl


def candidate(**overrides):
    c = {
        "game_pk": 823260, "game_start": "2026-08-25T23:05:00+00:00",
        "player_id": 670541, "name": "Yordan Alvarez", "team": "HOU",
        "matchup": "HOU@SEA", "bet_side": "over",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.71, "raw_hit_probability": 0.68,
        "probability_basis": "empirical_blend", "prob_ci": [0.62, 0.79],
        "sample_n": 412, "base_rate": 0.63, "lift": 0.08,
        "market_odds": -130, "market_implied": 0.565, "market_edge": 0.021,
        "reliability": "A", "score": 74.2, "cat_matchup": 71.0,
        "signals": {"platoon": 80.0},
        "status": "top_pick", "status_reasons": [],
        "line_options": [
            {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.71,
             "base_rate": 0.63, "lift": 0.08, "basis": "empirical_blend", "ci": [0.62, 0.79]},
            {"stat": "total_bases", "needs": 1, "line": 1.5, "prob": 0.58,
             "base_rate": 0.50, "lift": 0.08, "basis": "modelled", "ci": [0.48, 0.68]},
            {"stat": "hits_runs_rbis", "needs": 1, "line": 0.5, "prob": 0.66,
             "base_rate": 0.60, "lift": 0.06, "basis": "empirical_blend", "ci": [0.57, 0.75]},
        ],
    }
    c.update(overrides)
    return c


class CandidateIdentityTests(unittest.TestCase):
    def test_stable_across_repeated_calls(self):
        c = candidate()
        self.assertEqual(cfl.candidate_identity(c, date="2026-08-25"),
                         cfl.candidate_identity(c, date="2026-08-25"))

    def test_differs_for_different_players(self):
        c1 = candidate(player_id=1)
        c2 = candidate(player_id=2)
        self.assertNotEqual(cfl.candidate_identity(c1, date="2026-08-25"),
                            cfl.candidate_identity(c2, date="2026-08-25"))

    def test_differs_for_different_stats(self):
        c1 = candidate(projection={"stat": "hits", "value": 0.5, "needs": 1})
        c2 = candidate(projection={"stat": "total_bases", "value": 1.5, "needs": 1})
        self.assertNotEqual(cfl.candidate_identity(c1, date="2026-08-25"),
                            cfl.candidate_identity(c2, date="2026-08-25"))

    def test_uses_combo_player_ids_when_present(self):
        c = candidate(combo_player_ids=[1, 2], player_id=None)
        cid = cfl.candidate_identity(c, date="2026-08-25")
        self.assertIn("[1, 2]", cid)


class BuildFunnelRecordsTests(unittest.TestCase):
    def test_never_mutates_input_candidates(self):
        # The core safety contract: this module is read-only over
        # candidates. Deep-copy before, deep-copy after, must be identical.
        candidates = [candidate(), candidate(player_id=999, name="Other Batter")]
        before = copy.deepcopy(candidates)
        cfl.build_funnel_records(candidates, date="2026-08-25")
        self.assertEqual(candidates, before)

    def test_multiple_alt_lines_for_the_same_batter_all_survive(self):
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        self.assertEqual(len(records), 1)
        alt_lines = records[0]["decision"]["alt_lines"]
        self.assertEqual(len(alt_lines), 3)
        stats = {a["stat"] for a in alt_lines}
        self.assertEqual(stats, {"hits", "total_bases", "hits_runs_rbis"})
        self.assertEqual(records[0]["decision"]["n_alt_lines"], 3)

    def test_outcome_is_never_a_field_on_a_fresh_record(self):
        # Pregame features and postgame outcome must stay structurally
        # separate -- grading is a later, distinct step.
        record = cfl.build_funnel_records([candidate()], date="2026-08-25")[0]
        self.assertNotIn("outcome", record)

    def test_provenance_captured_when_given(self):
        record = cfl.build_funnel_records(
            [candidate()], date="2026-08-25", code_git_sha="abc1234",
            generated_at="2026-08-25T12:00:00+00:00")[0]
        self.assertEqual(record["provenance"]["code_git_sha"], "abc1234")
        self.assertEqual(record["provenance"]["generated_at"], "2026-08-25T12:00:00+00:00")

    def test_gate_trace_and_quality_control_overlaid_by_identity(self):
        c = candidate()
        cid = cfl.candidate_identity(c, date="2026-08-25")
        gate_traces = {cid: {"gates": {"has_prob": True}, "blocking_gate": "has_odds"}}
        qc_index = {cid: ("assumed_lineup", "lineup not confirmed")}
        record = cfl.build_funnel_records(
            [c], date="2026-08-25", gate_traces=gate_traces,
            quality_control_index=qc_index)[0]
        self.assertEqual(record["decision"]["blocking_gate"], "has_odds")
        self.assertEqual(record["decision"]["quality_control_status"], "assumed_lineup")

    def test_identity_prediction_market_evidence_all_mapped(self):
        record = cfl.build_funnel_records([candidate()], date="2026-08-25")[0]
        self.assertEqual(record["identity"]["player_name"], "Yordan Alvarez")
        self.assertEqual(record["prediction"]["hit_probability"], 0.71)
        self.assertEqual(record["market"]["market_odds"], -130)
        self.assertEqual(record["evidence"]["reliability"], "A")


class ContentHashTests(unittest.TestCase):
    def test_identical_records_hash_identically(self):
        r1 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T12:00:00Z")[0]
        r2 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T12:00:00Z")[0]
        self.assertEqual(cfl.content_hash(r1), cfl.content_hash(r2))

    def test_generated_at_alone_does_not_change_the_hash(self):
        r1 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T12:00:00Z")[0]
        r2 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T18:30:00Z")[0]
        self.assertEqual(cfl.content_hash(r1), cfl.content_hash(r2))

    def test_a_real_probability_change_does_change_the_hash(self):
        r1 = cfl.build_funnel_records([candidate(hit_probability=0.71)], date="2026-08-25")[0]
        r2 = cfl.build_funnel_records([candidate(hit_probability=0.66)], date="2026-08-25")[0]
        self.assertNotEqual(cfl.content_hash(r1), cfl.content_hash(r2))


class AppendNewSnapshotsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "candidate_funnel_2026-08-25.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_write_writes_everything(self):
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        n_written, n_skipped = cfl.append_new_snapshots(records, self.path)
        self.assertEqual(n_written, 1)
        self.assertEqual(n_skipped, 0)
        with open(self.path) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_identical_rerun_writes_nothing_new(self):
        # Deterministic dedup: unchanged candidate -> second run is a no-op.
        records1 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                            generated_at="2026-08-25T12:00:00Z")
        cfl.append_new_snapshots(records1, self.path)
        records2 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                            generated_at="2026-08-25T13:00:00Z")
        n_written, n_skipped = cfl.append_new_snapshots(records2, self.path)
        self.assertEqual(n_written, 0)
        self.assertEqual(n_skipped, 1)
        with open(self.path) as f:
            self.assertEqual(len(f.readlines()), 1)  # still just the one row

    def test_a_real_change_appends_a_new_row_not_a_rewrite(self):
        records1 = cfl.build_funnel_records([candidate(hit_probability=0.71)], date="2026-08-25")
        cfl.append_new_snapshots(records1, self.path)
        records2 = cfl.build_funnel_records([candidate(hit_probability=0.74)], date="2026-08-25")
        n_written, n_skipped = cfl.append_new_snapshots(records2, self.path)
        self.assertEqual(n_written, 1)
        with open(self.path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)  # append-only changelog, not overwritten

    def test_two_different_candidates_both_survive_independently(self):
        c1, c2 = candidate(player_id=1), candidate(player_id=2, name="Other")
        records = cfl.build_funnel_records([c1, c2], date="2026-08-25")
        n_written, _ = cfl.append_new_snapshots(records, self.path)
        self.assertEqual(n_written, 2)
        # Changing only c1 must not re-write c2's already-logged, unchanged row.
        records2 = cfl.build_funnel_records(
            [candidate(player_id=1, hit_probability=0.80), c2], date="2026-08-25")
        n_written2, n_skipped2 = cfl.append_new_snapshots(records2, self.path)
        self.assertEqual(n_written2, 1)
        self.assertEqual(n_skipped2, 1)

    def test_missing_file_is_a_clean_first_run_not_an_error(self):
        self.assertFalse(os.path.exists(self.path))
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        n_written, n_skipped = cfl.append_new_snapshots(records, self.path)
        self.assertEqual(n_written, 1)


class DefaultPathTests(unittest.TestCase):
    def test_path_is_per_date_and_matches_the_gitignored_backtest_glob(self):
        path = cfl.default_path_for_date("2026-08-25", out_dir="/tmp/x")
        self.assertTrue(path.endswith("candidate_funnel_2026-08-25.jsonl"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
