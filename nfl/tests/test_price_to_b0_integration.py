"""Adversarial boundary tests using the authentic September 24 book archive."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from nfl.prospective.shadow_snapshot import seal_snapshot
from nfl.research.price_aware_offer_capture import match_authoritative_b0, verify_capture
from nfl.research.price_to_b0_integration import (
    _b0_prices, integrate, raw_offer_matches, validate_b0,
)

ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / "engineering/nfl_price_b0_join_20260924/capture_01"


def sample():
    frozen = verify_capture(CAPTURE)
    original = next(r for r in frozen["records"] if r["candidate"]["shape"] == "primary")
    candidate = original["candidate"]
    b0_row = {
        "event_id": candidate["event_id"], "market_id": candidate["market_id"],
        "gsis_id": candidate["gsis_id"], "market": "receptions",
        "player_name": candidate["player_name"], "team": candidate["team"],
        "line": candidate["line"], "over_odds": candidate["over_odds"],
        "under_odds": candidate["under_odds"],
        "captured_at": "2026-09-24T14:40:00Z",
        "availability_status": "NOT_LISTED_INACTIVE",
        "decision_status": "SHADOW_ONLY", "model_projection": 3.2,
        "model_over_probability": .55, "model_under_probability": .45,
    }
    return frozen, original, b0_row


def board(row, *, sealed_at="2026-09-24T14:41:00Z"):
    return seal_snapshot([row], slate_date="2026-09-24", code_sha="test",
                         source_vintage="synthetic-boundary-fixture", sealed_at=sealed_at)


class PriceToB0Tests(unittest.TestCase):
    def test_real_capture_raw_binding_and_missing_evidence_quarantine(self):
        frozen, original, _ = sample()
        self.assertEqual(len(frozen["records"]), 57)
        self.assertTrue(raw_offer_matches(CAPTURE, frozen["sources"],
                                          original["candidate"], original["source_sha256"]))
        target = CAPTURE.parent / "test_integration_temporary.json"
        target.unlink(missing_ok=True)
        try:
            result = integrate(CAPTURE, target, as_of="2026-09-24T14:54:28Z")
            self.assertEqual(result["counts"], {"QUARANTINED": 57})
            self.assertEqual(result["b0_snapshot_sha256"], None)
            self.assertTrue(all(not r["bettable"] for r in result["records"]))
            self.assertTrue(all("QUOTE_TIMESTAMP_NOT_PROVIDED" in r["reasons"]
                                and "BOOK_ACTION_RULES_NOT_CERTIFIED" in r["reasons"]
                                and "UNKNOWN_GAME_COVERAGE" in r["reasons"]
                                and "AUTHORITATIVE_B0_SNAPSHOT_UNAVAILABLE" in r["reasons"]
                                for r in result["records"]))
            with self.assertRaises(FileExistsError):
                integrate(CAPTURE, target, as_of="2026-09-24T14:54:28Z")
        finally:
            target.unlink(missing_ok=True)

    def test_earlier_sealed_b0_prices_only_exact_primary_offer(self):
        frozen, original, row = sample()
        candidate = original["candidate"]
        shadow = board(row)
        validate_b0(shadow)
        self.assertIsNotNone(match_authoritative_b0(candidate, shadow))
        b0_path = CAPTURE.parent / "test_b0_temporary.json"
        target = CAPTURE.parent / "test_joined_temporary.json"
        b0_path.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        try:
            b0_path.write_text(json.dumps({"snapshot": shadow}), encoding="utf-8")
            result = integrate(CAPTURE, target, b0_path=b0_path,
                               as_of="2026-09-24T14:54:28Z")
        finally:
            b0_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
        joined = [r for r in result["records"] if r["b0_observation_id"]]
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["candidate"]["market_id"], candidate["market_id"])
        self.assertEqual(len(joined[0]["b0_prices"]), 2)
        self.assertAlmostEqual(joined[0]["b0_prices"][0]["model_win_probability"], .55)
        self.assertIn("QUOTE_TIMESTAMP_NOT_PROVIDED", joined[0]["reasons"])
        self.assertIn("CURRENT_ROLE_NOT_VERIFIED", joined[0]["reasons"])
        self.assertIn("BOOK_ACTION_RULES_NOT_CERTIFIED", joined[0]["reasons"])
        self.assertFalse(joined[0]["bettable"])
        self.assertTrue(all(not r["b0_observation_id"] for r in result["records"]
                            if r["candidate"]["shape"] != "primary"))

    def test_later_b0_and_identity_price_mismatch_do_not_join(self):
        _, original, row = sample()
        candidate = original["candidate"]
        late = board(row, sealed_at="2026-09-24T15:00:00Z")
        self.assertIsNone(match_authoritative_b0(candidate, late))
        late_path = CAPTURE.parent / "test_late_b0_temporary.json"
        target = CAPTURE.parent / "test_late_join_temporary.json"
        late_path.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        try:
            late_path.write_text(json.dumps(late), encoding="utf-8")
            late_result = integrate(CAPTURE, target, b0_path=late_path,
                                    as_of="2026-09-24T14:54:28Z")
            self.assertTrue(all("B0_SEALED_AFTER_OFFER" in r["reasons"]
                                for r in late_result["records"]))
            self.assertFalse(any(r["b0_observation_id"] for r in late_result["records"]))
        finally:
            late_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
        for change in ({"event_id": "wrong"}, {"gsis_id": "wrong"},
                       {"market": "passing_yards"}, {"market_id": "wrong"},
                       {"line": row["line"]+1}, {"over_odds": row["over_odds"]+1},
                       {"under_odds": row["under_odds"]-1}):
            with self.subTest(change=change):
                self.assertIsNone(match_authoritative_b0(candidate, board({**row, **change})))

    def test_duplicate_selection_and_corrupted_bytes_fail_closed(self):
        frozen, original, row = sample()
        bad = copy.deepcopy(original["candidate"])
        bad["under_selection_id"] = bad["over_selection_id"]
        self.assertFalse(raw_offer_matches(CAPTURE, frozen["sources"], bad,
                                           original["source_sha256"]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            seal_snapshot([row, row], slate_date="2026-09-24", code_sha="test",
                          source_vintage="synthetic", sealed_at="2026-09-24T14:41:00Z")
        original_read = Path.read_text
        def corrupt(path, *args, **kwargs):
            content = original_read(path, *args, **kwargs)
            if path.name == "receiving-props.raw.json":
                envelope = json.loads(content)
                envelope["sha256"] = "0"*64
                return json.dumps(envelope)
            return content
        with patch.object(Path, "read_text", corrupt):
            with self.assertRaisesRegex(ValueError, "envelope mismatch"):
                verify_capture(CAPTURE)

    def test_invalid_probability_is_rejected(self):
        _, original, row = sample()
        with self.assertRaisesRegex(ValueError, "probabilities invalid"):
            _b0_prices(original["candidate"], {**row, "model_over_probability": .8})

    def test_stale_offer_is_not_presented_as_current(self):
        target = CAPTURE.parent / "test_stale_temporary.json"
        target.unlink(missing_ok=True)
        try:
            result = integrate(CAPTURE, target, as_of="2026-09-24T15:30:00Z")
            self.assertEqual(result["counts"], {"NO_PLAY": 57})
            self.assertTrue(all(not r["bettable"] for r in result["records"]))
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
