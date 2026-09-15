#!/usr/bin/env python3
"""Audit nflverse game-line files without treating them as sportsbook quotes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


SOURCE_CLASS = "NFLVERSE_SCHEDULE_UNKNOWN_BOOK"
GAMES_REQUIRED = {
    "game_id", "season", "gameday", "away_team", "home_team",
    "away_score", "home_score", "result", "total", "spread_line",
    "away_spread_odds", "home_spread_odds", "total_line", "under_odds",
    "over_odds",
}
CLOSING_REQUIRED = {"game_id", "alt_game_id", "type", "side", "line", "odds", "outcome"}
INITIAL_REQUIRED = {"season", "sportsbook", "type", "about", "side", "line"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = sorted(required.difference(header))
        if missing:
            raise ValueError(f"{path.name} missing required columns: {', '.join(missing)}")
        return header, list(reader)


def _number(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"invalid numeric value {text!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite numeric value {text!r}")
    return number


def _season(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return -1


def audit_games(path: Path) -> dict:
    header, rows = read_csv(path, GAMES_REQUIRED)
    ids: set[str] = set()
    duplicate_ids = 0
    outcome_mismatches = 0
    by_season: dict[int, Counter] = defaultdict(Counter)
    settled = 0
    future = 0
    coverage = Counter()
    for row in rows:
        game_id = row["game_id"].strip()
        if not game_id:
            raise ValueError("games.csv contains a blank game_id")
        if game_id in ids:
            duplicate_ids += 1
        ids.add(game_id)
        season = _season(row["season"])
        away_score = _number(row["away_score"])
        home_score = _number(row["home_score"])
        is_settled = away_score is not None and home_score is not None
        bucket = by_season[season]
        bucket["rows"] += 1
        if is_settled:
            settled += 1
            bucket["settled"] += 1
            result = _number(row["result"])
            total = _number(row["total"])
            if result != home_score - away_score or total != home_score + away_score:
                outcome_mismatches += 1
        else:
            future += 1
        has_spread = _number(row["spread_line"]) is not None
        has_total = _number(row["total_line"]) is not None
        has_spread_prices = (
            _number(row["away_spread_odds"]) is not None
            and _number(row["home_spread_odds"]) is not None
        )
        has_total_prices = (
            _number(row["under_odds"]) is not None
            and _number(row["over_odds"]) is not None
        )
        prefix = "settled" if is_settled else "future"
        coverage[f"{prefix}_with_spread"] += has_spread
        coverage[f"{prefix}_with_total"] += has_total
        coverage[f"{prefix}_with_two_spread_prices"] += has_spread_prices
        coverage[f"{prefix}_with_two_total_prices"] += has_total_prices
        if has_spread:
            bucket["spread"] += 1
        if has_total:
            bucket["total"] += 1
        if has_spread_prices:
            bucket["spread_prices"] += 1
        if has_total_prices:
            bucket["total_prices"] += 1

    if duplicate_ids:
        raise ValueError(f"games.csv contains {duplicate_ids} duplicate game_id rows")
    if outcome_mismatches:
        raise ValueError(f"games.csv contains {outcome_mismatches} score/result inconsistencies")
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "rows": len(rows),
        "columns": len(header),
        "season_min": min(by_season) if by_season else None,
        "season_max": max(by_season) if by_season else None,
        "settled": settled,
        "future_or_unsettled": future,
        "coverage": dict(sorted(coverage.items())),
        "duplicate_game_ids": duplicate_ids,
        "score_result_inconsistencies": outcome_mismatches,
        "by_season": {str(k): dict(sorted(v.items())) for k, v in sorted(by_season.items())},
        "has_sportsbook_column": any("sportsbook" in name.lower() or name.lower() == "book" for name in header),
        "has_line_timestamp_column": any("time" in name.lower() or "captur" in name.lower() for name in header),
    }


def audit_closing(path: Path) -> dict:
    header, rows = read_csv(path, CLOSING_REQUIRED)
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    seasons = Counter()
    for row in rows:
        groups[(row["alt_game_id"], row["type"])].append(row)
        seasons[row["alt_game_id"].split("_", 1)[0]] += 1
    issues = Counter()
    for (_, market_type), group in groups.items():
        if len(group) != 2:
            issues["runner_cardinality"] += 1
            continue
        lines = [_number(row["line"]) for row in group]
        sides = {row["side"] for row in group}
        if market_type == "SPREAD" and (None in lines or not math.isclose(sum(lines), 0.0)):
            issues["spread_not_opposing"] += 1
        if market_type == "TOTAL" and (sides != {"Over", "Under"} or None in lines or lines[0] != lines[1]):
            issues["total_not_matching"] += 1
    if issues:
        raise ValueError(f"closing_lines.csv coherence failures: {dict(issues)}")
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "rows": len(rows),
        "game_ids": len({row["alt_game_id"] for row in rows}),
        "types": dict(sorted(Counter(row["type"] for row in rows).items())),
        "rows_with_odds": sum(_number(row["odds"]) is not None for row in rows),
        "season_rows": dict(sorted(seasons.items())),
        "coherence_issues": {},
        "has_sportsbook_column": any("sportsbook" in name.lower() or name.lower() == "book" for name in header),
        "has_line_timestamp_column": any("time" in name.lower() or "captur" in name.lower() for name in header),
    }


def audit_initial(path: Path) -> dict:
    header, rows = read_csv(path, INITIAL_REQUIRED)
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["about"], row["sportsbook"], row["type"])].append(row)
    issues = Counter()
    for (_, _, market_type), group in groups.items():
        if len(group) != 2:
            issues["runner_cardinality"] += 1
            continue
        lines = [_number(row["line"]) for row in group]
        sides = {row["side"] for row in group}
        if market_type == "SPREAD" and (None in lines or not math.isclose(sum(lines), 0.0)):
            issues["spread_not_opposing"] += 1
        if market_type == "TOTAL" and (sides != {"Over", "Under"} or None in lines or lines[0] != lines[1]):
            issues["total_not_matching"] += 1
    if issues:
        raise ValueError(f"initial_lines.csv coherence failures: {dict(issues)}")
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "rows": len(rows),
        "seasons": dict(sorted(Counter(row["season"] for row in rows).items())),
        "sportsbooks": dict(sorted(Counter(row["sportsbook"] for row in rows).items())),
        "types": dict(sorted(Counter(row["type"] for row in rows).items())),
        "coherence_issues": {},
        "has_odds_column": any("odds" in name.lower() or "price" in name.lower() for name in header),
        "has_line_timestamp_column": any("time" in name.lower() or "captur" in name.lower() for name in header),
    }


def verify_manifest(path: Path, manifest: dict, key: str) -> None:
    expected = manifest["files"][key]["sha256"]
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{key} source digest mismatch: {actual} != {expected}")


def run_audit(games: Path, closing: Path, initial: Path, manifest: dict) -> dict:
    for path, key in ((games, "games"), (closing, "closing_lines"), (initial, "initial_lines")):
        verify_manifest(path, manifest, key)
    return {
        "schema_version": 1,
        "source_class": SOURCE_CLASS,
        "source": manifest["source"],
        "files": {
            "games": audit_games(games),
            "closing_lines": audit_closing(closing),
            "initial_lines": audit_initial(initial),
        },
        "eligibility": {
            "generic_historical_outcome_baseline": True,
            "generic_historical_line_baseline": True,
            "fanduel_specific_calibration": False,
            "book_specific_clv": False,
            "precise_open_to_close_research": False,
        },
        "fail_closed_reasons": [
            "games.csv has no sportsbook identity",
            "games.csv has no line capture timestamp",
            "closing_lines.csv has no sportsbook identity or line capture timestamp",
            "initial_lines.csv covers one sportsbook and season and has no prices or timestamps",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--closing-lines", type=Path, required=True)
    parser.add_argument("--initial-lines", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    result = run_audit(args.games, args.closing_lines, args.initial_lines, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "source_class": result["source_class"],
        "games": result["files"]["games"]["rows"],
        "closing_lines": result["files"]["closing_lines"]["rows"],
        "initial_lines": result["files"]["initial_lines"]["rows"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
