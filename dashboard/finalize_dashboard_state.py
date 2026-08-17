#!/usr/bin/env python3
"""Revalidate a completed full build against current main before commit."""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from . import build_dashboard as bd
    from .live_state import atomic_write_json, parse_utc, utc_now
    from .publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from .prepare_pages_artifact import normalize_live, normalize_payload
except ImportError:
    import build_dashboard as bd
    from live_state import atomic_write_json, parse_utc, utc_now
    from publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from prepare_pages_artifact import normalize_live, normalize_payload


def finalize(candidate_path, current_path, live_path, out_path,
             registry_path=DEFAULT_REGISTRY_PATH, now=None, contexts=None):
    with open(candidate_path, encoding="utf-8") as handle:
        raw_candidate = json.load(handle)
    candidate, id_map = normalize_payload(raw_candidate)
    current = None
    if os.path.exists(current_path):
        with open(current_path, encoding="utf-8") as handle:
            raw_current = json.load(handle)
        current, current_id_map = normalize_payload(raw_current)
        id_map.update(current_id_map)
    candidate_at = parse_utc(candidate.get("generated_at"))
    current_at = parse_utc((current or {}).get("generated_at"))
    if current_at is not None and candidate_at is not None and current_at > candidate_at:
        print("Current main already has a newer full build; stale candidate is a safe no-op.")
        return False
    if os.path.exists(live_path):
        with open(live_path, encoding="utf-8") as handle:
            raw_live = json.load(handle)
    else:
        raw_live = {"props": {}}
    live = normalize_live(raw_live, id_map)
    registry = load_registry(registry_path)
    if contexts is None:
        import grade_results as gr
        contexts = gr.fetch_game_contexts(
            [row.get("game_pk") for row in candidate.get("props") or []], refresh=True,
        )
    schedule = {game_pk: {"status": value.get("status") or {}}
                for game_pk, value in (contexts or {}).items()}
    finalized = bd.reconcile_public_lifecycle(
        candidate, prior_payload=current, live=live, schedule=schedule,
        now=now or utc_now(), registry=registry,
    )
    atomic_write_json(out_path, finalized)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--current", default="docs/data.json")
    parser.add_argument("--live", default="docs/live.json")
    parser.add_argument("--out", default="docs/data.json")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    args = parser.parse_args()
    changed = finalize(
        args.candidate, args.current, args.live, args.out,
        registry_path=args.registry,
    )
    print("Final dashboard candidate written." if changed else "No dashboard write required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
