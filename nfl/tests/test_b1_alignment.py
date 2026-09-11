#!/usr/bin/env python3
"""Tests for B1 player-window versus current-team-window alignment."""
import unittest

from nfl.research import b1_alignment as al


def g(season, week, team):
    return (season, week, "REG", team)


class AlignmentClassification(unittest.TestCase):
    def test_exact_same_prior_game_window_is_aligned(self):
        player = [g(2025, w, "PHI") for w in (1, 2, 3, 4, 5)]
        team = [g(2025, w, "PHI") for w in (1, 2, 3, 4, 5)]
        self.assertEqual(
            al.classify_history_alignment(player, team, current_team="PHI"),
            "ALIGNED",
        )

    def test_prior_game_on_other_team_is_team_change(self):
        player = [
            g(2025, 1, "TEN"),
            g(2025, 2, "PHI"),
            g(2025, 3, "PHI"),
            g(2025, 4, "PHI"),
            g(2025, 5, "PHI"),
        ]
        team = [g(2025, w, "PHI") for w in (1, 2, 3, 4, 5)]
        self.assertEqual(
            al.classify_history_alignment(player, team, current_team="PHI"),
            "TEAM_CHANGE",
        )

    def test_same_team_full_window_with_gap_is_missed_game(self):
        player = [g(2025, w, "PHI") for w in (1, 2, 4, 5, 6)]
        team = [g(2025, w, "PHI") for w in (2, 3, 4, 5, 6)]
        self.assertEqual(
            al.classify_history_alignment(player, team, current_team="PHI"),
            "MISSED_GAMES",
        )

    def test_unequal_window_lengths_are_explicit(self):
        player = [g(2025, w, "PHI") for w in (3, 4, 5)]
        team = [g(2025, w, "PHI") for w in (1, 2, 3, 4, 5)]
        self.assertEqual(
            al.classify_history_alignment(player, team, current_team="PHI"),
            "WINDOW_LENGTH_MISMATCH",
        )

    def test_empty_history_is_not_called_aligned(self):
        self.assertEqual(
            al.classify_history_alignment([], [], current_team="PHI"),
            "NO_HISTORY",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
