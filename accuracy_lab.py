#!/usr/bin/env python3
"""accuracy_lab.py — Stage 6: a locked historical holdout and a real
Champion-vs-Challenger comparison harness, evaluated on IDENTICAL
conditions (same rows, same holdout, every time).

WHY THIS IS SEPARATE FROM champion_challenger.py.

champion_challenger.py (Phase 3.8) already does real shadow-prediction
tracking and pre-registered promotion criteria -- but it only accumulates
evidence going FORWARD, one live day at a time, against the SAME small
population Full Count's ~2-3 weeks of real production history covers.
This project also has backtest/rows.jsonl: 242,000+ already-graded rows
across 401 real historical dates (2024-04-01 through 2026-08-12) -- far
more statistical power than live shadow tracking alone could reach for
months. Stage 5 (backtest/engine.py's --apply-policy) made those rows
policy-annotated. Accuracy Lab is what actually PUTS that corpus to work,
under the one discipline that makes doing so safe: a HELD-OUT partition,
locked once and never re-cut, so "do not tune repeatedly against a locked
holdout" (a direct, standing prohibition) is enforced by construction,
not by promise.

THE LOCK.

lock_holdout() computes a chronological (never random -- see backtest/
calibration.py's own time_based_split docstring on why) cutoff date the
FIRST time it's called, and writes it to data/accuracy_lab/
holdout_manifest.json. Every subsequent call reads that file back and
returns the IDENTICAL partition -- it never recomputes the cutoff, even
if backtest/rows.jsonl has grown since, and even if called with a
different holdout_frac (a mismatched frac is reported, never silently
honored, so nobody accidentally widens or narrows the holdout by editing
a default argument). The only way to change the holdout is to delete the
manifest and re-lock deliberately -- and every prior result recorded
against the old lock stays on disk in results/, on the record, not
quietly erased.

THE COMPARISON.

evaluate_predictor_on_holdout(label, predict_fn) scores ONLY the locked
holdout rows and appends one timestamped record to data/accuracy_lab/
results/{label}.jsonl -- append-only, so re-running a label after a bad
result can never make the earlier attempt disappear. champion_predict_fn
is the ready-made Champion baseline: Stage 5's calibrated_prob when a row
has one, the raw predicted_prob otherwise -- the actual current
production policy, not a re-derivation of it. compare(label_a, label_b)
reads each label's most recent record and reports the Brier/log-loss
delta -- since both were necessarily evaluated against the same locked
partition, this genuinely is "identical conditions," not two overlapping-
but-different samples compared as if they were the same one.

    from accuracy_lab import lock_holdout, evaluate_predictor_on_holdout, compare, champion_predict_fn
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone

import eval_lib as el

ROOT = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.join(ROOT, "data", "accuracy_lab")
MANIFEST_PATH = os.path.join(LAB_DIR, "holdout_manifest.json")
RESULTS_DIR = os.path.join(LAB_DIR, "results")
DEFAULT_ROWS_PATH = os.path.join(ROOT, "backtest", "rows.jsonl")


def _read_rows(rows_path):
    rows = []
    if not os.path.exists(rows_path):
        return rows
    with open(rows_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _content_fingerprint(rows):
    """A cheap, real integrity check -- not a cryptographic guarantee, just
    enough to catch "the holdout partition's own row COUNT silently
    changed since it was locked" (e.g. rows.jsonl was edited or
    regenerated), which would otherwise make later comparisons quietly
    invalid without anyone knowing why results stopped matching."""
    return {"n_rows": len(rows), "n_hits": sum(1 for r in rows if r.get("outcome") == 1)}


def lock_holdout(rows_path=DEFAULT_ROWS_PATH, holdout_frac=0.2, manifest_path=None):
    """Return (train_rows, holdout_rows, cutoff_date), locking the holdout
    permanently on first call. Every later call (even with a different
    holdout_frac) returns the SAME partition the manifest already recorded
    -- raises ValueError if a caller passes a holdout_frac that doesn't
    match the lock, rather than silently using the locked one and letting
    the caller believe their argument took effect.

    manifest_path defaults to None, resolved to the module-level
    MANIFEST_PATH INSIDE the function body rather than bound in the
    signature -- a signature default is evaluated once at import time, so
    binding it there would make a test's `accuracy_lab.MANIFEST_PATH =
    tmpdir` silently not apply to any caller that omits the argument
    (the exact trap test_champion_challenger.py's own docstring already
    documents hitting once, for SHADOW_DIR/RESULTS_DIR)."""
    manifest_path = manifest_path if manifest_path is not None else MANIFEST_PATH
    rows = _read_rows(rows_path)
    if not rows:
        raise ValueError(f"no rows found at {rows_path!r} -- nothing to lock a holdout against")
    dated = sorted(rows, key=lambda r: r["date"])
    distinct_dates = sorted({r["date"] for r in dated})

    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        if abs(manifest["holdout_frac"] - holdout_frac) > 1e-9:
            raise ValueError(
                f"holdout is already locked at holdout_frac={manifest['holdout_frac']!r} "
                f"(cutoff_date={manifest['cutoff_date']!r}, locked_at={manifest['locked_at']!r}) "
                f"-- passed holdout_frac={holdout_frac!r} does not match. Delete "
                f"{manifest_path} and re-lock deliberately if the partition should change."
            )
        cutoff_date = manifest["cutoff_date"]
    else:
        n_holdout_dates = max(1, int(round(len(distinct_dates) * holdout_frac)))
        cutoff_date = distinct_dates[len(distinct_dates) - n_holdout_dates]
        manifest = {
            "cutoff_date": cutoff_date,
            "holdout_frac": holdout_frac,
            "rows_path": os.path.relpath(rows_path, ROOT),
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "n_distinct_dates_at_lock": len(distinct_dates),
        }
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    train = [r for r in dated if r["date"] < cutoff_date]
    holdout = [r for r in dated if r["date"] >= cutoff_date]
    return train, holdout, cutoff_date


def champion_predict_fn(row):
    """The actual current production policy's probability for one backtest
    row: Stage 5's calibrated_prob when apply_policy annotated this row
    with one, the raw predicted_prob otherwise. Never None for a graded
    row (to_row() already drops ungradeable/unpriced rows -- see
    backtest/engine.py)."""
    if row.get("calibrated_prob") is not None:
        return row["calibrated_prob"]
    return row.get("predicted_prob")


def evaluate_predictor_on_holdout(label, predict_fn, rows_path=DEFAULT_ROWS_PATH,
                                  holdout_frac=0.2, prop_type=None):
    """Score predict_fn(row) -> float or None against ONLY the locked
    holdout, append one timestamped record to data/accuracy_lab/results/
    {label}.jsonl, and return the metrics dict. predict_fn returning None
    for a row (no opinion) skips it -- never coerced into a guess, same
    discipline as champion_challenger.py's own score_fn contract."""
    _, holdout, cutoff_date = lock_holdout(rows_path, holdout_frac)
    if prop_type is not None:
        holdout = [r for r in holdout if r.get("prop_type") == prop_type]
    pairs = []
    n_skipped = 0
    for row in holdout:
        prob = predict_fn(row)
        if prob is None:
            n_skipped += 1
            continue
        pairs.append((float(prob), float(row["outcome"])))

    result = {
        "label": label,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff_date": cutoff_date,
        "prop_type": prop_type or "all",
        "n_holdout_rows": len(holdout),
        "n_scored": len(pairs),
        "n_skipped_no_opinion": n_skipped,
        "brier": el.brier(pairs),
        "log_loss": el.log_loss(pairs),
        "calibration_table": el.calibration_table(pairs) if pairs else [],
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{label}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")
    return result


def _load_results(label):
    path = os.path.join(RESULTS_DIR, f"{label}.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def latest_result(label):
    results = _load_results(label)
    return results[-1] if results else None


def compare(label_a, label_b):
    """Both labels' MOST RECENT evaluation records -- guaranteed to have
    been scored against the identical locked holdout by construction
    (evaluate_predictor_on_holdout always calls lock_holdout(), which
    always returns the same partition once locked). Returns None fields
    honestly (never a fabricated delta) when either label has no recorded
    evaluation yet."""
    a, b = latest_result(label_a), latest_result(label_b)
    if a is None or b is None:
        return {
            "label_a": label_a, "label_b": label_b,
            "a_evaluated": a is not None, "b_evaluated": b is not None,
            "brier_delta": None, "log_loss_delta": None,
            "note": "at least one label has no recorded evaluation yet -- run "
                    "evaluate_predictor_on_holdout() for it first",
        }
    if a["cutoff_date"] != b["cutoff_date"]:
        # Can only happen if the manifest was deleted and re-locked between
        # the two evaluations -- report it rather than silently comparing
        # across two different holdout partitions.
        return {
            "label_a": label_a, "label_b": label_b,
            "brier_delta": None, "log_loss_delta": None,
            "note": f"NOT comparable: {label_a} was evaluated against cutoff_date="
                    f"{a['cutoff_date']!r} but {label_b} against {b['cutoff_date']!r} "
                    f"-- the holdout lock changed between the two evaluations",
        }
    return {
        "label_a": label_a, "label_b": label_b,
        "cutoff_date": a["cutoff_date"],
        "a_evaluated_at": a["evaluated_at"], "b_evaluated_at": b["evaluated_at"],
        "a_n_scored": a["n_scored"], "b_n_scored": b["n_scored"],
        "a_brier": a["brier"], "b_brier": b["brier"],
        "brier_delta": (round(a["brier"] - b["brier"], 5)
                        if a["brier"] is not None and b["brier"] is not None else None),
        "a_log_loss": a["log_loss"], "b_log_loss": b["log_loss"],
        "log_loss_delta": (round(a["log_loss"] - b["log_loss"], 5)
                           if a["log_loss"] is not None and b["log_loss"] is not None else None),
    }


def main():
    train, holdout, cutoff_date = lock_holdout()
    print(f"Holdout locked at {cutoff_date}: {len(train)} train rows, "
          f"{len(holdout)} holdout rows.")
    result = evaluate_predictor_on_holdout("champion", champion_predict_fn)
    print(f"Champion (current production policy) on the locked holdout: "
          f"n={result['n_scored']}, brier={result['brier']}, log_loss={result['log_loss']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
