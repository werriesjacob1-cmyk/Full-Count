"""FC-NFL-004 game-market configurations: B0, F1, M0, C1 (see PREREG.md).

- B0: existing strictly-prior scoring blend, unchanged (``game_market_b0``).
- F1: affine recalibration of B0 (adds the home-field intercept B0 lacks and
  fixes its scale). Football-only; no market input.
- M0: market prior. The distribution whose 50/50 point is the market line
  being evaluated (deviation D1: solved, not ``center = line``). Historically
  the archived close (retrospective only); live, the capture-time offer line.
- C1: market-anchored football residual, ``M0 center + k * (F1 - line)``.

All parameters are fit on development seasons 2000-2019 only and frozen to a
JSON artifact whose sha256 is bound into every prospective research record.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from functools import lru_cache

from nfl.research.game_score_distribution import (
    MARGIN_SUPPORT,
    TOTAL_SUPPORT,
    GameDistribution,
    fit_key_weights,
    solve_center,
)

CONFIGS = ("B0", "F1", "M0", "C1")
MARKET_CONFIGS = frozenset({"M0", "C1"})
PARAMS_SCHEMA = "fc-nfl-004-game-market-params-v1"
ORIGINAL_PARAMS_PATH = Path(__file__).with_name("game_market_v1_params.json")  # frozen; bound by 2026-10-12T17:01Z freezes
DEFAULT_PARAMS_PATH = Path(__file__).with_name("game_market_v1_1_params.json")  # D1-consistent amended fit


class GameMarketModelError(ValueError):
    """Raised when a configuration cannot produce a legitimate distribution."""


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        raise GameMarketModelError("degenerate regressor")
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def _origin_slope(xs: list[float], ys: list[float]) -> float:
    denom = sum(x * x for x in xs)
    if denom <= 0:
        raise GameMarketModelError("degenerate regressor")
    return sum(x * y for x, y in zip(xs, ys)) / denom


def _rmse(errors: Iterable[float]) -> float:
    values = list(errors)
    return math.sqrt(statistics.fmean(e * e for e in values))


@dataclass(frozen=True, eq=False)
class GameMarketParams:
    f1_margin: tuple[float, float]
    f1_total: tuple[float, float]
    c1_k_margin: float
    c1_k_total: float
    sigma: Mapping[str, Mapping[str, float]]  # config -> {"margin","total"}
    margin_weights: Mapping[int, float]
    total_weights: Mapping[int, float]
    provenance: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": PARAMS_SCHEMA,
            "f1_margin": {"intercept": self.f1_margin[0], "slope": self.f1_margin[1]},
            "f1_total": {"intercept": self.f1_total[0], "slope": self.f1_total[1]},
            "c1_k_margin": self.c1_k_margin,
            "c1_k_total": self.c1_k_total,
            "sigma": {c: dict(v) for c, v in sorted(self.sigma.items())},
            "margin_weights": {str(k): round(v, 6) for k, v in sorted(self.margin_weights.items())},
            "total_weights": {str(k): round(v, 6) for k, v in sorted(self.total_weights.items())},
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "GameMarketParams":
        if data.get("schema") != PARAMS_SCHEMA:
            raise GameMarketModelError("unexpected params schema")
        return cls(
            f1_margin=(float(data["f1_margin"]["intercept"]), float(data["f1_margin"]["slope"])),
            f1_total=(float(data["f1_total"]["intercept"]), float(data["f1_total"]["slope"])),
            c1_k_margin=float(data["c1_k_margin"]),
            c1_k_total=float(data["c1_k_total"]),
            sigma={c: {t: float(s) for t, s in v.items()} for c, v in data["sigma"].items()},
            margin_weights={int(k): float(v) for k, v in data["margin_weights"].items()},
            total_weights={int(k): float(v) for k, v in data["total_weights"].items()},
            provenance=dict(data.get("provenance") or {}),
        )


def params_sha256(params: GameMarketParams) -> str:
    raw = json.dumps(params.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_params(path: Path = DEFAULT_PARAMS_PATH) -> GameMarketParams:
    return GameMarketParams.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def f1_centers(params: GameMarketParams, b0_margin: float, b0_total: float) -> tuple[float, float]:
    am, bm = params.f1_margin
    at, bt = params.f1_total
    return am + bm * b0_margin, at + bt * b0_total


def centers(
    config: str,
    params: GameMarketParams,
    *,
    b0_margin: float | None,
    b0_total: float | None,
    line_margin: float | None,
    line_total: float | None,
) -> tuple[float | None, float | None]:
    """Return (home-margin center, total center); ``None`` when inputs are absent.

    ``line_margin`` is the market's expected home margin (= -home handicap).
    """
    if config not in CONFIGS:
        raise GameMarketModelError(f"unknown config: {config}")
    has_b0 = b0_margin is not None and b0_total is not None
    if config == "B0":
        return (b0_margin, b0_total) if has_b0 else (None, None)
    m0m = None if line_margin is None else market_center(params, "margin", line_margin)
    m0t = None if line_total is None else market_center(params, "total", line_total)
    if config == "M0":
        return m0m, m0t
    if not has_b0:
        return None, None
    fm, ft = f1_centers(params, b0_margin, b0_total)
    if config == "F1":
        return fm, ft
    cm = None if m0m is None else m0m + params.c1_k_margin * (fm - line_margin)
    ct = None if m0t is None else m0t + params.c1_k_total * (ft - line_total)
    return cm, ct


def market_center(params: GameMarketParams, kind: str, line: float) -> float:
    """M0 center for one market line (cached per params identity and line)."""
    return _market_center_cached(params, kind, float(line))


@lru_cache(maxsize=4096)
def _market_center_cached(params: "GameMarketParams", kind: str, line: float) -> float:
    weights = params.margin_weights if kind == "margin" else params.total_weights
    return solve_center(kind, line, params.sigma["M0"][kind], weights)


def distribution(
    config: str, params: GameMarketParams, margin_center: float | None, total_center: float | None
) -> GameDistribution:
    sig = params.sigma[config]
    return GameDistribution.build(
        margin_center=margin_center,
        total_center=total_center,
        margin_sigma=sig["margin"],
        total_sigma=sig["total"],
        margin_weights=params.margin_weights,
        total_weights=params.total_weights,
    )


FIT_PROCEDURE_ORIGINAL = "v1_line_centered"
FIT_PROCEDURE_D1 = "v1_1_d1_solved_m0"
D1_OUTER_ITERATIONS = 3


def fit_params(
    dev_rows: list[Mapping[str, Any]],
    provenance: Mapping[str, Any],
    *,
    procedure: str = FIT_PROCEDURE_D1,
) -> GameMarketParams:
    """Fit every parameter on development rows only.

    Each row needs b0_margin, b0_total, line_margin, line_total, actual_margin,
    actual_total.

    ``FIT_PROCEDURE_ORIGINAL`` reproduces the pre-registered fit (M0 sigma, key
    weights and C1 k estimated around the raw line). It is kept only so the
    original params artifact, which earlier freezes are bound to, stays
    reproducible.

    ``FIT_PROCEDURE_D1`` (default, FC-NFL-004 amendment) estimates them around
    the SOLVED M0 center used at evaluation time (deviation D1). Because the
    solved center depends on sigma and the key weights, a fixed, pre-declared
    number of outer fixed-point iterations is run:
    centers <- solve(line | sigma, w); sigma <- RMSE(actual - centers);
    w <- fit_key_weights(centers). Then C1: (actual - m0) ~ k (F1 - line).
    """
    if not dev_rows:
        raise GameMarketModelError("no development rows")
    if procedure not in (FIT_PROCEDURE_ORIGINAL, FIT_PROCEDURE_D1):
        raise GameMarketModelError(f"unknown fit procedure: {procedure}")
    col = lambda k: [float(r[k]) for r in dev_rows]  # noqa: E731
    b0m, b0t = col("b0_margin"), col("b0_total")
    lm, lt = col("line_margin"), col("line_total")
    am, at = col("actual_margin"), col("actual_total")
    iam, iat = [int(x) for x in am], [int(x) for x in at]

    f1m = _ols(b0m, am)
    f1t = _ols(b0t, at)
    fm = [f1m[0] + f1m[1] * x for x in b0m]
    ft = [f1t[0] + f1t[1] * x for x in b0t]

    sm = _rmse(a - p for a, p in zip(am, lm))
    st = _rmse(a - p for a, p in zip(at, lt))
    mw = fit_key_weights(zip(lm, iam), sm, MARGIN_SUPPORT)
    tw = fit_key_weights(zip(lt, iat), st, TOTAL_SUPPORT)
    m0m, m0t = list(lm), list(lt)
    trace: list[dict[str, float]] = []
    if procedure == FIT_PROCEDURE_D1:
        for _ in range(D1_OUTER_ITERATIONS):
            cache_m = {l: solve_center("margin", l, sm, mw) for l in set(lm)}
            cache_t = {l: solve_center("total", l, st, tw) for l in set(lt)}
            new_m = [cache_m[l] for l in lm]
            new_t = [cache_t[l] for l in lt]
            deltas = [abs(a - b) for a, b in zip(new_m, m0m)] + [abs(a - b) for a, b in zip(new_t, m0t)]
            shift = max(deltas)
            mean_shift = statistics.fmean(deltas)
            m0m, m0t = new_m, new_t
            sm = _rmse(a - p for a, p in zip(am, m0m))
            st = _rmse(a - p for a, p in zip(at, m0t))
            mw = fit_key_weights(zip(m0m, iam), sm, MARGIN_SUPPORT)
            tw = fit_key_weights(zip(m0t, iat), st, TOTAL_SUPPORT)
            trace.append({"max_center_shift": shift, "mean_center_shift": mean_shift,
                          "sigma_margin": sm, "sigma_total": st})
        # Final centers solved with the FINAL sigma/weights are exactly the
        # evaluation-time M0 centers; C1's k and sigma are fit on them.
        cache_m = {l: solve_center("margin", l, sm, mw) for l in set(lm)}
        cache_t = {l: solve_center("total", l, st, tw) for l in set(lt)}
        m0m = [cache_m[l] for l in lm]
        m0t = [cache_t[l] for l in lt]

    km = _origin_slope([f - l for f, l in zip(fm, lm)], [a - c for a, c in zip(am, m0m)])
    kt = _origin_slope([f - l for f, l in zip(ft, lt)], [a - c for a, c in zip(at, m0t)])
    cm = [c + km * (f - l) for c, f, l in zip(m0m, fm, lm)]
    ct = [c + kt * (f - l) for c, f, l in zip(m0t, ft, lt)]

    sigma = {
        "B0": {"margin": _rmse(a - p for a, p in zip(am, b0m)), "total": _rmse(a - p for a, p in zip(at, b0t))},
        "F1": {"margin": _rmse(a - p for a, p in zip(am, fm)), "total": _rmse(a - p for a, p in zip(at, ft))},
        "M0": {"margin": sm, "total": st},
        "C1": {"margin": _rmse(a - p for a, p in zip(am, cm)), "total": _rmse(a - p for a, p in zip(at, ct))},
    }
    prov = {**dict(provenance), "fit_rows": len(dev_rows)}
    if procedure == FIT_PROCEDURE_D1:
        # The original artifact carries no procedure key; adding one only to the
        # amended fit keeps the original params byte-reproducible.
        prov.update({"fit_procedure": procedure, "d1_outer_iterations": D1_OUTER_ITERATIONS,
                     "d1_trace": trace})
    return GameMarketParams(
        f1_margin=f1m, f1_total=f1t, c1_k_margin=km, c1_k_total=kt,
        sigma=sigma, margin_weights=mw, total_weights=tw, provenance=prov,
    )
