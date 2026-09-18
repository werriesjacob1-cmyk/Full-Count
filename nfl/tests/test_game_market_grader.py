from copy import deepcopy
import unittest

from nfl.prospective.game_market_grader import GameMarketGradeError, grade_game_market

SHA = "a" * 64


def market(**overrides):
    value = {
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "market": "spread",
        "market_type": "MATCH_HANDICAP_(2-WAY)",
        "market_name": "Spread",
        "event_id": "35601246",
        "market_id": "spread-1",
        "market_time": "2026-09-15T00:15:00Z",
        "captured_at": "2026-09-14T23:30:00Z",
        "source_payload_sha256": SHA,
        "market_status": "OPEN",
        "in_play": False,
        "away_line": 2.5,
        "home_line": -2.5,
        "away_odds": -115,
        "home_odds": -105,
        "away_selection_id": "away-1",
        "home_selection_id": "home-1",
    }
    value.update(overrides)
    return value


def outcome(**overrides):
    value = {"event_id": "35601246", "home_score": 27, "away_score": 20, "final_status": "FINAL"}
    value.update(overrides)
    return value


def moneyline():
    return market(market="moneyline", market_type="MONEY_LINE", market_name="Moneyline", market_id="ml-1", away_line=None, home_line=None)


def total(line=47.0):
    return market(
        market="game_total",
        market_type="TOTAL_POINTS_(OVER/UNDER)",
        market_name="Total Points",
        market_id="total-1",
        line=line,
        away_line=None,
        home_line=None,
        over_odds=-110,
        under_odds=-110,
        over_selection_id="over-1",
        under_selection_id="under-1",
    )


class GameMarketGraderTests(unittest.TestCase):
    def test_moneyline_home_hit_and_away_miss(self):
        self.assertEqual(grade_game_market(moneyline(), outcome(), side="HOME")["settlement"], "HIT")
        self.assertEqual(grade_game_market(moneyline(), outcome(), side="AWAY")["settlement"], "MISS")

    def test_moneyline_tie_is_unresolved_not_guessed(self):
        result = grade_game_market(moneyline(), outcome(home_score=20, away_score=20), side="HOME")
        self.assertEqual(result["settlement"], "UNRESOLVED_TIE")

    def test_home_spread_hit_push_miss(self):
        for home_line, expected in [(-2.5, "HIT"), (-7.0, "PUSH"), (-7.5, "MISS")]:
            with self.subTest(home_line=home_line):
                m = market(home_line=home_line, away_line=-home_line)
                self.assertEqual(grade_game_market(m, outcome(), side="HOME")["settlement"], expected)

    def test_away_spread_orientation(self):
        m = market(home_line=-7.5, away_line=7.5)
        self.assertEqual(grade_game_market(m, outcome(), side="AWAY")["settlement"], "HIT")

    def test_total_settlements(self):
        for side, line, expected in [("OVER", 46.5, "HIT"), ("OVER", 47.0, "PUSH"), ("UNDER", 47.5, "HIT"), ("UNDER", 46.5, "MISS")]:
            with self.subTest(side=side, line=line):
                self.assertEqual(grade_game_market(total(line), outcome(), side=side)["settlement"], expected)

    def test_event_mismatch_fails_closed(self):
        with self.assertRaisesRegex(GameMarketGradeError, "event_id mismatch"):
            grade_game_market(market(), outcome(event_id="other"), side="HOME")

    def test_capture_must_be_strictly_pregame(self):
        with self.assertRaisesRegex(GameMarketGradeError, "strictly before market_time"):
            grade_game_market(market(captured_at="2026-09-15T00:15:00Z"), outcome(), side="HOME")

    def test_naive_time_fails_closed(self):
        with self.assertRaisesRegex(GameMarketGradeError, "timezone-aware"):
            grade_game_market(market(captured_at="2026-09-14T23:30:00"), outcome(), side="HOME")

    def test_nonfinal_outcome_fails_closed(self):
        with self.assertRaisesRegex(GameMarketGradeError, "not final"):
            grade_game_market(market(), outcome(final_status="IN_PROGRESS"), side="HOME")

    def test_invalid_scores_fail_closed(self):
        for bad_score in [-1, 20.5, True, "20"]:
            with self.subTest(bad_score=bad_score):
                with self.assertRaisesRegex(GameMarketGradeError, "non-negative integer"):
                    grade_game_market(market(), outcome(home_score=bad_score), side="HOME")

    def test_bad_market_side_pair_fails_closed(self):
        with self.assertRaisesRegex(GameMarketGradeError, "unsupported side"):
            grade_game_market(total(47.5), outcome(), side="HOME")

    def test_spread_handicaps_must_be_opposites(self):
        with self.assertRaisesRegex(GameMarketGradeError, "must be opposites"):
            grade_game_market(market(home_line=-2.5, away_line=3.0), outcome(), side="HOME")

    def test_total_requires_numeric_line(self):
        with self.assertRaisesRegex(GameMarketGradeError, "line must be numeric"):
            grade_game_market(total(None), outcome(), side="OVER")

    def test_market_must_be_open_and_pregame(self):
        with self.assertRaisesRegex(GameMarketGradeError, "OPEN and pregame"):
            grade_game_market(market(in_play=True), outcome(), side="HOME")

    def test_bad_payload_hash_fails_closed(self):
        with self.assertRaisesRegex(GameMarketGradeError, "64 lowercase hex"):
            grade_game_market(market(source_payload_sha256="xyz"), outcome(), side="HOME")

    def test_inputs_are_not_mutated_and_grade_is_deterministic(self):
        m = market()
        o = outcome()
        before_m = deepcopy(m)
        before_o = deepcopy(o)
        first = grade_game_market(m, o, side="HOME")
        second = grade_game_market(m, o, side="HOME")
        self.assertEqual(m, before_m)
        self.assertEqual(o, before_o)
        self.assertEqual(first, second)
        self.assertEqual(len(first["grade_sha256"]), 64)

    def test_result_preserves_normalized_market_identity(self):
        result = grade_game_market(market(), outcome(), side="AWAY")
        self.assertEqual(result["event_id"], "35601246")
        self.assertEqual(result["market_id"], "spread-1")
        self.assertEqual(result["canonical_market"], "spread")
        self.assertEqual(result["line"], 2.5)


if __name__ == "__main__":
    unittest.main()
