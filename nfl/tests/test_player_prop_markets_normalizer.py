#!/usr/bin/env python3
"""Contracts for strict FanDuel NFL non-passing player-prop normalization."""
import unittest

from nfl.normalize import player_prop_markets


def _event(event_id=999, name="Detroit Lions @ Buffalo Bills"):
    return {
        "999": {"eventId": event_id, "name": name,
                "openDate": "2026-09-17T23:15:00.000Z"}
    } if event_id == 999 else {
        str(event_id): {"eventId": event_id, "name": name,
                         "openDate": "2026-09-17T23:15:00.000Z"}
    }


def _payload(markets, event_id=999, event_name="Detroit Lions @ Buffalo Bills"):
    return {
        "attachments": {
            "events": _event(event_id, event_name),
            "markets": markets,
        }
    }


def primary_market(
    *,
    market_type="PLAYER_X_RUSHING_YARDS_HIGH",
    market_name="Jahmyr Gibbs - Rushing Yds",
    player="Jahmyr Gibbs",
    market_status="OPEN",
    in_play=False,
    over_line=64.5,
    under_line=64.5,
    over_odds=-115,
    under_odds=-115,
    event_id=999,
):
    return {
        "marketId": "800.1",
        "eventId": event_id,
        "marketName": market_name,
        "marketType": market_type,
        "marketStatus": market_status,
        "inPlay": in_play,
        "marketTime": "2026-09-17T23:00:00.000Z",
        "runners": [
            {
                "selectionId": 11,
                "runnerName": f"{player} Over",
                "runnerStatus": "ACTIVE",
                "handicap": over_line,
                "result": {"type": "OVER"},
                "winRunnerOdds": {
                    "americanDisplayOdds": {"americanOddsInt": over_odds}
                },
            },
            {
                "selectionId": 12,
                "runnerName": f"{player} Under",
                "runnerStatus": "ACTIVE",
                "handicap": under_line,
                "result": {"type": "UNDER"},
                "winRunnerOdds": {
                    "americanDisplayOdds": {"americanOddsInt": under_odds}
                },
            },
        ],
    }


def alt_ladder_market(
    *,
    market_type="PLAYER_X_ALT_RUSHING_YARDS_HIGH",
    market_name="Jahmyr Gibbs - Alt Rushing Yds",
    player="Jahmyr Gibbs",
    thresholds=(50, 75, 100),
    odds=(-150, 120, 300),
    statuses=None,
    event_id=999,
    market_status="OPEN",
    in_play=False,
):
    statuses = statuses or ["ACTIVE"] * len(thresholds)
    runners = []
    for idx, (t, o, s) in enumerate(zip(thresholds, odds, statuses)):
        runners.append({
            "selectionId": 20 + idx,
            "runnerName": f"{player} {t}+ Yards",
            "runnerStatus": s,
            "handicap": 0,
            "result": {},
            "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": o}},
        })
    return {
        "marketId": "800.2",
        "eventId": event_id,
        "marketName": market_name,
        "marketType": market_type,
        "marketStatus": market_status,
        "inPlay": in_play,
        "marketTime": "2026-09-17T23:00:00.000Z",
        "runners": runners,
    }


def single_threshold_market(
    *,
    market_type="ANY_TIME_TOUCHDOWN_SCORER",
    players=("Jahmyr Gibbs", "Josh Allen"),
    odds=(150, 200),
    statuses=None,
    event_id=999,
    market_status="OPEN",
    in_play=False,
):
    statuses = statuses or ["ACTIVE"] * len(players)
    runners = []
    for idx, (p, o, s) in enumerate(zip(players, odds, statuses)):
        runners.append({
            "selectionId": 30 + idx,
            "runnerName": p,
            "runnerStatus": s,
            "handicap": 0,
            "result": {},
            "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": o}},
        })
    return {
        "marketId": "800.3",
        "eventId": event_id,
        "marketName": "Anytime Touchdown Scorer",
        "marketType": market_type,
        "marketStatus": market_status,
        "inPlay": in_play,
        "marketTime": "2026-09-17T23:00:00.000Z",
        "runners": runners,
    }


class PrimaryShapeTests(unittest.TestCase):
    def test_rushing_yards_primary_market_normalizes(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": primary_market()}),
            captured_at="2026-09-17T22:00:00Z",
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(len(result["candidates"]), 1)
        row = result["candidates"][0]
        self.assertEqual(row["shape"], "primary")
        self.assertEqual(row["market"], "rushing_yards")
        self.assertEqual(row["player_name"], "Jahmyr Gibbs")
        self.assertEqual(row["line"], 64.5)
        self.assertEqual(row["over_odds"], -115)
        self.assertEqual(row["under_odds"], -115)
        self.assertEqual(row["event_id"], "999")
        self.assertEqual(row["captured_at"], "2026-09-17T22:00:00Z")

    def test_receiving_yards_and_receptions_and_rush_plus_rec_normalize(self):
        for market_type, name, canonical in (
            ("PLAYER_X_RECEIVING_YARDS_LOW", "Amon-Ra St. Brown - Receiving Yds",
             "receiving_yards"),
            ("PLAYER_X_RECEPTIONS_HIGH", "Amon-Ra St. Brown - Total Receptions",
             "receptions"),
            ("PLAYER_X_RUSHING_+_RECEIVING_YARDS",
             "Jahmyr Gibbs - Rushing + Receiving Yds", "rush_plus_rec_yards"),
            ("PLAYER_X_PASSING_TOUCHDOWNS_HIGH", "Josh Allen - Passing TDs",
             "passing_touchdowns"),
        ):
            with self.subTest(market_type=market_type):
                player = name.split(" - ")[0]
                result = player_prop_markets.normalize_payload(
                    _payload({"800.1": primary_market(
                        market_type=market_type, market_name=name,
                        player=player)})
                )
                self.assertEqual(result["rejections"], [])
                self.assertEqual(result["candidates"][0]["market"], canonical)

    def test_line_mismatch_is_explicit_rejection(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": primary_market(over_line=64.5, under_line=65.5)})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "LINE_MISMATCH")

    def test_missing_odds_is_explicit_rejection(self):
        market = primary_market()
        del market["runners"][0]["winRunnerOdds"]
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": market})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "MISSING_ODDS")

    def test_duplicate_over_side_is_rejected(self):
        market = primary_market()
        market["runners"][1]["result"] = {"type": "OVER"}
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": market})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "SIDE_CARDINALITY")

    def test_inactive_runner_is_rejected(self):
        market = primary_market()
        market["runners"][0]["runnerStatus"] = "SUSPENDED"
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": market})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "RUNNER_NOT_ACTIVE")

    def test_player_identity_mismatch_is_rejected(self):
        market = primary_market()
        market["runners"][0]["runnerName"] = "Someone Else Over"
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": market})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["rejections"][0]["reason"], "PLAYER_IDENTITY_MISMATCH"
        )

    def test_in_play_market_is_rejected(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": primary_market(in_play=True)})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "IN_PLAY")

    def test_non_game_event_is_rejected(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": primary_market()}, event_name="NFL Season Specials")
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "UNRESOLVED_GAME")

    def test_suspended_market_is_rejected(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": primary_market(market_status="SUSPENDED")})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "MARKET_NOT_OPEN")


class AltLadderShapeTests(unittest.TestCase):
    def test_alt_rushing_yards_ladder_normalizes_one_row_per_rung(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.2": alt_ladder_market()})
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(len(result["candidates"]), 3)
        thresholds = sorted(row["threshold"] for row in result["candidates"])
        self.assertEqual(thresholds, [50, 75, 100])
        for row in result["candidates"]:
            self.assertEqual(row["shape"], "alt_ladder")
            self.assertEqual(row["market"], "rushing_yards_alt")
            self.assertEqual(row["player_name"], "Jahmyr Gibbs")

    def test_alt_receiving_receptions_and_passing_tds_normalize(self):
        for market_type, name, player, thresholds, canonical in (
            ("PLAYER_X_ALT_RECEIVING_YARDS_LOW",
             "Amon-Ra St. Brown - Alt Receiving Yds", "Amon-Ra St. Brown",
             (40, 60), "receiving_yards_alt"),
            ("PLAYER_X_ALT_RECEPTIONS_HIGH", "Amon-Ra St. Brown - Alt Receptions",
             "Amon-Ra St. Brown", (4, 6), "receptions_alt"),
        ):
            with self.subTest(market_type=market_type):
                runners = [
                    {
                        "selectionId": 40 + i,
                        "runnerName": f"{player} {t}+ Receptions"
                        if "Receptions" in name else f"{player} {t}+ Yards",
                        "runnerStatus": "ACTIVE",
                        "handicap": 0,
                        "result": {},
                        "winRunnerOdds": {
                            "americanDisplayOdds": {"americanOddsInt": -110}
                        },
                    }
                    for i, t in enumerate(thresholds)
                ]
                market = {
                    "marketId": "800.2",
                    "eventId": 999,
                    "marketName": name,
                    "marketType": market_type,
                    "marketStatus": "OPEN",
                    "inPlay": False,
                    "marketTime": "2026-09-17T23:00:00.000Z",
                    "runners": runners,
                }
                result = player_prop_markets.normalize_payload(
                    _payload({"800.2": market})
                )
                self.assertEqual(result["rejections"], [])
                self.assertEqual(len(result["candidates"]), len(thresholds))
                self.assertTrue(
                    all(r["market"] == canonical for r in result["candidates"])
                )

    def test_alt_passing_touchdowns_ladder_normalizes(self):
        market = {
            "marketId": "800.2",
            "eventId": 999,
            "marketName": "Josh Allen - Alt Passing TDs",
            "marketType": "PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH",
            "marketStatus": "OPEN",
            "inPlay": False,
            "marketTime": "2026-09-17T23:00:00.000Z",
            "runners": [
                {
                    "selectionId": 50,
                    "runnerName": "Josh Allen 2+ Passing Touchdowns",
                    "runnerStatus": "ACTIVE",
                    "handicap": 0,
                    "result": {},
                    "winRunnerOdds": {
                        "americanDisplayOdds": {"americanOddsInt": -200}
                    },
                },
            ],
        }
        result = player_prop_markets.normalize_payload(
            _payload({"800.2": market})
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["candidates"][0]["market"], "passing_touchdowns_alt")
        self.assertEqual(result["candidates"][0]["threshold"], 2)

    def test_duplicate_threshold_rung_is_rejected(self):
        market = alt_ladder_market(thresholds=(50, 50), odds=(-150, -150))
        result = player_prop_markets.normalize_payload(
            _payload({"800.2": market})
        )
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["rejections"][0]["reason"], "DUPLICATE_THRESHOLD")

    def test_inactive_rung_is_rejected_others_kept(self):
        market = alt_ladder_market(
            thresholds=(50, 75), odds=(-150, 120),
            statuses=["ACTIVE", "SUSPENDED"],
        )
        result = player_prop_markets.normalize_payload(
            _payload({"800.2": market})
        )
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["threshold"], 50)
        self.assertEqual(result["rejections"][0]["reason"], "RUNNER_NOT_ACTIVE")

    def test_malformed_runner_name_is_rejected(self):
        market = alt_ladder_market()
        market["runners"][0]["runnerName"] = "Someone Else 50+ Yards"
        result = player_prop_markets.normalize_payload(
            _payload({"800.2": market})
        )
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(
            result["rejections"][0]["reason"], "MALFORMED_RUNNER_NAME"
        )

    def test_missing_odds_rung_is_rejected(self):
        market = alt_ladder_market()
        del market["runners"][0]["winRunnerOdds"]
        result = player_prop_markets.normalize_payload(
            _payload({"800.2": market})
        )
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["rejections"][0]["reason"], "MISSING_ODDS")


class SingleThresholdShapeTests(unittest.TestCase):
    def test_anytime_touchdown_scorer_normalizes_all_active_players(self):
        result = player_prop_markets.normalize_payload(
            _payload({"800.3": single_threshold_market()})
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(len(result["candidates"]), 2)
        for row in result["candidates"]:
            self.assertEqual(row["shape"], "single_threshold")
            self.assertEqual(row["market"], "anytime_touchdown")
            self.assertEqual(row["threshold"], 1)

    def test_two_three_four_plus_touchdowns_and_sack_markets_normalize(self):
        for market_type, canonical, threshold in (
            ("TO_SCORE_2+_TOUCHDOWNS", "two_plus_touchdowns", 2),
            ("TO_SCORE_3+_TOUCHDOWNS", "three_plus_touchdowns", 3),
            ("TO_SCORE_4+_TOUCHDOWNS", "four_plus_touchdowns", 4),
            ("TO_RECORD_1+_SACK", "record_a_sack", 1),
        ):
            with self.subTest(market_type=market_type):
                result = player_prop_markets.normalize_payload(
                    _payload({"800.3": single_threshold_market(
                        market_type=market_type)})
                )
                self.assertEqual(result["rejections"], [])
                row = result["candidates"][0]
                self.assertEqual(row["market"], canonical)
                self.assertEqual(row["threshold"], threshold)

    def test_duplicate_player_in_market_is_rejected(self):
        market = single_threshold_market(
            players=("Jahmyr Gibbs", "Jahmyr Gibbs"), odds=(150, 160)
        )
        result = player_prop_markets.normalize_payload(
            _payload({"800.3": market})
        )
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["rejections"][0]["reason"], "DUPLICATE_PLAYER")

    def test_inactive_player_is_rejected_others_kept(self):
        market = single_threshold_market(
            statuses=["ACTIVE", "SUSPENDED"]
        )
        result = player_prop_markets.normalize_payload(
            _payload({"800.3": market})
        )
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["rejections"][0]["reason"], "RUNNER_NOT_ACTIVE")


class DispatchAndPayloadTests(unittest.TestCase):
    def test_passing_yards_market_is_ignored_not_misclassified(self):
        market = primary_market(
            market_type="PLAYER_X_PASSING_YARDS_HIGH",
            market_name="Josh Allen - Passing Yds", player="Josh Allen",
        )
        result = player_prop_markets.normalize_payload(
            _payload({"800.1": market})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["stats"]["ignored_unsupported"], 1)

    def test_reception_yardage_threshold_market_is_ignored_not_misclassified(self):
        market = single_threshold_market(
            market_type="PLAYERS_WITH_20+_YARDS_RECEPTION"
        )
        result = player_prop_markets.normalize_payload(
            _payload({"800.3": market})
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["stats"]["ignored_unsupported"], 1)

    def test_multiple_supported_markets_in_one_payload_all_normalize(self):
        result = player_prop_markets.normalize_payload(
            _payload({
                "800.1": primary_market(),
                "800.2": alt_ladder_market(),
                "800.3": single_threshold_market(),
            })
        )
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["stats"]["markets_total"], 3)
        self.assertEqual(result["stats"]["supported_markets_seen"], 3)
        self.assertEqual(len(result["candidates"]), 1 + 3 + 2)

    def test_missing_attachments_markets_raises(self):
        with self.assertRaises(ValueError):
            player_prop_markets.normalize_payload({"attachments": {}})

    def test_non_mapping_payload_raises(self):
        with self.assertRaises(ValueError):
            player_prop_markets.normalize_payload(["not", "a", "mapping"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
