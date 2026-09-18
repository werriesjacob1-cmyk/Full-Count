#!/usr/bin/env python3
"""Contracts for strict FanDuel NFL primary game-line normalization."""
import copy
import json
import unittest
from pathlib import Path

from nfl.normalize import fanduel_game_lines


def runner(side, name, line, odds, selection_id):
    return {
        "selectionId": selection_id,
        "runnerName": name,
        "runnerStatus": "ACTIVE",
        "handicap": line,
        "result": {"type": side},
        "winRunnerOdds": {
            "americanDisplayOdds": {"americanOddsInt": odds}
        },
    }


def payload(*, include_moneyline=True, include_spread=True, include_total=True):
    markets = {}
    if include_moneyline:
        markets["m"] = {
            "marketId": "734.moneyline",
            "eventId": 999,
            "marketName": "Moneyline",
            "marketType": "MONEY_LINE",
            "marketStatus": "OPEN",
            "inPlay": False,
            "marketTime": "2026-09-15T00:15:00.000Z",
            "runners": [
                runner("AWAY", "Denver Broncos", 0, 116, 11),
                runner("HOME", "Kansas City Chiefs", 0, -136, 12),
            ],
        }
    if include_spread:
        markets["s"] = {
            "marketId": "734.spread",
            "eventId": 999,
            "marketName": "Spread",
            "marketType": "MATCH_HANDICAP_(2-WAY)",
            "marketStatus": "OPEN",
            "inPlay": False,
            "marketTime": "2026-09-15T00:15:00.000Z",
            "runners": [
                runner("AWAY", "Denver Broncos", 2.5, -115, 1),
                runner("HOME", "Kansas City Chiefs", -2.5, -105, 2),
            ],
        }
    if include_total:
        markets["t"] = {
            "marketId": "734.total",
            "eventId": 999,
            "marketName": "Total Points",
            "marketType": "TOTAL_POINTS_(OVER/UNDER)",
            "marketStatus": "OPEN",
            "inPlay": False,
            "marketTime": "2026-09-15T00:15:00.000Z",
            "runners": [
                runner("OVER", "Over", 43.5, -102, 3),
                runner("UNDER", "Under", 43.5, -120, 4),
            ],
        }
    return {
        "attachments": {
            "events": {
                "999": {
                    "eventId": 999,
                    "name": "Denver Broncos @ Kansas City Chiefs",
                    "openDate": "2026-09-15T00:15:00.000Z",
                }
            },
            "markets": markets,
        }
    }


class FanDuelGameLineTests(unittest.TestCase):
    def test_primary_game_markets_normalize_with_provenance(self):
        result = fanduel_game_lines.normalize_payload(
            payload(),
            captured_at="2026-09-14T22:57:21-00:00",
            source_payload_sha256="a" * 64,
            source_artifact="event_35601246_passing-props.json",
            source_url="https://sportsbook.example/event/35601246",
        )
        self.assertEqual(result["stats"]["normalized"], 3)
        by_market = {row["market"]: row for row in result["candidates"]}
        spread = by_market["spread"]
        total = by_market["game_total"]
        moneyline = by_market["moneyline"]
        self.assertEqual(moneyline["away_team"], "Denver Broncos")
        self.assertEqual(moneyline["away_odds"], 116)
        self.assertEqual(moneyline["home_odds"], -136)
        self.assertEqual(spread["market"], "spread")
        self.assertEqual(spread["away_team"], "Denver Broncos")
        self.assertEqual(spread["away_line"], 2.5)
        self.assertEqual(spread["home_line"], -2.5)
        self.assertEqual(spread["away_odds"], -115)
        self.assertEqual(total["market"], "game_total")
        self.assertEqual(total["line"], 43.5)
        self.assertEqual(total["over_odds"], -102)
        self.assertEqual(total["under_odds"], -120)
        self.assertEqual(total["captured_at"], "2026-09-14T22:57:21Z")
        self.assertEqual(total["source_payload_sha256"], "a" * 64)

    def test_alternate_and_period_markets_are_ignored(self):
        p = payload(
            include_moneyline=False, include_spread=False, include_total=False
        )
        p["attachments"]["markets"] = {
            "a": {
                "marketName": "Alternate Spread",
                "marketType": "ALTERNATE_HANDICAP",
            },
            "h": {
                "marketName": "1st Half Total",
                "marketType": "FIRST_HALF_TOTAL",
            },
        }
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["stats"]["ignored_non_primary"], 2)

    def test_primary_name_with_unknown_type_is_rejected(self):
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["marketType"] = "NEW_SPREAD"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "UNSUPPORTED_PRIMARY_TYPE")

    def test_primary_type_with_wrong_name_is_rejected(self):
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["marketName"] = "Alternate Spread"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "UNSUPPORTED_PRIMARY_NAME")

    def test_missing_in_play_state_fails_closed(self):
        p = payload(include_total=False)
        del p["attachments"]["markets"]["s"]["inPlay"]
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "IN_PLAY_OR_UNKNOWN")

    def test_closed_market_is_rejected(self):
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["marketStatus"] = "SUSPENDED"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "MARKET_NOT_OPEN")

    def test_spread_teams_must_match_event_sides(self):
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["runners"][0][
            "runnerName"
        ] = "Kansas City Chiefs"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "TEAM_IDENTITY_MISMATCH")

    def test_moneyline_teams_ids_and_odds_fail_closed(self):
        p = payload(include_spread=False, include_total=False)
        p["attachments"]["markets"]["m"]["runners"][0]["runnerName"] = "Wrong Team"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "TEAM_IDENTITY_MISMATCH")

        p = payload(include_spread=False, include_total=False)
        p["attachments"]["markets"]["m"]["runners"][1]["selectionId"] = 11
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "INVALID_SELECTION_ID")

        p = payload(include_spread=False, include_total=False)
        p["attachments"]["markets"]["m"]["runners"][0]["winRunnerOdds"] = {}
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "MISSING_ODDS")

    def test_spread_handicaps_must_be_opposites(self):
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["runners"][1]["handicap"] = -3.0
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "SPREAD_NOT_OPPOSING")

    def test_pickem_spread_is_valid(self):
        p = payload(include_moneyline=False, include_total=False)
        for row in p["attachments"]["markets"]["s"]["runners"]:
            row["handicap"] = 0
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["candidates"][0]["away_line"], 0.0)

    def test_total_lines_must_match(self):
        p = payload(include_spread=False)
        p["attachments"]["markets"]["t"]["runners"][1]["handicap"] = 44.5
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "LINE_MISMATCH")

    def test_total_side_names_must_match_results(self):
        p = payload(include_spread=False)
        p["attachments"]["markets"]["t"]["runners"][0][
            "runnerName"
        ] = "Under"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(
            result["rejections"][0]["reason"],
            "TOTAL_SIDE_IDENTITY_MISMATCH",
        )

    def test_missing_side_and_missing_odds_are_rejected(self):
        p = payload(include_spread=False)
        p["attachments"]["markets"]["t"]["runners"].pop()
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "SIDE_CARDINALITY")
        p = payload(include_spread=False)
        p["attachments"]["markets"]["t"]["runners"].append(
            runner("PUSH", "Push", 43.5, 100, 5)
        )
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "SIDE_CARDINALITY")
        p = payload(include_spread=False)
        del p["attachments"]["markets"]["t"]["runners"][1]["winRunnerOdds"]
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "MISSING_ODDS")

    def test_unresolved_non_game_event_is_rejected(self):
        p = payload(include_total=False)
        p["attachments"]["events"]["999"]["name"] = "NFL Season Specials"
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "UNRESOLVED_GAME")

    def test_market_time_must_match_event_kickoff(self):
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["marketTime"] = (
            "2026-09-15T00:20:00.000Z"
        )
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "EVENT_TIME_MISMATCH")

    def test_selection_ids_must_be_present_and_distinct(self):
        p = payload(include_total=False)
        del p["attachments"]["markets"]["s"]["runners"][0]["selectionId"]
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "INVALID_SELECTION_ID")
        p = payload(include_total=False)
        p["attachments"]["markets"]["s"]["runners"][1]["selectionId"] = 1
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["rejections"][0]["reason"], "INVALID_SELECTION_ID")

    def test_identical_duplicate_market_id_collapses(self):
        p = payload(include_moneyline=False, include_total=False)
        p["attachments"]["markets"]["copy"] = copy.deepcopy(
            p["attachments"]["markets"]["s"]
        )
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["stats"]["duplicates_collapsed"], 1)

    def test_conflicting_duplicate_market_id_rejects_all_versions(self):
        p = payload(include_moneyline=False, include_total=False)
        duplicate = copy.deepcopy(p["attachments"]["markets"]["s"])
        duplicate["runners"][0]["handicap"] = 3.5
        duplicate["runners"][1]["handicap"] = -3.5
        p["attachments"]["markets"]["copy"] = duplicate
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejections"][0]["reason"], "DUPLICATE_MARKET_CONFLICT")

    def test_distinct_primary_market_ids_for_same_family_reject_all(self):
        p = payload(include_moneyline=False, include_total=False)
        duplicate = copy.deepcopy(p["attachments"]["markets"]["s"])
        duplicate["marketId"] = "734.other-spread"
        p["attachments"]["markets"]["copy"] = duplicate
        result = fanduel_game_lines.normalize_payload(p)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            {row["reason"] for row in result["rejections"]},
            {"DUPLICATE_PRIMARY_MARKET"},
        )

    def test_malformed_digest_and_naive_capture_time_fail(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            fanduel_game_lines.normalize_payload(
                payload(), source_payload_sha256="BAD"
            )
        with self.assertRaisesRegex(ValueError, "timezone"):
            fanduel_game_lines.normalize_payload(
                payload(), captured_at="2026-09-14T22:57:21"
            )


class GameLineCoverageClassificationTests(unittest.TestCase):
    def test_registry_classification_stops_at_normalized(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "market_coverage"
            / "classifications"
            / "nfl_fanduel.json"
        )
        classifications = json.loads(path.read_text(encoding="utf-8"))
        for market_type in (
            fanduel_game_lines.MONEYLINE_TYPE,
            fanduel_game_lines.SPREAD_TYPE,
            fanduel_game_lines.TOTAL_TYPE,
        ):
            with self.subTest(market_type=market_type):
                row = classifications[market_type]
                self.assertEqual(row["lifecycle_status"], "NORMALIZED")
                capabilities = row["capabilities"]
                self.assertTrue(capabilities["ingested"])
                self.assertTrue(capabilities["normalized"])
                for name, enabled in capabilities.items():
                    if name not in {"ingested", "normalized"}:
                        self.assertFalse(enabled, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
