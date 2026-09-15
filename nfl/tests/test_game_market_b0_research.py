#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from nfl.research import game_market_b0_research as research


HEADER = (
    "game_id,season,game_type,week,away_team,away_score,home_team,home_score,"
    "result,total,spread_line,total_line\n"
)


class GameMarketB0ResearchRunnerTests(unittest.TestCase):
    def _write_and_pin(self, body: str):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "games.csv"
        path.write_text(HEADER + body, encoding="utf-8")
        original_bytes = research.PINNED_SCHEDULE_SOURCE["bytes"]
        original_sha = research.PINNED_SCHEDULE_SOURCE["sha256"]
        research.PINNED_SCHEDULE_SOURCE["bytes"] = path.stat().st_size
        research.PINNED_SCHEDULE_SOURCE["sha256"] = research.sha256_file(path)
        return tmp, path, original_bytes, original_sha

    def _restore(self, original_bytes, original_sha):
        research.PINNED_SCHEDULE_SOURCE["bytes"] = original_bytes
        research.PINNED_SCHEDULE_SOURCE["sha256"] = original_sha

    def test_history_includes_games_even_when_closing_line_missing(self):
        tmp, path, old_bytes, old_sha = self._write_and_pin(
            "2024_01_KC_DEN,2024,REG,1,KC,20,DEN,24,4,44,,\n"
            "2024_02_LV_DEN,2024,REG,2,LV,17,DEN,21,4,38,3.5,41.5\n"
        )
        try:
            scoring, market, counts = research.load_pinned_historical_rows(path)
        finally:
            self._restore(old_bytes, old_sha)
            tmp.cleanup()
        self.assertEqual(len(scoring), 2)
        self.assertEqual(len(market), 1)
        self.assertEqual(counts["historical_reg_final_rows"], 2)
        self.assertEqual(counts["historical_reg_rows_missing_closing_market"], 1)
        self.assertEqual(counts["historical_reg_rows_with_closing_market"], 1)
        self.assertTrue(all(row["final_status"] == "FINAL" for row in scoring))

    def test_result_inconsistency_fails_closed(self):
        tmp, path, old_bytes, old_sha = self._write_and_pin(
            "2024_01_KC_DEN,2024,REG,1,KC,20,DEN,24,999,44,3.5,41.5\n"
        )
        try:
            with self.assertRaisesRegex(ValueError, "result inconsistency"):
                research.load_pinned_historical_rows(path)
        finally:
            self._restore(old_bytes, old_sha)
            tmp.cleanup()

    def test_post_cutoff_current_season_is_not_used_as_historical_final(self):
        tmp, path, old_bytes, old_sha = self._write_and_pin(
            "2026_01_KC_DEN,2026,REG,1,KC,,DEN,,,,3.5,46.5\n"
        )
        try:
            scoring, market, counts = research.load_pinned_historical_rows(path)
        finally:
            self._restore(old_bytes, old_sha)
            tmp.cleanup()
        self.assertEqual(scoring, [])
        self.assertEqual(market, [])
        self.assertEqual(counts["post_cutoff_reg_rows"], 1)

    def test_asset_digest_drift_stops_before_parsing(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "games.csv"
        path.write_text(HEADER, encoding="utf-8")
        old_bytes = research.PINNED_SCHEDULE_SOURCE["bytes"]
        old_sha = research.PINNED_SCHEDULE_SOURCE["sha256"]
        research.PINNED_SCHEDULE_SOURCE["bytes"] = path.stat().st_size
        research.PINNED_SCHEDULE_SOURCE["sha256"] = "0" * 64
        try:
            with self.assertRaisesRegex(ValueError, "SHA-256 drift"):
                research.load_pinned_historical_rows(path)
        finally:
            self._restore(old_bytes, old_sha)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
