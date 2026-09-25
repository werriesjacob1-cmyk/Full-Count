#!/usr/bin/env python3
"""Focused tests for overdue immutable-public-grade alerting."""
from __future__ import annotations

import os
import unittest
from unittest import mock

import grade_results as gr


FINAL = {"codedGameState": "F", "detailedState": "Final"}
LIVE = {"codedGameState": "I", "detailedState": "In Progress"}
POSTPONED = {"codedGameState": "D", "detailedState": "Postponed"}
SUSPENDED = {"codedGameState": "U", "detailedState": "Suspended"}
CANCELLED = {"codedGameState": "C", "detailedState": "Cancelled"}


def row(state="ungraded", **overrides):
    value = {
        "id": "fc2:123:player-456:hits:1:over",
        "game_pk": 123,
        "player_id": 456,
        "grade": state,
        "settlement_state": state,
        "reason": "game_not_authoritatively_final",
    }
    value.update(overrides)
    return value


def contexts(status):
    return {123: {"status": status, "feed": {"gameData": {"status": status}}}}


class OverduePublicGradeAlertsTests(unittest.TestCase):
    def test_final_ungraded_pick_is_actionable(self):
        alerts = gr.overdue_public_grade_alerts("2026-09-20", [row()], contexts(FINAL))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["game_pk"], 123)
        self.assertEqual(alerts[0]["reason"], "game_not_authoritatively_final")

    def test_every_terminal_settlement_is_quiet(self):
        rows = [row(state) for state in ("hit", "miss", "void")]
        self.assertEqual(gr.overdue_public_grade_alerts("2026-09-20", rows, contexts(FINAL)), [])

    def test_live_game_is_retryable_without_false_alarm(self):
        self.assertEqual(gr.overdue_public_grade_alerts("2026-09-20", [row()], contexts(LIVE)), [])

    def test_postponed_suspended_and_cancelled_are_not_called_final(self):
        for status in (POSTPONED, SUSPENDED, CANCELLED):
            with self.subTest(status=status["detailedState"]):
                self.assertEqual(
                    gr.overdue_public_grade_alerts("2026-09-20", [row()], contexts(status)),
                    [],
                )

    def test_source_failure_is_retryable_without_false_alarm(self):
        self.assertEqual(gr.overdue_public_grade_alerts("2026-09-20", [row()], {}), [])

    def test_duplicate_registry_identity_emits_one_alert(self):
        alerts = gr.overdue_public_grade_alerts(
            "2026-09-20", [row(), row(reason="second observation")], contexts(FINAL),
        )
        self.assertEqual(len(alerts), 1)

    def test_cli_returns_failure_only_after_grade_day_records_alert(self):
        def fake_grade_day(date):
            gr._OVERDUE_PUBLIC_GRADE_ALERTS.append({"date": date, "id": "x"})
            return True

        with mock.patch.dict(os.environ, {"GRADE_DATE": "2026-09-20"}, clear=False), \
             mock.patch.object(gr, "grade_day", side_effect=fake_grade_day):
            self.assertEqual(gr.main(), 2)

    def test_batch_continues_but_returns_failure_when_one_date_raises(self):
        attempted = []

        def fake_grade_day(date):
            attempted.append(date)
            if date == "2026-09-19":
                raise RuntimeError("registry unavailable")
            return True

        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(gr, "days_with_no_board", return_value=[]), \
             mock.patch.object(gr, "dates_needing_grading", return_value=["2026-09-19", "2026-09-20"]), \
             mock.patch.object(gr, "grade_day", side_effect=fake_grade_day), \
             mock.patch.object(gr.m, "warn"):
            self.assertEqual(gr.main(), 1)
        self.assertEqual(attempted, ["2026-09-19", "2026-09-20"])

    def test_manual_grade_date_must_be_exact_and_path_safe(self):
        for value in ("2026-9-20", "2026-02-30", "../../docs/history.json"):
            with self.subTest(value=value), \
                 mock.patch.dict(os.environ, {"GRADE_DATE": value}, clear=True), \
                 mock.patch.object(gr, "grade_day") as grade_day:
                self.assertEqual(gr.main(), 64)
                grade_day.assert_not_called()


if __name__ == "__main__":
    unittest.main()
