#!/usr/bin/env python3
"""Read-only replay of the committed evidence behind AUDIT.md.

Reads git objects and committed files only. It writes nothing and makes no
network calls. Run it from the repository root:

    python3 engineering/central_slate_contract_20260924/replay_evidence.py [--since 2026-09-10]

Sections:
  A. Nightly rollover: for each night, the first full build whose payload
     `date` advanced, and how many of the previous slate's games were
     still pregame at that moment (plus how many research rows they had).
  B. MLB Daily "Picks" commits that landed between 00:00 and 06:00 UTC, and
     which picks_{date}.json files they wrote.
  C. Publication registry: the slate_date of each entry compared with the Central
     calendar date of its game's first pitch, and entries whose board was built
     on the previous Central evening.
  D. Counterfactual: under a Central build date, what the post-00Z runs of
     2026-09-24 would have replaced output/picks_2026-09-23.json and
     output/board_freeze_2026-09-23.json with.
"""
import argparse
import collections
import json
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def show_json(ref, path):
    try:
        return json.loads(git("show", f"{ref}:{path}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def ts(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def section_a(since):
    print("A. Nightly rollover of docs/data.json (first build with the next `date`)")
    log = git("log", "--reverse", "--format=%H %cI", f"--since={since}", "--", "docs/data.json")
    prev = None
    for line in log.splitlines():
        sha, when = line.split()
        payload = show_json(sha, "docs/data.json")
        if not payload:
            continue
        date = payload.get("date")
        if prev and date != prev[0]:
            at = ts(when).astimezone(timezone.utc)
            old = prev[1]
            pending = [g for g in old.get("schedule") or []
                       if g.get("game_start") and ts(g["game_start"]) > at]
            rows = sum(1 for p in old.get("props") or []
                       if p.get("game_start") and ts(p["game_start"]) > at)
            last = max((g["game_start"] for g in pending), default="-")
            print(f"  {prev[0]} -> {date} at {at:%m-%d %H:%MZ} ({at.astimezone(CT):%I:%M %p %Z}): "
                  f"{len(pending)} prior-slate game(s) still pregame, last first pitch {last}, "
                  f"{rows} research row(s) dropped")
        prev = (date, payload)


def section_b(since):
    print("\nB. MLB Daily 'Picks' commits between 00:00 and 06:00 UTC")
    log = git("log", "--format=%H %cI %s", "--grep=^Picks 20", f"--since={since}")
    hours = collections.Counter()
    for line in log.splitlines():
        sha, when, *_ = line.split()
        at = ts(when).astimezone(timezone.utc)
        hours[at.hour] += 1
        if at.hour < 6:
            files = [f for f in git("show", "--name-only", "--format=", sha).split()
                     if f.startswith("output/picks_") and f.count("_") == 1]
            print(f"  {at:%m-%d %H:%MZ} ({at.astimezone(CT):%m-%d %I:%M %p %Z}) wrote {files}")
    print(f"  commits by UTC hour: {dict(sorted(hours.items()))}")


def section_c():
    print("\nC. Publication registry slate_date against the game's Central date")
    registry = json.load(open("data/public_top_picks/registry.json", encoding="utf-8"))
    match = mismatch = prior_evening = 0
    for entry in registry["entries"].values():
        snap = entry.get("snapshot") or {}
        start = snap.get("game_start")
        slate = entry.get("slate_date")
        if not (start and slate):
            continue
        if ts(start).astimezone(CT).date().isoformat() == slate:
            match += 1
        else:
            mismatch += 1
        built = snap.get("board_timestamp")
        if built and ts(built).astimezone(CT).date().isoformat() < slate:
            prior_evening += 1
    print(f"  entries: {len(registry['entries'])}; slate_date == Central date of first pitch: "
          f"{match}; mismatches: {mismatch}")
    print(f"  entries published on the previous Central evening (UTC-rollover 'early picks'): "
          f"{prior_evening}")


def section_d():
    print("\nD. Counterfactual Central build date for the 2026-09-24 00:47Z / 01:45Z MLB Daily runs")
    canonical = show_json("b42a17fe8c", "output/picks_2026-09-23.json") or {}
    freeze = show_json("b42a17fe8c", "output/board_freeze_2026-09-23.json") or {}
    games = collections.Counter()

    def walk(node):
        if isinstance(node, dict):
            if "game_pk" in node:
                games[node["game_pk"]] += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(canonical)
    print(f"  canonical picks_2026-09-23.json (generated {canonical.get('generated')}): "
          f"{len(games)} games, {sum(games.values())} rows with game_pk")
    records = freeze.get("records") or []
    print(f"  board_freeze_2026-09-23.json: {len(records)} records over "
          f"{len({r.get('game_pk') for r in records})} games")
    schedule = (show_json("bde36d41d2", "docs/data.json") or {}).get("schedule") or []
    for run in ("2026-09-24T00:47:34+00:00", "2026-09-24T01:45:06+00:00"):
        at = ts(run)
        left = [g for g in schedule if ts(g["game_start"]) > at]
        print(f"  a Central-dated run at {at:%H:%MZ} would rebuild 2026-09-23 from "
              f"{len(left)} still-pregame game(s): {[g['matchup'] for g in left]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-09-10")
    args = parser.parse_args()
    section_a(args.since)
    section_b(args.since)
    section_c()
    section_d()
