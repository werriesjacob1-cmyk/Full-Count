#!/usr/bin/env python3
"""FC-ACC-018 steps 3-6: grade the frozen selections and apply PREREG §4-5 exactly.

Outcomes: official MLB StatsAPI regular-season game logs (FC-ACC-014 raw cache retrieved
2026-10-22). A uses its PUBLIC grades; A is also re-graded the same way for a consistency check.
Prices are the recorded dashboard / publication prices: UNAUTHENTICATED (not FC-OPS-001).

Usage: python3 evaluate.py <repo root> <raw StatsAPI dir>
Writes out/evaluation.json.
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import select_arms as SA  # noqa: E402

HOLD, REPS, SEED = 1.08, 10000, 20261024
P_DEV_DATES = {"2026-08-20", "2026-08-25", "2026-08-30", "2026-09-04", "2026-09-09", "2026-09-15", "2026-09-20", "2026-09-25"}
P_DEV_FROM = "2026-09-20"


def dec(a):
    return 1 + (100 / -a if a < 0 else a / 100)


def logs(raw):
    bat, pit = {}, {}
    for kind, store in (("blogs", bat), ("plogs", pit)):
        for f in sorted(glob.glob(str(Path(raw) / f"{kind}_2026_*.json"))):
            for person in json.loads(Path(f).read_text()).get("people", []):
                for block in person.get("stats", []):
                    for s in block.get("splits", []):
                        if s.get("gameType") == "R":
                            store[(int(s["game"]["gamePk"]), int(person["id"]))] = s["stat"]
    return bat, pit


def grade(row, bat, pit):
    """WIN / LOSS / VOID from official logs (PREREG §4)."""
    key = (int(row["game_pk"]), int(row["player_id"]))
    n = int(row["needs"])
    if row["stat"] in ("hits", "hits_runs_rbis"):
        s = bat.get(key)
        if not s or int(s.get("plateAppearances") or 0) == 0:
            return "VOID"
        v = int(s.get("hits") or 0) + (int(s.get("runs") or 0) + int(s.get("rbi") or 0) if row["stat"] == "hits_runs_rbis" else 0)
    else:
        s = pit.get(key)
        if not s or int(s.get("gamesStarted") or 0) != 1:
            return "VOID"
        v = int(s.get("strikeOuts") or 0) if row["stat"] == "strikeouts" else int(s.get("outs") or 0)
    return "WIN" if v >= n else "LOSS"


def arm_stats(rows, res_of):
    w = l = v = 0
    prof = exp = 0.0
    for r in rows:
        g = res_of(r)
        if g == "WIN":
            w += 1; prof += dec(r["american"]) - 1
        elif g == "LOSS":
            l += 1; prof -= 1
        else:
            v += 1
            continue
        exp += (r["market_implied"] or 0) / HOLD
    n = w + l
    return {"picks": len(rows), "settled": n, "wins": w, "losses": l, "void": v,
            "hit_rate": w / n if n else None, "roi_unauthenticated": prof / n if n else None,
            "profit": prof, "market_expected_wins": exp}


def main(repo, raw):
    sel = json.loads((HERE / "out" / "selection.json").read_text())
    U = json.loads((HERE / "out" / "universe.json").read_text())
    rows_by = {(x["slate"], x["id"]): x for x in U["candidates"]}
    champ = SA.champion(repo)
    pub_grade = {}
    for f in sorted(glob.glob(str(Path(repo) / "results/grades_*.json"))):
        for p in json.loads(Path(f).read_text()).get("public_top_picks") or []:
            pub_grade[p["id"]] = {"hit": "WIN", "miss": "LOSS"}.get(p.get("grade"), "VOID")
    bat, pit = logs(raw)
    # consistency of the StatsAPI grader against public grades on the champion picks
    agree = Counter()
    for d, rs in champ.items():
        if d > SA.__dict__.get("LAST", "2026-09-27"):
            continue
        for r in rs:
            if pub_grade.get(r["id"]) in ("WIN", "LOSS"):
                g = grade(r, bat, pit)
                agree["agree" if g == pub_grade[r["id"]] else ("statsapi_void" if g == "VOID" else "disagree")] += 1
    consistency = agree["agree"] / max(1, agree["agree"] + agree["disagree"])
    per = {}
    for d, s in sel["slates"].items():
        A = {r["id"]: r for r in champ[d]}
        rec = {}
        for bench, aid in (("original", "A_ids"), ("cap", "A_cap_ids")):
            a_rows = [A[i] for i in s[aid]]
            b_rows = [rows_by[(d, i)] for i in s["arms"][bench]["B"]["ids"]]
            c_rows = [rows_by[(d, i)] for i in s["arms"][bench]["C"]["ids"]]
            rec[bench] = {"A": a_rows, "B": b_rows, "C": c_rows}
        per[d] = rec

    def res_A(r):
        return pub_grade.get(r["id"], "VOID")

    def res_X(r):
        return grade(r, bat, pit)

    periods = {"P1_least_exposed": [d for d in per if d not in P_DEV_DATES and d < P_DEV_FROM],
               "P2_all_graded": sorted(per), "P_dev_only": [d for d in per if d in P_DEV_DATES or d >= P_DEV_FROM]}
    rng = np.random.default_rng(SEED)
    out = {"grading_consistency": {**agree, "agreement_share": consistency},
           "source": {"raw_logs_files": len(glob.glob(str(Path(raw) / "blogs_2026_*.json"))) + len(glob.glob(str(Path(raw) / "plogs_2026_*.json")))},
           "periods": {}}

    def cells(days, bench):
        """per-slate and per-game cells: arm -> (wins, settled, profit)."""
        S, Gm = [], defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0]))
        for d in days:
            row = {}
            for arm in ("A", "B", "C"):
                f = res_A if arm == "A" else res_X
                st = arm_stats(per[d][bench][arm], f)
                row[arm] = (st["wins"], st["settled"], st["profit"])
                for r in per[d][bench][arm]:
                    g = f(r)
                    if g in ("WIN", "LOSS"):
                        c = Gm[(d, int(r["game_pk"]))][arm]
                        c[0] += g == "WIN"; c[1] += 1; c[2] += (dec(r["american"]) - 1) if g == "WIN" else -1
            S.append(row)
        return S, Gm

    def boot(S, Gm, x, y):
        sv = np.array([[r[x][0], r[x][1], r[y][0], r[y][1]] for r in S], float)
        i = rng.integers(0, len(sv), (REPS, len(sv)))
        t = sv[i].sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            ds = t[:, 0] / t[:, 1] - t[:, 2] / t[:, 3]
        gv = np.array([[c[x][0], c[x][1], c[y][0], c[y][1]] for c in Gm.values()], float)
        j = rng.integers(0, len(gv), (REPS, len(gv)))
        u = gv[j].sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            dg = u[:, 0] / u[:, 1] - u[:, 2] / u[:, 3]
        q = lambda a: [float(np.nanpercentile(a, 2.5)), float(np.nanpercentile(a, 97.5))]  # noqa: E731
        tot = sv.sum(0)
        return {"diff": float(tot[0] / tot[1] - tot[2] / tot[3]) if tot[1] and tot[3] else None,
                "ci_slate": q(ds), "ci_game": q(dg), "slates": len(sv), "games": len(gv)}

    for pname, days in periods.items():
        P = {}
        for bench in ("original", "cap"):
            arms = {arm: arm_stats([r for d in days for r in per[d][bench][arm]], res_A if arm == "A" else res_X)
                    for arm in ("A", "B", "C")}
            S, Gm = cells(days, bench)
            target = sum(sel["slates"][d]["arms"][bench]["target"] for d in days)
            fill = {arm: sum(sel["slates"][d]["arms"][bench][arm]["filled"] for d in days) / target if target else None
                    for arm in ("B", "C")}
            ids = lambda arm: {(d, r["id"]) for d in days for r in per[d][bench][arm]}  # noqa: E731
            add_rm = {}
            for x, y in (("C", "A"), ("B", "A"), ("C", "B")):
                X_, Y_ = ids(x), ids(y)
                fx = res_A if x == "A" else res_X
                fy = res_A if y == "A" else res_X
                lk = {(d, r["id"]): r for d in days for arm in (x, y) for r in per[d][bench][arm]}
                added = X_ - Y_
                removed = Y_ - X_
                add_rm[f"{x}_vs_{y}"] = {"added": len(added), "added_wins": sum(fx(lk[k]) == "WIN" for k in added),
                                         "removed": len(removed), "removed_wins": sum(fy(lk[k]) == "WIN" for k in removed)}
            P[bench] = {"slates": len(days), "target": target, "fill": fill, "arms": arms, "added_removed": add_rm,
                        "C_minus_A": boot(S, Gm, "C", "A"), "B_minus_A": boot(S, Gm, "B", "A"),
                        "C_minus_B": boot(S, Gm, "C", "B"),
                        "by_month": {m: {arm: arm_stats([r for d in days if d[:7] == m for r in per[d][bench][arm]],
                                                        res_A if arm == "A" else res_X) for arm in ("A", "B", "C")}
                                     for m in sorted({d[:7] for d in days})},
                        "by_market_C": {k: arm_stats([r for d in days for r in per[d][bench]["C"] if r["stat"] == k], res_X)
                                        for k in sorted({r["stat"] for d in days for r in per[d][bench]["C"]})},
                        "by_market_B": {k: arm_stats([r for d in days for r in per[d][bench]["B"] if r["stat"] == k], res_X)
                                        for k in sorted({r["stat"] for d in days for r in per[d][bench]["B"]})}}
        out["periods"][pname] = {"dates": days, **P}
    # frozen verdict (PREREG §5): primary = P1, cap benchmark, C vs A_cap
    p1, p2 = out["periods"]["P1_least_exposed"]["cap"], out["periods"]["P2_all_graded"]["cap"]
    v = p1["C_minus_A"]
    vol_ok = (p1["fill"]["C"] or 0) >= 0.95
    roi_ok = (p1["arms"]["C"]["roi_unauthenticated"] or -9) >= (p1["arms"]["A"]["roi_unauthenticated"] or 9) - 0.02
    if not vol_ok or v["diff"] is None or v["diff"] <= 0:
        verdict = "STOP"
    elif (v["ci_slate"][0] > 0 and v["ci_game"][0] > 0 and roi_ok and (p2["C_minus_A"]["diff"] or 0) > 0
          and consistency >= 0.98):
        verdict = "GO"
    else:
        verdict = "MODIFY"
    cb = p1["C_minus_B"]
    out["frozen_verdict"] = {"verdict": verdict, "volume_ok_P1_cap": vol_ok, "roi_ok": roi_ok,
                             "consistency_ok": consistency >= 0.98,
                             "model_alpha_C_over_B": bool(cb["diff"] is not None and cb["ci_slate"][0] > 0 and cb["ci_game"][0] > 0)}
    (HERE / "out" / "evaluation.json").write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    o = main(sys.argv[1], sys.argv[2])
    print(json.dumps(o["frozen_verdict"], indent=1), json.dumps(o["grading_consistency"]))
