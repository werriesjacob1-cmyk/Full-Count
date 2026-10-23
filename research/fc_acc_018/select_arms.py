#!/usr/bin/env python3
"""FC-ACC-018 step 2b: arms and volume feasibility (PREREG §1-2). Reads NO outcome.

A     = actual published Top Picks per slate (registry + public grades; only the settled/void
        status is used here, to define N; win/loss is not read).
A_cap = A after one-per-player and <=2-per-game (champion's higher model probability kept first).
B     = market-only favorites from U, ranked by implied probability.
C     = frozen hybrid: devig >= 0.60, price >= -350, model >= 0.60, ranked by model.
B and C: one per player, <= 2 per game, greedy to target, shortfall reported.

Usage: python3 select_arms.py <repo root>    (reads out/universe.json)
Writes out/selection.json.
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOLD, GAME_CAP = 1.08, 2


def implied(a):
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def champion(repo):
    reg = json.loads((Path(repo) / "data/public_top_picks/registry.json").read_text())["entries"]
    status = {}
    for f in sorted(glob.glob(str(Path(repo) / "results/grades_*.json"))):
        for p in json.loads(Path(f).read_text()).get("public_top_picks") or []:
            status[p["id"]] = "settled" if p.get("grade") in ("hit", "miss") else p.get("grade")
    by = defaultdict(list)
    for cid, e in reg.items():
        s = e["snapshot"]
        by[e["slate_date"]].append({"id": cid, "game_pk": int(s["game_pk"]), "player_id": s.get("player_id"),
                                    "stat": s["projection"]["stat"], "needs": s["projection"]["needs"],
                                    "american": s["market_odds"], "market_implied": s.get("market_implied"),
                                    "model": s["hit_probability"], "status": status.get(cid)})
    return by


def constrained(rows, key, target):
    sel, players, games = [], set(), Counter()
    for r in sorted(rows, key=key):
        if len(sel) >= target:
            break
        if r["player_id"] in players or games[r["game_pk"]] >= GAME_CAP:
            continue
        sel.append(r)
        players.add(r["player_id"])
        games[r["game_pk"]] += 1
    return sel


def b_key(r):
    return (-implied(r["american"]), str(r["id"]))


def c_ok(r):
    return r["market_implied"] is not None and r["market_implied"] / HOLD >= 0.60 and r["american"] >= -350 \
        and r["model"] is not None and r["model"] >= 0.60


def c_key(r):
    return (-r["model"], str(r["id"]))


def conc(sel):
    g = Counter(r["game_pk"] for r in sel)
    return {"games": len(g), "max_per_game": max(g.values()) if g else 0,
            "share_price_le_-250": (sum(r["american"] <= -250 for r in sel) / len(sel)) if sel else None}


def main(repo):
    U = json.loads((HERE / "out" / "universe.json").read_text())
    cand = defaultdict(list)
    for x in U["candidates"]:
        cand[x["slate"]].append(x)
    champ = champion(repo)
    slates = sorted(U["per_slate_gates"])
    res = {"slates": {}}
    for d in slates:
        A = [r for r in champ.get(d, []) if r["status"] == "settled"]
        N = len(A)
        A_cap = constrained(A, lambda r: (-r["model"], r["id"]), N)
        Ncap = len(A_cap)
        pool = cand[d]
        cpool = [r for r in pool if c_ok(r)]
        rec = {"N": N, "N_cap": Ncap, "champion_removed_by_cap": N - Ncap,
               "universe": len(pool), "universe_games": len({r["game_pk"] for r in pool}),
               "universe_players": len({r["player_id"] for r in pool}),
               "universe_by_market": dict(Counter(r["stat"] for r in pool)),
               "C_gate_pass": len(cpool), "C_gate_players": len({r["player_id"] for r in cpool}),
               "C_gate_games": len({r["game_pk"] for r in cpool}),
               "gates": U["per_slate_gates"][d], "arms": {}}
        for bench, target in (("original", N), ("cap", Ncap)):
            B = constrained(pool, b_key, target)
            C = constrained(cpool, c_key, target)
            rec["arms"][bench] = {"target": target,
                                  "B": {"ids": [r["id"] for r in B], "filled": len(B), "shortfall": target - len(B), **conc(B)},
                                  "C": {"ids": [r["id"] for r in C], "filled": len(C), "shortfall": target - len(C), **conc(C)}}
        rec["A_ids"] = [r["id"] for r in A]
        rec["A_cap_ids"] = [r["id"] for r in A_cap]
        rec["A_conc"] = conc(A)
        res["slates"][d] = rec
    tot = {}
    for bench in ("original", "cap"):
        t = sum(s["arms"][bench]["target"] for s in res["slates"].values())
        for arm in ("B", "C"):
            f = sum(s["arms"][bench][arm]["filled"] for s in res["slates"].values())
            tot[f"{arm}_{bench}"] = {"target": t, "filled": f, "fill_share": f / t if t else None}
    res["totals"] = tot
    (HERE / "out" / "selection.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = main(sys.argv[1])
    print(json.dumps(r["totals"], indent=1))
