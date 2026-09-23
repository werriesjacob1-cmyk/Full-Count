#!/usr/bin/env python3
"""Unit and adversarial tests for `receptions_team_opportunity_challenger`.

Every real upstream builder this module composes (`game_matchup_features`,
`coach_regime_registry`, `role_intelligence_features.
build_player_dimension_history`) is exercised through this module's own
functions with realistic fixtures, not mocked away -- the only thing mocked
anywhere in this file is `score_shadow_candidate`'s own residual-scoring
math, which is already independently tested elsewhere and is not this
module's own logic to re-verify.
"""
from __future__ import annotations

import unittest
from datetime import date

from nfl.research import receptions_team_opportunity_challenger as opportunity_mod
from nfl.research.coach_regime_registry import RegimeInterval
from nfl.research.receptions_team_opportunity_challenger import (
    TeamOpportunityChallengerError,
    apply_snap_informed_target_share,
    build_opportunity_challenger_record,
    compute_opportunity_projection,
    estimate_current_week_catch_rate,
    estimate_current_week_snap_share,
    estimate_current_week_target_share,
    filter_team_rows_by_current_regime,
    opportunity_side_probabilities_for_lines,
    predict_team_pass_dropbacks,
    predict_team_pass_dropbacks_coaching_aware,
)

REAL_SHAPE_RESIDUALS = [0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5


def _matchup_row(
    *,
    home_off_dropbacks=34.0, home_off_n=5,
    away_def_allowed=30.0, away_def_n=5,
    away_off_dropbacks=32.0, away_off_n=5,
    home_def_allowed=28.0, home_def_n=5,
):
    return {
        "home_offense_prior_mean_dropback_proxy": home_off_dropbacks,
        "home_offense_prior_games_n": home_off_n,
        "away_defense_prior_mean_opp_dropback_proxy_allowed": away_def_allowed,
        "away_defense_prior_games_n": away_def_n,
        "away_offense_prior_mean_dropback_proxy": away_off_dropbacks,
        "away_offense_prior_games_n": away_off_n,
        "home_defense_prior_mean_opp_dropback_proxy_allowed": home_def_allowed,
        "home_defense_prior_games_n": home_def_n,
    }


class PredictTeamPassDropbacksTests(unittest.TestCase):
    def test_blends_offense_and_defense_when_both_have_real_history(self):
        result = predict_team_pass_dropbacks(_matchup_row(), side="home")
        self.assertEqual(result["basis"], "BLENDED_OFFENSE_AND_DEFENSE")
        self.assertAlmostEqual(result["predicted_dropbacks"], (34.0 + 30.0) / 2.0)

    def test_away_side_reads_the_correct_fields(self):
        result = predict_team_pass_dropbacks(_matchup_row(), side="away")
        self.assertAlmostEqual(result["predicted_dropbacks"], (32.0 + 28.0) / 2.0)

    def test_degrades_to_offense_only_when_opponent_has_no_real_history(self):
        row = _matchup_row(away_def_allowed=None, away_def_n=0)
        result = predict_team_pass_dropbacks(row, side="home")
        self.assertEqual(result["basis"], "OFFENSE_ONLY_NO_REAL_OPPONENT_PRIOR")
        self.assertEqual(result["predicted_dropbacks"], 34.0)

    def test_degrades_to_defense_only_when_own_side_has_no_real_history(self):
        row = _matchup_row(home_off_dropbacks=None, home_off_n=0)
        result = predict_team_pass_dropbacks(row, side="home")
        self.assertEqual(result["basis"], "DEFENSE_ONLY_NO_REAL_OWN_PRIOR")
        self.assertEqual(result["predicted_dropbacks"], 30.0)

    def test_no_real_prior_history_anywhere_returns_none_not_fabricated(self):
        row = _matchup_row(
            home_off_dropbacks=None, home_off_n=0, away_def_allowed=None, away_def_n=0,
        )
        result = predict_team_pass_dropbacks(row, side="home")
        self.assertEqual(result["basis"], "NO_REAL_PRIOR_HISTORY")
        self.assertIsNone(result["predicted_dropbacks"])

    def test_invalid_side_raises(self):
        with self.assertRaises(TeamOpportunityChallengerError):
            predict_team_pass_dropbacks(_matchup_row(), side="middle")


class FilterTeamRowsByCurrentRegimeTests(unittest.TestCase):
    def _rows(self):
        return [
            {"team": "KC", "season": 2025, "week": w, "attempts": 30.0}
            for w in range(1, 6)
        ] + [
            {"team": "SEA", "season": 2025, "week": w, "attempts": 25.0}
            for w in range(1, 6)
        ]

    def _game_date_index(self):
        idx = {}
        for w in range(1, 6):
            idx[("KC", 2025, w)] = date(2025, 9, 1) + (w - 1) * __import__("datetime").timedelta(days=7)
            idx[("SEA", 2025, w)] = date(2025, 9, 1) + (w - 1) * __import__("datetime").timedelta(days=7)
        idx[("KC", 2025, 6)] = date(2025, 9, 1) + 5 * __import__("datetime").timedelta(days=7)
        return idx

    def test_real_mid_window_regime_change_excludes_pre_change_games(self):
        # KC's HC changed starting week 4 (a real, disclosed test fixture,
        # not a claim about any actual team) -- weeks 1-3 must be excluded
        # from the regime-aware window, weeks 4-5 kept.
        game_date_index = self._game_date_index()
        interval = RegimeInterval(
            team="KC", role="HC", persons=("New Coach",),
            start_date=game_date_index[("KC", 2025, 4)],
            end_date=game_date_index[("KC", 2025, 6)],
            source="test fixture", confidence="CONFIRMED",
        )
        kept, note = filter_team_rows_by_current_regime(
            self._rows(), team="KC", target_season=2025, target_week=6,
            hc_intervals=[interval], game_date_index=game_date_index,
        )
        self.assertEqual(sorted(r["week"] for r in kept), [4, 5])
        self.assertTrue(note["regime_filter_applied"])
        self.assertEqual(note["rows_excluded_by_regime_filter"], 3)
        self.assertEqual(note["regime_lookup_status"], "RESOLVED")

    def test_no_coverage_falls_back_to_unfiltered_control_not_a_guess(self):
        # target_week=6 is strictly after all 5 real fixture weeks, so an
        # UNKNOWN regime lookup should still return every real prior row.
        kept, note = filter_team_rows_by_current_regime(
            self._rows(), team="KC", target_season=2025, target_week=6,
            hc_intervals=[], game_date_index={},
        )
        self.assertEqual(len(kept), 5)  # all KC rows, unfiltered
        self.assertFalse(note["regime_filter_applied"])
        self.assertEqual(note["regime_lookup_status"], "UNKNOWN")

    def test_only_returns_rows_for_the_requested_team(self):
        kept, _ = filter_team_rows_by_current_regime(
            self._rows(), team="KC", target_season=2025, target_week=6,
            hc_intervals=[], game_date_index={},
        )
        self.assertTrue(all(r["team"] == "KC" for r in kept))

    def test_never_leaks_a_game_at_or_after_the_target_week(self):
        # Real leakage-safety regression guard: even with NO regime filter
        # applied (UNKNOWN lookup), a game at or after the target week must
        # never appear in the returned set. This was a real bug -- the
        # original implementation only checked `team`, not the target week,
        # so feeding it a full multi-season row set could silently leak
        # future games into the rolling window.
        kept, _ = filter_team_rows_by_current_regime(
            self._rows(), team="KC", target_season=2025, target_week=4,
            hc_intervals=[], game_date_index={},
        )
        self.assertEqual(sorted(r["week"] for r in kept), [1, 2, 3])


class TargetShareEstimateTests(unittest.TestCase):
    def test_shrinks_current_season_toward_prior_season_with_thin_current_sample(self):
        # 2 current-season games at 0.30 share, prior season mean 0.10 --
        # a thin current sample should NOT fully override the prior.
        history = [(2025, w, 0.10) for w in range(1, 18)] + [(2026, 1, 0.30), (2026, 2, 0.30)]
        result = estimate_current_week_target_share(
            player_id="00-TEST", target_share_history=history,
            target_season=2026, target_week=3, shrinkage_k=3.0,
        )
        self.assertEqual(result["basis"], "SHRUNK_CURRENT_TOWARD_PRIOR_SEASON")
        # weight_current = 2 / (2+3) = 0.4 -> 0.4*0.30 + 0.6*0.10 = 0.18
        self.assertAlmostEqual(result["estimate"], 0.18, places=6)
        self.assertEqual(result["n_current_season_games"], 2)

    def test_large_current_sample_dominates_the_blend(self):
        history = [(2025, w, 0.10) for w in range(1, 18)] + [(2026, w, 0.30) for w in range(1, 9)]
        result = estimate_current_week_target_share(
            player_id="00-TEST", target_share_history=history,
            target_season=2026, target_week=9, shrinkage_k=3.0,
        )
        # weight_current = 8/(8+3) ~= 0.727 -> mostly current season
        self.assertGreater(result["estimate"], 0.24)

    def test_no_history_at_any_level_returns_none_not_fabricated(self):
        result = estimate_current_week_target_share(
            player_id="00-TEST", target_share_history=[], target_season=2026, target_week=1,
        )
        self.assertIsNone(result["estimate"])
        self.assertEqual(result["basis"], "NO_REAL_HISTORY_AT_ANY_LEVEL")

    def test_never_leaks_the_target_week_or_future_games(self):
        # A share recorded AT OR AFTER the target week must never influence
        # the estimate -- this is the no-lookahead invariant.
        history = [(2026, 5, 0.90)]  # the target game's own (hypothetical) share
        result = estimate_current_week_target_share(
            player_id="00-TEST", target_share_history=history, target_season=2026, target_week=5,
        )
        self.assertIsNone(result["estimate"])

    def test_impossible_share_above_one_raises_rather_than_clips(self):
        history = [(2025, w, 1.5) for w in range(1, 18)]
        with self.assertRaises(TeamOpportunityChallengerError):
            estimate_current_week_target_share(
                player_id="00-TEST", target_share_history=history, target_season=2026, target_week=1,
            )


class CatchRateEstimateTests(unittest.TestCase):
    def _log(self):
        return (
            [{"season": 2025, "week": w, "targets": 5.0, "receptions": 3.0} for w in range(1, 18)]
            + [{"season": 2026, "week": 1, "targets": 4.0, "receptions": 4.0}]
        )

    def test_shrinks_toward_prior_season_rate(self):
        result = estimate_current_week_catch_rate(
            player_id="00-TEST", game_log=self._log(), target_season=2026, target_week=2, shrinkage_k=5.0,
        )
        # current: 1 game at 1.0; prior season rate: 0.6
        # weight_current = 1/(1+5) = 1/6
        expected = (1 / 6) * 1.0 + (5 / 6) * 0.6
        self.assertAlmostEqual(result["estimate"], expected, places=6)

    def test_zero_target_games_are_excluded_from_the_rate_not_treated_as_zero_catch_rate(self):
        log = [{"season": 2025, "week": 1, "targets": 0.0, "receptions": 0.0},
               {"season": 2025, "week": 2, "targets": 4.0, "receptions": 2.0}]
        result = estimate_current_week_catch_rate(
            player_id="00-TEST", game_log=log, target_season=2026, target_week=1,
        )
        self.assertAlmostEqual(result["estimate"], 0.5, places=6)

    def test_no_history_returns_none(self):
        result = estimate_current_week_catch_rate(
            player_id="00-TEST", game_log=[], target_season=2026, target_week=1,
        )
        self.assertIsNone(result["estimate"])


class SnapShareEstimateTests(unittest.TestCase):
    def test_shrinks_current_season_toward_prior_season_snap_share(self):
        # Same shrinkage discipline as target share, over real per-game
        # offense-snap-share history instead of targets.
        history = [(2025, w, 0.40) for w in range(1, 18)] + [(2026, 1, 0.75), (2026, 2, 0.75)]
        result = estimate_current_week_snap_share(
            player_id="00-TEST", snap_share_history=history, target_season=2026, target_week=3, shrinkage_k=3.0,
        )
        # weight_current = 2/(2+3) = 0.4 -> 0.4*0.75 + 0.6*0.40 = 0.54
        self.assertAlmostEqual(result["estimate"], 0.54, places=6)
        self.assertEqual(result["n_current_season_games"], 2)
        self.assertAlmostEqual(result["current_season_mean"], 0.75, places=6)
        self.assertAlmostEqual(result["prior_season_value"], 0.40, places=6)

    def test_no_history_returns_none(self):
        result = estimate_current_week_snap_share(
            player_id="00-TEST", snap_share_history=[], target_season=2026, target_week=1,
        )
        self.assertIsNone(result["estimate"])

    def test_impossible_snap_share_above_one_raises(self):
        history = [(2025, w, 1.3) for w in range(1, 18)]
        with self.assertRaises(TeamOpportunityChallengerError):
            estimate_current_week_snap_share(
                player_id="00-TEST", snap_share_history=history, target_season=2026, target_week=1,
            )


class ApplySnapInformedTargetShareTests(unittest.TestCase):
    def test_real_role_change_boosts_target_share(self):
        # Real scenario: a player's snap share nearly doubled this season
        # (0.40 -> 0.75) versus his own prior-season baseline, but his
        # target-share sample is still thin (1 game) so
        # estimate_current_week_target_share's own shrinkage hasn't fully
        # caught up yet.
        target_share_info = {"estimate": 0.15, "n_current_season_games": 1, "basis": "SHRUNK_CURRENT_TOWARD_PRIOR_SEASON"}
        snap_share_info = {
            "n_current_season_games": 2, "current_season_mean": 0.75, "prior_season_value": 0.40,
        }
        result = apply_snap_informed_target_share(target_share_info=target_share_info, snap_share_info=snap_share_info)
        self.assertTrue(result["snap_role_change_applied"])
        # ratio = 0.75/0.40 = 1.875, clamped within [0.4, 2.5] -> unchanged
        self.assertAlmostEqual(result["snap_role_change_ratio"], 1.875, places=6)
        self.assertAlmostEqual(result["estimate"], 0.15 * 1.875, places=6)
        self.assertAlmostEqual(result["pre_snap_adjustment_target_share"], 0.15, places=6)

    def test_extreme_ratio_is_clamped_not_fabricated(self):
        target_share_info = {"estimate": 0.10, "n_current_season_games": 1}
        snap_share_info = {
            "n_current_season_games": 1, "current_season_mean": 0.90, "prior_season_value": 0.05,
        }
        result = apply_snap_informed_target_share(target_share_info=target_share_info, snap_share_info=snap_share_info)
        # raw ratio = 18.0, clamped to the pre-declared bound of 2.5
        self.assertAlmostEqual(result["snap_role_change_ratio"], 2.5, places=6)
        self.assertAlmostEqual(result["snap_role_change_ratio_unclamped"], 18.0, places=6)
        self.assertAlmostEqual(result["estimate"], 0.10 * 2.5, places=6)

    def test_no_current_season_snap_games_falls_back_to_control(self):
        target_share_info = {"estimate": 0.15, "n_current_season_games": 0}
        snap_share_info = {"n_current_season_games": 0, "current_season_mean": None, "prior_season_value": 0.40}
        result = apply_snap_informed_target_share(target_share_info=target_share_info, snap_share_info=snap_share_info)
        self.assertFalse(result["snap_role_change_applied"])
        self.assertEqual(result["estimate"], 0.15)

    def test_no_real_prior_season_baseline_falls_back_to_control(self):
        target_share_info = {"estimate": 0.15, "n_current_season_games": 1}
        snap_share_info = {"n_current_season_games": 1, "current_season_mean": 0.5, "prior_season_value": None}
        result = apply_snap_informed_target_share(target_share_info=target_share_info, snap_share_info=snap_share_info)
        self.assertFalse(result["snap_role_change_applied"])
        self.assertEqual(result["estimate"], 0.15)

    def test_missing_target_share_estimate_is_not_fabricated(self):
        target_share_info = {"estimate": None, "n_current_season_games": 0}
        snap_share_info = {"n_current_season_games": 2, "current_season_mean": 0.75, "prior_season_value": 0.40}
        result = apply_snap_informed_target_share(target_share_info=target_share_info, snap_share_info=snap_share_info)
        self.assertIsNone(result["estimate"])
        self.assertFalse(result["snap_role_change_applied"])


class ComputeOpportunityProjectionTests(unittest.TestCase):
    def test_happy_path_multiplies_all_three_real_inputs(self):
        result = compute_opportunity_projection(
            predicted_team_dropbacks=34.0, target_share=0.20, catch_rate=0.65,
        )
        self.assertAlmostEqual(result["projection"], 34.0 * 0.20 * 0.65)
        self.assertAlmostEqual(result["expected_targets"], 34.0 * 0.20)
        self.assertIsNone(result["reason"])

    def test_missing_team_dropbacks_abstains(self):
        result = compute_opportunity_projection(
            predicted_team_dropbacks=None, target_share=0.2, catch_rate=0.6,
        )
        self.assertIsNone(result["projection"])
        self.assertEqual(result["reason"], "MISSING_REQUIRED_INPUT")

    def test_non_positive_team_dropbacks_abstains(self):
        result = compute_opportunity_projection(
            predicted_team_dropbacks=0.0, target_share=0.2, catch_rate=0.6,
        )
        self.assertIsNone(result["projection"])
        self.assertEqual(result["reason"], "NON_POSITIVE_TEAM_DROPBACKS")

    def test_target_share_above_one_is_an_impossible_allocation(self):
        result = compute_opportunity_projection(
            predicted_team_dropbacks=34.0, target_share=1.4, catch_rate=0.6,
        )
        self.assertIsNone(result["projection"])
        self.assertEqual(result["reason"], "TARGET_SHARE_OUT_OF_RANGE")

    def test_catch_rate_above_one_is_impossible(self):
        result = compute_opportunity_projection(
            predicted_team_dropbacks=34.0, target_share=0.2, catch_rate=1.1,
        )
        self.assertIsNone(result["projection"])
        self.assertEqual(result["reason"], "CATCH_RATE_OUT_OF_RANGE")


class OpportunitySideProbabilitiesForLinesTests(unittest.TestCase):
    def test_probabilities_are_coherent_across_increasingly_demanding_thresholds(self):
        result = opportunity_side_probabilities_for_lines(
            projection=6.0, lines=[3.5, 5.5],
            over_odds_by_line={3.5: -140, 5.5: -110},
            under_odds_by_line={3.5: +115, 5.5: -120},
            residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertGreater(
            result[3.5]["model_over_probability"], result[5.5]["model_over_probability"],
        )

    def test_missing_real_odds_for_a_line_raises(self):
        with self.assertRaises(TeamOpportunityChallengerError):
            opportunity_side_probabilities_for_lines(
                projection=6.0, lines=[3.5], over_odds_by_line={}, under_odds_by_line={3.5: -110},
                residuals=REAL_SHAPE_RESIDUALS,
            )

    def test_no_lines_raises(self):
        with self.assertRaises(TeamOpportunityChallengerError):
            opportunity_side_probabilities_for_lines(
                projection=6.0, lines=[], over_odds_by_line={}, under_odds_by_line={},
                residuals=REAL_SHAPE_RESIDUALS,
            )


class PredictTeamPassDropbacksCoachingAwareTests(unittest.TestCase):
    def test_unknown_regime_makes_coaching_aware_and_naive_control_identical(self):
        rows = [
            {"team": "KC", "season": 2025, "week": w, "attempts": 30.0, "sacks_suffered": 2.0}
            for w in range(1, 6)
        ]
        result = predict_team_pass_dropbacks_coaching_aware(
            rows, team="KC", target_season=2025, target_week=6,
            hc_intervals=[], game_date_index={},
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertFalse(result["coaching_feature_changed_the_projection"])
        self.assertEqual(
            result["predicted_dropbacks_coaching_aware"], result["predicted_dropbacks_naive_control"],
        )
        self.assertEqual(result["regime_note"]["regime_lookup_status"], "UNKNOWN")

    def test_resolved_regime_change_makes_them_differ_and_reports_game_counts(self):
        rows = (
            [{"team": "KC", "season": 2025, "week": w, "attempts": 30.0, "sacks_suffered": 2.0} for w in range(1, 4)]
            + [{"team": "KC", "season": 2025, "week": w, "attempts": 48.0, "sacks_suffered": 2.0} for w in (4, 5)]
        )
        game_date_index = {("KC", 2025, w): date(2025, 9, 1) + __import__("datetime").timedelta(days=7 * (w - 1)) for w in range(1, 7)}
        interval = RegimeInterval(
            team="KC", role="HC", persons=("New Coach",),
            start_date=game_date_index[("KC", 2025, 4)], end_date=game_date_index[("KC", 2025, 6)],
            source="test fixture", confidence="CONFIRMED",
        )
        result = predict_team_pass_dropbacks_coaching_aware(
            rows, team="KC", target_season=2025, target_week=6,
            hc_intervals=[interval], game_date_index=game_date_index,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertTrue(result["coaching_feature_changed_the_projection"])
        self.assertEqual(result["own_games_used_coaching_aware"], 2)  # weeks 4-5 only
        self.assertEqual(result["own_games_used_naive_control"], 5)  # weeks 1-5
        self.assertGreater(
            result["predicted_dropbacks_coaching_aware"], result["predicted_dropbacks_naive_control"],
        )


class BuildOpportunityChallengerRecordTests(unittest.TestCase):
    def _real_shaped_inputs(self):
        matchup_row = _matchup_row()
        team_box_score_rows = [
            {"team": "KC", "season": 2025, "week": w, "attempts": 30.0, "sacks_suffered": 2.0}
            for w in range(1, 18)
        ]
        target_share_history = [(2025, w, 0.10) for w in range(1, 18)] + [(2026, 1, 0.30), (2026, 2, 0.30)]
        catch_rate_log = (
            [{"season": 2025, "week": w, "targets": 5.0, "receptions": 3.0} for w in range(1, 18)]
            + [{"season": 2026, "week": 1, "targets": 6.0, "receptions": 4.0}]
        )
        return matchup_row, team_box_score_rows, target_share_history, catch_rate_log

    def test_real_end_to_end_record_built_for_a_qualifying_candidate(self):
        matchup_row, team_box_score_rows, target_share_history, catch_rate_log = self._real_shaped_inputs()
        record = build_opportunity_challenger_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            matchup_row=matchup_row, side="home",
            team_box_score_rows=team_box_score_rows, hc_intervals=[], game_date_index={},
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2026, target_week=3,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
            b0_projection=4.0,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["candidate_player_id"], "00-TEST")
        self.assertGreater(record["opportunity_projection"], 0)
        self.assertEqual(record["prediction_source"], "B0_VS_TEAM_OPPORTUNITY_ENGINE_V1")
        self.assertEqual(record["status"], "RESEARCH_ONLY_NOT_PROMOTED")
        self.assertIn("model_over_probability", record)
        # UNKNOWN regime lookup (no hc_intervals supplied) -> the explicit
        # fallback: coaching-aware and naive-control use the same rows, so
        # they must be numerically identical, not just "close".
        self.assertFalse(record["coaching_feature_changed_the_projection"])
        self.assertEqual(record["opportunity_projection"], record["naive_control_projection"])

    def test_coaching_regime_change_actually_changes_the_projection(self):
        import datetime as _dt
        matchup_row, team_box_score_rows, target_share_history, catch_rate_log = self._real_shaped_inputs()

        game_date_index = {
            ("KC", 2025, w): date(2025, 9, 1) + _dt.timedelta(days=7 * (w - 1)) for w in range(1, 18)
        }
        target_date = date(2025, 9, 1) + _dt.timedelta(days=7 * 17)  # a real week strictly after week 17
        game_date_index[("KC", 2025, 18)] = target_date

        # Real regime change: KC's HC changed starting week 15 to a
        # dramatically higher-volume staff (50 dropbacks/game vs. the
        # weeks 1-14 32/game baseline). The coaching-aware rolling-5 window
        # (weeks 13-17, but only 15-17 survive the regime filter) should
        # differ from the naive control (rolling-5 over weeks 13-17
        # unfiltered).
        boosted_rows = [
            r for r in team_box_score_rows if r["week"] not in (15, 16, 17)
        ] + [
            {"team": "KC", "season": 2025, "week": w, "attempts": 48.0, "sacks_suffered": 2.0}
            for w in (15, 16, 17)
        ]
        interval = RegimeInterval(
            team="KC", role="HC", persons=("New OC",),
            start_date=game_date_index[("KC", 2025, 15)],
            end_date=target_date,
            source="test fixture", confidence="CONFIRMED",
        )

        record = build_opportunity_challenger_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            matchup_row=matchup_row, side="home",
            team_box_score_rows=boosted_rows, hc_intervals=[interval], game_date_index=game_date_index,
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2025, target_week=18,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNotNone(record)
        self.assertTrue(record["coaching_feature_changed_the_projection"])
        self.assertNotEqual(record["opportunity_projection"], record["naive_control_projection"])
        # The regime-pure window (weeks 15-17, all 50 dropbacks) predicts
        # MORE volume than the naive control (rolling-5 over weeks 13-17: a
        # 2/5 mix of 32 and 3/5 mix of 50).
        self.assertGreater(record["opportunity_projection"], record["naive_control_projection"])
        self.assertIsNotNone(record["naive_control_probabilities"])
        self.assertEqual(record["team_dropbacks"]["own_games_used_coaching_aware"], 3)
        self.assertEqual(record["team_dropbacks"]["own_games_used_naive_control"], 5)

    def test_no_record_when_team_has_no_real_prior_dropback_history(self):
        matchup_row = _matchup_row(
            home_off_dropbacks=None, home_off_n=0, away_def_allowed=None, away_def_n=0,
        )
        _, _, target_share_history, catch_rate_log = self._real_shaped_inputs()
        record = build_opportunity_challenger_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            matchup_row=matchup_row, side="home",
            team_box_score_rows=[], hc_intervals=[], game_date_index={},
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2026, target_week=3,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNone(record)

    def test_no_record_when_player_has_no_real_target_share_history(self):
        matchup_row, team_box_score_rows, _, catch_rate_log = self._real_shaped_inputs()
        record = build_opportunity_challenger_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            matchup_row=matchup_row, side="home",
            team_box_score_rows=team_box_score_rows, hc_intervals=[], game_date_index={},
            target_share_history=[], catch_rate_game_log=catch_rate_log,
            target_season=2026, target_week=3,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNone(record)

    def test_real_snap_share_role_change_changes_the_final_projection(self):
        # Mission 6 Section 4's required demonstration: a real current-
        # season role-change signal (snap share nearly doubling) actually
        # changes a real research prediction and its probability, with an
        # explicit otherwise-identical control (snap_unadjusted_projection).
        matchup_row, team_box_score_rows, target_share_history, catch_rate_log = self._real_shaped_inputs()
        snap_share_history = [(2025, w, 0.40) for w in range(1, 18)] + [(2026, 1, 0.78), (2026, 2, 0.78)]

        record = build_opportunity_challenger_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            matchup_row=matchup_row, side="home",
            team_box_score_rows=team_box_score_rows, hc_intervals=[], game_date_index={},
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2026, target_week=3,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
            snap_share_history=snap_share_history,
        )
        self.assertIsNotNone(record)
        self.assertTrue(record["snap_feature_changed_the_projection"])
        self.assertTrue(record["target_share"]["snap_role_change_applied"])
        # A real role-change boost (snap share 0.40 -> 0.78) increases the
        # projection and its over-probability relative to the explicit
        # unadjusted control, holding team volume and catch rate fixed.
        self.assertGreater(record["opportunity_projection"], record["snap_unadjusted_projection"])
        self.assertGreater(record["model_over_probability"], record["snap_unadjusted_probabilities"]["model_over_probability"])

    def test_omitting_snap_share_history_reproduces_prior_behavior_exactly(self):
        # Backward compatibility: no snap_share_history -> the new feature
        # is a true no-op, not a silent behavior change for every existing
        # caller (PR #179/#181's own evaluation script, unmodified here).
        matchup_row, team_box_score_rows, target_share_history, catch_rate_log = self._real_shaped_inputs()
        record = build_opportunity_challenger_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            matchup_row=matchup_row, side="home",
            team_box_score_rows=team_box_score_rows, hc_intervals=[], game_date_index={},
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2026, target_week=3,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNotNone(record)
        self.assertFalse(record["snap_feature_changed_the_projection"])
        self.assertEqual(record["opportunity_projection"], record["snap_unadjusted_projection"])
        self.assertIsNone(record["snap_share"])


class DisclosedNegativeFindingTests(unittest.TestCase):
    def test_the_real_negative_evaluation_finding_survives_verbatim(self):
        # Fails if a future edit quietly removes or softens the disclosed
        # real negative finding from the module's own docstring.
        self.assertIn("does NOT beat B0", opportunity_mod.__doc__)

    def test_the_snap_share_negative_finding_survives_verbatim(self):
        # Fails if a future edit quietly removes or softens the second
        # disclosed real negative finding (snap-share adjustment made
        # MAE worse, not better, on the real matched population).
        self.assertIn("made MAE modestly WORSE", opportunity_mod.__doc__)


if __name__ == "__main__":
    unittest.main()
