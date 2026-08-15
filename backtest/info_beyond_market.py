#!/usr/bin/env python3
"""info_beyond_market.py — Phase 3, item 6: "When Full Count disagrees
with the sportsbook, is that disagreement predictive? Do not judge this
only by comparing raw hit rates."

Four independent tests, all reusing eval_lib's shared primitives and
backtest/signals.py's already-proven IRLS logistic regression rather than
a second implementation:

  1. Brier / log-loss improvement -- model vs. the market's own no-vig
     probability (eval_lib.market_probability, exact where FanDuel quotes
     both sides, labelled-approximate where it does not).
  2. Calibration improvement -- mean squared calibration gap, model vs.
     market, weighted by bucket n.
  3. THE RIGOROUS TEST: logistic regression of outcome on the market's own
     probability PLUS the model's edge over it (model_prob - market_prob).
     A statistically significant, positive coefficient on the edge term
     means the model's disagreement with the market carries real
     information beyond what the market's own number already explains --
     not just "the model agrees with itself," a genuine residual test.
  4. Disagreement-grouped hit rates (does being MORE bullish than the
     market predict a higher hit rate than being LESS bullish), with a
     real two-proportion z-test rather than eyeballing two raw rates.

Every result is labelled by eval_lib.sample_size_label() -- "inconclusive"
below MIN_N_CONFIDENT (100) for the regression specifically, since a
logistic fit with under ~100 events is not trustworthy regardless of what
p-value it reports.

    /tmp/mlbvenv/bin/python3 backtest/info_beyond_market.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import eval_lib as el
import signals as sig  # backtest/signals.py -- _irls_ridge, sigmoid, two_sided_p


def load_rows():
    picks = el.graded_only(el.priced_only(el.load_graded_picks()))
    out = []
    for p in picks:
        prob = p.get("hit_probability")
        if prob is None:
            continue
        market_prob, exact = el.market_probability(p)
        if market_prob is None:
            continue
        stat = (p.get("projection") or {}).get("stat") or "?"
        out.append({"stat": stat, "model_prob": float(prob), "market_prob": market_prob,
                   "exact": exact, "outcome": 1.0 if p["grade"] == "hit" else 0.0})
    return out


def calibration_improvement(model_po, market_po):
    """Weighted-by-n mean squared calibration gap, model vs market. Lower
    is better calibrated. Returns (model_score, market_score) or (None,
    None) if neither has any reportable-n bucket."""
    def _score(pairs):
        rows = el.calibration_table(pairs)
        reportable = [r for r in rows if r["n"] >= el.MIN_N_REPORTABLE]
        if not reportable:
            return None
        total_n = sum(r["n"] for r in reportable)
        return sum(r["n"] * r["gap"] ** 2 for r in reportable) / total_n
    return _score(model_po), _score(market_po)


def residual_regression(rows):
    """The rigorous test. y ~ 1 + market_prob + edge, unpenalized IRLS
    (lam=0 -- the honest MLE standard errors, not ridge-shrunk ones; see
    signals._irls_ridge's own docstring on why lam=0 is required for a
    real hypothesis test). Returns a dict with the edge coefficient, its
    SE, z, p, and a plain-language verdict -- or a dict with only a
    "verdict" key if there isn't enough data to fit at all."""
    n = len(rows)
    if n < el.MIN_N_CONFIDENT:
        return {"n": n, "converged": False,
                "verdict": f"INCONCLUSIVE -- only {n} rows (need >= {el.MIN_N_CONFIDENT} "
                           f"for a logistic fit to mean anything)."}
    y = np.array([r["outcome"] for r in rows])
    market_prob = np.array([r["market_prob"] for r in rows])
    edge = np.array([r["model_prob"] - r["market_prob"] for r in rows])
    X = np.column_stack([market_prob, edge])
    beta, cov, converged = sig._irls_ridge(X, y, lam=0.0)
    # beta = [intercept, market_prob_coef, edge_coef]
    edge_idx = 2
    coef = beta[edge_idx]
    se = float(np.sqrt(cov[edge_idx, edge_idx])) if cov[edge_idx, edge_idx] > 0 else None
    if not converged or se is None or se == 0:
        return {"n": n, "converged": converged,
                "verdict": "INCONCLUSIVE -- the fit did not converge cleanly enough to "
                           "trust a standard error."}
    z = coef / se
    p = sig.two_sided_p(z)
    if p < 0.05 and coef > 0:
        verdict = ("SIGNIFICANT, POSITIVE (p=%.4f): when the model disagrees with the "
                  "market, the SIDE of that disagreement carries real information beyond "
                  "the market's own probability, at this sample size." % p)
    elif p < 0.05 and coef < 0:
        verdict = ("SIGNIFICANT, NEGATIVE (p=%.4f): the model's disagreement with the "
                  "market points the WRONG way, on average, at this sample size -- when "
                  "the model is more bullish than the market, that should be treated as a "
                  "warning sign, not an edge, until this changes." % p)
    else:
        verdict = ("NOT SIGNIFICANT (p=%.4f): no evidence yet, at this sample size, that "
                  "the model's disagreement with the market adds real information beyond "
                  "the market's own number. This does not prove there is none -- it means "
                  "the current sample cannot distinguish it from noise." % p)
    return {"n": n, "converged": True, "edge_coef": round(float(coef), 4),
           "se": round(se, 4), "z": round(float(z), 3), "p": round(float(p), 5),
           "verdict": verdict}


def two_proportion_z(k1, n1, k2, n2):
    """Standard two-proportion z-test, pooled variance. Returns (z, p) or
    (None, None) if either group is empty."""
    if n1 == 0 or n2 == 0:
        return None, None
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None, None
    z = (p1 - p2) / se
    return float(z), float(sig.two_sided_p(z))


def report(label, rows):
    n = len(rows)
    print(f"\n=== {label}  (n={n}, {el.sample_size_label(n)}) ===")
    if n < el.MIN_N_DIRECTIONAL:
        print(f"  Fewer than {el.MIN_N_DIRECTIONAL} rows -- skipped entirely.")
        return

    model_po = [(r["model_prob"], r["outcome"]) for r in rows]
    market_po = [(r["market_prob"], r["outcome"]) for r in rows]
    mb, mk = el.brier(model_po), el.brier(market_po)
    ml, kl = el.log_loss(model_po), el.log_loss(market_po)
    print(f"  [1] Brier: model={mb:.4f} market={mk:.4f} "
          f"(model {'better' if mb < mk else 'worse' if mb > mk else 'tied'} by {abs(mb-mk):.4f})")
    print(f"  [1] Log loss: model={ml:.4f} market={kl:.4f} "
          f"(model {'better' if ml < kl else 'worse' if ml > kl else 'tied'} by {abs(ml-kl):.4f})")

    cal_m, cal_k = calibration_improvement(model_po, market_po)
    if cal_m is not None and cal_k is not None:
        print(f"  [2] Calibration (weighted mean squared gap, reportable buckets only): "
              f"model={cal_m:.5f} market={cal_k:.5f} "
              f"(model {'better' if cal_m < cal_k else 'worse' if cal_m > cal_k else 'tied'} "
              f"calibrated)")
    else:
        print(f"  [2] Calibration improvement: INCONCLUSIVE -- no bucket for either side "
              f"reached n>={el.MIN_N_REPORTABLE}")

    reg = residual_regression(rows)
    print(f"  [3] Residual regression (outcome ~ market_prob + model_edge): {reg['verdict']}")
    if reg.get("converged") and "edge_coef" in reg:
        print(f"       edge coefficient={reg['edge_coef']:+.4f}  SE={reg['se']:.4f}  "
              f"z={reg['z']:+.3f}  p={reg['p']:.5f}  n={reg['n']}")

    disagree = [r for r in rows if abs(r["model_prob"] - r["market_prob"]) >= 0.03]
    if len(disagree) >= el.MIN_N_DIRECTIONAL:
        more = [r for r in disagree if r["model_prob"] > r["market_prob"]]
        less = [r for r in disagree if r["model_prob"] < r["market_prob"]]
        k_more, k_less = sum(r["outcome"] for r in more), sum(r["outcome"] for r in less)
        z, p = two_proportion_z(k_more, len(more), k_less, len(less))
        rate_more = k_more / len(more) if more else None
        rate_less = k_less / len(less) if less else None
        sig_note = (f"p={p:.4f} {'(statistically distinguishable)' if p is not None and p < 0.05 else '(not statistically distinguishable at this n)'}"
                   if z is not None else "z-test unavailable (one side empty)")
        print(f"  [4] Disagreement-grouped hit rate (|edge|>=3pts, n={len(disagree)}, "
              f"{el.sample_size_label(len(disagree))}): more-bullish rate="
              f"{round(rate_more,3) if rate_more is not None else 'n/a'} (n={len(more)}), "
              f"less-bullish rate={round(rate_less,3) if rate_less is not None else 'n/a'} "
              f"(n={len(less)}) -- {sig_note}")
    else:
        print(f"  [4] Disagreement-grouped hit rate: skipped, only {len(disagree)} rows "
              f"disagree by >=3pts")


def main():
    rows = load_rows()
    if not rows:
        print("No graded, market-priced, probability-carrying picks found.")
        return 1
    n_exact = sum(1 for r in rows if r["exact"])
    print(f"Loaded {len(rows)} rows. Market probability exact for {n_exact}/{len(rows)} "
          f"(see eval_lib.market_probability / results/ANALYSIS.md).")
    print("\n*** Every verdict below is scoped to the CURRENT sample. A result labelled "
          "'not significant' or 'inconclusive' means exactly that -- it is not evidence of "
          "'no effect,' only evidence that this sample cannot yet distinguish a real effect "
          "from noise. Re-run as the graded window grows. ***")

    from collections import defaultdict
    by_stat = defaultdict(list)
    for r in rows:
        by_stat[r["stat"]].append(r)

    report("POOLED (all families)", rows)
    for stat in sorted(by_stat, key=lambda s: -len(by_stat[s])):
        report(stat, by_stat[stat])

    return 0


if __name__ == "__main__":
    sys.exit(main())
