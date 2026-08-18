#!/usr/bin/env python3
"""Merge a stale workflow candidate into current-main live state safely."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .live_state import (
        PRICE_FIELDS, atomic_write_json, compact_live_state, load_live_state,
        merge_live_states, parse_utc, validate_live_state,
    )
    from .publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from .prepare_pages_artifact import normalize_live, normalize_payload
except ImportError:
    from live_state import (
        PRICE_FIELDS, atomic_write_json, compact_live_state, load_live_state,
        merge_live_states, parse_utc, validate_live_state,
    )
    from publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from prepare_pages_artifact import normalize_live, normalize_payload


def _current_board(data_path):
    try:
        with open(data_path, encoding="utf-8") as handle:
            raw_payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"current dashboard data is unreadable: {data_path}: {exc}") from exc
    if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("props"), list):
        raise RuntimeError(f"current dashboard data is invalid: {data_path}")
    try:
        payload, id_map = normalize_payload(raw_payload)
    except ValueError as exc:
        raise RuntimeError(f"current dashboard data is invalid: {data_path}: {exc}") from exc
    return ({row.get("id") for row in payload["props"] if row.get("id")},
            payload.get("generated_at"), id_map)


def _normalized_live(path, id_map):
    if not os.path.exists(path):
        raw = {"props": {}}
    else:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"live state is unreadable: {path}: {exc}") from exc
    try:
        return normalize_live(raw, id_map)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f"live state is invalid: {path}: {exc}") from exc


def _discard_price_facts_older_than_board(live, board_generated_at):
    board_at = parse_utc(board_generated_at)
    if board_at is None:
        return
    for prop_id, delta in list(live["props"].items()):
        basis = parse_utc(delta.get("price_basis_board_generated_at"))
        has_price_fact = any(field in delta for field in PRICE_FIELDS)
        if not has_price_fact or (basis is not None and basis >= board_at):
            continue
        for field in PRICE_FIELDS:
            delta.pop(field, None)
            (delta.get("_field_updated_at") or {}).pop(field, None)
        if set(delta) <= {"_field_updated_at"} and not delta.get("_field_updated_at"):
            del live["props"][prop_id]


def _durable_settlements(results_dir):
    durable = {}
    for path in glob.glob(os.path.join(results_dir, "grades_[0-9]*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # Unknown durability must preserve the overlay, not prune it.
            continue
        for row in payload.get("public_top_picks") or []:
            if row.get("settlement_state") in ("hit", "miss", "void") \
                    and row.get("settlement_authority") == "official_final" \
                    and row.get("id") and row.get("settlement_observed_at"):
                durable[row["id"]] = (row["settlement_state"], row["settlement_observed_at"])
    return durable


def merge(base_path, incoming_path, out_path, *, data_path=None,
          registry_path=DEFAULT_REGISTRY_PATH, results_dir=None):
    board = _current_board(data_path) if data_path else None
    id_map = board[2] if board else {}
    base = _normalized_live(base_path, id_map) if board else load_live_state(base_path)
    incoming = _normalized_live(incoming_path, id_map) if board else load_live_state(incoming_path)
    merged = merge_live_states(base, incoming)
    if data_path and results_dir:
        current_ids, board_generated_at, _ = board
        _discard_price_facts_older_than_board(merged, board_generated_at)
        registry = load_registry(registry_path)
        protected = {prop_id: delta.get("game_state") for prop_id, delta in merged["props"].items()}
        merged = compact_live_state(
            merged,
            current_ids=current_ids,
            published_ids=set(registry["entries"]),
            durable_settlements=_durable_settlements(results_dir),
            protected_game_states=protected,
        )
    validate_live_state(merged)
    atomic_write_json(out_path, merged)
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--incoming", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--data", default=None)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--results-dir", default=None)
    args = parser.parse_args()
    merged = merge(
        args.base, args.incoming, args.out, data_path=args.data,
        registry_path=args.registry, results_dir=args.results_dir,
    )
    print(f"Merged live state safely: {len(merged['props'])} retained delta(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
