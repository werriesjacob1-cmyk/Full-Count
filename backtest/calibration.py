#!/usr/bin/env python3
"""
calibration.py — checks whether the model's probabilities mean what they
claim to mean, and fixes them when they don't.

WHY THIS EXISTS:

prop_probability.py computes "71.4% to get a hit" from a convolution of rate
stats. That number is a MODEL probability, not an observed frequency -- it
assumes independent PAs and true rate stats, and it ignores opposing-pitcher
quality, park, and in-game leverage. The system's stated goal is picking
props by chance of hitting, which makes the honesty of that number the whole
product: a well-calibrated 65% is worth more than an uncalibrated 80%, because
the uncalibrated 80% is a guess wearing a number.

This module is the check and the fix. It reads backtest rows (the contract in
SCHEMA.md: one row = one historical pick plus what actually happened, with
predicted_prob BEFORE calibration and outcome as the 1/0 result) and answers,
per SCHEMA.md's own framing: "when we say 70%, what actually happens?"

METHOD:

- reliability_table / calibration_curve_ascii: the diagnostic. Bin predicted
  probability, compare to observed hit rate per bin.
- brier_score / log_loss: scalar summaries of overall accuracy, plus a Brier
  skill score against a base-rate-only baseline so the raw number has
  something to be judged against.
- fit_calibrator: learns raw-probability -> calibrated-probability, via
  either Platt scaling (1-D logistic regression) or isotonic regression
  (pool-adjacent-violators, implemented here directly rather than importing
  sklearn's isotonic regressor -- this stays self-contained and inspectable,
  matching the rest of the codebase's style of explicit, auditable logic).
- evaluate_calibration: before/after comparison on a held-out set.

CORRECTNESS RULES THIS MODULE ENFORCES:

- Never fit and evaluate on the same rows. time_based_split / split_by_date
  are the only splits provided -- both time-ordered (train on earlier dates,
  test on later), because a random split leaks future information into a
  forecasting system's training set and reports a fake accuracy number that
  the SCHEMA.md "no lookahead" rule exists specifically to prevent.
- Small-sample honesty. Every bin's count is always reported. Bins with
  fewer than min_bin_count rows (default 30) are marked unreliable and
  excluded from the summary error metric -- they're shown, never hidden, but
  never trusted either.
- fair_test rows (2 PA off the bench -- see SCHEMA.md / grade_results.py) are
  noise for calibration. Every entry point takes an explicit
  fair_test_only flag with NO default exclusion; whichever way the caller
  chooses, that choice is recorded in the output ("meta" / "note" fields)
  so a report can never be read without knowing which rows fed it.
- Segmentation by prop_type is a first-class path (fit_calibrators_by_prop_type),
  because a hits prop and a strikeout prop can be miscalibrated in opposite
  directions and a single pooled curve can hide both.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict

import numpy as np

# Below this many rows in a bin, an "observed rate" is mostly sampling noise
# -- e.g. 4/6 hitting isn't meaningfully different from 3/6 or 5/6. Applies
# to reliability_table bins and to the minimum sample fit_calibrator requires
# before it will produce a fit at all.
MIN_BIN_COUNT = 30


# ══════════════════════════════════════════════════════════════════════════
#  Row filtering helpers
# ══════════════════════════════════════════════════════════════════════════

def _filter_rows(rows, fair_test_only=False, prop_type=None):
    """Apply the two standard row filters. fair_test_only and prop_type are
    always explicit -- there is no calibration function in this module that
    filters silently."""
    out = []
    for r in rows:
        if prop_type is not None and r.get("prop_type") != prop_type:
            continue
        if fair_test_only and not r.get("fair_test", True):
            continue
        out.append(r)
    return out


def _extract(rows):
    p = np.array([float(r["predicted_prob"]) for r in rows], dtype=float)
    y = np.array([int(r["outcome"]) for r in rows], dtype=float)
    return p, y


def segment_by_prop_type(rows):
    """Group rows into {prop_type: [rows]}. A hits prop and a strikeout prop
    can be miscalibrated in opposite directions -- a single pooled curve can
    hide both, so most real calibration work should happen per segment."""
    segments = defaultdict(list)
    for r in rows:
        segments[r.get("prop_type", "unknown")].append(r)
    return dict(segments)


# ══════════════════════════════════════════════════════════════════════════
#  1. Reliability table -- the core diagnostic
# ══════════════════════════════════════════════════════════════════════════

def reliability_table(rows, n_bins=10, fair_test_only=False, prop_type=None,
                       min_bin_count=MIN_BIN_COUNT):
    """Bin rows by predicted_prob and report, per bin: count, mean predicted
    probability, observed hit rate, and the gap between them.

    gap = observed_rate - mean_predicted. Negative means the model is
    overconfident in that bin (it promises more than it delivers); positive
    means underconfident.

    fair_test_only and prop_type are never applied silently -- both are
    echoed back in the returned dict's "meta" so a downstream report always
    states which rows it's reading.

    Bins with fewer than min_bin_count rows are still reported (count is
    always shown) but are marked reliable=False and excluded from the
    aggregate expected_calibration_error, because a confident-looking gap
    computed from a handful of rows is noise, not a finding.
    """
    filtered = _filter_rows(rows, fair_test_only=fair_test_only, prop_type=prop_type)
    bins = [[] for _ in range(n_bins)]
    for r in filtered:
        p = float(r["predicted_prob"])
        idx = min(n_bins - 1, max(0, int(p * n_bins)))
        bins[idx].append(r)

    bin_rows = []
    ece_numerator = 0.0
    reliable_n = 0
    for i, brows in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        count = len(brows)
        if count == 0:
            bin_rows.append({
                "bin": i, "range": [lo, hi], "count": 0,
                "mean_predicted": None, "observed_rate": None,
                "gap": None, "reliable": False,
            })
            continue
        mean_pred = sum(float(r["predicted_prob"]) for r in brows) / count
        observed = sum(int(r["outcome"]) for r in brows) / count
        gap = observed - mean_pred
        reliable = count >= min_bin_count
        bin_rows.append({
            "bin": i, "range": [lo, hi], "count": count,
            "mean_predicted": round(mean_pred, 4),
            "observed_rate": round(observed, 4),
            "gap": round(gap, 4), "reliable": reliable,
        })
        if reliable:
            ece_numerator += count * abs(gap)
            reliable_n += count

    ece = (ece_numerator / reliable_n) if reliable_n > 0 else None

    return {
        "meta": {
            "n_rows_in": len(rows),
            "n_rows_used": len(filtered),
            "fair_test_only": fair_test_only,
            "prop_type": prop_type or "all",
            "n_bins": n_bins,
            "min_bin_count": min_bin_count,
        },
        "bins": bin_rows,
        "expected_calibration_error": round(ece, 4) if ece is not None else None,
        "note": (
            "expected_calibration_error is a count-weighted mean |gap| over "
            f"bins with count >= {min_bin_count} only; smaller bins are "
            "reported (see per-bin 'count') but excluded from that summary "
            "as statistically unreliable, not hidden."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════
#  2. Scalar accuracy summaries
# ══════════════════════════════════════════════════════════════════════════

def brier_score(rows, fair_test_only=False, prop_type=None):
    """Mean squared error between predicted_prob and outcome (lower is
    better; 0 is perfect). Also reports the Brier skill score against a
    baseline that always predicts the sample's base rate -- raw Brier is
    hard to interpret on its own since its scale depends on how extreme the
    true outcome distribution is; the skill score answers "better than just
    guessing the average hit rate?" directly. BSS > 0 means better than that
    baseline, BSS <= 0 means the model isn't earning its complexity.
    """
    filtered = _filter_rows(rows, fair_test_only, prop_type)
    if not filtered:
        return {
            "brier_score": None, "n": 0, "baseline_rate": None,
            "baseline_brier": None, "brier_skill_score": None,
            "fair_test_only": fair_test_only, "prop_type": prop_type or "all",
        }
    p, y = _extract(filtered)
    bs = float(np.mean((p - y) ** 2))
    base_rate = float(np.mean(y))
    bs_baseline = float(np.mean((base_rate - y) ** 2))
    bss = (1.0 - bs / bs_baseline) if bs_baseline > 0 else None
    return {
        "brier_score": round(bs, 5),
        "n": len(filtered),
        "baseline_rate": round(base_rate, 4),
        "baseline_brier": round(bs_baseline, 5),
        "brier_skill_score": round(bss, 5) if bss is not None else None,
        "fair_test_only": fair_test_only,
        "prop_type": prop_type or "all",
    }


def log_loss(rows, fair_test_only=False, prop_type=None, eps=1e-12):
    """Mean negative log-likelihood of the outcomes under predicted_prob
    (lower is better). Punishes confident wrong calls far harder than Brier
    does, which matters here because a confidently-wrong "92%" is exactly
    the failure mode that erodes trust in the whole pipeline. Also reports
    the same metric for a base-rate-only baseline for context."""
    filtered = _filter_rows(rows, fair_test_only, prop_type)
    if not filtered:
        return {
            "log_loss": None, "n": 0, "baseline_log_loss": None,
            "fair_test_only": fair_test_only, "prop_type": prop_type or "all",
        }
    p, y = _extract(filtered)
    p_c = np.clip(p, eps, 1 - eps)
    ll = float(-np.mean(y * np.log(p_c) + (1 - y) * np.log(1 - p_c)))
    base_rate = float(np.mean(y))
    br = min(max(base_rate, eps), 1 - eps)
    ll_baseline = float(-np.mean(y * np.log(br) + (1 - y) * np.log(1 - br)))
    return {
        "log_loss": round(ll, 5),
        "n": len(filtered),
        "baseline_log_loss": round(ll_baseline, 5),
        "fair_test_only": fair_test_only,
        "prop_type": prop_type or "all",
    }


# ══════════════════════════════════════════════════════════════════════════
#  3. Terminal-renderable reliability plot
# ══════════════════════════════════════════════════════════════════════════

def calibration_curve_ascii(rows, n_bins=10, fair_test_only=False, prop_type=None,
                             min_bin_count=MIN_BIN_COUNT, width=50, height=20):
    """A reliability plot drawn with characters, for a text report read in a
    terminal. '.' marks the diagonal (perfect calibration); '*' marks a bin
    with enough samples to trust; 'o' marks a bin below min_bin_count -- shown
    so it isn't hidden, but visually distinguished so it isn't mistaken for
    a real finding either.
    """
    table = reliability_table(rows, n_bins=n_bins, fair_test_only=fair_test_only,
                               prop_type=prop_type, min_bin_count=min_bin_count)
    lines = []
    lines.append(
        f"Reliability plot -- prop_type={table['meta']['prop_type']} "
        f"fair_test_only={fair_test_only} n={table['meta']['n_rows_used']}"
    )
    lines.append(
        "Y axis = observed hit rate, X axis = mean predicted probability. "
        f"'.' = perfect calibration, '*' = bin with n>={min_bin_count}, "
        f"'o' = bin with n<{min_bin_count} (shown, not trustworthy)."
    )
    lines.append("")

    grid = [[' ' for _ in range(width)] for _ in range(height)]

    def col(x):
        return min(width - 1, max(0, int(round(x * (width - 1)))))

    def row_of(yv):
        r = int(round((1 - yv) * (height - 1)))
        return min(height - 1, max(0, r))

    for x_i in range(width):
        xv = x_i / (width - 1)
        r = row_of(xv)
        if grid[r][x_i] == ' ':
            grid[r][x_i] = '.'

    for b in table["bins"]:
        if b["count"] == 0:
            continue
        c = col(b["mean_predicted"])
        r = row_of(b["observed_rate"])
        grid[r][c] = '*' if b["reliable"] else 'o'

    for r in range(height):
        yv = 1 - r / (height - 1)
        lines.append(f"{yv:4.2f} |" + ''.join(grid[r]))
    lines.append("     +" + '-' * width)
    ticks = [0.0, 0.25, 0.5, 0.75, 1.0]
    axis = [' '] * width
    for t in ticks:
        c = col(t)
        s = f"{t:.2f}"
        for i, ch in enumerate(s):
            if c + i < width:
                axis[c + i] = ch
    lines.append("      " + ''.join(axis))
    lines.append("")

    for b in table["bins"]:
        if b["count"] == 0:
            status = "empty"
            pred_s, obs_s, gap_s = "   -  ", "   -  ", "   -  "
        else:
            status = "reliable" if b["reliable"] else "TOO FEW SAMPLES (noise)"
            pred_s = f"{b['mean_predicted']:.3f}"
            obs_s = f"{b['observed_rate']:.3f}"
            gap_s = f"{b['gap']:+.3f}"
        lines.append(
            f"  bin {b['bin']:>2} [{b['range'][0]:.2f},{b['range'][1]:.2f}) "
            f"n={b['count']:<5} pred={pred_s} obs={obs_s} gap={gap_s}  {status}"
        )
    ece = table["expected_calibration_error"]
    lines.append("")
    lines.append(
        f"expected_calibration_error (reliable bins only) = "
        f"{ece if ece is not None else 'n/a -- no bin had >= ' + str(min_bin_count) + ' rows'}"
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  4. Fitting calibrators -- Platt scaling and isotonic regression (PAV)
# ══════════════════════════════════════════════════════════════════════════

def _sigmoid(z):
    z = np.clip(z, -500, 500)
    return 1.0 / (1.0 + np.exp(-z))


def _fit_platt(p, y, max_iter=200, tol=1e-9, l2=1e-6, max_step=25.0):
    """1-D logistic regression: calibrated = sigmoid(A*predicted_prob + B),
    fit by Newton's method on the standard logistic log-loss.

    Targets are Platt's label-smoothed targets rather than raw 0/1:
    t = (N+ + 1)/(N+ + 2) for hits, 1/(N- + 2) for misses. This keeps the fit
    from chasing perfectly-separable-looking data to infinite confidence on
    a small sample -- the same failure mode this whole module exists to
    catch, just inside the fitter itself.
    """
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    n_pos = float(np.sum(y == 1))
    n_neg = float(np.sum(y == 0))
    t = np.where(y == 1, (n_pos + 1.0) / (n_pos + 2.0), 1.0 / (n_neg + 2.0))

    A, B = 0.0, 0.0
    for _ in range(max_iter):
        z = A * p + B
        q = _sigmoid(z)
        d = q * (1 - q)
        g1 = np.sum((q - t) * p)
        g2 = np.sum(q - t)
        h11 = np.sum(d * p * p) + l2
        h22 = np.sum(d) + l2
        h12 = np.sum(d * p)
        det = h11 * h22 - h12 * h12
        if abs(det) < 1e-12:
            break
        dA = -(h22 * g1 - h12 * g2) / det
        dB = -(-h12 * g1 + h11 * g2) / det
        dA = max(-max_step, min(max_step, dA))
        dB = max(-max_step, min(max_step, dB))
        A += dA
        B += dB
        if abs(dA) < tol and abs(dB) < tol:
            break
    return float(A), float(B)


def _pool_adjacent_violators(x, y, w):
    """Pool-Adjacent-Violators, weighted L2. x must already be sorted
    ascending with one (weighted) point per unique x value. Returns yhat: a
    non-decreasing step function of x, fit by iteratively merging adjacent
    blocks that violate monotonicity into their weighted mean.

    Implemented directly (not via sklearn.isotonic) so the fit is a few
    lines of plain arithmetic anyone can step through, matching this
    codebase's preference for explicit, inspectable logic over an opaque
    library call for something this small.
    """
    n = len(y)
    # each stack entry: [level, weight, start_idx, end_idx_exclusive]
    stack = []
    for i in range(n):
        block = [float(y[i]), float(w[i]), i, i + 1]
        stack.append(block)
        while len(stack) > 1 and stack[-2][0] > stack[-1][0]:
            b2 = stack.pop()
            b1 = stack.pop()
            new_w = b1[1] + b2[1]
            new_level = (b1[0] * b1[1] + b2[0] * b2[1]) / new_w
            stack.append([new_level, new_w, b1[2], b2[3]])
    yhat = np.empty(n, dtype=float)
    for level_val, _wgt, s, e in stack:
        yhat[s:e] = level_val
    return yhat


def _fit_isotonic(p, y):
    """Aggregate duplicate predicted_prob values (weighted mean of their
    outcomes) so the fitted mapping is a genuine function of p, then run PAV
    over the unique points. Returns (x_points, y_points) suitable for linear
    interpolation at prediction time."""
    order = np.argsort(p, kind="stable")
    p_sorted = p[order]
    y_sorted = y[order]
    n = len(p_sorted)

    ux, uy, uw = [], [], []
    i = 0
    while i < n:
        j = i
        s, c = 0.0, 0
        xv = p_sorted[i]
        while j < n and p_sorted[j] == xv:
            s += y_sorted[j]
            c += 1
            j += 1
        ux.append(xv)
        uy.append(s / c)
        uw.append(c)
        i = j

    ux_arr = np.array(ux, dtype=float)
    uy_arr = np.array(uy, dtype=float)
    uw_arr = np.array(uw, dtype=float)
    fitted = _pool_adjacent_violators(ux_arr, uy_arr, uw_arr)
    return ux_arr.tolist(), fitted.tolist()


class Calibrator:
    """A fitted predicted_prob -> calibrated_prob mapping. Callable directly
    (calibrator(p)), and serializes to/from a plain JSON-able dict so the
    live pipeline can load a fit without ever re-running fit_calibrator."""

    def __init__(self, method, params, meta=None):
        if method not in ("isotonic", "platt"):
            raise ValueError(f"unknown calibration method: {method!r}")
        self.method = method
        self.params = params
        self.meta = meta or {}

    def predict(self, p):
        scalar = isinstance(p, (int, float))
        arr = np.atleast_1d(np.asarray(p, dtype=float))
        if self.method == "platt":
            A, B = self.params["A"], self.params["B"]
            out = _sigmoid(A * arr + B)
        else:  # isotonic
            xs, ys = self.params["x"], self.params["y"]
            out = np.interp(arr, xs, ys, left=ys[0], right=ys[-1])
        out = np.clip(out, 0.0, 1.0)
        return float(out[0]) if scalar else out

    def __call__(self, p):
        return self.predict(p)

    def to_dict(self):
        return {"method": self.method, "params": self.params, "meta": self.meta}

    @classmethod
    def from_dict(cls, d):
        return cls(d["method"], d["params"], d.get("meta", {}))

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls.from_dict(json.load(f))


def fit_calibrator(rows, method="isotonic", fair_test_only=False, prop_type=None,
                    min_rows=MIN_BIN_COUNT):
    """Fit a Calibrator on rows. Raises ValueError rather than returning a
    fit on fewer than min_rows rows -- a calibrator "fit" on a handful of
    rows is not a calibrator, it's the small-sample-noise problem this whole
    module exists to guard against, just moved into the fitter.

    fair_test_only has no default exclusion: the caller states it, and it is
    recorded on the returned Calibrator's .meta.
    """
    filtered = _filter_rows(rows, fair_test_only, prop_type)
    if len(filtered) < min_rows:
        raise ValueError(
            f"only {len(filtered)} rows available (min_rows={min_rows}); "
            "refusing to fit a calibrator on a sample this small -- the fit "
            "would encode noise, not a real miscalibration pattern."
        )
    p, y = _extract(filtered)
    dates = [r["date"] for r in filtered if "date" in r]
    meta = {
        "method": method,
        "n_rows": len(filtered),
        "fair_test_only": fair_test_only,
        "prop_type": prop_type or "all",
        "date_range": [min(dates), max(dates)] if dates else None,
    }
    if method == "isotonic":
        x_pts, y_pts = _fit_isotonic(p, y)
        params = {"x": x_pts, "y": y_pts}
    elif method == "platt":
        A, B = _fit_platt(p, y)
        params = {"A": A, "B": B}
    else:
        raise ValueError(
            f"unknown calibration method: {method!r} (expected 'isotonic' or 'platt')"
        )
    return Calibrator(method, params, meta)


def fit_calibrators_by_prop_type(rows, method="isotonic", fair_test_only=False,
                                  min_rows=MIN_BIN_COUNT):
    """Fit one Calibrator per prop_type rather than one pooled calibrator.
    Returns (calibrators, skipped): calibrators is {prop_type: Calibrator};
    skipped is {prop_type: row_count} for any prop_type with fewer than
    min_rows rows -- reported, never silently dropped.
    """
    segments = segment_by_prop_type(_filter_rows(rows, fair_test_only=fair_test_only))
    calibrators, skipped = {}, {}
    for prop_type, seg_rows in segments.items():
        if len(seg_rows) < min_rows:
            skipped[prop_type] = len(seg_rows)
            continue
        calibrators[prop_type] = fit_calibrator(
            seg_rows, method=method, fair_test_only=False, prop_type=None,
            min_rows=min_rows,
        )
    return calibrators, skipped


# ══════════════════════════════════════════════════════════════════════════
#  5. Applying + persisting calibrators
# ══════════════════════════════════════════════════════════════════════════

def apply_calibrator(calibrator, p):
    """Map a raw predicted_prob (scalar or array-like) through a fitted
    Calibrator to get a calibrated probability."""
    return calibrator.predict(p)


def save_calibrator(calibrator, path):
    calibrator.save(path)


def load_calibrator(path):
    return Calibrator.load(path)


def save_calibrators(calibrators, path):
    """Save a {prop_type: Calibrator} mapping as produced by
    fit_calibrators_by_prop_type."""
    payload = {pt: c.to_dict() for pt, c in calibrators.items()}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_calibrators(path):
    with open(path) as f:
        payload = json.load(f)
    return {pt: Calibrator.from_dict(d) for pt, d in payload.items()}


# ══════════════════════════════════════════════════════════════════════════
#  6. Time-based splitting and before/after evaluation
# ══════════════════════════════════════════════════════════════════════════

def time_based_split(rows, test_frac=0.3):
    """Split rows by date: train on the earlier portion, test on the later
    portion. A forecasting system must never be evaluated on a random split
    -- that leaks rows from the test set's future into training and reports
    an accuracy the system could never actually achieve live, which is
    exactly the "spectacular fake accuracy" SCHEMA.md's no-lookahead rule
    warns about. This is the honest split.
    """
    if not rows:
        return [], []
    dated = sorted(rows, key=lambda r: r["date"])
    n_test = max(1, int(round(len(dated) * test_frac))) if len(dated) > 1 else 0
    split_idx = len(dated) - n_test
    return dated[:split_idx], dated[split_idx:]


def split_by_date(rows, cutoff_date):
    """Split rows at an explicit date: train = date < cutoff, test = date >=
    cutoff. Prefer this over time_based_split when a specific cutoff (e.g.
    "everything before the backtest window I'm reporting on") matters more
    than a fixed fraction."""
    train = [r for r in rows if r["date"] < cutoff_date]
    test = [r for r in rows if r["date"] >= cutoff_date]
    return train, test


def evaluate_calibration(rows, calibrator, fair_test_only=False, prop_type=None,
                          n_bins=10, min_bin_count=MIN_BIN_COUNT):
    """Before/after comparison of raw vs calibrated probabilities on `rows`.

    `rows` must be held-out data the calibrator was NOT fit on -- use
    time_based_split or split_by_date to produce it. This function has no
    way to verify that itself, which is why the returned meta carries an
    explicit warning restating the requirement every time.
    """
    filtered = _filter_rows(rows, fair_test_only, prop_type)
    calibrated_rows = [
        dict(r, predicted_prob=apply_calibrator(calibrator, float(r["predicted_prob"])))
        for r in filtered
    ]

    before = {
        "brier": brier_score(filtered),
        "log_loss": log_loss(filtered),
        "reliability": reliability_table(filtered, n_bins=n_bins, min_bin_count=min_bin_count),
    }
    after = {
        "brier": brier_score(calibrated_rows),
        "log_loss": log_loss(calibrated_rows),
        "reliability": reliability_table(calibrated_rows, n_bins=n_bins, min_bin_count=min_bin_count),
    }

    brier_before, brier_after = before["brier"]["brier_score"], after["brier"]["brier_score"]
    ll_before, ll_after = before["log_loss"]["log_loss"], after["log_loss"]["log_loss"]

    return {
        "meta": {
            "n_rows": len(filtered),
            "fair_test_only": fair_test_only,
            "prop_type": prop_type or "all",
            "calibrator_method": calibrator.method,
            "calibrator_meta": calibrator.meta,
            "warning": (
                "Valid only if `rows` were not used to fit `calibrator`. "
                "Use time_based_split/split_by_date to guarantee that -- "
                "this function cannot check it for you."
            ),
        },
        "before": before,
        "after": after,
        "brier_improvement": round(brier_before - brier_after, 5)
            if brier_before is not None and brier_after is not None else None,
        "log_loss_improvement": round(ll_before - ll_after, 5)
            if ll_before is not None and ll_after is not None else None,
    }
