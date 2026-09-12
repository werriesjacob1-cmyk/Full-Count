#!/usr/bin/env python3
"""Contracts for strict FanDuel NFL primary passing-yards normalization."""
import unittest

from nfl.normalize import fanduel_passing


def payload(
    *,
    market_type="PLAYER_X_PASSING_YARDS_HIGH",
    market_status="OPEN",
    in_play=False,
    over_line=247.5,
    under_line=247.5,
    over_odds=-114,
    under_odds=-114,
    event_name="Philadelphia Eagles @ Kansas City Chiefs",
):
    market = {
        "marketId": "734.123",
        "eventId": 999,
        "marketName": "Jalen Hurts - Passing Yds",
        "marketType": market_type,
        "marketStatus": market_status,
        "inPlay": in_play,
        "marketTime": "2026-09-13T17:00:00.000Z",
        "runners": [
            {
                "selectionId": 1,
                "runnerName": "Jalen Hurts Over",
                "runnerStatus": "ACTIVE",
                "handicap": over_line,
                "result": {"type": "OVER"},
                "winRunnerOdds": {
                    "americanDisplayOdds": {"americanOddsInt": over_odds}
                },
            },
            {
                "selectionId": 2,
                "runnerName": "Jalen Hurts Under",
                "runnerStatus": "ACTIVE",
                "handicap": under_line,
                "result": {"type": "UNDER"},
                "winRunnerOdds": {
                    "americanDisplayOdds": {"americanOddsInt": under_odds}
                },
            },
        ],
    }
    return {
        "attachments": {
            "events": {
                "999": {
                    "eventId": 999,
                    "name": event_name,
                    "openDate": "2026-09-13T17:00:00.000Z",
                }
            },
            "markets": {"734.123": market},
        }
    }


class FanDuelPassingNormalizerTests(unittest.TestCase):
    def test_primary_passing_yards_market_normalizes(self):
        result = fanduel_passing.normalize_payload(
            payload(),
            captured_at="2026-09-13T16:50:00Z",
        )
        self.assertEqual(result["stats"]["primary_markets_seen"], 1)
        self.assertEqual(result["stats"]["normalized"], 1)
        self.assertEqual(result["rejections"], [])
        row = result["candidates"][0]
        self.assertEqual(row["market"], "passing_yards")
        self.assertEqual(row["player_name"], "Jalen Hurts")
        self.assertEqual(row["line"], 247.5)
        self.assertEqual(row["over_odds"], -114)
        self.assertEqual(row["under_odds"], -114)
        self.assertEqual(row["event_id"], "999")
        self.assertEqual(
            row["event_name"],
            "Philadelphia Eagles @ Kansas City Chiefs",
        )
        self.assertEqual(row["over_selection_id"], "1")
        self.assertEqual(row["under_selection_id"], "2")
        self.assertEqual(row["captured_at"], "2026-09-13T16:50:00Z")

    def test_high_medium_low_primary_slot_types_are_supported(self):
        for suffix in ("HIGH", "MEDIUM", "LOW"):
            with self.subTest(suffix=suffix):
                result = fanduel_passing.normalize_payload(
                    payload(
                        market_type=f"PLAYER_X_PASSING_YARDS_{suffix}"
                    )
                )
                self.assertEqual(len(result["candidates"]), 1)

    def test_alt_passing_yards_is_ignored_not_misclassified(self):
        result = fanduel_passing.normalize_payload(
            payload(market_type="PLAYER_X_ALT_PASSING_YARDS_HIGH")
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["stats"]["ignored_non_primary"], 1)

    def test_line_mismatch_is_explicit_rejection(self):
        result = fanduel_passing.normalize_payload(
            payload(over_line=247.5, under_line=248.5)
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "LINE_MISMATCH")

    def test_missing_side_odds_is_explicit_rejection(self):
        p = payload()
        del p["attachments"]["markets"]["734.123"]["runners"][1][
            "winRunnerOdds"
        ]
        result = fanduel_passing.normalize_payload(p)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "MISSING_ODDS")

    def test_in_play_market_is_rejected(self):
        result = fanduel_passing.normalize_payload(payload(in_play=True))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "IN_PLAY")

    def test_non_game_event_is_rejected(self):
        result = fanduel_passing.normalize_payload(
            payload(event_name="NFL Season Specials")
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "UNRESOLVED_GAME")

    def test_market_and_runner_player_names_must_agree(self):
        p = payload()
        p["attachments"]["markets"]["734.123"]["runners"][0][
            "runnerName"
        ] = "Patrick Mahomes Over"
        result = fanduel_passing.normalize_payload(p)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["rejections"][0]["reason"],
            "PLAYER_IDENTITY_MISMATCH",
        )

    def test_duplicate_over_side_is_rejected(self):
        p = payload()
        p["attachments"]["markets"]["734.123"]["runners"][1][
            "result"
        ] = {"type": "OVER"}
        result = fanduel_passing.normalize_payload(p)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["rejections"][0]["reason"],
            "SIDE_CARDINALITY",
        )

    def test_unknown_primary_type_fails_closed(self):
        result = fanduel_passing.normalize_payload(
            payload(market_type="PLAYER_X_PASSING_YARDS_UNKNOWN")
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["rejections"][0]["reason"],
            "UNSUPPORTED_PRIMARY_TYPE",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
