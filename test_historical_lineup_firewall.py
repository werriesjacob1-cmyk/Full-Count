#!/usr/bin/env python3
"""Historical lineup fallback firewall tests for canonical v2."""
from __future__ import annotations

import unittest

import mlb_daily as m


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def schedule_missing_lineups():
    return {
        "dates": [{
            "games": [{
                "gamePk": 123,
                "gameDate": "2024-06-01T19:00:00Z",
                "status": {"detailedState": "Final"},
                "venue": {"name": "Test Park"},
                "seriesGameNumber": 1,
                "gamesInSeries": 3,
                "teams": {
                    "away": {
                        "team": {"id": 1, "name": "Away"},
                        "leagueRecord": {"wins": 1, "losses": 1},
                    },
                    "home": {
                        "team": {"id": 2, "name": "Home"},
                        "leagueRecord": {"wins": 1, "losses": 1},
                    },
                },
                "lineups": {},
                "officials": [],
            }]
        }]
    }


class HistoricalFirewallTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "strict": m.HISTORICAL_REPLAY_STRICT_LINEUPS,
            "retry_get": m.retry_get,
            "mlbcom": m.fetch_mlb_dated_lineups_fallback,
            "rotowire": m.fetch_rotowire_lineups_by_team,
            "last_known": m.fetch_last_known_lineup,
            "get_team_ids": m.get_team_ids,
        }

    def tearDown(self):
        m.HISTORICAL_REPLAY_STRICT_LINEUPS = self.original["strict"]
        m.retry_get = self.original["retry_get"]
        m.fetch_mlb_dated_lineups_fallback = self.original["mlbcom"]
        m.fetch_rotowire_lineups_by_team = self.original["rotowire"]
        m.fetch_last_known_lineup = self.original["last_known"]
        m.get_team_ids = self.original["get_team_ids"]

    def test_strict_historical_mode_never_calls_current_rotowire_or_last_known(self):
        m.HISTORICAL_REPLAY_STRICT_LINEUPS = True
        m.retry_get = lambda *a, **k: FakeResponse(schedule_missing_lineups())
        m.fetch_mlb_dated_lineups_fallback = lambda date: {}

        def forbidden(*a, **k):
            raise AssertionError("current/assumed fallback was called")

        m.fetch_rotowire_lineups_by_team = forbidden
        m.fetch_last_known_lineup = forbidden
        m.get_team_ids = forbidden

        text, game_meta, player_ids = m.fetch_lineups("2024-06-01")

        self.assertEqual(len(game_meta), 1)
        self.assertEqual(game_meta[0]["away_lineup"], [])
        self.assertEqual(game_meta[0]["home_lineup"], [])
        self.assertEqual(player_ids, {})
        self.assertIn("canonical v2 fails closed", text)

    def test_default_live_behavior_remains_opt_in_false(self):
        self.assertFalse(self.original["strict"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
