"""Verify real archived bytes and replay mechanics without network access."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from nfl.research.price_aware_offer_capture import verify_capture, replay_capture

ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / "engineering/nfl_price_aware_20260923/capture_01"


class ArchiveTests(unittest.TestCase):
    def test_real_archive_seals_and_zero_eligibility(self):
        snapshot = verify_capture(CAPTURE)
        self.assertEqual(snapshot["canonical_game_id"], "2026_03_ATL_GB")
        self.assertEqual(len(snapshot["records"]), 54)
        self.assertEqual({r["decision_status"] for r in snapshot["records"]}, {"QUARANTINED"})
        self.assertEqual(len({r["record_sha256"] for r in snapshot["records"]}), 54)

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

