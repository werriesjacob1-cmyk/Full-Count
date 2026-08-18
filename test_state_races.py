#!/usr/bin/env python3
"""Reproduce same-base workflow races and file-integrity failures."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

from dashboard.live_state import (
    atomic_write_json, default_live_state, load_live_state, merge_live_states,
    merge_prop_fields,
)
from dashboard.merge_live_files import merge
from dashboard.publication_registry import default_registry, write_registry


PID = "fc2:1:player-101:hits:1:over"
T1 = "2026-08-17T17:00:00Z"
T2 = "2026-08-17T17:05:00Z"
T3 = "2026-08-17T17:10:00Z"


def board_row(**overrides):
    value = {
        "id": PID, "identity_version": 2, "type": "batter",
        "game_pk": 1, "game_start": "2026-08-17T20:00:00Z",
        "player_id": 101, "combo_player_ids": None,
        "projection": {"stat": "hits", "needs": 1, "value": 1},
        "stat": "hits", "market_side": "over",
        "recommendation_status": "lean",
    }
    value.update(overrides)
    return value


def board_payload(stamp=T3, rows=None):
    return {
        "schema_version": 3, "identity_schema_version": 2,
        "generated_at": stamp, "odds_fetched_at": stamp,
        "props": rows or [board_row()],
    }


def price(odds, stamp, basis=T1):
    state = default_live_state()
    merge_prop_fields(state, PID, {
        "market_odds": odds, "price_basis_board_generated_at": basis,
    }, stamp, channel="prices")
    return state


def settlement(state, authority, stamp, actual):
    value = default_live_state()
    merge_prop_fields(value, PID, {
        "settlement_state": state, "settlement_authority": authority,
        "settlement_observed_at": stamp,
        "settlement_source": "live" if authority == "live_observation" else "final",
        "result_actual": actual, "result_reason": state,
    }, stamp, channel="grades")
    return value


class SameBaseRaceTests(unittest.TestCase):
    def test_price_and_grade_survive_both_write_orders(self):
        p = price(-130, T2)
        g = settlement("hit", "official_final", T2, 1)
        for first, second in ((p, g), (g, p)):
            merged = merge_live_states(first, second)
            self.assertEqual(merged["props"][PID]["market_odds"], -130)
            self.assertEqual(merged["props"][PID]["settlement_state"], "hit")

    def test_stale_price_and_grade_lose_but_unrelated_fact_survives(self):
        current = merge_live_states(price(-150, T3), settlement("miss", "official_final", T3, 0))
        stale = merge_live_states(price(-110, T1), settlement("provisional_hit", "live_observation", T2, 1))
        merged = merge_live_states(current, stale)
        self.assertEqual(merged["props"][PID]["market_odds"], -150)
        self.assertEqual(merged["props"][PID]["settlement_state"], "miss")
        self.assertEqual(merged["props"][PID]["result_actual"], 0)

    def test_newer_official_correction_replaces_whole_fact(self):
        old = settlement("hit", "official_final", T2, 1)
        corrected = settlement("miss", "official_final", T3, 0)
        merged = merge_live_states(old, corrected)
        fact = merged["props"][PID]
        self.assertEqual((fact["settlement_state"], fact["result_actual"], fact["result_reason"]),
                         ("miss", 0, "miss"))

    def test_conflicting_equal_timestamp_keeps_current_main_fact(self):
        current = price(-150, T2)
        incoming = price(-110, T2)
        merged = merge_live_states(current, incoming)
        self.assertEqual(merged["props"][PID]["market_odds"], -150)


class PersistentMergeTests(unittest.TestCase):
    def test_candidate_rebased_on_newer_board_drops_stale_price_not_final_grade(self):
        with tempfile.TemporaryDirectory() as root:
            base_path = os.path.join(root, "base.json")
            incoming_path = os.path.join(root, "incoming.json")
            out_path = os.path.join(root, "out.json")
            data_path = os.path.join(root, "data.json")
            registry_path = os.path.join(root, "registry.json")
            results = os.path.join(root, "results")
            os.makedirs(results)
            base = settlement("miss", "official_final", T3, 0)
            incoming = merge_live_states(price(-105, T2, basis=T1),
                                         settlement("provisional_hit", "live_observation", T2, 1))
            atomic_write_json(base_path, base)
            atomic_write_json(incoming_path, incoming)
            atomic_write_json(data_path, board_payload())
            write_registry(registry_path, default_registry())
            merged = merge(
                base_path, incoming_path, out_path, data_path=data_path,
                registry_path=registry_path, results_dir=results,
            )
            fact = merged["props"][PID]
            self.assertNotIn("market_odds", fact)
            self.assertEqual(fact["settlement_state"], "miss")

    def test_corrupt_live_and_data_fail_without_becoming_empty(self):
        with tempfile.TemporaryDirectory() as root:
            corrupt = os.path.join(root, "live.json")
            with open(corrupt, "w", encoding="utf-8") as handle:
                handle.write("{")
            with self.assertRaises(RuntimeError):
                load_live_state(corrupt)
            with open(corrupt, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{")
            base = os.path.join(root, "base.json")
            incoming = os.path.join(root, "incoming.json")
            out = os.path.join(root, "out.json")
            data = os.path.join(root, "data.json")
            registry = os.path.join(root, "registry.json")
            results = os.path.join(root, "results")
            os.makedirs(results)
            atomic_write_json(base, default_live_state())
            atomic_write_json(incoming, default_live_state())
            atomic_write_json(out, {"prior": "intact"})
            write_registry(registry, default_registry())
            with open(data, "w", encoding="utf-8") as handle:
                handle.write("{")
            with self.assertRaises(RuntimeError):
                merge(base, incoming, out, data_path=data,
                      registry_path=registry, results_dir=results)
            with open(out, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"prior": "intact"})

    def test_unversioned_price_overlay_is_not_carried_across_new_board(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {name: os.path.join(root, name) for name in (
                "base.json", "incoming.json", "out.json", "data.json", "registry.json",
            )}
            incoming = default_live_state()
            merge_prop_fields(incoming, PID, {"market_odds": -105}, T2, channel="prices")
            atomic_write_json(paths["base.json"], default_live_state())
            atomic_write_json(paths["incoming.json"], incoming)
            atomic_write_json(paths["data.json"], board_payload())
            write_registry(paths["registry.json"], default_registry())
            results = os.path.join(root, "results")
            os.makedirs(results)
            merged = merge(
                paths["base.json"], paths["incoming.json"], paths["out.json"],
                data_path=paths["data.json"], registry_path=paths["registry.json"],
                results_dir=results,
            )
            self.assertNotIn(PID, merged["props"])

    def test_legacy_source_ids_are_boundedly_canonicalized_before_merge(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {name: os.path.join(root, name) for name in (
                "base.json", "incoming.json", "out.json", "data.json", "registry.json",
            )}
            legacy_id = "1-101-hits-1"
            legacy_row = board_row(id=legacy_id)
            legacy_row.pop("identity_version")
            legacy_data = board_payload(rows=[legacy_row])
            legacy_data.pop("schema_version")
            legacy_data.pop("identity_schema_version")
            legacy_live = {
                "prices_updated_at": T2, "grades_updated_at": None,
                "props": {legacy_id: {
                    "market_odds": -125,
                    "price_basis_board_generated_at": T3,
                }},
            }
            atomic_write_json(paths["data.json"], legacy_data)
            atomic_write_json(paths["base.json"], legacy_live)
            atomic_write_json(paths["incoming.json"], legacy_live)
            write_registry(paths["registry.json"], default_registry())
            results = os.path.join(root, "results")
            os.makedirs(results)
            merged = merge(
                paths["base.json"], paths["incoming.json"], paths["out.json"],
                data_path=paths["data.json"], registry_path=paths["registry.json"],
                results_dir=results,
            )
            self.assertEqual(merged["schema_version"], 3)
            self.assertIn(PID, merged["props"])
            self.assertNotIn(legacy_id, merged["props"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
