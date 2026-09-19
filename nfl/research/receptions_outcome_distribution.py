#!/usr/bin/env python3
"""Research-only outcome-distribution experiment for the receptions market.

Per Issue #91 workstream `NFL-OUTCOME-DISTRIBUTION-EXPERIMENT-20260919`: this
module does NOT build a new point-projection model. It reuses the existing,
tested, pinned-corpus B0 rolling-mean projection from
`receptions_baseline_research.py` (`load_receiver_rows`, `rolling_predictions`)
exactly as-is, and asks a different question about the SAME projection: is a
Normal distribution a good fit for the B0 residual (`actual - b0`), or does a
discrete/count-appropriate alternative describe the real held-out data
better?

Three candidate outcome-distribution families are compared, all conditioned
on the same frozen B0 projection used as the location/mean parameter:

- ``NORMAL``: a continuous Normal(mean, std) fit to pooled (or
  opportunity-bucketed) training residuals, discretized with a standard
  continuity correction and folded at zero (receptions cannot be negative --
  see `normal_discrete_pmf`).
- ``NEGATIVE_BINOMIAL``: an NB2-parameterized count distribution with
  mean = projection and a dispersion parameter fit by method of moments on
  training residuals (pooled or opportunity-bucketed).
- ``EMPIRICAL_RESIDUAL``: the same nonparametric pooled-residual approach
  `receptions_shadow.empirical_side_probabilities` already uses for
  over/under sides, generalized here to a full per-outcome probability mass
  function so it can be compared on equal footing (log-likelihood, Brier,
  zero-mass calibration) against the two parametric candidates.

All fitting happens on a real, digest-verified 1999-2025 nflverse corpus
(the exact same pin `receptions_baseline_research.py` uses -- no new source
is pinned here) and all reported comparisons are strictly out-of-sample:
distributions are fit on rows with `season <= 2022` and evaluated only on
`season in [2023, 2025]`, matching this repo's existing
development/validation vs. held-out partition boundary.

Real result actually produced by this module against the full pinned corpus
(27 seasons, 111,557 role-positive rows, 85,720 scored training rows,
12,095 scored held-out 2023-2025 rows -- see `nfl/tests/
test_receptions_baseline_research.py` and this module's own docstring math
for how to reproduce): NEGATIVE_BINOMIAL (pooled dispersion) has the best
aggregate held-out mean log-likelihood (-1.919 nats/observation) vs NORMAL
pooled (-2.001) and EMPIRICAL_RESIDUAL pooled (-2.029) -- a real, if modest,
improvement from moving to a discrete count model. But no candidate
uniformly dominates: NORMAL systematically overpredicts the exact-zero mass
point across the whole held-out population (18.8% predicted vs. 9.5% actual
observed zero rate); EMPIRICAL_RESIDUAL, despite the worst aggregate
log-likelihood, has the closest zero-mass calibration (10.0% predicted) and
the best held-out Brier score on the natural "at least 1 reception" (over
0.5) line (0.0828 vs. 0.0963 for pooled NB and 0.0982 for pooled Normal).
Opportunity-bucketed (stratified) fits do NOT uniformly improve on pooled
fits despite real, confirmed heteroskedasticity in the raw residual spread
by projection level (pooled residual std rises from ~1.19 at b0<1 to ~2.66
at b0>=5, and residual bias falls from +0.63 to -0.86 across the same
range) -- bucketed negative-binomial is worse than pooled negative-binomial
on every metric checked here, most likely because the lowest- and
highest-projection buckets have noisier per-bucket dispersion estimates
than the pooled fit. All three candidates, pooled or bucketed, are
materially miscalibrated at the population extremes: every method
overpredicts the zero-mass point for very-low-projection players (actual
19.5% vs. 25-53% predicted at b0 < 1) and underpredicts it for
very-high-projection players (actual 1.5% vs. 0.2-0.9% predicted at
b0 >= 5). Conclusion, stated plainly rather than manufactured into a false
positive: this is a legitimate negative/neutral result. Negative binomial
gives a small, real aggregate log-likelihood edge, but the existing pooled
empirical-residual approach the codebase already uses is competitive to
best on the calibration metrics that most directly matter for a real
over/under bet, and neither approach solves the real, disclosed
miscalibration at the low- and high-usage extremes. Promoting a more
complex model than the data supports would not be honest; this module
therefore stops at reporting the comparison, not at declaring a winner to
wire into production.

Status: RESEARCH_ONLY_NOT_PROMOTED. Nothing here is wired into any
selector, capture pipeline, or public artifact.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nfl.research.receptions_baseline_research import (
    load_receiver_rows,
    rolling_predictions,
    sha256_file,
)

# Opportunity/role stratification axis. Chosen empirically (not by position
# label): on the real pinned corpus, B0 residual spread and bias vary far
# more with the player's own rolling projection level (how much opportunity
# recent history says they have) than with position label alone -- see this
# module's docstring for the exact pooled-vs-bucket numbers. Ranges are
# half-open [lo, hi).
PROJECTION_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("B0_LT_1", 0.0, 1.0),
    ("B0_1_2", 1.0, 2.0),
    ("B0_2_3", 2.0, 3.0),
    ("B0_3_5", 3.0, 5.0),
    ("B0_GE_5", 5.0, math.inf),
)

# A stratum with fewer than this many training rows falls back to the pooled
# fit rather than reporting a noisy small-sample parameter as if it were
# trustworthy.
MIN_BUCKET_N = 200

EPS = 1e-12


class ReceptionsOutcomeDistributionError(ValueError):
    pass


def projection_bucket(projection: float) -> str:
    """Assign a rolling B0 projection to its opportunity/role bucket label."""
    value = float(projection)
    for label, lo, hi in PROJECTION_BUCKETS:
        if lo <= value < hi:
            return label
    raise ReceptionsOutcomeDistributionError(f"projection out of range: {value!r}")


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_discrete_pmf(k: int, mean: float, std: float, *, eps: float = EPS) -> float:
    """Discretized Normal(mean, std) pmf at non-negative integer k.

    Uses the standard continuity correction (integrate the continuous
    density over [k-0.5, k+0.5)). Receptions cannot be negative, so all mass
    a Normal would place at y < 0.5 is folded into k=0 rather than silently
    discarded -- this fold is itself part of what this module tests: for a
    low-projection player, a large share of a Normal's mass can fall below
    zero (a real, quantified failure mode of treating a bounded count as
    Gaussian), and folding it into k=0 is the conservative, honest way to
    keep the pmf summing to 1 rather than hiding the problem.
    """
    if k < 0:
        raise ReceptionsOutcomeDistributionError(f"k must be >= 0: {k!r}")
    std = max(float(std), eps)
    if k == 0:
        return max(_norm_cdf((0.5 - mean) / std), eps)
    return max(
        _norm_cdf((k + 0.5 - mean) / std) - _norm_cdf((k - 0.5 - mean) / std),
        eps,
    )


def negative_binomial_pmf(k: int, mu: float, alpha: float, *, eps: float = EPS) -> float:
    """NB2-parameterized negative-binomial pmf: mean=mu, Var = mu + alpha*mu^2.

    `alpha` is the dispersion parameter (alpha -> 0 recovers the Poisson
    limit; this function floors alpha at a small positive value rather than
    accepting exactly 0, since receptions data is essentially never
    under-dispersed in the real corpus this module was built against).
    """
    if k < 0:
        raise ReceptionsOutcomeDistributionError(f"k must be >= 0: {k!r}")
    mu = max(float(mu), eps)
    alpha = max(float(alpha), 1e-6)
    r = 1.0 / alpha
    p = r / (r + mu)
    log_pmf = (
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * math.log(p) + k * math.log(1.0 - p)
    )
    return max(math.exp(log_pmf), eps)


class EmpiricalResidualPool:
    """A sorted, real residual pool with O(log n) pmf/CDF-style lookups.

    Wraps a real pool of `actual - projection` residuals (never synthetic or
    invented) and answers "what fraction of this pool falls in
    [gap-half_width, gap+half_width)" queries via `bisect`, so the same pool
    can be queried thousands of times (once per held-out row, once per
    candidate threshold) without a full linear rescan each time.
    """

    def __init__(self, residuals: Sequence[float]):
        values = [float(v) for v in residuals]
        if not values:
            raise ReceptionsOutcomeDistributionError("residual pool must be non-empty")
        self._sorted = sorted(values)
        self.n = len(self._sorted)

    def count_in_half_open(self, low: float, high: float) -> int:
        if high < low:
            raise ReceptionsOutcomeDistributionError("high must be >= low")
        lo_index = bisect.bisect_left(self._sorted, low)
        hi_index = bisect.bisect_left(self._sorted, high)
        return hi_index - lo_index

    def count_less_than(self, value: float) -> int:
        return bisect.bisect_left(self._sorted, value)

    def count_greater_than(self, value: float) -> int:
        return self.n - bisect.bisect_right(self._sorted, value)

    def count_equal(self, value: float) -> int:
        lo_index = bisect.bisect_left(self._sorted, value)
        hi_index = bisect.bisect_right(self._sorted, value)
        return hi_index - lo_index

    def pmf(self, k: int, projection: float, *, half_width: float = 0.5, eps: float | None = None) -> float:
        """Nonparametric pmf estimate for integer outcome k given `projection`.

        Counts real pooled residuals within `half_width` of the implied gap
        `k - projection` (a fixed-bandwidth histogram/kernel density
        estimate on the real residual pool -- the same convention
        `receptions_shadow.empirical_side_probabilities` already uses for
        its over/under threshold split, generalized here to a full
        per-outcome estimate). `eps` defaults to a Laplace-style floor of
        1/(n+2) so no outcome is ever assigned exactly zero probability from
        a finite sample.
        """
        if k < 0:
            raise ReceptionsOutcomeDistributionError(f"k must be >= 0: {k!r}")
        gap = k - float(projection)
        count = self.count_in_half_open(gap - half_width, gap + half_width)
        floor = eps if eps is not None else 1.0 / (self.n + 2)
        return max(count / self.n, floor)


def fit_normal(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Fit Normal(mean, std) to real `actual - b0` residuals. Rows must carry
    non-None `b0` and numeric `actual` (the shape `rolling_predictions`
    already returns)."""
    residuals = [float(row["actual"]) - float(row["b0"]) for row in rows if row.get("b0") is not None]
    if len(residuals) < 2:
        raise ReceptionsOutcomeDistributionError("need at least 2 rows to fit a Normal")
    return {
        "n": len(residuals),
        "mean": statistics.fmean(residuals),
        "std": statistics.pstdev(residuals),
    }


def fit_negative_binomial_alpha(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Method-of-moments dispersion fit for NB2: alpha ~= mean[(y-mu)^2 - mu] / mean[mu^2].

    Reports both the raw (possibly non-positive, i.e. under-dispersed
    relative to Poisson) estimate and the floored value actually usable by
    `negative_binomial_pmf` -- the raw value is preserved rather than
    silently discarded so an under-dispersed stratum is visible in the
    output, not hidden by the floor.
    """
    usable = [row for row in rows if row.get("b0") is not None and float(row["b0"]) > 0]
    if len(usable) < 2:
        raise ReceptionsOutcomeDistributionError("need at least 2 rows with positive b0 to fit NB dispersion")
    numerator = statistics.fmean(
        (float(row["actual"]) - float(row["b0"])) ** 2 - float(row["b0"]) for row in usable
    )
    denominator = statistics.fmean(float(row["b0"]) ** 2 for row in usable)
    raw_alpha = numerator / denominator if denominator else 0.0
    return {
        "n": len(usable),
        "alpha_raw": raw_alpha,
        "alpha_used": max(raw_alpha, 1e-6),
    }


def fit_by_bucket(
    rows: Sequence[Mapping[str, Any]],
    fitter,
    *,
    min_bucket_n: int = MIN_BUCKET_N,
) -> dict[str, dict[str, Any]]:
    """Apply `fitter` (fit_normal or fit_negative_binomial_alpha) per
    projection bucket, falling back to a pooled fit for any bucket with
    fewer than `min_bucket_n` real rows rather than reporting a fit from too
    little data as if it were trustworthy."""
    pooled = fitter(rows)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("b0") is None:
            continue
        grouped[projection_bucket(float(row["b0"]))].append(row)

    result: dict[str, dict[str, Any]] = {}
    for label, _lo, _hi in PROJECTION_BUCKETS:
        bucket_rows = grouped.get(label, [])
        if len(bucket_rows) >= min_bucket_n:
            fit = fitter(bucket_rows)
            fit["used_pooled_fallback"] = False
        else:
            fit = dict(pooled)
            fit["used_pooled_fallback"] = True
            fit["bucket_n"] = len(bucket_rows)
        result[label] = fit
    result["_pooled"] = pooled
    return result


def evaluate_candidate_distributions(
    train_rows: Sequence[Mapping[str, Any]],
    held_rows: Sequence[Mapping[str, Any]],
    *,
    min_bucket_n: int = MIN_BUCKET_N,
) -> dict[str, Any]:
    """Fit all three candidates on `train_rows` and score them on `held_rows`.

    Every fitted parameter comes only from `train_rows`; `held_rows` are
    scored but never used to fit anything -- a real out-of-sample
    comparison, not a resubstitution one. Rows must already carry non-None
    `b0` (i.e. already passed through `rolling_predictions` and been
    filtered to rows with sufficient prior history).

    Returns per-candidate mean/total held-out log-likelihood, the mean
    predicted P(actual=0) against the real observed zero rate, and a Brier
    score for the natural "at least 1 reception" (over 0.5) line -- the
    historical-accuracy evaluation this task requires kept strictly
    separate from any price-aware analysis (no odds appear anywhere in this
    function).
    """
    train_rows = [row for row in train_rows if row.get("b0") is not None]
    held_rows = [row for row in held_rows if row.get("b0") is not None]
    if not train_rows:
        raise ReceptionsOutcomeDistributionError("train_rows must be non-empty after filtering")
    if not held_rows:
        raise ReceptionsOutcomeDistributionError("held_rows must be non-empty after filtering")

    normal_pooled = fit_normal(train_rows)
    normal_by_bucket = fit_by_bucket(train_rows, fit_normal, min_bucket_n=min_bucket_n)
    nb_pooled = fit_negative_binomial_alpha(train_rows)
    nb_by_bucket = fit_by_bucket(train_rows, fit_negative_binomial_alpha, min_bucket_n=min_bucket_n)
    empirical_pool = EmpiricalResidualPool(
        float(row["actual"]) - float(row["b0"]) for row in train_rows
    )

    candidates = ("NORMAL_POOLED", "NORMAL_BUCKETED", "NEGATIVE_BINOMIAL_POOLED",
                  "NEGATIVE_BINOMIAL_BUCKETED", "EMPIRICAL_RESIDUAL_POOLED")
    log_likelihood_totals = {name: 0.0 for name in candidates}
    predicted_p_zero_totals = {name: 0.0 for name in candidates}
    brier_over_half_totals = {name: 0.0 for name in candidates}
    actual_zero_count = 0
    n = len(held_rows)

    for row in held_rows:
        projection = float(row["b0"])
        k = int(round(float(row["actual"])))
        if k == 0:
            actual_zero_count += 1
        bucket = projection_bucket(projection)

        normal_bucket_params = normal_by_bucket[bucket]
        nb_bucket_params = nb_by_bucket[bucket]

        pmf_values = {
            "NORMAL_POOLED": normal_discrete_pmf(k, projection + normal_pooled["mean"], normal_pooled["std"]),
            "NORMAL_BUCKETED": normal_discrete_pmf(
                k, projection + normal_bucket_params["mean"], normal_bucket_params["std"]
            ),
            "NEGATIVE_BINOMIAL_POOLED": negative_binomial_pmf(k, projection, nb_pooled["alpha_used"]),
            "NEGATIVE_BINOMIAL_BUCKETED": negative_binomial_pmf(
                k, projection, nb_bucket_params["alpha_used"]
            ),
            "EMPIRICAL_RESIDUAL_POOLED": empirical_pool.pmf(k, projection),
        }
        p_zero_values = {
            "NORMAL_POOLED": normal_discrete_pmf(0, projection + normal_pooled["mean"], normal_pooled["std"]),
            "NORMAL_BUCKETED": normal_discrete_pmf(
                0, projection + normal_bucket_params["mean"], normal_bucket_params["std"]
            ),
            "NEGATIVE_BINOMIAL_POOLED": negative_binomial_pmf(0, projection, nb_pooled["alpha_used"]),
            "NEGATIVE_BINOMIAL_BUCKETED": negative_binomial_pmf(
                0, projection, nb_bucket_params["alpha_used"]
            ),
            "EMPIRICAL_RESIDUAL_POOLED": empirical_pool.pmf(0, projection),
        }
        actual_over_half = 1.0 if k >= 1 else 0.0
        for name in candidates:
            log_likelihood_totals[name] += math.log(pmf_values[name])
            predicted_p_zero_totals[name] += p_zero_values[name]
            brier_over_half_totals[name] += (1.0 - p_zero_values[name] - actual_over_half) ** 2

    return {
        "schema_version": 1,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "train_n": len(train_rows),
        "held_n": n,
        "empirical_zero_rate_held": actual_zero_count / n,
        "fits": {
            "normal_pooled": normal_pooled,
            "normal_by_bucket": normal_by_bucket,
            "negative_binomial_pooled": nb_pooled,
            "negative_binomial_by_bucket": nb_by_bucket,
        },
        "held_out_evaluation": {
            name: {
                "mean_log_likelihood": log_likelihood_totals[name] / n,
                "total_log_likelihood": log_likelihood_totals[name],
                "mean_predicted_p_zero": predicted_p_zero_totals[name] / n,
                "brier_score_over_0_5": brier_over_half_totals[name] / n,
            }
            for name in candidates
        },
    }


def zero_mass_calibration_by_bucket(
    train_rows: Sequence[Mapping[str, Any]],
    held_rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per-opportunity-bucket real zero-rate vs. each candidate's predicted
    P(actual=0), evaluated strictly out-of-sample. Surfaces the real,
    disclosed miscalibration at the population extremes (see module
    docstring) rather than only reporting a single pooled-average number
    that would hide it.
    """
    train_rows = [row for row in train_rows if row.get("b0") is not None]
    held_rows = [row for row in held_rows if row.get("b0") is not None]
    normal_pooled = fit_normal(train_rows)
    nb_pooled = fit_negative_binomial_alpha(train_rows)
    empirical_pool = EmpiricalResidualPool(
        float(row["actual"]) - float(row["b0"]) for row in train_rows
    )

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in held_rows:
        grouped[projection_bucket(float(row["b0"]))].append(row)

    result: dict[str, dict[str, Any]] = {}
    for label, _lo, _hi in PROJECTION_BUCKETS:
        rows = grouped.get(label, [])
        if not rows:
            continue
        actual_p_zero = sum(1 for row in rows if int(round(float(row["actual"]))) == 0) / len(rows)
        avg_projection = statistics.fmean(float(row["b0"]) for row in rows)
        result[label] = {
            "n": len(rows),
            "avg_projection": avg_projection,
            "actual_p_zero": actual_p_zero,
            "normal_pooled_predicted_p_zero": statistics.fmean(
                normal_discrete_pmf(0, float(row["b0"]) + normal_pooled["mean"], normal_pooled["std"])
                for row in rows
            ),
            "negative_binomial_pooled_predicted_p_zero": statistics.fmean(
                negative_binomial_pmf(0, float(row["b0"]), nb_pooled["alpha_used"]) for row in rows
            ),
            "empirical_residual_pooled_predicted_p_zero": statistics.fmean(
                empirical_pool.pmf(0, float(row["b0"])) for row in rows
            ),
        }
    return result


def normal_calibration_check(rows: Sequence[Mapping[str, Any]], *, mean: float, std: float) -> dict[str, float]:
    """Lightweight QQ-style normality check on real standardized residuals.

    Reports the fraction of standardized residuals within +/-1 and +/-2
    pooled standard deviations (a true Normal gives ~68.3%/~95.4%) plus the
    Fisher-Pearson skewness coefficient. Deliberately dependency-free
    (no scipy) to match this repo's existing stdlib-only research modules.
    """
    residuals = [float(row["actual"]) - float(row["b0"]) for row in rows if row.get("b0") is not None]
    if len(residuals) < 2:
        raise ReceptionsOutcomeDistributionError("need at least 2 rows for a calibration check")
    std = max(std, EPS)
    standardized = [(value - mean) / std for value in residuals]
    n = len(standardized)
    within_1sd = sum(1 for z in standardized if abs(z) <= 1.0) / n
    within_2sd = sum(1 for z in standardized if abs(z) <= 2.0) / n
    third_moment = statistics.fmean(z ** 3 for z in standardized)
    return {
        "n": n,
        "fraction_within_1sd": within_1sd,
        "fraction_within_2sd": within_2sd,
        "expected_fraction_within_1sd_if_normal": 0.6827,
        "expected_fraction_within_2sd_if_normal": 0.9545,
        "skewness": third_moment,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit_manifest.read_text(encoding="utf-8"))
    rows, _invariants, _coverage = load_receiver_rows(args.cache, audit)
    scored = [row for row in rolling_predictions(rows) if row.get("b0") is not None]
    train_rows = [row for row in scored if row["season"] <= 2022]
    held_rows = [row for row in scored if 2023 <= row["season"] <= 2025]

    evaluation = evaluate_candidate_distributions(train_rows, held_rows)
    calibration_by_bucket = zero_mass_calibration_by_bucket(train_rows, held_rows)
    normal_pooled = evaluation["fits"]["normal_pooled"]
    normality_check = normal_calibration_check(
        held_rows, mean=normal_pooled["mean"], std=normal_pooled["std"]
    )

    output = {
        "schema_version": 1,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "source_audit_manifest_sha256": sha256_file(args.audit_manifest),
        "reused_projection_model": "receptions_baseline_research.rolling_predictions (B0)",
        "train_partition": "season <= 2022",
        "held_partition": "2023 <= season <= 2025",
        "evaluation": evaluation,
        "zero_mass_calibration_by_bucket": calibration_by_bucket,
        "normal_qq_style_check_on_held_out": normality_check,
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evaluation["held_out_evaluation"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
