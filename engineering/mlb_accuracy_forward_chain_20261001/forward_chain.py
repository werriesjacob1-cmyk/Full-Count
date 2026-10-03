#!/usr/bin/env python3
"""Forward-chaining (rolling-origin) accuracy study: does the MLB model's
probability add accuracy beyond the posted price, across the whole 2026
regular season? EXPLORATORY, NOT PRE-REGISTERED (the pre-09-24 rows were
already examined in aggregate by PR #201 and the #217 fit). Research only.

Rules (fixed in this file before its first run):
- Rows: the #217 fit-sample construction (frozen-board records whose graded
  file links to the board by sha, plus final-run board picks with a
  recommendation status; deduplicated by canonical id, frozen-board preferred),
  extended to every regular-season date 2026-08-04..2026-09-27. Requires
  market_odds, hit_probability and a hit or miss grade. Read via `git show` at
  DATA_SHA.
- q is the raw posted implied probability from American odds (as #217).
- Folds: ISO weeks. Week k is predicted only from coefficients fitted on rows
  dated before week k's Monday. The first two weeks are training-only.
- Arms, per family (hits, hits_runs_rbis, strikeouts, pitcher_outs, other;
  a family with < MIN_FAMILY_TRAIN training rows uses the pooled fit):
    p0    the model's hit_probability (no fit)
    pq    q (no fit)
    p1    logistic(a + c*logit q)                market-only recalibration
    p2    logistic(a + b*logit p0 + c*logit q)   blend
    pcal  logistic(a + b*logit p0)               model-only recalibration
  Penalized Newton-Raphson exactly as #217's fit.py (L2 1.0 on slopes,
  clip 1e-4).
- Metrics on the pooled out-of-sample rows: log loss and Brier per arm; paired
  mean log-loss differences with a game-clustered percentile bootstrap
  (B=2000, seed 20261001): p2-p1 (model adds beyond price?), p2-p0, pcal-p0,
  p1-p0, p1-pq.
- Selection, descriptive: per date, champion = rows whose recommendation
  status is top_pick; challenger = the same number of rows ranked by p2-q
  among p2 >= 0.60, no backfill. Hits, stated-minus-realized, 1-unit ROI at
  market_odds.

    python3 engineering/mlb_accuracy_forward_chain_20261001/forward_chain.py
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA_SHA = "58abe9f2e5a2b69d0f8bc98c22c8bafe7b00a224"  # origin/main when this study started
FIRST, LAST = "2026-08-04", "2026-09-27"  # regular season with graded data
FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
L2, CLIP, B, SEED = 1.0, 1e-4, 2000, 20261001
MIN_FAMILY_TRAIN = 50
SELECT_FLOOR = 0.60


def _git(*args):
    return subprocess.check_output(["git", "-C", HERE, *args])


def git_json(path):
    return json.loads(_git("show", f"{DATA_SHA}:{path}"))


def git_ls(prefix):
    return _git("ls-tree", "--full-tree", "--name-only", DATA_SHA, prefix + "/").decode().split()


def family(stat):
    return stat if stat in FAMILIES else "other"


def implied(odds):
    return (-odds) / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def clip(p):
    return min(max(p, CLIP), 1 - CLIP)


def logit(p):
    p = clip(p)
    return math.log(p / (1 - p))


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


def row(cid, d, game_pk, stat, p, odds, y, status, source):
    if cid is None or p is None or odds is None or y is None or stat is None:
        return None
    return {"id": cid, "date": d, "game_pk": str(game_pk), "family": family(stat), "p0": p,
            "odds": odds, "q": implied(odds), "y": y, "status": status, "source": source}


def load_rows():
    names = set(git_ls("output")) | set(git_ls("results"))
    fb, db = {}, {}
    for path in sorted(names):
        base = os.path.basename(path)
        if base.startswith("board_freeze_graded_") and base.endswith(".json"):
            d = base[len("board_freeze_graded_"):-5]
            if not FIRST <= d <= LAST:
                continue
            graded = git_json(path)
            board = git_json(f"output/board_freeze_{d}.json")
            if graded.get("source_board_sha256") != board.get("board_sha256"):
                continue
            for x in graded["records"]:
                r = row(x.get("candidate_id"), d, x.get("game_pk"), x.get("stat"),
                        (x.get("prediction") or {}).get("hit_probability"),
                        (x.get("market") or {}).get("market_odds"), y_of(x.get("grade")),
                        (x.get("selector") or {}).get("recommendation_status"), "FB")
                if r:
                    fb[r["id"]] = r
        elif base.startswith("grades_") and base.endswith(".json") and path.startswith("results/"):
            d = base[len("grades_"):-5]
            if not FIRST <= d <= LAST:
                continue
            for x in git_json(path).get("picks") or []:
                if x.get("recommendation_status") is None:
                    continue
                r = row(canonical_id(x), d, x.get("game_pk"), (x.get("projection") or {}).get("stat"),
                        x.get("hit_probability"), x.get("market_odds"), y_of(x.get("grade")),
                        x.get("recommendation_status"), "DB")
                if r:
                    db[r["id"]] = r
    merged = dict(db)
    merged.update(fb)
    return sorted(merged.values(), key=lambda r: (r["date"], r["id"])), {
        "n_fb": len(fb), "n_db": len(db), "n_overlap": len(set(fb) & set(db)), "n_merged": len(merged)}


def fit_logistic(X, y):
    beta = np.zeros(X.shape[1])
    pen = np.full(X.shape[1], L2)
    pen[0] = 0.0
    for _ in range(100):
        mu = 1 / (1 + np.exp(-(X @ beta)))
        grad = X.T @ (y - mu) - pen * beta
        hess = (X * (mu * (1 - mu))[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


ARMS = {"p1": ("lq",), "p2": ("lp", "lq"), "pcal": ("lp",)}


def design(rows, cols):
    return np.column_stack([np.ones(len(rows))] + [[r[c] for r in rows] for c in cols])


def fit_all(train):
    out = {}
    groups = defaultdict(list)
    for r in train:
        groups[r["family"]].append(r)
    for arm, cols in ARMS.items():
        pooled = fit_logistic(design(train, cols), np.array([r["y"] for r in train], float))
        out[arm] = {"_pooled": pooled}
        for f, rs in groups.items():
            if len(rs) >= MIN_FAMILY_TRAIN and 0 < sum(r["y"] for r in rs) < len(rs):
                out[arm][f] = fit_logistic(design(rs, cols), np.array([r["y"] for r in rs], float))
    return out


def monday(d):
    x = date.fromisoformat(d)
    return x - timedelta(days=x.weekday())


def forward_chain(rows):
    for r in rows:
        r["lp"], r["lq"] = logit(r["p0"]), logit(r["q"])
    weeks = sorted({monday(r["date"]) for r in rows})
    tested, folds = [], []
    for wk in weeks[2:]:
        train = [r for r in rows if monday(r["date"]) < wk]
        test = [r for r in rows if monday(r["date"]) == wk]
        coef = fit_all(train)
        for r in test:
            r = dict(r)
            r["pq"] = r["q"]
            for arm, cols in ARMS.items():
                beta = coef[arm].get(r["family"], coef[arm]["_pooled"])
                z = beta[0] + sum(b * r[c] for b, c in zip(beta[1:], cols))
                r[arm] = 1 / (1 + math.exp(-z))
            tested.append(r)
        folds.append({"week_of": wk.isoformat(), "n_train": len(train), "n_test": len(test),
                      "families_fitted": sorted(k for k in coef["p2"] if k != "_pooled")})
    return tested, folds


def ll(p, y):
    p = clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def paired(rows, a, b, rng):
    d = [ll(r[a], r["y"]) - ll(r[b], r["y"]) for r in rows]
    cl = defaultdict(list)
    for r, di in zip(rows, d):
        cl[(r["date"], r["game_pk"])].append(di)
    keys = sorted(cl)
    sums = np.array([sum(cl[k]) for k in keys])
    cnts = np.array([len(cl[k]) for k in keys])
    draws = []
    for _ in range(B):
        idx = [rng.randrange(len(keys)) for _k in keys]
        draws.append(sums[idx].sum() / cnts[idx].sum())
    return {"n": len(d), "n_clusters": len(keys), "mean_diff": float(np.mean(d)),
            "ci95": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))]}


def scores(rows):
    if not rows:
        return {"n": 0}
    out = {"n": len(rows), "base_rate": float(np.mean([r["y"] for r in rows]))}
    for arm in ("p0", "pq", "p1", "p2", "pcal"):
        out[arm] = {"log_loss": float(np.mean([ll(r[arm], r["y"]) for r in rows])),
                    "brier": float(np.mean([(clip(r[arm]) - r["y"]) ** 2 for r in rows])),
                    "mean_stated": float(np.mean([r[arm] for r in rows]))}
    return out


def profit(odds, y):
    return (odds / 100.0 if odds > 0 else 100.0 / (-odds)) if y else -1.0


def pick_summary(picks, arm):
    if not picks:
        return {"n": 0}
    hits = sum(r["y"] for r in picks)
    units = sum(profit(r["odds"], r["y"]) for r in picks)
    stated = float(np.mean([r[arm] for r in picks]))
    return {"n": len(picks), "hits": hits, "hit_rate": hits / len(picks), "mean_stated": stated,
            "stated_minus_realized": stated - hits / len(picks), "units": units, "roi": units / len(picks)}


def selection(rows):
    champ, chall = [], []
    for d in sorted({r["date"] for r in rows}):
        day = [r for r in rows if r["date"] == d]
        c = [r for r in day if r["status"] == "top_pick"]
        pool = sorted((r for r in day if r["p2"] >= SELECT_FLOOR), key=lambda r: (-(r["p2"] - r["q"]), r["id"]))
        champ += c
        chall += pool[:len(c)]
    cids, hids = {r["id"] for r in champ}, {r["id"] for r in chall}
    return {"champion_top_pick_p0": pick_summary(champ, "p0"), "challenger_blend_p2": pick_summary(chall, "p2"),
            "overlap": len(cids & hids),
            "added": pick_summary([r for r in chall if r["id"] not in cids], "p2"),
            "removed": pick_summary([r for r in champ if r["id"] not in hids], "p0")}


def main():
    rows, meta = load_rows()
    tested, folds = forward_chain(rows)
    rng = random.Random(SEED)
    comps = {name: paired(tested, a, b, rng) for name, (a, b) in {
        "p2_minus_p1": ("p2", "p1"), "p2_minus_p0": ("p2", "p0"), "pcal_minus_p0": ("pcal", "p0"),
        "p1_minus_p0": ("p1", "p0"), "p1_minus_pq": ("p1", "pq")}.items()}
    by_fam = defaultdict(list)
    for r in tested:
        by_fam[r["family"]].append(r)
    top = [r for r in tested if r["status"] == "top_pick"]
    report = {"evidence_class": "EXPLORATORY_NOT_PREREGISTERED_FORWARD_CHAIN", "data_sha": DATA_SHA,
              "date_range": [FIRST, LAST], "sample": meta, "n_rows_all": len(rows),
              "n_out_of_sample": len(tested), "folds": folds,
              "paired_log_loss": comps, "pooled": scores(tested),
              "by_family": {f: scores(v) for f, v in sorted(by_fam.items())},
              "by_status": {s: scores([r for r in tested if r["status"] == s])
                            for s in sorted({str(r["status"]) for r in tested})},
              "top_picks": scores(top), "selection_equal_volume": selection(tested),
              "status_counts": dict(Counter(str(r["status"]) for r in tested))}
    with open(os.path.join(HERE, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: report[k] for k in ("sample", "n_out_of_sample", "paired_log_loss", "top_picks",
                                             "selection_equal_volume")}, indent=1))


if __name__ == "__main__":
    main()
