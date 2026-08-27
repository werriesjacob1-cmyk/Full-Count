#!/usr/bin/env python3
"""Adversarial interruption-safety tests for backtest/canonical_run.py.

Required by the governing FULL COUNT canonical-rebuild mission spec
(Phase 4, "PROVE INTERRUPTION SAFETY BEFORE THE GIANT RUN" -- a HARD GATE:
the real 2024-04-01..2026-08-25 backfill may not launch until every
scenario below passes). Each scenario is a real adversarial condition the
2026-08-25 PID-1633 incident (or a plausible variant of it) could produce,
not a synthetic happy path.

simulate_date() itself (the real MLB-fetching/scoring logic) is replaced
with a deterministic FAKE_WORK function for these tests -- these tests are
about the interruption-safety LAYER canonical_run.py adds around that
call, not a re-test of backtest simulation itself (that is
test_backtest_engine.py's job). FAKE_WORK is still driven through the same
DateResult/status contract simulate_date() actually returns, so the layer
under test never sees a synthetic input shape it wouldn't see for real.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from collections import defaultdict
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import backtest.canonical_run as cr
from backtest.engine import DateResult


def fake_result(date, *, n_rows=3, status="ok", reason=None):
    res = DateResult(date)
    res.status = status
    res.reason = reason
    if status == "ok":
        res.rows = [
            {"date": date, "game_pk": 1000 + i, "player_id": 500 + i,
             "prop_type": "hits", "line": 1.5, "outcome": i % 2,
             "code_git_sha": "deadbeef"}
            for i in range(n_rows)
        ]
        res.n_games = 2
        res.n_candidates = n_rows
    return res


DATES = ["2024-04-01", "2024-04-02", "2024-04-03", "2024-04-04", "2024-04-05"]


def make_fake_simulate(results_by_date):
    def _fake(date, store, **kwargs):
        return results_by_date[date]
    return _fake


class CanonicalRunTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base_dir = os.path.join(self.tmp.name, "canonical_runs")
        self.sha_patch = mock.patch(
            "backtest.canonical_run.recommendation.git_sha", return_value="fixedsha0000000000000000000000000000000")
        self.sha_patch.start()

    def tearDown(self):
        self.sha_patch.stop()
        self.tmp.cleanup()

    def new_manifest(self, start=DATES[0], end=DATES[-1]):
        identity = cr.build_run_identity(start, end, out_target="unused")
        run_dir = cr.run_dir_for(self.base_dir, identity["run_id"])
        os.makedirs(run_dir, exist_ok=True)
        return run_dir, cr.create_manifest(run_dir, identity)

    def run_with_results(self, run_dir, manifest, results_by_date, **kwargs):
        with mock.patch("backtest.engine.simulate_date", side_effect=make_fake_simulate(results_by_date)), \
             mock.patch("backtest.engine.StatcastStore") as MockStore:
            MockStore.return_value = mock.Mock(load=mock.Mock())
            return cr.run(run_dir, manifest, sleep=0, **kwargs)


class NormalReferenceTests(CanonicalRunTestBase):
    """Normal uninterrupted reference run."""

    def test_uninterrupted_run_produces_valid_complete_state(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d, n_rows=(0 if d == "2024-04-03" else 3),
                                  status=("no_games" if d == "2024-04-03" else "ok"))
                  for d in DATES}
        self.run_with_results(run_dir, manifest, results)
        state = cr.load_run_state(run_dir, DATES)
        cr.validate_complete(state)  # must not raise
        self.assertEqual(state["2024-04-03"]["resolved"], "no_games")
        for d in DATES:
            if d != "2024-04-03":
                self.assertEqual(state[d]["resolved"], "ok")
        summary = cr.assemble(run_dir, manifest)
        self.assertEqual(summary["total_rows"], 3 * 4)
        self.assertEqual(summary["ok_dates"], 4)
        self.assertEqual(summary["no_games_dates"], 1)
        self.reference_fingerprint = summary["logical_fingerprint"]
        self.reference_checksum = summary["byte_sha256"]


class MidDateInterruptionTests(CanonicalRunTestBase):
    def test_kill_mid_date_leaves_no_meta_and_resume_recovers_cleanly(self):
        run_dir, manifest = self.new_manifest()
        # Simulate a kill mid-date: a data file exists (the OS-level write
        # landed) but no meta was ever written (the process died before the
        # second atomic write). This is exactly the shape write_checkpoint's
        # two-phase write is designed to leave behind on a real kill.
        os.makedirs(cr.checkpoints_dir(run_dir), exist_ok=True)
        with open(cr.checkpoint_data_path(run_dir, "2024-04-02"), "w") as f:
            f.write('{"date": "2024-04-02", "game_pk": 1, "player_id": 1}\n')  # half a date's rows

        state_before = cr.load_run_state(run_dir, DATES)
        self.assertEqual(state_before["2024-04-02"]["resolved"], "partial")

        results = {d: fake_result(d) for d in DATES}
        self.run_with_results(run_dir, manifest, results)

        state_after = cr.load_run_state(run_dir, DATES)
        cr.validate_complete(state_after)
        self.assertEqual(state_after["2024-04-02"]["resolved"], "ok")
        ok, meta = cr.validate_checkpoint(run_dir, "2024-04-02")
        self.assertTrue(ok)
        self.assertEqual(meta["row_count"], 3)  # the fake result's real 3 rows, not the stray half-write


class BetweenDateInterruptionTests(CanonicalRunTestBase):
    def test_interrupt_after_completed_checkpoint_resume_matches_reference(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d) for d in DATES}

        # First invocation: only process the first two dates (simulating a
        # process that was killed cleanly between dates 2 and 3).
        self.run_with_results(run_dir, manifest, results, max_dates=2)
        state_partial = cr.load_run_state(run_dir, DATES)
        self.assertEqual(
            [d for d in DATES if state_partial[d]["resolved"] == "ok"], DATES[:2])

        # Resume: must continue from date 3, never re-touch 1-2.
        with mock.patch.object(cr, "write_checkpoint", wraps=cr.write_checkpoint) as spy:
            self.run_with_results(run_dir, manifest, results)
            written_dates = [call.args[1] for call in spy.call_args_list]
        self.assertEqual(sorted(written_dates), DATES[2:])

        state_final = cr.load_run_state(run_dir, DATES)
        cr.validate_complete(state_final)

        # Reference: a from-scratch uninterrupted run in a separate dir.
        ref_dir, ref_manifest = self.new_manifest()
        cr.load_manifest  # (no-op, just documenting we could reload if needed)
        self.run_with_results(ref_dir, ref_manifest, results)
        ref_summary = cr.assemble(ref_dir, ref_manifest)
        resumed_summary = cr.assemble(run_dir, manifest)
        self.assertEqual(resumed_summary["logical_fingerprint"], ref_summary["logical_fingerprint"])
        self.assertEqual(resumed_summary["total_rows"], ref_summary["total_rows"])


class TruncatedCheckpointTests(CanonicalRunTestBase):
    def test_corrupted_checkpoint_detected_and_rebuilt_on_resume(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d) for d in DATES}
        self.run_with_results(run_dir, manifest, results)

        # Deliberately truncate a completed checkpoint's data file after the
        # fact (simulating disk corruption / an incomplete flush that
        # nonetheless left a meta file behind from an earlier, different
        # write).
        target = cr.checkpoint_data_path(run_dir, "2024-04-02")
        with open(target) as f:
            original = f.read()
        with open(target, "w") as f:
            f.write(original[: len(original) // 2])

        ok, reason = cr.validate_checkpoint(run_dir, "2024-04-02")
        self.assertFalse(ok)
        self.assertIn("checksum", reason)

        state = cr.load_run_state(run_dir, DATES)
        self.assertEqual(state["2024-04-02"]["resolved"], "error")
        remaining = cr.plan_remaining(state)
        self.assertIn("2024-04-02", remaining)

        # Resume must rebuild it correctly.
        self.run_with_results(run_dir, manifest, results)
        ok2, meta2 = cr.validate_checkpoint(run_dir, "2024-04-02")
        self.assertTrue(ok2)
        self.assertEqual(meta2["row_count"], 3)


class StalePartialFileTests(CanonicalRunTestBase):
    def test_leftover_tmp_file_from_interrupted_write_is_ignored_and_cleaned_path_used(self):
        run_dir, manifest = self.new_manifest()
        os.makedirs(cr.checkpoints_dir(run_dir), exist_ok=True)
        stray = os.path.join(cr.checkpoints_dir(run_dir), ".2024-04-01.jsonl.abc123.tmp")
        with open(stray, "w") as f:
            f.write("garbage-not-json\n")

        results = {d: fake_result(d) for d in DATES}
        self.run_with_results(run_dir, manifest, results)

        state = cr.load_run_state(run_dir, DATES)
        cr.validate_complete(state)  # the stray .tmp must not count as, or block, a real checkpoint
        self.assertTrue(os.path.exists(stray), "the run must not silently delete forensic leftovers")
        ok, meta = cr.validate_checkpoint(run_dir, "2024-04-01")
        self.assertTrue(ok)
        self.assertEqual(meta["row_count"], 3)


class ManifestMismatchTests(CanonicalRunTestBase):
    def test_code_sha_drift_fails_closed_by_default(self):
        run_dir, manifest = self.new_manifest()
        with mock.patch("backtest.canonical_run.recommendation.git_sha",
                        return_value="different_sha_entirely"):
            with self.assertRaises(cr.CodeIdentityDrift):
                cr.verify_code_identity(manifest)
            # explicit opt-in required to proceed
            result = cr.verify_code_identity(manifest, allow_sha_drift=True)
            self.assertFalse(result["consistent"])

    def test_run_refuses_to_resume_under_drifted_sha(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d) for d in DATES}
        with mock.patch("backtest.canonical_run.recommendation.git_sha",
                        return_value="different_sha_entirely"):
            with self.assertRaises(cr.CodeIdentityDrift):
                self.run_with_results(run_dir, manifest, results)
        # nothing should have been written
        state = cr.load_run_state(run_dir, DATES)
        self.assertTrue(all(s["resolved"] == "never_run" for s in state.values()))


class DuplicateCheckpointRerunTests(CanonicalRunTestBase):
    def test_rerun_of_completed_date_without_force_is_a_noop(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d) for d in DATES}
        self.run_with_results(run_dir, manifest, results)
        ok_before, meta_before = cr.validate_checkpoint(run_dir, "2024-04-01")

        with mock.patch.object(cr, "write_checkpoint", wraps=cr.write_checkpoint) as spy:
            self.run_with_results(run_dir, manifest, results)
            self.assertEqual(spy.call_args_list, [])  # nothing re-run, everything already ok

        ok_after, meta_after = cr.validate_checkpoint(run_dir, "2024-04-01")
        self.assertEqual(meta_before["sha256"], meta_after["sha256"])

    def test_force_deterministically_replaces_not_appends(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d, n_rows=3) for d in DATES}
        self.run_with_results(run_dir, manifest, results)
        different_results = {d: fake_result(d, n_rows=7) for d in DATES}
        self.run_with_results(run_dir, manifest, different_results, force=True)
        ok, meta = cr.validate_checkpoint(run_dir, "2024-04-01")
        self.assertTrue(ok)
        self.assertEqual(meta["row_count"], 7)  # replaced, not 3+7=10


class MissingDateTests(CanonicalRunTestBase):
    def test_removed_completed_checkpoint_fails_final_validation(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d) for d in DATES}
        self.run_with_results(run_dir, manifest, results)
        os.remove(cr.checkpoint_meta_path(run_dir, "2024-04-03"))
        os.remove(cr.checkpoint_data_path(run_dir, "2024-04-03"))

        state = cr.load_run_state(run_dir, DATES)
        self.assertEqual(state["2024-04-03"]["resolved"], "never_run")
        with self.assertRaises(RuntimeError):
            cr.validate_complete(state)
        with self.assertRaises(RuntimeError):
            cr.assemble(run_dir, manifest)


class NoGamesDateTests(CanonicalRunTestBase):
    def test_no_games_is_distinct_from_missing_and_valid(self):
        run_dir, manifest = self.new_manifest()
        results = dict((d, fake_result(d)) for d in DATES)
        results["2024-04-03"] = fake_result("2024-04-03", status="no_games")
        self.run_with_results(run_dir, manifest, results)

        state = cr.load_run_state(run_dir, DATES)
        self.assertEqual(state["2024-04-03"]["resolved"], "no_games")
        cr.validate_complete(state)  # a no_games date counts as resolved, not missing
        # write_checkpoint always writes a (possibly empty) data file for
        # bookkeeping uniformity; validate_checkpoint tolerates that for
        # no_games as long as it carries zero bytes -- exercised directly:
        self.assertEqual(os.path.getsize(cr.checkpoint_data_path(run_dir, "2024-04-03")), 0)
        # tampering a no_games date with a fabricated NON-EMPTY data file must be caught
        with open(cr.checkpoint_data_path(run_dir, "2024-04-03"), "w") as f:
            f.write('{"date": "2024-04-03"}\n')
        ok, reason = cr.validate_checkpoint(run_dir, "2024-04-03")
        self.assertFalse(ok)


class ErrorDateTests(CanonicalRunTestBase):
    def test_source_error_stays_retryable_never_marked_complete(self):
        run_dir, manifest = self.new_manifest()
        results = dict((d, fake_result(d)) for d in DATES)
        results["2024-04-03"] = fake_result("2024-04-03", status="failed", reason="MLB API timeout")
        self.run_with_results(run_dir, manifest, results)

        state = cr.load_run_state(run_dir, DATES)
        self.assertEqual(state["2024-04-03"]["resolved"], "error")
        with self.assertRaises(RuntimeError):
            cr.validate_complete(state)
        remaining = cr.plan_remaining(state)
        self.assertEqual(remaining, ["2024-04-03"])

        # Retry succeeds once the transient error clears.
        results["2024-04-03"] = fake_result("2024-04-03", status="ok")
        self.run_with_results(run_dir, manifest, results)
        state2 = cr.load_run_state(run_dir, DATES)
        cr.validate_complete(state2)  # no longer raises


class ConcurrentWriterTests(CanonicalRunTestBase):
    def test_second_owner_cannot_acquire_a_live_lock(self):
        run_dir, manifest = self.new_manifest()
        lock1 = cr.acquire_lock(run_dir, manifest["run_id"], owner_token="owner-a")
        with self.assertRaises(cr.LockHeldElsewhere):
            cr.acquire_lock(run_dir, manifest["run_id"], owner_token="owner-b")
        cr.release_lock(run_dir, lock1)
        # after release, a second owner can acquire cleanly
        lock2 = cr.acquire_lock(run_dir, manifest["run_id"], owner_token="owner-b")
        self.assertEqual(cr.read_lock(run_dir)["owner_token"], "owner-b")

    def test_stale_lock_from_dead_pid_is_reclaimable(self):
        run_dir, manifest = self.new_manifest()
        # a lock claiming a PID that (almost certainly) does not exist
        dead_lock = {
            "run_id": manifest["run_id"], "pid": 999999, "hostname": cr.socket.gethostname(),
            "owner_token": "dead-owner", "acquired_at": cr._now_iso(), "heartbeat_at": cr._now_iso(),
        }
        cr._atomic_write_json(cr.lock_path(run_dir), dead_lock)
        self.assertTrue(cr.is_lock_stale(dead_lock))
        # a new owner must be able to reclaim it without manual intervention
        lock2 = cr.acquire_lock(run_dir, manifest["run_id"], owner_token="rescuer")
        self.assertEqual(lock2["owner_token"], "rescuer")

    def test_stale_lock_by_heartbeat_age_alone_is_reclaimable_cross_host(self):
        run_dir, manifest = self.new_manifest()
        import datetime as _dt
        old = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(seconds=cr.LOCK_STALE_SECONDS + 60)).isoformat()
        remote_lock = {
            "run_id": manifest["run_id"], "pid": 1, "hostname": "some-other-container",
            "owner_token": "remote-owner", "acquired_at": old, "heartbeat_at": old,
        }
        cr._atomic_write_json(cr.lock_path(run_dir), remote_lock)
        self.assertTrue(cr.is_lock_stale(remote_lock))
        lock2 = cr.acquire_lock(run_dir, manifest["run_id"], owner_token="rescuer")
        self.assertEqual(lock2["owner_token"], "rescuer")


class LockLeaseHardeningTests(CanonicalRunTestBase):
    """2026-08-27 lock-lease race. The previous is_lock_stale() checked
    heartbeat age BEFORE PID liveness, so a healthy but busy same-host
    owner was declared stale purely for taking a long time.

    This was not theoretical. The live canonical run's own lock.json
    recorded acquired_at=03:40:11 and its first heartbeat_at=03:55:44 --
    933 seconds apart, against LOCK_STALE_SECONDS=900 -- because Statcast
    warmup runs before the first date completes and heartbeats were only
    emitted on date boundaries. For 33 seconds, a second process would
    have been told it could reclaim the lock from a running, correct,
    multi-hour job.

    The seven scenarios below are the ones the governing mission requires.
    """

    def _lock(self, **over):
        base = {"run_id": "r", "owner_token": "tok",
                "acquired_at": cr._now_iso(), "heartbeat_at": cr._now_iso(),
                **cr.owner_process_identity()}
        base.update(over)
        return base

    def _ancient(self):
        import datetime as _dt
        return (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(seconds=cr.LOCK_STALE_SECONDS * 10)).isoformat()

    # 1
    def test_same_host_alive_pid_with_ancient_heartbeat_is_NOT_stale(self):
        lock = self._lock(heartbeat_at=self._ancient(), acquired_at=self._ancient())
        self.assertFalse(cr.is_lock_stale(lock),
                         "a verifiably-alive same-host owner must never be stale on time alone")

    # 2
    def test_same_host_dead_pid_with_ancient_heartbeat_is_stale(self):
        lock = self._lock(pid=999999, pid_start_ticks=None,
                          heartbeat_at=self._ancient())
        self.assertTrue(cr.is_lock_stale(lock))

    def test_same_host_dead_pid_with_FRESH_heartbeat_is_still_stale(self):
        # Crash recovery must not have to wait out the clock.
        lock = self._lock(pid=999999, pid_start_ticks=None)
        self.assertTrue(cr.is_lock_stale(lock))

    def test_recycled_pid_is_treated_as_dead(self):
        # Same PID, different process: start-ticks will not match. Without
        # this check a recycled PID would let a dead run hold its lock
        # forever, which is the failure mode that makes "liveness beats
        # age" safe to adopt in the first place.
        lock = self._lock(pid_start_ticks=(cr._proc_start_ticks(os.getpid()) or 0) + 987654)
        self.assertTrue(cr.is_lock_stale(lock))

    def test_reboot_since_lock_is_treated_as_dead(self):
        lock = self._lock(boot_id="a-different-boot-entirely", heartbeat_at=self._ancient())
        self.assertTrue(cr.is_lock_stale(lock))

    # 3
    def test_different_host_ancient_heartbeat_is_stale(self):
        lock = self._lock(hostname="some-other-container", pid=1,
                          pid_start_ticks=None, boot_id=None,
                          heartbeat_at=self._ancient())
        self.assertTrue(cr.is_lock_stale(lock))

    def test_different_host_FRESH_heartbeat_is_not_stale(self):
        # A foreign PID number is meaningless locally, so age is the only
        # honest signal there -- and a fresh one must be respected.
        lock = self._lock(hostname="some-other-container", pid=1,
                          pid_start_ticks=None, boot_id=None)
        self.assertFalse(cr.is_lock_stale(lock))

    # 4
    def test_live_lease_during_long_operation_keeps_second_owner_out(self):
        run_dir, manifest = self.new_manifest()
        lock = cr.acquire_lock(run_dir, manifest["run_id"], owner_token="owner-a")
        # Simulate the live incident exactly: the lease's LAST recorded
        # heartbeat is older than the stale threshold (a long phase), yet
        # the owner process is genuinely alive.
        stale_looking = dict(lock, heartbeat_at=self._ancient(),
                             acquired_at=self._ancient())
        cr._atomic_write_json(cr.lock_path(run_dir), stale_looking)
        with self.assertRaises(cr.LockHeldElsewhere):
            cr.acquire_lock(run_dir, manifest["run_id"], owner_token="owner-b")

        # And with an active lease thread the heartbeat does not even go
        # stale in the first place.
        lease = cr.LeaseHeartbeat(run_dir, lock, interval=0.05).start()
        try:
            deadline = time.time() + 2.0
            while lease.ticks < 2 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(lease.ticks, 2, "lease thread did not refresh the lock")
            refreshed = cr.read_lock(run_dir)
            self.assertFalse(cr.is_lock_stale(refreshed))
            self.assertEqual(refreshed["owner_token"], "owner-a",
                             "lease must never change who owns the lock")
        finally:
            lease.stop()

    # 5
    def test_lease_thread_is_cleaned_up_after_normal_completion(self):
        run_dir, manifest = self.new_manifest()
        lock = cr.acquire_lock(run_dir, manifest["run_id"])
        before = threading.active_count()
        with cr.LeaseHeartbeat(run_dir, lock, interval=0.05) as lease:
            self.assertTrue(lease.running)
        self.assertFalse(lease.running)
        for _ in range(50):
            if threading.active_count() <= before:
                break
            time.sleep(0.02)
        self.assertLessEqual(threading.active_count(), before, "lease thread leaked")

    # 6
    def test_lease_thread_is_cleaned_up_after_an_exception(self):
        run_dir, manifest = self.new_manifest()
        lock = cr.acquire_lock(run_dir, manifest["run_id"])
        before = threading.active_count()
        lease = None
        with self.assertRaises(RuntimeError):
            with cr.LeaseHeartbeat(run_dir, lock, interval=0.05) as lease:
                raise RuntimeError("simulated failure inside the long phase")
        self.assertFalse(lease.running)
        for _ in range(50):
            if threading.active_count() <= before:
                break
            time.sleep(0.02)
        self.assertLessEqual(threading.active_count(), before,
                             "lease thread leaked on the exception path")

    def test_lease_write_failure_is_survivable_and_not_masked_as_success(self):
        run_dir, manifest = self.new_manifest()
        lock = cr.acquire_lock(run_dir, manifest["run_id"])
        with mock.patch.object(cr, "heartbeat_lock", side_effect=OSError("disk gone")):
            with cr.LeaseHeartbeat(run_dir, lock, interval=0.05) as lease:
                deadline = time.time() + 2.0
                while lease.errors < 2 and time.time() < deadline:
                    time.sleep(0.02)
        self.assertGreaterEqual(lease.errors, 2, "write failures must be counted")
        self.assertEqual(lease.ticks, 0, "a failed write must never count as a tick")

    # 7
    def test_no_second_writer_admitted_while_original_owner_alive(self):
        run_dir, manifest = self.new_manifest()
        results = {d: fake_result(d) for d in DATES}
        self.run_with_results(run_dir, manifest, results, max_dates=1)
        # Owner A takes the lock and is alive; a second run() invocation
        # must be refused before it can write a single checkpoint.
        cr.acquire_lock(run_dir, manifest["run_id"], owner_token="owner-a")
        before = {d: cr.validate_checkpoint(run_dir, d)[0] for d in DATES}
        with self.assertRaises(cr.LockHeldElsewhere):
            self.run_with_results(run_dir, manifest, results)
        after = {d: cr.validate_checkpoint(run_dir, d)[0] for d in DATES}
        self.assertEqual(before, after,
                         "a refused second owner must not have written anything")


class WorktreeCodeMutationTests(CanonicalRunTestBase):
    def test_manifest_pins_sha_at_creation_and_run_reverifies_it_every_invocation(self):
        run_dir, manifest = self.new_manifest()
        self.assertEqual(manifest["code_git_sha"], "fixedsha0000000000000000000000000000000")
        results = {d: fake_result(d) for d in DATES[:2]}
        self.run_with_results(run_dir, manifest, results, max_dates=2)

        # Simulate the exact failure class this project actually hit: the
        # active checkout mutates underneath the run mid-flight.
        with mock.patch("backtest.canonical_run.recommendation.git_sha",
                        return_value="mutated_underneath_sha"):
            with self.assertRaises(cr.CodeIdentityDrift):
                self.run_with_results(run_dir, manifest, {d: fake_result(d) for d in DATES})
        # the two already-completed dates must be untouched by the refusal
        state = cr.load_run_state(run_dir, DATES)
        self.assertEqual(state["2024-04-01"]["resolved"], "ok")
        self.assertEqual(state["2024-04-02"]["resolved"], "ok")


class CriticalEquivalenceTest(CanonicalRunTestBase):
    """The mission's own 'not optional' equivalence requirement: a resumed
    run must be demonstrably equivalent to an uninterrupted reference on
    every listed axis."""

    def test_resumed_run_is_equivalent_to_uninterrupted_reference_on_every_axis(self):
        results = {d: fake_result(d, n_rows=(0 if d == "2024-04-04" else 4),
                                  status=("no_games" if d == "2024-04-04" else "ok"))
                  for d in DATES}

        ref_dir, ref_manifest = self.new_manifest()
        self.run_with_results(ref_dir, ref_manifest, results)
        ref_summary = cr.assemble(ref_dir, ref_manifest)

        # Interrupted variant: 2 dates, corrupt one, kill mid-date on
        # another, then multiple resumes to completion.
        int_dir, int_manifest = self.new_manifest()
        self.run_with_results(int_dir, int_manifest, results, max_dates=2)
        with open(cr.checkpoint_data_path(int_dir, "2024-04-02")) as f:
            original = f.read()
        with open(cr.checkpoint_data_path(int_dir, "2024-04-02"), "w") as f:
            f.write(original[:10])  # corrupt
        os.makedirs(cr.checkpoints_dir(int_dir), exist_ok=True)
        with open(cr.checkpoint_data_path(int_dir, "2024-04-03"), "w") as f:
            f.write('{"partial": true}\n')  # mid-date kill shape, no meta
        self.run_with_results(int_dir, int_manifest, results)  # resume 1
        self.run_with_results(int_dir, int_manifest, results)  # resume 2, must be a clean no-op
        int_summary = cr.assemble(int_dir, int_manifest)

        def rows_of(run_dir, summary):
            with open(summary["rows_path"]) as f:
                return [json.loads(line) for line in f if line.strip()]

        ref_rows = rows_of(ref_dir, ref_summary)
        int_rows = rows_of(int_dir, int_summary)

        self.assertEqual(len(ref_rows), len(int_rows), "exact row count")
        ref_idents = sorted(tuple(r.get(k) for k in cr.CANDIDATE_IDENTITY_FIELDS) for r in ref_rows)
        int_idents = sorted(tuple(r.get(k) for k in cr.CANDIDATE_IDENTITY_FIELDS) for r in int_rows)
        self.assertEqual(ref_idents, int_idents, "candidate identities")
        self.assertEqual(sorted(r["outcome"] for r in ref_rows),
                         sorted(r["outcome"] for r in int_rows), "candidate values")
        self.assertEqual({r["date"] for r in ref_rows}, {r["date"] for r in int_rows}, "date coverage")
        self.assertEqual(len(ref_idents), len(set(ref_idents)), "reference has no duplicates")
        self.assertEqual(len(int_idents), len(set(int_idents)), "resumed has no duplicates")
        # order: both assembled in the same deterministic sorted-date order
        self.assertEqual([r["date"] for r in ref_rows], [r["date"] for r in int_rows], "row order")
        self.assertEqual(ref_summary["logical_fingerprint"], int_summary["logical_fingerprint"],
                         "logical fingerprint")


class LegacySalvageImportTests(CanonicalRunTestBase):
    """import_legacy() -- the mechanism that lets the 2026-08-27 forensically
    validated 450,621-row partial artifact (rows_backfill_v2.jsonl,
    2024-04-01..2025-04-20) become the starting checkpoint state for the
    new architecture instead of being re-fetched from scratch."""

    def _write_legacy(self, dirpath, rows_by_date, state_dates):
        legacy_jsonl = os.path.join(dirpath, "legacy.jsonl")
        with open(legacy_jsonl, "w") as f:
            for d in sorted(rows_by_date):
                for row in rows_by_date[d]:
                    f.write(json.dumps(row) + "\n")
        legacy_state = os.path.join(dirpath, "legacy.jsonl.state.json")
        with open(legacy_state, "w") as f:
            json.dump({"dates": state_dates}, f)
        return legacy_jsonl, legacy_state

    def test_import_creates_checkpoints_tagged_with_legacy_sha(self):
        run_dir, manifest = self.new_manifest(start="2024-04-01", end="2024-04-02")
        rows = {
            "2024-04-01": [{"date": "2024-04-01", "game_pk": 1, "player_id": 1,
                            "prop_type": "hits", "line": 1.5, "outcome": 1}],
            "2024-04-02": [],
        }
        state_dates = {
            "2024-04-01": {"status": "ok", "rows": 1},
            "2024-04-02": {"status": "no_games", "rows": 0},
        }
        legacy_jsonl, legacy_state = self._write_legacy(self.tmp.name, rows, state_dates)

        result = cr.import_legacy(run_dir, legacy_jsonl, legacy_state,
                                  legacy_code_git_sha="2ce95fe9legacyshafull000000000000")
        self.assertEqual(result, {"ok": 1, "no_games": 1, "skipped_already_present": 0})

        ok, meta = cr.validate_checkpoint(run_dir, "2024-04-01")
        self.assertTrue(ok)
        self.assertEqual(meta["code_git_sha"], "2ce95fe9legacyshafull000000000000")
        self.assertEqual(meta["extra"]["imported_from"], "legacy_rows_backfill_v2")

        state = cr.load_run_state(run_dir, ["2024-04-01", "2024-04-02"])
        cr.validate_complete(state)  # imported dates count as real, complete checkpoints

    def test_import_skips_dates_already_validly_present(self):
        run_dir, manifest = self.new_manifest(start="2024-04-01", end="2024-04-01")
        results = {"2024-04-01": fake_result("2024-04-01")}
        self.run_with_results(run_dir, manifest, results)

        rows = {"2024-04-01": [{"date": "2024-04-01", "game_pk": 999, "player_id": 999,
                                "prop_type": "hits", "line": 1.5, "outcome": 0}]}
        state_dates = {"2024-04-01": {"status": "ok", "rows": 1}}
        legacy_jsonl, legacy_state = self._write_legacy(self.tmp.name, rows, state_dates)

        result = cr.import_legacy(run_dir, legacy_jsonl, legacy_state, legacy_code_git_sha="legacysha")
        self.assertEqual(result["skipped_already_present"], 1)
        # the ALREADY-run checkpoint must survive untouched, not be overwritten by the import
        ok, meta = cr.validate_checkpoint(run_dir, "2024-04-01")
        self.assertEqual(meta["row_count"], 3)  # fake_result's 3 rows, not the legacy file's 1

    def test_import_refuses_an_internally_inconsistent_legacy_date(self):
        run_dir, manifest = self.new_manifest(start="2024-04-01", end="2024-04-01")
        rows = {"2024-04-01": [{"date": "2024-04-01", "game_pk": 1, "player_id": 1,
                                "prop_type": "hits", "line": 1.5, "outcome": 1}]}
        # state claims 5 rows, file only has 1 -- a real internal inconsistency
        state_dates = {"2024-04-01": {"status": "ok", "rows": 5}}
        legacy_jsonl, legacy_state = self._write_legacy(self.tmp.name, rows, state_dates)
        with self.assertRaises(RuntimeError):
            cr.import_legacy(run_dir, legacy_jsonl, legacy_state, legacy_code_git_sha="legacysha")


class PushManifestSnapshotNeverMovesHeadTests(CanonicalRunTestBase):
    """2026-08-27 real incident: an earlier push_manifest_snapshot() called
    plain `git commit` in REPO_ROOT, which -- when run from inside the
    pinned canonical-run worktree, exactly its intended call site --
    silently advanced that worktree's own HEAD and then correctly tripped
    its own CodeIdentityDrift guard on the very next resume, blocking the
    real backfill. This locks in the fix: the function must be provably
    incapable of moving HEAD, the real index, or the working tree of
    whatever repo it is invoked from."""

    def test_head_and_index_are_unchanged_after_a_successful_snapshot_push(self):
        import subprocess
        run_dir, manifest = self.new_manifest(start="2024-04-01", end="2024-04-01")
        self.run_with_results(run_dir, manifest, {"2024-04-01": fake_result("2024-04-01")})

        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                     capture_output=True, text=True).stdout
        status_before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                       capture_output=True, text=True).stdout

        result = cr.push_manifest_snapshot(run_dir, branch="canonical-run-manifests-test-noop",
                                           remote="__no_such_remote__")
        self.assertFalse(result["pushed"])  # expected: no such remote -- proves the push path ran

        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                    capture_output=True, text=True).stdout
        status_after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                      capture_output=True, text=True).stdout
        self.assertEqual(head_before, head_after, "HEAD must never move in the caller's repo")
        self.assertEqual(status_before, status_after,
                         "the real index/working tree must show zero changes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
