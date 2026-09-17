#!/usr/bin/env python3
"""Contracts for nflverse box-score -> player-prop grader outcome mapping.

Fixture rows are real values captured live from nflverse's
stats_player_week_2025.csv for game_id 2025_01_PIT_NYJ (verified
2026-09-17), not invented, matching this repo's discipline of testing
against real recorded shapes.
"""
import unittest

from nfl.prospective.player_prop_grader import grade_player_prop_market
from nfl.research import box_score_outcomes as outcomes


def _row(**overrides):
    row = {
        "player_id": "", "player_display_name": "", "position": "",
        "season": "2025", "week": "1", "season_type": "REG",
        "team": "", "opponent_team": "", "game_id": "2025_01_PIT_NYJ",
        "passing_yards": "0", "passing_tds": "0",
        "rushing_yards": "0", "rushing_tds": "0",
        "receiving_yards": "0", "receiving_tds": "0", "receptions": "0",
        "def_sacks": "0", "def_tds": "0", "special_teams_tds": "0",
        "fumble_recovery_tds": "0",
    }
    row.update(overrides)
    return row


RODGERS = _row(
    player_id="00-0023459", player_display_name="Aaron Rodgers",
    position="QB", team="PIT", opponent_team="NYJ",
    passing_yards="244", passing_tds="4", rushing_yards="-1",
)
WILSON = _row(
    player_id="00-0037740", player_display_name="Garrett Wilson",
    position="WR", team="NYJ", opponent_team="PIT",
    receptions="7", receiving_yards="95", receiving_tds="1",
)
Q_WILLIAMS = _row(
    player_id="00-0035680", player_display_name="Quincy Williams",
    position="LB", team="NYJ", opponent_team="PIT", def_sacks="1",
)
MCDONALD = _row(
    player_id="00-0039142", player_display_name="Will McDonald IV",
    position="DE", team="NYJ", opponent_team="PIT", def_sacks="2",
)

GAME_ROWS = [RODGERS, WILSON, Q_WILLIAMS, MCDONALD]


class BuildPlayerOutcomesTests(unittest.TestCase):
    def test_real_game_rows_produce_expected_totals(self):
        result = outcomes.build_player_outcomes(
            GAME_ROWS, game_id="2025_01_PIT_NYJ"
        )
        self.assertEqual(result["00-0023459"]["passing_yards"], 244.0)
        self.assertEqual(result["00-0023459"]["passing_tds"], 4.0)
        self.assertEqual(result["00-0037740"]["receiving_yards"], 95.0)
        self.assertEqual(result["00-0037740"]["receptions"], 7.0)
        self.assertEqual(result["00-0035680"]["def_sacks"], 1.0)
        self.assertEqual(result["00-0039142"]["def_sacks"], 2.0)

    def test_half_sack_is_preserved_as_a_fraction(self):
        half_sack = _row(
            player_id="00-0036333", def_sacks="0.5",
        )
        result = outcomes.build_player_outcomes(
            GAME_ROWS + [half_sack], game_id="2025_01_PIT_NYJ"
        )
        self.assertEqual(result["00-0036333"]["def_sacks"], 0.5)

    def test_no_rows_for_game_id_is_not_yet_final(self):
        with self.assertRaisesRegex(
            outcomes.BoxScoreOutcomeError, "NOT_YET_FINAL"
        ):
            outcomes.build_player_outcomes(
                GAME_ROWS, game_id="2026_02_DET_BUF"
            )

    def test_duplicate_gsis_id_in_one_game_is_rejected(self):
        duplicate = _row(player_id="00-0023459", passing_yards="1")
        with self.assertRaisesRegex(outcomes.BoxScoreOutcomeError, "duplicate"):
            outcomes.build_player_outcomes(
                GAME_ROWS + [duplicate], game_id="2025_01_PIT_NYJ"
            )

    def test_missing_required_column_fails_closed(self):
        broken = dict(RODGERS)
        del broken["def_sacks"]
        with self.assertRaisesRegex(
            outcomes.BoxScoreOutcomeError, "missing required"
        ):
            outcomes.build_player_outcomes([broken], game_id="2025_01_PIT_NYJ")

    def test_empty_rows_sequence_is_not_yet_final(self):
        with self.assertRaisesRegex(
            outcomes.BoxScoreOutcomeError, "NOT_YET_FINAL"
        ):
            outcomes.build_player_outcomes([], game_id="2025_01_PIT_NYJ")


class OutcomeForCandidateTests(unittest.TestCase):
    def setUp(self):
        self.player_outcomes = outcomes.build_player_outcomes(
            GAME_ROWS, game_id="2025_01_PIT_NYJ"
        )

    def test_primary_passing_yards_candidate_gets_real_stat_value(self):
        candidate = {
            "event_id": "999", "gsis_id": "00-0023459",
            "market": "passing_yards",
        }
        result = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        self.assertEqual(result, {
            "event_id": "999", "gsis_id": "00-0023459",
            "final_status": "FINAL", "appeared": True, "stat_value": 244.0,
        })

    def test_receptions_candidate_gets_real_stat_value(self):
        candidate = {
            "event_id": "999", "gsis_id": "00-0037740",
            "market": "receptions",
        }
        result = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        self.assertEqual(result["stat_value"], 7.0)
        self.assertTrue(result["appeared"])

    def test_record_a_sack_candidate_gets_real_stat_value(self):
        candidate = {
            "event_id": "999", "gsis_id": "00-0039142",
            "market": "record_a_sack",
        }
        result = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        self.assertEqual(result["stat_value"], 2.0)

    def test_rush_plus_rec_yards_sums_two_fields(self):
        combo_row = outcomes.build_player_outcomes(
            GAME_ROWS + [_row(
                player_id="00-0099999", rushing_yards="40",
                receiving_yards="15",
            )],
            game_id="2025_01_PIT_NYJ",
        )
        candidate = {
            "event_id": "999", "gsis_id": "00-0099999",
            "market": "rush_plus_rec_yards",
        }
        result = outcomes.outcome_for_candidate(candidate, combo_row)
        self.assertEqual(result["stat_value"], 55.0)

    def test_anytime_touchdown_sums_every_scoring_field_not_passing(self):
        scorer = outcomes.build_player_outcomes(
            GAME_ROWS + [_row(
                player_id="00-0088888", rushing_tds="1", receiving_tds="1",
                passing_tds="3",
            )],
            game_id="2025_01_PIT_NYJ",
        )
        candidate = {
            "event_id": "999", "gsis_id": "00-0088888",
            "market": "anytime_touchdown",
        }
        result = outcomes.outcome_for_candidate(candidate, scorer)
        self.assertEqual(result["stat_value"], 2.0)

    def test_unmatched_gsis_id_is_void_dnp_shaped(self):
        candidate = {
            "event_id": "999", "gsis_id": "00-0000000",
            "market": "rushing_yards",
        }
        result = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        self.assertEqual(result, {
            "event_id": "999", "gsis_id": "00-0000000",
            "final_status": "FINAL", "appeared": False, "stat_value": None,
        })

    def test_unsupported_market_is_rejected(self):
        candidate = {
            "event_id": "999", "gsis_id": "00-0023459",
            "market": "reception_yardage_threshold",
        }
        with self.assertRaisesRegex(
            outcomes.BoxScoreOutcomeError, "unsupported market"
        ):
            outcomes.outcome_for_candidate(candidate, self.player_outcomes)

    def test_missing_gsis_id_is_rejected(self):
        candidate = {"event_id": "999", "market": "rushing_yards"}
        with self.assertRaisesRegex(
            outcomes.BoxScoreOutcomeError, "gsis_id"
        ):
            outcomes.outcome_for_candidate(candidate, self.player_outcomes)


class HelperTests(unittest.TestCase):
    def test_game_id_for_matches_nflverse_shape(self):
        self.assertEqual(
            outcomes.game_id_for(2026, 2, "det", "buf"), "2026_02_DET_BUF"
        )

    def test_stats_url_rejects_implausible_season(self):
        with self.assertRaises(outcomes.BoxScoreOutcomeError):
            outcomes.stats_url(1500)

    def test_stats_url_formats_real_season(self):
        self.assertEqual(
            outcomes.stats_url(2026),
            "https://github.com/nflverse/nflverse-data/releases/download/"
            "stats_player/stats_player_week_2026.csv",
        )


class EndToEndWithRealGraderTests(unittest.TestCase):
    """Prove the outcome mapping this module builds is actually consumable
    by the real grader, not just shaped like its contract."""

    def setUp(self):
        self.player_outcomes = outcomes.build_player_outcomes(
            GAME_ROWS, game_id="2025_01_PIT_NYJ"
        )

    def _market(self, **overrides):
        market = {
            "event_id": "999", "market_id": "800.1",
            "market": "receiving_yards", "gsis_id": "00-0037740",
            "binding_status": "BOUND", "line": 84.5,
            "market_status": "OPEN", "in_play": False,
            "captured_at": "2025-09-07T16:00:00Z",
            "market_time": "2025-09-07T17:00:00Z",
        }
        market.update(overrides)
        return market

    def test_real_wilson_over_hits_against_real_outcome(self):
        candidate = self._market()
        outcome = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        result = grade_player_prop_market(candidate, outcome, side="OVER")
        self.assertEqual(result["settlement"], "HIT")
        self.assertEqual(result["stat_value"], 95.0)

    def test_real_sack_market_hits_against_real_outcome(self):
        candidate = self._market(
            market="record_a_sack", gsis_id="00-0039142", line=None,
            threshold=1,
        )
        # record_a_sack is a SINGLE_THRESHOLD market: grader reads threshold,
        # not line.
        candidate = {k: v for k, v in candidate.items() if k != "line"}
        outcome = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        result = grade_player_prop_market(candidate, outcome)
        self.assertEqual(result["settlement"], "HIT")
        self.assertEqual(result["stat_value"], 2.0)

    def test_real_dnp_candidate_voids_regardless_of_line(self):
        candidate = self._market(gsis_id="00-0000001")
        outcome = outcomes.outcome_for_candidate(candidate, self.player_outcomes)
        result = grade_player_prop_market(candidate, outcome, side="OVER")
        self.assertEqual(result["settlement"], "VOID_DNP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
