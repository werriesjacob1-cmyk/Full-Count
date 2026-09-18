#!/usr/bin/env python3
"""Point-in-time-safety and coverage-gap contracts for starter availability."""
import unittest

from nfl.research.injury_availability_features import (
    EARLIEST_COVERED_SEASON,
    InjuryAvailabilityError,
    build_prior_starter_availability_features,
    injury_report_url,
    season_is_covered,
)
from nfl.research.qb_continuity_features import build_prior_qb_continuity_features


def qb_row(player_id, team, opponent, season, week, attempts):
    return {
        "player_id": player_id,
        "position": "QB",
        "season": season,
        "week": week,
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "attempts": attempts,
    }


def injury_row(season, team, week, gsis_id, report_status, *, game_type="REG"):
    return {
        "season": season,
        "game_type": game_type,
        "team": team,
        "week": week,
        "gsis_id": gsis_id,
        "position": "QB",
        "report_status": report_status,
    }


class UrlAndCoverageTests(unittest.TestCase):
    def test_url_template(self):
        self.assertEqual(
            injury_report_url(2023),
            "https://github.com/nflverse/nflverse-data/releases/download/"
            "injuries/injuries_2023.csv",
        )

    def test_earliest_covered_season_is_2009(self):
        # nflreadr::load_injuries() enforces seasons >= 2009, and this was
        # independently confirmed live on 2026-09-18: injuries_2009.csv is
        # HTTP 200, injuries_2008.csv is HTTP 404.
        self.assertEqual(EARLIEST_COVERED_SEASON, 2009)
        self.assertTrue(season_is_covered(2009))
        self.assertFalse(season_is_covered(2008))


class RealDataShapeTests(unittest.TestCase):
    """Fixture rows transcribed verbatim from the real nflverse
    injuries_2023.csv release asset (fetched 2026-09-18), proving the parser
    handles the actual source shape, not just synthetic rows."""

    def test_real_bryce_young_out_row_is_recognized_as_game_affecting(self):
        real_injury_row = {
            "season": 2023,
            "game_type": "REG",
            "team": "CAR",
            "week": 3,
            "gsis_id": "00-0039150",
            "position": "QB",
            "report_status": "Out",
        }
        qb_rows = [
            qb_row("00-0039150", "CAR", "SEA", 2023, 1, 30),
            qb_row("00-0039150", "CAR", "NO", 2023, 2, 28),
            qb_row("BACKUP", "CAR", "MIN", 2023, 3, 15),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        out = build_prior_starter_availability_features([real_injury_row], continuity)
        week3 = next(r for r in out if r["week"] == 3)
        self.assertEqual(week3["incumbent_starter_player_id"], "00-0039150")
        self.assertEqual(week3["availability_status"], "LISTED_OUT")
        self.assertTrue(week3["starter_out_feature"])


class AvailabilityFeatureTests(unittest.TestCase):
    def test_healthy_incumbent_not_listed_is_not_game_affecting(self):
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        # A covered-season injury report exists for the team/week but has no
        # row at all for QB_A -- meaning no designation was filed for him.
        injuries = [injury_row(2025, "DEN", 2, "SOME_OTHER_PLAYER", "Questionable")]
        out = build_prior_starter_availability_features(injuries, continuity)
        week2 = next(r for r in out if r["week"] == 2)
        self.assertEqual(week2["availability_status"], "NOT_LISTED_GAME_AFFECTING_STATUS")
        self.assertFalse(week2["starter_out_feature"])

    def test_doubtful_incumbent_is_game_affecting(self):
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        injuries = [injury_row(2025, "DEN", 2, "QB_A", "Doubtful")]
        out = build_prior_starter_availability_features(injuries, continuity)
        week2 = next(r for r in out if r["week"] == 2)
        self.assertEqual(week2["availability_status"], "LISTED_DOUBTFUL")
        self.assertTrue(week2["starter_out_feature"])

    def test_questionable_incumbent_is_not_game_affecting(self):
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        injuries = [injury_row(2025, "DEN", 2, "QB_A", "Questionable")]
        out = build_prior_starter_availability_features(injuries, continuity)
        week2 = next(r for r in out if r["week"] == 2)
        self.assertFalse(week2["starter_out_feature"])

    def test_week_one_has_no_prior_starter_so_availability_is_unknown(self):
        qb_rows = [qb_row("QB_A", "DEN", "KC", 2025, 1, 30)]
        continuity = build_prior_qb_continuity_features(qb_rows)
        out = build_prior_starter_availability_features([], continuity)
        week1 = out[0]
        self.assertEqual(week1["availability_status"], "UNKNOWN_NO_PRIOR_STARTER_IDENTITY")
        self.assertIsNone(week1["starter_out_feature"])

    def test_pre_2009_season_is_explicitly_unavailable_not_a_fabricated_zero(self):
        """(b) missing/unavailable injury data for older seasons must produce
        an explicit unavailable state, never a fabricated favorable zero."""
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2005, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2005, 2, 28),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        # Even if an injury row somehow exists for this season, coverage
        # fails closed on the season itself, never trusting a stray row.
        injuries = [injury_row(2005, "DEN", 2, "QB_A", "Out")]
        out = build_prior_starter_availability_features(injuries, continuity)
        week2 = next(r for r in out if r["week"] == 2)
        self.assertEqual(week2["availability_status"], "SEASON_NOT_COVERED_BY_SOURCE")
        self.assertIsNone(week2["starter_out_feature"])
        self.assertIsNone(week2["report_status_raw"])

    def test_duplicate_injury_report_row_fails_closed(self):
        injuries = [
            injury_row(2025, "DEN", 2, "QB_A", "Out"),
            injury_row(2025, "DEN", 2, "QB_A", "Doubtful"),
        ]
        with self.assertRaisesRegex(InjuryAvailabilityError, "duplicate injury report row"):
            build_prior_starter_availability_features(injuries, [])

    def test_unrecognized_status_fails_closed(self):
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        injuries = [injury_row(2025, "DEN", 2, "QB_A", "SomeFutureStatus")]
        with self.assertRaisesRegex(InjuryAvailabilityError, "unrecognized report_status"):
            build_prior_starter_availability_features(injuries, continuity)

    def test_availability_feature_never_reads_target_block(self):
        """(a) point-in-time safety: the availability lookup only reads the
        continuity row's `features` block. A row whose `target` implies a
        starter change must not change how availability is computed --
        proven by deleting `target` entirely and confirming identical output."""
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_B", "DEN", "LV", 2025, 2, 28),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)
        injuries = [injury_row(2025, "DEN", 2, "QB_A", "Out")]

        with_target = build_prior_starter_availability_features(injuries, continuity)
        stripped = [{k: v for k, v in row.items() if k != "target"} for row in continuity]
        without_target = build_prior_starter_availability_features(injuries, stripped)
        self.assertEqual(with_target, without_target)

    def test_later_week_injury_row_does_not_change_earlier_week_output(self):
        """(a) point-in-time safety: a future week's Out designation for the
        same player must not retroactively change an earlier week's row."""
        qb_rows = [
            qb_row("QB_A", "DEN", "KC", 2025, 1, 30),
            qb_row("QB_A", "DEN", "LV", 2025, 2, 28),
            qb_row("QB_A", "DEN", "LAC", 2025, 3, 25),
        ]
        continuity = build_prior_qb_continuity_features(qb_rows)

        base_injuries = [injury_row(2025, "DEN", 2, "QB_A", "Questionable")]
        future_injuries = base_injuries + [injury_row(2025, "DEN", 3, "QB_A", "Out")]

        base_out = build_prior_starter_availability_features(base_injuries, continuity)
        future_out = build_prior_starter_availability_features(future_injuries, continuity)

        base_week2 = next(r for r in base_out if r["week"] == 2)
        future_week2 = next(r for r in future_out if r["week"] == 2)
        self.assertEqual(base_week2, future_week2)


if __name__ == "__main__":
    unittest.main()
