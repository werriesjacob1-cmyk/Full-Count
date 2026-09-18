#!/usr/bin/env python3
"""Point-in-time-safety and starter-change-detection contracts for QB continuity."""
import unittest

from nfl.research.qb_continuity_features import (
    QBContinuityError,
    build_prior_qb_continuity_features,
    infer_team_week_starters,
)


def qb_row(player_id, team, opponent, season, week, attempts, *, position="QB", season_type="REG"):
    return {
        "player_id": player_id,
        "position": position,
        "season": season,
        "week": week,
        "season_type": season_type,
        "team": team,
        "opponent_team": opponent,
        "attempts": attempts,
    }


class InferStartersTests(unittest.TestCase):
    def test_most_attempts_wins_starter(self):
        rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_B", "DEN", "KC", 2025, 1, 5),
        ]
        starters = infer_team_week_starters(rows)
        self.assertEqual(len(starters), 1)
        self.assertEqual(starters[0]["starter_player_id"], "QB_A")

    def test_tie_breaks_by_smallest_player_id(self):
        rows = [
            qb_row("QB_Z", "DEN", "KC", 2025, 1, 20),
            qb_row("QB_A", "DEN", "KC", 2025, 1, 20),
        ]
        starters = infer_team_week_starters(rows)
        self.assertEqual(starters[0]["starter_player_id"], "QB_A")

    def test_zero_attempts_produces_no_starter_row(self):
        rows = [qb_row("QB_A", "DEN", "KC", 2025, 1, 0)]
        self.assertEqual(infer_team_week_starters(rows), [])

    def test_non_qb_and_non_reg_rows_are_ignored_not_rejected(self):
        rows = [
            qb_row("RB_A", "DEN", "KC", 2025, 1, 10, position="RB"),
            qb_row("QB_A", "DEN", "KC", 2025, 1, 20, season_type="POST"),
        ]
        self.assertEqual(infer_team_week_starters(rows), [])

    def test_missing_column_fails_closed(self):
        row = qb_row("QB_A", "DEN", "KC", 2025, 1, 20)
        del row["attempts"]
        with self.assertRaisesRegex(QBContinuityError, "missing required columns"):
            infer_team_week_starters([row])

    def test_duplicate_player_week_fails_closed(self):
        rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 20),
            qb_row("QB_A", "DEN", "KC", 2025, 1, 5),
        ]
        with self.assertRaisesRegex(QBContinuityError, "duplicate QB/team/week row"):
            infer_team_week_starters(rows)

    def test_ambiguous_opponent_identity_fails_closed(self):
        rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 20),
            qb_row("QB_B", "DEN", "LV", 2025, 1, 5),
        ]
        with self.assertRaisesRegex(QBContinuityError, "ambiguous opponent identity"):
            infer_team_week_starters(rows)


class BuildPriorQBContinuityFeaturesTests(unittest.TestCase):
    def test_week_one_has_no_prior_starter_identity(self):
        rows = [qb_row("QB_A", "DEN", "KC", 2025, 1, 30)]
        built = build_prior_qb_continuity_features(rows)
        self.assertEqual(len(built), 1)
        row = built[0]
        self.assertEqual(row["prior_games_n"], 0)
        self.assertIsNone(row["features"]["prior_starter_player_id"])
        self.assertIsNone(row["features"]["qb_tenure_starts"])
        self.assertIsNone(row["target"]["starter_changed_from_prior"])
        self.assertEqual(row["target"]["actual_starter_player_id"], "QB_A")

    def test_same_starter_across_weeks_accumulates_tenure(self):
        rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
            qb_row("QB_A", "DEN", "LAC", 2025, 3, 25),
        ]
        built = build_prior_qb_continuity_features(rows)
        week2 = next(r for r in built if r["week"] == 2)
        week3 = next(r for r in built if r["week"] == 3)
        self.assertEqual(week2["features"]["prior_starter_player_id"], "QB_A")
        self.assertEqual(week2["features"]["qb_tenure_starts"], 1)
        self.assertFalse(week2["target"]["starter_changed_from_prior"])
        self.assertEqual(week3["features"]["qb_tenure_starts"], 2)

    def test_starter_change_is_detected_using_only_prior_game_data(self):
        """(c) QB-continuity feature correctly identifies a starter change.

        Week 3's FEATURE (prior_starter_player_id) must equal the week-2
        starter (QB_A) -- it must never "know" week 3's own starter is QB_B.
        Week 3's TARGET is where the realized change becomes visible, for a
        later evaluation harness only.
        """
        rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
            qb_row("QB_B", "DEN", "LAC", 2025, 3, 22),
        ]
        built = build_prior_qb_continuity_features(rows)
        week3 = next(r for r in built if r["week"] == 3)
        self.assertEqual(week3["features"]["prior_starter_player_id"], "QB_A")
        self.assertEqual(week3["features"]["qb_tenure_starts"], 2)
        self.assertEqual(week3["target"]["actual_starter_player_id"], "QB_B")
        self.assertTrue(week3["target"]["starter_changed_from_prior"])

        # Tenure resets: the week AFTER the change has tenure 1 under the
        # new incumbent, not a continuation of QB_A's count.
        rows.append(qb_row("QB_B", "DEN", "MIA", 2025, 4, 24))
        built2 = build_prior_qb_continuity_features(rows)
        week4 = next(r for r in built2 if r["week"] == 4)
        self.assertEqual(week4["features"]["prior_starter_player_id"], "QB_B")
        self.assertEqual(week4["features"]["qb_tenure_starts"], 1)

    def test_features_never_contain_current_game_attempts_or_starter(self):
        """(a) point-in-time safety: features dict is structurally free of
        this game's own starter/attempts information."""
        rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_B", "DEN", "LV", 2025, 2, 28),
        ]
        built = build_prior_qb_continuity_features(rows)
        week2 = next(r for r in built if r["week"] == 2)
        self.assertNotIn("actual_starter_player_id", week2["features"])
        self.assertNotIn("actual_starter_attempts", week2["features"])
        self.assertFalse(week2["features"]["current_game_attempts_used"])

    def test_future_weeks_do_not_change_past_feature_rows(self):
        """(a) point-in-time safety: appending a FUTURE week (even one that
        changes the starter) must not alter any earlier week's features."""
        base_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
        ]
        extended_rows = base_rows + [
            qb_row("QB_B", "DEN", "LAC", 2025, 3, 22),
        ]

        built_base = build_prior_qb_continuity_features(base_rows)
        built_extended = build_prior_qb_continuity_features(extended_rows)

        base_by_week = {r["week"]: r for r in built_base}
        extended_by_week = {r["week"]: r for r in built_extended}
        for week in (1, 2):
            self.assertEqual(
                base_by_week[week]["features"],
                extended_by_week[week]["features"],
                f"week {week} features changed after appending a future week",
            )
            self.assertEqual(
                base_by_week[week]["target"],
                extended_by_week[week]["target"],
                f"week {week} target changed after appending a future week",
            )

    def test_rolling_window_bounds_prior_starters_list(self):
        rows = [
            qb_row("QB_A", "DEN", f"OPP{i}", 2025, i, 20)
            for i in range(1, 8)
        ]
        built = build_prior_qb_continuity_features(rows, rolling_window=3)
        week8_equivalent = built[-1]
        self.assertLessEqual(
            len(week8_equivalent["features"]["prior_starters_last_n"]), 3
        )

    def test_invalid_rolling_window_fails_closed(self):
        with self.assertRaisesRegex(QBContinuityError, "positive integer"):
            build_prior_qb_continuity_features([], rolling_window=0)


if __name__ == "__main__":
    unittest.main()
