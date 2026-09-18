#!/usr/bin/env python3
"""Reproduce the first NFL spread/total B0 on a pinned nflverse schedules asset.

This runner is intentionally offline-input-only. It never fetches a moving URL.
The caller must provide the exact audited `games.csv` bytes, which are verified
against the same immutable nflverse commit and digest used by the historical
game-line normalizer before any research is computed.

Historical scoring state is built from every completed REG game through 2025.
Closing lines are then used only to define the retrospective benchmark subset;
line availability never changes the scoring history used by B0.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nfl.research.game_market_b0 import (
    BASELINE_NAME,
    build_b0_predictions,
    evaluate_against_closing_market,
)
from nfl.research.scoring_prior_features import build_prior_scoring_features


PINNED_SCHEDULE_SOURCE = {
    "repository": "https://github.com/nflverse/nfldata",
    "commit": "8ed09b2fe3ea42332b2249a995737e13dd931ff3",
    "path": "data/games.csv",
    "bytes": 2177838,
    "sha256": "26332ae5d8d8d0481f0670cf5e3849497a415351d4026ae5bee15a5aab96d188",
    "source_class": "NFLVERSE_PFR_CLOSING",
    "market_source": "PRO_FOOTBALL_REFERENCE_VIA_NFLVERSE",
    "market_vintage": "CLOSING",
    "allowed_use": "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY",
    "point_in_time_feature_eligible": False,
    "historical_cutoff_season": 2025,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _int_text(value: Any, field: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} missing")
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be integer: {value!r}") from exc
    return parsed


def _float_text(value: Any, field: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} missing")
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def load_pinned_historical_rows(path: Path) -> tuple[list[dict], list[dict], dict]:
    """Return scoring-history rows and market-evaluation rows from pinned CSV."""
    observed_bytes = path.stat().st_size
    if observed_bytes != PINNED_SCHEDULE_SOURCE["bytes"]:
        raise ValueError(
            f"schedule asset byte drift: {observed_bytes} != {PINNED_SCHEDULE_SOURCE['bytes']}"
        )
    observed_sha = sha256_file(path)
    if observed_sha != PINNED_SCHEDULE_SOURCE["sha256"]:
        raise ValueError(
            f"schedule asset SHA-256 drift: {observed_sha} != {PINNED_SCHEDULE_SOURCE['sha256']}"
        )

    scoring_rows: list[dict] = []
    market_rows: list[dict] = []
    counts = Counter()
    seen_game_ids: set[str] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "game_id", "season", "game_type", "week", "away_team", "away_score",
            "home_team", "home_score", "result", "total", "spread_line", "total_line",
        }
        missing_columns = sorted(required.difference(reader.fieldnames or []))
        if missing_columns:
            raise ValueError(
                f"schedule CSV missing required columns: {', '.join(missing_columns)}"
            )

        for csv_row_number, row in enumerate(reader, start=2):
            counts["source_rows"] += 1
            game_id = str(row.get("game_id") or "").strip()
            if not game_id:
                raise ValueError(f"row {csv_row_number}: game_id missing")
            if game_id in seen_game_ids:
                raise ValueError(f"row {csv_row_number}: duplicate game_id {game_id}")
            seen_game_ids.add(game_id)
            game_type = str(row.get("game_type") or "").strip().upper()
            if game_type != "REG":
                counts["non_reg_rows"] += 1
                continue
            season = _int_text(row.get("season"), "season")
            if season > PINNED_SCHEDULE_SOURCE["historical_cutoff_season"]:
                counts["post_cutoff_reg_rows"] += 1
                continue
            week = _int_text(row.get("week"), "week")
            home_team = str(row.get("home_team") or "").strip().upper()
            away_team = str(row.get("away_team") or "").strip().upper()
            if not home_team or not away_team or home_team == away_team:
                raise ValueError(f"row {csv_row_number}: invalid team identity")
            home_score = _int_text(row.get("home_score"), "home_score")
            away_score = _int_text(row.get("away_score"), "away_score")
            if home_score < 0 or away_score < 0:
                raise ValueError(f"row {csv_row_number}: negative score")
            reported_result = _float_text(row.get("result"), "result")
            reported_total = _float_text(row.get("total"), "total")
            calculated_result = home_score - away_score
            calculated_total = home_score + away_score
            if not math.isclose(reported_result, calculated_result, abs_tol=1e-12):
                raise ValueError(f"row {csv_row_number}: result inconsistency")
            if not math.isclose(reported_total, calculated_total, abs_tol=1e-12):
                raise ValueError(f"row {csv_row_number}: total inconsistency")

            scoring_rows.append({
                "game_id": game_id,
                "season": season,
                "week": week,
                "game_type": game_type,
                "home_team": home_team,
                "away_team": away_team,
                "final_status": "FINAL",
                "home_score": home_score,
                "away_score": away_score,
            })
            counts["historical_reg_final_rows"] += 1

            spread_text = str(row.get("spread_line") or "").strip()
            total_line_text = str(row.get("total_line") or "").strip()
            if not spread_text or not total_line_text:
                counts["historical_reg_rows_missing_closing_market"] += 1
                continue
            spread_line = _float_text(spread_text, "spread_line")
            total_line = _float_text(total_line_text, "total_line")
            if total_line <= 0:
                raise ValueError(f"row {csv_row_number}: total_line must be positive")
            market_rows.append({
                "game_id": game_id,
                "season": season,
                "week": week,
                "game_type": game_type,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "spread_line": spread_line,
                "total_line": total_line,
                "source_class": PINNED_SCHEDULE_SOURCE["source_class"],
                "market_source": PINNED_SCHEDULE_SOURCE["market_source"],
                "market_vintage": PINNED_SCHEDULE_SOURCE["market_vintage"],
                "allowed_use": PINNED_SCHEDULE_SOURCE["allowed_use"],
                "point_in_time_feature_eligible": (
                    PINNED_SCHEDULE_SOURCE["point_in_time_feature_eligible"]
                ),
            })
            counts["historical_reg_rows_with_closing_market"] += 1

    return scoring_rows, market_rows, dict(sorted(counts.items()))


def run_research(schedules_csv: Path) -> dict:
    scoring_rows, market_rows, source_counts = load_pinned_historical_rows(schedules_csv)
    scoring_features = build_prior_scoring_features(scoring_rows, rolling_window=5)
    predictions = build_b0_predictions(scoring_features, min_prior_games=3)
    market_game_ids = {row["game_id"] for row in market_rows}
    market_predictions = [row for row in predictions if row["game_id"] in market_game_ids]
    report = evaluate_against_closing_market(
        market_predictions,
        market_rows,
        bootstrap_iterations=2000,
        bootstrap_seed=20260914,
    )
    eligibility_counts = Counter(row["eligibility"] for row in predictions)
    market_eligibility_counts = Counter(row["eligibility"] for row in market_predictions)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "baseline_name": BASELINE_NAME,
        "source": dict(PINNED_SCHEDULE_SOURCE),
        "source_counts": source_counts,
        "scoring_feature_rows": len(scoring_features),
        "game_predictions": len(predictions),
        "prediction_eligibility": dict(sorted(eligibility_counts.items())),
        "market_population_predictions": len(market_predictions),
        "market_population_eligibility": dict(sorted(market_eligibility_counts.items())),
        "evaluation": report,
        "research_limitations": [
            "Closing lines are retrospective Pro-Football-Reference controls, not timestamped FanDuel observations.",
            "B0 has no home-field adjustment, injury/roster/weather/PBP context, probability calibration, or selector.",
            "The evaluation does not establish sportsbook profitability or a deployable betting strategy.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedules-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_research(args.schedules_csv)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    held = report["evaluation"]["partitions"]["held_2023_2025"]
    print(json.dumps({
        "source_sha256": report["source"]["sha256"],
        "market_population_eligibility": report["market_population_eligibility"],
        "held": held,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
