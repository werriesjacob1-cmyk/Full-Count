"""Contract tests for the inactive FC-ACC-012 historical diagnostic."""
import json
import math
import tempfile
import unittest
from pathlib import Path

from backtest import hrrbi_pa_diagnostic as d


class HrrbiPaDiagnosticTests(unittest.TestCase):
    def test_slot_inversion_and_invalid_values(self):
        for slot in range(1, 10):
            signal = (9 - slot) * 100 / 8
            self.assertEqual(d.order_from_signal(signal), slot)
        for bad in (None, "bad", float("nan"), -100, 1000):
            self.assertIsNone(d.order_from_signal(bad))

    def test_fit_detects_pa_signal_without_component_independence(self):
        rows = []
        for i in range(400):
            pa = 3.4 if i % 2 else 4.5
            # Composite binary event; no independent H/R/RBI fields exist.
            y = int(i % 10 < (2 if pa < 4 else 7))
            rows.append({"p": 0.45, "pa_expected": pa, "y": y})
        control = d.fit(rows, False)
        challenger = d.fit(rows, True)
        self.assertEqual(len(control), 2)
        self.assertGreater(challenger[2], 0)
        self.assertGreater(d.predict({"p": 0.45, "pa_expected": 4.5}, challenger),
                           d.predict({"p": 0.45, "pa_expected": 3.4}, challenger))

    def test_no_later_outcomes_in_pa_training_and_conflict_rejected(self):
        rows = []
        for slot in range(1, 10):
            for i in range(50):
                rows.append({"date": "2024-06-01", "game_pk": i, "player_id": slot,
                             "actual_pa": 5 if slot <= 3 else 3,
                             "signals": {"lineup_slot": (9 - slot) * 100 / 8}})
        means, counts = d.training_pa_means(rows)
        self.assertEqual(counts[1], 50)
        later = dict(rows[0], date="2025-06-01", actual_pa=0)
        self.assertEqual(d.training_pa_means(rows + [later])[0], means)
        conflict = dict(rows[0], actual_pa=1)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            d.training_pa_means(rows + [conflict])

    def test_duplicate_and_outcome_mismatch_fail_closed(self):
        row = {"date": "2025-06-01", "game_pk": 1, "player_id": 2,
               "prop_type": "hits_runs_rbis", "needs": 2, "outcome": 1,
               "actual": 1, "predicted_prob": 0.6,
               "code_git_sha": "6b748538", "backtest_generated_at": "2026-08-25T00:00:00Z"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "threshold/outcome mismatch"):
                d.load(path)
            row["actual"] = 2
            path.write_text((json.dumps(row) + "\n") * 2, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                d.load(path)

    def test_same_n_counts_swaps_and_no_volume_inflation(self):
        rows = [
            {"date": "2025-06-01", "needs": 2, "key": (1,), "p": 0.7, "challenger": 0.5, "y": 0},
            {"date": "2025-06-01", "needs": 2, "key": (2,), "p": 0.5, "challenger": 0.7, "y": 1},
        ]
        out = d.decisions(rows, "p", "challenger")["same_n_by_date_and_line"]
        self.assertEqual((out["base"], out["challenger"], out["added_wins"], out["removed_wins"]), (1, 1, 1, 0))
        self.assertTrue(math.isfinite(d.metrics(rows, "p")["log_loss"]))


if __name__ == "__main__":
    unittest.main()

