"""Bounded historical test of B0 football disagreement with archived NFL closing lines.

Closing lines are retrospective controls, not authenticated point-in-time offers.
Nothing here is a live selector or an official-pick pathway.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from nfl.research.game_market_b0 import build_b0_predictions
from nfl.research.game_market_b0_research import PINNED_SCHEDULE_SOURCE, load_pinned_historical_rows
from nfl.research.scoring_prior_features import build_prior_scoring_features


PARTITIONS = {"development": (2000, 2019), "validation": (2020, 2022), "held": (2023, 2025)}
SEED = 20261002


def fit_slope(rows: list[dict], market: str) -> float:
    """OLS through the origin: actual-minus-market on B0-minus-market."""
    xs = [r[f"b0_{market}"] - r[f"line_{market}"] for r in rows]
    ys = [r[f"actual_{market}"] - r[f"line_{market}"] for r in rows]
    denom = sum(x * x for x in xs)
    if denom <= 0:
        raise ValueError("no football disagreement with market")
    return sum(x * y for x, y in zip(xs, ys)) / denom


def matched_rows(path: Path) -> tuple[list[dict], dict]:
    scoring, market, counts = load_pinned_historical_rows(path)
    features = build_prior_scoring_features(scoring, rolling_window=5)
    preds = {p["game_id"]: p for p in build_b0_predictions(features, min_prior_games=3)}
    rows = []
    for m in market:
        p = preds[m["game_id"]]
        if p["eligibility"] != "ELIGIBLE":
            continue
        if any(p[k] != m[k] for k in ("season", "week", "home_team", "away_team")):
            raise ValueError(f"game identity mismatch: {m['game_id']}")
        if m["point_in_time_feature_eligible"] is not False or m["market_vintage"] != "CLOSING":
            raise ValueError("unexpected market vintage")
        rows.append({
            "game_id": m["game_id"], "season": m["season"], "week": m["week"],
            "b0_margin": p["predicted_home_margin"], "line_margin": m["spread_line"],
            "actual_margin": m["home_score"] - m["away_score"],
            "b0_total": p["predicted_total"], "line_total": m["total_line"],
            "actual_total": m["home_score"] + m["away_score"],
        })
    rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    if len({r["game_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate matched games")
    return rows, counts


def score(rows: list[dict], market: str, slope: float) -> dict:
    if not rows:
        raise ValueError("empty partition")
    errors = {"closing_market": [], "b0": [], "residual_blend": []}
    cover = {"b0": [], "residual_blend": []}
    pushes = 0
    for r in rows:
        line, actual, b0 = (r[f"{k}_{market}"] for k in ("line", "actual", "b0"))
        pred = line + slope * (b0 - line)
        for name, value in (("closing_market", line), ("b0", b0), ("residual_blend", pred)):
            errors[name].append(abs(value - actual))
        if actual == line:
            pushes += 1
            continue
        for name, delta in (("b0", b0 - line), ("residual_blend", slope * (b0 - line))):
            if delta == 0:
                continue
            cover[name].append(delta * (actual - line) > 0)
    return {
        "n_games": len(rows), "pushes": pushes,
        "mae": {name: statistics.fmean(v) for name, v in errors.items()},
        "direction": {name: {"n": len(v), "correct": sum(v), "rate": statistics.fmean(v) if v else None}
                      for name, v in cover.items()},
    }


def block_interval(rows: list[dict], market: str, slope: float, *, iterations: int = 2000) -> dict:
    """Season-week cluster bootstrap for paired MAE deltas and nonpush direction."""
    blocks = defaultdict(list)
    for row in rows:
        blocks[(row["season"], row["week"])].append(row)
    keys = sorted(blocks)
    rng = random.Random(SEED)
    values = {"blend_minus_market_mae": [], "b0_minus_market_mae": [], "blend_direction": []}
    for _ in range(iterations):
        sample = [r for _ in keys for r in blocks[keys[rng.randrange(len(keys))]]]
        s = score(sample, market, slope)
        values["blend_minus_market_mae"].append(s["mae"]["residual_blend"] - s["mae"]["closing_market"])
        values["b0_minus_market_mae"].append(s["mae"]["b0"] - s["mae"]["closing_market"])
        d = s["direction"]["residual_blend"]["rate"]
        if d is not None:
            values["blend_direction"].append(d)
    def interval(v):
        v.sort()
        return [v[int((len(v)-1)*p)] for p in (0.025, 0.5, 0.975)] if v else None
    return {"unit": "season_week", "iterations": iterations, "seed": SEED,
            "p2_5_p50_p97_5": {k: interval(v) for k, v in values.items()}}


def run(path: Path) -> dict:
    rows, source_counts = matched_rows(path)
    parts = {name: [r for r in rows if lo <= r["season"] <= hi]
             for name, (lo, hi) in PARTITIONS.items()}
    if not all(parts.values()):
        raise ValueError("missing fixed partition")
    results = {}
    for market in ("margin", "total"):
        slope = fit_slope(parts["development"], market)
        results[market] = {"development_slope": slope, "partitions": {
            name: {**score(group, market, slope), "uncertainty": block_interval(group, market, slope)}
            for name, group in parts.items()}}
    return {
        "schema": 1, "status": "EXPLORATORY_RESEARCH_ONLY_NOT_A_PICK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": PINNED_SCHEDULE_SOURCE, "source_counts": source_counts,
        "partitions": PARTITIONS, "baseline": "GAME_MARKET_B0_PRIOR_SCORING_BLEND",
        "method": "OLS origin slope fit 2000-2019 only; fixed slope evaluated 2020-2022 and 2023-2025; archived closing lines only",
        "matched_games": len(rows), "results": results,
        "limitations": ["Closing lines lack earlier offer timestamps and odds; no real usable-volume or ROI claim.",
                        "Market-only has no directional side against its own line.",
                        "A positive scalar blend preserves B0's side, so cannot improve its equal-volume cover rate.",
                        "The held partition is historical confirmation only, not a prospective trial."],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.games_csv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"matched_games": report["matched_games"], "results": report["results"]}, indent=2))


if __name__ == "__main__":
    main()

