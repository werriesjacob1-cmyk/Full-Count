#!/usr/bin/env python3
"""Contracts for fail-closed NFL player-prop grading."""
import unittest

from nfl.prospective.player_prop_grader import (
    PlayerPropGradeError,
    grade_player_prop_market,
)


def primary_market(**overrides):
    market = {
        "event_id": "999",
        "market_id": "800.1",
        "market": "rushing_yards",
        "gsis_id": "00-0039164",
        "binding_status": "BOUND",
        "line": 64.5,
        "market_status": "OPEN",
        "in_play": False,
        "captured_at": "2026-09-17T22:00:00Z",
        "market_time": "2026-09-17T23:00:00Z",
    }
    market.update(overrides)
    return market


def ladder_market(**overrides):
    market = {
        "event_id": "999",
        "market_id": "800.2",
        "market": "rushing_yards_alt",
        "gsis_id": "00-0039164",
        "binding_status": "BOUND",
        "threshold": 75,
        "market_status": "OPEN",
        "in_play": False,
        "captured_at": "2026-09-17T22:00:00Z",
        "market_time": "2026-09-17T23:00:00Z",
    }
    market.update(overrides)
    return market


def sack_market(**overrides):
    market = {
        "event_id": "999",
        "market_id": "800.3",
        "market": "record_a_sack",
        "gsis_id": "00-0038543",
        "binding_status": "BOUND",
        "threshold": 1,
        "market_status": "OPEN",
        "in_play": False,
        "captured_at": "2026-09-17T22:00:00Z",
        "market_time": "2026-09-17T23:00:00Z",
    }
    market.update(overrides)
    return market


def outcome(**overrides):
    out = {
        "event_id": "999",
        "gsis_id": "00-0039164",
        "final_status": "FINAL",
        "appeared": True,
        "stat_value": 80,
    }
    out.update(overrides)
    return out


class PrimaryShapeGradingTests(unittest.TestCase):
    def test_over_hits_when_stat_exceeds_line(self):
        result = grade_player_prop_market(
            primary_market(), outcome(stat_value=80), side="OVER"
        )
        self.assertEqual(result["settlement"], "HIT")
        self.assertEqual(result["side"], "OVER")

    def test_over_misses_when_stat_below_line(self):
        result = grade_player_prop_market(
            primary_market(), outcome(stat_value=40), side="OVER"
        )
        self.assertEqual(result["settlement"], "MISS")

    def test_under_hits_when_stat_below_line(self):
        result = grade_player_prop_market(
            primary_market(), outcome(stat_value=40), side="UNDER"
        )
        self.assertEqual(result["settlement"], "HIT")

    def test_exact_line_is_push(self):
        result = grade_player_prop_market(
            primary_market(line=64.0), outcome(stat_value=64), side="OVER"
        )
        self.assertEqual(result["settlement"], "PUSH")

    def test_missing_side_fails_closed(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(primary_market(), outcome())

    def test_invalid_side_fails_closed(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(primary_market(), outcome(), side="MIDDLE")


class AltLadderShapeGradingTests(unittest.TestCase):
    def test_stat_at_or_above_threshold_hits(self):
        result = grade_player_prop_market(ladder_market(), outcome(stat_value=75))
        self.assertEqual(result["settlement"], "HIT")

    def test_stat_below_threshold_misses(self):
        result = grade_player_prop_market(ladder_market(), outcome(stat_value=74))
        self.assertEqual(result["settlement"], "MISS")

    def test_no_push_state_exists_for_ladder(self):
        for stat_value in (75, 74, 76, 0, 200):
            with self.subTest(stat_value=stat_value):
                result = grade_player_prop_market(
                    ladder_market(), outcome(stat_value=stat_value)
                )
                self.assertIn(result["settlement"], {"HIT", "MISS"})


class SingleThresholdShapeGradingTests(unittest.TestCase):
    def test_touchdown_count_at_threshold_hits(self):
        market = ladder_market(market="two_plus_touchdowns", threshold=2)
        result = grade_player_prop_market(market, outcome(stat_value=2))
        self.assertEqual(result["settlement"], "HIT")

    def test_touchdown_count_below_threshold_misses(self):
        market = ladder_market(market="two_plus_touchdowns", threshold=2)
        result = grade_player_prop_market(market, outcome(stat_value=1))
        self.assertEqual(result["settlement"], "MISS")

    def test_half_sack_credit_hits_record_a_sack(self):
        result = grade_player_prop_market(
            sack_market(),
            outcome(gsis_id="00-0038543", stat_value=0.5),
        )
        self.assertEqual(result["settlement"], "HIT")

    def test_zero_sacks_misses(self):
        result = grade_player_prop_market(
            sack_market(),
            outcome(gsis_id="00-0038543", stat_value=0),
        )
        self.assertEqual(result["settlement"], "MISS")


class VoidDnpTests(unittest.TestCase):
    def test_did_not_appear_voids_primary_regardless_of_side(self):
        result = grade_player_prop_market(
            primary_market(), outcome(appeared=False, stat_value=0), side="UNDER"
        )
        self.assertEqual(result["settlement"], "VOID_DNP")
        self.assertIsNone(result["stat_value"])

    def test_did_not_appear_voids_ladder(self):
        result = grade_player_prop_market(
            ladder_market(), outcome(appeared=False, stat_value=0)
        )
        self.assertEqual(result["settlement"], "VOID_DNP")

    def test_did_not_appear_voids_sack_market(self):
        result = grade_player_prop_market(
            sack_market(), outcome(gsis_id="00-0038543", appeared=False, stat_value=0)
        )
        self.assertEqual(result["settlement"], "VOID_DNP")

    def test_appeared_must_be_boolean(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(), outcome(appeared="yes"), side="OVER"
            )


class FailClosedGuardTests(unittest.TestCase):
    def test_unbound_candidate_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(binding_status="UNRESOLVED_PLAYER"),
                outcome(),
                side="OVER",
            )

    def test_event_id_mismatch_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(), outcome(event_id="111"), side="OVER"
            )

    def test_gsis_id_mismatch_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(), outcome(gsis_id="00-0000000"), side="OVER"
            )

    def test_in_play_market_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(in_play=True), outcome(), side="OVER"
            )

    def test_suspended_market_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(market_status="SUSPENDED"), outcome(), side="OVER"
            )

    def test_captured_at_after_market_time_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(
                    captured_at="2026-09-17T23:30:00Z",
                    market_time="2026-09-17T23:00:00Z",
                ),
                outcome(),
                side="OVER",
            )

    def test_non_final_outcome_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(), outcome(final_status="IN_PROGRESS"), side="OVER"
            )

    def test_unsupported_market_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(market="reception_yardage_threshold"),
                outcome(), side="OVER",
            )

    def test_passing_yards_is_a_supported_primary_market(self):
        result = grade_player_prop_market(
            primary_market(market="passing_yards"),
            outcome(stat_value=300), side="OVER",
        )
        self.assertEqual(result["settlement"], "HIT")

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(PlayerPropGradeError):
            grade_player_prop_market(
                primary_market(captured_at="2026-09-17T22:00:00"),
                outcome(),
                side="OVER",
            )


class ProvenanceTests(unittest.TestCase):
    def test_grade_sha256_is_deterministic(self):
        r1 = grade_player_prop_market(primary_market(), outcome(), side="OVER")
        r2 = grade_player_prop_market(primary_market(), outcome(), side="OVER")
        self.assertEqual(r1["grade_sha256"], r2["grade_sha256"])

    def test_grade_sha256_differs_on_different_settlement(self):
        hit = grade_player_prop_market(
            primary_market(), outcome(stat_value=80), side="OVER"
        )
        miss = grade_player_prop_market(
            primary_market(), outcome(stat_value=40), side="OVER"
        )
        self.assertNotEqual(hit["grade_sha256"], miss["grade_sha256"])

    def test_inputs_are_not_mutated(self):
        market = primary_market()
        out = outcome()
        market_copy = dict(market)
        outcome_copy = dict(out)
        grade_player_prop_market(market, out, side="OVER")
        self.assertEqual(market, market_copy)
        self.assertEqual(out, outcome_copy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
