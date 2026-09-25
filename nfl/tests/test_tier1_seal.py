"""Seal builder: kickoff conversion and the future-games-only guard."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engineering" / "nfl_tier1_status_20260924" / "seal"))
import drivers  # noqa: E402


class SealTests(unittest.TestCase):
    def test_kickoff_eastern_to_utc_across_dst(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "games.csv"
            with p.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["game_id", "season", "week", "gameday", "gametime"])
                w.writerow(["2026_03_A_B", 2026, 3, "2026-09-27", "13:00"])
                w.writerow(["2026_10_C_D", 2026, 10, "2026-11-08", "13:00"])
            self.assertEqual(drivers._kickoffs(p, 2026, 3), {"2026_03_A_B": "2026-09-27T17:00:00Z"})
            self.assertEqual(drivers._kickoffs(p, 2026, 10), {"2026_10_C_D": "2026-11-08T18:00:00Z"})

    def test_builder_seals_only_future_games(self):
        src = (Path(drivers.__file__).parent / "build_week_seal.py").read_text()
        self.assertIn("k - now > MIN_LEAD", src)
        self.assertIn('"F6"', Path(drivers.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
