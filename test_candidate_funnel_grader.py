#!/usr/bin/env python3
"""test_candidate_funnel_grader.py -- coverage for
backtest/candidate_funnel_grader.py, the outcome-join grader built
2026-08-25 as Priority 2 item 3 (later outcome join / grading readiness).
Synthetic fixtures + mocked grade_results calls only -- no network.

    /tmp/mlbvenv/bin/python3 test_candidate_funnel_grader.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import candidate_funnel_grader as cfg
import candidate_funnel_logger as cfl
import prospective_durability as pdur


def funnel_record(candidate_id="2026-08-25:1:100:hits:2", game_pk=1, player_id=100,
                   combo_player_ids=None, type_="batter", stat="hits", needs=2,
                   threshold=1.5, side="over", qc_status="confirmed_lineup", **overrides):
    record = {
        "identity": {
            "candidate_id": candidate_id, "date": "2026-08-25", "game_pk": game_pk,
            "game_start": "2026-08-25T23:30:00Z", "type": type_, "player_id": player_id,
            "combo_player_ids": combo_player_ids, "player_name": "Fixture Player",
            "team": "A", "matchup": "A @ B", "stat": stat, "side": side,
            "threshold": threshold, "needs": needs,
        },
        "prediction": {"hit_probability": 0.66},
        "market": {},
        "evidence": {},
        "decision": {"quality_control_status": qc_status, "quality_control_reason": None},
        "provenance": {"generated_at": "2026-08-25T20:00:00Z"},
    }
    record.update(overrides)
    return record


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class LoadLatestRecordsTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(cfg.load_latest_records("/tmp/does-not-exist-xyz.jsonl"), {})

    def test_later_snapshot_supersedes_earlier_for_same_identity(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            write_jsonl(path, [
                funnel_record(threshold=1.5, qc_status="rejected"),
                funnel_record(threshold=1.5, qc_status="confirmed_lineup"),
            ])
            latest = cfg.load_latest_records(path)
            self.assertEqual(len(latest), 1)
            rec = next(iter(latest.values()))
            self.assertEqual(rec["decision"]["quality_control_status"], "confirmed_lineup")
        finally:
            os.unlink(path)

    def test_two_distinct_candidates_both_survive(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            write_jsonl(path, [
                funnel_record(candidate_id="a", player_id=100),
                funnel_record(candidate_id="b", player_id=200),
            ])
            self.assertEqual(len(cfg.load_latest_records(path)), 2)
        finally:
            os.unlink(path)

    def test_malformed_lines_skipped_not_fatal(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not valid json\n")
            f.write(json.dumps(funnel_record()) + "\n")
            path = f.name
        try:
            self.assertEqual(len(cfg.load_latest_records(path)), 1)
        finally:
            os.unlink(path)


class PickFromFunnelRecordTests(unittest.TestCase):
    def test_reconstructs_grade_pick_compatible_shape(self):
        record = funnel_record(type_="pitcher", stat="strikeouts", needs=5, side="over")
        pick = cfg.pick_from_funnel_record(record)
        self.assertEqual(pick["type"], "pitcher")
        self.assertEqual(pick["game_pk"], 1)
        self.assertEqual(pick["player_id"], 100)
        self.assertEqual(pick["projection"]["stat"], "strikeouts")
        self.assertEqual(pick["projection"]["needs"], 5)
        self.assertEqual(pick["bet_side"], "over")

    def test_combo_player_ids_carried_through_for_combined_strikeouts(self):
        record = funnel_record(type_="pitcher", stat="combined_strikeouts",
                                combo_player_ids=[100, 200], needs=9)
        pick = cfg.pick_from_funnel_record(record)
        self.assertEqual(pick["combo_player_ids"], [100, 200])

    def test_never_mutates_input_record(self):
        record = funnel_record()
        import copy
        before = copy.deepcopy(record)
        cfg.pick_from_funnel_record(record)
        self.assertEqual(record, before)


FINAL = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}


class GradeDateTests(unittest.TestCase):
    def test_rejected_candidates_are_graded_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_jsonl(cfg.funnel_path_for_date("2026-08-25", tmp), [
                funnel_record(candidate_id="rej1", qc_status="rejected", stat="hits", needs=2),
            ])
            with mock.patch.object(cfg.gr, "fetch_game_contexts", return_value={1: {"status": FINAL}}), \
                 mock.patch.object(cfg.gr, "grade_pick",
                                    return_value={"grade": "hit", "actual": 3}):
                outcomes, n_read = cfg.grade_date("2026-08-25", out_dir=tmp)
            self.assertEqual(n_read, 1)
            self.assertEqual(outcomes[0]["grade"], "hit")
            self.assertEqual(outcomes[0]["quality_control_status"], "rejected")

    def test_kept_and_rejected_and_assumed_lineup_all_graded_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_jsonl(cfg.funnel_path_for_date("2026-08-25", tmp), [
                funnel_record(candidate_id="a", qc_status="confirmed_lineup"),
                funnel_record(candidate_id="b", qc_status="rejected", player_id=200),
                funnel_record(candidate_id="c", qc_status="assumed_lineup", player_id=300),
            ])
            with mock.patch.object(cfg.gr, "fetch_game_contexts", return_value={1: {"status": FINAL}}), \
                 mock.patch.object(cfg.gr, "grade_pick",
                                    return_value={"grade": "miss", "actual": 0}):
                outcomes, n_read = cfg.grade_date("2026-08-25", out_dir=tmp)
            self.assertEqual(n_read, 3)
            self.assertEqual({o["candidate_id"] for o in outcomes}, {"a", "b", "c"})

    def test_no_funnel_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcomes, n_read = cfg.grade_date("2026-08-25", out_dir=tmp)
            self.assertEqual(outcomes, [])
            self.assertEqual(n_read, 0)

    def test_game_not_final_yields_ungraded_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_jsonl(cfg.funnel_path_for_date("2026-08-25", tmp), [funnel_record()])
            with mock.patch.object(cfg.gr, "fetch_game_contexts", return_value={}), \
                 mock.patch.object(cfg.gr, "grade_pick",
                                    return_value={"grade": "ungraded", "reason": "game not final yet"}):
                outcomes, n_read = cfg.grade_date("2026-08-25", out_dir=tmp)
            self.assertEqual(outcomes[0]["grade"], "ungraded")


class DurableSnapshotGradingTests(unittest.TestCase):
    def _snapshot(self, rows, observed_at):
        return cfl.build_snapshot_manifest(
            rows, date="2026-08-25", observed_at=observed_at,
            code_git_sha="a" * 40)

    def test_union_grades_candidates_seen_in_any_durable_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            a1 = funnel_record(candidate_id="a", player_id=100)
            a2 = funnel_record(candidate_id="a", player_id=100)
            b2 = funnel_record(candidate_id="b", player_id=200)
            s1 = self._snapshot([a1], "2026-08-25T17:00:00Z")
            s2 = self._snapshot([a2, b2], "2026-08-25T18:00:00Z")
            pdur.materialize_snapshot([a1], s1, tmp)
            pdur.materialize_snapshot([a2, b2], s2, tmp)

            records, n_snapshots = cfg.load_materialized_date_records(
                tmp, "2026-08-25")
            self.assertEqual(n_snapshots, 2)
            self.assertEqual(set(records), {"a", "b"})

            with mock.patch.object(
                    cfg.gr, "fetch_game_contexts",
                    return_value={1: {"status": FINAL}}), \
                 mock.patch.object(
                    cfg.gr, "grade_pick",
                    return_value={"grade": "hit", "actual": 2}):
                outcomes, n_read = cfg.grade_materialized_date(
                    tmp, "2026-08-25")
            self.assertEqual(n_read, 2)
            self.assertEqual({o["candidate_id"] for o in outcomes}, {"a", "b"})
            self.assertTrue(all(o["grade"] == "hit" for o in outcomes))

    def test_settlement_identity_drift_under_same_candidate_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a1 = funnel_record(
                candidate_id="a", player_id=100, threshold=1.5, needs=2)
            a2 = funnel_record(
                candidate_id="a", player_id=100, threshold=2.5, needs=2)
            s1 = self._snapshot([a1], "2026-08-25T17:00:00Z")
            s2 = self._snapshot([a2], "2026-08-25T18:00:00Z")
            pdur.materialize_snapshot([a1], s1, tmp)
            pdur.materialize_snapshot([a2], s2, tmp)
            with self.assertRaises(RuntimeError):
                cfg.load_materialized_date_records(tmp, "2026-08-25")

    def test_no_ephemeral_funnel_file_is_required_for_durable_grading(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = funnel_record(candidate_id="durable-only", player_id=100)
            snap = self._snapshot([row], "2026-08-25T17:00:00Z")
            pdur.materialize_snapshot([row], snap, tmp)

            self.assertFalse(os.path.exists(
                cfg.funnel_path_for_date("2026-08-25", tmp)))
            with mock.patch.object(
                    cfg.gr, "fetch_game_contexts",
                    return_value={1: {"status": FINAL}}), \
                 mock.patch.object(
                    cfg.gr, "grade_pick",
                    return_value={"grade": "miss", "actual": 0}):
                outcomes, n_read = cfg.grade_materialized_date(
                    tmp, "2026-08-25")
            self.assertEqual(n_read, 1)
            self.assertEqual(outcomes[0]["candidate_id"], "durable-only")
            self.assertEqual(outcomes[0]["grade"], "miss")


class WriteOutcomesTests(unittest.TestCase):
    def test_writes_one_line_per_outcome_and_never_touches_pregame_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pregame_path = cfg.funnel_path_for_date("2026-08-25", tmp)
            write_jsonl(pregame_path, [funnel_record()])
            with open(pregame_path, encoding="utf-8") as fh:
                pregame_before = fh.read()

            out_path = cfg.outcomes_path_for_date("2026-08-25", tmp)
            n = cfg.write_outcomes(
                [{"candidate_id": "a", "date": "2026-08-25", "grade": "hit"}], out_path)
            self.assertEqual(n, 1)

            with open(pregame_path, encoding="utf-8") as fh:
                pregame_after = fh.read()
            self.assertEqual(pregame_before, pregame_after)

            with open(out_path, encoding="utf-8") as fh:
                lines = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["candidate_id"], "a")

    def test_append_only_across_two_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = cfg.outcomes_path_for_date("2026-08-25", tmp)
            cfg.write_outcomes([{"candidate_id": "a"}], out_path)
            cfg.write_outcomes([{"candidate_id": "b"}], out_path)
            with open(out_path, encoding="utf-8") as fh:
                lines = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
