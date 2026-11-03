"""FC-NFL-004 historical benchmark: B0 vs F1 vs M0 vs C1 (see PREREG.md).

Fits every parameter on development seasons 2000-2019 only, then reports
validation 2020-2022, held 2023-2025 (previously inspected by PR #222) and a
fresh 2026 Weeks 1-5 population once. Closing lines/odds are retrospective
controls; nothing here is an operational price or a pick.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nfl.research.game_market_b0 import build_b0_predictions
from nfl.research.game_market_b0_research import PINNED_SCHEDULE_SOURCE, load_pinned_historical_rows
from nfl.research.game_market_models import (
    CONFIGS,
    FIT_PROCEDURE_D1,
    FIT_PROCEDURE_ORIGINAL,
    GameMarketParams,
    centers,
    distribution,
    fit_params,
    params_sha256,
)
from nfl.research.game_score_distribution import conditional_win
from nfl.research.scoring_prior_features import build_prior_scoring_features

PARTITIONS = {"development": (2000, 2019), "validation": (2020, 2022), "held": (2023, 2025)}
FRESH_SEASON = 2026
FRESH_SOURCE_SHA256 = "9380292232901e4321c99992c7efca0c567a0a801dcb292662a3f78a9a101c49"
SEED = 20261012
ITERATIONS = 2000
BREAK_EVEN_110 = 110 / 210
THRESHOLDS = (BREAK_EVEN_110, 0.55)
EPS = 1e-12


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _odds(text: str | None) -> int | None:
    t = str(text or "").strip()
    if not t:
        return None
    v = float(t)
    return int(v) if v.is_integer() and v != 0 else None


def _extras(path: Path) -> dict[str, dict]:
    """Retrospective-only columns: closing odds and the actual starting QB ids."""
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["game_id"]] = {
                "home_spread_odds": _odds(row.get("home_spread_odds")),
                "away_spread_odds": _odds(row.get("away_spread_odds")),
                "over_odds": _odds(row.get("over_odds")),
                "under_odds": _odds(row.get("under_odds")),
                "home_qb_id": (row.get("home_qb_id") or "").strip() or None,
                "away_qb_id": (row.get("away_qb_id") or "").strip() or None,
                "gameday": row.get("gameday"),
            }
    return out


def _qb_change_flags(rows: list[dict], extras: dict[str, dict]) -> dict[str, bool | None]:
    """Did either team's actual starter differ from its previous REG game starter?

    LEAKY retrospective diagnostic only: uses the realised starter.
    """
    ordered = sorted(rows, key=lambda r: (r["season"], r["week"], r["game_id"]))
    last: dict[str, str] = {}
    flags: dict[str, bool | None] = {}
    for r in ordered:
        e = extras.get(r["game_id"], {})
        changed: bool | None = False
        for side in ("home", "away"):
            team, qb = r[f"{side}_team"], e.get(f"{side}_qb_id")
            prev = last.get(team)
            if qb is None or prev is None:
                changed = None if changed is False else changed
            elif qb != prev:
                changed = True
            if qb is not None:
                last[team] = qb
        flags[r["game_id"]] = changed
    return flags


def _matched(scoring: list[dict], market: list[dict], extras: dict[str, dict]) -> list[dict]:
    features = build_prior_scoring_features(scoring, rolling_window=5)
    preds = {p["game_id"]: p for p in build_b0_predictions(features, min_prior_games=3)}
    qb = _qb_change_flags(scoring, extras)
    rows = []
    for m in market:
        p = preds.get(m["game_id"])
        if p is None or p["eligibility"] != "ELIGIBLE":
            continue
        if any(p[k] != m[k] for k in ("season", "week", "home_team", "away_team")):
            raise ValueError(f"identity mismatch {m['game_id']}")
        e = extras.get(m["game_id"], {})
        rows.append({
            "game_id": m["game_id"], "season": m["season"], "week": m["week"],
            "b0_margin": p["predicted_home_margin"], "b0_total": p["predicted_total"],
            "line_margin": m["spread_line"], "line_total": m["total_line"],
            "actual_margin": m["home_score"] - m["away_score"],
            "actual_total": m["home_score"] + m["away_score"],
            "qb_change": qb.get(m["game_id"]),
            **{k: e.get(k) for k in ("home_spread_odds", "away_spread_odds", "over_odds", "under_odds")},
        })
    rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    return rows


def load_fresh_rows(current_csv: Path) -> list[dict]:
    """2026 REG finals; B0 history = 2025 REG + strictly earlier 2026 weeks."""
    if _sha256(current_csv) != FRESH_SOURCE_SHA256:
        raise ValueError("fresh source sha256 drift")
    scoring, market = [], []
    with current_csv.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["game_type"] != "REG" or int(row["season"]) not in (2025, FRESH_SEASON):
                continue
            if not row["home_score"] or not row["away_score"]:
                continue
            base = {
                "game_id": row["game_id"], "season": int(row["season"]), "week": int(row["week"]),
                "game_type": "REG", "home_team": row["home_team"], "away_team": row["away_team"],
                "home_score": int(row["home_score"]), "away_score": int(row["away_score"]),
            }
            scoring.append({**base, "final_status": "FINAL"})
            if base["season"] == FRESH_SEASON and row["spread_line"] and row["total_line"]:
                market.append({**base, "spread_line": float(row["spread_line"]),
                               "total_line": float(row["total_line"])})
    return _matched(scoring, market, _extras(current_csv))


# --------------------------------------------------------------------------
def _american_profit(odds: int) -> float:
    return odds / 100 if odds > 0 else 100 / -odds


def _game_metrics(row: dict, config: str, params: GameMarketParams) -> dict | None:
    cm, ct = centers(config, params, b0_margin=row["b0_margin"], b0_total=row["b0_total"],
                     line_margin=row["line_margin"], line_total=row["line_total"])
    if cm is None or ct is None:
        return None
    dist = distribution(config, params, cm, ct)
    out: dict[str, Any] = {
        "abs_margin": abs(dist.expected_margin - row["actual_margin"]),
        "sq_margin": (dist.expected_margin - row["actual_margin"]) ** 2,
        "abs_total": abs(dist.expected_total - row["actual_total"]),
        "sq_total": (dist.expected_total - row["actual_total"]) ** 2,
    }
    for market, side_a, side_b, line, actual, odds_a, odds_b in (
        ("spread", "HOME", "AWAY", -row["line_margin"], row["actual_margin"],
         row.get("home_spread_odds"), row.get("away_spread_odds")),
        ("total", "OVER", "UNDER", row["line_total"], row["actual_total"],
         row.get("over_odds"), row.get("under_odds")),
    ):
        o = dist.spread(side_a, line) if market == "spread" else dist.total(side_a, line)
        result = actual + line if market == "spread" else actual - line
        idx = 0 if result > 0 else 1 if result == 0 else 2
        out[f"{market}_logloss3"] = -math.log(max(o[idx], EPS))
        out[f"{market}_push_pred"] = o[1]
        out[f"{market}_push_obs"] = 1.0 if idx == 1 else 0.0
        p = conditional_win(o)
        out[f"{market}_p_side_a"] = p
        if idx != 1:
            y = 1.0 if idx == 0 else 0.0
            out[f"{market}_brier"] = (p - y) ** 2
            out[f"{market}_y"] = y
        for thr in THRESHOLDS:
            key = f"{market}_sel_{thr:.4f}"
            if max(p, 1 - p) < thr:
                out[key] = None
                continue
            pick_a = p >= 0.5
            if idx == 1:
                out[key] = {"result": "PUSH", "profit": 0.0}
                continue
            hit = (idx == 0) == pick_a
            odds = odds_a if pick_a else odds_b
            profit = None if odds is None else (_american_profit(odds) if hit else -1.0)
            out[key] = {"result": "HIT" if hit else "MISS", "profit": profit}
    hw = dist.moneyline("HOME")
    if row["actual_margin"] != 0:
        p = hw[0] / (hw[0] + hw[2])
        out["homewin_logloss"] = -math.log(max(p if row["actual_margin"] > 0 else 1 - p, EPS))
    return out


def _summarise(metrics: list[dict]) -> dict:
    def mean(key):
        vals = [m[key] for m in metrics if m.get(key) is not None]
        return statistics.fmean(vals) if vals else None
    out = {
        "n": len(metrics),
        "margin_mae": mean("abs_margin"), "margin_rmse": math.sqrt(mean("sq_margin")),
        "total_mae": mean("abs_total"), "total_rmse": math.sqrt(mean("sq_total")),
        "homewin_logloss": mean("homewin_logloss"),
    }
    for market in ("spread", "total"):
        out[f"{market}_logloss3"] = mean(f"{market}_logloss3")
        out[f"{market}_brier_nonpush"] = mean(f"{market}_brier")
        out[f"{market}_push_pred_mean"] = mean(f"{market}_push_pred")
        out[f"{market}_push_obs_rate"] = mean(f"{market}_push_obs")
        for thr in THRESHOLDS:
            sel = [m[f"{market}_sel_{thr:.4f}"] for m in metrics if m.get(f"{market}_sel_{thr:.4f}")]
            hits = sum(s["result"] == "HIT" for s in sel)
            misses = sum(s["result"] == "MISS" for s in sel)
            priced = [s["profit"] for s in sel if s["result"] != "PUSH" and s["profit"] is not None]
            out[f"{market}_sides_{thr:.4f}"] = {
                "picks": len(sel), "hits": hits, "misses": misses,
                "pushes": len(sel) - hits - misses,
                "hit_rate": hits / (hits + misses) if hits + misses else None,
                "RETROSPECTIVE_CLOSING_PRICE_CONTROL_roi": (sum(priced) / len(priced)) if priced else None,
                "closing_price_rows": len(priced),
            }
    return out


def _reliability(metrics: list[dict], market: str) -> list[dict]:
    bins = defaultdict(list)
    for m in metrics:
        if m.get(f"{market}_y") is None:
            continue
        p = m[f"{market}_p_side_a"]
        bins[min(9, int(p * 10))].append((p, m[f"{market}_y"]))
    return [{"bin": b, "n": len(v), "mean_pred": statistics.fmean(x for x, _ in v),
             "obs_rate": statistics.fmean(y for _, y in v)} for b, v in sorted(bins.items())]


def _bootstrap(rows: list[dict], per_config: dict[str, list[dict]]) -> dict:
    blocks = defaultdict(list)
    for i, r in enumerate(rows):
        blocks[(r["season"], r["week"])].append(i)
    keys = sorted(blocks)
    rng = random.Random(SEED)
    stats = defaultdict(list)
    for _ in range(ITERATIONS):
        idx = [i for _ in keys for i in blocks[keys[rng.randrange(len(keys))]]]
        for config in CONFIGS:
            if config == "M0":
                continue
            for market in ("spread", "total"):
                d = statistics.fmean(
                    per_config[config][i][f"{market}_logloss3"] - per_config["M0"][i][f"{market}_logloss3"]
                    for i in idx)
                stats[f"{config}_minus_M0_{market}_logloss3"].append(d)
                sel = [per_config[config][i][f"{market}_sel_{BREAK_EVEN_110:.4f}"] for i in idx]
                sel = [s for s in sel if s and s["result"] != "PUSH"]
                if sel:
                    stats[f"{config}_{market}_hit_rate_at_breakeven"].append(
                        sum(s["result"] == "HIT" for s in sel) / len(sel))
    def q(v):
        v = sorted(v)
        return [v[int((len(v) - 1) * p)] for p in (0.025, 0.5, 0.975)]
    return {"unit": "season_week", "iterations": ITERATIONS, "seed": SEED,
            "p2_5_p50_p97_5": {k: q(v) for k, v in sorted(stats.items())}}


def _evaluate(rows: list[dict], params: GameMarketParams, *, bootstrap: bool) -> dict:
    per_config = {c: [_game_metrics(r, c, params) for r in rows] for c in CONFIGS}
    for c in CONFIGS:
        if any(m is None for m in per_config[c]):
            raise ValueError(f"{c} missing metrics on matched rows")
    out = {"n_games": len(rows), "configs": {c: _summarise(per_config[c]) for c in CONFIGS},
           "reliability": {c: {mk: _reliability(per_config[c], mk) for mk in ("spread", "total")}
                           for c in ("F1", "C1")}}
    subgroups = {
        "weeks_1_4": [i for i, r in enumerate(rows) if r["week"] <= 4],
        "weeks_5_plus": [i for i, r in enumerate(rows) if r["week"] >= 5],
        "qb_change_LEAKY": [i for i, r in enumerate(rows) if r["qb_change"] is True],
        "qb_stable_LEAKY": [i for i, r in enumerate(rows) if r["qb_change"] is False],
    }
    out["subgroups"] = {
        name: {"n": len(idx), **{c: {k: v for k, v in _summarise([per_config[c][i] for i in idx]).items()
                                     if k in ("margin_mae", "total_mae", "spread_logloss3", "total_logloss3",
                                              f"spread_sides_{BREAK_EVEN_110:.4f}",
                                              f"total_sides_{BREAK_EVEN_110:.4f}")}
                                 for c in CONFIGS}}
        for name, idx in subgroups.items() if idx
    }
    if bootstrap:
        out["uncertainty"] = _bootstrap(rows, per_config)
    return out


def _ols2(rows: list[dict], x1: str, x2: str, y: str) -> dict:
    """OLS y ~ 1 + x1 + x2 via normal equations (diagnostic only)."""
    n = len(rows)
    X = [[1.0, r[x1], r[x2]] for r in rows]
    Y = [r[y] for r in rows]
    xtx = [[sum(X[k][i] * X[k][j] for k in range(n)) for j in range(3)] for i in range(3)]
    xty = [sum(X[k][i] * Y[k] for k in range(n)) for i in range(3)]
    # Gaussian elimination
    a = [row[:] + [v] for row, v in zip(xtx, xty)]
    for i in range(3):
        piv = a[i][i]
        a[i] = [v / piv for v in a[i]]
        for j in range(3):
            if j != i:
                f = a[j][i]
                a[j] = [vj - f * vi for vj, vi in zip(a[j], a[i])]
    return {"intercept": a[0][3], x1: a[1][3], x2: a[2][3]}


def _why(dev: list[dict], params: GameMarketParams) -> dict:
    out = {}
    for t in ("margin", "total"):
        b0, line, act = (f"b0_{t}", f"line_{t}", f"actual_{t}")
        resid = [r[act] - r[b0] for r in dev]
        out[t] = {
            "b0_mean_residual_actual_minus_b0": statistics.fmean(resid),
            "line_mean_residual_actual_minus_line": statistics.fmean(r[act] - r[line] for r in dev),
            "corr_b0_line": statistics.correlation([r[b0] for r in dev], [r[line] for r in dev]),
            "sd_b0": statistics.pstdev(r[b0] for r in dev),
            "sd_line": statistics.pstdev(r[line] for r in dev),
            "ols_actual_on_line_and_b0": _ols2(dev, line, b0, act),
        }
    out["f1"] = {"margin": params.f1_margin, "total": params.f1_total}
    out["c1_k"] = {"margin": params.c1_k_margin, "total": params.c1_k_total}
    return out


CLASSIFICATION = {
    FIT_PROCEDURE_ORIGINAL: (
        "SUPERSEDED_INCONSISTENT: pre-registered line-centered fit evaluated with the "
        "D1 solved M0; M0/C1 fit and evaluation definitions differ (Codex P1)"),
    FIT_PROCEDURE_D1: (
        "AMENDED_EXPLORATORY_POST_D1: fit and evaluation both use the solved M0; "
        "amendment made after D1 was found; validation/held were viewed before the "
        "amendment and held 2023-2025 was also inspected by PR #222; NOT a virgin "
        "holdout and NOT promotion evidence"),
}


def run(pinned_csv: Path, current_csv: Path | None,
        procedure: str = FIT_PROCEDURE_D1) -> tuple[dict, GameMarketParams]:
    scoring, market, counts = load_pinned_historical_rows(pinned_csv)
    rows = _matched(scoring, market, _extras(pinned_csv))
    parts = {n: [r for r in rows if lo <= r["season"] <= hi] for n, (lo, hi) in PARTITIONS.items()}
    params = fit_params(parts["development"], {
        "fit_partition": "development_2000_2019",
        "source_sha256": PINNED_SCHEDULE_SOURCE["sha256"],
        "source_commit": PINNED_SCHEDULE_SOURCE["commit"],
        "prereg": "engineering/nfl_game_market_pathway_20261012/PREREG.md",
        "prereg_commit": "35a6cb032c5f6ac5ced37ba3a86716a57e1123e8",
    }, procedure=procedure)
    report: dict[str, Any] = {
        "schema": "fc-nfl-004-benchmark-v1",
        "status": "RESEARCH_ONLY_NOT_PROMOTED_NOT_A_PICK",
        "fit_procedure": procedure,
        "result_classification": CLASSIFICATION[procedure],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "prereg_commit": "35a6cb032c5f6ac5ced37ba3a86716a57e1123e8",
        "source": PINNED_SCHEDULE_SOURCE, "source_counts": counts,
        "matched_games": len(rows), "params_sha256": params_sha256(params),
        "why_diagnostics_development": _why(parts["development"], params),
        "partitions": {n: _evaluate(p, params, bootstrap=n != "development") for n, p in parts.items()},
    }
    if current_csv is not None:
        fresh = load_fresh_rows(current_csv)
        report["fresh_2026"] = {"source_sha256": FRESH_SOURCE_SHA256,
                                "game_ids": [r["game_id"] for r in fresh],
                                **_evaluate(fresh, params, bootstrap=False)}
    report["limitations"] = [
        "Closing lines/odds are retrospective controls; no timestamped earlier offers exist in this source.",
        "Information timing (open vs close) cannot be tested from this source.",
        "QB-change subgroup uses realised starters (leaky); it is a diagnostic, never a feature.",
        "Margin and total are separate marginals; no same-game joint distribution.",
        "Held 2023-2025 was previously inspected by PR #222 for B0/close.",
    ]
    return report, params


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pinned-csv", type=Path, required=True)
    ap.add_argument("--current-csv", type=Path)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--params", type=Path, required=True)
    ap.add_argument("--procedure", choices=sorted(CLASSIFICATION), default=FIT_PROCEDURE_D1)
    args = ap.parse_args()
    report, params = run(args.pinned_csv, args.current_csv, args.procedure)
    args.params.write_text(json.dumps(params.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"params_sha256": report["params_sha256"], "matched": report["matched_games"]}))


if __name__ == "__main__":
    main()
