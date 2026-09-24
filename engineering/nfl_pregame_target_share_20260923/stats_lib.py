#!/usr/bin/env python3
"""Dependency-free statistics used by the locked holdout evaluation."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Callable, Sequence

POISSON_LINES = (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        raise ValueError("Poisson mean must be positive")
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def poisson_prob_over(line: float, lam: float) -> float:
    """P(Y > line) for a half-point line."""
    k_max = int(math.floor(line))
    return 1.0 - sum(poisson_pmf(k, lam) for k in range(k_max + 1))


def poisson_brier(lam: float, realized: float, lines: Sequence[float] = POISSON_LINES) -> float:
    return sum((poisson_prob_over(line, lam) - (1.0 if realized > line else 0.0)) ** 2 for line in lines) / len(lines)


def poisson_log_score(lam: float, realized: float) -> float:
    """Negative log-likelihood of the realized integer count."""
    return -math.log(max(poisson_pmf(int(round(realized)), lam), 1e-300))


def player_clustered_diff_ci(
    rows: Sequence[dict],
    loss_a: Callable[[dict], float],
    loss_b: Callable[[dict], float],
    *,
    cluster_key: str = "player_id",
    n_resamples: int = 2000,
    seed: int = 20260924,
) -> dict:
    """Mean(loss_a) - mean(loss_b) on paired rows, with a 95% percentile
    interval from resampling whole clusters (players) with replacement."""
    by_cluster: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        by_cluster[r[cluster_key]].append((loss_a(r), loss_b(r)))
    clusters = list(by_cluster)
    if len(clusters) < 2:
        raise ValueError("need at least two clusters for a clustered interval")
    point = sum(a - b for pairs in by_cluster.values() for a, b in pairs) / sum(
        len(p) for p in by_cluster.values()
    )
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_resamples):
        total, count = 0.0, 0
        for _ in clusters:
            for a, b in by_cluster[rng.choice(clusters)]:
                total += a - b
                count += 1
        diffs.append(total / count)
    diffs.sort()
    return {
        "point": point,
        "ci95": [diffs[int(0.025 * n_resamples)], diffs[int(0.975 * n_resamples) - 1]],
        "n_rows": sum(len(p) for p in by_cluster.values()),
        "n_clusters": len(clusters),
        "n_resamples": n_resamples,
        "seed": seed,
    }
