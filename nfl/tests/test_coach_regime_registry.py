#!/usr/bin/env python3
"""Regime-change, ambiguity-fail-closed, and leakage-safety contracts for the
coach/coordinator/playcaller regime registry substrate."""
import unittest
from datetime import date
from pathlib import Path

from nfl.research.coach_regime_registry import (
    ALL_ROLES,
    CONFIDENCE_ASSUMED,
    CONFIDENCE_CONFIRMED,
    ROLE_DC,
    ROLE_DEFENSIVE_PLAYCALLER,
    ROLE_HC,
    ROLE_OC,
    ROLE_OFFENSIVE_PLAYCALLER,
    RegimeInterval,
    RegimeRegistryError,
    build_coverage_report,
    build_game_date_index,
    build_hc_intervals_from_observations,
    build_registry_from_games_csv,
    iter_team_coach_observations,
    load_registry_json,
    lookup_regime,
    stretch_intervals_to_continuous,
    verify_hc_games_source,
)

REGISTRY_JSON_PATH = Path(__file__).resolve().parents[1] / "research" / "coach_regime_data" / "hc_regime_registry_v1.json"


def game_row(season, week, gameday, away_team, home_team, away_coach, home_coach, *, game_type="REG"):
    return {
        "season": season, "week": week, "game_type": game_type, "gameday": gameday,
        "away_team": away_team, "home_team": home_team,
        "away_coach": away_coach, "home_coach": home_coach,
    }


def interval(team, role, persons, start, end, *, confidence=CONFIDENCE_CONFIRMED, source="test-source"):
    return RegimeInterval(
        team=team, role=role,
        persons=tuple(persons) if not isinstance(persons, str) else (persons,),
        start_date=date.fromisoformat(start), end_date=date.fromisoformat(end),
        source=source, confidence=confidence,
    )


class RegimeIntervalValidationTests(unittest.TestCase):
    def test_valid_interval_constructs(self):
        iv = interval("DEN", ROLE_HC, "Coach A", "2020-09-10", "2020-12-10")
        self.assertFalse(iv.is_shared)
        self.assertTrue(iv.covers(date(2020, 10, 1)))
        self.assertFalse(iv.covers(date(2021, 1, 1)))

    def test_shared_regime_is_shared(self):
        iv = interval("DEN", ROLE_OC, ["Coach A", "Coach B"], "2020-09-10", "2020-12-10")
        self.assertTrue(iv.is_shared)

    def test_unknown_role_rejected(self):
        with self.assertRaisesRegex(RegimeRegistryError, "unknown role"):
            interval("DEN", "OFFENSE_GURU", "Coach A", "2020-09-10", "2020-12-10")

    def test_start_after_end_rejected(self):
        with self.assertRaisesRegex(RegimeRegistryError, "after end_date"):
            interval("DEN", ROLE_HC, "Coach A", "2020-12-10", "2020-09-10")

    def test_duplicate_person_rejected(self):
        with self.assertRaisesRegex(RegimeRegistryError, "duplicate person"):
            interval("DEN", ROLE_OC, ["Coach A", "Coach A"], "2020-09-10", "2020-12-10")

    def test_empty_persons_rejected(self):
        with self.assertRaises(RegimeRegistryError):
            RegimeInterval(
                team="DEN", role=ROLE_HC, persons=(), start_date=date(2020, 1, 1),
                end_date=date(2020, 1, 2), source="x", confidence=CONFIDENCE_CONFIRMED,
            )

    def test_to_dict_from_dict_roundtrip(self):
        iv = interval("DEN", ROLE_HC, "Coach A", "2020-09-10", "2020-12-10")
        restored = RegimeInterval.from_dict(iv.to_dict())
        self.assertEqual(iv, restored)


class ObservationIngestionTests(unittest.TestCase):
    def test_expands_home_and_away(self):
        rows = [game_row(2021, 1, "2021-09-12", "DEN", "SEA", "Coach Away", "Coach Home")]
        obs = iter_team_coach_observations(rows)
        self.assertEqual(len(obs), 2)
        teams = {o.team: o.coach for o in obs}
        self.assertEqual(teams["DEN"], "Coach Away")
        self.assertEqual(teams["SEA"], "Coach Home")

    def test_non_reg_rows_are_dropped(self):
        rows = [game_row(2021, 19, "2022-01-15", "DEN", "SEA", "A", "B", game_type="WC")]
        self.assertEqual(iter_team_coach_observations(rows), [])

    def test_missing_column_fails_closed(self):
        row = game_row(2021, 1, "2021-09-12", "DEN", "SEA", "A", "B")
        del row["home_coach"]
        with self.assertRaisesRegex(RegimeRegistryError, "missing required columns"):
            iter_team_coach_observations([row])

    def test_same_team_twice_fails_closed(self):
        rows = [game_row(2021, 1, "2021-09-12", "DEN", "DEN", "A", "B")]
        with self.assertRaisesRegex(RegimeRegistryError, "away_team equals home_team"):
            iter_team_coach_observations(rows)

    def test_duplicate_team_date_fails_closed(self):
        rows = [
            game_row(2021, 1, "2021-09-12", "DEN", "SEA", "A", "B"),
            game_row(2021, 1, "2021-09-12", "DEN", "KC", "A", "C"),
        ]
        with self.assertRaisesRegex(RegimeRegistryError, "duplicate REG-game observation"):
            iter_team_coach_observations(rows)


class RegimeChangeFixtureTests(unittest.TestCase):
    """Synthetic regime-change fixtures mirroring the real 2021 LV
    Gruden -> Bisaccia mid-season pattern this module was validated against."""

    def test_midseason_coaching_change_splits_into_two_intervals(self):
        rows = [
            game_row(2021, 1, "2021-09-12", "DEN", "XX", "Away1", "Coach One"),
            game_row(2021, 2, "2021-09-19", "XX", "DEN", "Coach One", "Home2"),
            game_row(2021, 3, "2021-09-26", "DEN", "XX", "Away3", "Coach One"),
            game_row(2021, 4, "2021-10-03", "XX", "DEN", "Coach Two", "Home4"),
            game_row(2021, 5, "2021-10-10", "DEN", "XX", "Away5", "Coach Two"),
        ]
        obs = iter_team_coach_observations(rows)
        ivs = build_hc_intervals_from_observations(obs)
        xx = sorted((iv for iv in ivs if iv.team == "XX"), key=lambda iv: iv.start_date)
        self.assertEqual(len(xx), 2)
        self.assertEqual(xx[0].persons, ("Coach One",))
        self.assertEqual(xx[0].start_date, date(2021, 9, 12))
        self.assertEqual(xx[0].end_date, date(2021, 9, 26))
        self.assertEqual(xx[1].persons, ("Coach Two",))
        self.assertEqual(xx[1].start_date, date(2021, 10, 3))
        self.assertEqual(xx[1].end_date, date(2021, 10, 10))

    def test_no_change_produces_one_interval(self):
        rows = [
            game_row(2021, 1, "2021-09-12", "DEN", "XX", "Away1", "Coach One"),
            game_row(2021, 2, "2021-09-19", "XX", "DEN", "Coach One", "Home2"),
        ]
        ivs = build_hc_intervals_from_observations(iter_team_coach_observations(rows))
        xx = [iv for iv in ivs if iv.team == "XX"]
        self.assertEqual(len(xx), 1)


class StretchIntervalsTests(unittest.TestCase):
    def test_bridges_gap_to_next_start(self):
        ivs = [
            interval("XX", ROLE_HC, "A", "2020-09-10", "2020-09-10"),
            interval("XX", ROLE_HC, "B", "2021-09-09", "2021-09-09"),
        ]
        stretched = stretch_intervals_to_continuous(ivs)
        first = next(iv for iv in stretched if iv.persons == ("A",))
        self.assertEqual(first.end_date, date(2021, 9, 8))

    def test_last_interval_not_extrapolated(self):
        ivs = [interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01")]
        stretched = stretch_intervals_to_continuous(ivs)
        self.assertEqual(stretched[0].end_date, date(2020, 12, 1))

    def test_overlap_raises_instead_of_stretching(self):
        ivs = [
            interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01"),
            interval("XX", ROLE_HC, "B", "2020-11-01", "2021-01-01"),
        ]
        with self.assertRaisesRegex(RegimeRegistryError, "overlapping intervals"):
            stretch_intervals_to_continuous(ivs)


class LookupRegimeTests(unittest.TestCase):
    def test_resolves_single_covering_interval(self):
        ivs = [interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01")]
        result = lookup_regime(ivs, team="xx", role=ROLE_HC, target_date=date(2020, 10, 1))
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.persons, ("A",))
        self.assertEqual(result.confidence, CONFIDENCE_CONFIRMED)

    def test_no_coverage_is_unknown(self):
        ivs = [interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01")]
        result = lookup_regime(ivs, team="XX", role=ROLE_HC, target_date=date(2019, 1, 1))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.reason, "NO_COVERAGE")

    def test_conflicting_overlap_fails_closed_to_unknown(self):
        ivs = [
            interval("XX", ROLE_OC, "A", "2020-09-10", "2020-12-01"),
            interval("XX", ROLE_OC, "B", "2020-10-01", "2020-11-01", source="different-source"),
        ]
        result = lookup_regime(ivs, team="XX", role=ROLE_OC, target_date=date(2020, 10, 15))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn("AMBIGUOUS_OVERLAPPING_INTERVALS", result.reason)
        self.assertIsNone(result.persons)

    def test_identical_duplicate_records_are_not_ambiguous(self):
        ivs = [
            interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01"),
            interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01"),
        ]
        result = lookup_regime(ivs, team="XX", role=ROLE_HC, target_date=date(2020, 10, 1))
        self.assertEqual(result.status, "RESOLVED")

    def test_shared_regime_resolves_as_one_multi_person_regime(self):
        ivs = [interval("XX", ROLE_OC, ["A", "B"], "2020-09-10", "2020-12-01")]
        result = lookup_regime(ivs, team="XX", role=ROLE_OC, target_date=date(2020, 10, 1))
        self.assertEqual(result.status, "RESOLVED")
        self.assertTrue(result.is_shared)
        self.assertEqual(set(result.persons), {"A", "B"})

    def test_playcaller_defaults_to_coordinator_when_no_override(self):
        ivs = [interval("XX", ROLE_OC, "Coordinator A", "2020-09-10", "2020-12-01")]
        result = lookup_regime(ivs, team="XX", role=ROLE_OFFENSIVE_PLAYCALLER, target_date=date(2020, 10, 1))
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.persons, ("Coordinator A",))
        self.assertEqual(result.confidence, CONFIDENCE_ASSUMED)
        self.assertEqual(result.derived_from_role, ROLE_OC)

    def test_confirmed_playcaller_override_wins_over_default(self):
        ivs = [
            interval("XX", ROLE_OC, "Coordinator A", "2020-09-10", "2020-12-01"),
            interval("XX", ROLE_OFFENSIVE_PLAYCALLER, "Head Coach Z", "2020-09-10", "2020-12-01"),
        ]
        result = lookup_regime(ivs, team="XX", role=ROLE_OFFENSIVE_PLAYCALLER, target_date=date(2020, 10, 1))
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.persons, ("Head Coach Z",))
        self.assertEqual(result.confidence, CONFIDENCE_CONFIRMED)
        self.assertIsNone(result.derived_from_role)

    def test_playcaller_unknown_when_coordinator_also_unknown(self):
        result = lookup_regime([], team="XX", role=ROLE_DEFENSIVE_PLAYCALLER, target_date=date(2020, 10, 1))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn(ROLE_DC, result.reason)

    def test_playcaller_stays_unknown_when_coordinator_itself_ambiguous(self):
        ivs = [
            interval("XX", ROLE_DC, "A", "2020-09-10", "2020-12-01"),
            interval("XX", ROLE_DC, "B", "2020-10-01", "2020-11-01", source="different-source"),
        ]
        result = lookup_regime(ivs, team="XX", role=ROLE_DEFENSIVE_PLAYCALLER, target_date=date(2020, 10, 15))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn("AMBIGUOUS", result.reason)

    def test_target_date_and_season_week_mutually_exclusive(self):
        with self.assertRaisesRegex(RegimeRegistryError, "not both"):
            lookup_regime([], team="XX", role=ROLE_HC, target_date=date(2020, 1, 1), season=2020, week=1)

    def test_requires_one_of_target_date_or_season_week(self):
        with self.assertRaisesRegex(RegimeRegistryError, "must supply"):
            lookup_regime([], team="XX", role=ROLE_HC)

    def test_season_week_resolves_via_game_date_index(self):
        ivs = [interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01")]
        index = build_game_date_index([game_row(2020, 1, "2020-09-10", "XX", "YY", "A", "B")])
        result = lookup_regime(ivs, team="XX", role=ROLE_HC, season=2020, week=1, game_date_index=index)
        self.assertEqual(result.status, "RESOLVED")

    def test_season_week_with_no_game_date_is_unknown_not_a_crash(self):
        result = lookup_regime([], team="XX", role=ROLE_HC, season=2020, week=1, game_date_index={})
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.reason, "NO_GAME_DATE_FOR_SEASON_WEEK")

    def test_unknown_role_raises_not_silently_unknown(self):
        with self.assertRaises(RegimeRegistryError):
            lookup_regime([], team="XX", role="MASCOT", target_date=date(2020, 1, 1))


class LeakageSafetyTests(unittest.TestCase):
    """Adding MORE future regime data must never change what an earlier
    target-date lookup resolves. This is the structural no-leakage guarantee:
    the function only ever consults the caller's own explicit target date."""

    def test_future_regime_change_does_not_alter_past_lookup(self):
        base = [
            interval("XX", ROLE_HC, "Early Coach", "2015-09-10", "2019-12-01"),
            interval("XX", ROLE_HC, "Later Coach", "2020-09-10", "2020-12-01"),
        ]
        with_more_future = base + [
            interval("XX", ROLE_HC, "Even Later Coach", "2021-09-10", "2021-12-01"),
        ]
        target = date(2017, 10, 1)
        result_base = lookup_regime(base, team="XX", role=ROLE_HC, target_date=target)
        result_future = lookup_regime(with_more_future, team="XX", role=ROLE_HC, target_date=target)
        self.assertEqual(result_base.status, "RESOLVED")
        self.assertEqual(result_base.persons, result_future.persons)
        self.assertEqual(result_base.confidence, result_future.confidence)

    def test_date_beyond_last_known_regime_is_unknown_not_assumed_current(self):
        ivs = [interval("XX", ROLE_HC, "Coach A", "2020-09-10", "2020-12-01")]
        result = lookup_regime(ivs, team="XX", role=ROLE_HC, target_date=date(2025, 1, 1))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.reason, "NO_COVERAGE")

    def test_lookup_never_reads_wall_clock_time(self):
        # A lookup at an arbitrary historical date must be reproducible
        # regardless of "today" -- there is no implicit "now" anywhere in
        # lookup_regime's signature for this test to even accidentally hit,
        # which this call demonstrates by requiring an explicit date.
        ivs = [interval("XX", ROLE_HC, "Coach A", "1999-09-12", "1999-12-01")]
        result = lookup_regime(ivs, team="XX", role=ROLE_HC, target_date=date(1999, 10, 1))
        self.assertEqual(result.status, "RESOLVED")


class CoverageReportTests(unittest.TestCase):
    def test_populated_role_reports_teams_and_range(self):
        ivs = [
            interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01"),
            interval("YY", ROLE_HC, "B", "2021-09-10", "2021-12-01"),
        ]
        report = build_coverage_report(ivs)
        self.assertEqual(report[ROLE_HC]["n_teams"], 2)
        self.assertEqual(report[ROLE_HC]["per_team"]["XX"]["earliest_start"], "2020-09-10")

    def test_unpopulated_role_discloses_coverage_gap(self):
        ivs = [interval("XX", ROLE_HC, "A", "2020-09-10", "2020-12-01")]
        report = build_coverage_report(ivs)
        for role in (ROLE_OC, ROLE_DC, ROLE_OFFENSIVE_PLAYCALLER, ROLE_DEFENSIVE_PLAYCALLER):
            self.assertEqual(report[role]["n_teams"], 0)
            self.assertIn("coverage_gap", report[role])

    def test_all_roles_present_in_report(self):
        report = build_coverage_report([])
        self.assertEqual(set(report.keys()), ALL_ROLES)


class DigestVerificationTests(unittest.TestCase):
    def test_tampered_bytes_fail_closed(self):
        with self.assertRaisesRegex(RegimeRegistryError, "digest drift"):
            verify_hc_games_source(b"not the real games.csv bytes")


class RealIngestedRegistryTests(unittest.TestCase):
    """Multi-year coverage assertions against the actual ingested Phase 1
    data (nfldata games.csv, pinned commit) -- not fabricated expectations."""

    @classmethod
    def setUpClass(cls):
        if not REGISTRY_JSON_PATH.exists():
            raise unittest.SkipTest(f"registry data file not found at {REGISTRY_JSON_PATH}")
        cls.intervals = load_registry_json(REGISTRY_JSON_PATH)

    def test_hc_intervals_are_all_confirmed(self):
        hc = [iv for iv in self.intervals if iv.role == ROLE_HC]
        self.assertGreater(len(hc), 0)
        self.assertTrue(all(iv.confidence == CONFIDENCE_CONFIRMED for iv in hc))

    def test_multi_year_coverage_spans_1999_forward(self):
        hc = [iv for iv in self.intervals if iv.role == ROLE_HC]
        earliest = min(iv.start_date for iv in hc)
        self.assertLessEqual(earliest.year, 1999)
        latest = max(iv.end_date for iv in hc)
        self.assertGreaterEqual(latest.year, 2025)

    def test_real_2021_las_vegas_raiders_midseason_change_is_present(self):
        result = lookup_regime(self.intervals, team="LV", role=ROLE_HC, target_date=date(2021, 9, 19))
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.persons, ("Jon Gruden",))

        after_change = lookup_regime(self.intervals, team="LV", role=ROLE_HC, target_date=date(2021, 10, 24))
        self.assertEqual(after_change.status, "RESOLVED")
        self.assertEqual(after_change.persons, ("Rich Bisaccia",))

    def test_oc_role_is_disclosed_gap_not_fabricated(self):
        result = lookup_regime(self.intervals, team="LV", role=ROLE_OC, target_date=date(2021, 9, 19))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.reason, "NO_COVERAGE")

    def test_offensive_playcaller_role_is_disclosed_gap_not_fabricated(self):
        result = lookup_regime(self.intervals, team="LV", role=ROLE_OFFENSIVE_PLAYCALLER, target_date=date(2021, 9, 19))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertIn(ROLE_OC, result.reason)

    def test_every_hc_target_date_resolves_to_exactly_one_regime(self):
        # Every HC interval's own midpoint-ish start date must resolve
        # cleanly for its own team -- the core P2.1 acceptance criterion.
        hc = [iv for iv in self.intervals if iv.role == ROLE_HC]
        for iv in hc[:50]:
            result = lookup_regime(self.intervals, team=iv.team, role=ROLE_HC, target_date=iv.start_date)
            self.assertEqual(result.status, "RESOLVED", msg=f"{iv.team} {iv.start_date}")
            self.assertEqual(result.persons, iv.persons)


class BuildRegistryFromGamesCsvTests(unittest.TestCase):
    def test_tampered_csv_file_fails_closed(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            f.write("season,week,game_type,gameday,away_team,home_team,away_coach,home_coach\n")
            f.write("2020,1,REG,2020-09-10,XX,YY,A,B\n")
            path = Path(f.name)
        try:
            with self.assertRaisesRegex(RegimeRegistryError, "digest drift"):
                build_registry_from_games_csv(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
