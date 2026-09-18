#!/usr/bin/env python3
"""Contracts for dashboard/build_history.py -- the past-picks archive builder."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from dashboard.build_history import PICK_FIELDS, build_history


def _pick(**overrides):
    row = {
        "id": "fc2:1:player-1:hits:1:over",
        "identity_version": 2,
        "game_pk": 1,
        "player_id": 1,
        "name": "Test Player",
        "team": "Test Team",
        "matchup": "Test Team @ Other Team",
        "type": "batter",
        "stat": "hits",
        "prop": "Over 0.5 Hits",
        "market_side": "over",
        "market_odds": -120,
        "hit_probability": 0.62,
        "market_edge": 0.05,
        "reliability": "A",
        "reliability_note": None,
        "why": ["real reason"],
        "watchouts": [],
        "recommendation_status": "top_pick",
        "publication_run_id": "internal-only-should-be-dropped",
        "grade": "hit",
        "actual": 1,
        "actual_stat": "hits",
        "threshold": 0.5,
        "settlement_state": "hit",
        "game_start": "2026-09-16T22:00:00Z",
    }
    row.update(overrides)
    return row


def _grades_file(root, date, picks):
    path = os.path.join(root, f"grades_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "date": date, "hits": 0, "misses": 0, "ungraded": 0,
            "public_top_picks": picks,
        }, f)
    return path


class BuildHistoryTests(unittest.TestCase):
    def test_real_shaped_day_produces_expected_summary(self):
        with tempfile.TemporaryDirectory() as root:
            _grades_file(root, "2026-09-16", [
                _pick(id="a", grade="hit"),
                _pick(id="b", grade="miss"),
                _pick(id="c", grade="hit"),
            ])
            payload = build_history(results_dir=root, retention_days=45)
            self.assertEqual(len(payload["days"]), 1)
            day = payload["days"][0]
            self.assertEqual(day["date"], "2026-09-16")
            self.assertEqual(day["hits"], 2)
            self.assertEqual(day["misses"], 1)
            self.assertAlmostEqual(day["hit_rate"], 2 / 3)
            self.assertEqual(len(day["picks"]), 3)

    def test_ungraded_day_has_null_hit_rate_not_fabricated_zero(self):
        with tempfile.TemporaryDirectory() as root:
            _grades_file(root, "2026-09-17", [
                _pick(id="a", grade="pending"),
            ])
            payload = build_history(results_dir=root, retention_days=45)
            day = payload["days"][0]
            self.assertEqual(day["hits"], 0)
            self.assertEqual(day["misses"], 0)
            self.assertIsNone(day["hit_rate"])

    def test_internal_fields_are_dropped_public_fields_kept(self):
        with tempfile.TemporaryDirectory() as root:
            _grades_file(root, "2026-09-16", [_pick(id="a")])
            payload = build_history(results_dir=root, retention_days=45)
            pick = payload["days"][0]["picks"][0]
            self.assertNotIn("publication_run_id", pick)
            self.assertNotIn("identity_version", pick)
            self.assertNotIn("player_id", pick)
            for field in ("id", "name", "prop", "grade", "actual", "why"):
                self.assertIn(field, pick)
            self.assertEqual(set(pick.keys()) - set(PICK_FIELDS), set())

    def test_day_with_no_top_picks_is_omitted(self):
        with tempfile.TemporaryDirectory() as root:
            _grades_file(root, "2026-09-16", [])
            payload = build_history(results_dir=root, retention_days=45)
            self.assertEqual(payload["days"], [])

    def test_retention_window_excludes_old_days(self):
        with tempfile.TemporaryDirectory() as root:
            old_date = (
                datetime.now(timezone.utc) - timedelta(days=100)
            ).strftime("%Y-%m-%d")
            recent_date = (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).strftime("%Y-%m-%d")
            _grades_file(root, old_date, [_pick(id="old")])
            _grades_file(root, recent_date, [_pick(id="recent")])
            payload = build_history(results_dir=root, retention_days=45)
            dates = [d["date"] for d in payload["days"]]
            self.assertIn(recent_date, dates)
            self.assertNotIn(old_date, dates)

    def test_days_sorted_newest_first(self):
        with tempfile.TemporaryDirectory() as root:
            _grades_file(root, "2026-09-14", [_pick(id="a")])
            _grades_file(root, "2026-09-16", [_pick(id="b")])
            _grades_file(root, "2026-09-15", [_pick(id="c")])
            payload = build_history(results_dir=root, retention_days=45)
            dates = [d["date"] for d in payload["days"]]
            self.assertEqual(dates, ["2026-09-16", "2026-09-15", "2026-09-14"])

    def test_malformed_grades_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "grades_2026-09-16.json"), "w") as f:
                f.write("{not valid json")
            _grades_file(root, "2026-09-17", [_pick(id="a")])
            payload = build_history(results_dir=root, retention_days=45)
            dates = [d["date"] for d in payload["days"]]
            self.assertEqual(dates, ["2026-09-17"])

    def test_non_dict_rows_in_public_top_picks_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "grades_2026-09-16.json")
            with open(path, "w") as f:
                json.dump({
                    "date": "2026-09-16",
                    "public_top_picks": [_pick(id="a"), "not a dict", None],
                }, f)
            payload = build_history(results_dir=root, retention_days=45)
            self.assertEqual(len(payload["days"][0]["picks"]), 1)

    def test_schema_has_stable_top_level_shape(self):
        with tempfile.TemporaryDirectory() as root:
            _grades_file(root, "2026-09-16", [_pick(id="a")])
            payload = build_history(results_dir=root, retention_days=45)
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("generated_at", payload)
            self.assertEqual(payload["retention_days"], 45)
            self.assertIn("days", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
