#!/usr/bin/env python3
"""Tests for canonical-v2 bound-Statcast outcome grading."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import grade_results as gr
from backtest.canonical_v2_grading import FrozenOutcomeGrader


class FakeStore:
    def __init__(self, frame):
        self.frame = frame

    def window(self, start_dt=None, end_dt=None, **kwargs):
        frame = self.frame
        if start_dt is not None:
            frame = frame[frame["game_date"] >= str(start_dt)]
        if end_dt is not None:
            frame = frame[frame["game_date"] <= str(end_dt)]
        return frame.copy()


def pick(stat, player=10, game=100):
    return {
        "game_pk": game,
        "player_id": player,
        "type": "batter",
        "projection": {"stat": stat, "value": 1, "needs": 1},
        "prop": stat,
    }


def box_row():
    return {
        "ab": 4,
        "bb": 0,
        "substitution": False,
        "battingOrder": "100",
    }


FINAL = {100: {"codedGameState": "F", "detailedState": "Final"}}


class FrozenOutcomeTests(unittest.TestCase):
    def test_hard_hit_true_from_bound_day_frame(self):
        frame = pd.DataFrame([{
            "game_date": "2025-08-20",
            "game_pk": 100,
            "batter": 10,
            "events": "home_run",
            "launch_speed": 108.0,
            "hit_distance_sc": 410.0,
        }])
        grader = FrozenOutcomeGrader(FakeStore(frame))
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            result = grader.grade_pick(
                pick("hard_hit_105"),
                FINAL,
                date="2025-08-20",
            )
        self.assertEqual(result["grade"], "hit")
        self.assertTrue(result["actual"])
        self.assertEqual(result["threshold"], 105)
        self.assertEqual(
            result["canonical_v2_outcome_source"],
            "bound_predictor_statcast_parquet",
        )

    def test_appeared_without_qualifying_event_is_legitimate_miss(self):
        frame = pd.DataFrame([
            {
                "game_date": "2025-08-20",
                "game_pk": 100,
                "batter": 10,
                "events": "single",
                "launch_speed": 102.0,
                "hit_distance_sc": 250.0,
            },
            {
                "game_date": "2025-08-20",
                "game_pk": 100,
                "batter": 11,
                "events": "home_run",
                "launch_speed": 112.0,
                "hit_distance_sc": 430.0,
            },
        ])
        grader = FrozenOutcomeGrader(FakeStore(frame))
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            laser = grader.grade_pick(
                pick("hard_hit_105"),
                FINAL,
                date="2025-08-20",
            )
            moon = grader.grade_pick(
                pick("moonshot_420"),
                FINAL,
                date="2025-08-20",
            )
        self.assertEqual(laser["grade"], "miss")
        self.assertFalse(laser["actual"])
        self.assertEqual(moon["grade"], "miss")
        self.assertFalse(moon["actual"])

    def test_player_with_box_appearance_but_no_statcast_batter_row_is_miss_if_game_covered(self):
        frame = pd.DataFrame([{
            "game_date": "2025-08-20",
            "game_pk": 100,
            "batter": 99,
            "events": "strikeout",
            "launch_speed": None,
            "hit_distance_sc": None,
        }])
        grader = FrozenOutcomeGrader(FakeStore(frame))
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            result = grader.grade_pick(
                pick("moonshot_420", player=10),
                FINAL,
                date="2025-08-20",
            )
        self.assertEqual(result["grade"], "miss")

    def test_missing_game_in_bound_statcast_stays_ungraded(self):
        frame = pd.DataFrame([{
            "game_date": "2025-08-20",
            "game_pk": 999,
            "batter": 10,
            "events": "single",
            "launch_speed": 90.0,
            "hit_distance_sc": 200.0,
        }])
        grader = FrozenOutcomeGrader(FakeStore(frame))
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            result = grader.grade_pick(
                pick("hard_hit_105"),
                FINAL,
                date="2025-08-20",
            )
        self.assertEqual(result["grade"], "ungraded")
        self.assertIn("no rows for this game", result["reason"])

    def test_player_dnp_stays_ungraded_even_if_game_is_covered(self):
        frame = pd.DataFrame([{
            "game_date": "2025-08-20",
            "game_pk": 100,
            "batter": 99,
            "events": "single",
            "launch_speed": 90.0,
            "hit_distance_sc": 200.0,
        }])
        grader = FrozenOutcomeGrader(FakeStore(frame))
        with patch.object(
            gr,
            "get_box_line",
            return_value=(None, "player not found in box score (scratched or DNP)"),
        ):
            result = grader.grade_pick(
                pick("hard_hit_105"),
                FINAL,
                date="2025-08-20",
            )
        self.assertEqual(result["grade"], "ungraded")
        self.assertIn("scratched or DNP", result["reason"])


    def test_fetch_game_statuses_switches_http_to_outcome_phase(self):
        class FakeLedger:
            def __init__(self):
                self.phases = []

            def set_phase(self, phase):
                self.phases.append(phase)

        grader = FrozenOutcomeGrader(FakeStore(pd.DataFrame()))
        grader._original_fetch_game_statuses = lambda *args, **kwargs: {"ok": True}
        ledger = FakeLedger()
        with patch(
            "backtest.canonical_v2_grading.get_active_ledger",
            return_value=ledger,
        ):
            result = grader.fetch_game_statuses("2025-08-20")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(ledger.phases, ["outcome_grading"])


    def test_final_day_outcome_frame_never_reads_predictor_store(self):
        class ExplodingPredictorStore:
            def window(self, *args, **kwargs):
                raise AssertionError(
                    "predictor Statcast store must not be asked for outcome-only date"
                )

        frame = pd.DataFrame([{
            "game_date": "2026-08-25",
            "game_pk": 100,
            "batter": 10,
            "events": "home_run",
            "launch_speed": 108.0,
            "hit_distance_sc": 425.0,
        }])
        grader = FrozenOutcomeGrader(
            ExplodingPredictorStore(),
            outcome_only_frame=frame,
            outcome_only_date="2026-08-25",
        )
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            result = grader.grade_pick(
                pick("moonshot_420"),
                FINAL,
                date="2026-08-25",
            )
        self.assertEqual(result["grade"], "hit")
        self.assertEqual(
            result["canonical_v2_outcome_source"],
            "bound_outcome_only_statcast_parquet",
        )

    def test_final_day_without_outcome_only_frame_fails_ungraded(self):
        grader = FrozenOutcomeGrader(
            FakeStore(pd.DataFrame()),
            outcome_only_date="2026-08-25",
        )
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            result = grader.grade_pick(
                pick("hard_hit_105"),
                FINAL,
                date="2026-08-25",
            )
        self.assertEqual(result["grade"], "ungraded")
        self.assertIn("lacks required same-day", result["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
