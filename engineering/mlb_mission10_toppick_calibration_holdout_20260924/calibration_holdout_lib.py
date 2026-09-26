"""Reusable, dependency-light statistics for the Mission 10 Top Pick
calibration holdout replication (see DESIGN.md in this directory).

Deliberately avoids ``scipy`` (not a pinned project dependency) -- the exact
binomial test is computed directly from the binomial PMF using
``math.comb``, which is exact for the n involved here (tens to low
hundreds) and matches the methodology PR #128 used.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence


def parse_utc(ts: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp (accepts a trailing 'Z')."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def exact_binomial_two_sided_pvalue(observed_hits: int, n: int, p: float) -> float:
    """Exact two-sided binomial test p-value: P(X in {k : P(X=k) <= P(X=obs)}).

    This is the standard exact-binomial-test definition (matches R's
    ``binom.test`` and ``scipy.stats.binomtest`` for the two-sided case) and
    is computed directly from the PMF so no external dependency is needed.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    if not 0 <= observed_hits <= n:
        raise ValueError("observed_hits must be in [0, n]")

    def pmf(k: int) -> float:
        if p == 0.0:
            return 1.0 if k == 0 else 0.0
        if p == 1.0:
            return 1.0 if k == n else 0.0
        return math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))

    observed_pmf = pmf(observed_hits)
    # Guard against floating point noise: a k with pmf indistinguishable
    # from the observed pmf must be included (this matters most exactly at
    # k == observed_hits itself).
    epsilon = observed_pmf * 1e-7
    total = 0.0
    for k in range(0, n + 1):
        pk = pmf(k)
        if pk <= observed_pmf + epsilon:
            total += pk
    return min(1.0, total)


@dataclass(frozen=True)
class Row:
    candidate_id: str
    slate_date: str
    game_pk: str
    player_id: str
    stat: str
    hit_probability: float
    grade: str  # "hit" or "miss"
    published_top_pick_at: str
    game_start: str

    @property
    def is_hit(self) -> bool:
        return self.grade == "hit"

    @property
    def cluster_key(self) -> tuple:
        return (self.slate_date, self.game_pk)


def assert_pregame_integrity(rows: Sequence[Row]) -> None:
    """Every row's publication timestamp must strictly precede its own
    game_start. Raises AssertionError (fails loudly) on any violation."""
    violations = []
    for r in rows:
        try:
            pub = parse_utc(r.published_top_pick_at)
            start = parse_utc(r.game_start)
        except Exception as exc:  # pragma: no cover - defensive
            violations.append((r.candidate_id, f"unparseable timestamp: {exc}"))
            continue
        if not (pub < start):
            violations.append((r.candidate_id, f"{pub.isoformat()} !< {start.isoformat()}"))
    if violations:
        raise AssertionError(
            "Pregame integrity check FAILED for "
            f"{len(violations)} row(s): {violations[:5]}"
            + (" ... (truncated)" if len(violations) > 5 else "")
        )


@dataclass(frozen=True)
class GapSummary:
    n: int
    hits: int
    mean_predicted: float
    realized_rate: float
    gap: float


def summarize(rows: Sequence[Row]) -> GapSummary:
    n = len(rows)
    if n == 0:
        return GapSummary(n=0, hits=0, mean_predicted=float("nan"), realized_rate=float("nan"), gap=float("nan"))
    hits = sum(1 for r in rows if r.is_hit)
    mean_predicted = sum(r.hit_probability for r in rows) / n
    realized_rate = hits / n
    return GapSummary(n=n, hits=hits, mean_predicted=mean_predicted, realized_rate=realized_rate, gap=mean_predicted - realized_rate)


def cluster_bootstrap_gap_ci(
    rows: Sequence[Row],
    n_resamples: int = 10000,
    seed: int = 20260924,
    ci: float = 0.90,
) -> tuple[float, float]:
    """Cluster bootstrap CI for `gap`, resampling clusters (slate_date,
    game_pk) with replacement. Each resample draws len(clusters) clusters
    (with replacement) and recomputes the gap over all rows belonging to the
    drawn clusters (a cluster drawn twice contributes its rows twice).

    Returns (lower, upper) at the requested two-sided CI level (default 90%:
    5th/95th percentile).
    """
    if not rows:
        raise ValueError("rows must be non-empty")
    clusters: dict[tuple, list[Row]] = {}
    for r in rows:
        clusters.setdefault(r.cluster_key, []).append(r)
    cluster_keys = list(clusters.keys())
    m = len(cluster_keys)

    rng = random.Random(seed)
    gaps = []
    for _ in range(n_resamples):
        resampled_rows: list[Row] = []
        for _ in range(m):
            key = cluster_keys[rng.randrange(m)]
            resampled_rows.extend(clusters[key])
        gaps.append(summarize(resampled_rows).gap)

    gaps.sort()
    alpha = (1.0 - ci) / 2.0
    lo_idx = max(0, int(math.floor(alpha * n_resamples)))
    hi_idx = min(n_resamples - 1, int(math.ceil((1.0 - alpha) * n_resamples)) - 1)
    return gaps[lo_idx], gaps[hi_idx]


def load_public_top_picks(
    grades_files: Mapping[str, dict],
    excluded_ids: Iterable[str] = (),
) -> list[Row]:
    """Extract fair, settled public Top Pick rows from a mapping of
    {filepath: parsed_json} results/grades_*.json documents.

    Only rows with grade in {"hit", "miss"} are returned. `excluded_ids` are
    dropped (used for the one pre-registration-integrity exclusion; see
    DESIGN.md).
    """
    excluded = set(excluded_ids)
    out: list[Row] = []
    for path, doc in grades_files.items():
        slate_date = doc.get("date")
        for rec in doc.get("public_top_picks") or []:
            if rec.get("grade") not in ("hit", "miss"):
                continue
            cid = rec.get("id")
            if cid in excluded:
                continue
            hp = rec.get("hit_probability")
            if hp is None:
                continue
            out.append(
                Row(
                    candidate_id=cid,
                    slate_date=rec.get("slate_date") or slate_date,
                    game_pk=str(rec.get("game_pk")),
                    player_id=str(rec.get("player_id")),
                    stat=rec.get("stat"),
                    hit_probability=float(hp),
                    grade=rec.get("grade"),
                    published_top_pick_at=rec.get("published_top_pick_at"),
                    game_start=rec.get("game_start"),
                )
            )
    return out
