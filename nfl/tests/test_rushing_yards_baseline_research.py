#!/usr/bin/env python3
"""Contracts for the offline rushing-yards baseline + challenger research script."""
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from nfl.research.rushing_yards_baseline_research import (
    RushingYardsDataError,
    load_rusher_rows,
    metrics,
    paired_delta,
    rolling_predictions,
)


CSV_FIELDS = [
    "player_id", "player_display_name", "position", "season", "week",
    "season_type", "game_id", "team", "carries", "rushing_yards",
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
        "player_display_name": "Running Back",
        "position": "RB",
        "season": "2025",
        "week": "1",
        "season_type": "REG",
        "game_id": "2025_01_A_B",
        "team": "A",
        "carries": "10",
        "rushing_yards": "40",
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
        rows = season_rows.get(season, [base_row(season=str(season), carries="0", rushing_yards="0")])
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


class LoadRusherRowsTests(unittest.TestCase):
    def test_role_gate_excludes_zero_carry_rows_regardless_of_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            rows_2025 = [
                base_row(player_id="rb1", position="RB", carries="12", rushing_yards="55"),
                # A QB scramble carry: real role signal despite a passer label.
                base_row(player_id="qb1", position="QB", carries="3", rushing_yards="20"),
                # A receiver with no carries this week: excluded.
                base_row(player_id="wr1", position="WR", carries="0", rushing_yards="0"),
            ]
            audit = build_full_corpus(tmp_dir, {2025: rows_2025})
            rows, invariants, coverage = load_rusher_rows(tmp_dir, audit)
            player_ids = {row["player_id"] for row in rows if row["season"] == 2025}
            self.assertIn("rb1", player_ids)
            self.assertIn("qb1", player_ids)
            self.assertNotIn("wr1", player_ids)

    def test_zero_carries_with_nonzero_yards_is_counted_and_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # A real nflverse quirk (e.g. a lateral/fumble-return credited as
            # rushing yardage without a charted carry): counted as an
            # invariant failure, never silently dropped or fabricated into a
            # role-positive row.
            quirky_row = base_row(player_id="wr2", carries="0", rushing_yards="12")
            audit = build_full_corpus(tmp_dir, {2025: [quirky_row]})
            rows, invariants, coverage = load_rusher_rows(tmp_dir, audit)
            kept = [row for row in rows if row["season"] == 2025]
            self.assertEqual(len(kept), 0)
            self.assertEqual(invariants["production_with_zero_carries"], 1)

    def test_byte_size_drift_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["seasons"][0]["bytes"] += 1
            with self.assertRaisesRegex(RushingYardsDataError, "byte size drift"):
                load_rusher_rows(tmp_dir, audit)

    def test_sha256_drift_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["seasons"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(RushingYardsDataError, "SHA-256 drift"):
                load_rusher_rows(tmp_dir, audit)

    def test_wrong_season_count_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["seasons"] = audit["seasons"][:26]
            with self.assertRaisesRegex(RushingYardsDataError, "27 seasons"):
                load_rusher_rows(tmp_dir, audit)

    def test_missing_identity_population_drift_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            audit["summary"]["missing_identity_rows_with_offense"] = 8
            with self.assertRaisesRegex(RushingYardsDataError, "missing-identity population drift"):
                load_rusher_rows(tmp_dir, audit)

    def test_missing_cached_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir)
            (tmp_dir / "stats_player_week_1999.csv").unlink()
            with self.assertRaisesRegex(RushingYardsDataError, "missing"):
                load_rusher_rows(tmp_dir, audit)

    def test_reproducible_digest_across_identical_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            audit = build_full_corpus(tmp_dir, {
                2025: [
                    base_row(player_id="rb1", week="1"),
                    base_row(player_id="rb1", week="2", carries="15", rushing_yards="70"),
                ],
            })
            rows_a, invariants_a, coverage_a = load_rusher_rows(tmp_dir, audit)
            rows_b, invariants_b, coverage_b = load_rusher_rows(tmp_dir, audit)
            self.assertEqual(rows_a, rows_b)
            self.assertEqual(invariants_a, invariants_b)
            self.assertEqual(coverage_a, coverage_b)


class RollingPredictionTests(unittest.TestCase):
    def test_prediction_is_prior_safe_and_never_leaks_current_row(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "carries": 10.0, "rushing_yards": 40.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "carries": 12.0, "rushing_yards": 55.0},
            {"player_id": "p1", "season": 2025, "week": 3, "season_type": "REG", "game_id": "g3", "carries": 8.0, "rushing_yards": 30.0},
            {"player_id": "p1", "season": 2025, "week": 4, "season_type": "REG", "game_id": "g4", "carries": 20.0, "rushing_yards": 999.0},
        ]
        result = rolling_predictions(rows)[-1]
        # b0 for the final row must average only the three PRIOR appearances,
        # never leaking the current row's own 999-yard outcome into itself.
        self.assertAlmostEqual(result["b0"], (40.0 + 55.0 + 30.0) / 3.0)
        self.assertEqual(result["actual"], 999.0)
        self.assertNotEqual(result["b0"], 999.0)

    def test_c1_is_carries3_times_aggregate_ypc8(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": w, "season_type": "REG", "game_id": f"g{w}", "carries": 10.0, "rushing_yards": 40.0}
            for w in range(1, 4)
        ] + [{"player_id": "p1", "season": 2025, "week": 4, "season_type": "REG", "game_id": "g4", "carries": 5.0, "rushing_yards": 500.0}]
        result = rolling_predictions(rows)[-1]
        # Three prior appearances: carries all 10.0 -> carries_3 mean = 10.0.
        # Aggregate ypc over the same three prior appearances: 120 yards / 30 carries = 4.0.
        self.assertAlmostEqual(result["c1_carries3_times_ypc8"], 10.0 * 4.0)

    def test_minimum_three_appearances_required(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "carries": 10.0, "rushing_yards": 40.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "carries": 10.0, "rushing_yards": 40.0},
        ]
        scored = rolling_predictions(rows)
        self.assertIsNone(scored[0]["b0"])
        self.assertIsNone(scored[1]["b0"])
        self.assertIsNone(scored[0]["c1_carries3_times_ypc8"])
        self.assertIsNone(scored[1]["c1_carries3_times_ypc8"])

    def test_only_last_five_appearances_used_for_b0(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": w, "season_type": "REG", "game_id": f"g{w}", "carries": 10.0, "rushing_yards": 200.0 if w == 1 else 5.0}
            for w in range(1, 8)
        ]
        result = rolling_predictions(rows)[-1]
        self.assertAlmostEqual(result["b0"], 5.0)

    def test_post_season_appearances_feed_history_but_are_not_scored(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "carries": 10.0, "rushing_yards": 40.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "carries": 10.0, "rushing_yards": 40.0},
            {"player_id": "p1", "season": 2025, "week": 20, "season_type": "POST", "game_id": "g3", "carries": 10.0, "rushing_yards": 200.0},
            {"player_id": "p1", "season": 2026, "week": 1, "season_type": "REG", "game_id": "g4", "carries": 10.0, "rushing_yards": 40.0},
        ]
        scored = rolling_predictions(rows)
        scored_game_ids = [row["game_id"] for row in scored]
        self.assertNotIn("g3", scored_game_ids)
        last = scored[-1]
        self.assertEqual(last["game_id"], "g4")
        self.assertAlmostEqual(last["b0"], (40.0 + 40.0 + 200.0) / 3.0)

    def test_zero_carry_history_entry_does_not_divide_by_zero(self):
        # This row would only reach rolling_predictions if it survived
        # load_rusher_rows's carries > 0 gate, but rolling_predictions
        # itself must stay fail-closed (no ZeroDivisionError, no fabricated
        # value) even if a zero-carry row is ever passed in directly.
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "carries": 0.0, "rushing_yards": 0.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "carries": 0.0, "rushing_yards": 0.0},
            {"player_id": "p1", "season": 2025, "week": 3, "season_type": "REG", "game_id": "g3", "carries": 0.0, "rushing_yards": 0.0},
            {"player_id": "p1", "season": 2025, "week": 4, "season_type": "REG", "game_id": "g4", "carries": 5.0, "rushing_yards": 20.0},
        ]
        result = rolling_predictions(rows)[-1]
        self.assertIsNone(result["c1_carries3_times_ypc8"])


class MetricsAndPairedDeltaTests(unittest.TestCase):
    def test_metrics_native_shape(self):
        rows = [
            {"actual": 50.0, "b0": 40.0},
            {"actual": 30.0, "b0": 50.0},
            {"actual": 20.0, "b0": None},
        ]
        result = metrics(rows, "b0")
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["mae"], 15.0)
        self.assertAlmostEqual(result["bias_prediction_minus_actual"], 5.0)

    def test_paired_delta_uses_only_common_population(self):
        rows = [
            {"actual": 100.0, "b0": 90.0, "challenger": 80.0},
            {"actual": 100.0, "b0": 0.0, "challenger": None},
        ]
        result = paired_delta(rows, "challenger")
        self.assertEqual(result["n"], 1)
        self.assertEqual(result["mae_delta_vs_b0"], 10.0)


if __name__ == "__main__":
    unittest.main()
