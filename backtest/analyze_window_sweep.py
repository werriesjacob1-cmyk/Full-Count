#!/usr/bin/env python3
"""analyze_window_sweep.py -- consumes backtest/_sweep_window_pairs.jsonl
(the completed 68/68-date league-rate window sweep: window_days in
{7,14,21,30} tested against IDENTICAL real point-in-time inputs for
hits/total_bases/home_runs/singles) and reports whether any window beats
the shipped default (30) on real graded outcomes.

Paired, not independent: every row carries all four windows' predictions
for the SAME real (player, game, outcome) triple, so this can report the
PAIRED difference in Brier/log-loss against window=30 for each alternative
-- a much lower-noise comparison than four independent Brier scores would
give, and the same paired-bootstrap discipline this project's own prior
window-shrinkage audits (see generate_picks.py's EMPIRICAL_WEIGHT comment
block) already established as the right tool for this exact question.

    /tmp/mlbvenv/bin/python3 backtest/analyze_window_sweep.py
"""
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eval_lib as el

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sweep_window_pairs.jsonl")
WINDOWS = (7, 14, 21, 30)
STATS = ("hits", "total_bases", "home_runs", "singles")
N_BOOTSTRAP = 2000


def load_rows():
    rows = []
    with open(ROWS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def paired_bootstrap_ci(diffs, n=N_BOOTSTRAP, seed=1234):
    """95% CI on the mean of `diffs` via simple percentile bootstrap,
    resampling PAIRS (same convention as the 2026-08-07 shrink-k audit
    this project already ran for an identical kind of question)."""
    if not diffs:
        return None, None
    rng = random.Random(seed)
    n_obs = len(diffs)
    means = []
    for _ in range(n):
        resample = [diffs[rng.randrange(n_obs)] for _ in range(n_obs)]
        means.append(sum(resample) / n_obs)
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[int(0.975 * n) - 1]
    return lo, hi


def main():
    rows = load_rows()
    print(f"{len(rows)} paired rows loaded from {ROWS_PATH}\n")

    print("=" * 100)
    print("POOLED (all 4 stats): Brier and log loss per window, all real rows with a real "
          "prediction+outcome")
    print("=" * 100)
    for w in WINDOWS:
        pairs = [(r[f"prob_{w}"], r["outcome"]) for r in rows if r.get(f"prob_{w}") is not None]
        print(f"  window={w:>2d}d  n={len(pairs):5d}  brier={el.brier(pairs):.5f}  "
              f"log_loss={el.log_loss(pairs):.5f}")

    print()
    print("=" * 100)
    print("PAIRED COMPARISON vs the SHIPPED default (window=30) -- same games, same outcomes, "
          "only the window differs. Negative mean diff = the alternative window's per-row "
          "squared error is LOWER (better) than 30's on that exact row.")
    print("=" * 100)
    for w in WINDOWS:
        if w == 30:
            continue
        diffs_brier = []
        diffs_ll = []
        for r in rows:
            p_alt, p_30, y = r.get(f"prob_{w}"), r.get("prob_30"), r["outcome"]
            if p_alt is None or p_30 is None:
                continue
            diffs_brier.append((p_alt - y) ** 2 - (p_30 - y) ** 2)
            eps = 1e-6
            pa = min(max(p_alt, eps), 1 - eps)
            p3 = min(max(p_30, eps), 1 - eps)
            ll_alt = -(y * __import__("math").log(pa) + (1 - y) * __import__("math").log(1 - pa))
            ll_30 = -(y * __import__("math").log(p3) + (1 - y) * __import__("math").log(1 - p3))
            diffs_ll.append(ll_alt - ll_30)
        mean_b = sum(diffs_brier) / len(diffs_brier) if diffs_brier else None
        lo_b, hi_b = paired_bootstrap_ci(diffs_brier)
        mean_l = sum(diffs_ll) / len(diffs_ll) if diffs_ll else None
        sig = "" if (lo_b is not None and lo_b <= 0 <= hi_b) else "  <-- 95% CI excludes zero"
        print(f"  {w:>2d}d vs 30d  n={len(diffs_brier):5d}  "
              f"mean Brier diff={mean_b:+.5f} [95% CI {lo_b:+.5f}, {hi_b:+.5f}]{sig}  "
              f"mean logloss diff={mean_l:+.5f}")

    print()
    print("=" * 100)
    print("BY STAT: same paired comparison, split out (a market hiding inside the pooled "
          "number would show up here)")
    print("=" * 100)
    for stat in STATS:
        stat_rows = [r for r in rows if r["stat"] == stat]
        print(f"\n--- {stat} (n={len(stat_rows)} rows) ---")
        for w in WINDOWS:
            if w == 30:
                continue
            diffs = []
            for r in stat_rows:
                p_alt, p_30, y = r.get(f"prob_{w}"), r.get("prob_30"), r["outcome"]
                if p_alt is None or p_30 is None:
                    continue
                diffs.append((p_alt - y) ** 2 - (p_30 - y) ** 2)
            if len(diffs) < 30:
                print(f"    {w:>2d}d vs 30d  n={len(diffs):4d}  SKIPPED, too few paired rows")
                continue
            mean_b = sum(diffs) / len(diffs)
            lo_b, hi_b = paired_bootstrap_ci(diffs)
            sig = "" if (lo_b <= 0 <= hi_b) else "  <-- 95% CI excludes zero"
            print(f"    {w:>2d}d vs 30d  n={len(diffs):4d}  mean Brier diff={mean_b:+.5f} "
                  f"[95% CI {lo_b:+.5f}, {hi_b:+.5f}]{sig}")

    print()
    print("=" * 100)
    print("APRIL-ONLY (the early-season dates this sweep was specifically built to test -- "
          "see this module's own docstring on why a 30-day window is nearly powerless there)")
    print("=" * 100)
    april_rows = [r for r in rows if r["tag"] == "april"]
    print(f"n={len(april_rows)} april rows")
    for w in WINDOWS:
        if w == 30:
            continue
        diffs = []
        for r in april_rows:
            p_alt, p_30, y = r.get(f"prob_{w}"), r.get("prob_30"), r["outcome"]
            if p_alt is None or p_30 is None:
                continue
            diffs.append((p_alt - y) ** 2 - (p_30 - y) ** 2)
        if not diffs:
            continue
        mean_b = sum(diffs) / len(diffs)
        lo_b, hi_b = paired_bootstrap_ci(diffs)
        sig = "" if (lo_b <= 0 <= hi_b) else "  <-- 95% CI excludes zero"
        print(f"  {w:>2d}d vs 30d  n={len(diffs):5d}  mean Brier diff={mean_b:+.5f} "
              f"[95% CI {lo_b:+.5f}, {hi_b:+.5f}]{sig}")

    print()
    print("=" * 100)
    print("basis_30 distribution (how often each window's prediction actually came from "
          "league_only vs modelled_shrunk/blended -- confirms whether a window difference "
          "even had a real mechanism to bite through)")
    print("=" * 100)
    for w in WINDOWS:
        c = defaultdict(int)
        for r in rows:
            c[r.get(f"basis_{w}")] += 1
        print(f"  window={w}: {dict(c)}")


if __name__ == "__main__":
    main()
