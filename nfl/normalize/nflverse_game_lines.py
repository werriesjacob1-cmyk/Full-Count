#!/usr/bin/env python3
"""Normalize settled nflverse schedule lines with explicit source limits."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


SOURCE_CLASS = "NFLVERSE_PFR_CLOSING"
SOURCE_REPOSITORY = "https://github.com/nflverse/nfldata"
REQUIRED = {
    "game_id", "season", "game_type", "week", "gameday", "away_team",
    "home_team", "away_score", "home_score", "result", "total",
    "spread_line", "away_spread_odds", "home_spread_odds", "total_line",
    "under_odds", "over_odds",
}


def _finite(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    number = _finite(value)
    return int(number) if number is not None and number.is_integer() else None


def _american(value: object) -> int | None:
    number = _integer(value)
    return number if number not in (None, 0) else None


def _utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("acquired_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("acquired_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: str) -> str:
    digest = value.strip()
    if len(digest) != 64 or digest != digest.lower() or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("source_file_sha256 must be lowercase SHA-256 hex")
    return digest


def _outcome(value: float, line: float, high: str, low: str) -> str:
    if value > line:
        return high
    if value < line:
        return low
    return "PUSH"


def normalize_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    source_commit: str,
    source_file_sha256: str,
    acquired_at: str,
) -> dict:
    """Normalize settled rows; retain every exclusion with a reason."""
    commit = source_commit.strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit.lower()):
        raise ValueError("source_commit must be a 40-character Git SHA")
    digest = _digest(source_file_sha256)
    acquired = _utc(acquired_at)
    normalized = []
    excluded = []
    materialized = [(row_number, dict(raw)) for row_number, raw in enumerate(rows, 2)]
    id_counts = Counter(
        str(row.get("game_id") or "").strip()
        for _, row in materialized
        if str(row.get("game_id") or "").strip()
    )
    for row_number, row in materialized:
        missing = sorted(REQUIRED.difference(row))
        game_id = str(row.get("game_id") or "").strip() or None
        if missing:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "MISSING_COLUMNS", "detail": missing})
            continue
        if game_id is None:
            excluded.append({"row_number": row_number, "game_id": None, "reason": "MISSING_GAME_ID"})
            continue
        if id_counts[game_id] > 1:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "DUPLICATE_GAME_ID"})
            continue
        away = str(row["away_team"] or "").strip()
        home = str(row["home_team"] or "").strip()
        if not away or not home or away == home:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "INVALID_TEAM_IDENTITY"})
            continue
        season = _integer(row["season"])
        week = _integer(row["week"])
        game_type = str(row["game_type"] or "").strip()
        game_date = str(row["gameday"] or "").strip()
        try:
            date.fromisoformat(game_date)
        except ValueError:
            game_date = ""
        if season is None or season < 1990 or week is None or week < 1 or not game_type or not game_date:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "INVALID_TEMPORAL_IDENTITY"})
            continue
        away_score = _integer(row["away_score"])
        home_score = _integer(row["home_score"])
        if away_score is None or home_score is None:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "UNSETTLED_GAME"})
            continue
        home_margin = home_score - away_score
        actual_total = home_score + away_score
        if _finite(row["result"]) != home_margin or _finite(row["total"]) != actual_total:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "OUTCOME_INCONSISTENT"})
            continue
        source_spread = _finite(row["spread_line"])
        total_line = _finite(row["total_line"])
        if source_spread is None or total_line is None or total_line <= 0:
            excluded.append({"row_number": row_number, "game_id": game_id, "reason": "INVALID_MARKET_LINE"})
            continue
        away_price = _american(row["away_spread_odds"])
        home_price = _american(row["home_spread_odds"])
        under_price = _american(row["under_odds"])
        over_price = _american(row["over_odds"])
        spread_prices_complete = away_price is not None and home_price is not None
        total_prices_complete = under_price is not None and over_price is not None
        normalized.append({
            "source_class": SOURCE_CLASS,
            "source_repository": SOURCE_REPOSITORY,
            "source_commit": commit,
            "source_file_sha256": digest,
            "source_acquired_at": acquired,
            "source_game_id": game_id,
            "market_source": "PRO_FOOTBALL_REFERENCE_VIA_NFLVERSE",
            "market_vintage": "CLOSING",
            "allowed_use": "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY",
            "point_in_time_feature_eligible": False,
            "season": season,
            "game_type": game_type,
            "week": week,
            "game_date": game_date,
            "away_team": away,
            "home_team": home,
            "away_score": away_score,
            "home_score": home_score,
            "home_margin": home_margin,
            "actual_total": actual_total,
            "source_spread_line": source_spread,
            "away_handicap": source_spread,
            "home_handicap": -source_spread,
            "spread_outcome": _outcome(home_margin, source_spread, "HOME_COVER", "AWAY_COVER"),
            "away_spread_odds": away_price,
            "home_spread_odds": home_price,
            "spread_prices_complete": spread_prices_complete,
            "total_line": total_line,
            "total_outcome": _outcome(actual_total, total_line, "OVER", "UNDER"),
            "over_odds": over_price,
            "under_odds": under_price,
            "total_prices_complete": total_prices_complete,
            "book_specific_eligible": False,
            "line_movement_eligible": False,
        })
    return {
        "schema_version": 1,
        "source_class": SOURCE_CLASS,
        "normalized": normalized,
        "excluded": excluded,
        "stats": {
            "normalized": len(normalized),
            "excluded": len(excluded),
            "spread_prices_complete": sum(r["spread_prices_complete"] for r in normalized),
            "total_prices_complete": sum(r["total_prices_complete"] for r in normalized),
        },
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--acquired-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actual = sha256_file(args.games)
    expected = _digest(args.expected_sha256)
    if actual != expected:
        raise ValueError(f"source digest mismatch: {actual} != {expected}")
    with args.games.open("r", encoding="utf-8-sig", newline="") as handle:
        result = normalize_rows(
            csv.DictReader(handle), source_commit=args.source_commit,
            source_file_sha256=actual, acquired_at=args.acquired_at,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(result["stats"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
