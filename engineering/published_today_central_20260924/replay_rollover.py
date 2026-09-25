#!/usr/bin/env python3
"""Replay real committed builds through reconcile_public_lifecycle.

Evidence for the 2026-09-24 "published Top Picks disappear at 7 pm Central"
fix. For each commit, it reads that commit's docs/data.json, docs/live.json
and data/public_top_picks/registry.json with `git show`, runs the CURRENT
checkout's reconcile_public_lifecycle at the build time, and counts published
Top Picks on the board. Run from the repo root on any checkout to compare
code versions:

    python3 engineering/published_today_central_20260924/replay_rollover.py

schedule={} (no live MLB status) is passed deliberately so the replay is
offline and deterministic; game state then falls back to live.json.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
from dashboard import build_dashboard as bd  # noqa: E402
from dashboard.prepare_pages_artifact import normalize_live, normalize_payload  # noqa: E402

BUILDS = (
    ("d668cc356", "2026-09-24T00:13:23Z"),  # 7:13 pm CDT, first build after UTC rollover
    ("4e02929e7", "2026-09-24T01:56:23Z"),
    ("55f98df8b", "2026-09-24T03:31:00Z"),
    ("940e7aebe", "2026-09-24T06:03:00Z"),  # 1:03 am CDT, after Central midnight
)


def show(commit, path):
    return json.loads(subprocess.check_output(["git", "show", f"{commit}:{path}"]))


def main():
    for commit, now in BUILDS:
        data, id_map = normalize_payload(show(commit, "docs/data.json"))
        live = normalize_live(show(commit, "docs/live.json"), id_map)
        registry = show(commit, "data/public_top_picks/registry.json")
        out = bd.reconcile_public_lifecycle(data, live=live, schedule={}, now=now, registry=registry)
        published = [p for p in out["props"]
                     if p.get("published_top_pick_at") and p.get("publication_artifact_id")]
        by_day = {}
        for p in published:
            key = p.get("published_slate_date") or p.get("slate_date") or "?"
            by_day[key] = by_day.get(key, 0) + 1
        print(f"{commit} {now} published on board: {len(published)} by slate day: {by_day}")


if __name__ == "__main__":
    main()
