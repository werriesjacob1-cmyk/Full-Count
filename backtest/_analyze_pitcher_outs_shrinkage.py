#!/usr/bin/env python3
"""_analyze_pitcher_outs_shrinkage.py -- scores every candidate n0 already
collected in _pitcher_outs_shrinkage_pairs.jsonl (7,483 real historical
pitcher_outs rows) against the real recorded outcome: Brier score, log
loss, and calibration-by-probability-bucket, current production (n0=6)
vs every candidate.

WHY n0=20 SPECIFICALLY MATTERS HERE, BEYOND THE ORIGINAL CANDIDATE SWEEP:
empirical_pitcher_outs_rates() only ever pools a single night's confirmed
starters (~10-16 pitchers) per threshold key when it calls _apply_shrinkage.
MIN_PLAYERS_TO_FIT_SHRINKAGE=30 in mlb_sources.py means _fit_shrinkage_n0
falls back to its flat SHRINKAGE_PRIOR_GAMES=20 default on essentially
every real slate for this market -- there are never 30+ starting pitchers
on one night. So switching pitcher_outs from its current explicit
prior_games=6 override to the "let it fit per-key" default (prior_games=
None, what every OTHER shrunk market uses) would NOT actually produce a
real per-threshold fit for this market -- it would just silently become
flat n0=20 on nearly every real call. Scoring n0=20 here answers the real
question ("does switching to the shared default help") without needing to
build a second fetch pass, since prior_games=6 was itself an arbitrary
borrow from the strikeout market (see empirical_pitcher_outs_rates's own
comment), not something to treat as sacred.

    /tmp/mlbvenv/bin/python3 backtest/_analyze_pitcher_outs_shrinkage.py
"""
import json
import math
import os

PAIRS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pitcher_outs_shrinkage_pairs.jsonl")
CANDIDATE_N0 = (3, 6, 10, 15, 20, 30, 40)
BUCKETS = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]


def load():
    rows = []
    with open(PAIRS_PATH, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def brier(rows, n0):
    return sum((r[f"p_hat_{n0}"] - r["outcome"]) ** 2 for r in rows) / len(rows)


def logloss(rows, n0):
    eps = 1e-6
    total = 0.0
    for r in rows:
        p = min(max(r[f"p_hat_{n0}"], eps), 1 - eps)
        y = r["outcome"]
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def calibration_table(rows, n0):
    out = []
    for lo, hi in BUCKETS:
        sub = [r for r in rows if lo <= r[f"p_hat_{n0}"] < hi]
        if not sub:
            out.append((lo, hi, 0, None, None, None))
            continue
        avg_p = sum(r[f"p_hat_{n0}"] for r in sub) / len(sub)
        hit_rate = sum(r["outcome"] for r in sub) / len(sub)
        out.append((lo, hi, len(sub), avg_p, hit_rate, hit_rate - avg_p))
    return out


def main():
    rows = load()
    print(f"{len(rows)} real historical pitcher_outs rows\n")

    print("=== Overall Brier / log loss, current (n0=6) vs every candidate ===")
    base_brier, base_ll = brier(rows, 6), logloss(rows, 6)
    for n0 in CANDIDATE_N0:
        b, ll = brier(rows, n0), logloss(rows, n0)
        marker = "  <- current production" if n0 == 6 else ""
        star = "  *** matches per-key-fit fallback ***" if n0 == 20 else ""
        print(f"  n0={n0:3d}  Brier={b:.4f} ({b - base_brier:+.4f})  "
              f"LogLoss={ll:.4f} ({ll - base_ll:+.4f}){marker}{star}")

    print("\n=== Calibration by probability bucket: n0=6 (current) ===")
    for lo, hi, n, avg_p, hit, gap in calibration_table(rows, 6):
        if n == 0:
            print(f"  [{lo:.1f},{hi:.1f}) n=0")
            continue
        print(f"  [{lo:.1f},{hi:.1f}) n={n:5d}  avg_p={avg_p:.3f}  hit_rate={hit:.3f}  gap={gap:+.3f}")

    print("\n=== Calibration by probability bucket: n0=20 (per-key-fit fallback value) ===")
    for lo, hi, n, avg_p, hit, gap in calibration_table(rows, 20):
        if n == 0:
            print(f"  [{lo:.1f},{hi:.1f}) n=0")
            continue
        print(f"  [{lo:.1f},{hi:.1f}) n={n:5d}  avg_p={avg_p:.3f}  hit_rate={hit:.3f}  gap={gap:+.3f}")

    print("\n=== High-probability tail only (p_hat >= 0.7), every candidate ===")
    for n0 in CANDIDATE_N0:
        sub = [r for r in rows if r[f"p_hat_{n0}"] >= 0.7]
        if not sub:
            print(f"  n0={n0:3d}  n=0")
            continue
        avg_p = sum(r[f"p_hat_{n0}"] for r in sub) / len(sub)
        hit = sum(r["outcome"] for r in sub) / len(sub)
        print(f"  n0={n0:3d}  n={len(sub):5d}  avg_p={avg_p:.3f}  hit_rate={hit:.3f}  gap={hit - avg_p:+.3f}")

    print("\n=== Low-probability tail only (p_hat < 0.5), every candidate ===")
    for n0 in CANDIDATE_N0:
        sub = [r for r in rows if r[f"p_hat_{n0}"] < 0.5]
        if not sub:
            print(f"  n0={n0:3d}  n=0")
            continue
        avg_p = sum(r[f"p_hat_{n0}"] for r in sub) / len(sub)
        hit = sum(r["outcome"] for r in sub) / len(sub)
        print(f"  n0={n0:3d}  n={len(sub):5d}  avg_p={avg_p:.3f}  hit_rate={hit:.3f}  gap={hit - avg_p:+.3f}")


if __name__ == "__main__":
    main()
