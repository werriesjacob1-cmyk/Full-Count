#!/usr/bin/env python3
"""Parsing/join contracts for the role-intelligence data-prep layer."""
import unittest

from nfl.research.role_intelligence_data_prep import (
    RoleIntelligenceDataPrepError,
    build_player_game_usage_rows,
    fetch_and_verify,
    fetch_depth_chart_rows,
    parse_depth_chart_csv,
    parse_players_crosswalk_csv,
    parse_snap_counts_csv,
    parse_weekly_stats_csv,
)
from nfl.research.role_intelligence_source_digests import DEPTH_CHART_SCHEMA_BREAK_SEASONS


class PlayersCrosswalkTests(unittest.TestCase):
    def test_maps_pfr_id_to_gsis_id(self):
        text = "gsis_id,pfr_id\n00-0028830,AaitIs00\n"
        self.assertEqual(parse_players_crosswalk_csv(text), {"AaitIs00": "00-0028830"})

    def test_rows_missing_either_id_are_skipped(self):
        text = "gsis_id,pfr_id\n,AaitIs00\n00-0028830,\n"
        self.assertEqual(parse_players_crosswalk_csv(text), {})

    def test_ambiguous_pfr_id_fails_closed(self):
        text = "gsis_id,pfr_id\n00-0028830,X\n00-0099999,X\n"
        with self.assertRaisesRegex(RoleIntelligenceDataPrepError, "ambiguous pfr_id"):
            parse_players_crosswalk_csv(text)

    def test_missing_columns_fail_closed(self):
        with self.assertRaisesRegex(RoleIntelligenceDataPrepError, "missing"):
            parse_players_crosswalk_csv("a,b\n1,2\n")


class WeeklyStatsParseTests(unittest.TestCase):
    HEADER = (
        "player_id,player_display_name,position,season,week,season_type,"
        "team,opponent_team,targets,carries\n"
    )

    def test_reg_only_and_wr_rb_and_other_positions_all_kept(self):
        text = (
            self.HEADER
            + "00-01,WR One,WR,2020,1,REG,SF,ARI,5,0\n"
            + "00-02,RB One,RB,2020,1,REG,SF,ARI,1,10\n"
            + "00-03,Post Guy,WR,2020,1,POST,SF,ARI,9,0\n"
            + "00-04,TE One,TE,2020,1,REG,SF,ARI,3,0\n"
        )
        rows = parse_weekly_stats_csv(text, 2020)
        self.assertEqual({r["player_id"] for r in rows}, {"00-01", "00-02", "00-04"})

    def test_blank_player_id_row_is_skipped(self):
        text = self.HEADER + ",Nobody,,2020,1,REG,SF,ARI,0,0\n"
        self.assertEqual(parse_weekly_stats_csv(text, 2020), [])

    def test_missing_columns_fail_closed(self):
        with self.assertRaisesRegex(RoleIntelligenceDataPrepError, "missing required columns"):
            parse_weekly_stats_csv("a,b\n1,2\n", 2020)


class SnapCountsParseTests(unittest.TestCase):
    HEADER = "game_id,season,game_type,week,pfr_player_id,position,team,opponent,offense_snaps\n"

    def test_joins_via_crosswalk(self):
        text = self.HEADER + "2020_01_ARI_SF,2020,REG,1,AaitIs00,WR,SF,ARI,60\n"
        rows = parse_snap_counts_csv(text, 2020, {"AaitIs00": "00-0028830"})
        self.assertEqual(rows[0]["player_id"], "00-0028830")

    def test_unmapped_pfr_id_is_quarantined_not_dropped(self):
        text = self.HEADER + "2020_01_ARI_SF,2020,REG,1,UnknownX00,WR,SF,ARI,60\n"
        rows = parse_snap_counts_csv(text, 2020, {})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["player_id"])

    def test_empty_header_only_file_is_a_real_disclosed_gap(self):
        # The real nflverse 2012 snap_counts asset is header-only.
        self.assertEqual(parse_snap_counts_csv("", 2012, {}), [])

    def test_post_season_rows_excluded(self):
        text = self.HEADER + "2020_21_ARI_SF,2020,POST,21,AaitIs00,WR,SF,ARI,60\n"
        self.assertEqual(parse_snap_counts_csv(text, 2020, {"AaitIs00": "00-1"}), [])


class DepthChartParseTests(unittest.TestCase):
    HEADER = (
        "season,club_code,week,game_type,depth_team,last_name,first_name,football_name,"
        "formation,gsis_id,jersey_number,position,elias_id,depth_position,full_name\n"
    )

    def test_filters_to_wr_rb_and_reg(self):
        text = (
            self.HEADER
            + "2020,SF,1,REG,1,X,Y,Y,Offense,00-01,1,WR,Z,WR,Test WR\n"
            + "2020,SF,1,REG,1,X,Y,Y,Offense,00-02,1,QB,Z,QB,Test QB\n"
            + "2020,SF,1,POST,1,X,Y,Y,Offense,00-03,1,RB,Z,RB,Test RB\n"
        )
        rows = parse_depth_chart_csv(text, 2020)
        self.assertEqual([r["player_id"] for r in rows], ["00-01"])

    def test_2025_schema_break_is_refused_not_coerced(self):
        self.assertIn(2025, DEPTH_CHART_SCHEMA_BREAK_SEASONS)
        with self.assertRaisesRegex(RoleIntelligenceDataPrepError, "post-break"):
            fetch_depth_chart_rows(2025)


class FetchAndVerifyDigestTests(unittest.TestCase):
    def test_digest_mismatch_fails_closed_without_retrying_forever(self):
        from unittest import mock

        from nfl.research.role_intelligence_data_prep import fetch_and_verify

        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"not the expected bytes"
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            with self.assertRaisesRegex(RoleIntelligenceDataPrepError, "asset digest drift"):
                fetch_and_verify(
                    "https://example.invalid/asset.csv",
                    {"bytes": 999, "sha256": "0" * 64},
                    tries=1,
                )

    def test_sha256_bytes_matches_hashlib(self):
        import hashlib

        from nfl.research.role_intelligence_data_prep import sha256_bytes

        data = b"hello world"
        self.assertEqual(sha256_bytes(data), hashlib.sha256(data).hexdigest())


class MergeUsageRowsTests(unittest.TestCase):
    def test_merges_all_sources_by_stable_id_and_filters_to_wr_rb(self):
        weekly_rows = [
            {"player_id": "00-01", "player_display_name": "WR1", "position": "WR",
             "season": 2020, "week": 1, "team": "SF", "opponent_team": "ARI",
             "targets": 5.0, "carries": 0.0},
            {"player_id": "00-02", "player_display_name": "QB1", "position": "QB",
             "season": 2020, "week": 1, "team": "SF", "opponent_team": "ARI",
             "targets": 0.0, "carries": 1.0},
        ]
        snap_rows = [
            {"game_id": "2020_01_ARI_SF", "season": 2020, "week": 1, "pfr_player_id": "X",
             "player_id": "00-01", "position": "WR", "team": "SF", "opponent": "ARI",
             "offense_snaps": 40.0},
            {"game_id": "2020_01_ARI_SF", "season": 2020, "week": 1, "pfr_player_id": "Y",
             "player_id": "00-02", "position": "QB", "team": "SF", "opponent": "ARI",
             "offense_snaps": 65.0},
        ]
        depth_rows = [
            {"season": 2020, "week": 1, "team": "SF", "player_id": "00-01", "position": "WR", "depth_team": 1},
        ]
        injury_rows = []
        pbp_player_rows = [
            {"season": 2020, "week": 1, "game_id": "2020_01_ARI_SF", "team": "SF", "player_id": "00-01",
             "red_zone_targets": 1.0, "red_zone_carries": 0.0, "goal_line_carries": 0.0,
             "third_down_targets": 1.0, "third_down_carries": 0.0, "two_minute_targets": 0.0, "two_minute_carries": 0.0},
        ]
        pbp_team_totals = {
            ("2020_01_ARI_SF", "SF"): {
                "team_red_zone_opportunities": 4.0, "team_goal_line_carries": 1.0,
                "team_third_down_opportunities": 3.0, "team_two_minute_opportunities": 2.0,
            }
        }
        merged = build_player_game_usage_rows(
            weekly_rows, snap_rows, depth_rows, injury_rows, pbp_player_rows, pbp_team_totals
        )
        self.assertEqual(len(merged), 1)
        row = merged[0]
        self.assertEqual(row["player_id"], "00-01")
        # QB's 65 snaps become the team_offense_snaps denominator (max convention).
        self.assertEqual(row["team_offense_snaps"], 65.0)
        self.assertEqual(row["offense_snaps"], 40.0)
        self.assertEqual(row["depth_team"], 1)
        self.assertEqual(row["team_red_zone_opportunities"], 4.0)

    def test_player_with_no_snap_counts_row_is_none_not_zero(self):
        weekly_rows = [
            {"player_id": "00-01", "player_display_name": "WR1", "position": "WR",
             "season": 2012, "week": 1, "team": "SF", "opponent_team": "ARI",
             "targets": 5.0, "carries": 0.0},
        ]
        merged = build_player_game_usage_rows(weekly_rows, [], [], [], [], {})
        self.assertIsNone(merged[0]["offense_snaps"])
        self.assertIsNone(merged[0]["team_offense_snaps"])


if __name__ == "__main__":
    unittest.main()
