#!/usr/bin/env python3
"""FC-ACC-017 verification tests (read-only over repository evidence; synthetic for filters)."""
import json
import os
import sys
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger  # noqa: E402
import replacement as RP  # noqa: E402
import snapshot_replication as SR  # noqa: E402


class Ledger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.recon, cls.per_day = ledger.build()

    def test_registry_and_grades_reconcile_exactly(self):
        r = self.recon
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["duplicate_graded_ids"], [])
        self.assertEqual(r["registry_ids_never_graded"], [])
        self.assertEqual(r["registry_entries"], r["graded_rows"])
        self.assertTrue(r["history_matches"])

    def test_every_graded_snapshot_is_a_published_top_pick(self):
        self.assertTrue(all(x["snapshot"]["recommendation_status"] == "top_pick" for x in self.rows))
        self.assertTrue(all(x["first_published_at"] for x in self.rows))


class Replacement(unittest.TestCase):
    def test_pool_is_pregame_priced_confirmed_and_graded(self):
        import glob
        fs = sorted(glob.glob(os.path.join(HERE, "..", "..", "output", "board_freeze_graded_2026-*.json")))
        self.assertTrue(fs)
        for f in fs:
            d, pool, _ = RP.pool_for(f)
            b = json.load(open(f.replace("_graded", "")))
            gen = RP.T(b["board_generated_at"])
            starts = {str(k): RP.T(v) for k, v in (b.get("game_start_times") or {}).items()}
            for x in pool:
                self.assertGreater(starts[str(x["game_pk"])], gen)
                self.assertIsNotNone(x["market"]["market_odds"])
                self.assertEqual(x["eligibility"]["qc_status"], "kept")
                self.assertFalse(x["eligibility"]["lineup_assumed"])
                self.assertIn(x["stat"], RP.MARKETS)

    def test_pick_never_exceeds_n_and_one_per_player(self):
        pool = [{"player_id": i % 3, "candidate_id": str(i), "market": {"market_implied": 0.9 - i / 100}} for i in range(9)]
        sel = RP.pick(pool, 5, lambda x: (-x["market"]["market_implied"], x["candidate_id"]))
        self.assertEqual(len(sel), 3)                                   # only 3 distinct players -> shortfall, never filled
        self.assertEqual(len({x["player_id"] for x in sel}), 3)

    def test_replacement_is_deterministic(self):
        RP.main()
        a = open(os.path.join(HERE, "out", "replacement.json")).read()
        RP.main()
        self.assertEqual(a, open(os.path.join(HERE, "out", "replacement.json")).read())


class SnapshotFilter(unittest.TestCase):
    def test_only_pregame_priced_confirmed_hits1(self):
        snap = {"generated_at": "2026-09-01T22:30:00Z", "props": [
            {"projection": {"stat": "hits", "needs": 1}, "market_odds": -200, "hit_probability": 0.6, "lineup_assumed": False,
             "game_start": "2026-09-01T23:05:00Z"},
            {"projection": {"stat": "hits", "needs": 1}, "market_odds": -200, "hit_probability": 0.6, "lineup_assumed": False,
             "game_start": "2026-09-01T20:05:00Z"},                     # already started -> excluded
            {"projection": {"stat": "hits", "needs": 1}, "market_odds": None, "hit_probability": 0.6, "lineup_assumed": False,
             "game_start": "2026-09-01T23:05:00Z"},                     # unpriced -> excluded
            {"projection": {"stat": "hits", "needs": 1}, "market_odds": -200, "hit_probability": 0.6, "lineup_assumed": True,
             "game_start": "2026-09-01T23:05:00Z"},                     # assumed lineup -> excluded
            {"projection": {"stat": "hits_runs_rbis", "needs": 1}, "market_odds": -200, "hit_probability": 0.6,
             "lineup_assumed": False, "game_start": "2026-09-01T23:05:00Z"}]}
        self.assertEqual(len(list(SR.candidates(snap))), 1)


if __name__ == "__main__":
    unittest.main()
