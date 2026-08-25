#!/usr/bin/env python3
"""test_backfill_resume.py -- Priority 3 of the restart-safety phase
(2026-08-25): proves backtest/engine.py's run_backtest() is already
genuinely interruption-resumable (state-file checkpointing +
dates_already_in_output()'s "trust the real output file over the
bookkeeping" belt-and-braces check), and locks in the one hardening this
session added on top of it: a single atomic write() per date instead of
a per-row write loop, which narrows the crash window that could leave a
date PARTIALLY written yet silently treated as "done" on resume.

Mocks backtest.engine.simulate_date entirely -- no real network/Statcast
access, deterministic, fast. This does not re-test simulate_date() itself
(test_backtest_engine.py's own docstring already explains that needs real
API access) -- only run_backtest()'s checkpoint/resume orchestration
around it.

    /tmp/mlbvenv/bin/python3 test_backfill_resume.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import engine as be


def date_result(date, status="ok", rows=None, reason=None, n_games=1):
    r = be.DateResult(date)
    r.status = status
    r.reason = reason
    r.n_games = n_games
    r.n_candidates = len(rows) if rows else 0
    if rows is not None:
        r.rows = rows
    return r


def rows_for(date, n=3):
    return [{"date": date, "game_pk": 1, "player_id": i, "prop_type": "hits",
              "needs": 1, "predicted_prob": 0.6, "outcome": i % 2,
              "code_git_sha": "abc123"} for i in range(n)]


class FreshRunTests(unittest.TestCase):
    def test_processes_all_dates_and_writes_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            results = {"2024-04-01": date_result("2024-04-01", rows=rows_for("2024-04-01")),
                       "2024-04-02": date_result("2024-04-02", rows=rows_for("2024-04-02"))}
            with mock.patch.object(be, "simulate_date", side_effect=lambda d, *a, **kw: results[d]), \
                 mock.patch.object(be, "StatcastStore"):
                summary, state = be.run_backtest("2024-04-01", "2024-04-02", out,
                                                  store=object(), sleep=0, verbose=False)
            self.assertEqual(summary["completed"], ["2024-04-01", "2024-04-02"])
            with open(out) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 6)

    def test_no_games_and_failed_dates_recorded_distinctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            results = {"2024-04-01": date_result("2024-04-01", status="no_games"),
                       "2024-04-02": date_result("2024-04-02", status="failed", reason="API down")}
            with mock.patch.object(be, "simulate_date", side_effect=lambda d, *a, **kw: results[d]), \
                 mock.patch.object(be, "StatcastStore"):
                summary, state = be.run_backtest("2024-04-01", "2024-04-02", out,
                                                  store=object(), sleep=0, verbose=False)
            self.assertEqual(summary["no_games"], ["2024-04-01"])
            self.assertEqual(summary["failed"], {"2024-04-02": "API down"})
            self.assertEqual(state["dates"]["2024-04-01"]["status"], "no_games")
            self.assertEqual(state["dates"]["2024-04-02"]["status"], "failed")


class InterruptionResumeTests(unittest.TestCase):
    def test_resume_skips_completed_dates_without_recomputing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            all_dates = ["2024-04-01", "2024-04-02", "2024-04-03"]
            results = {d: date_result(d, rows=rows_for(d)) for d in all_dates}

            # First "session": only process the first date, then die (simulate
            # interruption by just not processing the rest -- call run_backtest
            # with a truncated date range to model a crash after date 1).
            with mock.patch.object(be, "simulate_date", side_effect=lambda d, *a, **kw: results[d]), \
                 mock.patch.object(be, "StatcastStore"):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)

            # Second "session": resume with the FULL range. The already-done
            # date must be skipped (not recomputed), and the mock must never
            # be called for it again.
            call_log = []
            def tracking_side_effect(d, *a, **kw):
                call_log.append(d)
                return results[d]
            with mock.patch.object(be, "simulate_date", side_effect=tracking_side_effect), \
                 mock.patch.object(be, "StatcastStore"):
                summary, state = be.run_backtest("2024-04-01", "2024-04-03", out,
                                                  store=object(), sleep=0, verbose=False)

            self.assertNotIn("2024-04-01", call_log)  # never recomputed
            self.assertEqual(sorted(call_log), ["2024-04-02", "2024-04-03"])
            self.assertEqual(sorted(summary["skipped"]), ["2024-04-01"])
            with open(out) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 9)  # 3 dates x 3 rows, no duplicates

    def test_resume_trusts_output_file_over_stale_or_missing_state(self):
        """dates_already_in_output()'s own belt-and-braces design: even if
        the state file is deleted/corrupted after a crash, a date whose rows
        are genuinely present in the output file must still be skipped, not
        silently recomputed and duplicated."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with open(out, "w") as f:
                for row in rows_for("2024-04-01"):
                    f.write(json.dumps(row) + "\n")
            # No state.json exists at all -- only the raw output file.
            self.assertFalse(os.path.exists(be.state_path(out)))

            call_log = []
            def tracking_side_effect(d, *a, **kw):
                call_log.append(d)
                return date_result(d, rows=rows_for(d))
            with mock.patch.object(be, "simulate_date", side_effect=tracking_side_effect), \
                 mock.patch.object(be, "StatcastStore"):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)

            self.assertEqual(call_log, [])  # never recomputed despite no state file
            with open(out) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 3)  # not duplicated

    def test_failed_dates_are_retried_on_resume_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with mock.patch.object(be, "simulate_date",
                                    return_value=date_result("2024-04-01", status="failed", reason="boom")), \
                 mock.patch.object(be, "StatcastStore"):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)

            call_log = []
            def now_succeeds(d, *a, **kw):
                call_log.append(d)
                return date_result(d, rows=rows_for(d))
            with mock.patch.object(be, "simulate_date", side_effect=now_succeeds), \
                 mock.patch.object(be, "StatcastStore"):
                summary, state = be.run_backtest("2024-04-01", "2024-04-01", out,
                                                  store=object(), sleep=0, verbose=False)

            self.assertEqual(call_log, ["2024-04-01"])  # retried, not skipped
            self.assertEqual(state["dates"]["2024-04-01"]["status"], "ok")

    def test_no_games_dates_are_skipped_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with mock.patch.object(be, "simulate_date",
                                    return_value=date_result("2024-04-01", status="no_games")), \
                 mock.patch.object(be, "StatcastStore"):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)

            call_log = []
            with mock.patch.object(be, "simulate_date",
                                    side_effect=lambda d, *a, **kw: call_log.append(d)), \
                 mock.patch.object(be, "StatcastStore"):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)
            self.assertEqual(call_log, [])  # never recomputed


class ForceFlagTests(unittest.TestCase):
    def test_force_recomputes_and_overwrites_state_but_appends_rows(self):
        """--force wipes the STATE (so nothing is skipped), but does not
        truncate the existing output file itself -- re-running with --force
        after a completed date would append a second copy of that date's
        rows. This is documented existing behavior, not a new gap this
        session introduced -- --force is an explicit, rarely-used escape
        hatch, not the normal resume path."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with mock.patch.object(be, "simulate_date",
                                    return_value=date_result("2024-04-01", rows=rows_for("2024-04-01"))), \
                 mock.patch.object(be, "StatcastStore"):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False, force=True)
            with open(out) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 6)  # force duplicated -- expected, not a bug


class RegimeConsistencyTests(unittest.TestCase):
    def test_empty_or_missing_file_is_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "does_not_exist.jsonl")
            result = be.check_regime_consistency(out, current_sha="abc123")
            self.assertTrue(result["consistent"])
            self.assertEqual(result["shas"], {})

    def test_single_sha_matching_current_is_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with open(out, "w") as f:
                for row in rows_for("2024-04-01"):
                    row["code_git_sha"] = "abc123"
                    f.write(json.dumps(row) + "\n")
            result = be.check_regime_consistency(out, current_sha="abc123")
            self.assertTrue(result["consistent"])
            self.assertEqual(result["shas"], {"abc123": 3})

    def test_two_distinct_shas_is_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with open(out, "w") as f:
                for row in rows_for("2024-04-01"):
                    row["code_git_sha"] = "old_sha"
                    f.write(json.dumps(row) + "\n")
                for row in rows_for("2024-04-02"):
                    row["code_git_sha"] = "new_sha"
                    f.write(json.dumps(row) + "\n")
            result = be.check_regime_consistency(out, current_sha="new_sha")
            self.assertFalse(result["consistent"])
            self.assertEqual(result["shas"], {"old_sha": 3, "new_sha": 3})

    def test_single_sha_different_from_current_is_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with open(out, "w") as f:
                for row in rows_for("2024-04-01"):
                    row["code_git_sha"] = "old_sha"
                    f.write(json.dumps(row) + "\n")
            result = be.check_regime_consistency(out, current_sha="new_sha")
            self.assertFalse(result["consistent"])

    def test_malformed_lines_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            with open(out, "w") as f:
                f.write("not valid json\n")
                for row in rows_for("2024-04-01"):
                    row["code_git_sha"] = "abc123"
                    f.write(json.dumps(row) + "\n")
            result = be.check_regime_consistency(out, current_sha="abc123")
            self.assertTrue(result["consistent"])


class AtomicWritePerDateTests(unittest.TestCase):
    """Hardening added this session: one write() call per date instead of a
    per-row loop, narrowing (not eliminating -- true fsync/rename atomicity
    for an append-mode shared log is a much bigger change, out of scope per
    'smallest safe change') the crash window that could leave a date's rows
    partially written."""

    def test_a_dates_rows_are_written_in_a_single_write_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rows.jsonl")
            write_calls = []
            real_open = open

            class TrackingFile:
                def __init__(self, f):
                    self._f = f
                def write(self, data):
                    write_calls.append(data)
                    return self._f.write(data)
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    self._f.close()

            def tracking_open(path, mode="r", **kw):
                f = real_open(path, mode, **kw)
                if path == out and "a" in mode:
                    return TrackingFile(f)
                return f

            with mock.patch.object(be, "simulate_date",
                                    return_value=date_result("2024-04-01", rows=rows_for("2024-04-01", n=5))), \
                 mock.patch.object(be, "StatcastStore"), \
                 mock.patch("builtins.open", side_effect=tracking_open):
                be.run_backtest("2024-04-01", "2024-04-01", out, store=object(),
                                sleep=0, verbose=False)
            self.assertEqual(len(write_calls), 1,
                              f"expected exactly one write() call for the date's 5 rows, got {len(write_calls)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
