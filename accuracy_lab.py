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
import hashlib
import json
import os
from datetime import datetime, timezone

import eval_lib as el
import recommendation as _rec

ROOT = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.join(ROOT, "data", "accuracy_lab")
MANIFEST_PATH = os.path.join(LAB_DIR, "holdout_manifest.json")
RESULTS_DIR = os.path.join(LAB_DIR, "results")
DEFAULT_ROWS_PATH = os.path.join(ROOT, "backtest", "rows.jsonl")

MANIFEST_SCHEMA_VERSION = 2  # bumped 2026-08-27 -- see IncompatibleDatasetError

# The lowest manifest schema that can actually PROVE which bytes a holdout
# was locked against. v1 records only a path and a cutoff; v2 adds
# artifact_sha256/row_count/date_range, which is what makes content
# replacement at the same path detectable at all.
MIN_PROMOTION_GRADE_SCHEMA_VERSION = 2


class WeakDatasetIdentityError(Exception):
    """Raised when a caller asks for PROMOTION-GRADE evidence but the
    holdout manifest cannot prove what dataset it was locked against.

    2026-08-27, the second half of the dataset-identity hardening. Making
    v2 the default for NEW locks fixed the going-forward case, but every
    pre-existing schema-v1 manifest -- including the real production one
    at data/accuracy_lab/holdout_manifest.json -- remained silently
    acceptable, and a v1 manifest stores no checksum at all. Against a v1
    manifest, replacing backtest/rows.jsonl wholesale with a completely
    different dataset at the same path is undetectable: same path, same
    holdout_frac, same recorded cutoff, so the lock "matches" and every
    downstream comparison proceeds against a partition that no longer
    means what it claims. That is tolerable for replaying an old
    experiment (where the whole point is to reproduce what was done under
    the conditions of the time); it is not tolerable as the evidentiary
    basis for promoting a challenger into production.

    So the rule is a MODE, not a migration: legacy v1 keeps working
    untouched for reproducibility, historical comparison, and replay, and
    is refused only where the caller explicitly claims promotion-grade
    evidence. Nothing is silently rewritten, and no historical research
    breaks."""


class IncompatibleDatasetError(Exception):
    """Raised by lock_holdout() when the manifest at manifest_path is bound
    to a genuinely different artifact than the one being requested now.

    2026-08-27 hardening: the accuracy-lab holdout mechanism previously
    trusted a manifest's cutoff_date forever, for ANY rows_path a caller
    happened to pass, as long as holdout_frac matched -- it never checked
    whether the underlying data was still the SAME data. A new canonical
    artifact (backtest/canonical_run.py's assembled output, or any future
    replacement for backtest/rows.jsonl) landing at the same path, or a
    caller pointing the same manifest_path at a different rows_path, would
    have been silently accepted and evaluated against a holdout partition
    that no longer means what it claims to. This exception is that hazard
    made loud instead of silent, per the governing mission's explicit
    Phase 5 requirement: 'a holdout/experiment manifest should fail closed
    against a mismatched dataset... No silent reuse.'"""


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


def _artifact_sha256(rows_path):
    h = hashlib.sha256()
    with open(rows_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifact_identity(rows_path, rows, distinct_dates):
    """Every field this run of lock_holdout() can independently re-derive
    from the file on disk right now -- compared against what a prior lock
    recorded, never trusted from the manifest alone."""
    return {
        "artifact_sha256": _artifact_sha256(rows_path),
        "artifact_row_count": len(rows),
        "artifact_n_distinct_dates": len(distinct_dates),
        "artifact_date_range": [distinct_dates[0], distinct_dates[-1]] if distinct_dates else None,
    }


PROMOTION_GRADE_BINDING_FIELDS = (
    "cutoff_date",
    "holdout_frac",
    "rows_path",
    "artifact_sha256",
    "artifact_row_count",
    "artifact_n_distinct_dates",
    "artifact_date_range",
    "code_git_sha_at_lock",
)


def manifest_identity_strength(manifest):
    """Describe how strongly a manifest pins its dataset, without judging.

    Promotion-grade means more than "there is a checksum-shaped key".
    The manifest must bind the artifact bytes, row/date shape, source path,
    chronological partition, and code identity recorded when the lock was
    created.  Keeping this definition here gives every downstream research
    harness one fail-closed source of truth instead of letting each caller
    invent a weaker subset of fields.
    """
    version = int(manifest.get("manifest_schema_version", 1) or 1)
    missing = [k for k in PROMOTION_GRADE_BINDING_FIELDS
               if manifest.get(k) is None or manifest.get(k) == ""]
    has_checksum = bool(manifest.get("artifact_sha256"))
    return {
        "manifest_schema_version": version,
        "has_artifact_checksum": has_checksum,
        "has_row_count": manifest.get("artifact_row_count") is not None,
        "has_date_range": manifest.get("artifact_date_range") is not None,
        "has_code_sha_at_lock": bool(manifest.get("code_git_sha_at_lock")),
        "required_binding_fields": list(PROMOTION_GRADE_BINDING_FIELDS),
        "missing_binding_fields": missing,
        "promotion_grade": (
            version >= MIN_PROMOTION_GRADE_SCHEMA_VERSION
            and not missing
        ),
        "can_detect_content_replacement": has_checksum,
    }


def assert_promotion_grade_manifest(manifest, manifest_path=None):
    """Fail closed unless this manifest can prove its dataset identity.

    Callable directly by any framework that claims promotion-grade
    evidence (see backtest/equal_volume.py), so the rule lives in one
    place rather than being re-implemented per experiment.
    """
    strength = manifest_identity_strength(manifest)
    if strength["promotion_grade"]:
        return strength
    missing = strength["missing_binding_fields"]
    raise WeakDatasetIdentityError(
        f"holdout manifest{f' at {manifest_path!r}' if manifest_path else ''} is "
        f"schema v{strength['manifest_schema_version']} and cannot prove the complete "
        f"dataset/holdout identity required for a promotion-grade claim; missing "
        f"binding fields={missing}. A checksum alone is not enough to prove the "
        f"partition, row/date shape, path, and code identity used when the holdout "
        f"was locked. This manifest remains valid for legacy replay/reproduction "
        f"(require_strong_dataset_identity=False) -- it is NOT being migrated or "
        f"rewritten. For promotion-grade evidence, lock a fresh v"
        f"{MIN_PROMOTION_GRADE_SCHEMA_VERSION} manifest at a NEW manifest_path against "
        f"the artifact you intend to certify.")


def lock_holdout(rows_path=DEFAULT_ROWS_PATH, holdout_frac=0.2, manifest_path=None,
                 require_strong_dataset_identity=False):
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
    documents hitting once, for SHADOW_DIR/RESULTS_DIR).

    2026-08-27 hardening (see IncompatibleDatasetError): a manifest is now
    additionally bound to the exact artifact it was locked against --
    resolved rows_path, content sha256, row count, and date range. A call
    against an existing manifest whose rows_path resolves elsewhere, or
    whose CURRENT file content no longer matches what was recorded at lock
    time, raises IncompatibleDatasetError rather than silently reusing a
    stale or mismatched cutoff. This never rewrites an old manifest to
    "fix" a mismatch -- a genuinely new/different dataset must be locked
    under its own, newly created manifest_path. A pre-hardening (schema
    version 1, no artifact_sha256) manifest keeps working unmodified for
    calls that still point at its original rows_path, since nothing about
    THAT artifact's identity is actually in question; it is not retro-
    actively rewritten in place either.

    require_strong_dataset_identity (2026-08-27) is the PROMOTION-GRADE
    mode. Default False preserves every legacy/replay caller exactly.
    Passing True refuses any manifest that cannot prove which bytes it was
    locked against -- in practice, any schema-v1 manifest, which stores no
    checksum and therefore cannot detect the file at its path being
    replaced wholesale. A newly created manifest is always v2 and so is
    always promotion-grade; the gate only ever bites on a pre-existing
    weak one, and it refuses rather than upgrading it (see
    WeakDatasetIdentityError)."""
    manifest_path = manifest_path if manifest_path is not None else MANIFEST_PATH
    rows = _read_rows(rows_path)
    if not rows:
        raise ValueError(f"no rows found at {rows_path!r} -- nothing to lock a holdout against")
    dated = sorted(rows, key=lambda r: r["date"])
    distinct_dates = sorted({r["date"] for r in dated})
    requested_rows_path_rel = os.path.relpath(os.path.abspath(rows_path), ROOT)

    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        if require_strong_dataset_identity:
            # Checked BEFORE any other validation so a weak manifest can
            # never accidentally satisfy a promotion-grade caller by
            # passing the checks it is capable of.
            assert_promotion_grade_manifest(manifest, manifest_path)
        if abs(manifest["holdout_frac"] - holdout_frac) > 1e-9:
            raise ValueError(
                f"holdout is already locked at holdout_frac={manifest['holdout_frac']!r} "
                f"(cutoff_date={manifest['cutoff_date']!r}, locked_at={manifest['locked_at']!r}) "
                f"-- passed holdout_frac={holdout_frac!r} does not match. Delete "
                f"{manifest_path} and re-lock deliberately if the partition should change."
            )
        if manifest.get("rows_path") != requested_rows_path_rel:
            raise IncompatibleDatasetError(
                f"manifest at {manifest_path!r} is locked to rows_path="
                f"{manifest.get('rows_path')!r}, but this call passed "
                f"{requested_rows_path_rel!r} -- these are different artifacts. "
                f"Point at a NEW manifest_path to lock a holdout for this dataset; "
                f"do not reuse an existing manifest across artifacts.")
        if manifest.get("manifest_schema_version", 1) >= 2:
            current_identity = _artifact_identity(rows_path, rows, distinct_dates)
            recorded_identity = {k: manifest.get(k) for k in current_identity}
            if current_identity != recorded_identity:
                raise IncompatibleDatasetError(
                    f"manifest at {manifest_path!r} no longer matches the artifact at "
                    f"{rows_path!r}: locked identity was {recorded_identity}, current "
                    f"file identity is {current_identity}. The underlying dataset changed "
                    f"since this holdout was locked (regenerated, replaced, or truncated) "
                    f"-- results evaluated against the old lock are no longer meaningful "
                    f"for this file. This manifest is NOT automatically updated; either "
                    f"restore the original artifact or lock a fresh manifest at a new path "
                    f"for the new one.")
        cutoff_date = manifest["cutoff_date"]
    else:
        n_holdout_dates = max(1, int(round(len(distinct_dates) * holdout_frac)))
        cutoff_date = distinct_dates[len(distinct_dates) - n_holdout_dates]
        manifest = {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "cutoff_date": cutoff_date,
            "holdout_frac": holdout_frac,
            "rows_path": requested_rows_path_rel,
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "n_distinct_dates_at_lock": len(distinct_dates),
            "code_git_sha_at_lock": _rec.git_sha(short=False),
            **_artifact_identity(rows_path, rows, distinct_dates),
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


def challenger_predict_fn(challenger_name):
    """Stage 7 bridge: adapt a champion_challenger.py-REGISTERED Challenger
    (score_fn(candidate: dict), using the live candidate's own field names
    -- hit_probability/signals/market_odds/projection) into a predict_fn
    usable against backtest rows (predicted_prob/calibrated_prob/signals/
    prop_type). Reuses the real registered score_fn verbatim -- never
    reimplements a challenger's own logic here. The candidate's
    hit_probability is built from champion_predict_fn(row), so the
    challenger nudges the exact same base number the Champion itself is
    evaluated against, an honest apples-to-apples starting point. A
    challenger raising an exception on one row is caught and treated as
    "no opinion" for that row only, matching run_shadow()'s own
    one-broken-idea-must-not-take-down-the-run discipline."""
    import champion_challenger as cc
    spec = cc.registered().get(challenger_name)
    if spec is None:
        raise ValueError(f"no registered challenger named {challenger_name!r} -- "
                         f"registered: {sorted(cc.registered())}")
    score_fn = spec["score_fn"]

    def predict_fn(row):
        candidate = {
            "hit_probability": champion_predict_fn(row),
            "signals": row.get("signals") or {},
            "market_odds": row.get("market_odds"),
            "projection": {"stat": row.get("prop_type"), "needs": row.get("needs")},
        }
        try:
            return score_fn(candidate)
        except Exception:
            return None
    return predict_fn


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
    result = {
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
    if a["n_scored"] != b["n_scored"]:
        # Same locked holdout row SET, but one side may have declined to
        # opine on some rows (returned None -- see evaluate_predictor_on_
        # holdout's n_skipped_no_opinion). The delta above is still real,
        # but it is comparing two different row SUBSETS, not identical
        # ones -- say so rather than letting "same cutoff_date" imply more
        # than it does.
        result["note"] = (
            f"CAUTION: {label_a} scored {a['n_scored']} rows but {label_b} scored "
            f"{b['n_scored']} -- one of them skipped rows it had no opinion on (see "
            f"n_skipped_no_opinion), so this delta compares two different row "
            f"subsets of the same holdout, not an identical one")
    return result


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
