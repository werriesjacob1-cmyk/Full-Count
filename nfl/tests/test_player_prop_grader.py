#!/usr/bin/env python3
from copy import deepcopy
import unittest

from nfl.prospective.player_prop_grader import (
    PlayerPropGradeError,
    grade_player_prop,
)


SHA = "a" * 64
OUTCOME_SHA = "b" * 64
GSIS = "00-0034857"


def market(**overrides):
    value = {
        "event_id": "35599552",
        "market_id": "734.1",
        "selection_id": "10",
        "canonical_market": "passing_yards",
        "player_gsis_id": GSIS,
        "binding_status": "BOUND",
        "market_status": "OPEN",
        "in_play": False,
        "side": "OVER",
        "line": 250.5,
        "threshold": None,
        "captured_at": "2026-09-17T23:45:00Z",
        "market_time": "2026-09-18T00:15:00Z",
        "source_payload_sha256": SHA,
        "prediction_recorded_pregame": True,
    }
    value.update(overrides)
    return value


def outcome(**overrides):
    value = {
        "event_id": "35599552",
        "final_status": "FINAL",
        "participation_complete": True,
        "player_stats_complete": True,
        "play_by_play_complete": True,
        "touchdown_coverage": "all_credited",
        "participants": [GSIS],
        "player_stats": {
            GSIS: {
                "passing_yards": 275,
                "passing_touchdowns": 2,
                "rushing_yards": 35,
                "receiving_yards": 0,
                "receptions": 0,
                "touchdowns": 1,
                "sacks": 0,
                "longest_reception_yards": 0,
            }
        },
        "source_outcome_sha256": OUTCOME_SHA,
    }
    value.update(overrides)
    return value


class PlayerPropGraderTests(unittest.TestCase):
    def test_numeric_over_under_hit_miss_and_push(self):
        self.assertEqual(grade_player_prop(market(), outcome())["settlement"], "HIT")
        self.assertEqual(
            grade_player_prop(market(side="UNDER"), outcome())["settlement"],
            "MISS",
        )
        self.assertEqual(
            grade_player_prop(market(line=275.0), outcome())["settlement"],
            "PUSH",
        )

    def test_alt_threshold_is_yes_market(self):
        result = grade_player_prop(
            market(side="YES", line=None, threshold=275.0), outcome()
        )
        self.assertEqual(result["settlement"], "HIT")
        self.assertEqual(result["final_stat_value"], 275.0)

    def test_rush_plus_receive_sums_same_bound_player_row(self):
        result = grade_player_prop(
            market(
                canonical_market="rush_plus_rec_yards",
                side="OVER",
                line=34.5,
                prediction_recorded_pregame=False,
            ),
            outcome(),
        )
        self.assertEqual(result["final_stat_value"], 35.0)
        self.assertEqual(result["settlement"], "HIT")
        self.assertEqual(result["evidence_class"], "market_only_settled")

    def test_no_appearance_is_void_dnp_not_zero_miss(self):
        result = grade_player_prop(
            market(
                canonical_market="record_a_sack",
                side="YES",
                line=None,
                threshold=1.0,
                prediction_recorded_pregame=False,
            ),
            outcome(participants=[], player_stats={}),
        )
        self.assertEqual(result["settlement"], "VOID_DNP")
        self.assertIsNone(result["final_stat_value"])

    def test_participant_without_explicit_stats_fails_closed(self):
        with self.assertRaisesRegex(PlayerPropGradeError, "explicit stats row"):
            grade_player_prop(market(), outcome(player_stats={}))

    def test_long_reception_requires_complete_play_by_play(self):
        prop = market(
            canonical_market="reception_yardage_threshold",
            side="YES",
            line=None,
            threshold=20.0,
            prediction_recorded_pregame=False,
        )
        with self.assertRaisesRegex(PlayerPropGradeError, "play-by-play"):
            grade_player_prop(prop, outcome(play_by_play_complete=False))

    def test_touchdown_requires_all_scoring_mechanisms(self):
        prop = market(
            canonical_market="anytime_touchdown",
            side="YES",
            line=None,
            threshold=1.0,
            prediction_recorded_pregame=False,
        )
        with self.assertRaisesRegex(PlayerPropGradeError, "all credited"):
            grade_player_prop(prop, outcome(touchdown_coverage="rush_receive_only"))
        self.assertEqual(grade_player_prop(prop, outcome())["settlement"], "HIT")

    def test_fixed_threshold_contract_rejects_wrong_value(self):
        prop = market(
            canonical_market="two_plus_touchdowns",
            side="YES",
            line=None,
            threshold=3.0,
            prediction_recorded_pregame=False,
        )
        with self.assertRaisesRegex(PlayerPropGradeError, "must equal 2"):
            grade_player_prop(prop, outcome())

    def test_only_passing_yards_with_pregame_prediction_is_prediction_graded(self):
        self.assertEqual(
            grade_player_prop(market(), outcome())["evidence_class"],
            "prediction_graded",
        )
        passing_td = market(
            canonical_market="passing_touchdowns",
            line=1.5,
            prediction_recorded_pregame=True,
        )
        self.assertEqual(
            grade_player_prop(passing_td, outcome())["evidence_class"],
            "market_only_settled",
        )

    def test_incomplete_participation_nonfinal_and_late_capture_fail_closed(self):
        with self.assertRaisesRegex(PlayerPropGradeError, "participation"):
            grade_player_prop(market(), outcome(participation_complete=False))
        with self.assertRaisesRegex(PlayerPropGradeError, "not final"):
            grade_player_prop(market(), outcome(final_status="IN_PROGRESS"))
        with self.assertRaisesRegex(PlayerPropGradeError, "strictly before"):
            grade_player_prop(
                market(captured_at="2026-09-18T00:15:00Z"), outcome()
            )

    def test_inputs_are_immutable_and_grade_is_deterministic(self):
        prop = market()
        final = outcome()
        before_prop = deepcopy(prop)
        before_final = deepcopy(final)
        first = grade_player_prop(prop, final)
        second = grade_player_prop(prop, final)
        self.assertEqual(prop, before_prop)
        self.assertEqual(final, before_final)
        self.assertEqual(first, second)
        self.assertEqual(len(first["grade_sha256"]), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
