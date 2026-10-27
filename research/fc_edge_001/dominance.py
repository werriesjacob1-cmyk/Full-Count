#!/usr/bin/env python3
"""FC-EDGE-001 Phase 3: exact logical-dominance check on retained FanDuel prop snapshots.

Source: data/props/props_<date>.json on main. These are production-captured one-sided "Yes" prices from
FanDuel's web endpoint; they are NOT FC-OPS-001 authenticated (no raw-response hash) and are used
here only as timestamped evidence of what was displayed.

Only exact implications are used (same player, same game, same settlement; X implies Y means every
outcome that wins X also wins Y, so P(Y) >= P(X)):
  hits>=1 => H+R+RBI>=1 | TB>=2 => hits>=1 | hits>=2 => TB>=2 | doubles>=1 => TB>=2
  triples>=1 => TB>=3 | HR>=1 => TB>=4 | HR>=1 => H+R+RBI>=3 | runs>=1 => H+R+RBI>=1
  rbis>=1 => H+R+RBI>=1 | singles>=1 => hits>=1 | X>=k+1 => X>=k (same stat)
A "dominance" observation is a snapshot where the IMPLIED (superset) wager Y is priced at least as long
as X (raw implied(Y) <= raw implied(X)): betting Y instead of X then wins in every case X wins and pays
at least as much. It is NOT arbitrage and NOT by itself positive expected value.
Rows are kept only if taken_at < start_time and not in_play.

Usage: python3 dominance.py <repo root>
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES = [  # (name, dominated X, implied superset Y)
    ("hits1=>hrr1", ("hits", 1), ("hits_runs_rbis", 1)),
    ("tb2=>hits1", ("total_bases", 2), ("hits", 1)),
    ("hits2=>tb2", ("hits", 2), ("total_bases", 2)),
    ("doubles1=>tb2", ("doubles", 1), ("total_bases", 2)),
    ("triples1=>tb3", ("triples", 1), ("total_bases", 3)),
    ("hr1=>tb4", ("home_runs", 1), ("total_bases", 4)),
    ("hr1=>hrr3", ("home_runs", 1), ("hits_runs_rbis", 3)),
    ("runs1=>hrr1", ("runs", 1), ("hits_runs_rbis", 1)),
    ("rbis1=>hrr1", ("rbis", 1), ("hits_runs_rbis", 1)),
    ("singles1=>hits1", ("singles", 1), ("hits", 1)),
]


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def imp(a):
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def monotone_rules(book):
    out = []
    by = defaultdict(dict)
    for (stat, k), a in book.items():
        by[stat][k] = a
    for stat, ks in by.items():
        for k in ks:
            if k + 1 in ks:
                out.append((f"{stat}{k + 1}=>{stat}{k}", (stat, k + 1), (stat, k)))
    return out


def main(repo):
    files = sorted(glob.glob(str(Path(repo) / "data/props/props_2026-*.json")))
    seen = Counter()
    viol = Counter()
    strict = Counter()
    distinct_pairs = defaultdict(set)
    distinct_viol = defaultdict(set)
    gaps = defaultdict(list)
    snaps = rows_used = 0
    for f in files:
        d = json.loads(Path(f).read_text())
        day = d.get("date") or Path(f).stem[-10:]
        for s in d.get("snapshots", []):
            snaps += 1
            book = defaultdict(dict)
            for r in s.get("rows", []):
                if r.get("in_play") or not r.get("start_time") or T(r["taken_at"]) >= T(r["start_time"]):
                    continue
                if not isinstance(r.get("american"), int):
                    continue
                book[(r["event_id"], r["player_norm"])][(r["stat"], int(r["needs"]))] = r["american"]
                rows_used += 1
            for key, b in book.items():
                for name, X, Y in RULES + monotone_rules(b):
                    if X in b and Y in b:
                        fam = name if name in [n for n, _, _ in RULES] else "monotone_threshold"
                        seen[fam] += 1
                        distinct_pairs[fam].add((day, key, name))
                        if imp(b[Y]) <= imp(b[X]):
                            viol[fam] += 1
                            distinct_viol[fam].add((day, key, name))
                            gaps[fam].append(imp(b[X]) - imp(b[Y]))
                            if imp(b[Y]) < imp(b[X]):
                                strict[fam] += 1
    res = {"files": len(files), "snapshots": snaps, "pregame_rows_used": rows_used, "rules": {}}
    for fam in sorted(seen):
        g = sorted(gaps[fam])
        res["rules"][fam] = {"pair_observations": seen[fam], "dominance_observations": viol[fam],
                             "strict_dominance_observations": strict[fam],
                             "distinct_player_game_pairs": len(distinct_pairs[fam]),
                             "distinct_player_game_with_dominance": len(distinct_viol[fam]),
                             "median_implied_gap_when_dominant": g[len(g) // 2] if g else None,
                             "max_implied_gap": g[-1] if g else None}
    (HERE / "out" / "dominance.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = main(sys.argv[1])
    print(json.dumps({k: v for k, v in r.items() if k != "rules"}))
    for k, v in r["rules"].items():
        print(f"{k:22s} pairs={v['distinct_player_game_pairs']:6d} dominant={v['distinct_player_game_with_dominance']:5d} "
              f"obs={v['pair_observations']:7d}/{v['dominance_observations']:6d} strict={v['strict_dominance_observations']:6d} "
              f"gap_med={v['median_implied_gap_when_dominant']}")
