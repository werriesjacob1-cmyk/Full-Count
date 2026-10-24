#!/usr/bin/env python3
"""FC-ACC-018 step 2a: build the common pregame candidate universe U (PREREG §1). Outcome-free.

For each regular-season game on each graded slate, take that game's FINAL PREGAME production
dashboard state: the last `docs/data.json` commit on main at or before first_pitch - 10 min,
from a blobless partial clone (blobs fetched lazily, read-only). Only rows of that game are kept,
then the common gates are applied. Every candidate carries its commit SHA, commit time and the
payload's odds_fetched_at, so availability is reproducible.

Usage: python3 universe.py <partial clone dir> <raw StatsAPI schedule dir> <ledger slate list json>
Writes out/universe.json.
"""
from __future__ import annotations

import bisect
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKETS = ("hits_runs_rbis", "hits", "strikeouts", "pitcher_outs")
MAX_PRICE, MAX_AGE, MARGIN = -350, timedelta(minutes=45), timedelta(minutes=10)
LAST_GRADED = "2026-09-27"


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def commits(clone):
    out = subprocess.run(["git", "-C", clone, "log", "--format=%H %cI", "origin/main", "--", "docs/data.json"],
                         check=True, capture_output=True, text=True).stdout.split("\n")
    rows = sorted((T(t), h) for h, t in (l.split() for l in out if l.strip()))
    return [t for t, _ in rows], [h for _, h in rows]


def schedule(raw_dir):
    games = defaultdict(list)
    for f in sorted(Path(raw_dir).glob("schedule_2026_*.json")):
        for d in json.loads(f.read_text()).get("dates", []):
            for g in d.get("games", []):
                if g.get("gameType") == "R" and g.get("officialDate"):
                    games[g["officialDate"]].append((int(g["gamePk"]), T(g["gameDate"])))
    return games


def gate(p, commit_time, payload):
    """Common gates (PREREG §1); returns the list of failed gate names."""
    pr = p.get("projection") or {}
    fails = []
    if pr.get("stat") not in MARKETS or p.get("market_side") not in (None, "over"):
        fails.append("market")
    if p.get("market_odds") is None:
        fails.append("unpriced")
    elif p["market_odds"] < MAX_PRICE:
        fails.append("price_shorter_than_-350")
    of = payload.get("odds_fetched_at")
    if not of or T(of) > commit_time or commit_time - T(of) > MAX_AGE:
        fails.append("stale_or_missing_odds_time")
    if p.get("lineup_assumed"):
        fails.append("lineup_not_confirmed")
    gs = str(p.get("game_state") or "").lower()
    if any(x in gs for x in ("live", "in_progress", "final")):
        fails.append("game_not_pregame")
    return fails


def main(clone, raw_dir, slates_path):
    slates = [d for d in json.loads(Path(slates_path).read_text()) if d <= LAST_GRADED]
    ctimes, chashes = commits(clone)
    sched = schedule(raw_dir)
    cache, out, diag = {}, [], {}
    for d in slates:
        dg = Counter()
        for gpk, start in sched.get(d, []):
            i = bisect.bisect_right(ctimes, start - MARGIN) - 1
            if i < 0:
                dg["game_no_prior_commit"] += 1
                continue
            h, ct = chashes[i], ctimes[i]
            if h not in cache:
                txt = subprocess.run(["git", "-C", clone, "show", f"{h}:docs/data.json"], capture_output=True, text=True).stdout
                try:
                    cache[h] = json.loads(txt)
                except ValueError:
                    cache[h] = {}
            pay = cache[h]
            if pay.get("date") != d:
                dg["game_final_state_wrong_payload_date"] += 1
                continue
            rows = [p for p in pay.get("props") or [] if p.get("game_pk") is not None and int(p["game_pk"]) == gpk]
            dg["games_with_state"] += 1
            dg["rows_in_game"] += len(rows)
            for p in rows:
                f = gate(p, ct, pay)
                for x in f:
                    dg[f"fail_{x}"] += 1
                if f:
                    continue
                pr = p["projection"]
                out.append({"slate": d, "game_pk": gpk, "first_pitch": start.isoformat(), "commit": h,
                            "commit_time": ct.isoformat(), "odds_fetched_at": pay.get("odds_fetched_at"),
                            "id": p.get("id"), "player_id": p.get("player_id"), "name": p.get("name"),
                            "type": p.get("type"), "stat": pr["stat"], "needs": pr.get("needs"), "line": pr.get("value"),
                            "side": "over", "american": p["market_odds"], "market_implied": p.get("market_implied"),
                            "model": p.get("hit_probability"), "status": p.get("recommendation_status")})
            dg["eligible"] = sum(1 for x in out if x["slate"] == d)
        dg["games_scheduled"] = len(sched.get(d, []))
        diag[d] = dict(dg)
        print(d, dict(dg), flush=True)
    seen, uniq = set(), []
    for x in out:                      # a row id appears once per game state
        k = (x["slate"], x["id"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    res = {"commits_considered": len(chashes), "snapshots_read": len(cache), "per_slate_gates": diag, "candidates": uniq}
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "universe.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main(*sys.argv[1:4])
