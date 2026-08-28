#!/usr/bin/env python3
"""A healthy observer must not make an unhealthy source look healthy.

check_live_freshness gated its health decision on the single global
`updated_at`. That was survivable while only the price and grade channels
wrote it -- both stall together when dashboard-live.yml stops running.

Reconciliation broke the assumption. It runs inside the same workflow,
succeeds on its own, and (on HEAD 2ee82ed7) set:

    live["updated_at"] = state["checked_at"]

So a reconciliation pass every five minutes would have advanced the health
clock while sportsbook pricing or game-state observation was dead. The
watchdog would have reported FRESH the entire time.

Every test in TestReconciliationCannotMaskAStaleChannel fails on
2ee82ed7: there, `updated_at` is fresh and `is_stale()` returns False
regardless of which upstream has stopped.

SEMANTICS, so no field can stand in for another:

    prices_checked_at          a real sportsbook observation ATTEMPT completed
    grades_checked_at          a real MLB game-state/settlement ATTEMPT completed
    reconciliation.checked_at  publication-vs-authoritative reconciliation ran
    *_updated_at               the corresponding FACTS actually changed
    updated_at                 "something changed" -- overlay recency ordering
                               only, deliberately NOT a health signal
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard import check_live_freshness as clf  # noqa: E402

NOW = datetime(2026, 8, 28, 21, 0, tzinfo=timezone.utc)


def ago(minutes):
    return (NOW - timedelta(minutes=minutes)).isoformat()


def live(*, prices=2, grades=2, recon=2, updated=2):
    """A live document with each clock at a chosen age in minutes."""
    doc = {}
    if prices is not None:
        doc["prices_checked_at"] = ago(prices)
    if grades is not None:
        doc["grades_checked_at"] = ago(grades)
    if recon is not None:
        doc["reconciliation"] = {"checked_at": ago(recon), "open": {}}
    if updated is not None:
        doc["updated_at"] = ago(updated)
    return doc


class TestReconciliationCannotMaskAStaleChannel(unittest.TestCase):
    """A-D from the mission. Each FAILS on HEAD 2ee82ed7."""

    def test_A_reconciliation_fresh_price_channel_dead(self):
        doc = live(prices=90, grades=2, recon=1, updated=1)
        stale, reason = clf.is_stale(doc, now=NOW)
        self.assertTrue(stale, f"a dead price channel must not read healthy: {reason}")
        rep = clf.health(doc, now=NOW)
        self.assertFalse(rep["healthy"])
        self.assertEqual(rep["degraded_channels"], ["sportsbook_price"])

    def test_B_reconciliation_fresh_grade_channel_dead(self):
        doc = live(prices=2, grades=90, recon=1, updated=1)
        stale, _ = clf.is_stale(doc, now=NOW)
        self.assertTrue(stale)
        rep = clf.health(doc, now=NOW)
        self.assertFalse(rep["healthy"])
        self.assertEqual(rep["degraded_channels"], ["game_state_and_settlement"])

    def test_C_price_fresh_grades_stale(self):
        rep = clf.health(live(prices=1, grades=45, recon=1, updated=1), now=NOW)
        self.assertFalse(rep["healthy"])
        self.assertIn("game_state_and_settlement", rep["degraded_channels"])

    def test_D_grades_fresh_price_stale(self):
        rep = clf.health(live(prices=45, grades=1, recon=1, updated=1), now=NOW)
        self.assertFalse(rep["healthy"])
        self.assertIn("sportsbook_price", rep["degraded_channels"])

    def test_E_both_required_channels_fresh_is_healthy(self):
        rep = clf.health(live(prices=2, grades=3, recon=2), now=NOW)
        self.assertTrue(rep["healthy"])
        self.assertEqual(rep["degraded_channels"], [])
        self.assertFalse(clf.is_stale(live(prices=2, grades=3), now=NOW)[0])

    def test_F_reconciliation_itself_stale_is_reported_not_fatal(self):
        """Reconciliation stalling is worth SEEING. It is not the same
        failure as an upstream stalling, and conflating them would repeat
        the mistake in the other direction."""
        rep = clf.health(live(prices=2, grades=2, recon=120), now=NOW)
        self.assertTrue(rep["healthy"], "upstreams are fine; this is not an upstream failure")
        self.assertTrue(rep["reconciliation_stale"], "but it must be visible")


class TestUpdatedAtIsNotTheGate(unittest.TestCase):
    def test_a_fresh_updated_at_cannot_rescue_a_dead_channel(self):
        """The exact 2ee82ed7 shape: updated_at seconds old, price channel
        an hour and a half dead."""
        doc = live(prices=90, grades=90, recon=1, updated=0)
        self.assertTrue(clf.is_stale(doc, now=NOW)[0])

    def test_updated_at_is_not_consulted_for_health(self):
        with_updated = live(prices=90, grades=2, updated=0)
        without = live(prices=90, grades=2, updated=None)
        self.assertEqual(clf.health(with_updated, now=NOW)["healthy"],
                         clf.health(without, now=NOW)["healthy"])

    def test_reconciliation_is_not_in_the_required_set(self):
        self.assertNotIn(clf.RECONCILIATION_CHANNEL, clf.REQUIRED_CHANNELS)
        self.assertIn("prices_checked_at", clf.REQUIRED_CHANNELS.values())
        self.assertIn("grades_checked_at", clf.REQUIRED_CHANNELS.values())


class TestUnknownIsNeverFresh(unittest.TestCase):
    def test_missing_price_channel(self):
        self.assertTrue(clf.is_stale(live(prices=None), now=NOW)[0])

    def test_missing_grade_channel(self):
        self.assertTrue(clf.is_stale(live(grades=None), now=NOW)[0])

    def test_summary_reports_the_worst_channel_not_the_newest(self):
        age, reason = clf.staleness_minutes(live(prices=1, grades=50), now=NOW)
        self.assertAlmostEqual(age, 50, delta=0.2)
        self.assertIn("game_state_and_settlement", reason)


class TestReconciliationDoesNotWriteTheGlobalClock(unittest.TestCase):
    def test_runner_never_assigns_updated_at(self):
        """Source-level, because this is the line that created the masking."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dashboard", "run_reconciliation.py")
        with open(path, encoding="utf-8") as fh:
            code = "\n".join(l for l in fh.read().splitlines()
                             if not l.lstrip().startswith("#"))
        self.assertNotIn('live["updated_at"]', code,
                         "reconciliation must not advance the global clock")
        self.assertIn('live["reconciliation"] = state', code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
