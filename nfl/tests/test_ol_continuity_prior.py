#!/usr/bin/env python3
import unittest

from nfl.research.ol_continuity_prior import OLContinuityError, build_prior_ol_continuity


def row(game, season, week, team, opp, player, pos, snaps):
    return {
        "game_id": game, "season": season, "game_type": "REG", "week": week,
        "pfr_player_id": player, "position": pos, "team": team, "opponent": opp,
        "offense_snaps": snaps,
    }


def team_game(game, season, week, team, opp, ids):
    rows = []
    # QB establishes team offensive snap denominator independently of OL rotation.
    rows.append(row(game, season, week, team, opp, f"qb-{team}-{week}", "QB", 70))
    for i, player in enumerate(ids):
        rows.append(row(game, season, week, team, opp, player, "T" if i < 2 else "G", 70 - i))
    return rows


class OLContinuityTests(unittest.TestCase):
    def test_current_game_snap_distribution_does_not_enter_target_features(self):
        rows = []
        rows += team_game("g1", 2025, 1, "DEN", "KC", ["a","b","c","d","e"])
        rows += team_game("g2", 2025, 2, "DEN", "LV", ["a","b","c","d","f"])
        rows += team_game("g3", 2025, 3, "DEN", "LAC", ["x","y","z","u","v"])
        built = build_prior_ol_continuity(rows)
        w2 = next(r for r in built if r["week"] == 2)
        w3 = next(r for r in built if r["week"] == 3)
        self.assertEqual(w2["prior_games_n"], 1)
        self.assertEqual(set(w2["prior_last_game_top5_ids"]), {"a","b","c","d","e"})
        # Week 3 overlap is between Weeks 1 and 2 only: 4 intersection / 6 union.
        self.assertAlmostEqual(w3["prior_last_two_top5_jaccard"], 4/6)
        self.assertFalse(w3["current_game_snap_counts_used"])
        self.assertFalse(w3["upcoming_starting_five_known"])

    def test_history_may_cross_season_boundary(self):
        rows = []
        rows += team_game("old", 2025, 18, "KC", "DEN", ["a","b","c","d","e"])
        rows += team_game("new", 2026, 1, "KC", "LV", ["a","b","c","d","f"])
        built = build_prior_ol_continuity(rows)
        new = next(r for r in built if r["season"] == 2026)
        self.assertEqual(new["prior_games_n"], 1)
        self.assertEqual(set(new["prior_last_game_top5_ids"]), {"a","b","c","d","e"})

    def test_duplicate_player_game_fails_closed(self):
        duplicate = row("g1", 2025, 1, "DEN", "KC", "a", "T", 70)
        with self.assertRaisesRegex(OLContinuityError, "duplicate player/game"):
            build_prior_ol_continuity([duplicate, dict(duplicate)])

    def test_no_recognized_ol_rows_fails_closed(self):
        rows = [row("g1", 2025, 1, "DEN", "KC", "qb", "QB", 70)]
        with self.assertRaisesRegex(OLContinuityError, "no recognized OL"):
            build_prior_ol_continuity(rows)

    def test_postseason_rejected(self):
        bad = row("g1", 2025, 1, "DEN", "KC", "a", "T", 70)
        bad["game_type"] = "POST"
        with self.assertRaisesRegex(OLContinuityError, "REG rows only"):
            build_prior_ol_continuity([bad])


if __name__ == "__main__":
    unittest.main()
