import unittest

from nfl.prospective.game_identity import GameIdentityError, bind_nflverse_game_identity
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot

FANDUEL_SHA = "a" * 64
SOURCE_SHA = "c" * 64
SOURCE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/schedules.csv"


def snapshot(*, records=None):
    if records is None:
        records = [market_record(name) for name in ("moneyline", "spread", "game_total")]
    present = {record["market"] for record in records}
    failures = [
        {"event_id": "35601246", "market": name, "reason": "TEST_MISSING"}
        for name in {"moneyline", "spread", "game_total"} - present
    ]
    return seal_game_market_snapshot(
        records,
        failures,
        event_id="35601246",
        sealed_at="2026-09-14T23:31:00Z",
    )


def market_record(name):
    return {
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "market": name,
        "market_type": "TYPE",
        "market_name": "Primary",
        "event_id": "35601246",
        "market_id": f"{name}-1",
        "market_time": "2026-09-15T00:15:00Z",
        "captured_at": "2026-09-14T23:30:00Z",
        "source_payload_sha256": FANDUEL_SHA,
        "market_status": "OPEN",
        "in_play": False,
        "away_team": "Denver Broncos",
        "home_team": "Kansas City Chiefs",
    }


def row(**overrides):
    value = {
        "game_id": "2026_01_DEN_KC",
        "season": 2026,
        "game_type": "REG",
        "week": 1,
        "gameday": "2026-09-14",
        "gametime": "20:15",
        "away_team": "DEN",
        "away_score": "",
        "home_team": "KC",
        "home_score": "",
        "result": "",
        "total": "",
        "gsis": 60193,
        "espn": 401872931,
    }
    value.update(overrides)
    return value


ALIASES = {"Denver Broncos": "DEN", "Kansas City Chiefs": "KC"}


def bind(schedule_rows=None, **kwargs):
    return bind_nflverse_game_identity(
        snapshot(),
        [row()] if schedule_rows is None else schedule_rows,
        team_aliases=kwargs.pop("team_aliases", ALIASES),
        source_url=kwargs.pop("source_url", SOURCE_URL),
        source_sha256=kwargs.pop("source_sha256", SOURCE_SHA),
        observed_at=kwargs.pop("observed_at", "2026-09-14T23:45:00Z"),
        **kwargs,
    )


class GameIdentityTests(unittest.TestCase):
    def test_binds_den_kc_by_teams_and_exact_eastern_kickoff(self):
        result = bind()
        self.assertEqual(result["sportsbook_event_id"], "35601246")
        self.assertEqual(result["nflverse_game_id"], "2026_01_DEN_KC")
        self.assertEqual(result["away_team"], "DEN")
        self.assertEqual(result["home_team"], "KC")
        self.assertEqual(result["binding_basis"], "AWAY_HOME_TEAMS_PLUS_EXACT_ET_KICKOFF")
        self.assertEqual(result["score_state"], "UNSETTLED")
        self.assertIsNone(result["final_status"])
        self.assertIs(result["finality_proven"], False)
        self.assertEqual(len(result["binding_sha256"]), 64)

    def test_consistent_scores_are_reported_but_never_called_final(self):
        result = bind([row(away_score=24, home_score=27, result=3, total=51)])
        self.assertEqual(result["score_state"], "SCORES_PRESENT_CONSISTENT")
        self.assertEqual(result["away_score"], 24)
        self.assertEqual(result["home_score"], 27)
        self.assertIsNone(result["final_status"])
        self.assertIs(result["finality_proven"], False)

    def test_partial_or_inconsistent_scores_fail_closed(self):
        with self.assertRaisesRegex(GameIdentityError, "partial nflverse score state"):
            bind([row(away_score=24)])
        with self.assertRaisesRegex(GameIdentityError, "result does not equal"):
            bind([row(away_score=24, home_score=27, result=2, total=51)])
        with self.assertRaisesRegex(GameIdentityError, "total does not equal"):
            bind([row(away_score=24, home_score=27, result=3, total=50)])

    def test_zero_matches_fail_closed(self):
        with self.assertRaisesRegex(GameIdentityError, "found 0"):
            bind([row(gametime="20:20")])

    def test_duplicate_matches_fail_closed(self):
        with self.assertRaisesRegex(GameIdentityError, "found 2"):
            bind([row(), row()])

    def test_unresolved_team_alias_fails_closed(self):
        with self.assertRaisesRegex(GameIdentityError, "unresolved sportsbook team alias"):
            bind(team_aliases={"Denver Broncos": "DEN"})

    def test_aliases_cannot_collapse_both_teams(self):
        with self.assertRaisesRegex(GameIdentityError, "same team"):
            bind(team_aliases={"Denver Broncos": "KC", "Kansas City Chiefs": "KC"})

    def test_total_only_snapshot_retains_team_identity(self):
        total_only = snapshot(records=[market_record("game_total")])
        result = bind_nflverse_game_identity(
            total_only,
            [row()],
            team_aliases=ALIASES,
            source_url=SOURCE_URL,
            source_sha256=SOURCE_SHA,
            observed_at="2026-09-14T23:45:00Z",
        )
        self.assertEqual(result["nflverse_game_id"], "2026_01_DEN_KC")

    def test_normalized_records_must_agree_on_team_identity(self):
        records = snapshot()["records"]
        records[1] = dict(records[1], home_team="Other Team")
        disagreeing = snapshot(records=records)
        with self.assertRaisesRegex(GameIdentityError, "disagree on away/home"):
            bind_nflverse_game_identity(
                disagreeing,
                [row()],
                team_aliases={**ALIASES, "Other Team": "LV"},
                source_url=SOURCE_URL,
                source_sha256=SOURCE_SHA,
                observed_at="2026-09-14T23:45:00Z",
            )

    def test_records_must_share_one_market_time(self):
        records = snapshot()["records"]
        records[1] = dict(records[1], market_time="2026-09-15T00:16:00Z")
        with self.assertRaisesRegex(GameIdentityError, "share exactly one market_time"):
            bind_nflverse_game_identity(
                snapshot(records=records),
                [row()],
                team_aliases=ALIASES,
                source_url=SOURCE_URL,
                source_sha256=SOURCE_SHA,
                observed_at="2026-09-14T23:45:00Z",
            )

    def test_bad_source_hash_and_naive_observed_at_fail_closed(self):
        with self.assertRaisesRegex(GameIdentityError, "64 lowercase hex"):
            bind(source_sha256="bad")
        with self.assertRaisesRegex(GameIdentityError, "timezone-aware"):
            bind(observed_at="2026-09-14T23:45:00")

    def test_snapshot_must_be_research_only_nfl(self):
        bad = snapshot()
        bad["research_only"] = False
        with self.assertRaisesRegex(GameIdentityError, "research-only"):
            bind_nflverse_game_identity(
                bad,
                [row()],
                team_aliases=ALIASES,
                source_url=SOURCE_URL,
                source_sha256=SOURCE_SHA,
                observed_at="2026-09-14T23:45:00Z",
            )

    def test_tampered_sealed_snapshot_fails_closed(self):
        sealed = snapshot(records=[market_record("moneyline"), market_record("spread"), market_record("game_total")])
        sealed["records"][0]["home_team"] = "LV"

        with self.assertRaisesRegex(GameIdentityError, "invalid sealed snapshot"):
            bind_nflverse_game_identity(
                sealed,
                [row()],
                team_aliases=ALIASES,
                source_url=SOURCE_URL,
                source_sha256=SOURCE_SHA,
                observed_at="2026-09-14T23:45:00Z",
            )


if __name__ == "__main__":
    unittest.main()
