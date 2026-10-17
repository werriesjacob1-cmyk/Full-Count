"""Discrete NFL home-margin / game-total distributions for spread and total pricing.

One parameter set (center, sigma, key-number weights) produces every derived
quantity, so a fair spread, a cover probability and a moneyline probability for
the same game cannot contradict each other.

Shape: a discretised Normal(center, sigma) over integers, multiplied by
per-integer key-number weights fitted on historical development data (FC-NFL-004
PREREG). The weights carry football's lumpy scoring (margins of 3 and 7, totals
of 37/41/44/...), which a plain Normal misses and which matters for push
probability at integer lines.

Known limitation: margin and total are separate marginals, not a joint score
distribution. Same-game correlation (spread x total, player props) is NOT
supported; a joint model is the extension point for that.

Research only. Nothing here selects, publishes, or prices a public pick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

MARGIN_SUPPORT = tuple(range(-70, 71))
TOTAL_SUPPORT = tuple(range(0, 131))
WEIGHT_CLIP = (0.2, 5.0)
FIT_ITERATIONS = 2


class GameDistributionError(ValueError):
    """Raised when a distribution request is ill-posed."""


def _finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameDistributionError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GameDistributionError(f"{field} must be finite")
    return result


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def discrete_pmf(
    center: float,
    sigma: float,
    support: Sequence[int],
    weights: Mapping[int, float] | None = None,
) -> dict[int, float]:
    """Discretised Normal times key-number weights, renormalised on ``support``."""
    c = _finite(center, "center")
    s = _finite(sigma, "sigma")
    if s <= 0:
        raise GameDistributionError("sigma must be positive")
    raw: dict[int, float] = {}
    for k in support:
        mass = _phi((k + 0.5 - c) / s) - _phi((k - 0.5 - c) / s)
        raw[k] = mass * (weights.get(k, 1.0) if weights else 1.0)
    norm = sum(raw.values())
    if norm <= 0:
        raise GameDistributionError("distribution has no mass on support")
    return {k: v / norm for k, v in raw.items()}


def fit_key_weights(
    pairs: Iterable[tuple[float, int]],
    sigma: float,
    support: Sequence[int],
    *,
    iterations: int = FIT_ITERATIONS,
    clip: tuple[float, float] = WEIGHT_CLIP,
) -> dict[int, float]:
    """Multiplicative fixed-point fit of key weights: w *= (obs+1)/(exp+1)."""
    data = [(float(c), int(a)) for c, a in pairs]
    if not data:
        raise GameDistributionError("no data to fit key weights")
    lo, hi = clip
    observed = {k: 0 for k in support}
    for _, actual in data:
        if actual in observed:
            observed[actual] += 1
    weights = {k: 1.0 for k in support}
    for _ in range(iterations):
        expected = {k: 0.0 for k in support}
        for center, _ in data:
            for k, p in discrete_pmf(center, sigma, support, weights).items():
                expected[k] += p
        weights = {
            k: min(hi, max(lo, weights[k] * (observed[k] + 1.0) / (expected[k] + 1.0)))
            for k in support
        }
    return weights


def _outcome(pmf: Mapping[int, float], win, push) -> tuple[float, float, float]:
    w = sum(p for k, p in pmf.items() if win(k))
    u = sum(p for k, p in pmf.items() if push(k))
    return w, u, max(0.0, 1.0 - w - u)


@dataclass(frozen=True)
class GameDistribution:
    """Margin (home minus away) and total pmfs for one game under one config."""

    margin_pmf: Mapping[int, float] | None
    total_pmf: Mapping[int, float] | None

    @classmethod
    def build(
        cls,
        *,
        margin_center: float | None,
        total_center: float | None,
        margin_sigma: float,
        total_sigma: float,
        margin_weights: Mapping[int, float] | None = None,
        total_weights: Mapping[int, float] | None = None,
    ) -> "GameDistribution":
        """Either marginal may be absent (``None``); asking for it then raises."""
        if margin_center is None and total_center is None:
            raise GameDistributionError("at least one marginal is required")
        if total_center is not None and _finite(total_center, "total_center") <= 0:
            raise GameDistributionError("total_center must be positive")
        return cls(
            None if margin_center is None
            else discrete_pmf(margin_center, margin_sigma, MARGIN_SUPPORT, margin_weights),
            None if total_center is None
            else discrete_pmf(total_center, total_sigma, TOTAL_SUPPORT, total_weights),
        )

    def _margin(self) -> Mapping[int, float]:
        if self.margin_pmf is None:
            raise GameDistributionError("margin distribution unavailable")
        return self.margin_pmf

    def _total(self) -> Mapping[int, float]:
        if self.total_pmf is None:
            raise GameDistributionError("total distribution unavailable")
        return self.total_pmf

    # ---- point summaries -------------------------------------------------
    @property
    def expected_margin(self) -> float:
        return sum(k * p for k, p in self._margin().items())

    @property
    def expected_total(self) -> float:
        return sum(k * p for k, p in self._total().items())

    # ---- market outcomes: (win, push, lose) for the named side -------------
    def spread(self, side: str, line: float) -> tuple[float, float, float]:
        """``line`` is the selected side's own handicap (e.g. HOME -3.5)."""
        h = _finite(line, "line")
        side = str(side).upper()
        if side == "HOME":
            return _outcome(self._margin(), lambda m: m + h > 0, lambda m: m + h == 0)
        if side == "AWAY":
            return _outcome(self._margin(), lambda m: -m + h > 0, lambda m: -m + h == 0)
        raise GameDistributionError(f"unsupported spread side: {side}")

    def total(self, side: str, line: float) -> tuple[float, float, float]:
        t = _finite(line, "line")
        side = str(side).upper()
        if side == "OVER":
            return _outcome(self._total(), lambda x: x > t, lambda x: x == t)
        if side == "UNDER":
            return _outcome(self._total(), lambda x: x < t, lambda x: x == t)
        raise GameDistributionError(f"unsupported total side: {side}")

    def moneyline(self, side: str) -> tuple[float, float, float]:
        """(win, tie, lose). A tie is graded UNRESOLVED_TIE, priced as a push."""
        side = str(side).upper()
        if side == "HOME":
            return _outcome(self._margin(), lambda m: m > 0, lambda m: m == 0)
        if side == "AWAY":
            return _outcome(self._margin(), lambda m: m < 0, lambda m: m == 0)
        raise GameDistributionError(f"unsupported moneyline side: {side}")

    # ---- fair values -----------------------------------------------------
    def fair_home_spread(self) -> float:
        """Home handicap on a half-point grid whose two sides are closest to 50/50."""
        return _fair_line(lambda h: self.spread("HOME", h), lo=-40.0, hi=40.0)

    def fair_total(self) -> float:
        return _fair_line(lambda t: self.total("OVER", t), lo=20.0, hi=80.0)

    def fair_home_win_probability(self) -> float:
        win, _, lose = self.moneyline("HOME")
        return win / (win + lose)


def conditional_win(outcome: tuple[float, float, float]) -> float:
    """Win probability conditional on the wager not pushing."""
    win, _, lose = outcome
    if win + lose <= 0:
        raise GameDistributionError("no decisive mass")
    return win / (win + lose)


def solve_center(
    kind: str,
    line: float,
    sigma: float,
    weights: Mapping[int, float] | None,
    *,
    target: float = 0.5,
) -> float:
    """Center whose distribution makes ``line`` a ``target`` proposition.

    ``kind`` is "margin" (``line`` = market expected home margin, i.e. minus the
    home handicap; target = P(home covers | no push)) or "total" (target =
    P(over | no push)). With key-number weights the Normal center is NOT the
    50/50 point of the line, so a market-prior distribution must be solved
    for, not assumed (FC-NFL-004 deviation D1).
    """
    value = _finite(line, "line")
    if not 0.0 < target < 1.0:
        raise GameDistributionError("target must be in (0, 1)")
    support = MARGIN_SUPPORT if kind == "margin" else TOTAL_SUPPORT if kind == "total" else None
    if support is None:
        raise GameDistributionError(f"unsupported kind: {kind}")

    def prob(center: float) -> float:
        pmf = discrete_pmf(center, sigma, support, weights)
        if kind == "margin":
            outcome = _outcome(pmf, lambda m: m - value > 0, lambda m: m - value == 0)
        else:
            outcome = _outcome(pmf, lambda x: x > value, lambda x: x == value)
        return conditional_win(outcome)

    lo, hi = value - 12.0, value + 12.0
    if not prob(lo) <= target <= prob(hi):
        raise GameDistributionError("target not bracketed")
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if prob(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _fair_line(fn, *, lo: float, hi: float) -> float:
    best, best_gap = None, math.inf
    steps = int(round((hi - lo) * 2))
    for i in range(steps + 1):
        line = lo + i * 0.5
        gap = abs(conditional_win(fn(line)) - 0.5)
        if gap < best_gap - 1e-12:
            best, best_gap = line, gap
    return best
