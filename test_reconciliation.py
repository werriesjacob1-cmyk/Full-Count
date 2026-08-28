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


class TestBoardAge(unittest.TestCase):
    def test_fresh_board_is_no_mismatch(self):
        self.assertIsNone(rc.board_age_mismatch(board(10), now=NOW))

    def test_stale_board_is_a_mismatch(self):
        m = rc.board_age_mismatch(board(200), now=NOW)
        self.assertEqual(m["kind"], rc.KIND_BOARD_AGE)

    def test_unknown_age_is_never_treated_as_fresh(self):
        m = rc.board_age_mismatch({"generated_at": None}, now=NOW)
        self.assertIsNotNone(m)

    def test_recovery_fires_before_actionability_is_lost(self):
        """The whole point of a separate recovery threshold: start rebuilding
        while the board is still bettable, so the product limit is never
        reached. Recovery earlier than suppression, not stricter than it."""
        self.assertLess(rc.RECOVERY_BOARD_AGE_MINUTES,
                        recommendation.MAX_BOARD_AGE_SECONDS / 60)


class TestLineups(unittest.TestCase):
    def test_confirmed_lineup_while_we_still_say_assumed(self):
        p = board(5, [prop(lineup_assumed=True, batting_order=3, player_id=9)])
        out = rc.lineup_mismatches(p, {1: {3: 9}})
        self.assertEqual(len(out), 1)
        self.assertIn("ASSUMED", out[0]["detail"])

    def test_published_order_differs_from_confirmed(self):
        p = board(5, [prop(lineup_assumed=False, batting_order=3, player_id=9)])
        out = rc.lineup_mismatches(p, {1: {3: 77}})
        self.assertEqual(len(out), 1)
        self.assertIn("differs", out[0]["detail"])

    def test_matching_confirmed_lineup_is_not_a_mismatch(self):
        p = board(5, [prop(lineup_assumed=False, batting_order=3, player_id=9)])
        self.assertEqual(rc.lineup_mismatches(p, {1: {3: 9}}), [])

    def test_no_posted_lineup_is_not_a_mismatch(self):
        """Assumed is the honest best answer before anyone posts."""
        p = board(5, [prop(lineup_assumed=True, batting_order=3, player_id=9)])
        self.assertEqual(rc.lineup_mismatches(p, {}), [])

    def test_fingerprint_tracks_the_authoritative_lineup(self):
        p = board(5, [prop(lineup_assumed=True)])
        a = rc.lineup_mismatches(p, {1: {3: 9}})[0]["fingerprint"]
        b = rc.lineup_mismatches(p, {1: {3: 77}})[0]["fingerprint"]
        self.assertNotEqual(a, b, "a different lineup must open a different mismatch")


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
        self.stale = board(200)

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
        a = rc.reconcile(board(200), now=NOW)
        b = rc.reconcile(board(300), now=NOW + timedelta(minutes=5), prior=a)
        self.assertNotEqual(list(a["open"]), list(b["open"]))


class TestStampedeGuard(unittest.TestCase):
    class _Proc:
        def __init__(self, out, rc_=0):
            self.stdout, self.returncode = out, rc_

    def _runner(self, body, code=0):
        return lambda args: self._Proc(body, code)

    def test_does_not_dispatch_while_one_is_in_progress(self):
        s = rc.reconcile(board(200), now=NOW)
        ok, why = rc.should_dispatch_rebuild(
            s, token="t", runner=self._runner('{"workflow_runs":[{"status":"in_progress"}]}'))
        self.assertFalse(ok)
        self.assertIn("already queued or in progress", why)

    def test_does_not_dispatch_while_one_is_queued(self):
        s = rc.reconcile(board(200), now=NOW)
        ok, _ = rc.should_dispatch_rebuild(
            s, token="t", runner=self._runner('{"workflow_runs":[{"status":"queued"}]}'))
        self.assertFalse(ok)

    def test_dispatches_when_nothing_is_running(self):
        s = rc.reconcile(board(200), now=NOW)
        ok, _ = rc.should_dispatch_rebuild(
            s, token="t", runner=self._runner('{"workflow_runs":[{"status":"completed"}]}'))
        self.assertTrue(ok)

    def test_unknown_run_state_fails_closed(self):
        """A duplicate rebuild is worse than a delayed one, and the next
        tick is five minutes away."""
        s = rc.reconcile(board(200), now=NOW)
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
        for payload, lineups in (
            (board(200), None),
            (board(5, [prop(lineup_assumed=True)]), {1: {3: 9}}),
            (board(5, [prop(market_fetch_state="LINE_MOVED", market_posted_line=14.5)]), None),
        ):
            s = rc.reconcile(payload, confirmed_lineups=lineups, now=NOW)
            self.assertTrue(rc.needs_rebuild(s))


if __name__ == "__main__":
    unittest.main(verbosity=2)
