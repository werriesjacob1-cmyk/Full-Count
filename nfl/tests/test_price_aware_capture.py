"""Verify real archived bytes and replay mechanics without network access."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from nfl.research.price_aware_offer_capture import (
    verify_capture, replay_capture, add_unique_offer, match_authoritative_b0)
from nfl.prospective.shadow_snapshot import seal_snapshot

ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / "engineering/nfl_price_aware_20260923/capture_01"


class ArchiveTests(unittest.TestCase):
    def test_authoritative_b0_requires_exact_prior_offer(self):
        base = dict(event_id="e",market_id="m",gsis_id="p",market="receptions",
                    player_name="P",team="ATL",captured_at="2026-09-24T18:00:00Z",
                    availability_status="NOT_LISTED_INACTIVE",decision_status="SHADOW_ONLY",
                    line=3.5,over_odds=100,under_odds=-120,model_projection=3.2)
        shadow = seal_snapshot([base],slate_date="2026-09-24",code_sha="a",
                               source_vintage="s",sealed_at="2026-09-24T18:01:00Z")
        quote = dict(base,shape="primary",captured_at="2026-09-24T18:02:00Z")
        self.assertIsNotNone(match_authoritative_b0(quote,shadow))
        self.assertIsNone(match_authoritative_b0(dict(quote,over_odds=110),shadow))
        self.assertIsNone(match_authoritative_b0(dict(quote,gsis_id="other"),shadow))
        self.assertIsNone(match_authoritative_b0(dict(quote,captured_at="2026-09-24T18:00:30Z"),shadow))

    def test_cross_tab_conflict_blocks_offer(self):
        old = {"market_id":"m", "selection_id":"s", "event_id":"e",
               "player_name":"P", "threshold":3, "yes_odds":150}
        newer = dict(old, yes_odds=170)
        candidates, blocked = {}, set()
        add_unique_offer(candidates, blocked, old, "first")
        add_unique_offer(candidates, blocked, dict(old), "same")
        self.assertEqual(candidates[("m","s")][1], "first")
        add_unique_offer(candidates, blocked, newer, "conflict")
        self.assertEqual(candidates, {})
        add_unique_offer(candidates, blocked, old, "later")
        self.assertEqual(candidates, {})

    def test_real_archive_seals_and_zero_eligibility(self):
        snapshot = verify_capture(CAPTURE)
        self.assertEqual(snapshot["canonical_game_id"], "2026_03_ATL_GB")
        self.assertEqual(len(snapshot["records"]), 54)
        self.assertEqual({r["decision_status"] for r in snapshot["records"]}, {"QUARANTINED"})
        self.assertEqual(len({r["record_sha256"] for r in snapshot["records"]}), 54)
        self.assertTrue(snapshot["_verification"]["full_source_verification"])

    def test_corrupted_source_and_snapshot_fail(self):
        original = Path.read_text
        def corrupted(path, *args, **kwargs):
            data = original(path, *args, **kwargs)
            if path.name == "receiving-props.raw.json":
                value = json.loads(data)
                value["sha256"] = "0" * 64
                return json.dumps(value)
            return data
        with patch.object(Path, "read_text", corrupted):
            with self.assertRaisesRegex(ValueError, "envelope mismatch"):
                verify_capture(CAPTURE)

    def test_corrupted_history_bytes_fail_when_locally_present(self):
        original = Path.read_bytes
        def corrupted(path):
            if path.name == "stats_player_week_2025.csv":
                return b"corrupted"
            return original(path)
        with patch.object(Path, "read_bytes", corrupted):
            with self.assertRaisesRegex(ValueError, "external source bytes mismatch"):
                verify_capture(CAPTURE)

    def test_missing_declared_external_source_is_explicit(self):
        original = Path.exists
        def absent(path):
            if path.name == "stats_player_week_2025.csv":
                return False
            return original(path)
        with patch.object(Path, "exists", absent):
            self.assertIn("stats_player_week_2025.csv",
                          verify_capture(CAPTURE)["_verification"]["external_sources_missing"])
            with self.assertRaisesRegex(ValueError, "external sources absent"):
                verify_capture(CAPTURE, require_external_sources=True)

    def test_replay_keeps_original_cutoff_and_zero_eligibility(self):
        replay = ROOT/"engineering/nfl_price_aware_20260923/test_replay.json"
        replay.unlink(missing_ok=True)
        try:
            result = replay_capture(CAPTURE, replay)
            self.assertEqual(result["counts"], {"QUARANTINED": 54})
            with self.assertRaises(FileExistsError):
                replay_capture(CAPTURE, replay)
        finally:
            replay.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

