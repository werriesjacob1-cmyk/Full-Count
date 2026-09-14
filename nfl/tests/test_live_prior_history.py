#!/usr/bin/env python3
"""Contracts for live strictly-prior NFL player history."""
import unittest

from nfl.research.live_prior_history import collect_prior_appearances


class LivePriorHistoryTests(unittest.TestCase):
    def test_week_one_excludes_all_current_regular_season_rows(self):
        rows = [
            {"player_id": "QB1", "season": 2025, "week": 18, "season_type": "REG", "team": "KC"},
            {"player_id": "QB1", "season": 2025, "week": 19, "season_type": "POST", "team": "KC"},
            {"player_id": "QB1", "season": 2026, "week": 1, "season_type": "REG", "team": "KC"},
        ]
        out = collect_prior_appearances(rows, target_season=2026, target_week=1)
        self.assertEqual(
            [(r["season"], r["season_type"], r["week"]) for r in out["QB1"]],
            [(2025, "REG", 18), (2025, "POST", 19)],
        )

    def test_week_two_includes_week_one_but_not_target_week(self):
        rows = [
            {"player_id": "QB1", "season": 2025, "week": 19, "season_type": "POST", "team": "KC"},
            {"player_id": "QB1", "season": 2026, "week": 1, "season_type": "REG", "team": "KC"},
            {"player_id": "QB1", "season": 2026, "week": 2, "season_type": "REG", "team": "KC"},
        ]
        out = collect_prior_appearances(rows, target_season=2026, target_week=2)
        self.assertEqual(
            [(r["season"], r["season_type"], r["week"]) for r in out["QB1"]],
            [(2025, "POST", 19), (2026, "REG", 1)],
        )

    def test_latest_prior_team_is_true_latest_row(self):
        rows = [
            {"player_id": "QB1", "season": 2025, "week": 18, "season_type": "REG", "team": "OLD"},
            {"player_id": "QB1", "season": 2026, "week": 1, "season_type": "REG", "team": "NEW"},
        ]
        out = collect_prior_appearances(rows, target_season=2026, target_week=2)
        self.assertEqual(out["QB1"][-1]["team"], "NEW")

    def test_future_season_is_excluded(self):
        rows = [
            {"player_id": "QB1", "season": 2025, "week": 1, "season_type": "REG"},
            {"player_id": "QB1", "season": 2027, "week": 1, "season_type": "REG"},
        ]
        out = collect_prior_appearances(rows, target_season=2026, target_week=1)
        self.assertEqual(len(out["QB1"]), 1)
        self.assertEqual(out["QB1"][0]["season"], 2025)

    def test_duplicate_player_week_fails_closed(self):
        rows = [
            {"player_id": "QB1", "season": 2025, "week": 1, "season_type": "REG"},
            {"player_id": "QB1", "season": 2025, "week": 1, "season_type": "REG"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            collect_prior_appearances(rows, target_season=2026, target_week=1)

    def test_same_season_post_row_is_not_prior_to_regular_target(self):
        rows = [
            {"player_id": "QB1", "season": 2026, "week": 1, "season_type": "POST"},
        ]
        out = collect_prior_appearances(rows, target_season=2026, target_week=2)
        self.assertNotIn("QB1", out)

    def test_postseason_target_includes_all_regular_rows(self):
        rows = [
            {"player_id": "QB1", "season": 2026, "week": 18, "season_type": "REG", "team": "KC"},
            {"player_id": "QB1", "season": 2026, "week": 19, "season_type": "POST", "team": "KC"},
            {"player_id": "QB1", "season": 2026, "week": 20, "season_type": "POST", "team": "KC"},
        ]
        out = collect_prior_appearances(
            rows,
            target_season=2026,
            target_week=20,
            target_season_type="POST",
        )
        self.assertEqual(
            [(r["season_type"], r["week"]) for r in out["QB1"]],
            [("REG", 18), ("POST", 19)],
        )

    def test_invalid_target_week_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            collect_prior_appearances([], target_season=2026, target_week=0)

    def test_unknown_season_type_fails_closed(self):
        rows = [{"player_id": "QB1", "season": 2025, "week": 1, "season_type": "PRE"}]
        with self.assertRaisesRegex(ValueError, "unsupported"):
            collect_prior_appearances(rows, target_season=2026, target_week=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
