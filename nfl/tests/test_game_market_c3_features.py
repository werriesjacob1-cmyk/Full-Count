#!/usr/bin/env python3
import copy
import unittest

from nfl.research.game_market_c3_features import (
    GameMarketC3FeatureError,
    NEW_MARGIN_FEATURES,
    build_c3_game_rows,
    filter_injury_rows_keep_latest_status_update,
    filter_injury_rows_to_known_status_vocabulary,
    filter_qb_player_stat_rows_for_missing_team_identity,
    normalize_qb_player_stat_team_identity,
    summarize_c3_exclusions,
)


def _c2_row(game_id, season=2015, week=1, home="DEN", away="KC", eligible=True, actual_margin=3.0):
    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "game_type": "REG",
        "home_team": home,
        "away_team": away,
        "target_final_status": "FINAL",
        "eligibility": "ELIGIBLE" if eligible else "INSUFFICIENT_HISTORY",
        "data_gap_reason": None if eligible else "PBP_TENDENCY_STATS_MISSING_FOR_GAME",
        "margin_features": (
            {
                "scoring_diff_points_for": 1.0, "scoring_diff_points_against": 1.0,
                "matchup_diff_ypp": 1.0, "matchup_diff_play_volume": 1.0,
                "epa_diff_offense": 1.0, "epa_diff_defense_allowed": 1.0,
                "pbp_diff_neutral_dropback_rate": 1.0, "pbp_diff_scrimmage_plays_per_game": 1.0,
            } if eligible else None
        ),
        "total_features": None,
        "actual_margin": actual_margin if eligible else None,
        "actual_total": 44.0 if eligible else None,
    }


def _qb_row(season, week, team, opponent, *, tenure=3, actual_starter="QB_TARGET"):
    return {
        "season": season, "week": week, "game_type": "REG",
        "team": team, "opponent_team": opponent,
        "prior_games_n": 5, "rolling_window": 5,
        "feature_semantics": "STRICTLY_PRIOR_QB_STARTER_CONTINUITY",
        "features": {
            "prior_starter_player_id": None if tenure is None else "QB_INCUMBENT",
            "qb_tenure_starts": tenure,
            "games_since_qb_change": tenure,
            "prior_starters_last_n": ["QB_INCUMBENT"] if tenure is not None else [],
            "current_game_attempts_used": False,
            "starter_source": "NFLVERSE_WEEKLY_PLAYER_STAT_ATTEMPTS_PROXY",
            "depth_chart_or_official_starter_designation_used": False,
        },
        # `target` intentionally carries a DIFFERENT starter than `features`
        # would imply, and a wildly different tenure-like fact, so any test
        # that leaks `target` into the join output is caught deterministically.
        "target": {
            "actual_starter_player_id": actual_starter,
            "actual_starter_attempts": 999,
            "starter_changed_from_prior": True,
        },
    }


def _avail_row(season, week, team, opponent, *, status="NOT_LISTED_GAME_AFFECTING_STATUS", starter_out=False):
    return {
        "season": season, "week": week, "game_type": "REG",
        "team": team, "opponent_team": opponent,
        "incumbent_starter_player_id": "QB_INCUMBENT",
        "source_class": "NFLVERSE_WEEKLY_INJURY_REPORT",
        "starter_definition": "QB_ONLY_USAGE_PROXY_INCUMBENT_ENTERING_GAME",
        "official_inactive_list_used": False,
        "current_game_information_used": False,
        "feature_semantics": "STRICTLY_PRIOR_STARTER_AVAILABILITY_FROM_WEEKLY_INJURY_REPORT",
        "availability_status": status,
        "report_status_raw": None,
        "starter_out_feature": starter_out,
    }


class BuildC3GameRowsEligibleTests(unittest.TestCase):
    def test_eligible_row_carries_forward_all_c2_fields_plus_three_new_ones(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [
            _qb_row(2015, 1, "DEN", "KC", tenure=5),
            _qb_row(2015, 1, "KC", "DEN", tenure=2),
        ]
        avail_rows = [
            _avail_row(2015, 1, "DEN", "KC", starter_out=False),
            _avail_row(2015, 1, "KC", "DEN", starter_out=True),
        ]
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["c3_eligibility"], "ELIGIBLE")
        self.assertIsNone(row["c3_data_gap_reason"])
        # C2's own 8 features are preserved unchanged.
        for key in c2_rows[0]["margin_features"]:
            self.assertEqual(row["c3_margin_features"][key], c2_rows[0]["margin_features"][key])
        # New features present with the documented home-minus-away /
        # away-minus-home sign conventions.
        self.assertEqual(row["c3_margin_features"]["qb_diff_tenure_starts"], 5.0 - 2.0)
        self.assertEqual(row["c3_margin_features"]["qb_diff_games_since_change"], 5.0 - 2.0)
        # away starter_out=True, home starter_out=False -> away disadvantage -> positive (home advantage)
        self.assertEqual(row["c3_margin_features"]["availability_diff_starter_out"], 1.0)
        for name in NEW_MARGIN_FEATURES:
            self.assertIn(name, row["c3_margin_features"])
        # Original C2 fields (e.g. actual_margin) are untouched.
        self.assertEqual(row["actual_margin"], 3.0)

    def test_availability_diff_sign_convention_home_out_is_negative(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=1), _qb_row(2015, 1, "KC", "DEN", tenure=1)]
        avail_rows = [
            _avail_row(2015, 1, "DEN", "KC", starter_out=True),
            _avail_row(2015, 1, "KC", "DEN", starter_out=False),
        ]
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        self.assertEqual(out[0]["c3_margin_features"]["availability_diff_starter_out"], -1.0)


class BuildC3GameRowsExclusionTests(unittest.TestCase):
    def test_base_c2_ineligible_row_passes_through_as_ineligible_base_c2(self):
        c2_rows = [_c2_row("bad", eligible=False)]
        out = build_c3_game_rows(c2_rows, [], [])
        self.assertEqual(out[0]["c3_eligibility"], "INELIGIBLE_BASE_C2")
        self.assertIsNone(out[0]["c3_margin_features"])
        self.assertEqual(out[0]["c3_data_gap_reason"], "PBP_TENDENCY_STATS_MISSING_FOR_GAME")

    def test_missing_qb_continuity_row_excluded_and_reported(self):
        c2_rows = [_c2_row("g1")]
        # Only home team has a QB-continuity row; away team is missing entirely.
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=3)]
        avail_rows = [_avail_row(2015, 1, "DEN", "KC"), _avail_row(2015, 1, "KC", "DEN")]
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        self.assertEqual(out[0]["c3_eligibility"], "INSUFFICIENT_AVAILABILITY_HISTORY")
        self.assertEqual(out[0]["c3_data_gap_reason"], "QB_CONTINUITY_ROW_MISSING_FOR_TEAM_WEEK")
        self.assertIsNone(out[0]["c3_margin_features"])

    def test_unknown_no_prior_starter_identity_excluded_and_reported(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=None), _qb_row(2015, 1, "KC", "DEN", tenure=3)]
        avail_rows = [
            _avail_row(2015, 1, "DEN", "KC", status="UNKNOWN_NO_PRIOR_STARTER_IDENTITY", starter_out=None),
            _avail_row(2015, 1, "KC", "DEN"),
        ]
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        self.assertEqual(out[0]["c3_eligibility"], "INSUFFICIENT_AVAILABILITY_HISTORY")
        self.assertEqual(out[0]["c3_data_gap_reason"], "UNKNOWN_NO_PRIOR_STARTER_IDENTITY")

    def test_season_not_covered_by_source_excluded_and_reported_never_imputed(self):
        c2_rows = [_c2_row("g1", season=2005)]
        qb_rows = [_qb_row(2005, 1, "DEN", "KC", tenure=3), _qb_row(2005, 1, "KC", "DEN", tenure=3)]
        avail_rows = [
            _avail_row(2005, 1, "DEN", "KC", status="SEASON_NOT_COVERED_BY_SOURCE", starter_out=None),
            _avail_row(2005, 1, "KC", "DEN", status="SEASON_NOT_COVERED_BY_SOURCE", starter_out=None),
        ]
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        self.assertEqual(out[0]["c3_eligibility"], "INSUFFICIENT_AVAILABILITY_HISTORY")
        self.assertEqual(out[0]["c3_data_gap_reason"], "SEASON_NOT_COVERED_BY_SOURCE")
        # Never fabricated to False/0 -- no c3_margin_features at all.
        self.assertIsNone(out[0]["c3_margin_features"])

    def test_missing_availability_row_excluded_and_reported(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=3), _qb_row(2015, 1, "KC", "DEN", tenure=3)]
        avail_rows = [_avail_row(2015, 1, "DEN", "KC")]  # away team's availability row missing
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        self.assertEqual(out[0]["c3_eligibility"], "INSUFFICIENT_AVAILABILITY_HISTORY")
        self.assertEqual(out[0]["c3_data_gap_reason"], "AVAILABILITY_ROW_MISSING_FOR_TEAM_WEEK")

    def test_summarize_c3_exclusions_groups_by_reason_and_game_id(self):
        c2_rows = [
            _c2_row("g1", season=2005),
            _c2_row("g2", season=2005, home="SF", away="LAC"),
            _c2_row("bad", eligible=False),
        ]
        qb_rows = [
            _qb_row(2005, 1, "DEN", "KC", tenure=3), _qb_row(2005, 1, "KC", "DEN", tenure=3),
            _qb_row(2005, 1, "SF", "LAC", tenure=3), _qb_row(2005, 1, "LAC", "SF", tenure=3),
        ]
        avail_rows = [
            _avail_row(2005, 1, "DEN", "KC", status="SEASON_NOT_COVERED_BY_SOURCE", starter_out=None),
            _avail_row(2005, 1, "KC", "DEN", status="SEASON_NOT_COVERED_BY_SOURCE", starter_out=None),
            _avail_row(2005, 1, "SF", "LAC", status="SEASON_NOT_COVERED_BY_SOURCE", starter_out=None),
            _avail_row(2005, 1, "LAC", "SF", status="SEASON_NOT_COVERED_BY_SOURCE", starter_out=None),
        ]
        out = build_c3_game_rows(c2_rows, qb_rows, avail_rows)
        summary = summarize_c3_exclusions(out)
        self.assertEqual(summary["SEASON_NOT_COVERED_BY_SOURCE"], ["g1", "g2"])
        self.assertEqual(summary["PBP_TENDENCY_STATS_MISSING_FOR_GAME"], ["bad"])
        self.assertNotIn("g1", summary.get("ELIGIBLE", []))


class BuildC3GameRowsLeakageTests(unittest.TestCase):
    def test_join_never_reads_target_block_of_qb_continuity_rows(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=4), _qb_row(2015, 1, "KC", "DEN", tenure=1)]
        avail_rows = [_avail_row(2015, 1, "DEN", "KC"), _avail_row(2015, 1, "KC", "DEN")]

        baseline = build_c3_game_rows(c2_rows, copy.deepcopy(qb_rows), copy.deepcopy(avail_rows))

        # Mutate every `target` field on every QB-continuity row to garbage
        # current-game facts. If the join used `target` at all, the output
        # would change; it must not.
        mutated_qb_rows = copy.deepcopy(qb_rows)
        for row in mutated_qb_rows:
            row["target"] = {
                "actual_starter_player_id": "SOMEBODY_ELSE_ENTIRELY",
                "actual_starter_attempts": -12345,
                "starter_changed_from_prior": not row["target"]["starter_changed_from_prior"],
            }
        mutated = build_c3_game_rows(c2_rows, mutated_qb_rows, copy.deepcopy(avail_rows))
        self.assertEqual(baseline[0]["c3_margin_features"], mutated[0]["c3_margin_features"])

        # Deleting `target` entirely must also have no effect (structural
        # proof the join never even looks the key up).
        deleted_qb_rows = copy.deepcopy(qb_rows)
        for row in deleted_qb_rows:
            del row["target"]
        deleted = build_c3_game_rows(c2_rows, deleted_qb_rows, copy.deepcopy(avail_rows))
        self.assertEqual(baseline[0]["c3_margin_features"], deleted[0]["c3_margin_features"])

    def test_future_week_qb_continuity_row_never_changes_an_already_built_row(self):
        c2_rows = [_c2_row("g1", season=2015, week=1)]
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=4), _qb_row(2015, 1, "KC", "DEN", tenure=1)]
        avail_rows = [_avail_row(2015, 1, "DEN", "KC"), _avail_row(2015, 1, "KC", "DEN")]
        baseline = build_c3_game_rows(c2_rows, qb_rows, avail_rows)

        future_qb_rows = qb_rows + [_qb_row(2015, 2, "DEN", "KC", tenure=999)]
        future_avail_rows = avail_rows + [_avail_row(2015, 2, "DEN", "KC", starter_out=True)]
        with_future = build_c3_game_rows(c2_rows, future_qb_rows, future_avail_rows)
        self.assertEqual(baseline[0]["c3_margin_features"], with_future[0]["c3_margin_features"])


class BuildC3GameRowsDuplicateTests(unittest.TestCase):
    def test_duplicate_qb_continuity_row_raises(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [
            _qb_row(2015, 1, "DEN", "KC", tenure=3),
            _qb_row(2015, 1, "DEN", "KC", tenure=5),
        ]
        with self.assertRaisesRegex(GameMarketC3FeatureError, "duplicate QB-continuity row"):
            build_c3_game_rows(c2_rows, qb_rows, [])

    def test_duplicate_availability_row_raises(self):
        c2_rows = [_c2_row("g1")]
        qb_rows = [_qb_row(2015, 1, "DEN", "KC", tenure=3), _qb_row(2015, 1, "KC", "DEN", tenure=3)]
        avail_rows = [_avail_row(2015, 1, "DEN", "KC"), _avail_row(2015, 1, "DEN", "KC")]
        with self.assertRaisesRegex(GameMarketC3FeatureError, "duplicate availability row"):
            build_c3_game_rows(c2_rows, qb_rows, avail_rows)


class BuildC3GameRowsReproducibilityTests(unittest.TestCase):
    def test_identical_inputs_produce_byte_identical_output_regardless_of_input_order(self):
        c2_rows = [
            _c2_row("g1", season=2015, week=1, home="DEN", away="KC"),
            _c2_row("g2", season=2015, week=1, home="SF", away="LAC"),
        ]
        qb_rows = [
            _qb_row(2015, 1, "DEN", "KC", tenure=5), _qb_row(2015, 1, "KC", "DEN", tenure=2),
            _qb_row(2015, 1, "SF", "LAC", tenure=3), _qb_row(2015, 1, "LAC", "SF", tenure=1),
        ]
        avail_rows = [
            _avail_row(2015, 1, "DEN", "KC"), _avail_row(2015, 1, "KC", "DEN", starter_out=True),
            _avail_row(2015, 1, "SF", "LAC"), _avail_row(2015, 1, "LAC", "SF"),
        ]
        first = build_c3_game_rows(copy.deepcopy(c2_rows), copy.deepcopy(qb_rows), copy.deepcopy(avail_rows))
        # Shuffle input order -- the join must not depend on input row order.
        second = build_c3_game_rows(
            list(reversed(copy.deepcopy(c2_rows))),
            list(reversed(copy.deepcopy(qb_rows))),
            list(reversed(copy.deepcopy(avail_rows))),
        )
        self.assertEqual(first, second)


class NormalizeQbPlayerStatTeamIdentityTests(unittest.TestCase):
    def _schedule(self, game_id, home, away):
        return {"game_id": game_id, "home_team": home, "away_team": away}

    def test_relocated_franchise_current_code_remapped_to_historical(self):
        schedule = [self._schedule("2009_01_SD_OAK", home="OAK", away="SD")]
        rows = [
            {"player_id": "p1", "team": "LAC", "opponent_team": "LV", "game_id": "2009_01_SD_OAK"},
            {"player_id": "p2", "team": "LV", "opponent_team": "LAC", "game_id": "2009_01_SD_OAK"},
        ]
        normalized, unresolved = normalize_qb_player_stat_team_identity(rows, schedule)
        self.assertEqual(unresolved, [])
        by_player = {r["player_id"]: r for r in normalized}
        self.assertEqual(by_player["p1"]["team"], "SD")
        self.assertEqual(by_player["p1"]["opponent_team"], "OAK")
        self.assertEqual(by_player["p2"]["team"], "OAK")
        self.assertEqual(by_player["p2"]["opponent_team"], "SD")

    def test_already_historical_code_left_unchanged(self):
        schedule = [self._schedule("2021_01_DEN_KC", home="KC", away="DEN")]
        rows = [{"player_id": "p1", "team": "KC", "opponent_team": "DEN", "game_id": "2021_01_DEN_KC"}]
        normalized, unresolved = normalize_qb_player_stat_team_identity(rows, schedule)
        self.assertEqual(unresolved, [])
        self.assertEqual(normalized[0]["team"], "KC")

    def test_unknown_game_id_left_unchanged_and_not_flagged_unresolved(self):
        rows = [{"player_id": "p1", "team": "LAC", "opponent_team": "LV", "game_id": "NOT_IN_SCHEDULE"}]
        normalized, unresolved = normalize_qb_player_stat_team_identity(rows, [])
        self.assertEqual(normalized[0]["team"], "LAC")
        self.assertEqual(unresolved, [])

    def test_blank_team_is_unresolved_not_guessed(self):
        schedule = [self._schedule("1999_09_PHI_CAR", home="PHI", away="CAR")]
        rows = [{"player_id": "p1", "team": "", "opponent_team": "", "game_id": "1999_09_PHI_CAR"}]
        normalized, unresolved = normalize_qb_player_stat_team_identity(rows, schedule)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(normalized[0]["team"], "")  # left unchanged, never guessed


class FilterQbPlayerStatMissingTeamIdentityTests(unittest.TestCase):
    def test_blank_team_or_opponent_excluded(self):
        rows = [
            {"player_id": "good", "team": "KC", "opponent_team": "DEN"},
            {"player_id": "bad_team", "team": "", "opponent_team": "DEN"},
            {"player_id": "bad_opp", "team": "KC", "opponent_team": ""},
        ]
        kept, excluded = filter_qb_player_stat_rows_for_missing_team_identity(rows)
        self.assertEqual([r["player_id"] for r in kept], ["good"])
        self.assertEqual({r["player_id"] for r in excluded}, {"bad_team", "bad_opp"})


class FilterInjuryRowsToKnownVocabularyTests(unittest.TestCase):
    def test_probable_and_unknown_statuses_excluded_known_kept(self):
        rows = [
            {"gsis_id": "a", "report_status": "Out"},
            {"gsis_id": "b", "report_status": "Doubtful"},
            {"gsis_id": "c", "report_status": "Questionable"},
            {"gsis_id": "d", "report_status": ""},
            {"gsis_id": "e", "report_status": "Probable"},
            {"gsis_id": "f", "report_status": "Note"},
        ]
        kept, excluded = filter_injury_rows_to_known_status_vocabulary(rows)
        self.assertEqual({r["gsis_id"] for r in kept}, {"a", "b", "c", "d"})
        self.assertEqual({r["gsis_id"] for r in excluded}, {"e", "f"})


class FilterInjuryRowsKeepLatestStatusUpdateTests(unittest.TestCase):
    def test_duplicate_key_keeps_latest_date_modified(self):
        rows = [
            {
                "season": "2024", "week": "15", "team": "HOU", "gsis_id": "00-0039359",
                "report_status": "Questionable", "date_modified": "2024-12-15T03:34:33Z",
            },
            {
                "season": "2024", "week": "15", "team": "HOU", "gsis_id": "00-0039359",
                "report_status": "Out", "date_modified": "2024-12-15T14:17:06Z",
            },
        ]
        kept, dropped = filter_injury_rows_keep_latest_status_update(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["report_status"], "Out")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["report_status"], "Questionable")

    def test_non_duplicate_rows_pass_through_unchanged(self):
        rows = [
            {"season": "2024", "week": "1", "team": "KC", "gsis_id": "a", "report_status": "Out", "date_modified": "x"},
            {"season": "2024", "week": "1", "team": "DEN", "gsis_id": "b", "report_status": "Out", "date_modified": "y"},
        ]
        kept, dropped = filter_injury_rows_keep_latest_status_update(rows)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
