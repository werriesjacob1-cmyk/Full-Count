from copy import deepcopy
import unittest

from nfl.prospective.game_market_shadow_board import (
    GameMarketShadowBoardError,
    build_game_market_shadow_board,
    verify_game_market_shadow_board,
)
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot

EVENT = "35601246"
CAPTURED = "2026-09-20T16:00:00Z"
KICKOFF = "2026-09-20T17:00:00Z"
MARKET_SEALED = "2026-09-20T16:01:00Z"
BOARD_SEALED = "2026-09-20T16:02:00Z"
SHA = "a" * 64


def _record(market):
    base = {
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
        "source_url": "https://example.test",
        "market": market,
        "away_team": "Miami Dolphins",
        "home_team": "Buffalo Bills",
    }
    if market == "spread":
        base.update({
            "away_line": 3.0,
            "home_line": -3.0,
            "away_odds": -110,
            "home_odds": -110,
            "away_selection_id": "a-spread",
            "home_selection_id": "h-spread",
        })
    elif market == "game_total":
        base.update({
            "line": 45.5,
            "over_odds": -105,
            "under_odds": -115,
            "over_selection_id": "over-total",
            "under_selection_id": "under-total",
        })
    elif market == "moneyline":
        base.update({
            "away_odds": 130,
            "home_odds": -150,
            "away_selection_id": "a-ml",
            "home_selection_id": "h-ml",
        })
    return base


def _snapshot(records=None, failures=None):
    rows = records if records is not None else [
        _record("moneyline"), _record("spread"), _record("game_total")
    ]
    return seal_game_market_snapshot(
        rows,
        [] if failures is None else failures,
        event_id=EVENT,
        sealed_at=MARKET_SEALED,
    )


def _prediction(**overrides):
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
        "predicted_home_points": 25.0,
        "predicted_away_points": 20.0,
        "predicted_home_margin": 5.0,
        "predicted_total": 45.0,
        "baseline_name": "GAME_MARKET_B0_PRIOR_SCORING_BLEND",
        "min_prior_games": 3,
        "uses_market_line_as_feature": False,
        "uses_current_game_outcome_as_feature": False,
        "home_field_adjustment": 0.0,
    }
    row.update(overrides)
    return row


class GameMarketShadowBoardTests(unittest.TestCase):
    def test_spread_and_total_side_calls_are_bound_to_real_price(self):
        board = build_game_market_shadow_board(
            _snapshot(),
            _prediction(),
            model_code_sha="deadbeef",
            source_vintage="fixture-v1",
            sealed_at=BOARD_SEALED,
        )
        rows = {row["market"]: row for row in board["records"]}
        self.assertEqual(rows["spread"]["decision_status"], "SHADOW_ONLY")
        self.assertEqual(rows["spread"]["selected_side"], "HOME")
        self.assertEqual(rows["spread"]["selected_line"], -3.0)
        self.assertEqual(rows["spread"]["selected_odds"], -110)
        self.assertEqual(rows["spread"]["model_edge_points"], 2.0)
        self.assertEqual(rows["game_total"]["selected_side"], "UNDER")
        self.assertEqual(rows["game_total"]["selected_odds"], -115)
        self.assertAlmostEqual(rows["game_total"]["model_edge_points"], 0.5)
        self.assertFalse(board["public_eligible"])
        self.assertTrue(board["research_only"])

    def test_missing_target_market_becomes_explicit_no_play(self):
        snap = _snapshot(
            records=[_record("moneyline"), _record("spread")],
            failures=[{
                "event_id": EVENT,
                "market": "game_total",
                "reason": "MALFORMED",
            }],
        )
        board = build_game_market_shadow_board(
            snap,
            _prediction(),
            model_code_sha="deadbeef",
            source_vintage="fixture-v1",
            sealed_at=BOARD_SEALED,
        )
        rows = {row["market"]: row for row in board["records"]}
        self.assertEqual(rows["game_total"]["decision_status"], "NO_PLAY")
        self.assertEqual(rows["game_total"]["reason"], "MARKET_NOT_NORMALIZED")

    def test_model_ineligibility_is_no_play_not_disappearance(self):
        board = build_game_market_shadow_board(
            _snapshot(),
            _prediction(
                eligibility="INSUFFICIENT_HISTORY",
                predicted_home_margin=None,
                predicted_total=None,
            ),
            model_code_sha="deadbeef",
            source_vintage="fixture-v1",
            sealed_at=BOARD_SEALED,
        )
        self.assertEqual(
            {row["decision_status"] for row in board["records"]},
            {"NO_PLAY"},
        )
        self.assertEqual(
            {row["reason"] for row in board["records"]},
            {"INSUFFICIENT_HISTORY"},
        )

    def test_team_identity_mismatch_fails_closed(self):
        with self.assertRaisesRegex(GameMarketShadowBoardError, "home team identity mismatch"):
            build_game_market_shadow_board(
                _snapshot(),
                _prediction(home_team_full="Kansas City Chiefs"),
                model_code_sha="deadbeef",
                source_vintage="fixture-v1",
                sealed_at=BOARD_SEALED,
            )

    def test_rejected_model_contract_fails_closed(self):
        with self.assertRaisesRegex(GameMarketShadowBoardError, "B0 control only"):
            build_game_market_shadow_board(
                _snapshot(),
                _prediction(baseline_name="C2_REJECTED"),
                model_code_sha="deadbeef",
                source_vintage="fixture-v1",
                sealed_at=BOARD_SEALED,
            )

    def test_board_cannot_precede_market_seal(self):
        with self.assertRaisesRegex(GameMarketShadowBoardError, "before market snapshot"):
            build_game_market_shadow_board(
                _snapshot(),
                _prediction(),
                model_code_sha="deadbeef",
                source_vintage="fixture-v1",
                sealed_at="2026-09-20T16:00:30Z",
            )

    def test_tamper_detection_rebuilds_canonical_board(self):
        snap = _snapshot()
        pred = _prediction()
        board = build_game_market_shadow_board(
            snap,
            pred,
            model_code_sha="deadbeef",
            source_vintage="fixture-v1",
            sealed_at=BOARD_SEALED,
        )
        self.assertEqual(
            verify_game_market_shadow_board(board, snap, pred),
            board["board_sha256"],
        )
        tampered = deepcopy(board)
        tampered["records"][0]["selected_odds"] = 999
        with self.assertRaisesRegex(GameMarketShadowBoardError, "hash/content mismatch"):
            verify_game_market_shadow_board(tampered, snap, pred)


if __name__ == "__main__":
    unittest.main(verbosity=2)
