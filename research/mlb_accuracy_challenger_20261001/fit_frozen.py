#!/usr/bin/env python3
"""Fit and freeze the challenger coefficients on DEVELOPMENT data only.

Development data = the 2026 regular season with graded priced rows
(2026-08-04..2026-09-27), the exact row construction PR #219 used (frozen-board
records linked by sha plus final-run board picks with a recommendation status,
deduplicated by canonical id, frozen-board preferred), read via `git show` at
DATA_SHA. These outcomes were already examined by PR #201, #217 and #219, so
nothing fitted here is evidence; it only freezes the challengers before the
preregistration commit (see ../mlb_accuracy_challenger_prereg_20261001.md).

Per family f (hits, hits_runs_rbis, strikeouts, pitcher_outs, other), with
q = raw posted implied probability from the observed American odds and
p0 = the production model's hit_probability:
  C0  p1   = logistic(a + c*logit q)               market-only
  C1  pcal = logistic(a + b*logit p0)              calibrated model
  C2  p2   = logistic(a + b*logit p0 + c*logit q)  market + model
Penalized Newton-Raphson (L2 1.0 on slopes, clip 1e-4), identical to #219.
A family with fewer than MIN_FAMILY_ROWS rows, or a single outcome class,
uses the pooled fit.

    python3 research/mlb_accuracy_challenger_20261001/fit_frozen.py
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA_SHA = "58abe9f2e5a2b69d0f8bc98c22c8bafe7b00a224"  # the #219 data snapshot
DEV_FIRST, DEV_LAST = "2026-08-04", "2026-09-27"
FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
L2, CLIP, MIN_FAMILY_ROWS = 1.0, 1e-4, 50
ARMS = {"p1": ("lq",), "pcal": ("lp",), "p2": ("lp", "lq")}


def _git(*args):
    return subprocess.check_output(["git", "-C", HERE, *args])


def git_json(path):
    return json.loads(_git("show", f"{DATA_SHA}:{path}"))


def implied(odds):
    return (-odds) / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def logit(p):
    p = min(max(p, CLIP), 1 - CLIP)
    return math.log(p / (1 - p))


def family(stat):
    return stat if stat in FAMILIES else "other"


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


def load_dev_rows():
    names = _git("ls-tree", "--full-tree", "--name-only", DATA_SHA, "output/").decode().split() + \
        _git("ls-tree", "--full-tree", "--name-only", DATA_SHA, "results/").decode().split()
    fb, db = {}, {}
    for path in sorted(names):
        base = os.path.basename(path)
        if base.startswith("board_freeze_graded_"):
            d = base[len("board_freeze_graded_"):-5]
            if not DEV_FIRST <= d <= DEV_LAST:
                continue
            graded, board = git_json(path), git_json(f"output/board_freeze_{d}.json")
            if graded.get("source_board_sha256") != board.get("board_sha256"):
                continue
            for x in graded["records"]:
                p, o, y = (x.get("prediction") or {}).get("hit_probability"), \
                    (x.get("market") or {}).get("market_odds"), y_of(x.get("grade"))
                if None in (x.get("candidate_id"), x.get("stat"), p, o, y):
                    continue
                fb[x["candidate_id"]] = {"family": family(x["stat"]), "p0": p, "q": implied(o), "y": y}
        elif base.startswith("grades_") and path.startswith("results/"):
            d = base[len("grades_"):-5]
            if not DEV_FIRST <= d <= DEV_LAST:
                continue
            for x in git_json(path).get("picks") or []:
                if x.get("recommendation_status") is None:
                    continue
                cid, st = canonical_id(x), (x.get("projection") or {}).get("stat")
                p, o, y = x.get("hit_probability"), x.get("market_odds"), y_of(x.get("grade"))
                if None in (cid, st, p, o, y):
                    continue
                db[cid] = {"family": family(st), "p0": p, "q": implied(o), "y": y}
    rows = dict(db)
    rows.update(fb)
    out = list(rows.values())
    for r in out:
        r["lp"], r["lq"] = logit(r["p0"]), logit(r["q"])
    return out, {"n_fb": len(fb), "n_db": len(db), "n_overlap": len(set(fb) & set(db)), "n_rows": len(out)}


def fit_logistic(rows, cols):
    X = np.column_stack([np.ones(len(rows))] + [[r[c] for r in rows] for c in cols])
    y = np.array([r["y"] for r in rows], dtype=float)
    beta = np.zeros(X.shape[1])
    pen = np.full(X.shape[1], L2)
    pen[0] = 0.0
    for _ in range(100):
        mu = 1 / (1 + np.exp(-(X @ beta)))
        step = np.linalg.solve((X * (mu * (1 - mu))[:, None]).T @ X + np.diag(pen), X.T @ (y - mu) - pen * beta)
        beta += step
        if np.max(np.abs(step)) < 1e-10:
            break
    return [float(v) for v in beta]


def main():
    rows, meta = load_dev_rows()
    coef = {}
    for arm, cols in ARMS.items():
        coef[arm] = {"_pooled": {"beta": fit_logistic(rows, cols), "n": len(rows)}}
        for f in (*FAMILIES, "other"):
            fr = [r for r in rows if r["family"] == f]
            if len(fr) >= MIN_FAMILY_ROWS and 0 < sum(r["y"] for r in fr) < len(fr):
                coef[arm][f] = {"beta": fit_logistic(fr, cols), "n": len(fr)}
    out = {"purpose": "FROZEN challenger coefficients, development data only; not evidence",
           "data_sha": DATA_SHA, "dev_window": [DEV_FIRST, DEV_LAST], "l2": L2, "clip": CLIP,
           "min_family_rows": MIN_FAMILY_ROWS, "arms": {a: list(c) for a, c in ARMS.items()},
           "sample": meta, "coefficients": coef}
    with open(os.path.join(HERE, "frozen_coefficients.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({a: {f: [round(b, 4) for b in v["beta"]] for f, v in c.items()} for a, c in coef.items()}, indent=1))
    print(meta)


if __name__ == "__main__":
    main()
