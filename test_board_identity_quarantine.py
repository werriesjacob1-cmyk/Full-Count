#!/usr/bin/env python3
"""One unidentifiable candidate must not delete a valid board.

2026-08-28 incident: three consecutive Dashboard Refresh runs (07:11,
12:35, 14:43 UTC) died in _clean_candidate_rows() with
`ValueError: prop has no stable player/combo/game-level subject` after
generating 972 candidates across 15 games. Production then served the
06:32 board for nine hours while refresh_prices.py kept updating prices on
top of it.

The boundary tested here is deliberately two-sided: isolated rows are
quarantined so the rest of the board publishes, and widespread identity
failure still fails closed, because publishing most of a corrupt board is
worse than publishing none.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard import build_dashboard as bd


def row(pid=101, stat="hits", needs=1, game_pk=700, name="Real Player"):
    return {"type": "batter", "name": name, "player_id": pid, "game_pk": game_pk,
            "team": "T", "matchup": "A @ B", "prop": "Over 0.5 Hits",
            "projection": {"stat": stat, "value": 0.5, "needs": needs},
            "hit_probability": 0.6, "score": 61.0, "signals": {}}


def orphan(**kw):
    """No player_id, no combo, and a stat that is not game-level."""
    r = row(**kw)
    r["player_id"] = None
    r["combo_player_ids"] = None
    r["name"] = "Unknown Callup"
    return r


SCHED = {}


class TestIsolatedRowDoesNotKillBoard(unittest.TestCase):
    def test_one_orphan_among_many_valid_rows_publishes_the_rest(self):
        rows = [row(pid=i) for i in range(200)] + [orphan(pid=None)]
        out = bd._clean_candidate_rows(rows, SCHED)
        self.assertEqual(len(out), 200)
        self.assertTrue(all(r["id"] for r in out))

    def test_the_orphan_is_absent_from_output(self):
        out = bd._clean_candidate_rows([row(pid=1), orphan()], SCHED)
        self.assertEqual([r["name"] for r in out], ["Real Player"])

    def test_identity_is_never_synthesized_from_the_display_name(self):
        """A fabricated subject would settle a wager against a player we
        cannot prove we meant."""
        out = bd._clean_candidate_rows([row(pid=1)] * 10 + [orphan()], SCHED)
        for r in out:
            self.assertNotIn("Unknown", r["id"])
            self.assertNotIn("Callup", r["id"])
        self.assertTrue(all("player-" in r["id"] or "combo-" in r["id"]
                            or r["id"].endswith(":game") or ":game:" in r["id"]
                            for r in out))


class TestSystemicCorruptionFailsClosed(unittest.TestCase):
    def test_widespread_identity_failure_raises(self):
        rows = [row(pid=i) for i in range(50)] + [orphan() for _ in range(40)]
        with self.assertRaises(bd.IdentityCorruption):
            bd._clean_candidate_rows(rows, SCHED)

    def test_an_all_orphan_batch_raises(self):
        with self.assertRaises(bd.IdentityCorruption):
            bd._clean_candidate_rows([orphan() for _ in range(30)], SCHED)

    def test_budget_rule_is_explicit_and_bounded(self):
        self.assertEqual(bd.quarantine_budget(0), bd.QUARANTINE_ABSOLUTE_FLOOR)
        self.assertEqual(bd.quarantine_budget(100), bd.QUARANTINE_ABSOLUTE_FLOOR)
        self.assertEqual(bd.quarantine_budget(1000), 20)   # 2% of 1000
        self.assertEqual(bd.QUARANTINE_MAX_RATE, 0.02)

    def test_exactly_at_budget_publishes_but_over_budget_fails(self):
        valid = [row(pid=i) for i in range(1000)]
        at = valid + [orphan() for _ in range(bd.quarantine_budget(1000 + 20))]
        self.assertEqual(len(bd._clean_candidate_rows(at, SCHED)), 1000)
        over = valid + [orphan() for _ in range(bd.quarantine_budget(1000) + 25)]
        with self.assertRaises(bd.IdentityCorruption):
            bd._clean_candidate_rows(over, SCHED)


class TestValidIdentitiesUnaffected(unittest.TestCase):
    def test_game_level_and_combo_rows_still_publish(self):
        rows = [
            dict(row(stat="nrfi_combined", needs=1), player_id=None,
                 combo_player_ids=None, type="game"),
            dict(row(stat="combined_strikeouts", needs=7), player_id=None,
                 combo_player_ids=["11", "22"], type="pitcher_combo"),
        ]
        out = bd._clean_candidate_rows(rows, SCHED)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
