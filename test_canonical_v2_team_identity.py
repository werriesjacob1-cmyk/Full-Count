#!/usr/bin/env python3
"""Tests for canonical-v2 historical team identity isolation."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import backtest.engine as engine
import generate_picks as gp
import mlb_daily as m
from backtest.canonical_v2_team_identity import (
    HistoricalTeamIdentity,
    HistoricalTeamIdentityError,
)


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def season_teams(year):
    rows = []
    for team_id in range(101, 131):
        rows.append({
            "id": team_id,
            "name": f"Team {team_id}",
            "abbreviation": f"T{team_id}",
        })
    # Replace one stable franchise identity with the real rename shape this
    # test is designed to protect.
    rows[0] = {
        "id": 133,
        "name": "Oakland Athletics" if int(year) == 2024 else "Athletics",
        "abbreviation": "OAK" if int(year) == 2024 else "ATH",
    }
    return rows


class HistoricalTeamIdentityTests(unittest.TestCase):
    def setUp(self):
        self.saved_year = m.YEAR
        self.saved_engine_cache = engine._ABBR_BY_NAME

    def tearDown(self):
        m.YEAR = self.saved_year
        engine._ABBR_BY_NAME = self.saved_engine_cache

    def fake_retry_get(self, url, params=None, **kwargs):
        self.assertEqual(url, "https://statsapi.mlb.com/api/v1/teams")
        self.assertEqual(params.get("sportId"), 1)
        year = int(params["season"])
        return FakeResponse({"teams": season_teams(year)})

    def test_team_directory_is_explicitly_season_addressed(self):
        adapter = HistoricalTeamIdentity()
        with patch.object(m, "retry_get", side_effect=self.fake_retry_get):
            teams_2024 = adapter.get_team_ids_for_season(2024)
            teams_2025 = adapter.get_team_ids_for_season(2025)

        oakland = next(row for row in teams_2024 if row["id"] == 133)
        athletics = next(row for row in teams_2025 if row["id"] == 133)
        self.assertEqual(oakland["name"], "Oakland Athletics")
        self.assertEqual(oakland["abbr"], "OAK")
        self.assertEqual(athletics["name"], "Athletics")
        self.assertEqual(athletics["abbr"], "ATH")

    def test_prepare_date_resets_engine_name_cache_across_seasons(self):
        adapter = HistoricalTeamIdentity()
        engine._ABBR_BY_NAME = {"Oakland Athletics": "OAK"}
        with patch.object(m, "retry_get", side_effect=self.fake_retry_get):
            adapter.prepare_date("2025-05-01")
        self.assertIsNone(engine._ABBR_BY_NAME)

    def test_installed_get_team_ids_tracks_simulated_year_not_current_year(self):
        adapter = HistoricalTeamIdentity()
        original = m.get_team_ids
        try:
            with patch.object(m, "retry_get", side_effect=self.fake_retry_get):
                adapter.install()
                m.YEAR = 2024
                t2024 = next(row for row in m.get_team_ids() if row["id"] == 133)
                m.YEAR = 2025
                t2025 = next(row for row in m.get_team_ids() if row["id"] == 133)
            self.assertEqual(t2024["name"], "Oakland Athletics")
            self.assertEqual(t2025["name"], "Athletics")
        finally:
            adapter.uninstall()
        self.assertIs(m.get_team_ids, original)

    def test_bullpen_resolution_uses_schedule_team_ids_never_lookup_team(self):
        adapter = HistoricalTeamIdentity()
        m.YEAR = 2024

        game_meta = [{
            "game_pk": 123,
            "away_team": "Oakland Athletics",
            "away_team_id": 133,
            "home_team": "Team 102",
            "home_team_id": 102,
            "game_start_utc": "2024-05-01T23:05:00Z",
        }]

        usage = {
            "Reliever One": {
                "IP": 2.0,
                "apps": 2,
                "pitches": 65,
                "games": [{"date": "2024-04-30", "ip": 1.0, "pitches": 35}],
            }
        }

        def fake_fetch(job, is_rotation_starter=None):
            team_name, team_id = job
            return team_name, usage, None

        with patch.object(m, "retry_get", side_effect=self.fake_retry_get), \
             patch.object(m, "_bullpen_fetch_one", side_effect=fake_fetch), \
             patch.object(gp, "_bullpen_role_classifier", return_value=None), \
             patch.object(gp, "_reliever_detail", return_value=[{"name": "Reliever One"}]), \
             patch.object(m.statsapi, "lookup_team",
                          side_effect=AssertionError("lookup_team must not be called")):
            result = adapter.fetch_bullpen_scores(game_meta, pit_season_df=None)

        self.assertIn("Oakland Athletics", result)
        self.assertEqual(
            result["Oakland Athletics"]["fatigued_relievers"],
            1,
        )
        self.assertIn("Team 102", result)

    def test_missing_schedule_team_id_fails_closed(self):
        adapter = HistoricalTeamIdentity()
        m.YEAR = 2024
        game_meta = [{
            "game_pk": 123,
            "away_team": "Oakland Athletics",
            "away_team_id": None,
            "home_team": "Team 102",
            "home_team_id": 102,
            "game_start_utc": "2024-05-01T23:05:00Z",
        }]
        with patch.object(m, "retry_get", side_effect=self.fake_retry_get):
            with self.assertRaises(HistoricalTeamIdentityError):
                adapter.fetch_bullpen_scores(game_meta)

    def test_team_id_absent_from_historical_season_directory_fails_closed(self):
        adapter = HistoricalTeamIdentity()
        m.YEAR = 2024
        game_meta = [{
            "game_pk": 123,
            "away_team": "Unknown Historical Club",
            "away_team_id": 999,
            "home_team": "Team 102",
            "home_team_id": 102,
            "game_start_utc": "2024-05-01T23:05:00Z",
        }]
        with patch.object(m, "retry_get", side_effect=self.fake_retry_get):
            with self.assertRaises(HistoricalTeamIdentityError):
                adapter.fetch_bullpen_scores(game_meta)


    def test_postponed_game_never_reaches_bullpen_boxscore_schedule(self):
        adapter = HistoricalTeamIdentity()
        m.YEAR = 2025
        game_meta = [{
            "game_pk": 9000,
            "away_team": "Team 102",
            "away_team_id": 102,
            "home_team": "Team 103",
            "home_team_id": 103,
            "game_start_utc": "2025-08-19T23:05:00Z",
        }]

        schedule_payload = {
            "dates": [{
                "date": "2025-08-18",
                "games": [
                    {
                        "gamePk": 7001,
                        "officialDate": "2025-08-18",
                        "gameDate": "2025-08-18T18:00:00Z",
                        "gameNumber": 1,
                        "status": {
                            "codedGameState": "F",
                            "statusCode": "F",
                            "detailedState": "Final",
                        },
                    },
                    {
                        # Exact failure shape observed in the real pilot:
                        # the bounded D-1 date block retained a postponed
                        # gamePk whose current official date moved to D.
                        "gamePk": 7002,
                        "officialDate": "2025-08-19",
                        "gameDate": "2025-08-19T00:05:00Z",
                        "gameNumber": 1,
                        "status": {
                            "codedGameState": "D",
                            "statusCode": "DR",
                            "detailedState": "Postponed",
                        },
                    },
                    {
                        # Even an old officialDate is insufficient if the
                        # bounded schedule says the game was not complete.
                        "gamePk": 7003,
                        "officialDate": "2025-08-18",
                        "gameDate": "2025-08-18T20:00:00Z",
                        "gameNumber": 1,
                        "status": {
                            "codedGameState": "S",
                            "statusCode": "S",
                            "detailedState": "Suspended",
                        },
                    },
                ],
            }],
        }

        def retry_get(url, params=None, **kwargs):
            if url.endswith("/api/v1/teams"):
                return FakeResponse({"teams": season_teams(2025)})
            if url.endswith("/api/v1/schedule"):
                self.assertEqual(params["endDate"], "2025-08-18")
                self.assertEqual(params["startDate"], "2025-08-11")
                return FakeResponse(schedule_payload)
            raise AssertionError(f"unexpected URL {url}")

        observed_game_ids = []

        def fake_fetch(job, is_rotation_starter=None):
            team_name, team_id = job
            games = m.statsapi.schedule(
                start_date="2025-08-11",
                end_date="2025-08-18",
                team=team_id,
            )
            observed_game_ids.extend(game["game_id"] for game in games)
            return team_name, {}, None

        with patch.object(m, "retry_get", side_effect=retry_get), \
             patch.object(m, "_bullpen_fetch_one", side_effect=fake_fetch), \
             patch.object(gp, "_bullpen_role_classifier", return_value=None):
            adapter.prepare_date("2025-08-19")
            adapter.fetch_bullpen_scores(game_meta, pit_season_df=None)

        self.assertTrue(observed_game_ids)
        self.assertEqual(set(observed_game_ids), {7001})
        self.assertNotIn(7002, observed_game_ids)
        self.assertNotIn(7003, observed_game_ids)


    def test_scheduled_start_timecode_is_one_second_before_first_pitch(self):
        self.assertEqual(
            HistoricalTeamIdentity._scheduled_start_timecode(
                "2025-08-19T23:05:00Z"
            ),
            "20250819_230459",
        )
        self.assertEqual(
            HistoricalTeamIdentity._scheduled_start_timecode(
                "2025-08-19T19:05:00-04:00"
            ),
            "20250819_230459",
        )

    def test_missing_game_start_fails_closed_for_bullpen_evidence(self):
        adapter = HistoricalTeamIdentity()
        m.YEAR = 2025
        game_meta = [{
            "game_pk": 9000,
            "away_team": "Team 102",
            "away_team_id": 102,
            "home_team": "Team 103",
            "home_team_id": 103,
        }]
        with patch.object(m, "retry_get", side_effect=self.fake_retry_get):
            with self.assertRaises(HistoricalTeamIdentityError):
                adapter.fetch_bullpen_scores(game_meta)

    def test_bullpen_boxscores_are_timebounded_per_team_thread(self):
        observed = []

        def fake_boxscore(game_pk, *args, **kwargs):
            observed.append((int(game_pk), kwargs.get("timecode")))
            return {}

        with patch.object(m.statsapi, "boxscore_data", side_effect=fake_boxscore):
            adapter = HistoricalTeamIdentity()
            m.YEAR = 2025
            game_meta = [
                {
                    "game_pk": 9101,
                    "away_team": "Team 102",
                    "away_team_id": 102,
                    "home_team": "Team 103",
                    "home_team_id": 103,
                    "game_start_utc": "2025-08-19T17:10:00Z",
                },
                {
                    "game_pk": 9102,
                    "away_team": "Team 104",
                    "away_team_id": 104,
                    "home_team": "Team 105",
                    "home_team_id": 105,
                    "game_start_utc": "2025-08-19T23:40:00Z",
                },
            ]

            def fake_fetch(job, is_rotation_starter=None):
                team_name, team_id = job
                # Exercise the adapter's globally-patched boxscore_data from
                # the same worker thread that owns this team.
                m.statsapi.boxscore_data(800000 + team_id)
                return team_name, {}, None

            with patch.object(
                m, "retry_get", side_effect=self.fake_retry_get
            ), patch.object(
                m, "_bullpen_fetch_one", side_effect=fake_fetch
            ), patch.object(
                gp, "_bullpen_role_classifier", return_value=None
            ):
                adapter.fetch_bullpen_scores(game_meta, pit_season_df=None)

        by_team_id = {
            game_pk - 800000: timecode
            for game_pk, timecode in observed
        }
        self.assertEqual(
            by_team_id,
            {
                102: "20250819_170959",
                103: "20250819_170959",
                104: "20250819_233959",
                105: "20250819_233959",
            },
        )
        self.assertIsNotNone(m.statsapi.boxscore_data)

    def test_doubleheader_uses_earliest_team_first_pitch_cutoff(self):
        observed = {}

        def fake_boxscore(game_pk, *args, **kwargs):
            observed[int(game_pk) - 800000] = kwargs.get("timecode")
            return {}

        with patch.object(m.statsapi, "boxscore_data", side_effect=fake_boxscore):
            adapter = HistoricalTeamIdentity()
            m.YEAR = 2025
            game_meta = [
                {
                    "game_pk": 9201,
                    "away_team": "Team 102",
                    "away_team_id": 102,
                    "home_team": "Team 103",
                    "home_team_id": 103,
                    "game_start_utc": "2025-08-19T17:00:00Z",
                },
                {
                    "game_pk": 9202,
                    "away_team": "Team 102",
                    "away_team_id": 102,
                    "home_team": "Team 104",
                    "home_team_id": 104,
                    "game_start_utc": "2025-08-19T23:00:00Z",
                },
            ]

            def fake_fetch(job, is_rotation_starter=None):
                team_name, team_id = job
                m.statsapi.boxscore_data(800000 + team_id)
                return team_name, {}, None

            with patch.object(
                m, "retry_get", side_effect=self.fake_retry_get
            ), patch.object(
                m, "_bullpen_fetch_one", side_effect=fake_fetch
            ), patch.object(
                gp, "_bullpen_role_classifier", return_value=None
            ):
                adapter.fetch_bullpen_scores(game_meta, pit_season_df=None)

        self.assertEqual(observed[102], "20250819_165959")


if __name__ == "__main__":
    unittest.main(verbosity=2)
