#!/usr/bin/env python3
"""Reconciliation: publication vs authoritative state.

Two shapes were wrong in the first P0 pass and are corrected here.

SCHEDULING. The first fix added another GitHub-cron watchdog. GitHub's
`schedule` trigger is throttled in this repo -- Lineup Watch declares */10
and delivers 9% of it, worst observed gap 11.0 h -- so a recovery mechanism
on that queue cannot bound anything. infra/live-heartbeat already dispatches
dashboard-live.yml every 5 minutes from Cloudflare, and reconciliation now
runs there.

ACKNOWLEDGMENT. A watchdog that dispatches a rebuild and considers itself
done acknowledges an EVENT. The question that matters is whether
publication MATCHES reality, and those come apart exactly when it counts:
the dispatch can be dropped, the rebuild can fail, or it can succeed and
still not fix the mismatch. So a mismatch clears only by re-observation,
never by asking.
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dashboard import reconcile as rc  # noqa: E402
import recommendation  # noqa: E402

NOW = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)


def board(minutes_old, props=None, **extra):
    p = {"generated_at": (NOW - timedelta(minutes=minutes_old)).isoformat(),
         "props": props or []}
    p.update(extra)
    return p


def prop(**kw):
    base = {"id": "fc2:1:player-9:hits:1:over", "game_pk": 1, "player_id": 9,
            "name": "A Batter", "stat": "hits", "projection": {"stat": "hits", "value": 0.5},
            "batting_order": 3, "lineup_assumed": False, "market_fetch_state": "MATCHED"}
    base.update(kw)
    return base


class TestMissedWindow(unittest.TestCase):
    """Recovery fires on a MISSED scheduled rebuild, not on raw board age.

    The 180-minute raw-age rule this replaces was documented as "fires only
    once a scheduled window has genuinely been MISSED". Across the real
    schedule that was false: the 03:00-13:00 UTC gap has no window at all,
    so raw age tripped roughly three times a night with nothing missed.
    """

    def test_fresh_board_is_no_mismatch(self):
        self.assertIsNone(rc.board_age_mismatch(board(10), now=NOW))

    def test_unknown_age_is_never_treated_as_fresh(self):
        m = rc.board_age_mismatch({"generated_at": None}, now=NOW)
        self.assertIsNotNone(m)

    def test_a_genuinely_missed_window_is_a_mismatch(self):
        """19:00 build never happened; at 20:00 that window is due."""
        stale = {"generated_at": datetime(2026, 8, 28, 17, 14,
                                          tzinfo=timezone.utc).isoformat()}
        m = rc.board_age_mismatch(stale, now=NOW)
        self.assertIsNotNone(m)
        self.assertEqual(m["kind"], rc.KIND_BOARD_AGE)
        self.assertIn("19:00", m["detail"])

    def test_a_window_is_not_due_until_grace_has_elapsed(self):
        """At 19:30 the 19:00 build may simply still be queueing. Firing
        here would preempt the very rebuild we are waiting for."""
        stale = {"generated_at": datetime(2026, 8, 28, 17, 14,
                                          tzinfo=timezone.utc).isoformat()}
        soon = datetime(2026, 8, 28, 19, 30, tzinfo=timezone.utc)
        self.assertIsNone(rc.board_age_mismatch(stale, now=soon))

    def test_declared_windows_match_the_workflow_exactly(self):
        """The policy is only sound if these ARE the scheduled windows."""
        path = os.path.join(ROOT, ".github", "workflows", "dashboard-refresh.yml")
        with open(path, encoding="utf-8") as fh:
            hours = sorted(int(h) for h in re.findall(
                r"cron: '0 (\d+) \* \* \*'", fh.read()))
        self.assertEqual(hours, sorted(rc.SCHEDULED_REBUILD_HOURS_UTC))

    def test_active_window_recovery_precedes_actionability_loss(self):
        """Inside the 2-hourly active window the worst case is cadence +
        grace, which must stay under recommendation.py's 4-hour limit with
        room for several observe+rebuild cycles."""
        hours = sorted(rc.SCHEDULED_REBUILD_HOURS_UTC)
        gaps = [(b - a) % 24 for a, b in zip(hours, hours[1:] + [hours[0] + 24])]
        active_gap = min(gaps) * 60
        worst = active_gap + rc.REBUILD_GRACE_MINUTES
        limit = recommendation.MAX_BOARD_AGE_SECONDS / 60
        self.assertLess(worst, limit)
        self.assertGreaterEqual((limit - worst) // 20, 3,
                                "must allow at least three observe+rebuild cycles")


class TestHealthyDayDispatches(unittest.TestCase):
    """A healthy day must produce ZERO recovery rebuilds.

    This is the whole point of the change. Under the raw-age rule the same
    simulation produced a recovery dispatch every night inside a gap where
    nothing was scheduled and nothing had failed.
    """

    @staticmethod
    def _healthy_boards(hours):
        """generated_at for a day where every scheduled window built on
        time (15 minutes to complete), newest-first."""
        day = datetime(2026, 8, 28, tzinfo=timezone.utc)
        out = []
        for offset in (-1, 0):
            for h in sorted(hours):
                out.append(day + timedelta(days=offset, hours=h, minutes=15))
        return sorted(out)

    def _latest_board_at(self, moment, builds):
        prior = [b for b in builds if b <= moment]
        return prior[-1] if prior else None

    def test_zero_recovery_dispatches_on_a_perfectly_healthy_day(self):
        builds = self._healthy_boards(rc.SCHEDULED_REBUILD_HOURS_UTC)
        fired = []
        # The live observer runs every 5 minutes from Cloudflare.
        moment = datetime(2026, 8, 28, tzinfo=timezone.utc)
        for _ in range(288):
            built = self._latest_board_at(moment, builds)
            if built is not None:
                m = rc.board_age_mismatch({"generated_at": built.isoformat()},
                                          now=moment)
                if m:
                    fired.append((moment.isoformat(), m["detail"]))
            moment += timedelta(minutes=5)
        self.assertEqual(fired, [], f"healthy day dispatched recovery: {fired[:3]}")

    def test_the_old_raw_age_rule_would_have_fired_overnight(self):
        """Locks in WHY this changed: 180 minutes of raw age trips inside
        the 03:00-13:00 gap, where no window is due and nothing is wrong."""
        builds = self._healthy_boards(rc.SCHEDULED_REBUILD_HOURS_UTC)
        raw_age_hits = 0
        moment = datetime(2026, 8, 28, tzinfo=timezone.utc)
        for _ in range(288):
            built = self._latest_board_at(moment, builds)
            if built is not None:
                age = (moment - built).total_seconds() / 60.0
                if age > 180:
                    raw_age_hits += 1
            moment += timedelta(minutes=5)
        self.assertGreater(raw_age_hits, 0,
                           "the rule this replaces must be shown to misfire")


class TestOvernightGap(unittest.TestCase):
    """The 10-hour gap vs the 4-hour actionability contract.

    Recorded, not silently fixed. Recovery deliberately does nothing here;
    whether the SCHEDULE should change is a separate product decision.
    """

    def test_the_gap_exceeds_the_actionability_limit(self):
        hours = sorted(rc.SCHEDULED_REBUILD_HOURS_UTC)
        gaps = [(b - a) % 24 for a, b in zip(hours, hours[1:] + [hours[0] + 24])]
        self.assertGreater(max(gaps) * 3600,
                           recommendation.MAX_BOARD_AGE_SECONDS,
                           "gap no longer exceeds the limit -- update this note")

    def test_board_fails_closed_rather_than_being_recovered(self):
        """At 11:00 UTC the last build was 03:15. The board is ~7.75h old,
        past the 4-hour limit, so recommendation.py's contract suppresses
        it -- and reconciliation requests nothing, because no window was
        missed. Stale-and-fail-closed, not stale-and-served."""
        moment = datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc)
        built = datetime(2026, 8, 28, 3, 15, tzinfo=timezone.utc)
        payload = {"generated_at": built.isoformat(), "props": []}
        age_seconds = (moment - built).total_seconds()
        self.assertGreater(age_seconds, recommendation.MAX_BOARD_AGE_SECONDS)
        self.assertIsNone(rc.board_age_mismatch(payload, now=moment))
        state = rc.reconcile(payload, confirmed_lineups={}, now=moment)
        self.assertFalse(rc.needs_rebuild(state))


# Lineup reconciliation moved to test_lineup_basis.py when it stopped
# reconstructing the published lineup from candidate rows. The tests that
# lived here asserted that superseded contract, so they are gone rather
# than left passing against an API nothing uses.


class TestLineMoved(unittest.TestCase):
    def test_line_moved_row_is_a_mismatch(self):
        p = board(5, [prop(market_fetch_state="LINE_MOVED", market_posted_line=14.5)])
        out = rc.line_moved_mismatches(p)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["authoritative"], 14.5)

    def test_matched_and_not_posted_are_not_mismatches(self):
        p = board(5, [prop(market_fetch_state="MATCHED"),
                      prop(id="x", market_fetch_state="NOT_POSTED")])
        self.assertEqual(rc.line_moved_mismatches(p), [])


class TestPersistenceNotAcknowledgment(unittest.TestCase):
    """The correction that matters most."""

    def setUp(self):
        self.stale = board(250)

    def test_requesting_a_rebuild_does_not_clear_the_mismatch(self):
        s = rc.reconcile(self.stale, now=NOW)
        self.assertTrue(rc.needs_rebuild(s))
        rc.mark_rebuild_requested(s, at=NOW)
        self.assertTrue(rc.needs_rebuild(s), "asking must never resolve anything")

    def test_a_failed_rebuild_leaves_it_open_across_cycles(self):
        s = rc.reconcile(self.stale, now=NOW)
        rc.mark_rebuild_requested(s, at=NOW)
        later = rc.reconcile(self.stale, now=NOW + timedelta(minutes=5), prior=s)
        self.assertTrue(rc.needs_rebuild(later))
        entry = list(later["open"].values())[0]
        self.assertEqual(entry["rebuild_requests"], 1)
        self.assertEqual(entry["first_seen_at"], list(s["open"].values())[0]["first_seen_at"],
                         "a persisting mismatch keeps its original first_seen_at")

    def test_it_clears_only_when_publication_actually_matches(self):
        s = rc.reconcile(self.stale, now=NOW)
        rc.mark_rebuild_requested(s, at=NOW)
        rebuilt = rc.reconcile(board(2), now=NOW + timedelta(minutes=20), prior=s)
        self.assertFalse(rc.needs_rebuild(rebuilt))
        self.assertEqual(len(rebuilt["resolved_this_cycle"]), 1)

    def test_a_new_stale_basis_is_a_new_mismatch(self):
        """A rebuild that produced a board which is ITSELF already too old
        must not inherit the old mismatch's bookkeeping."""
        a = rc.reconcile(board(250), now=NOW)
        b = rc.reconcile(board(300), now=NOW + timedelta(minutes=5), prior=a)
        self.assertNotEqual(list(a["open"]), list(b["open"]))


class TestStampedeGuard(unittest.TestCase):
    class _Proc:
        def __init__(self, out, rc_=0):
            self.stdout, self.returncode = out, rc_

    def _runner(self, body, code=0):
        return lambda args: self._Proc(body, code)

    def test_does_not_dispatch_while_one_is_in_progress(self):
        s = rc.reconcile(board(250), now=NOW)
        ok, why = rc.should_dispatch_rebuild(
            s, token="t", runner=self._runner('{"workflow_runs":[{"status":"in_progress"}]}'))
        self.assertFalse(ok)
        self.assertIn("already queued or in progress", why)

    def test_does_not_dispatch_while_one_is_queued(self):
        s = rc.reconcile(board(250), now=NOW)
        ok, _ = rc.should_dispatch_rebuild(
            s, token="t", runner=self._runner('{"workflow_runs":[{"status":"queued"}]}'))
        self.assertFalse(ok)

    def test_dispatches_when_nothing_is_running(self):
        s = rc.reconcile(board(250), now=NOW)
        ok, _ = rc.should_dispatch_rebuild(
            s, token="t", runner=self._runner('{"workflow_runs":[{"status":"completed"}]}'))
        self.assertTrue(ok)

    def test_unknown_run_state_fails_closed(self):
        """A duplicate rebuild is worse than a delayed one, and the next
        tick is five minutes away."""
        s = rc.reconcile(board(250), now=NOW)
        ok, why = rc.should_dispatch_rebuild(s, token="t", runner=self._runner("not json"))
        self.assertFalse(ok)
        self.assertIn("cannot determine", why)

    def test_no_mismatch_never_dispatches(self):
        s = rc.reconcile(board(2), now=NOW)
        ok, why = rc.should_dispatch_rebuild(s, token="t", runner=self._runner('{"workflow_runs":[]}'))
        self.assertFalse(ok)
        self.assertEqual(why, "no open mismatch")


class TestAllThreeKindsRequestARebuild(unittest.TestCase):
    def test_only_a_full_rebuild_can_resolve_any_of_them(self):
        """A price refresh cannot fix a stale basis, a changed lineup, or a
        moved threshold -- which is exactly why the live overlay kept
        looking healthy while the board rotted underneath it."""
        lineup_board = board(5)
        lineup_board["lineup_basis"] = [{
            "game_pk": 1, "side": "away", "team": "T", "matchup": "A @ H",
            "slots": [{"slot": i, "player_id": 900 + i} for i in range(1, 10)],
            "provenance": "assumed", "observed_at": NOW.isoformat(),
            "source": "test"}]
        for payload, lineups in (
            (board(250), None),
            (lineup_board, {(1, "away"): {i: 900 + i for i in range(1, 10)}}),
            (board(5, [prop(market_fetch_state="LINE_MOVED", market_posted_line=14.5)]), None),
        ):
            s = rc.reconcile(payload, confirmed_lineups=lineups, now=NOW)
            self.assertTrue(rc.needs_rebuild(s))


if __name__ == "__main__":
    unittest.main(verbosity=2)
