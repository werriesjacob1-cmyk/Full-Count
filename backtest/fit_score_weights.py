#!/usr/bin/env python3
"""backtest/fit_score_weights.py — tests whether score_batter/score_pitcher's
hand-set 35/25/15/15/10 category weights (MATCHUP/RECENT FORM/ENVIRONMENT/
BASELINE SKILL/CONTEXT) are actually the best split, against real graded
outcomes, instead of assuming they are.

WHY THIS EXISTS: those weights were carried over verbatim from
mlb_daily.py's "SYNTHESIS LAYER REFERENCE" section -- a manual-reasoning
cheat sheet written for a human/LLM to read the text report and eyeball a
pick, from before generate_picks.py existed as deterministic code. Unlike
hit_probability (Platt-calibrated against real outcomes, see
refit_calibrators.py) and individual signals (AUC-measured, see
measure_signals.py / backtest/signals.py), the CATEGORY weights themselves
have never been fit or validated as an ensemble. This closes that gap,
using the same time-based train/held-out discipline as the rest of this
project's fitting code (backtest/calibration.py's time_based_split).

IMPORTANT SCOPE NOTE, stated honestly up front: `score` does not drive
what ships on the live board. select_main_board() (generate_picks.py)
ranks purely on price_clears/market_edge, both derived from
hit_probability, which this script does not touch. `score` only decides
(a) the MIN_QUALITY_SCORE gate -- whether a candidate is considered at
all -- and (b) tiebreaking in the "skipped"/near-miss diagnostic list.
So a materially better score formula would improve WHICH candidates
reach pricing, not directly the accuracy of what ships today. Report
this finding honestly regardless of which way it comes out.

This is a REPORT-ONLY script. It never edits generate_picks.py's weight
constants -- per this project's own "record, measure, THEN promote"
discipline, that is a separate, deliberate decision for a human to make
after seeing this report, not something to auto-apply.

Run: /tmp/mlbvenv/bin/python3 backtest/fit_score_weights.py
     /tmp/mlbvenv/bin/python3 backtest/fit_score_weights.py --rows backtest/rows.jsonl --test-frac 0.3
"""
import argparse
import json
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0].rsplit("/", 1)[0] if "/" in __file__ else ".")
from backtest.calibration import time_based_split

CATS = ["cat_matchup", "cat_recent_form", "cat_environment", "cat_baseline_skill", "cat_context"]
CURRENT_WEIGHTS = {"cat_matchup": 0.35, "cat_recent_form": 0.25, "cat_environment": 0.15,
                    "cat_baseline_skill": 0.15, "cat_context": 0.10}
LABELS = {"cat_matchup": "MATCHUP", "cat_recent_form": "RECENT FORM",
          "cat_environment": "ENVIRONMENT", "cat_baseline_skill": "BASELINE SKILL",
          "cat_context": "CONTEXT"}


def load_rows(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def usable(rows):
    """Only batter/pitcher rows carry the cat_ fields (see SCHEMA.md) --
    the other prop-specific scorers (pitcher_outs, combined_strikeouts,
    stolen_base, laser, walk, first_inning) use their own formulas."""
    return [r for r in rows
            if all(r.get(c) is not None for c in CATS) and r.get("outcome") is not None]


def current_formula_score(row):
    return sum(row[c] * w for c, w in CURRENT_WEIGHTS.items())


def bootstrap_auc_ci(y_true, y_score, n_boot=2000, seed=0):
    from sklearn.metrics import roc_auc_score
    rng = np.random.RandomState(seed)
    y_true = np.asarray(y_true)
    y_score = np.asarray(y_score)
    n = len(y_true)
    point = roc_auc_score(y_true, y_score)
    boots = []
    for _ in range(n_boot):
        idx = rng.randint(0, n, n)
        yt, ys = y_true[idx], y_score[idx]
        if len(set(yt.tolist())) < 2:
            continue
        boots.append(roc_auc_score(yt, ys))
    if not boots:
        return point, point, point
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return point, lo, hi


def fit_and_report(rows, label, test_frac):
    usable_rows = usable(rows)
    print(f"\n{'=' * 78}\n{label}  ({len(usable_rows)} usable rows of {len(rows)} total)\n{'=' * 78}")
    if len(usable_rows) < 100:
        print("  Too few usable rows to fit reliably (need >= 100). Skipping.")
        return

    train, held_out = time_based_split(usable_rows, test_frac=test_frac)
    print(f"  train={len(train)} rows ({train[0]['date']}..{train[-1]['date']})  "
          f"held_out={len(held_out)} rows ({held_out[0]['date']}..{held_out[-1]['date']})")

    from sklearn.linear_model import LogisticRegression

    X_train = np.array([[r[c] for c in CATS] for r in train], dtype=float) / 100.0
    y_train = np.array([r["outcome"] for r in train], dtype=int)
    X_held = np.array([[r[c] for c in CATS] for r in held_out], dtype=float) / 100.0
    y_held = np.array([r["outcome"] for r in held_out], dtype=int)

    if len(set(y_held.tolist())) < 2:
        print("  Held-out set is single-class (all hits or all misses) -- cannot compute AUC. Skipping.")
        return

    # CURRENT FORMULA, evaluated on held-out data it was never fit to (it
    # was never "fit" at all, but evaluating it on the same held-out split
    # keeps the comparison apples-to-apples).
    current_score_held = np.array([current_formula_score(r) for r in held_out])
    cur_auc, cur_lo, cur_hi = bootstrap_auc_ci(y_held, current_score_held)
    print(f"\n  CURRENT hand-set weights (35/25/15/15/10):")
    print(f"    held-out AUC = {cur_auc:.4f}  [{cur_lo:.4f}, {cur_hi:.4f}]")

    # FITTED weights: logistic regression on the 5 raw category components,
    # trained on the EARLIER portion only, scored on the LATER held-out
    # portion -- never sees held-out data during fitting.
    clf = LogisticRegression(max_iter=2000, C=1.0)
    clf.fit(X_train, y_train)
    fitted_score_held = clf.decision_function(X_held)
    fit_auc, fit_lo, fit_hi = bootstrap_auc_ci(y_held, fitted_score_held)

    coefs = clf.coef_[0]
    # Normalize to a 0-100%, sum-to-1 "weight" scheme for direct comparison
    # to 35/25/15/15/10, using |coef| (a negative coef means that category's
    # raw score was fit to move OUTCOME probability the opposite direction
    # from how it's currently used -- reported as a negative share, not
    # clipped, since that itself is informative).
    total = sum(abs(c) for c in coefs)
    print(f"\n  FITTED weights (logistic regression, held out from training):")
    for c, coef in zip(CATS, coefs):
        share = (coef / total * 100) if total else 0.0
        print(f"    {LABELS[c]:16s} fitted share = {share:+6.1f}%   (raw coef {coef:+.4f})")
    print(f"    held-out AUC = {fit_auc:.4f}  [{fit_lo:.4f}, {fit_hi:.4f}]")

    delta = fit_auc - cur_auc
    print(f"\n  DELTA (fitted - current): {delta:+.4f}")
    if fit_lo > cur_hi:
        print("  -> Fitted weights CLEAR the current formula's CI: a real improvement.")
    elif cur_lo > fit_hi:
        print("  -> Current formula CLEARS the fitted weights' CI: fitting made it WORSE "
              "(likely overfit on a small category-count problem, or the categories genuinely "
              "don't separate outcomes well regardless of weighting).")
    else:
        print("  -> Confidence intervals overlap: no statistically distinguishable difference. "
              "The specific 35/25/15/15/10 split is not costing measurable accuracy at this "
              "sample size, whatever its philosophical origin.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", default="backtest/rows.jsonl")
    ap.add_argument("--test-frac", type=float, default=0.3)
    args = ap.parse_args()

    rows = load_rows(args.rows)
    print(f"Loaded {len(rows)} total backtest rows from {args.rows}")

    fit_and_report(rows, "ALL (batters + pitchers combined)", args.test_frac)
    fit_and_report([r for r in rows if r.get("prop_type") not in ("strikeouts",)],
                    "BATTERS ONLY (prop_type != strikeouts)", args.test_frac)
    fit_and_report([r for r in rows if r.get("prop_type") == "strikeouts"],
                    "PITCHERS ONLY (prop_type == strikeouts)", args.test_frac)


if __name__ == "__main__":
    main()
