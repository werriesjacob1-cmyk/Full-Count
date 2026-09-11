#!/usr/bin/env python3
"""Strict offline contracts for official-inactive -> roster identity binding."""
import unittest

from nfl.normalize import inactive_roster_binding as binding


ROSTER = [
    {
        "season": "2026", "team": "NE", "position": "RB",
        "depth_chart_position": "RB", "full_name": "TreVeyon Henderson",
        "gsis_id": "00-0040734", "esb_id": "HEN144466",
    },
    {
        "season": "2026", "team": "NE", "position": "QB",
        "depth_chart_position": "QB", "full_name": "Behren Morton",
        "gsis_id": "00-0041123", "esb_id": "MOR767384",
    },
    {
        "season": "2026", "team": "SEA", "position": "DB",
        "depth_chart_position": "SS", "full_name": "Nick Emmanwori",
        "gsis_id": "00-0040733", "esb_id": "EMM083509",
    },
    {
        "season": "2026", "team": "SEA", "position": "QB",
        "depth_chart_position": "QB", "full_name": "Jalen Milroe",
        "gsis_id": "00-0040673", "esb_id": "MIL777073",
    },
]


def player(name, position):
    return {
        "player_name": name,
        "listed_position": position,
        "source_player_href": f"/players/{name.lower().replace(' ', '-')}",
        "source_player_slug": name.lower().replace(" ", "-"),
        "listed_inactive": True,
        "note": None,
        "emergency_third_qb": False,
    }


class InactiveRosterBindingTests(unittest.TestCase):
    def test_real_report_targets_bind_to_expected_gsis_ids(self):
        cases = [
            ("PATRIOTS", "TreVeyon Henderson", "RB", "00-0040734"),
            ("PATRIOTS", "Behren Morton", "QB", "00-0041123"),
            ("SEAHAWKS", "Nick Emmanwori", "S", "00-0040733"),
            ("SEAHAWKS", "Jalen Milroe", "QB", "00-0040673"),
        ]
        for team_label, name, pos, expected in cases:
            with self.subTest(name=name):
                result = binding.bind_player(
                    team_label, player(name, pos), ROSTER, season=2026
                )
                self.assertEqual(result["binding_status"], "BOUND")
                self.assertEqual(result["gsis_id"], expected)
                self.assertEqual(result["binding_method"],
                                 "exact_name+team+position_group")

    def test_team_nickname_mapping_covers_all_32_clubs_once(self):
        nicknames = {
            "CARDINALS": "ARI", "FALCONS": "ATL", "RAVENS": "BAL",
            "BILLS": "BUF", "PANTHERS": "CAR", "BEARS": "CHI",
            "BENGALS": "CIN", "BROWNS": "CLE", "COWBOYS": "DAL",
            "BRONCOS": "DEN", "LIONS": "DET", "PACKERS": "GB",
            "TEXANS": "HOU", "COLTS": "IND", "JAGUARS": "JAX",
            "CHIEFS": "KC", "RAIDERS": "LV", "CHARGERS": "LAC",
            "RAMS": "LAR", "DOLPHINS": "MIA", "VIKINGS": "MIN",
            "PATRIOTS": "NE", "SAINTS": "NO", "GIANTS": "NYG",
            "JETS": "NYJ", "EAGLES": "PHI", "STEELERS": "PIT",
            "49ERS": "SF", "SEAHAWKS": "SEA", "BUCCANEERS": "TB",
            "TITANS": "TEN", "COMMANDERS": "WAS",
        }
        self.assertEqual(len(nicknames), 32)
        self.assertEqual(len(set(nicknames.values())), 32)
        for label, expected in nicknames.items():
            self.assertEqual(binding.team_abbr(label), expected)

    def test_unknown_team_never_falls_back_to_name_only(self):
        result = binding.bind_player(
            "MYSTERY TEAM", player("TreVeyon Henderson", "RB"),
            ROSTER, season=2026
        )
        self.assertEqual(result["binding_status"], "UNRESOLVED_TEAM")
        self.assertIsNone(result["gsis_id"])

    def test_duplicate_exact_candidates_are_ambiguous_not_auto_bound(self):
        roster = ROSTER + [{**ROSTER[0], "esb_id": "OTHER"}]
        result = binding.bind_player(
            "PATRIOTS", player("TreVeyon Henderson", "RB"),
            roster, season=2026
        )
        self.assertEqual(result["binding_status"], "AMBIGUOUS_PLAYER")
        self.assertIsNone(result["gsis_id"])

    def test_position_mismatch_fails_closed(self):
        result = binding.bind_player(
            "PATRIOTS", player("TreVeyon Henderson", "QB"),
            ROSTER, season=2026
        )
        self.assertEqual(result["binding_status"], "POSITION_MISMATCH")
        self.assertIsNone(result["gsis_id"])

    def test_missing_durable_id_fails_closed(self):
        roster = [{**ROSTER[0], "gsis_id": ""}]
        result = binding.bind_player(
            "PATRIOTS", player("TreVeyon Henderson", "RB"),
            roster, season=2026
        )
        self.assertEqual(result["binding_status"], "MISSING_DURABLE_ID")
        self.assertIsNone(result["gsis_id"])

    def test_no_name_match_is_explicitly_unresolved(self):
        result = binding.bind_player(
            "PATRIOTS", player("Nobody Here", "RB"),
            ROSTER, season=2026
        )
        self.assertEqual(result["binding_status"], "UNRESOLVED_PLAYER")
        self.assertIsNone(result["gsis_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
