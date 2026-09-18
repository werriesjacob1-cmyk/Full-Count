import hashlib
import json
import unittest

from nfl.prospective.espn_final_outcome import extract_espn_final_outcome
from nfl.prospective.game_identity import bind_nflverse_game_identity
from nfl.prospective.game_market_postgame_report import (
    GameMarketPostgameError,
    american_unit_profit,
    build_slate_postgame_report,
    grade_shadow_event,
    verify_slate_manifest,
)
from nfl.prospective.game_market_shadow_board import build_game_market_shadow_board
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot

EVENT = "35601246"
CAPTURED = "2026-09-20T16:00:00Z"
KICKOFF = "2026-09-20T17:00:00Z"
MARKET_SEALED = "2026-09-20T16:01:00Z"
BOARD_SEALED = "2026-09-20T16:02:00Z"
SHA = "a" * 64


def market_record(market):
    row = {
        "source": "fanduel_nfl",
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "event_id": EVENT,
        "event_name": "Miami Dolphins @ Buffalo Bills",
        "event_open_date": KICKOFF,
        "market_id": f"{market}-1",
        "market_name": market,
        "market_type": "TYPE",
        "market_time": KICKOFF,
        "market_status": "OPEN",
        "in_play": False,
        "captured_at": CAPTURED,
        "source_payload_sha256": SHA,
        "source_artifact": "fixture",
        "source_url": "https://example.test/fanduel",
        "market": market,
        "away_team": "Miami Dolphins",
        "home_team": "Buffalo Bills",
    }
    if market == "moneyline":
        row.update({
            "away_odds": 130,
            "home_odds": -150,
            "away_selection_id": "a-ml",
            "home_selection_id": "h-ml",
        })
    elif market == "spread":
        row.update({
            "away_line": 3.0,
            "home_line": -3.0,
            "away_odds": -110,
            "home_odds": -110,
            "away_selection_id": "a-spread",
            "home_selection_id": "h-spread",
        })
    elif market == "game_total":
        row.update({
            "line": 45.5,
            "over_odds": 105,
            "under_odds": -125,
            "over_selection_id": "over-total",
            "under_selection_id": "under-total",
        })
    return row


def snapshot():
    return seal_game_market_snapshot(
        [
            market_record("moneyline"),
            market_record("spread"),
            market_record("game_total"),
        ],
        [],
        event_id=EVENT,
        sealed_at=MARKET_SEALED,
    )


def prediction(**overrides):
    row = {
        "game_id": "2026_03_MIA_BUF",
        "season": 2026,
        "week": 3,
        "game_type": "REG",
        "home_team": "BUF",
        "away_team": "MIA",
        "home_team_full": "Buffalo Bills",
        "away_team_full": "Miami Dolphins",
        "target_final_status": "PREGAME",
        "home_prior_games_n": 5,
        "away_prior_games_n": 5,
        "eligibility": "ELIGIBLE",
        "predicted_home_points": 26.0,
        "predicted_away_points": 21.0,
        "predicted_home_margin": 5.0,
        "predicted_total": 47.0,
        "baseline_name": "GAME_MARKET_B0_PRIOR_SCORING_BLEND",
        "min_prior_games": 3,
        "uses_market_line_as_feature": False,
        "uses_current_game_outcome_as_feature": False,
        "home_field_adjustment": 0.0,
    }
    row.update(overrides)
    return row


def board(snap=None, pred=None):
    return build_game_market_shadow_board(
        snapshot() if snap is None else snap,
        prediction() if pred is None else pred,
        model_code_sha="deadbeef",
        source_vintage="fixture-v1",
        sealed_at=BOARD_SEALED,
    )


def binding(snap):
    return bind_nflverse_game_identity(
        snap,
        [{
            "game_id": "2026_03_MIA_BUF",
            "season": 2026,
            "game_type": "REG",
            "week": 3,
            "gameday": "2026-09-20",
            "gametime": "13:00",
            "away_team": "MIA",
            "home_team": "BUF",
            "away_score": "",
            "home_score": "",
            "result": "",
            "total": "",
            "gsis": 123,
            "espn": 401999999,
        }],
        team_aliases={
            "Miami Dolphins": "MIA",
            "Buffalo Bills": "BUF",
        },
        source_url="https://example.test/games.csv",
        source_sha256="b" * 64,
        observed_at="2026-09-20T16:05:00Z",
    )


def espn_event(home_score="27", away_score="20"):
    return {
        "id": "401999999",
        "date": KICKOFF,
        "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
        "competitions": [{
            "id": "401999999",
            "competitors": [
                {
                    "homeAway": "home",
                    "score": home_score,
                    "team": {"abbreviation": "BUF"},
                },
                {
                    "homeAway": "away",
                    "score": away_score,
                    "team": {"abbreviation": "MIA"},
                },
            ],
        }],
    }


def outcome(bound, home_score="27", away_score="20"):
    return extract_espn_final_outcome(
        bound,
        espn_event(home_score, away_score),
        source_url="https://example.test/espn",
        payload_sha256="c" * 64,
        observed_at="2026-09-20T21:00:00Z",
    )


def manifest(event_status="BOARD_BUILT"):
    body = {
        "schema_version": 1,
        "sport": "NFL",
        "evidence_class": "PROSPECTIVE_SHADOW_FULL_SLATE",
        "research_only": True,
        "public_eligible": False,
        "target_local_date": "2026-09-20",
        "generated_at": "2026-09-20T16:03:00Z",
        "code_sha": "deadbeef",
        "schedule_source_url": "https://example.test/games.csv",
        "schedule_source_sha256": "b" * 64,
        "discovered_event_count": 1,
        "accounted_event_count": 1,
        "market_decision_counts": {"SHADOW_ONLY": 2, "NO_PLAY": 0},
        "events": [{
            "event_id": EVENT,
            "event_name": "Miami Dolphins @ Buffalo Bills",
            "open_date": KICKOFF,
            "status": event_status,
            "game_id": "2026_03_MIA_BUF" if event_status == "BOARD_BUILT" else None,
            "reason": None if event_status == "BOARD_BUILT" else "fixture no play",
        }],
    }
    raw = json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return {**body, "manifest_sha256": hashlib.sha256(raw).hexdigest()}


class GameMarketPostgameTests(unittest.TestCase):
    def test_american_unit_profit(self):
        self.assertAlmostEqual(american_unit_profit(-125, "HIT"), 0.8)
        self.assertAlmostEqual(american_unit_profit(150, "HIT"), 1.5)
        self.assertEqual(american_unit_profit(-110, "MISS"), -1.0)
        self.assertEqual(american_unit_profit(-110, "PUSH"), 0.0)

    def test_event_chain_grades_exact_frozen_spread_and_total(self):
        snap = snapshot()
        pred = prediction()
        shadow = board(snap, pred)
        bound = binding(snap)
        final = outcome(bound, home_score="27", away_score="20")
        report = grade_shadow_event(shadow, snap, pred, bound, final)
        rows = {row["market"]: row for row in report["records"]}

        self.assertEqual(rows["spread"]["selected_side"], "HOME")
        self.assertEqual(rows["spread"]["settlement"], "HIT")
        self.assertEqual(rows["spread"]["selected_odds"], -110)
        self.assertAlmostEqual(rows["spread"]["profit_units"], 100 / 110)

        self.assertEqual(rows["game_total"]["selected_side"], "OVER")
        self.assertEqual(rows["game_total"]["settlement"], "HIT")
        self.assertEqual(rows["game_total"]["selected_odds"], 105)
        self.assertAlmostEqual(rows["game_total"]["profit_units"], 1.05)

    def test_market_no_play_remains_zero_stake(self):
        snap = snapshot()
        pred = prediction(predicted_total=45.5)
        shadow = board(snap, pred)
        bound = binding(snap)
        final = outcome(bound)
        report = grade_shadow_event(shadow, snap, pred, bound, final)
        rows = {row["market"]: row for row in report["records"]}
        self.assertEqual(rows["game_total"]["decision_status"], "NO_PLAY")
        self.assertIsNone(rows["game_total"]["settlement"])
        self.assertEqual(rows["game_total"]["stake_units"], 0.0)
        self.assertEqual(rows["game_total"]["profit_units"], 0.0)

    def test_tampered_selected_odds_fails_closed(self):
        snap = snapshot()
        pred = prediction()
        shadow = board(snap, pred)
        shadow["records"][0]["selected_odds"] = 999
        bound = binding(snap)
        final = outcome(bound)
        with self.assertRaises(Exception):
            grade_shadow_event(shadow, snap, pred, bound, final)

    def test_full_slate_report_preserves_denominator_and_roi(self):
        snap = snapshot()
        pred = prediction()
        shadow = board(snap, pred)
        bound = binding(snap)
        final = outcome(bound, home_score="27", away_score="20")
        frozen_manifest = manifest()
        report = build_slate_postgame_report(
            frozen_manifest,
            [{
                "event_id": EVENT,
                "board": shadow,
                "snapshot": snap,
                "prediction": pred,
                "binding": bound,
                "outcome": final,
            }],
            generated_at="2026-09-20T21:05:00Z",
        )
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["graded_wager_count"], 2)
        self.assertEqual(report["market_no_play_count"], 0)
        self.assertEqual(report["settlement_counts"]["HIT"], 2)
        self.assertEqual(report["settlement_counts"]["MISS"], 0)
        self.assertEqual(report["by_market"]["spread"]["hits"], 1)
        self.assertEqual(report["by_market"]["game_total"]["hits"], 1)
        self.assertGreater(report["profit_units"], 1.9)
        self.assertGreater(report["roi"], 0.9)

    def test_manifest_event_no_play_requires_package_but_not_outcome(self):
        frozen_manifest = manifest(event_status="NO_PLAY")
        report = build_slate_postgame_report(
            frozen_manifest,
            [{"event_id": EVENT}],
            generated_at="2026-09-20T21:05:00Z",
        )
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["graded_wager_count"], 0)
        self.assertEqual(report["events"][0]["status"], "NO_PLAY")

    def test_missing_event_package_fails_denominator_check(self):
        with self.assertRaisesRegex(GameMarketPostgameError, "coverage mismatch"):
            build_slate_postgame_report(
                manifest(),
                [],
                generated_at="2026-09-20T21:05:00Z",
            )

    def test_manifest_tamper_fails(self):
        value = manifest()
        value["accounted_event_count"] = 0
        with self.assertRaisesRegex(GameMarketPostgameError, "hash mismatch"):
            verify_slate_manifest(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
