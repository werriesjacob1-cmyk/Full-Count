#!/usr/bin/env python3
"""Contracts for the offline receptions B0 baseline research script."""
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from nfl.research.receptions_baseline_research import (
    load_receiver_rows,
    metrics,
    rolling_predictions,
)


CSV_FIELDS = [
    "player_id", "player_display_name", "position", "season", "week",
    "season_type", "game_id", "team", "targets", "receptions",
    "receiving_yards", "receiving_tds",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def write_season_csv(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def base_row(**overrides):
    row = {
        "player_id": "p1",
        "player_display_name": "Wide Receiver",
        "position": "WR",
        "season": "2025",
        "week": "1",
        "season_type": "REG",
        "game_id": "2025_01_A_B",
        "team": "A",
        "targets": "5",
        "receptions": "3",
        "receiving_yards": "40",
        "receiving_tds": "0",
    }
    row.update(overrides)
    return row


def build_full_corpus(tmp_dir: Path, season_rows: dict[int, list[dict]] | None = None):
    """Write 27 minimal season CSVs (1999-2025) and a matching audit manifest.

    Any season not given explicit rows gets a single structurally-empty
    filler row so the on-disk corpus mirrors the real one (27 files present)
    without needing real data for the drift-detection tests below.
    """
    season_rows = season_rows or {}
    seasons = []
    for season in range(1999, 2026):
        rows = season_rows.get(season, [base_row(season=str(season))])
        path = tmp_dir / f"stats_player_week_{season}.csv"
        write_season_csv(path, rows)
        seasons.append({
            "season": season,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    audit = {
        "seasons": seasons,
        "summary": {"missing_identity_rows_with_offense": 7},
    }
    return audit


class LoadReceiverRowsTests(unittest.TestCase):
    def test_filters_by_role_not_position_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            rows_2025 = [
                base_row(player_id="wr1", position="WR", targets="6", receptions="4"),
                # An offensive lineman targeted on a trick play: real role
                # signal despite a non-skill position label.
                base_row(player_id="ot1", position="OT", targets="1", receptions="1"),
                # A linebacker with zero targets/receptions: excluded.
                base_row(player_id="lb1", position="LB", targets="0", receptions="0"),
            ]
            audit = build_full_corpus(tmp_dir, {2025: rows_2025})
            rows, invariants, coverage = load_receiver_rows(tmp_dir, audit)
            player_ids = {row["player_id"] for row in rows if row["season"] == 2025}
            self.assertIn("wr1", player_ids)
            self.assertIn("ot1", player_ids)
            self.assertNotIn("lb1", player_ids)

    def test_effective_targets_falls_back_to_receptions_when_targets_column_is_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # Simulates the confirmed 2003-2008 nflverse gap: targets reads 0
            # even though a real reception was recorded.
            broken_row = base_row(player_id="wr1", targets="0", receptions="5", receiving_yards="60")
            audit = build_full_corpus(tmp_dir, {2025: [broken_row]})
            rows, invariants, coverage = load_receiver_rows(tmp_dir, audit)
            kept = [row for row in rows if row["season"] == 2025]
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]["effective_targets"], 5.0)
            self.assertEqual(invariants["receptions_gt_targets"], 1)
            self.assertEqual(
                coverage["2025"]["raw_targets_column_unavailable_rows"], 1
            )

    def test_byte_size_drift_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["seasons"][0]["bytes"] += 1
            with self.assertRaisesRegex(ValueError, "byte size drift"):
                load_receiver_rows(tmp_dir, audit)

    def test_sha256_drift_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["seasons"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "SHA-256 drift"):
                load_receiver_rows(tmp_dir, audit)

    def test_wrong_season_count_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["seasons"] = audit["seasons"][:26]
            with self.assertRaisesRegex(ValueError, "27 seasons"):
                load_receiver_rows(tmp_dir, audit)

    def test_missing_identity_population_drift_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["summary"]["missing_identity_rows_with_offense"] = 8
            with self.assertRaisesRegex(ValueError, "missing-identity population drift"):
                load_receiver_rows(tmp_dir, audit)

    def test_reproducible_digest_across_identical_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir, {
                2025: [
                    base_row(player_id="wr1", week="1"),
                    base_row(player_id="wr1", week="2", targets="7", receptions="5"),
                ],
            })
            rows_a, invariants_a, coverage_a = load_receiver_rows(tmp_dir, audit)
            rows_b, invariants_b, coverage_b = load_receiver_rows(tmp_dir, audit)
            self.assertEqual(rows_a, rows_b)
            self.assertEqual(invariants_a, invariants_b)
            self.assertEqual(coverage_a, coverage_b)


class RollingPredictionTests(unittest.TestCase):
    def test_zero_role_history_is_excluded_and_target_is_prior_safe(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "position": "WR", "targets": 8.0, "effective_targets": 8.0, "receptions": 5.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "position": "WR", "targets": 6.0, "effective_targets": 6.0, "receptions": 4.0},
            {"player_id": "p1", "season": 2025, "week": 3, "season_type": "REG", "game_id": "g3", "position": "WR", "targets": 9.0, "effective_targets": 9.0, "receptions": 7.0},
            {"player_id": "p1", "season": 2025, "week": 4, "season_type": "REG", "game_id": "g4", "position": "WR", "targets": 3.0, "effective_targets": 3.0, "receptions": 2.0},
            {"player_id": "p1", "season": 2025, "week": 5, "season_type": "REG", "game_id": "g5", "position": "WR", "targets": 10.0, "effective_targets": 10.0, "receptions": 99.0},
        ]
        result = rolling_predictions(rows)[-1]
        # b0 for the final row must average only the four PRIOR appearances,
        # never leaking the current row's own receptions (99) into itself.
        self.assertAlmostEqual(result["b0"], (5.0 + 4.0 + 7.0 + 2.0) / 4.0)
        self.assertEqual(result["actual"], 99.0)

    def test_minimum_three_appearances_required(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
            {"player_id": "p1", "season": 2025, "week": 3, "season_type": "REG", "game_id": "g3", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
        ]
        scored = rolling_predictions(rows)
        self.assertIsNone(scored[0]["b0"])
        self.assertIsNone(scored[1]["b0"])
        # Third row still has only two PRIOR appearances (< 3), so b0 stays None.
        self.assertIsNone(scored[2]["b0"])

    def test_only_last_five_appearances_used(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": w, "season_type": "REG", "game_id": f"g{w}", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 100.0 if w == 1 else 2.0}
            for w in range(1, 8)
        ]
        result = rolling_predictions(rows)[-1]
        self.assertAlmostEqual(result["b0"], 2.0)

    def test_zero_effective_targets_window_stays_ineligible(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "position": "WR", "targets": 0.0, "effective_targets": 0.0, "receptions": 0.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "position": "WR", "targets": 0.0, "effective_targets": 0.0, "receptions": 0.0},
            {"player_id": "p1", "season": 2025, "week": 3, "season_type": "REG", "game_id": "g3", "position": "WR", "targets": 0.0, "effective_targets": 0.0, "receptions": 0.0},
            {"player_id": "p1", "season": 2025, "week": 4, "season_type": "REG", "game_id": "g4", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
        ]
        result = rolling_predictions(rows)[-1]
        self.assertIsNone(result["b0"])

    def test_post_season_appearances_feed_history_but_are_not_scored(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
            {"player_id": "p1", "season": 2025, "week": 20, "season_type": "POST", "game_id": "g3", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 10.0},
            {"player_id": "p1", "season": 2026, "week": 1, "season_type": "REG", "game_id": "g4", "position": "WR", "targets": 5.0, "effective_targets": 5.0, "receptions": 3.0},
        ]
        scored = rolling_predictions(rows)
        scored_game_ids = [row["game_id"] for row in scored]
        self.assertNotIn("g3", scored_game_ids)
        last = scored[-1]
        self.assertEqual(last["game_id"], "g4")
        self.assertAlmostEqual(last["b0"], (3.0 + 3.0 + 10.0) / 3.0)


class MetricsTests(unittest.TestCase):
    def test_metrics_native_shape(self):
        rows = [
            {"actual": 5.0, "b0": 4.0},
            {"actual": 3.0, "b0": 5.0},
            {"actual": 2.0, "b0": None},
        ]
        result = metrics(rows, "b0")
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["mae"], 1.5)
        self.assertAlmostEqual(result["bias_prediction_minus_actual"], 0.5)


if __name__ == "__main__":
    unittest.main()
