#!/usr/bin/env python3
"""Post-review verification checks (NOT part of the pre-registered analysis).

The independent statistical review of 88cb60ab5b raised findings that
depend on numbers the locked analysis did not report. This script
recomputes them from the same pinned artifacts (DATA_SHA) with the same
loaders, so the README corrections cite reproduced figures, not the
reviewer's word. Everything here is post hoc and descriptive; nothing here
changes the pre-registered verdict.

    python3 engineering/mlb_selection_overconfidence_20260924/review_checks.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analysis as A  # noqa: E402

PITCHER = {"strikeouts", "pitcher_outs", "nrfi", "combined_strikeouts"}


def family(r):
    return "pitcher" if r["market"] in PITCHER else "batter"


def gap_ci(rows, cluster=A.game_cluster):
    s = A.settled(rows)
    if not s:
        return {"n": 0}
    boot = A.cluster_bootstrap(s, A.gap, cluster)
    ci = boot.get("ci95") if isinstance(boot, dict) else boot
    return {"n": len(s), "gap": A.r4(A.gap(s)), "ci95": A.rci(ci) if ci else None}


def implied_audit(rows):
    out = defaultdict(Counter)
    for r in rows:
        if r["odds"] is None or r["implied"] is None:
            continue
        raw = A._implied(r["odds"])
        out[r["market"]]["raw" if abs(raw - r["implied"]) < 0.005 else "not_raw"] += 1
    return {str(k): dict(v) for k, v in sorted(out.items(), key=lambda kv: str(kv[0]))}


def realized_minus_fair(rows):
    s = [r for r in A.settled(rows) if r["implied"] is not None]
    if not s:
        return None
    return {"n": len(s), "mean_y_minus_implied": A.r4(sum(r["y"] - r["implied"] for r in s) / len(s))}


def main():
    pub, db = A.load_grades(A.DATA_SHA)
    db_ref = [r for r in db if r["status"] != "top_pick"]
    db_ref_hi = [r for r in db_ref if A.is_high(r["p"])]
    db_ref_elig_hi = [r for r in db_ref_hi if r["reliability"] in ("A", "B")
                      and not r["lineup_assumed"] and r["odds"] is not None]
    big = lambda r: (r["edge"] or 0) >= 0.10  # noqa: E731

    rep = {"data_sha": A.DATA_SHA, "note": "post hoc review checks; not pre-registered"}
    rep["F1_market_implied_is_raw_by_market"] = {
        "PUB": implied_audit(pub), "DB_ref_p>=0.60": implied_audit(db_ref_hi)}
    rep["F1_edge>=0.10_market_mix"] = {
        "PUB": dict(Counter(str(r["market"]) for r in A.settled(pub) if big(r))),
        "DB_ref_p>=0.60": dict(Counter(str(r["market"]) for r in A.settled(db_ref_hi) if big(r)))}
    rep["F1_edge>=0.10_gaps"] = {
        "PUB_all": gap_ci([r for r in pub if big(r)]),
        "DB_ref_hi_all": gap_ci([r for r in db_ref_hi if big(r)]),
        "DB_ref_hi_excl_combined_strikeouts": gap_ci(
            [r for r in db_ref_hi if big(r) and r["market"] != "combined_strikeouts"]),
        "PUB_pitcher": gap_ci([r for r in pub if big(r) and family(r) == "pitcher"]),
        "DB_ref_hi_pitcher_excl_combined": gap_ci(
            [r for r in db_ref_hi if big(r) and family(r) == "pitcher"
             and r["market"] != "combined_strikeouts"]),
    }
    rep["F1_batter_DB_ref_hi_edge_bands"] = dict(Counter(
        str(A.edge_band(r)) for r in A.settled(db_ref_hi) if family(r) == "batter"))
    rep["F1_realized_minus_market_implied_edge>=0.10_pitcher"] = {
        "PUB": realized_minus_fair([r for r in pub if big(r) and family(r) == "pitcher"]),
        "DB_ref_hi": realized_minus_fair([r for r in db_ref_hi if big(r) and family(r) == "pitcher"
                                          and r["market"] != "combined_strikeouts"])}
    rep["F2_DB_ref_p>=0.60"] = {"all": gap_ci(db_ref_hi), "eligible": gap_ci(db_ref_elig_hi)}

    # F4: Monte-Carlo sensitivity of the locked verdict's W upper bound.
    seeds = {}
    for seed in (1, 2, 3, 4):
        res = A.joint_decompose_bootstrap(pub, db_ref, A.game_cluster, seed=seed)
        seeds[str(seed)] = {k: A.rci(v) for k, v in res["ci95"].items() if k in ("W", "S_sel")}
    rep["F4_primary_by_seed"] = seeds
    date_res = A.joint_decompose_bootstrap(pub, db_ref, lambda r: r["date"])
    rep["F4_primary_date_clustered"] = {
        "n_clusters": date_res["n_clusters"],
        "ci95": {k: A.rci(v) for k, v in date_res["ci95"].items() if k in ("G", "W", "S_sel")}}

    out = os.path.join(A.HERE, "review_checks.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(rep, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
