#!/usr/bin/env python3
import csv
import hashlib
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from nfl.research.nflverse_game_lines_audit import (
    SOURCE_CLASS,
    audit_closing,
    audit_games,
    audit_initial,
    verify_manifest,
)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@contextmanager
def scratch_file(label: str):
    # This runner blocks access to directories created by tempfile on Windows,
    # while ordinary workspace files remain writable. Keep fixtures in the
    # workspace and always remove them.
    path = Path.cwd() / f".tmp-{uuid.uuid4().hex}-{label}"
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def game(game_id: str = "2025_01_AAA_BBB") -> dict:
    return {
        "game_id": game_id, "season": "2025", "gameday": "2025-09-01",
        "away_team": "AAA", "home_team": "BBB", "away_score": "20",
        "home_score": "24", "result": "4", "total": "44",
        "spread_line": "-3.5", "away_spread_odds": "-110",
        "home_spread_odds": "-110", "total_line": "43.5",
        "under_odds": "-105", "over_odds": "-115",
    }


class GameLineSourceAuditTests(unittest.TestCase):
    def test_games_audit_preserves_unknown_book_classification(self):
        self.assertEqual(SOURCE_CLASS, "NFLVERSE_SCHEDULE_UNKNOWN_BOOK")
        with scratch_file("games.csv") as path:
            write_csv(path, [game()])
            result = audit_games(path)
        self.assertEqual(result["settled"], 1)
        self.assertFalse(result["has_sportsbook_column"])
        self.assertFalse(result["has_line_timestamp_column"])

    def test_games_audit_rejects_duplicate_game_id(self):
        with scratch_file("games.csv") as path:
            write_csv(path, [game(), game()])
            with self.assertRaisesRegex(ValueError, "duplicate game_id"):
                audit_games(path)

    def test_games_audit_rejects_inconsistent_outcome(self):
        row = game()
        row["total"] = "45"
        with scratch_file("games.csv") as path:
            write_csv(path, [row])
            with self.assertRaisesRegex(ValueError, "score/result inconsistencies"):
                audit_games(path)

    def test_closing_requires_two_coherent_runners(self):
        rows = [
            {"game_id": "1", "alt_game_id": "2025_01_AAA_BBB", "type": "SPREAD", "side": "AAA", "line": "3.5", "odds": "-110", "outcome": "1"},
            {"game_id": "1", "alt_game_id": "2025_01_AAA_BBB", "type": "SPREAD", "side": "BBB", "line": "-3.5", "odds": "-110", "outcome": "0"},
        ]
        with scratch_file("closing.csv") as path:
            write_csv(path, rows)
            self.assertEqual(audit_closing(path)["coherence_issues"], {})
            rows[1]["line"] = "-2.5"
            write_csv(path, rows)
            with self.assertRaisesRegex(ValueError, "spread_not_opposing"):
                audit_closing(path)

    def test_initial_identifies_narrow_source_without_inventing_odds(self):
        rows = [
            {"season": "2021", "sportsbook": "WSGT", "type": "TOTAL", "about": "g1", "side": "Over", "line": "44.5"},
            {"season": "2021", "sportsbook": "WSGT", "type": "TOTAL", "about": "g1", "side": "Under", "line": "44.5"},
        ]
        with scratch_file("initial.csv") as path:
            write_csv(path, rows)
            result = audit_initial(path)
        self.assertEqual(result["sportsbooks"], {"WSGT": 2})
        self.assertFalse(result["has_odds_column"])
        self.assertFalse(result["has_line_timestamp_column"])

    def test_manifest_digest_is_enforced(self):
        with scratch_file("games.csv") as path:
            path.write_text("x", encoding="utf-8")
            good = hashlib.sha256(b"x").hexdigest()
            verify_manifest(path, {"files": {"games": {"sha256": good}}}, "games")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                verify_manifest(path, {"files": {"games": {"sha256": "0" * 64}}}, "games")


if __name__ == "__main__":
    unittest.main()
