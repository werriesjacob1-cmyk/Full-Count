#!/usr/bin/env python3
"""Build docs/history.json: a lazy-loaded per-day archive of past Top Picks.

results/grades_{date}.json already computes a full grade (hit/miss/void/
ungraded, actual stat, settlement_state) for every published Top Pick, every
day, via grade_results.py -- this script's only job is to trim that existing,
already-correct record down to what a public "past picks" page needs and
write it to its own file, kept separate from docs/data.json and docs/live.json
so the 5-minute live-update loop and the always-loaded homepage payload are
never touched or slowed by this.

Deliberately read-only against results/: this script never grades a pick or
mutates settlement state, it only republishes what grade_results.py already
decided.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timedelta, timezone

try:
    from .live_state import atomic_write_json
except ImportError:
    from live_state import atomic_write_json

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
OUTPUT_PATH = os.path.join(REPO_ROOT, "docs", "history.json")

# Bounds the public archive so this stays a lightweight lazy-loaded file
# even as the season accumulates months of graded days.
RETENTION_DAYS = 45

# Fields a past-picks card actually needs to render honestly (what was
# picked, why, and what happened) -- deliberately excludes internal-only
# bookkeeping (publication_run_id, versions, prob_ci, etc.) that a public
# page has no use for.
PICK_FIELDS = (
    "id", "name", "team", "matchup", "type", "stat", "prop", "market_side",
    "market_odds", "hit_probability", "market_edge", "reliability",
    "reliability_note", "why", "watchouts", "grade", "actual", "actual_stat",
    "threshold", "settlement_state", "game_start",
)


def _trim_pick(row):
    return {field: row.get(field) for field in PICK_FIELDS if field in row}


def build_history(results_dir=RESULTS_DIR, retention_days=RETENTION_DAYS):
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=retention_days)
    ).strftime("%Y-%m-%d")
    days = []
    for path in sorted(glob.glob(os.path.join(results_dir, "grades_*.json"))):
        date = os.path.basename(path)[len("grades_"):-len(".json")]
        if date < cutoff:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue  # a malformed/missing day is skipped, not fatal to the archive

        picks = [
            _trim_pick(row) for row in (data.get("public_top_picks") or [])
            if isinstance(row, dict)
        ]
        if not picks:
            continue

        graded = [p for p in picks if p.get("grade") in ("hit", "miss")]
        hits = sum(1 for p in graded if p["grade"] == "hit")
        days.append({
            "date": date,
            "hits": hits,
            "misses": len(graded) - hits,
            "hit_rate": (hits / len(graded)) if graded else None,
            "picks": picks,
        })

    days.sort(key=lambda d: d["date"], reverse=True)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "retention_days": retention_days,
        "days": days,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT_PATH)
    parser.add_argument("--results-dir", default=RESULTS_DIR)
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    args = parser.parse_args(argv)

    payload = build_history(args.results_dir, args.retention_days)
    atomic_write_json(args.out, payload)
    total_picks = sum(len(d["picks"]) for d in payload["days"])
    print(f"wrote {args.out}: {len(payload['days'])} days, {total_picks} picks")


if __name__ == "__main__":
    main()
