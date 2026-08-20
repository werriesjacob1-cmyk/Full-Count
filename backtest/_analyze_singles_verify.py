#!/usr/bin/env python3
"""_analyze_singles_verify.py -- OLD-vs-NEW analysis of
backtest/_singles_verify_pairs.jsonl (produced by _verify_singles.py).
Scratch tooling, not part of the shipped pipeline. Reuses eval_lib.py's
brier/log_loss/calibration_table -- the same shared primitives every other
Phase 3+ analysis in this repo uses, rather than a fresh reimplementation.

Can be re-run at any time while _verify_singles.py is still appending to
the pairs file -- reports are always "as of the rows collected so far."

    /tmp/mlbvenv/bin/python3 backtest/_analyze_singles_verify.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eval_lib as el

PAIRS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_singles_verify_pairs.jsonl")
BUCKETS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.01)


def load_pairs():
    rows = []
    if not os.path.exists(PAIRS_PATH):
        return rows
    with open(PAIRS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _print_table(pairs, indent="  "):
    rows = el.calibration_table(pairs, buckets=BUCKETS)
    if not rows:
        print(f"{indent}(no data)")
        return
    for row in rows:
        flag = ""
        if row["n"] >= el.MIN_N_REPORTABLE and abs(row["gap"]) >= 0.08:
            flag = "  <-- REAL GAP"
        elif row["n"] < el.MIN_N_REPORTABLE:
            flag = "  (thin)"
        print(f"{indent}{row['range']:>10s}  n={row['n']:4d}  pred={row['predicted']:.3f}  "
              f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}  brier={row['brier']:.4f}  "
              f"logloss={row['log_loss']:.4f}{flag}")


def summarize(label, pairs):
    n = len(pairs)
    if n == 0:
        print(f"{label}: no data")
        return None
    pred_avg = sum(p for p, _ in pairs) / n
    actual = sum(o for _, o in pairs) / n
    gap = actual - pred_avg
    brier = el.brier(pairs)
    logloss = el.log_loss(pairs)
    print(f"{label}: n={n}  pred={pred_avg:.4f}  actual={actual:.4f}  gap={gap:+.4f}  "
         f"brier={brier:.4f}  logloss={logloss:.4f}")
    return {"n": n, "pred": pred_avg, "actual": actual, "gap": gap,
           "brier": brier, "log_loss": logloss}


def main():
    rows = load_pairs()
    n_total = len(rows)
    dates = sorted({r["date"] for r in rows})
    print(f"{n_total} total OLD/NEW observation rows across {len(dates)} distinct dates "
         f"(as of this run -- verification may still be in progress)")
    if not dates:
        return
    print(f"date range so far: {dates[0]} .. {dates[-1]}\n")

    matched = [r for r in rows if r.get("old_prob") is not None and r.get("new_prob") is not None]
    old_only = [r for r in rows if r.get("old_prob") is not None and r.get("new_prob") is None]
    new_only = [r for r in rows if r.get("old_prob") is None and r.get("new_prob") is not None]
    neither = [r for r in rows if r.get("old_prob") is None and r.get("new_prob") is None]
    print(f"matched (both sides have a real prob): {len(matched)}")
    print(f"old-only (NEW declined to price):       {len(old_only)}")
    print(f"new-only (OLD declined to price):       {len(new_only)}")
    print(f"neither side priced:                    {len(neither)}\n")

    old_pairs = [(r["old_prob"], r["outcome"]) for r in matched]
    new_pairs = [(r["new_prob"], r["outcome"]) for r in matched]

    print("=" * 90)
    print("MATCHED-PAIR SUMMARY (identical observations, both sides priced)")
    print("=" * 90)
    old_summary = summarize("OLD (empirical/league-only)", old_pairs)
    new_summary = summarize("NEW (modelled+shrink)      ", new_pairs)
    if old_summary and new_summary:
        print(f"\nBrier improvement (OLD - NEW, positive = NEW better): "
             f"{old_summary['brier'] - new_summary['brier']:+.5f}")
        print(f"Log-loss improvement (OLD - NEW, positive = NEW better): "
             f"{old_summary['log_loss'] - new_summary['log_loss']:+.5f}")
        print(f"Calibration gap: OLD {old_summary['gap']:+.4f} vs NEW {new_summary['gap']:+.4f} "
             f"(closer to 0 is better)")

    print("\n" + "=" * 90)
    print("OLD CALIBRATION BY PROBABILITY BUCKET")
    print("=" * 90)
    _print_table(old_pairs)

    print("\n" + "=" * 90)
    print("NEW CALIBRATION BY PROBABILITY BUCKET")
    print("=" * 90)
    _print_table(new_pairs)

    if len(dates) >= 4:
        mid = dates[len(dates) // 2]
        early = [r for r in matched if r["date"] < mid]
        late = [r for r in matched if r["date"] >= mid]
        print("\n" + "=" * 90)
        print(f"TEMPORAL SLICE (split at {mid})")
        print("=" * 90)
        summarize("  OLD, early half", [(r["old_prob"], r["outcome"]) for r in early])
        summarize("  NEW, early half", [(r["new_prob"], r["outcome"]) for r in early])
        summarize("  OLD, late half ", [(r["old_prob"], r["outcome"]) for r in late])
        summarize("  NEW, late half ", [(r["new_prob"], r["outcome"]) for r in late])

    print("\n" + "=" * 90)
    print("BY NEW_BASIS (which path NEW's probability actually came from)")
    print("=" * 90)
    by_basis = {}
    for r in matched:
        by_basis.setdefault(r.get("new_basis"), []).append(r)
    for basis, rs in sorted(by_basis.items(), key=lambda x: -len(x[1])):
        summarize(f"  new_basis={basis!r}", [(r["new_prob"], r["outcome"]) for r in rs])

    print("\n" + "=" * 90)
    print(f"n_pass so far: {len(dates)}/130 dates collected")
    print("=" * 90)


if __name__ == "__main__":
    main()
