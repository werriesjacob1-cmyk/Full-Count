#!/usr/bin/env python3
"""FC-ACC-017 Phase 4: equal-count replacement test on the authentic frozen candidate pool.

Population: output/board_freeze_graded_<date>.json (complete candidate universe frozen at
generation, graded by the production grader). Prices are production-captured FanDuel quotes
at board generation (NOT FC-OPS-001 authenticated). The pool is a DIFFERENT pipeline from the
dashboard champion (only part of the public picks appear in it) - disclosed.

Per slate, N = the number of actual PUBLIC Top Picks (ledger). Arms:
  A  actual public Top Picks (their real public grades and published prices);
  M  market-only: top N by market-implied probability from the pool;
  MM model-within-favorites: among pool rows with devig market >= 0.60, top N by model probability;
  MMc  MM with production's user price limit (American >= -350, prop_probability.USER_MAX_PRICE).
Pool rule (fixed before running): champion markets (hits, hits_runs_rbis, strikeouts, pitcher_outs),
priced, graded hit/miss, qc kept, lineup not assumed, game start after board generation;
one selection per player. Shortfalls are reported, never filled.
"""
from __future__ import annotations

import glob
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MARKETS = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
HOLD = 1.08


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def dec(a):
    return 1 + (100 / -a if a < 0 else a / 100)


def pool_for(day_graded):
    b = json.loads(Path(day_graded.replace("_graded", "")).read_text())
    g = json.loads(Path(day_graded).read_text())
    gen = T(b["board_generated_at"])
    starts = {str(k): T(v) for k, v in (b.get("game_start_times") or {}).items()}
    out = []
    for x in g["records"]:
        if x["stat"] not in MARKETS or x["market"].get("market_odds") is None or x["grade"] not in ("hit", "miss"):
            continue
        if x["eligibility"].get("qc_status") != "kept" or x["eligibility"].get("lineup_assumed"):
            continue
        s = starts.get(str(x["game_pk"]))
        if s is None or s <= gen:
            continue
        out.append(x)
    return g["date"], out, g.get("source_board_sha256")


def pick(pool, n, key, cond=lambda x: True):
    sel, seen = [], set()
    for x in sorted((x for x in pool if cond(x)), key=key):
        if len(sel) == n:
            break
        if x["player_id"] in seen:
            continue
        sel.append(x)
        seen.add(x["player_id"])
    return sel


def stats(items, win, odds):
    n = len(items)
    w = sum(win(i) for i in items)
    prof = sum((dec(odds(i)) - 1) if win(i) else -1 for i in items)
    return {"n": n, "wins": w, "profit": prof}


def main():
    L = json.loads((HERE / "out" / "ledger.json").read_text())
    pub = defaultdict(list)
    for r in L["rows"]:
        if r["grade"] in ("hit", "miss"):
            pub[r["slate_date"]].append(r)
    days, shas = [], {}
    for f in sorted(glob.glob(str(REPO / "output/board_freeze_graded_2026-*.json"))):
        d, pool, sha = pool_for(f)
        shas[d] = sha
        P = pub.get(d, [])
        if not P:
            continue
        N = len(P)
        M = pick(pool, N, lambda x: (-x["market"]["market_implied"], x["candidate_id"]))
        MM = pick(pool, N, lambda x: (-x["prediction"]["hit_probability"], x["candidate_id"]),
                  cond=lambda x: x["market"]["market_implied"] / HOLD >= 0.60)
        MMc = pick(pool, N, lambda x: (-x["prediction"]["hit_probability"], x["candidate_id"]),
                   cond=lambda x: x["market"]["market_implied"] / HOLD >= 0.60 and x["market"]["market_odds"] >= -350)
        rec = {"date": d, "N": N, "pool": len(pool),
               "A": stats(P, lambda r: r["grade"] == "hit", lambda r: r["snapshot"]["market_odds"]),
               "M": stats(M, lambda x: x["grade"] == "hit", lambda x: x["market"]["market_odds"]),
               "MM": stats(MM, lambda x: x["grade"] == "hit", lambda x: x["market"]["market_odds"]),
               "MMc": stats(MMc, lambda x: x["grade"] == "hit", lambda x: x["market"]["market_odds"]),
               "MMc_games": len({x["game_pk"] for x in MMc}),
               "M_markets": dict(Counter(x["stat"] for x in M)), "A_markets": dict(Counter(r["snapshot"]["stat"] for r in P)),
               "M_mean_implied": float(np.mean([x["market"]["market_implied"] for x in M])) if M else None,
               "overlap_A_in_pool": len({r["id"] for r in P} & {x["candidate_id"] for x in pool})}
        days.append(rec)
    rng = np.random.default_rng(20261023)

    def summary(sub, arm):
        n = sum(d[arm]["n"] for d in sub)
        w = sum(d[arm]["wins"] for d in sub)
        p = sum(d[arm]["profit"] for d in sub)
        return {"n": n, "wins": w, "hit_rate": w / n if n else None, "roi": p / n if n else None}

    def boot(sub, arm):
        idx = rng.integers(0, len(sub), size=(10000, len(sub)))
        def agg(a, k):
            return np.array([[d[a][k] for d in sub]])[0][idx].sum(1)
        hr = agg(arm, "wins") / agg(arm, "n") - agg("A", "wins") / agg("A", "n")
        roi = agg(arm, "profit") / agg(arm, "n") - agg("A", "profit") / agg("A", "n")
        return {"d_hit_rate_ci": [float(np.percentile(hr, 2.5)), float(np.percentile(hr, 97.5))],
                "d_roi_ci": [float(np.percentile(roi, 2.5)), float(np.percentile(roi, 97.5))]}
    out = {"days": days, "source_board_sha256": shas}
    for label, sub in (("all_days", days), ("equal_count_days_M", [d for d in days if d["M"]["n"] == d["N"]]),
                       ("equal_count_days_MM", [d for d in days if d["MM"]["n"] == d["N"]]),
                       ("equal_count_days_MMc", [d for d in days if d["MMc"]["n"] == d["N"]])):
        arm = "M" if label == "all_days" else label.rsplit("_", 1)[1]
        out[label] = {"days": len(sub), "A": summary(sub, "A"), arm: summary(sub, arm),
                      "shortfall": sum(d["N"] - d[arm]["n"] for d in sub), **(boot(sub, arm) if len(sub) > 2 else {})}
    out["MM_all_days"] = summary(days, "MM")
    out["M_market_mix"] = dict(sum((Counter(d["M_markets"]) for d in days), Counter()))
    out["A_market_mix"] = dict(sum((Counter(d["A_markets"]) for d in days), Counter()))
    (HERE / "out" / "replacement.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("days", "source_board_sha256")}, indent=1))


if __name__ == "__main__":
    main()
