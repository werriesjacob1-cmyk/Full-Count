#!/usr/bin/env python3
"""FC-EDGE-001 Phase 2: does FanDuel's displayed price react when FULL COUNT observes a projected
lineup becoming confirmed, and is there a window? (production dashboard history; outcome-free)

For each sampled slate, every `docs/data.json` commit on main between 14:00 and 23:59 UTC whose
payload date is the slate. For each batter prop row (game, player, stat, needs) we track
(commit time, odds_fetched_at, lineup_assumed, batting_order, market_odds, model probability).
Transition = last observation with lineup_assumed True followed by the first with False.

Reported: how often the price changed across the transition vs across adjacent assumed/assumed
pairs (control); price change vs batting-order change direction; how often the price was already
different at FULL COUNT's first confirmed observation; observation lags. Prices are production
captures of FanDuel (not authenticated); timestamps are FULL COUNT's acquisition times, not the
book's update times or a bettor's execution times.

Usage: python3 lineup_timing.py <partial clone of main>
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SLATES = ("2026-08-22", "2026-08-27", "2026-09-02", "2026-09-10", "2026-09-17", "2026-09-24")
STATS = ("hits", "hits_runs_rbis", "total_bases", "runs", "rbis", "home_runs", "singles")


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def imp(a):
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def main(clone):
    series = defaultdict(list)
    meta = {}
    for d in SLATES:
        log = subprocess.run(["git", "-C", clone, "log", "--format=%H %cI", "origin/main", f"--since={d}T14:00:00Z",
                              f"--until={d}T23:59:00Z", "--", "docs/data.json"], capture_output=True, text=True).stdout.split("\n")
        commits = sorted((T(t), h) for h, t in (l.split() for l in log if l.strip()))
        used = 0
        for ct, h in commits:
            try:
                pay = json.loads(subprocess.run(["git", "-C", clone, "show", f"{h}:docs/data.json"], capture_output=True,
                                                text=True).stdout)
            except ValueError:
                continue
            if pay.get("date") != d:
                continue
            used += 1
            of = pay.get("odds_fetched_at")
            for p in pay.get("props") or []:
                pr = p.get("projection") or {}
                if p.get("type") != "batter" or pr.get("stat") not in STATS or p.get("market_odds") is None:
                    continue
                if p.get("game_start") and T(p["game_start"]) <= ct:
                    continue
                series[(d, p.get("game_pk"), p.get("player_id"), pr["stat"], pr.get("needs"))].append(
                    (ct, of, bool(p.get("lineup_assumed")), p.get("batting_order"), p["market_odds"], p.get("hit_probability")))
        meta[d] = {"commits": len(commits), "commits_with_slate_payload": used}
        print(d, meta[d], flush=True)
    trans, ctrl = [], []
    for k, obs in series.items():
        obs.sort()
        dedup = []
        for o in obs:                                   # collapse identical consecutive odds fetches
            if dedup and o[1] == dedup[-1][1] and o[2] == dedup[-1][2]:
                continue
            dedup.append(o)
        for a, b in zip(dedup, dedup[1:]):
            if a[2] and not b[2]:
                trans.append((k, a, b))
            elif a[2] and b[2] and a[1] != b[1]:
                ctrl.append((k, a, b))

    def summarize(pairs):
        ch = [imp(b[4]) - imp(a[4]) for _, a, b in pairs]
        mins = [(b[0] - a[0]).total_seconds() / 60 for _, a, b in pairs]
        return {"n": len(pairs), "share_price_changed": float(np.mean([c != 0 for c in ch])) if ch else None,
                "mean_abs_implied_change": float(np.mean(np.abs(ch))) if ch else None,
                "median_minutes_between_obs": float(np.median(mins)) if mins else None}
    slot = Counter()
    directional = defaultdict(list)
    for k, a, b in trans:
        oa, ob = a[3], b[3]
        if isinstance(oa, int) and isinstance(ob, int):
            dslot = ob - oa
            lab = "moved_up" if dslot < 0 else ("moved_down" if dslot > 0 else "same_slot")
            slot[lab] += 1
            directional[lab].append(imp(b[4]) - imp(a[4]))
    res = {"slates": meta, "series": len(series), "transition": summarize(trans), "control_assumed_pairs": summarize(ctrl),
           "slot_change_at_confirmation": dict(slot),
           "implied_change_by_slot_change": {k: {"n": len(v), "mean": float(np.mean(v)), "share_changed": float(np.mean([x != 0 for x in v]))}
                                             for k, v in directional.items()},
           "note": "timestamps are FULL COUNT observation times (dashboard commits / odds_fetched_at), not book update or execution times"}
    (HERE / "out" / "lineup_timing.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), indent=1))
