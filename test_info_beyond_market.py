#!/usr/bin/env python3
"""test_info_beyond_market.py — coverage for backtest/info_beyond_market.py,
Phase 3 item 6: "when Full Count disagrees with the sportsbook, is that
disagreement predictive? Do not judge this only by comparing raw hit
rates."

Direct regression test for a real bug caught by eye while first running
this script live: the printed "market" log loss value was accidentally
the market's BRIER score (mk) instead of its log loss (kl) -- the
underlying comparison logic was already correct, only the display was
wrong, but a wrong number in a report like this is exactly the kind of
thing this whole Phase 3 pass exists to prevent.

    /tmp/mlbvenv/bin/python3 test_info_beyond_market.py
"""
import sys
import io
import contextlib
import random

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, "backtest")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


import info_beyond_market as ibm
import eval_lib as el

head("1. THE REAL BUG THIS FIXES: the printed market log-loss value must actually BE "
     "the market's log loss, not its Brier score -- these are different numbers on real "
     "data and must never be silently swapped in the report")

random.seed(7)
rows1 = []
for i in range(40):
    model_p = random.uniform(0.4, 0.8)
    market_p = random.uniform(0.4, 0.8)
    outcome = 1.0 if random.random() < model_p else 0.0
    rows1.append({"stat": "hits", "model_prob": model_p, "market_prob": market_p,
                 "exact": False, "outcome": outcome})

model_po = [(r["model_prob"], r["outcome"]) for r in rows1]
market_po = [(r["market_prob"], r["outcome"]) for r in rows1]
real_market_brier = el.brier(market_po)
real_market_logloss = el.log_loss(market_po)
check(abs(real_market_brier - real_market_logloss) > 0.05,
      "sanity: on this fixture, Brier and log loss are genuinely different numbers, so a "
      "swap bug would actually be visible and this test can catch it",
      f"brier={real_market_brier:.4f} logloss={real_market_logloss:.4f}")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ibm.report("fixture", rows1)
out = buf.getvalue()
logloss_line = next(l for l in out.splitlines() if l.strip().startswith("[1] Log loss"))
check(f"market={real_market_logloss:.4f}" in logloss_line,
      "the printed market log-loss value matches the REAL market log loss, not the "
      "market's Brier score", logloss_line)
brier_line = next(l for l in out.splitlines() if l.strip().startswith("[1] Brier"))
check(f"market={real_market_brier:.4f}" in brier_line,
      "the printed market Brier value matches the real market Brier score", brier_line)

head("2. two_proportion_z(): a real, large, obvious difference is significant; two "
     "identical proportions are not")

z, p = ibm.two_proportion_z(80, 100, 20, 100)
check(p is not None and p < 0.001,
      "80/100 vs 20/100 is an obviously real difference -- p must be tiny", f"got p={p}")
z2, p2 = ibm.two_proportion_z(50, 100, 50, 100)
check(p2 is not None and p2 > 0.9,
      "identical proportions (50/100 vs 50/100) must show z=0, p=1 (no difference)",
      f"got z={z2} p={p2}")
z3, p3 = ibm.two_proportion_z(5, 0, 5, 10)
check(z3 is None and p3 is None, "an empty group returns (None, None), not a crash")

head("3. residual_regression(): below MIN_N_CONFIDENT is always labelled INCONCLUSIVE, "
     "regardless of how clean the underlying signal is")

small_rows = rows1[:20]
reg_small = ibm.residual_regression(small_rows)
check("INCONCLUSIVE" in reg_small["verdict"] and reg_small["n"] < el.MIN_N_CONFIDENT,
      "a 20-row sample is always INCONCLUSIVE, never a confident verdict",
      reg_small["verdict"])

head("4. residual_regression(): a real, strong, planted edge signal is detected as "
     "significant and positive at a large enough sample size")

random.seed(3)
rows4 = []
for i in range(400):
    market_p = random.uniform(0.4, 0.75)
    # Plant a REAL edge: outcome depends partly on (model_prob - market_prob)
    # being positive, at a strong effect size, so this is a real signal-
    # detection test, not just "does the code run."
    edge = random.uniform(-0.15, 0.15)
    model_p = max(0.05, min(0.95, market_p + edge))
    true_p = max(0.05, min(0.95, market_p + 2.5 * edge))
    outcome = 1.0 if random.random() < true_p else 0.0
    rows4.append({"stat": "hits", "model_prob": model_p, "market_prob": market_p,
                 "exact": False, "outcome": outcome})
reg4 = ibm.residual_regression(rows4)
check(reg4["converged"] is True, "the fit converges on a real, well-behaved 400-row sample",
      str(reg4))
check(reg4.get("edge_coef", 0) > 0,
      "a genuinely planted positive edge effect is recovered with a positive coefficient",
      str(reg4))
check(reg4.get("p", 1) < 0.05,
      "a strongly planted effect at n=400 is detected as statistically significant",
      str(reg4))

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
