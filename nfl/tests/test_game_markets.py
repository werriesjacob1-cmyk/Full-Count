#!/usr/bin/env python3
"""Contracts for primary FanDuel NFL full-game market normalization."""
import copy
import unittest

from nfl.normalize.game_markets import normalize_primary_game_markets


def odds(value):
    return {
        "americanDisplayOdds": {"americanOdds": value, "americanOddsInt": value}
    }


def market(mid, mtype, name, runners, event="35601246"):
    return {
        "marketId": mid,
        "eventId": event,
        "marketName": name,
        "marketType": mtype,
        "marketTime": "2026-09-15T00:15:00.000Z",
        "marketStatus": "OPEN",
        "inPlay": False,
        "runners": runners,
    }


def away_home(name1, name2, h1, h2, o1, o2):
    return [
        {
            "selectionId": 50211,
            "handicap": h1,
            "runnerName": name1,
            "result": {"type": "AWAY"},
            "runnerStatus": "ACTIVE",
            "winRunnerOdds": odds(o1),
        },
        {
            "selectionId": 50214,
            "handicap": h2,
            "runnerName": name2,
            "result": {"type": "HOME"},
            "runnerStatus": "ACTIVE",
            "winRunnerOdds": odds(o2),
        },
    ]


def observed_payload():
    return {
        "attachments": {
            "markets": {
                "734.168694353": market(
                    "734.168694353",
                    "MONEY_LINE",
                    "Moneyline",
                    away_home("Denver Broncos", "Kansas City Chiefs", 0, 0, 116, -136),
                ),
                "734.168694354": market(
                    "734.168694354",
                    "MATCH_HANDICAP_(2-WAY)",
                    "Spread",
                    away_home("Denver Broncos", "Kansas City Chiefs", 2.5, -2.5, -115, -105),
                ),
                "734.168694356": market(
                    "734.168694356",
                    "TOTAL_POINTS_(OVER/UNDER)",
                    "Total Points",
                    [
                        {
                            "selectionId": 7017916,
                            "handicap": 43.5,
                            "runnerName": "Over",
                            "result": {"type": "OVER"},
                            "runnerStatus": "ACTIVE",
                            "winRunnerOdds": odds(-102),
                        },
                        {
                            "selectionId": 7017917,
                            "handicap": 43.5,
                            "runnerName": "Under",
                            "result": {"type": "UNDER"},
                            "runnerStatus": "ACTIVE",
                            "winRunnerOdds": odds(-120),
                        },
                    ],
                ),
                "ignore": market(
                    "ignore", "PLAYER_X_PASSING_YARDS_HIGH", "Passing Yards", [],
                ),
            }
        }
    }


class GameMarketNormalizerTests(unittest.TestCase):
    def normalize(self, payload=None, **kwargs):
        return normalize_primary_game_markets(
            payload or observed_payload(),
            payload_sha256="a" * 64,
            observed_at="2026-09-14T22:57:20Z",
            **kwargs,
        )

    def test_observed_den_kc_fixture_normalizes_three_primary_markets(self):
        rows, failures = self.normalize(event_id="35601246")
        self.assertEqual(failures, [])
        self.assertEqual([r["canonical_market"] for r in rows], [
            "game_total", "moneyline", "spread"
        ])
        by_market = {r["canonical_market"]: r for r in rows}
        self.assertEqual(by_market["spread"]["away_handicap"], 2.5)
        self.assertEqual(by_market["spread"]["away_odds"], -115)
        self.assertEqual(by_market["spread"]["home_handicap"], -2.5)
        self.assertEqual(by_market["spread"]["home_odds"], -105)
        self.assertEqual(by_market["game_total"]["total"], 43.5)
        self.assertEqual(by_market["game_total"]["over_odds"], -102)
        self.assertEqual(by_market["game_total"]["under_odds"], -120)
        self.assertEqual(by_market["moneyline"]["away_odds"], 116)
        self.assertEqual(by_market["moneyline"]["home_odds"], -136)

    def test_provenance_fields_are_preserved(self):
        rows, _ = self.normalize(event_id="35601246")
        for row in rows:
            self.assertEqual(row["source_payload_sha256"], "a" * 64)
            self.assertEqual(row["captured_at"], "2026-09-14T22:57:20Z")
            self.assertEqual(row["sportsbook"], "FANDUEL")
            self.assertEqual(row["sport"], "NFL")

    def test_event_filter_excludes_other_games(self):
        rows, failures = self.normalize(event_id="not-this-event")
        self.assertEqual(rows, [])
        self.assertEqual(failures, [])

    def test_duplicate_primary_market_fails_closed(self):
        payload = observed_payload()
        clone = copy.deepcopy(payload["attachments"]["markets"]["734.168694354"])
        clone["marketId"] = "duplicate-spread"
        payload["attachments"]["markets"]["duplicate-spread"] = clone
        rows, failures = self.normalize(payload, event_id="35601246")
        self.assertNotIn("spread", {r["canonical_market"] for r in rows})
        self.assertIn("DUPLICATE_PRIMARY_MARKETS:2", {f["reason"] for f in failures})

    def test_non_opposite_spread_fails_closed(self):
        payload = observed_payload()
        payload["attachments"]["markets"]["734.168694354"]["runners"][1]["handicap"] = -3.0
        rows, failures = self.normalize(payload, event_id="35601246")
        self.assertNotIn("spread", {r["canonical_market"] for r in rows})
        self.assertTrue(any("opposites" in f["reason"] for f in failures))

    def test_disagreeing_total_lines_fail_closed(self):
        payload = observed_payload()
        payload["attachments"]["markets"]["734.168694356"]["runners"][1]["handicap"] = 44.5
        rows, failures = self.normalize(payload, event_id="35601246")
        self.assertNotIn("game_total", {r["canonical_market"] for r in rows})
        self.assertTrue(any("disagree" in f["reason"] for f in failures))

    def test_in_play_market_is_excluded(self):
        payload = observed_payload()
        payload["attachments"]["markets"]["734.168694354"]["inPlay"] = True
        rows, failures = self.normalize(payload, event_id="35601246")
        self.assertNotIn("spread", {r["canonical_market"] for r in rows})
        self.assertEqual(failures, [])

    def test_closed_market_is_excluded(self):
        payload = observed_payload()
        payload["attachments"]["markets"]["734.168694356"]["marketStatus"] = "CLOSED"
        rows, failures = self.normalize(payload, event_id="35601246")
        self.assertNotIn("game_total", {r["canonical_market"] for r in rows})
        self.assertEqual(failures, [])

    def test_missing_or_zero_odds_fail_closed(self):
        payload = observed_payload()
        payload["attachments"]["markets"]["734.168694353"]["runners"][0]["winRunnerOdds"] = {}
        rows, failures = self.normalize(payload, event_id="35601246")
        self.assertNotIn("moneyline", {r["canonical_market"] for r in rows})
        self.assertTrue(any("winRunnerOdds" in f["reason"] for f in failures))

    def test_unrecognized_market_type_is_ignored(self):
        payload = observed_payload()
        payload["attachments"]["markets"] = {
            "x": market("x", "ALTERNATE_TOTAL", "Alternate Total", [])
        }
        rows, failures = self.normalize(payload)
        self.assertEqual(rows, [])
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
