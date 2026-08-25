#!/usr/bin/env python3
"""analyze_short_window_sweep.py -- consumes backtest/_sweep_short_windows_pairs.jsonl
(the completed 68/68-date league-rate window sweep: window_days in
{1,2,3,5,7} tested against IDENTICAL real point-in-time inputs for
hits/total_bases/home_runs/singles) and answers task #114 ("test shorter
league-rate windows, below 7 days"): does going below the shortest window
already tested in the companion {7,14,21,30} sweep (analyze_window_sweep.py)
help, hurt, or do nothing on real graded outcomes.

Paired, not independent: every row carries all five windows' predictions
for the SAME real (player, game, outcome) triple, so this reports the
PAIRED difference in Brier/log-loss against window=7 (the shortest window
already validated in the companion sweep, so the natural anchor here) for
each shorter alternative.

    /tmp/mlbvenv/bin/python3 backtest/analyze_short_window_sweep.py
"""
import json
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eval_lib as el

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sweep_short_windows_pairs.jsonl")
WINDOWS = (1, 2, 3, 5, 7)
ANCHOR = 7
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
    print(f"PAIRED COMPARISON vs the {ANCHOR}-day anchor (shortest window already validated "
          "in the companion {7,14,21,30} sweep). Negative mean diff = the shorter alternative's "
          "per-row squared error is LOWER (better) than 7d's on that exact row.")
    print("=" * 100)
    for w in WINDOWS:
        if w == ANCHOR:
            continue
        diffs_brier = []
        diffs_ll = []
        for r in rows:
            p_alt, p_anchor, y = r.get(f"prob_{w}"), r.get(f"prob_{ANCHOR}"), r["outcome"]
            if p_alt is None or p_anchor is None:
                continue
            diffs_brier.append((p_alt - y) ** 2 - (p_anchor - y) ** 2)
            eps = 1e-6
            pa = min(max(p_alt, eps), 1 - eps)
            p7 = min(max(p_anchor, eps), 1 - eps)
            ll_alt = -(y * math.log(pa) + (1 - y) * math.log(1 - pa))
            ll_anchor = -(y * math.log(p7) + (1 - y) * math.log(1 - p7))
            diffs_ll.append(ll_alt - ll_anchor)
        mean_b = sum(diffs_brier) / len(diffs_brier) if diffs_brier else None
        lo_b, hi_b = paired_bootstrap_ci(diffs_brier)
        mean_l = sum(diffs_ll) / len(diffs_ll) if diffs_ll else None
        sig = "" if (lo_b is not None and lo_b <= 0 <= hi_b) else "  <-- 95% CI excludes zero"
        print(f"  {w:>2d}d vs {ANCHOR}d  n={len(diffs_brier):5d}  "
              f"mean Brier diff={mean_b:+.5f} [95% CI {lo_b:+.5f}, {hi_b:+.5f}]{sig}  "
              f"mean logloss diff={mean_l:+.5f}")

    print()
    print("=" * 100)
    print("BY STAT: same paired comparison, split out")
    print("=" * 100)
    for stat in STATS:
        stat_rows = [r for r in rows if r["stat"] == stat]
        print(f"\n--- {stat} (n={len(stat_rows)} rows) ---")
        for w in WINDOWS:
            if w == ANCHOR:
                continue
            diffs = []
            for r in stat_rows:
                p_alt, p_anchor, y = r.get(f"prob_{w}"), r.get(f"prob_{ANCHOR}"), r["outcome"]
                if p_alt is None or p_anchor is None:
                    continue
                diffs.append((p_alt - y) ** 2 - (p_anchor - y) ** 2)
            if len(diffs) < 30:
                print(f"    {w:>2d}d vs {ANCHOR}d  n={len(diffs):4d}  SKIPPED, too few paired rows")
                continue
            mean_b = sum(diffs) / len(diffs)
            lo_b, hi_b = paired_bootstrap_ci(diffs)
            sig = "" if (lo_b <= 0 <= hi_b) else "  <-- 95% CI excludes zero"
            print(f"    {w:>2d}d vs {ANCHOR}d  n={len(diffs):4d}  mean Brier diff={mean_b:+.5f} "
                  f"[95% CI {lo_b:+.5f}, {hi_b:+.5f}]{sig}")

    print()
    print("=" * 100)
    print("BY TAG (april = early-season thin-history dates this sweep targets; control = "
          "mid-season dates with plenty of history for any window)")
    print("=" * 100)
    for tag in ("april", "control"):
        tag_rows = [r for r in rows if r["tag"] == tag]
        print(f"\n--- {tag} (n={len(tag_rows)} rows) ---")
        for w in WINDOWS:
            if w == ANCHOR:
                continue
            diffs = []
            for r in tag_rows:
                p_alt, p_anchor, y = r.get(f"prob_{w}"), r.get(f"prob_{ANCHOR}"), r["outcome"]
                if p_alt is None or p_anchor is None:
                    continue
                diffs.append((p_alt - y) ** 2 - (p_anchor - y) ** 2)
            if not diffs:
                continue
            mean_b = sum(diffs) / len(diffs)
            lo_b, hi_b = paired_bootstrap_ci(diffs)
            sig = "" if (lo_b <= 0 <= hi_b) else "  <-- 95% CI excludes zero"
            print(f"  {w:>2d}d vs {ANCHOR}d  n={len(diffs):5d}  mean Brier diff={mean_b:+.5f} "
                  f"[95% CI {lo_b:+.5f}, {hi_b:+.5f}]{sig}")

    print()
    print("=" * 100)
    print("basis distribution (how often each window's prediction actually came from "
          "league_only vs modelled_shrunk/blended)")
    print("=" * 100)
    for w in WINDOWS:
        c = defaultdict(int)
        for r in rows:
            c[r.get(f"basis_{w}")] += 1
        print(f"  window={w}: {dict(c)}")


if __name__ == "__main__":
    main()
