#!/usr/bin/env python3
import unittest

from nfl.normalize import fanduel_player_props as normalizer


SHA = "a" * 64


def runner(name, selection_id, odds, *, side=None, line=0, status="ACTIVE"):
    value = {
        "runnerName": name,
        "selectionId": selection_id,
        "runnerStatus": status,
        "handicap": line,
        "result": {} if side is None else {"type": side},
        "winRunnerOdds": {
            "americanDisplayOdds": {"americanOddsInt": odds}
        },
    }
    return value


def payload(market_type, market_name, runners, **market_overrides):
    market = {
        "marketId": "734.1",
        "eventId": 35599552,
        "marketName": market_name,
        "marketType": market_type,
        "marketStatus": "OPEN",
        "inPlay": False,
        "marketTime": "2026-09-18T00:15:00.000Z",
        "runners": runners,
    }
    market.update(market_overrides)
    return {
        "attachments": {
            "events": {
                "35599552": {
                    "eventId": 35599552,
                    "name": "Detroit Lions @ Buffalo Bills",
                    "openDate": "2026-09-18T00:15:00.000Z",
                }
            },
            "markets": {"734.1": market},
        }
    }


def normalize(value):
    return normalizer.normalize_payload(
        value,
        captured_at="2026-09-17T16:08:00Z",
        source_payload_sha256=SHA,
    )


class FanDuelPlayerPropNormalizerTests(unittest.TestCase):
    def test_primary_market_emits_paired_selection_level_rows(self):
        result = normalize(
            payload(
                "PLAYER_X_RECEIVING_YARDS_HIGH",
                "Khalil Shakir - Receiving Yds",
                [
                    runner("Khalil Shakir Over", 1, -113, side="OVER", line=44.5),
                    runner("Khalil Shakir Under", 2, -113, side="UNDER", line=44.5),
                ],
            )
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(len(result["selections"]), 2)
        over, under = result["selections"]
        self.assertEqual(over["canonical_market"], "receiving_yards")
        self.assertEqual(over["side"], "OVER")
        self.assertEqual(over["line"], 44.5)
        self.assertEqual(over["price_shape"], "TWO_SIDED")
        self.assertEqual(over["paired_selection_id"], "2")
        self.assertEqual(under["side"], "UNDER")
        self.assertEqual(under["paired_selection_id"], "1")
        self.assertEqual(over["source_payload_sha256"], SHA)

    def test_live_alt_shape_is_one_sided_threshold_ladder(self):
        result = normalize(
            payload(
                "PLAYER_X_ALT_PASSING_YARDS_HIGH",
                "Jared Goff - Alt Passing Yds",
                [
                    runner("Jared Goff 175+ Yards", 10, -1200),
                    runner("Jared Goff 200+ Yards", 11, -490),
                ],
            )
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(
            [row["threshold"] for row in result["selections"]],
            [175.0, 200.0],
        )
        self.assertTrue(all(row["side"] == "YES" for row in result["selections"]))
        self.assertTrue(all(row["line"] is None for row in result["selections"]))
        self.assertEqual(
            result["selections"][0]["price_shape"],
            "ONE_SIDED_THRESHOLD",
        )

    def test_alt_passing_touchdown_unit_is_parsed_strictly(self):
        result = normalize(
            payload(
                "PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH",
                "Josh Allen - Alt Passing TDs",
                [runner("Josh Allen 2+ Passing Touchdowns", 20, 120)],
            )
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["selections"][0]["threshold"], 2.0)
        self.assertEqual(
            result["selections"][0]["canonical_market"],
            "passing_touchdowns",
        )

    def test_anytime_touchdown_emits_each_priced_player(self):
        result = normalize(
            payload(
                "ANY_TIME_TOUCHDOWN_SCORER",
                "Any Time Touchdown Scorer",
                [
                    runner("Jahmyr Gibbs", 30, -300),
                    runner("Josh Allen", 31, -140),
                ],
            )
        )
        self.assertEqual(len(result["selections"]), 2)
        self.assertEqual(result["selections"][0]["threshold"], 1.0)
        self.assertEqual(
            result["selections"][0]["canonical_market"],
            "anytime_touchdown",
        )

    def test_team_defense_runner_is_quarantined_as_non_player(self):
        result = normalize(
            payload(
                "ANY_TIME_TOUCHDOWN_SCORER",
                "Any Time Touchdown Scorer",
                [runner("Detroit Defense", 32, 8000)],
            )
        )
        self.assertEqual(result["selections"], [])
        self.assertEqual(result["rejections"][0]["reason"], "NON_PLAYER_SELECTION")

    def test_long_reception_threshold_is_not_total_receiving_yards(self):
        result = normalize(
            payload(
                "PLAYERS_WITH_20+_YARDS_RECEPTION",
                "Player To Record a 20+ Yard Reception",
                [runner("Amon-Ra St. Brown", 40, -210)],
            )
        )
        row = result["selections"][0]
        self.assertEqual(row["canonical_market"], "reception_yardage_threshold")
        self.assertEqual(row["threshold"], 20.0)

    def test_malformed_alt_runner_fails_closed(self):
        result = normalize(
            payload(
                "PLAYER_X_ALT_RECEPTIONS_HIGH",
                "Dalton Kincaid - Alt Receptions",
                [runner("Dalton Kincaid many Receptions", 50, 130)],
            )
        )
        self.assertEqual(result["selections"], [])
        self.assertEqual(result["rejections"][0]["reason"], "MALFORMED_ALT_RUNNER")

    def test_primary_line_or_identity_mismatch_rejects_whole_market(self):
        value = payload(
            "PLAYER_X_RUSHING_YARDS_HIGH",
            "Josh Allen - Rushing Yds",
            [
                runner("Josh Allen Over", 60, -113, side="OVER", line=31.5),
                runner("Jared Goff Under", 61, -113, side="UNDER", line=32.5),
            ],
        )
        result = normalize(value)
        self.assertEqual(result["selections"], [])
        self.assertEqual(result["rejections"][0]["reason"], "LINE_MISMATCH")

    def test_in_play_or_non_open_market_is_rejected(self):
        base_runners = [
            runner("Josh Allen Over", 70, -113, side="OVER", line=250.5),
            runner("Josh Allen Under", 71, -113, side="UNDER", line=250.5),
        ]
        self.assertEqual(
            normalize(
                payload(
                    "PLAYER_X_PASSING_YARDS_HIGH",
                    "Josh Allen - Passing Yds",
                    base_runners,
                    inPlay=True,
                )
            )["rejections"][0]["reason"],
            "IN_PLAY",
        )
        self.assertEqual(
            normalize(
                payload(
                    "PLAYER_X_PASSING_YARDS_HIGH",
                    "Josh Allen - Passing Yds",
                    base_runners,
                    marketStatus="SUSPENDED",
                )
            )["rejections"][0]["reason"],
            "MARKET_NOT_OPEN",
        )

    def test_unknown_market_is_ignored_and_digest_is_required(self):
        result = normalize(payload("UNKNOWN_PLAYER_PROP", "Unknown", []))
        self.assertEqual(result["selections"], [])
        self.assertEqual(result["stats"]["ignored_unsupported"], 1)
        with self.assertRaisesRegex(ValueError, "64 lowercase hex"):
            normalizer.normalize_payload(
                payload("UNKNOWN_PLAYER_PROP", "Unknown", []),
                captured_at="2026-09-17T16:08:00Z",
                source_payload_sha256="bad",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
