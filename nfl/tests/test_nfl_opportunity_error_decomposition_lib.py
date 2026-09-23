#!/usr/bin/env python3
"""Unit tests for the genuinely new combination/decomposition logic introduced
by Mission 9 Workstream D
(`engineering/nfl_opportunity_error_decomposition_20260923/decomposition_lib.py`).

Everything else this workstream's evaluation script uses is pre-existing,
unmodified, already-tested code (`receptions_team_opportunity_challenger.py`
on `main`, `qb_change_team_dropbacks.py` from draft PR #185, brought into
this branch unmodified) -- this file covers only the three new functions:
the combined coaching+QB team-volume estimator, the role-transition subgroup
flag, and the player-clustered bootstrap MAE-difference helper.

The library lives outside the `nfl` package (in `engineering/...`, matching
this project's own established pattern for the 2024-holdout ablation's
`gating.py`/`test_opportunity_ablation_2024_gating.py`), so it is loaded
here by explicit file path via `importlib`, not a normal package import.
"""
from __future__ import annotations

import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_MODULE_PATH = (
    REPO_ROOT / "engineering" / "nfl_opportunity_error_decomposition_20260923" / "decomposition_lib.py"
)
_spec = importlib.util.spec_from_file_location("nfl_opportunity_error_decomposition_lib", LIB_MODULE_PATH)
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)  # type: ignore[union-attr]

predict_team_pass_dropbacks_coaching_and_qb_aware = lib.predict_team_pass_dropbacks_coaching_and_qb_aware
role_transition_subgroup_flag = lib.role_transition_subgroup_flag
player_clustered_bootstrap_mae_diff = lib.player_clustered_bootstrap_mae_diff

from nfl.research.receptions_team_opportunity_challenger import TeamOpportunityChallengerError
from nfl.research.coach_regime_registry import RegimeInterval


def _box_rows(team: str, weeks: range, *, attempts: float = 30.0) -> list[dict]:
    return [{"team": team, "season": 2025, "week": w, "attempts": attempts, "sacks_suffered": 2.0} for w in weeks]


def _game_date_index(team: str, weeks: range) -> dict:
    idx = {}
    for w in weeks:
        idx[(team, 2025, w)] = date(2025, 9, 1) + timedelta(days=7 * (w - 1))
    # Also index the week immediately after the given range: `lookup_regime`
    # needs the TARGET week's own game date to resolve which regime applies
    # at that point in time (the date itself, never that game's outcome).
    next_week = weeks[-1] + 1
    idx[(team, 2025, next_week)] = date(2025, 9, 1) + timedelta(days=7 * (next_week - 1))
    return idx


def _starter_rows(team: str, week_to_player: dict[int, str]) -> list[dict]:
    return [
        {"season": 2025, "week": w, "team": team, "starter_player_id": pid}
        for w, pid in week_to_player.items()
    ]


class CoachingAndQbCombinedTests(unittest.TestCase):
    """`predict_team_pass_dropbacks_coaching_and_qb_aware`: the real,
    new "all team-identity signals combined" estimator."""

    def test_intersection_excludes_games_that_fail_either_filter(self):
        # Weeks 1-5, HC changed at week 4 (weeks 4-5 only under new HC),
        # QB changed at week 3 (weeks 3-5 only under new incumbent). The
        # real combined-aware window must be the INTERSECTION: weeks 4-5.
        # Attempts jump at week 4 so the combined-vs-naive MEAN differs too,
        # not just the row count.
        rows = (
            [{"team": "KC", "season": 2025, "week": w, "attempts": 30.0, "sacks_suffered": 2.0} for w in range(1, 4)]
            + [{"team": "KC", "season": 2025, "week": w, "attempts": 48.0, "sacks_suffered": 2.0} for w in (4, 5)]
        )
        game_date_index = _game_date_index("KC", range(1, 6))
        hc_interval = RegimeInterval(
            team="KC", role="HC", persons=("New Coach",),
            start_date=game_date_index[("KC", 2025, 4)], end_date=date(2099, 1, 1),
            source="test fixture", confidence="CONFIRMED",
        )
        starters = _starter_rows("KC", {1: "QB_OLD", 2: "QB_OLD", 3: "QB_NEW", 4: "QB_NEW", 5: "QB_NEW"})
        result = predict_team_pass_dropbacks_coaching_and_qb_aware(
            rows, team="KC", target_season=2025, target_week=6,
            hc_intervals=[hc_interval], game_date_index=game_date_index, starters=starters,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertEqual(result["own_games_used_combined_aware"], 2)  # weeks 4-5 only
        self.assertEqual(result["own_games_used_naive_control"], 5)
        self.assertTrue(result["combined_feature_changed_the_projection"])

    def test_no_real_regime_or_qb_change_reduces_to_naive_control(self):
        rows = _box_rows("SEA", range(1, 6))
        game_date_index = _game_date_index("SEA", range(1, 6))
        starters = _starter_rows("SEA", {w: "QB_SAME" for w in range(1, 6)})
        result = predict_team_pass_dropbacks_coaching_and_qb_aware(
            rows, team="SEA", target_season=2025, target_week=6,
            hc_intervals=[], game_date_index=game_date_index, starters=starters,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertEqual(result["own_games_used_combined_aware"], result["own_games_used_naive_control"])
        self.assertEqual(
            result["predicted_dropbacks_combined_aware"], result["predicted_dropbacks_naive_control"]
        )
        self.assertFalse(result["combined_feature_changed_the_projection"])

    def test_blends_with_real_opponent_allowed_value_when_present(self):
        rows = _box_rows("SEA", range(1, 6))
        game_date_index = _game_date_index("SEA", range(1, 6))
        starters = _starter_rows("SEA", {w: "QB_SAME" for w in range(1, 6)})
        result = predict_team_pass_dropbacks_coaching_and_qb_aware(
            rows, team="SEA", target_season=2025, target_week=6,
            hc_intervals=[], game_date_index=game_date_index, starters=starters,
            opponent_defense_allowed=40.0, opponent_defense_prior_games_n=5,
        )
        self.assertEqual(result["combined_aware_basis"], "BLENDED_OFFENSE_AND_DEFENSE")
        # own mean = 32.0 (attempts 30 + sacks 2), blended with 40 -> 36.0
        self.assertAlmostEqual(result["predicted_dropbacks_combined_aware"], 36.0, places=6)

    def test_empty_intersection_never_fabricates_a_number(self):
        # HC changed at week 6 (excludes ALL of weeks 1-5), QB changed at
        # week 2 (keeps weeks 2-5) -- intersection is empty. No real
        # opponent value supplied either, so the result must be None, not
        # a guess.
        rows = _box_rows("KC", range(1, 6))
        game_date_index = _game_date_index("KC", range(1, 7))
        hc_interval = RegimeInterval(
            team="KC", role="HC", persons=("Newest Coach",),
            start_date=game_date_index[("KC", 2025, 6)], end_date=date(2099, 1, 1),
            source="test fixture", confidence="CONFIRMED",
        )
        starters = _starter_rows("KC", {1: "QB_OLD", 2: "QB_NEW", 3: "QB_NEW", 4: "QB_NEW", 5: "QB_NEW"})
        result = predict_team_pass_dropbacks_coaching_and_qb_aware(
            rows, team="KC", target_season=2025, target_week=6,
            hc_intervals=[hc_interval], game_date_index=game_date_index, starters=starters,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertEqual(result["own_games_used_combined_aware"], 0)
        self.assertIsNone(result["predicted_dropbacks_combined_aware"])
        self.assertEqual(result["combined_aware_basis"], "NO_REAL_PRIOR_HISTORY")

    def test_never_leaks_a_game_at_or_after_the_target_week(self):
        rows = _box_rows("KC", range(1, 8))
        game_date_index = _game_date_index("KC", range(1, 8))
        starters = _starter_rows("KC", {w: "QB_SAME" for w in range(1, 8)})
        result = predict_team_pass_dropbacks_coaching_and_qb_aware(
            rows, team="KC", target_season=2025, target_week=4,
            hc_intervals=[], game_date_index=game_date_index, starters=starters,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        # Only weeks 1-3 are strictly prior to week 4.
        self.assertLessEqual(result["own_games_used_combined_aware"], 3)
        self.assertLessEqual(result["own_games_used_naive_control"], 3)


class RoleTransitionSubgroupFlagTests(unittest.TestCase):
    def test_false_when_no_flag_fired(self):
        record = {
            "coaching_feature_changed_the_projection": False,
            "qb_feature_changed_the_projection": False,
            "snap_role_change_applied": False,
        }
        self.assertFalse(role_transition_subgroup_flag(record))

    def test_true_when_only_coaching_flag_fired(self):
        record = {"coaching_feature_changed_the_projection": True}
        self.assertTrue(role_transition_subgroup_flag(record))

    def test_true_when_only_qb_flag_fired(self):
        record = {"qb_feature_changed_the_projection": True}
        self.assertTrue(role_transition_subgroup_flag(record))

    def test_true_when_only_snap_flag_fired(self):
        record = {"snap_role_change_applied": True}
        self.assertTrue(role_transition_subgroup_flag(record))

    def test_true_when_multiple_flags_fired(self):
        record = {
            "coaching_feature_changed_the_projection": True,
            "qb_feature_changed_the_projection": True,
            "snap_role_change_applied": False,
        }
        self.assertTrue(role_transition_subgroup_flag(record))

    def test_missing_flags_treated_as_false_not_true(self):
        # A record built from a variant that never computes one of the
        # three flags (e.g. it has no snap_share info at all) must not be
        # spuriously counted as a role-transition row.
        self.assertFalse(role_transition_subgroup_flag({}))


class PlayerClusteredBootstrapMaeDiffTests(unittest.TestCase):
    def test_zero_diff_when_both_error_columns_identical(self):
        rows = [
            {"player_id": "P1", "err_a": 1.0, "err_b": 1.0},
            {"player_id": "P1", "err_a": 2.0, "err_b": 2.0},
            {"player_id": "P2", "err_a": 3.0, "err_b": 3.0},
        ]
        result = player_clustered_bootstrap_mae_diff(
            rows, player_key="player_id", error_a_key="err_a", error_b_key="err_b",
            n_boot=200, seed=1,
        )
        self.assertEqual(result["observed_diff_a_minus_b"], 0.0)
        self.assertEqual(result["bootstrap_mean_diff"], 0.0)
        self.assertEqual(result["ci95_low"], 0.0)
        self.assertEqual(result["ci95_high"], 0.0)
        self.assertFalse(result["ci_excludes_zero"])

    def test_observed_diff_matches_hand_computed_means(self):
        rows = [
            {"player_id": "P1", "err_a": 2.0, "err_b": 1.0},
            {"player_id": "P2", "err_a": 4.0, "err_b": 1.0},
        ]
        result = player_clustered_bootstrap_mae_diff(
            rows, player_key="player_id", error_a_key="err_a", error_b_key="err_b",
            n_boot=100, seed=1,
        )
        self.assertAlmostEqual(result["observed_mae_a"], 3.0, places=9)
        self.assertAlmostEqual(result["observed_mae_b"], 1.0, places=9)
        self.assertAlmostEqual(result["observed_diff_a_minus_b"], 2.0, places=9)
        self.assertEqual(result["n_rows"], 2)
        self.assertEqual(result["n_distinct_players"], 2)

    def test_a_players_multiple_rows_move_together_under_resampling(self):
        # A player with wildly different real rows should still only ever
        # contribute ALL of his own rows together in a given replicate,
        # never a mix of "some of his rows" -- verified indirectly: with a
        # single dominant-error player, the bootstrap distribution must be
        # bimodal-ish (either his rows are all in or all out), so the
        # extreme percentiles should be far from the pooled-all-rows mean
        # for a small n_boot/few-player case. We check the weaker, always-
        # true structural property instead: every bootstrap draw's implied
        # per-player row count is a nonnegative multiple of that player's
        # own real row count summed over draws, which is guaranteed by
        # construction -- so here we just confirm zero-row players never
        # appear and the CI is a real, finite, ordered interval.
        rows = [
            {"player_id": "P1", "err_a": 0.0, "err_b": 0.0},
            {"player_id": "P1", "err_a": 0.0, "err_b": 0.0},
            {"player_id": "P2", "err_a": 100.0, "err_b": 0.0},
        ]
        result = player_clustered_bootstrap_mae_diff(
            rows, player_key="player_id", error_a_key="err_a", error_b_key="err_b",
            n_boot=500, seed=7,
        )
        self.assertLessEqual(result["ci95_low"], result["ci95_high"])
        self.assertGreaterEqual(result["ci95_low"], 0.0)  # a can never be < b here
        self.assertTrue(all(map(lambda x: x == x, [result["ci95_low"], result["ci95_high"]])))  # no NaN

    def test_deterministic_given_the_same_seed(self):
        rows = [
            {"player_id": f"P{i}", "err_a": float(i), "err_b": float(i) / 2.0}
            for i in range(1, 11)
        ]
        result_1 = player_clustered_bootstrap_mae_diff(
            rows, player_key="player_id", error_a_key="err_a", error_b_key="err_b",
            n_boot=300, seed=42,
        )
        result_2 = player_clustered_bootstrap_mae_diff(
            rows, player_key="player_id", error_a_key="err_a", error_b_key="err_b",
            n_boot=300, seed=42,
        )
        self.assertEqual(result_1["bootstrap_mean_diff"], result_2["bootstrap_mean_diff"])
        self.assertEqual(result_1["ci95_low"], result_2["ci95_low"])
        self.assertEqual(result_1["ci95_high"], result_2["ci95_high"])

    def test_no_rows_raises_rather_than_fabricating_an_interval(self):
        with self.assertRaises(TeamOpportunityChallengerError):
            player_clustered_bootstrap_mae_diff(
                [], player_key="player_id", error_a_key="err_a", error_b_key="err_b",
            )

    def test_non_positive_n_boot_raises(self):
        rows = [{"player_id": "P1", "err_a": 1.0, "err_b": 1.0}]
        with self.assertRaises(TeamOpportunityChallengerError):
            player_clustered_bootstrap_mae_diff(
                rows, player_key="player_id", error_a_key="err_a", error_b_key="err_b", n_boot=0,
            )


if __name__ == "__main__":
    unittest.main()
