#!/usr/bin/env python3
"""Analysis commit for PREREGISTRATION.md (single pre-registered analysis).

Reads the evaluation slates 2026-09-25..27 via `git show` at EVAL_SHA, applies
the coefficients frozen in coefficients.json (fit commit 063aea6bb5) without
refitting, and writes report.json. Run once.

    python3 engineering/mlb_market_anchor_20260925/analysis.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_SHA = "d50281cedb01dc85d0e44c2100bec3919af226f7"  # origin/main at analysis time
EVAL_DATES = ("2026-09-25", "2026-09-26", "2026-09-27")
FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
CLIP = 1e-4
B = 2000
SEED = 20260925
MIN_SETTLED = 1000
MIN_P0_60 = 150
SELECT_FLOOR = 0.60
MIN_PICKS_FOR_SIGNIFICANCE = 60
BANDS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0001)]


def _git(*args):
    return subprocess.check_output(["git", "-C", HERE, *args])


def git_json(path):
    return json.loads(_git("show", f"{EVAL_SHA}:{path}"))


def family(stat):
    return stat if stat in FAMILIES else "other"


def implied(odds):
    """Raw posted implied probability from American odds (pre-registered q)."""
    return (-odds) / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def clip(p):
    return min(max(p, CLIP), 1 - CLIP)


def logit(p):
    p = clip(p)
    return math.log(p / (1 - p))


def logistic(z):
    return 1.0 / (1.0 + math.exp(-z))


def y_of(grade):
    return 1 if grade == "hit" else 0 if grade == "miss" else None


def ll(p, y):
    p = clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def board_hash(board):
    """Canonical seal hash, as board_freeze._canonical_hash over the body."""
    body = {k: v for k, v in board.items() if k != "board_sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def profit(odds, y):
    """1-unit stake profit at American odds."""
    if not y:
        return -1.0
    return odds / 100.0 if odds > 0 else 100.0 / (-odds)


def load_fb_e(coef):
    rows, audit = [], {}
    for date in EVAL_DATES:
        board = git_json(f"output/board_freeze_{date}.json")
        graded = git_json(f"output/board_freeze_graded_{date}.json")
        recomputed = board_hash(board)
        if recomputed != board["board_sha256"] or graded["source_board_sha256"] != board["board_sha256"]:
            raise SystemExit(f"{date}: board/graded hash linkage failed")
        by_id = {r["candidate_id"]: r for r in board["records"]}
        grades = {r["candidate_id"]: r for r in graded["records"]}
        if set(grades) - set(by_id):
            raise SystemExit(f"{date}: graded records not on the frozen board")
        excluded = Counter()
        for cid, r in by_id.items():
            g = grades.get(cid)
            p0 = (r.get("prediction") or {}).get("hit_probability")
            odds = (r.get("market") or {}).get("market_odds")
            elig = r.get("eligibility") or {}
            y = y_of((g or {}).get("grade"))
            if elig.get("qc_status") != "kept":
                excluded["qc_status_not_kept"] += 1
            elif elig.get("lineup_assumed"):
                excluded["lineup_assumed"] += 1
            elif odds is None or p0 is None:
                excluded["missing_odds_or_probability"] += 1
            elif y is None:
                excluded["not_hit_or_miss:" + str((g or {}).get("reason") or (g or {}).get("grade"))] += 1
            else:
                fam = family(r.get("stat"))
                q = implied(odds)
                c1, c2 = coef[fam]["market_only"], coef[fam]["blend"]
                rows.append({
                    "id": cid, "date": date, "game_pk": str(r.get("game_pk")), "family": fam,
                    "stat": r.get("stat"), "odds": odds, "y": y, "q": q, "p0": p0,
                    "p1": logistic(c1["a"] + c1["c"] * logit(q)),
                    "p2": logistic(c2["a"] + c2["b"] * logit(p0) + c2["c"] * logit(q)),
                    "top_pick": (r.get("selector") or {}).get("recommendation_status") == "top_pick",
                })
        audit[date] = {"board_sha256": board["board_sha256"], "recomputed_board_sha256": recomputed,
                       "graded_source_board_sha256": graded["source_board_sha256"],
                       "graded_at": graded.get("graded_at"), "board_records": len(by_id),
                       "graded_records": len(grades),
                       "graded_grade_counts": dict(Counter(x.get("grade") for x in graded["records"])),
                       "fb_e_rows": sum(1 for x in rows if x["date"] == date),
                       "excluded": dict(sorted(excluded.items()))}
    return rows, audit


def paired(rows, arm_a, arm_b, rng):
    """Mean LL(arm_a) - LL(arm_b) with game-clustered percentile bootstrap."""
    d = [ll(r[arm_a], r["y"]) - ll(r[arm_b], r["y"]) for r in rows]
    clusters = defaultdict(list)
    for r, di in zip(rows, d):
        clusters[(r["date"], r["game_pk"])].append(di)
    keys = sorted(clusters)
    sums = np.array([sum(clusters[k]) for k in keys])
    counts = np.array([len(clusters[k]) for k in keys])
    draws = []
    for _ in range(B):
        idx = [rng.randrange(len(keys)) for _k in keys]
        draws.append(sums[idx].sum() / counts[idx].sum())
    return {"n": len(d), "n_clusters": len(keys), "mean_diff": float(np.mean(d)),
            "ci95": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))],
            "share_bootstrap_below_0": float(np.mean(np.array(draws) < 0))}


def scores(rows):
    out = {"n": len(rows), "base_rate": float(np.mean([r["y"] for r in rows])) if rows else None}
    for arm in ("p0", "p1", "p2"):
        out[arm] = {"log_loss": float(np.mean([ll(r[arm], r["y"]) for r in rows])) if rows else None,
                    "brier": float(np.mean([(clip(r[arm]) - r["y"]) ** 2 for r in rows])) if rows else None}
    return out


def reliability(rows):
    out = {}
    for arm in ("p0", "p1", "p2"):
        bands = []
        for lo, hi in BANDS:
            sub = [r for r in rows if lo <= r[arm] < hi]
            bands.append({"band": [lo, min(hi, 1.0)], "n": len(sub),
                          "mean_stated": float(np.mean([r[arm] for r in sub])) if sub else None,
                          "realized": float(np.mean([r["y"] for r in sub])) if sub else None})
        out[arm] = bands
    return out


def pick_summary(picks, arm):
    n = len(picks)
    if not n:
        return {"n": 0, "hits": 0, "hit_rate": None, "mean_stated": None,
                "stated_minus_realized": None, "units": 0.0, "roi": None}
    hits = sum(r["y"] for r in picks)
    stated = float(np.mean([r[arm] for r in picks]))
    units = sum(profit(r["odds"], r["y"]) for r in picks)
    return {"n": n, "hits": hits, "hit_rate": hits / n, "mean_stated": stated,
            "stated_minus_realized": stated - hits / n, "units": units, "roi": units / n}


def equal_volume(rows):
    per_slate, champ_all, chall_all = {}, [], []
    for date in EVAL_DATES:
        day = [r for r in rows if r["date"] == date]
        champ = [r for r in day if r["top_pick"]]
        n_d = len(champ)
        pool = sorted((r for r in day if r["p2"] >= SELECT_FLOOR), key=lambda r: (-(r["p2"] - r["q"]), r["id"]))
        chall = pool[:n_d]
        cids, hids = {r["id"] for r in champ}, {r["id"] for r in chall}
        per_slate[date] = {"N_d": n_d, "challenger_n": len(chall), "challenger_qualifying_pool": len(pool),
                           "overlap": len(cids & hids)}
        champ_all += champ
        chall_all += chall
    cids, hids = {r["id"] for r in champ_all}, {r["id"] for r in chall_all}
    added = [r for r in chall_all if r["id"] not in cids]
    removed = [r for r in champ_all if r["id"] not in hids]
    champion = pick_summary(champ_all, "p0")
    challenger = pick_summary(chall_all, "p2")
    enough = min(champion["n"], challenger["n"]) >= MIN_PICKS_FOR_SIGNIFICANCE
    return {"per_slate": per_slate, "champion": champion, "challenger": challenger,
            "overlap": len(cids & hids),
            "added": {"n": len(added), "hits": sum(r["y"] for r in added),
                      "ids": sorted(r["id"] for r in added)},
            "removed": {"n": len(removed), "hits": sum(r["y"] for r in removed),
                        "ids": sorted(r["id"] for r in removed)},
            "significance_claim_allowed": enough,
            "more_winners_at_equal_volume": ("NOT YET PROVEN" if not enough else
                                             challenger["hits"] > champion["hits"])}


def main():
    fitted = json.load(open(os.path.join(HERE, "coefficients.json"), encoding="utf-8"))
    coef = fitted["families"]
    rows, audit = load_fb_e(coef)
    rng = random.Random(SEED)
    primary = paired(rows, "p2", "p1", rng)
    secondary_a = paired(rows, "p2", "p0", rng)
    n_p0_60 = sum(1 for r in rows if r["p0"] >= 0.60)
    b_ci_above_0 = sorted(f for f, d in coef.items() if d["blend_ci95"]["b"][0] > 0)
    sufficient = len(rows) >= MIN_SETTLED and n_p0_60 >= MIN_P0_60
    if not sufficient:
        verdict = "insufficient n"
    elif primary["ci95"][1] < 0 and b_ci_above_0:
        verdict = "success"
    else:
        verdict = "no demonstrated edge beyond the market"
    by_fam = defaultdict(list)
    for r in rows:
        by_fam[r["family"]].append(r)
    report = {
        "prereg": "PREREGISTRATION.md", "fit_commit": "063aea6bb5", "eval_sha": EVAL_SHA,
        "eval_dates": list(EVAL_DATES), "B": B, "seed": SEED, "clip": CLIP,
        "slate_audit": audit,
        "fb_e": {"n_settled": len(rows), "n_p0_ge_0_60": n_p0_60,
                 "n_games": len({(r["date"], r["game_pk"]) for r in rows}),
                 "family_counts": dict(Counter(r["family"] for r in rows))},
        "minimums": {"min_settled": MIN_SETTLED, "min_p0_ge_0_60": MIN_P0_60, "met": sufficient},
        "primary": {"statistic": "mean LL(p2) - LL(p1)", **primary,
                    "fit_families_with_b_ci_above_0": b_ci_above_0, "verdict": verdict},
        "secondary_a": {"statistic": "mean LL(p2) - LL(p0)", **secondary_a},
        "secondary_b_equal_volume": equal_volume(rows),
        "descriptive": {"pooled": scores(rows),
                        "by_family": {f: scores(by_fam[f]) for f in sorted(by_fam)},
                        "reliability_pooled": reliability(rows)},
        "clv": "NOT AVAILABLE (no closing line captured for frozen records)",
        "postseason": "not analyzed here; separately labelled regime, never pooled",
    }
    with open(os.path.join(HERE, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: report[k] for k in ("fb_e", "minimums", "primary", "secondary_a")}, indent=1))
    ev = report["secondary_b_equal_volume"]
    print(json.dumps({k: ev[k] for k in ("per_slate", "champion", "challenger", "overlap",
                                         "more_winners_at_equal_volume")}, indent=1))


if __name__ == "__main__":
    main()
