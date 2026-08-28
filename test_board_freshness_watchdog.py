#!/usr/bin/env python3
"""The blind spot: a fresh price overlay on a stale board.

2026-08-28. The Live Freshness Watchdog was green for the entire ten-hour
outage and was RIGHT to be -- docs/live.json really was updating every few
minutes. Nothing anywhere watched docs/data.json's own generated_at, so a
06:31:57Z model and lineup basis kept being repainted with current prices
and nothing raised a hand.

Measured on the real production payload during the incident:
    docs/data.json.generated_at   2026-08-28T06:31:57Z   (10.11 h old)
    docs/live.json.updated_at     2026-08-28T16:36:29Z   ( 0.03 h old)

This locks the checker's semantics, the three-way threshold ordering it
has to sit in, and the workflow's own honesty about what it can promise.
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dashboard.check_board_freshness import (  # noqa: E402
    HARD_FAIL_MINUTES, SLA_MINUTES, board_age_minutes, board_freshness,
)
import recommendation  # noqa: E402

NOW = datetime(2026, 8, 28, 16, 40, tzinfo=timezone.utc)


def board(age_minutes):
    return {"generated_at": (NOW - timedelta(minutes=age_minutes)).isoformat()}


class TestTheIncident(unittest.TestCase):
    def test_the_real_payload_is_classified_hard_stale(self):
        rep = board_freshness({"generated_at": "2026-08-28T06:31:57.954681+00:00"}, now=NOW)
        self.assertEqual(rep["state"], "hard_stale")
        self.assertFalse(rep["actionable"])
        self.assertAlmostEqual(rep["age_minutes"], 608.0, delta=1.0)

    def test_a_fresh_price_stamp_does_not_make_the_board_fresh(self):
        """The whole blind spot in one assertion: the checker must read the
        BOARD's timestamp, not any price/commit stamp sitting next to it."""
        payload = dict(board(600))
        payload["prices_updated_at"] = NOW.isoformat()
        payload["odds_fetched_at"] = NOW.isoformat()
        self.assertTrue(board_freshness(payload, now=NOW)["stale"])


class TestUnknownIsNeverFresh(unittest.TestCase):
    def test_missing_timestamp(self):
        rep = board_freshness({}, now=NOW)
        self.assertEqual(rep["state"], "unknown")
        self.assertTrue(rep["stale"])
        self.assertFalse(rep["actionable"])

    def test_malformed_timestamp(self):
        self.assertTrue(board_freshness({"generated_at": "not a date"}, now=NOW)["stale"])

    def test_naive_timestamp_is_refused_rather_than_assumed_utc(self):
        """A half-written payload is exactly what a naive stamp looks like."""
        self.assertIsNone(board_age_minutes({"generated_at": "2026-08-28T06:31:57"}, now=NOW))


class TestThresholds(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(board_freshness(board(SLA_MINUTES - 1), now=NOW)["state"], "fresh")
        self.assertEqual(board_freshness(board(SLA_MINUTES + 1), now=NOW)["state"], "stale")
        self.assertEqual(board_freshness(board(HARD_FAIL_MINUTES + 1), now=NOW)["state"], "hard_stale")

    def test_recover_before_suppressing_before_declaring_dead(self):
        """Three thresholds, three different questions, and the ORDER between
        them is the design:

            180 min  dispatch a recovery rebuild   (this watchdog's SLA)
            240 min  stop publishing Top Picks     (recommendation.py)
            360 min  declare the board unusable    (hard fail)

        Try to recover before you are forced to suppress, and suppress well
        before you call it dead. Inverting any pair silently changes the
        product's behaviour under failure, so it is asserted rather than
        left to a comment.
        """
        stop_recommending = recommendation.MAX_BOARD_AGE_SECONDS / 60
        self.assertLess(SLA_MINUTES, stop_recommending,
                        "recovery must be attempted before Top Picks are suppressed")
        self.assertLess(stop_recommending, HARD_FAIL_MINUTES,
                        "Top Picks must be suppressed before the board is called dead")


class TestCheckerNeverWrites(unittest.TestCase):
    def test_source_contains_no_write_path(self):
        """docs/data.json keeps exactly one semantic writer."""
        path = os.path.join(ROOT, "dashboard", "check_board_freshness.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for forbidden in ('open(', 'json.dump', 'atomic_write'):
            if forbidden == 'open(':
                self.assertNotIn('"w"', src, "the checker opens a file for writing")
                continue
            self.assertNotIn(forbidden, src, f"the checker calls {forbidden}")


class TestWorkflowHonesty(unittest.TestCase):
    def setUp(self):
        path = os.path.join(ROOT, ".github", "workflows", "board-freshness-watchdog.yml")
        with open(path, encoding="utf-8") as fh:
            self.wf = fh.read()

    def test_it_dispatches_a_recovery_and_fails_visibly(self):
        self.assertIn("gh workflow run dashboard-refresh.yml", self.wf)
        self.assertIn("::error::", self.wf)
        self.assertRegex(self.wf, r"exit 1")

    def test_it_does_not_write_the_board(self):
        """Checked against the EXECUTABLE steps only. The file's own prose
        says "never git commit age" -- matching that would be matching the
        explanation rather than the behaviour."""
        code = "\n".join(l for l in self.wf.splitlines()
                          if not l.lstrip().startswith("#"))
        self.assertNotIn("git push", code)
        self.assertNotIn("git commit", code)
        self.assertNotIn("git add", code)

    def test_it_does_not_claim_a_cadence_github_does_not_deliver(self):
        """Lineup Watch declares */10 and delivers 9% of it. Repeating that
        overclaim here would make this watchdog's own docs the next thing
        someone trusts and should not."""
        self.assertNotIn("Every 20 minutes,", self.wf,
                         "the schedule is described as a guarantee")
        self.assertIn("NOT A GUARANTEE", self.wf.upper(),
                      "the schedule's unreliability is not disclosed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
