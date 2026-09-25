"""calibration_lib.py -- reusable, dependency-free analysis functions for the
MLB full-board calibration / winner's-curse investigation.

These functions are deliberately small and generic so they can be unit
tested in isolation (see test_calibration_lib.py) and reused by
analyze_fullboard_calibration.py without re-deriving bucketing or
cluster-resampling logic inline.

Nothing here reads or writes any repository file. It operates purely on
in-memory lists of dicts that already carry ``predicted`` (float in [0, 1]),
``hit`` (0/1 int), and ``cluster`` (hashable, e.g. game_pk) keys, so it has
no dependency on the board_freeze schema itself -- that mapping lives in
analyze_fullboard_calibration.py.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Callable, Dict, Hashable, List, Sequence, Tuple, TypeVar

T = TypeVar("T")


def make_quantile_buckets(records: Sequence[dict], n_buckets: int) -> List[List[dict]]:
    """Split ``records`` (each must carry a numeric ``predicted`` field) into
    up to ``n_buckets`` roughly-equal-sized groups ordered by ``predicted``,
    ascending. Ties on the bucket boundary stay with the lower bucket.

    Returns fewer than ``n_buckets`` groups if there are not enough distinct
    records to fill them (e.g. n_buckets requested > len(records)); never
    raises for a small input, since honest small-sample reporting is a
    requirement of the calling analysis, not an error condition.
    """
    if n_buckets < 1:
        raise ValueError("n_buckets must be >= 1")
    ordered = sorted(records, key=lambda r: r["predicted"])
    n = len(ordered)
    if n == 0:
        return []
    n_buckets = min(n_buckets, n)
    # Contiguous near-equal split: first (n % n_buckets) buckets get one
    # extra element. This keeps every real record in exactly one bucket
    # and never invents or drops one.
    base, extra = divmod(n, n_buckets)
    buckets: List[List[dict]] = []
    idx = 0
    for b in range(n_buckets):
        size = base + (1 if b < extra else 0)
        buckets.append(ordered[idx: idx + size])
        idx += size
    return [b for b in buckets if b]


def summarize_bucket(records: Sequence[dict]) -> Dict[str, object]:
    """Real summary statistics for one bucket: n, mean predicted probability,
    realized hit rate, and the distinct clusters (games) represented, so a
    caller can see whether a bucket's evidence is spread across independent
    games or concentrated in one."""
    n = len(records)
    mean_predicted = statistics.fmean(r["predicted"] for r in records) if n else None
    realized_hit_rate = statistics.fmean(r["hit"] for r in records) if n else None
    clusters = sorted({r["cluster"] for r in records}, key=str)
    return {
        "n": n,
        "n_games": len(clusters),
        "mean_predicted": mean_predicted,
        "realized_hit_rate": realized_hit_rate,
        "calibration_gap": (
            None
            if mean_predicted is None or realized_hit_rate is None
            else realized_hit_rate - mean_predicted
        ),
        "predicted_range": (
            None if n == 0 else [min(r["predicted"] for r in records), max(r["predicted"] for r in records)]
        ),
    }


def cluster_bootstrap_ci(
    records: Sequence[dict],
    statistic_fn: Callable[[Sequence[dict]], float],
    *,
    n_boot: int = 2000,
    seed: int = 20260923,
    alpha: float = 0.05,
) -> Dict[str, object]:
    """A cluster (block) bootstrap confidence interval for ``statistic_fn``
    applied to ``records``, resampling whole clusters (e.g. games) with
    replacement rather than individual rows.

    This is the honest way to account for candidates in the same game not
    being independent observations (multiple players/props from one game
    share that game's real outcome-generating context) without pulling in
    a statistics dependency. Returns None values when there are fewer than
    2 distinct clusters, since a bootstrap over 0-1 clusters is not
    meaningful and must not silently manufacture a false-precision interval.
    """
    clusters: Dict[Hashable, List[dict]] = {}
    for r in records:
        clusters.setdefault(r["cluster"], []).append(r)
    cluster_ids = list(clusters.keys())
    point_estimate = statistic_fn(records) if records else None

    if len(cluster_ids) < 2 or not records:
        return {
            "point_estimate": point_estimate,
            "ci_low": None,
            "ci_high": None,
            "n_boot": 0,
            "n_clusters": len(cluster_ids),
            "note": "fewer than 2 distinct clusters (games) -- a cluster bootstrap CI is not meaningful here",
        }

    rng = random.Random(seed)
    draws: List[float] = []
    for _ in range(n_boot):
        resampled_ids = [rng.choice(cluster_ids) for _ in cluster_ids]
        resampled_records: List[dict] = []
        for cid in resampled_ids:
            resampled_records.extend(clusters[cid])
        try:
            draws.append(statistic_fn(resampled_records))
        except (ZeroDivisionError, statistics.StatisticsError):
            continue

    if not draws:
        return {
            "point_estimate": point_estimate,
            "ci_low": None,
            "ci_high": None,
            "n_boot": 0,
            "n_clusters": len(cluster_ids),
            "note": "no bootstrap draw produced a defined statistic",
        }

    draws.sort()
    lo_idx = max(0, int(math.floor((alpha / 2) * len(draws))))
    hi_idx = min(len(draws) - 1, int(math.ceil((1 - alpha / 2) * len(draws))) - 1)
    return {
        "point_estimate": point_estimate,
        "ci_low": draws[lo_idx],
        "ci_high": draws[hi_idx],
        "n_boot": len(draws),
        "n_clusters": len(cluster_ids),
        "note": None,
    }


def mean_hit_rate(records: Sequence[dict]) -> float:
    """Statistic helper: realized hit rate over a set of records."""
    return statistics.fmean(r["hit"] for r in records)


def mean_predicted(records: Sequence[dict]) -> float:
    """Statistic helper: mean predicted probability over a set of records."""
    return statistics.fmean(r["predicted"] for r in records)


def calibration_gap_statistic(records: Sequence[dict]) -> float:
    """Statistic helper: realized hit rate minus mean predicted probability
    (the winner's-curse / calibration-gap quantity), for use as the
    ``statistic_fn`` passed to ``cluster_bootstrap_ci``."""
    return mean_hit_rate(records) - mean_predicted(records)


def group_gap_difference_statistic(
    group_a: Sequence[dict], group_b: Sequence[dict]
) -> float:
    """(realized - predicted) gap for group_a minus that same gap for
    group_b -- the quantity that must be significantly different from zero
    for a real, measurable winner's-curse differential to exist between the
    two groups (e.g. selected Top Picks vs. everything else)."""
    return calibration_gap_statistic(group_a) - calibration_gap_statistic(group_b)


def cluster_bootstrap_group_gap_diff_ci(
    records: Sequence[dict],
    group_key: str,
    group_a_value: object,
    group_b_value: object,
    *,
    n_boot: int = 2000,
    seed: int = 20260923,
    alpha: float = 0.05,
) -> Dict[str, object]:
    """Cluster (by ``cluster``, e.g. game_pk) bootstrap CI for the
    calibration-gap difference between two groups drawn from the SAME
    population of records (e.g. selected Top Pick vs. everything else in the
    same real graded boards). Resampling is done once per game across the
    whole population -- not independently per group -- because both groups'
    records from the same game share that game's real outcome-generating
    context; a naive per-group bootstrap would understate shared dependence.

    Returns None bounds (with an explicit note) when either group has fewer
    than 2 distinct clusters in the real data, or when the point estimate
    itself cannot be computed because one group is empty -- an honest
    reflection of an inconclusive real sample, never a fabricated interval.
    """
    clusters: Dict[Hashable, List[dict]] = {}
    for r in records:
        clusters.setdefault(r["cluster"], []).append(r)
    cluster_ids = list(clusters.keys())

    def _split(recs: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
        a = [r for r in recs if r[group_key] == group_a_value]
        b = [r for r in recs if r[group_key] == group_b_value]
        return a, b

    real_a, real_b = _split(records)
    n_clusters_a = len({r["cluster"] for r in real_a})
    n_clusters_b = len({r["cluster"] for r in real_b})

    if not real_a or not real_b:
        return {
            "point_estimate": None,
            "ci_low": None,
            "ci_high": None,
            "n_boot": 0,
            "n_group_a": len(real_a),
            "n_group_b": len(real_b),
            "n_clusters_group_a": n_clusters_a,
            "n_clusters_group_b": n_clusters_b,
            "note": "one of the two groups has zero real records in this population -- gap difference is undefined",
        }

    point_estimate = group_gap_difference_statistic(real_a, real_b)

    if n_clusters_a < 2 or n_clusters_b < 2 or len(cluster_ids) < 2:
        return {
            "point_estimate": point_estimate,
            "ci_low": None,
            "ci_high": None,
            "n_boot": 0,
            "n_group_a": len(real_a),
            "n_group_b": len(real_b),
            "n_clusters_group_a": n_clusters_a,
            "n_clusters_group_b": n_clusters_b,
            "note": (
                "fewer than 2 distinct games behind at least one group -- a cluster bootstrap CI "
                "would be false precision; point estimate is reported, interval is not"
            ),
        }

    rng = random.Random(seed)
    draws: List[float] = []
    for _ in range(n_boot):
        resampled_ids = [rng.choice(cluster_ids) for _ in cluster_ids]
        resampled: List[dict] = []
        for cid in resampled_ids:
            resampled.extend(clusters[cid])
        a, b = _split(resampled)
        if not a or not b:
            continue
        try:
            draws.append(group_gap_difference_statistic(a, b))
        except (ZeroDivisionError, statistics.StatisticsError):
            continue

    if not draws:
        return {
            "point_estimate": point_estimate,
            "ci_low": None,
            "ci_high": None,
            "n_boot": 0,
            "n_group_a": len(real_a),
            "n_group_b": len(real_b),
            "n_clusters_group_a": n_clusters_a,
            "n_clusters_group_b": n_clusters_b,
            "note": "no bootstrap resample retained both groups -- interval undefined at this cluster count",
        }

    draws.sort()
    lo_idx = max(0, int(math.floor((alpha / 2) * len(draws))))
    hi_idx = min(len(draws) - 1, int(math.ceil((1 - alpha / 2) * len(draws))) - 1)
    return {
        "point_estimate": point_estimate,
        "ci_low": draws[lo_idx],
        "ci_high": draws[hi_idx],
        "n_boot": len(draws),
        "n_group_a": len(real_a),
        "n_group_b": len(real_b),
        "n_clusters_group_a": n_clusters_a,
        "n_clusters_group_b": n_clusters_b,
        "note": None,
    }
