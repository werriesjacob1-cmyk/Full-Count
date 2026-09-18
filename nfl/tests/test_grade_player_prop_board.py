#!/usr/bin/env python3
"""Contracts for grading a captured player-prop board against a box score.

Candidate shapes below match the real fields PR #123's capture workflow
actually produces (verified against a live-downloaded board artifact,
2026-09-17), not an assumed/simplified schema.
"""
import unittest
from copy import deepcopy

from nfl.research import box_score_outcomes as outcomes
from nfl.research.grade_player_prop_board import (
    PlayerPropBoardIntegrityError,
    grade_bound_candidates,
    grade_player_prop_board,
    seal_player_prop_board,
    verify_player_prop_board,
)


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
    passing_yards="244", passing_tds="4",
)
WILSON = _row(
    player_id="00-0037740", player_display_name="Garrett Wilson",
    position="WR", team="NYJ", opponent_team="PIT",
    receptions="7", receiving_yards="95", receiving_tds="1",
)
MCDONALD = _row(
    player_id="00-0039142", player_display_name="Will McDonald IV",
    position="DE", team="NYJ", opponent_team="PIT", def_sacks="2",
)

PLAYER_OUTCOMES = outcomes.build_player_outcomes(
    [RODGERS, WILSON, MCDONALD], game_id="2025_01_PIT_NYJ",
)


def _board_row(**overrides):
    row = {
        "binding_contract_version": 1,
        "binding_method": "exact_name+event_team+qb_position",
        "binding_status": "BOUND",
        "candidate_count": 1,
        "captured_at": "2025-09-07T16:00:00Z",
        "esb_id": "ROD123456",
        "event_away_team": "PIT",
        "event_home_team": "NYJ",
        "event_id": "999",
        "event_name": "Pittsburgh Steelers @ New York Jets",
        "event_open_date": "2025-09-07T17:00:00.000Z",
        "gsis_id": "00-0023459",
        "market": "passing_yards",
        "market_id": "734.1",
        "market_name": "Aaron Rodgers - Passing Yds",
        "market_time": "2025-09-07T17:00:00.000Z",
        "market_type": "PLAYER_X_PASSING_YARDS_HIGH",
        "line": 220.5,
        "over_odds": -113,
        "under_odds": -113,
        "player_name": "Aaron Rodgers",
        "roster_status": "ACT",
        "shape": "primary",
        "source": "fanduel_nfl",
        "source_payload_sha256": "a" * 64,
        "source_tab": "passing-props",
        "source_url": "https://example/event-page",
        "team": "PIT",
    }
    row.update(overrides)
    return row


def _sealed_board(*rows):
    bound = list(rows or [_board_row()])
    return seal_player_prop_board({
        "analysis": "NFL_LIVE_PLAYER_PROP_BOARD_CAPTURE",
        "status": "RESEARCH_ONLY_NO_PUBLICATION",
        "grading_status": "PREGAME_CAPTURE_NOT_GRADED",
        "created_at": "2025-09-07T16:05:00Z",
        "code_sha": "test-sha",
        "event": {
            "event_id": "999",
            "event_name": "Pittsburgh Steelers @ New York Jets",
            "open_date": "2025-09-07T17:00:00Z",
        },
        "coverage": {},
        "candidates": {
            "raw_normalized_count": len(bound),
            "bound_count": len(bound),
            "unmapped_count": 0,
            "bound": bound,
            "unmapped": [],
            "rejections": [],
        },
        "identity": {"roster_sha256": "b" * 64},
    })


class GradeBoundCandidatesTests(unittest.TestCase):
    def test_complete_sealed_board_verifies_before_grading(self):
        board = _sealed_board()
        result = grade_player_prop_board(board, PLAYER_OUTCOMES)
        self.assertEqual(result["source_board_sha256"], board["board_sha256"])
        self.assertEqual(result["event_id"], "999")
        self.assertEqual(result["graded_count"], 1)
        self.assertEqual(result["graded"][0]["settlement"], "HIT")

    def test_post_seal_mutation_fails_before_grading(self):
        board = _sealed_board()
        board["candidates"]["bound"][0]["line"] = 1.5
        with self.assertRaisesRegex(PlayerPropBoardIntegrityError, "hash mismatch"):
            grade_player_prop_board(board, PLAYER_OUTCOMES)

    def test_missing_digest_and_population_drift_fail_closed(self):
        board = _sealed_board()
        without_digest = deepcopy(board)
        without_digest.pop("board_sha256")
        with self.assertRaisesRegex(PlayerPropBoardIntegrityError, "board_sha256"):
            verify_player_prop_board(without_digest)

        drifted = deepcopy(board)
        drifted["candidates"]["bound_count"] = 2
        drifted.pop("board_sha256")
        with self.assertRaisesRegex(PlayerPropBoardIntegrityError, "bound_count"):
            seal_player_prop_board(drifted)

    def test_post_kickoff_board_cannot_be_sealed(self):
        board = _sealed_board()
        board.pop("board_sha256")
        board["created_at"] = "2025-09-07T17:00:00Z"
        with self.assertRaisesRegex(PlayerPropBoardIntegrityError, "strictly precede kickoff"):
            seal_player_prop_board(board)

    def test_real_passing_yards_primary_candidate_grades_hit(self):
        result = grade_bound_candidates([_board_row()], PLAYER_OUTCOMES)
        self.assertEqual(result["graded_count"], 1)
        self.assertEqual(result["error_count"], 0)
        row = result["graded"][0]
        self.assertEqual(row["settlement"], "HIT")
        self.assertEqual(row["stat_value"], 244.0)
        self.assertEqual(row["player_name"], "Aaron Rodgers")

    def test_alt_ladder_candidate_shape_grades(self):
        candidate = _board_row(
            market="receptions_alt", market_id="734.2",
            market_name="Garrett Wilson - Alt Receptions",
            market_type="PLAYER_X_ALT_RECEPTIONS_HIGH",
            gsis_id="00-0037740", player_name="Garrett Wilson",
            event_away_team="PIT", event_home_team="NYJ", team="NYJ",
            shape="alt_ladder", threshold=5, selection_id="123",
            yes_odds=-150,
        )
        del candidate["line"]
        del candidate["over_odds"]
        del candidate["under_odds"]
        result = grade_bound_candidates([candidate], PLAYER_OUTCOMES)
        self.assertEqual(result["graded_count"], 1)
        self.assertEqual(result["graded"][0]["settlement"], "HIT")

    def test_single_threshold_candidate_shape_grades(self):
        candidate = _board_row(
            market="record_a_sack", market_id="734.3",
            market_name="To Record 1+ Sack",
            market_type="TO_RECORD_1+_SACK",
            gsis_id="00-0039142", player_name="Will McDonald IV",
            event_away_team="PIT", event_home_team="NYJ", team="NYJ",
            shape="single_threshold", threshold=1, selection_id="456",
            yes_odds=250,
        )
        del candidate["line"]
        del candidate["over_odds"]
        del candidate["under_odds"]
        result = grade_bound_candidates([candidate], PLAYER_OUTCOMES)
        self.assertEqual(result["graded"][0]["settlement"], "HIT")
        self.assertEqual(result["graded"][0]["stat_value"], 2.0)

    def test_non_bound_candidate_is_skipped_not_errored(self):
        candidate = _board_row(binding_status="UNRESOLVED_PLAYER")
        result = grade_bound_candidates([candidate], PLAYER_OUTCOMES)
        self.assertEqual(result["graded_count"], 0)
        self.assertEqual(result["error_count"], 0)

    def test_unresolved_gsis_id_grades_void_dnp(self):
        candidate = _board_row(gsis_id="00-0000000")
        result = grade_bound_candidates([candidate], PLAYER_OUTCOMES)
        self.assertEqual(result["graded_count"], 1)
        self.assertEqual(result["graded"][0]["settlement"], "VOID_DNP")

    def test_malformed_candidate_goes_to_errors_not_crash(self):
        good = _board_row()
        bad = _board_row(market_id="734.9")
        del bad["captured_at"]
        result = grade_bound_candidates([good, bad], PLAYER_OUTCOMES)
        self.assertEqual(result["graded_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["market_id"], "734.9")

    def test_settlement_and_market_counts_aggregate_correctly(self):
        rows = [
            _board_row(market_id="734.1"),
            _board_row(market_id="734.2", line=500.5),
        ]
        result = grade_bound_candidates(rows, PLAYER_OUTCOMES)
        self.assertEqual(result["settlement_counts"], {"HIT": 1, "MISS": 1})
        self.assertEqual(result["market_counts"], {"passing_yards": 2})

    def test_under_side_convention_flips_settlement(self):
        result = grade_bound_candidates(
            [_board_row()], PLAYER_OUTCOMES, primary_side="UNDER",
        )
        self.assertEqual(result["graded"][0]["settlement"], "MISS")
        self.assertEqual(result["primary_side_convention"], "UNDER")

    def test_invalid_primary_side_raises(self):
        with self.assertRaises(ValueError):
            grade_bound_candidates([_board_row()], PLAYER_OUTCOMES, primary_side="MIDDLE")

    def test_empty_board_grades_nothing(self):
        result = grade_bound_candidates([], PLAYER_OUTCOMES)
        self.assertEqual(result["graded_count"], 0)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["settlement_counts"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
