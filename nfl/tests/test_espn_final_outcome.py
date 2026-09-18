import unittest

from nfl.normalize.fanduel_game_lines import normalize_payload
from nfl.prospective.espn_final_outcome import EspnFinalOutcomeError, extract_espn_final_outcome
from nfl.prospective.game_identity import bind_nflverse_game_identity
from nfl.prospective.game_market_grader import grade_game_market
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot
from nfl.tests.test_fanduel_game_lines import payload

BINDING_SHA = "b" * 64
PAYLOAD_SHA = "c" * 64
SOURCE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard/401872931"


def binding(**overrides):
    value = {
        "sportsbook_event_id": "35601246",
        "binding_sha256": BINDING_SHA,
        "nflverse_espn": "401872931",
        "away_team": "DEN",
        "home_team": "KC",
        "scheduled_kickoff": "2026-09-15T00:15:00+00:00",
        "finality_proven": False,
    }
    value.update(overrides)
    return value


def event(**overrides):
    value = {
        "id": "401872931",
        "date": "2026-09-15T00:15:00Z",
        "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
        "competitions": [
            {
                "id": "401872931",
                "competitors": [
                    {"homeAway": "home", "score": "27", "team": {"abbreviation": "KC"}},
                    {"homeAway": "away", "score": "24", "team": {"abbreviation": "DEN"}},
                ],
            }
        ],
    }
    value.update(overrides)
    return value


def extract(binding_value=None, event_value=None, **kwargs):
    return extract_espn_final_outcome(
        binding() if binding_value is None else binding_value,
        event() if event_value is None else event_value,
        source_url=kwargs.pop("source_url", SOURCE_URL),
        payload_sha256=kwargs.pop("payload_sha256", PAYLOAD_SHA),
        observed_at=kwargs.pop("observed_at", "2026-09-15T04:00:00Z"),
        **kwargs,
    )


class EspnFinalOutcomeTests(unittest.TestCase):
    def test_canonical_market_reaches_deterministic_final_grade(self):
        normalized = normalize_payload(
            payload(),
            captured_at="2026-09-14T22:57:21Z",
            source_payload_sha256="a" * 64,
            source_artifact="event-999.json",
            source_url="https://sportsbook.example/event/999",
        )
        sealed = seal_game_market_snapshot(
            normalized["candidates"],
            normalized["rejections"],
            event_id="999",
            sealed_at="2026-09-14T23:31:00Z",
        )
        game_binding = bind_nflverse_game_identity(
            sealed,
            [{
                "game_id": "2026_01_DEN_KC", "season": 2026, "game_type": "REG",
                "week": 1, "gameday": "2026-09-14", "gametime": "20:15",
                "away_team": "DEN", "home_team": "KC", "away_score": "",
                "home_score": "", "result": "", "total": "",
                "gsis": 60193, "espn": 401872931,
            }],
            team_aliases={"Denver Broncos": "DEN", "Kansas City Chiefs": "KC"},
            source_url="https://github.com/nflverse/nflverse-data/releases/download/schedules/schedules.csv",
            source_sha256="b" * 64,
            observed_at="2026-09-14T23:45:00Z",
        )
        final = extract_espn_final_outcome(
            game_binding,
            event(),
            source_url=SOURCE_URL,
            payload_sha256=PAYLOAD_SHA,
            observed_at="2026-09-15T04:00:00Z",
        )
        spread = next(row for row in normalized["candidates"] if row["market"] == "spread")
        grade = grade_game_market(spread, final, side="HOME")

        self.assertEqual(grade["settlement"], "HIT")
        self.assertEqual(grade["event_id"], "999")
        self.assertEqual(len(grade["grade_sha256"]), 64)

    def test_explicit_final_event_becomes_grader_ready_outcome(self):
        result = extract()
        self.assertEqual(result["event_id"], "35601246")
        self.assertEqual(result["authoritative_event_id"], "401872931")
        self.assertEqual(result["away_score"], 24)
        self.assertEqual(result["home_score"], 27)
        self.assertEqual(result["final_status"], "FINAL")
        self.assertEqual(result["finality_basis"], "ESPN_STATUS_FINAL_AND_COMPLETED_TRUE")
        self.assertEqual(len(result["outcome_sha256"]), 64)

    def test_completed_false_fails_closed_even_with_scores(self):
        value = event()
        value["status"] = {"type": {"name": "STATUS_FINAL", "completed": False}}
        with self.assertRaisesRegex(EspnFinalOutcomeError, "not explicitly final"):
            extract(event_value=value)

    def test_nonfinal_status_fails_closed_even_if_completed_true(self):
        value = event()
        value["status"] = {"type": {"name": "STATUS_IN_PROGRESS", "completed": True}}
        with self.assertRaisesRegex(EspnFinalOutcomeError, "not explicitly final"):
            extract(event_value=value)

    def test_espn_id_must_equal_nflverse_retained_id(self):
        with self.assertRaisesRegex(EspnFinalOutcomeError, "event id does not match"):
            extract(event_value=event(id="other"))

    def test_competition_id_must_match_event_id(self):
        value = event()
        value["competitions"][0]["id"] = "other"
        with self.assertRaisesRegex(EspnFinalOutcomeError, "competition id does not match"):
            extract(event_value=value)

    def test_kickoff_mismatch_fails_closed(self):
        with self.assertRaisesRegex(EspnFinalOutcomeError, "kickoff does not match"):
            extract(event_value=event(date="2026-09-15T00:20:00Z"))

    def test_team_identity_and_roles_must_match_binding(self):
        value = event()
        value["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "LV"
        with self.assertRaisesRegex(EspnFinalOutcomeError, "team identity mismatch"):
            extract(event_value=value)

        value = event()
        value["competitions"][0]["competitors"][1]["homeAway"] = "home"
        with self.assertRaisesRegex(EspnFinalOutcomeError, "unique home/away roles"):
            extract(event_value=value)

    def test_competition_and_competitor_cardinality_fail_closed(self):
        with self.assertRaisesRegex(EspnFinalOutcomeError, "exactly one competition"):
            extract(event_value=event(competitions=[]))
        value = event()
        value["competitions"][0]["competitors"] = value["competitions"][0]["competitors"][:1]
        with self.assertRaisesRegex(EspnFinalOutcomeError, "exactly two competitors"):
            extract(event_value=value)

    def test_invalid_scores_fail_closed(self):
        for bad in ["", "24.5", -1, True, None]:
            with self.subTest(score=bad):
                value = event()
                value["competitions"][0]["competitors"][1]["score"] = bad
                with self.assertRaises(EspnFinalOutcomeError):
                    extract(event_value=value)

    def test_bad_provenance_hashes_and_naive_times_fail_closed(self):
        with self.assertRaisesRegex(EspnFinalOutcomeError, "64 lowercase hex"):
            extract(payload_sha256="bad")
        with self.assertRaisesRegex(EspnFinalOutcomeError, "64 lowercase hex"):
            extract(binding_value=binding(binding_sha256="bad"))
        with self.assertRaisesRegex(EspnFinalOutcomeError, "timezone-aware"):
            extract(observed_at="2026-09-15T04:00:00")

    def test_final_observation_cannot_precede_kickoff(self):
        with self.assertRaisesRegex(EspnFinalOutcomeError, "cannot precede kickoff"):
            extract(observed_at="2026-09-14T23:00:00Z")

    def test_numeric_scores_are_accepted_and_deterministic(self):
        value = event()
        value["competitions"][0]["competitors"][0]["score"] = 27
        value["competitions"][0]["competitors"][1]["score"] = 24
        first = extract(event_value=value)
        second = extract(event_value=value)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
