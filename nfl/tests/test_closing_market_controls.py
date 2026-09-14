import unittest

from nfl.research.closing_market_controls import (
    ClosingMarketControlError,
    build_closing_market_control,
)


def row(**overrides):
    value = {
        "game_id": "2025_01_DET_KC",
        "season": 2025,
        "week": 1,
        "game_type": "REG",
        "away_team": "DET",
        "home_team": "KC",
        "away_score": 20,
        "home_score": 27,
        "result": 7,
        "total": 47,
        "spread_line": 3.0,
        "total_line": 45.5,
    }
    value.update(overrides)
    return value


class ClosingMarketControlTests(unittest.TestCase):
    def test_home_favorite_cover_and_over_sign_conventions(self):
        result = build_closing_market_control(row())
        self.assertEqual(result["home_margin_minus_close"], 4.0)
        self.assertEqual(result["spread_settlement"], "HOME_COVER")
        self.assertEqual(result["actual_total_minus_close"], 1.5)
        self.assertEqual(result["total_settlement"], "OVER")

    def test_away_cover_when_home_margin_below_positive_home_favorite_line(self):
        result = build_closing_market_control(row(home_score=22, away_score=20, result=2, total=42, total_line=44.0))
        self.assertEqual(result["spread_settlement"], "AWAY_COVER")
        self.assertEqual(result["total_settlement"], "UNDER")

    def test_negative_spread_means_away_team_favored(self):
        result = build_closing_market_control(row(home_score=24, away_score=27, result=-3, total=51, spread_line=-6.0, total_line=51.0))
        self.assertEqual(result["home_margin_minus_close"], 3.0)
        self.assertEqual(result["spread_settlement"], "HOME_COVER")
        self.assertEqual(result["total_settlement"], "PUSH")

    def test_spread_push_is_exact(self):
        result = build_closing_market_control(row(spread_line=7.0, total_line=47.0))
        self.assertEqual(result["spread_settlement"], "PUSH")
        self.assertEqual(result["total_settlement"], "PUSH")

    def test_historical_controls_are_never_point_in_time_features(self):
        result = build_closing_market_control(row())
        self.assertEqual(result["market_vintage"], "CLOSING")
        self.assertEqual(result["allowed_use"], "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY")
        self.assertIs(result["point_in_time_feature_eligible"], False)
        self.assertIsNone(result["historical_sportsbook_identity"])

    def test_scores_must_match_reported_result_and_total(self):
        with self.assertRaisesRegex(ClosingMarketControlError, "result does not equal"):
            build_closing_market_control(row(result=6))
        with self.assertRaisesRegex(ClosingMarketControlError, "total does not equal"):
            build_closing_market_control(row(total=46))

    def test_missing_closing_lines_fail_closed(self):
        with self.assertRaisesRegex(ClosingMarketControlError, "spread_line must be numeric"):
            build_closing_market_control(row(spread_line=""))
        with self.assertRaisesRegex(ClosingMarketControlError, "total_line must be numeric"):
            build_closing_market_control(row(total_line=None))

    def test_invalid_scores_and_total_line_fail_closed(self):
        with self.assertRaisesRegex(ClosingMarketControlError, "home_score must be >= 0"):
            build_closing_market_control(row(home_score=-1, result=-21, total=19))
        with self.assertRaisesRegex(ClosingMarketControlError, "total_line must be positive"):
            build_closing_market_control(row(total_line=0))

    def test_same_team_and_bad_week_fail_closed(self):
        with self.assertRaisesRegex(ClosingMarketControlError, "must differ"):
            build_closing_market_control(row(away_team="KC"))
        with self.assertRaisesRegex(ClosingMarketControlError, "week must be >= 1"):
            build_closing_market_control(row(week=0))


if __name__ == "__main__":
    unittest.main()
