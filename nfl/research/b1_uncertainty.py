#!/usr/bin/env python3
"""Deterministic clustered uncertainty utilities for NFL research.

These helpers estimate uncertainty in paired challenger-vs-baseline error
deltas while respecting obvious dependence structures (week, game, player).

They are descriptive bootstrap intervals, NOT formal p-values and NOT proof of
independence. The caller chooses the scientifically meaningful cluster.
"""
from __future__ import annotations

from collections import defaultdict
import math
import random
from typing import Iterable, Mapping, Any


def percentile(values: Iterable[float], q: float) -> float:
    """Linear-interpolated percentile on a finite non-empty sample."""
    vals = sorted(float(v) for v in values)
    if not vals:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= q <= 1.0:
        raise ValueError("percentile q must be in [0, 1]")
    if any(not math.isfinite(v) for v in vals):
        raise ValueError("percentile values must be finite")
    if len(vals) == 1:
        return vals[0]

    pos = q * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    weight = pos - lo
    return vals[lo] * (1.0 - weight) + vals[hi] * weight


def cluster_bootstrap_mean_ci(
    rows: Iterable[Mapping[str, Any]],
    *,
    cluster_field: str,
    value_field: str,
    reps: int = 10_000,
    seed: int = 20260911,
    ci: float = 0.95,
) -> dict:
    """Cluster-resample a row-weighted mean.

    Each replicate samples the same number of clusters with replacement,
    includes every row from each sampled cluster, then computes the row-weighted
    mean. This preserves within-cluster dependence better than treating every
    player-game row as independent.

    bootstrap_fraction_below_zero is descriptive and is not a formal p-value.
    """
    if reps <= 0:
        raise ValueError("reps must be positive")
    if not 0.0 < ci < 1.0:
        raise ValueError("ci must be between 0 and 1")

    grouped: dict[Any, list[float]] = defaultdict(list)
    n_rows = 0
    total = 0.0

    for index, row in enumerate(rows):
        if cluster_field not in row:
            raise ValueError(
                f"missing cluster field {cluster_field!r} at row {index}"
            )
        if value_field not in row:
            raise ValueError(
                f"missing value field {value_field!r} at row {index}"
            )
        cluster = row[cluster_field]
        if cluster is None:
            raise ValueError(
                f"missing cluster value for {cluster_field!r} at row {index}"
            )
        try:
            value = float(row[value_field])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"non-numeric value for {value_field!r} at row {index}"
            ) from exc
        if not math.isfinite(value):
            raise ValueError(
                f"non-finite value for {value_field!r} at row {index}"
            )
        grouped[cluster].append(value)
        n_rows += 1
        total += value

    clusters = list(grouped)
    if len(clusters) < 2:
        raise ValueError("cluster bootstrap requires at least two clusters")

    observed_mean = total / n_rows
    rng = random.Random(seed)
    boot_means: list[float] = []

    for _ in range(reps):
        sampled_values: list[float] = []
        for _ in range(len(clusters)):
            sampled_cluster = clusters[rng.randrange(len(clusters))]
            sampled_values.extend(grouped[sampled_cluster])
        boot_means.append(sum(sampled_values) / len(sampled_values))

    alpha = (1.0 - ci) / 2.0
    below_zero = sum(1 for value in boot_means if value < 0.0)

    return {
        "n_rows": n_rows,
        "n_clusters": len(clusters),
        "observed_mean": observed_mean,
        "ci_level": ci,
        "ci_low": percentile(boot_means, alpha),
        "ci_high": percentile(boot_means, 1.0 - alpha),
        "bootstrap_fraction_below_zero": below_zero / reps,
        "reps": reps,
        "seed": seed,
        "cluster_field": cluster_field,
        "value_field": value_field,
        "interpretation": (
            "descriptive clustered bootstrap; fraction below zero is not a "
            "formal p-value"
        ),
    }
