"""Focused parser and chronology invariants for FC-ACC-006."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("fc_acc_006_run", Path(__file__).with_name("run.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def player(pid, code, pa, hits=0, positions=("C",)):
    return {"person": {"id": pid, "fullName": str(pid)},
            "battingOrder": str(code),
            "stats": {"batting": {"plateAppearances": pa, "hits": hits}},
            "allPositions": [{"abbreviation": p} for p in positions]}


def game():
    starters = {f"ID{i}": player(i, i * 100, 4) for i in range(1, 10)}
    side = {"team": {"id": 1}, "players": starters,
            "battingOrder": list(range(1, 10))}
    return {"game_pk": 1, "date": "2025-06-01", "game_time": "2025-06-01T20:00:00Z",
            "season": 2025, "url": "https://example.invalid/boxscore",
            "sha256": "a" * 64, "body": {"teams": {"away": side}}}


class ParserTests(unittest.TestCase):
    def test_multiple_replacements_count_only_their_pa(self):
        g = game()
        team = g["body"]["teams"]["away"]
        team["players"]["ID101"] = player(101, 901, 1, positions=("PH",))
        team["players"]["ID102"] = player(102, 902, 0, positions=("C",))
        team["battingOrder"][-1] = 102  # final lineup is not original lineup
        rows, problems = module.parse_side(g, "away")
        self.assertEqual(problems, [])
        ninth = rows[8]
        self.assertEqual(ninth["player_id"], 9)
        self.assertEqual(ninth["replacement_ids"], [101, 102])
        self.assertEqual(ninth["slot_pa"], 5)
        self.assertEqual(ninth["lost_pa"], 1)
        self.assertTrue(ninth["lost_turn"])

    def test_pinch_runner_after_last_turn_is_exit_without_lost_pa(self):
        g = game()
        g["body"]["teams"]["away"]["players"]["ID101"] = player(101, 201, 0, positions=("PR",))
        rows, problems = module.parse_side(g, "away")
        self.assertEqual(problems, [])
        self.assertTrue(rows[1]["replaced"])
        self.assertFalse(rows[1]["lost_turn"])

    def test_position_switch_does_not_create_replacement(self):
        g = game()
        g["body"]["teams"]["away"]["players"]["ID1"]["allPositions"] = [
            {"abbreviation": "3B"}, {"abbreviation": "1B"}]
        rows, problems = module.parse_side(g, "away")
        self.assertEqual(problems, [])
        self.assertFalse(rows[0]["replaced"])

    def test_missing_starter_or_batting_stats_fail_closed(self):
        g = game()
        del g["body"]["teams"]["away"]["players"]["ID1"]
        rows, problems = module.parse_side(g, "away")
        self.assertEqual(len(rows), 8)
        self.assertIn("missing_or_duplicate_starter", problems)
        g = game()
        del g["body"]["teams"]["away"]["players"]["ID1"]["stats"]["batting"]["plateAppearances"]
        _, problems = module.parse_side(g, "away")
        self.assertIn("missing_or_invalid_batting_stats", problems)

    def test_production_pa_formula_is_loaded_from_source(self):
        f, digest = module.production_pa_function()
        self.assertEqual(len(digest), 64)
        self.assertEqual([f(i, None) for i in range(1, 10)],
                         [4.51, 4.44, 4.33, 4.24, 4.12, 3.98, 3.86, 3.75, 3.60])


if __name__ == "__main__":
    unittest.main()
