#!/usr/bin/env python3
"""FC-ACC-018 tests: common gates, symmetric constraints, grading rules, outcome-blind selection."""
import inspect
import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evaluate as EV  # noqa: E402
import select_arms as SA  # noqa: E402
import universe as UN  # noqa: E402

CT = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
PAY = {"odds_fetched_at": "2026-09-01T21:40:00Z"}


def row(**kw):
    r = {"projection": {"stat": "hits", "needs": 1, "value": 0.5}, "market_side": "over", "market_odds": -200,
         "lineup_assumed": False, "game_state": "scheduled"}
    r.update(kw)
    return r


class Gates(unittest.TestCase):
    def test_common_gates(self):
        self.assertEqual(UN.gate(row(), CT, PAY), [])
        self.assertIn("price_shorter_than_-350", UN.gate(row(market_odds=-400), CT, PAY))
        self.assertIn("unpriced", UN.gate(row(market_odds=None), CT, PAY))
        self.assertIn("lineup_not_confirmed", UN.gate(row(lineup_assumed=True), CT, PAY))
        self.assertIn("market", UN.gate(row(projection={"stat": "hard_hit_105", "needs": 1}), CT, PAY))
        self.assertIn("stale_or_missing_odds_time", UN.gate(row(), CT, {"odds_fetched_at": "2026-09-01T20:00:00Z"}))
        self.assertIn("stale_or_missing_odds_time", UN.gate(row(), CT, {"odds_fetched_at": "2026-09-01T22:05:00Z"}))
        self.assertIn("game_not_pregame", UN.gate(row(game_state="live"), CT, PAY))

    def test_same_constraints_for_b_and_c(self):
        pool = [{"id": str(i), "player_id": i // 2, "game_pk": i // 6, "american": -150 - i, "market_implied": 0.8,
                 "model": 0.7 - i / 1000} for i in range(30)]
        for key in (SA.b_key, SA.c_key):
            sel = SA.constrained(pool, key, 30)
            per_game = {}
            for r in sel:
                per_game[r["game_pk"]] = per_game.get(r["game_pk"], 0) + 1
            self.assertLessEqual(max(per_game.values()), 2)
            self.assertEqual(len({r["player_id"] for r in sel}), len(sel))

    def test_c_frozen_thresholds(self):
        base = {"market_implied": 0.648, "american": -184, "model": 0.60}
        self.assertTrue(SA.c_ok(base))                                   # 0.648/1.08 = 0.60
        self.assertFalse(SA.c_ok({**base, "model": 0.599}))
        self.assertFalse(SA.c_ok({**base, "market_implied": 0.64}))
        self.assertFalse(SA.c_ok({**base, "american": -351}))

    def test_selection_code_never_reads_outcomes(self):
        # the only grade access defines N (settled vs void); it never separates a win from a loss
        allowed = 'status[p["id"]] = "settled" if p.get("grade") in ("hit", "miss") else p.get("grade")'
        src = (inspect.getsource(SA) + inspect.getsource(UN)).replace(allowed, "")
        for w in ('"grade"', "'grade'", '"actual', '"hit"', '"miss"', '"WIN"', '"LOSS"'):
            self.assertNotIn(w, src)


class Grading(unittest.TestCase):
    def test_grade_rules(self):
        bat = {(1, 10): {"plateAppearances": 4, "hits": 0, "runs": 1, "rbi": 0}, (1, 11): {"plateAppearances": 0}}
        pit = {(1, 20): {"gamesStarted": 1, "strikeOuts": 5, "outs": 15}, (1, 21): {"gamesStarted": 0}}
        g = lambda stat, pid, n: EV.grade({"game_pk": 1, "player_id": pid, "stat": stat, "needs": n}, bat, pit)  # noqa: E731
        self.assertEqual(g("hits", 10, 1), "LOSS")
        self.assertEqual(g("hits_runs_rbis", 10, 1), "WIN")
        self.assertEqual(g("hits", 11, 1), "VOID")
        self.assertEqual(g("hits", 12, 1), "VOID")
        self.assertEqual(g("strikeouts", 20, 5), "WIN")
        self.assertEqual(g("strikeouts", 20, 6), "LOSS")
        self.assertEqual(g("pitcher_outs", 20, 16), "LOSS")
        self.assertEqual(g("strikeouts", 21, 4), "VOID")


if __name__ == "__main__":
    unittest.main()
