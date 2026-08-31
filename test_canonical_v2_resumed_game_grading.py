#!/usr/bin/env python3
"""Adversarial tests for canonical-v2 suspended/resumed-game grading."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import grade_results as gr
from backtest.canonical_v2_grading import FrozenOutcomeGrader


class FakeStore:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def window(self, start_dt=None, end_dt=None, **kwargs):
        self.calls.append((str(start_dt), str(end_dt)))
        frame = self.frame
        if start_dt is not None:
            frame = frame[frame["game_date"] >= str(start_dt)]
        if end_dt is not None:
            frame = frame[frame["game_date"] <= str(end_dt)]
        return frame.copy()


def pick(game=100, player=10, stat="hard_hit_105"):
    return {
        "game_pk": game,
        "player_id": player,
        "type": "batter",
        "projection": {"stat": stat, "value": 1, "needs": 1},
        "prop": stat,
    }


def box_row():
    return {"ab": 4, "bb": 0, "substitution": False, "battingOrder": "100"}


FINAL = {100: {"codedGameState": "F", "detailedState": "Final"}}


class ResumedGameFallbackTests(unittest.TestCase):
    def _grade(self, grader, *, date="2025-05-21", stat="hard_hit_105"):
        with patch.object(gr, "get_box_line", return_value=(box_row(), None)), \
             patch.object(gr, "opportunity_context", return_value={"fair_test": True}):
            return grader.grade_pick(pick(stat=stat), FINAL, date=date)

    def test_exact_game_pk_on_original_date_can_grade_resumed_game(self):
        frame = pd.DataFrame([
            {
                "game_date": "2025-05-20",
                "game_pk": 100,
                "batter": 10,
                "events": "home_run",
                "launch_speed": 108.0,
                "hit_distance_sc": 425.0,
            },
            {
                "game_date": "2025-05-21",
                "game_pk": 999,
                "batter": 10,
                "events": "single",
                "launch_speed": 90.0,
                "hit_distance_sc": 200.0,
            },
        ])
        grader = FrozenOutcomeGrader(FakeStore(frame))
        result = self._grade(grader)
        self.assertEqual(result["grade"], "hit")
        self.assertEqual(
            result["canonical_v2_outcome_source"],
            "bound_predictor_statcast_parquet_exact_game_pk_fallback",
        )

    def test_wrong_prior_game_pk_cannot_satisfy_fallback(self):
        frame = pd.DataFrame([
            {
                "game_date": "2025-05-20",
                "game_pk": 999,
                "batter": 10,
                "events": "home_run",
                "launch_speed": 115.0,
                "hit_distance_sc": 450.0,
            },
            {
                "game_date": "2025-05-21",
                "game_pk": 998,
                "batter": 10,
                "events": "single",
                "launch_speed": 90.0,
                "hit_distance_sc": 200.0,
            },
        ])
        result = self._grade(FrozenOutcomeGrader(FakeStore(frame)))
        self.assertEqual(result["grade"], "ungraded")
        self.assertIn("no rows for this game", result["reason"])

    def test_future_exact_game_pk_cannot_be_borrowed(self):
        frame = pd.DataFrame([
            {
                "game_date": "2025-05-21",
                "game_pk": 999,
                "batter": 10,
                "events": "single",
                "launch_speed": 90.0,
                "hit_distance_sc": 200.0,
            },
            {
                "game_date": "2025-05-22",
                "game_pk": 100,
                "batter": 10,
                "events": "home_run",
                "launch_speed": 115.0,
                "hit_distance_sc": 450.0,
            },
        ])
        result = self._grade(FrozenOutcomeGrader(FakeStore(frame)))
        self.assertEqual(result["grade"], "ungraded")
        self.assertIn("no rows for this game", result["reason"])

    def test_ambiguous_original_dates_fail_closed(self):
        frame = pd.DataFrame([
            {
                "game_date": "2025-05-19",
                "game_pk": 100,
                "batter": 10,
                "events": "single",
                "launch_speed": 90.0,
                "hit_distance_sc": 200.0,
            },
            {
                "game_date": "2025-05-20",
                "game_pk": 100,
                "batter": 10,
                "events": "home_run",
                "launch_speed": 115.0,
                "hit_distance_sc": 450.0,
            },
            {
                "game_date": "2025-05-21",
                "game_pk": 999,
                "batter": 10,
                "events": "single",
                "launch_speed": 90.0,
                "hit_distance_sc": 200.0,
            },
        ])
        result = self._grade(FrozenOutcomeGrader(FakeStore(frame)))
        self.assertEqual(result["grade"], "ungraded")
        self.assertIn("ambiguous source dates", result["reason"])

    def test_same_day_path_remains_preferred(self):
        frame = pd.DataFrame([{
            "game_date": "2025-05-21",
            "game_pk": 100,
            "batter": 10,
            "events": "home_run",
            "launch_speed": 108.0,
            "hit_distance_sc": 425.0,
        }])
        store = FakeStore(frame)
        result = self._grade(FrozenOutcomeGrader(store))
        self.assertEqual(result["grade"], "hit")
        self.assertEqual(
            result["canonical_v2_outcome_source"],
            "bound_predictor_statcast_parquet",
        )
        self.assertEqual(store.calls, [("2025-05-21", "2025-05-21")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
