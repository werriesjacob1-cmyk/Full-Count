#!/usr/bin/env python3
"""Fail-closed contracts for the nflverse quarantine ledger."""
import csv
import hashlib
import json
import shutil
import unittest
import uuid
from pathlib import Path

from nfl.research.nflverse_full_audit import NUMERIC
from nfl.research.nflverse_quarantine import (
    MISSING_DISPLAY_POSITION,
    MISSING_IDENTITY_OFFENSE,
    MISSING_OPPONENT,
    STRUCTURAL_ZERO,
    build_ledger,
    ledger_entry,
)


def sample_row(player_id="p1", **overrides):
    row = {
        "player_id": player_id,
        "player_name": "Quarter Back" if player_id else "",
        "player_display_name": "Quarter Back" if player_id else "",
        "position": "QB" if player_id else "",
        "season": "2025",
        "week": "1",
        "season_type": "REG",
        "game_id": "2025_01_A_B",
        "team": "A",
        "opponent_team": "B",
    }
    row.update({field: "0" for field in NUMERIC})
    row.update(overrides)
    return row


class RowClassificationTests(unittest.TestCase):
    def test_structural_zero_is_explicitly_excluded(self):
        entry = ledger_entry(sample_row(""), "asset.csv", "a" * 64, 2)
        self.assertEqual(entry["reasons"], [STRUCTURAL_ZERO])
        self.assertEqual(entry["disposition"], "EXCLUDED_STRUCTURAL_ZERO")
        self.assertEqual(entry["allowed_uses"], ["source_completeness_accounting"])

    def test_missing_identity_with_offense_has_no_allowed_use(self):
        entry = ledger_entry(
            sample_row("", passing_yards="42"), "asset.csv", "b" * 64, 7
        )
        self.assertEqual(entry["reasons"], [MISSING_IDENTITY_OFFENSE])
        self.assertEqual(entry["allowed_uses"], [])
        self.assertEqual(entry["nonzero_offense"], {"passing_yards": "42"})

    def test_multiple_blocking_reasons_are_sorted_and_stable(self):
        row = sample_row("p1", player_display_name="", position="", opponent_team="")
        one = ledger_entry(row, "asset.csv", "c" * 64, 9)
        two = ledger_entry(dict(reversed(list(row.items()))), "asset.csv", "c" * 64, 9)
        self.assertEqual(one["reasons"], [MISSING_DISPLAY_POSITION, MISSING_OPPONENT])
        self.assertEqual(one["quarantine_id"], two["quarantine_id"])

    def test_clean_row_has_no_ledger_entry(self):
        self.assertIsNone(ledger_entry(sample_row(), "asset.csv", "d" * 64, 2))

    def test_structural_row_with_missing_team_becomes_a_blocker(self):
        entry = ledger_entry(
            sample_row("", team=""), "asset.csv", "e" * 64, 3
        )
        self.assertEqual(entry["reasons"], ["MISSING_TEAM", STRUCTURAL_ZERO])
        self.assertEqual(entry["disposition"], "QUARANTINED_RESEARCH_BLOCKER")
        self.assertEqual(entry["allowed_uses"], [])

    def test_blank_audited_numeric_field_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "blank audited numeric field"):
            ledger_entry(
                sample_row("p1", passing_yards=""), "asset.csv", "f" * 64, 4
            )


class LedgerVerificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / f".test-nflverse-quarantine-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_fixture(self, root: Path):
        cache = root / "cache"
        cache.mkdir()
        source = cache / "stats_player_week_2025.csv"
        rows = [sample_row(), sample_row("", passing_yards="1")]
        fields = list(rows[0])
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        audit = {
            "audited_at": "2026-09-14T00:00:00Z",
            "source": {"repository": "nflverse/nflverse-data"},
            "summary": {"rows": 2},
            "seasons": [{
                "season": 2025,
                "bytes": source.stat().st_size,
                "sha256": digest,
                "blank_id_structural_zero_rows": 0,
                "sentinel_zero_id_structural_rows": 0,
                "missing_identity_rows_with_offense": 1,
            }],
        }
        audit_path = root / "audit.json"
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
        return audit_path, cache, source

    def test_build_verifies_sources_and_emits_blocker(self):
        audit, cache, _ = self.write_fixture(self.root)
        ledger = build_ledger(audit, cache)
        self.assertEqual(ledger["summary"]["source_assets_verified"], 1)
        self.assertEqual(ledger["summary"]["reason_counts"], {
            MISSING_IDENTITY_OFFENSE: 1
        })
        self.assertEqual(ledger["entries"][0]["allowed_uses"], [])

    def test_source_digest_drift_fails_closed(self):
        audit, cache, source = self.write_fixture(self.root)
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source size drift"):
            build_ledger(audit, cache)


if __name__ == "__main__":
    unittest.main()
