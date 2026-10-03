#!/usr/bin/env python3
"""Fit commit for PREREGISTRATION.md (market-anchored vs market-only arms).

Reads ONLY slates dated <= 2026-09-23 (the pre-registered fit sample) via
`git show` at FIT_SHA, fits per-family penalized logistic regressions, and
writes coefficients.json. Touches no evaluation outcome (evaluation slates
are 2026-09-25..27). After this commit the coefficients are frozen.

    python3 engineering/mlb_market_anchor_20260925/fit.py
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIT_SHA = "30731f102095fed75ed6a399c5717558f37bbd56"  # origin/main when pre-registered
FIT_LAST_DATE = "2026-09-23"
FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
L2 = 1.0
CLIP = 1e-4
B = 2000
SEED = 20260925


def _git(*args):
    return subprocess.check_output(["git", "-C", HERE, *args])


def git_json(path):
    return json.loads(_git("show", f"{FIT_SHA}:{path}"))


def git_ls(prefix):
    return _git("ls-tree", "--full-tree", "--name-only", FIT_SHA, prefix + "/").decode().split()


def family(stat):
    return stat if stat in FAMILIES else "other"


def implied(odds):
    """Raw posted implied probability from American odds (pre-registered q)."""
    return (-odds) / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def logit(p):
    p = min(max(p, CLIP), 1 - CLIP)
    return float(np.log(p / (1 - p)))


def y_of(grade):
    return 1 if grade == "hit" else 0 if grade == "miss" else None


def canonical_id(x):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from dashboard.live_state import canonical_prop_id
    try:
        return canonical_prop_id(x)
    except Exception:
        return None


def row(cid, date, game_pk, stat, p, odds, y, source):
    if cid is None or p is None or odds is None or y is None or stat is None:
        return None
    return {"id": cid, "date": date, "game_pk": str(game_pk), "family": family(stat),
            "lp": logit(p), "lq": logit(implied(odds)), "y": y, "source": source}


def load_fit_sample():
    names = set(git_ls("output")) | set(git_ls("results"))
    fb, db = {}, {}
    for path in sorted(names):
        base = os.path.basename(path)
        if base.startswith("board_freeze_graded_") and base.endswith(".json"):
            date = base[len("board_freeze_graded_"):-5]
            if date > FIT_LAST_DATE:
                continue
            graded = git_json(path)
            board = git_json(f"output/board_freeze_{date}.json")
            if graded.get("source_board_sha256") != board.get("board_sha256"):
                continue
            for x in graded["records"]:
                r = row(x.get("candidate_id"), date, x.get("game_pk"), x.get("stat"),
                        (x.get("prediction") or {}).get("hit_probability"),
                        (x.get("market") or {}).get("market_odds"), y_of(x.get("grade")), "FB")
                if r:
                    fb[r["id"]] = r
        elif base.startswith("grades_") and base.endswith(".json") and path.startswith("results/"):
            date = base[len("grades_"):-5]
            if date > FIT_LAST_DATE:
                continue
            for x in git_json(path).get("picks") or []:
                if x.get("recommendation_status") is None:
                    continue
                r = row(canonical_id(x), date, x.get("game_pk"),
                        (x.get("projection") or {}).get("stat"), x.get("hit_probability"),
                        x.get("market_odds"), y_of(x.get("grade")), "DB")
                if r:
                    db[r["id"]] = r
    merged = dict(db)
    merged.update(fb)  # frozen-board record preferred
    return list(merged.values()), {"n_fb": len(fb), "n_db": len(db),
                                   "n_overlap": len(set(fb) & set(db)), "n_merged": len(merged)}


def fit_logistic(X, y, penalized):
    """Newton-Raphson; L2 penalty on the non-intercept columns listed in penalized."""
    beta = np.zeros(X.shape[1])
    pen = np.zeros(X.shape[1])
    pen[list(penalized)] = L2
    for _ in range(100):
        mu = 1 / (1 + np.exp(-(X @ beta)))
        grad = X.T @ (y - mu) - pen * beta
        hess = (X * (mu * (1 - mu))[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


def design(rows, arm):
    lq = np.array([r["lq"] for r in rows])
    ones = np.ones(len(rows))
    if arm == "market_only":
        return np.column_stack([ones, lq]), [1]
    lp = np.array([r["lp"] for r in rows])
    return np.column_stack([ones, lp, lq]), [1, 2]


def fit_family(rows, rng):
    y = np.array([r["y"] for r in rows], dtype=float)
    out = {"n": len(rows), "n_games": len({(r["date"], r["game_pk"]) for r in rows}),
           "base_rate": float(y.mean())}
    for arm, names in (("market_only", ("a", "c")), ("blend", ("a", "b", "c"))):
        X, pen = design(rows, arm)
        out[arm] = dict(zip(names, map(float, fit_logistic(X, y, pen))))
    clusters = defaultdict(list)
    for r in rows:
        clusters[(r["date"], r["game_pk"])].append(r)
    keys = sorted(clusters)
    draws = {"b": [], "c": []}
    for _ in range(B):
        sample = [r for _k in keys for r in clusters[keys[rng.randrange(len(keys))]]]
        ys = np.array([r["y"] for r in sample], dtype=float)
        if ys.min() == ys.max():
            continue
        X, pen = design(sample, "blend")
        beta = fit_logistic(X, ys, pen)
        draws["b"].append(beta[1])
        draws["c"].append(beta[2])
    out["blend_ci95"] = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
                         for k, v in draws.items()}
    out["n_boot"] = len(draws["b"])
    return out


def main():
    rows, meta = load_fit_sample()
    assert all(r["date"] <= FIT_LAST_DATE for r in rows)
    by_fam = defaultdict(list)
    for r in rows:
        by_fam[r["family"]].append(r)
    rng = random.Random(SEED)
    rep = {"prereg": "PREREGISTRATION.md", "fit_sha": FIT_SHA, "fit_last_date": FIT_LAST_DATE,
           "l2": L2, "clip": CLIP, "B": B, "seed": SEED, "sample": meta,
           "date_range": [min(r["date"] for r in rows), max(r["date"] for r in rows)],
           "families": {f: fit_family(by_fam[f], rng) for f in (*FAMILIES, "other")},
           "source_mix": {f: dict(Counter(r["source"] for r in by_fam[f])) for f in by_fam}}
    with open(os.path.join(HERE, "coefficients.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({f: {k: v for k, v in d.items() if k in ("n", "blend", "blend_ci95")}
                      for f, d in rep["families"].items()}, indent=1))


if __name__ == "__main__":
    main()
