#!/usr/bin/env python3
"""Manual, real-source demonstration of the sealed B0-vs-challenger receptions
connection (`receptions_challenger_snapshot.py`).

**Not part of any scheduled workflow.** This script is meant to be run by
hand for verification/evidence purposes only -- it is not imported by, and
does not import from, any `.github/workflows/` file, and it never seals
into the live board's own evidence stream. It reuses the exact real,
already-tested pieces the live receptions workflow itself uses (FanDuel
capture, official-inactive capture, roster binding, B0 projection/scoring)
plus the new, separate challenger/sealing connector -- nothing here is a
second, divergent implementation of any of those.

Run with: `PYTHONPATH=. python3 nfl/prospective/receptions_challenger_live_demo.py`

Writes a real, timestamped, sealed evidence snapshot to
`engineering/evidence/nfl_receptions_challenger_snapshot_<date>.json` when
run -- this is a durable, honest record of one real trial run, not a
constantly-refreshed live artifact.
"""
from __future__ import annotations

import csv
import io
import json
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import requests

from nfl.archive.sources import fanduel_nfl, official_nfl
from nfl.normalize.inactive_roster_binding import bind_report
from nfl.normalize.official_inactives import parse_report
from nfl.normalize.player_prop_markets import normalize_payload
from nfl.normalize.player_prop_roster_binding import bind_player_prop_candidate
from nfl.normalize.pregame_availability import evaluate_candidate
from nfl.prospective.receptions_challenger_snapshot import (
    build_challenger_snapshot_record,
    seal_challenger_snapshot,
)
from nfl.research.receptions_frozen_challenger import (
    FROZEN_NB_FIT,
    compare_b0_vs_frozen_challenger,
)
from nfl.research.receptions_shadow import current_b0_projection, score_shadow_candidate

ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_2026.csv"
)
HISTORY_CACHE_DIR = Path("/tmp/nfl_corpus_cache")
HISTORY_SEASONS = (2023, 2024, 2025)
MIN_HISTORY = 3
MAX_RECORDS = 10


def _effective_targets(row: dict) -> float:
    try:
        return max(float(row.get("targets") or 0), float(row.get("receptions") or 0))
    except (TypeError, ValueError):
        return 0.0


def _load_receiving_candidates(captured_at: str, event_limit: int = 16) -> list[dict]:
    records = fanduel_nfl.capture(event_limit=event_limit)
    candidates: list[dict] = []
    for record in records:
        if not record.artifact.endswith("tab_receiving-props"):
            continue
        try:
            payload = json.loads(record.body)
        except (ValueError, TypeError):
            continue
        result = normalize_payload(payload, captured_at=captured_at)
        candidates.extend(c for c in result["candidates"] if c["market"] == "receptions")
    return candidates


def _load_real_bound_inactive_reports(roster_rows: list[dict], captured_at: str) -> list[dict]:
    bound_reports = []
    for record in official_nfl.capture():
        if not record.artifact.startswith("inactive_report_"):
            continue
        parsed = parse_report(record.body)
        bound = bind_report(parsed, roster_rows, season=2026)
        bound["report_observed_at"] = captured_at
        bound_reports.append(bound)
    return bound_reports


def _load_prior_by_player_2025() -> dict[str, list[dict]]:
    prior_by_player: dict[str, list[dict]] = defaultdict(list)
    with (HISTORY_CACHE_DIR / "stats_player_week_2025.csv").open(
        encoding="utf-8-sig"
    ) as handle:
        for row in csv.DictReader(handle):
            player_id = str(row.get("player_id") or "").strip()
            if not player_id:
                continue
            try:
                targets = float(row.get("targets") or 0)
                receptions = float(row.get("receptions") or 0)
                week = int(row.get("week"))
            except (TypeError, ValueError):
                continue
            prior_by_player[player_id].append({
                "season": 2025, "week": week,
                "season_type": str(row.get("season_type") or ""),
                "targets": targets, "receptions": receptions,
                "team": str(row.get("team") or "").strip().upper(),
            })
    for rows in prior_by_player.values():
        rows.sort(key=lambda x: (x["season"], x["week"], x["season_type"]))
    return prior_by_player


def _load_pooled_residuals() -> list[float]:
    all_rows: list[dict] = []
    for season in HISTORY_SEASONS:
        with (HISTORY_CACHE_DIR / f"stats_player_week_{season}.csv").open(
            encoding="utf-8-sig"
        ) as handle:
            all_rows.extend(csv.DictReader(handle))

    role_rows = []
    for row in all_rows:
        if _effective_targets(row) <= 0:
            continue
        player_id = str(row.get("player_id") or "").strip()
        try:
            week = int(row.get("week"))
            season = int(row.get("season"))
            receptions = float(row.get("receptions"))
        except (TypeError, ValueError):
            continue
        if not player_id:
            continue
        role_rows.append({
            "player_id": player_id, "season": season, "week": week,
            "season_type": str(row.get("season_type") or ""),
            "effective_targets": _effective_targets(row), "receptions": receptions,
        })
    role_rows.sort(key=lambda r: (r["season"], r["week"], r["player_id"]))

    history_by_player: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
    residuals: list[float] = []
    for row in role_rows:
        appearances = history_by_player[row["player_id"]]
        if (
            len(appearances) >= MIN_HISTORY
            and statistics.fmean(a["effective_targets"] for a in appearances) > 0
        ):
            b0 = statistics.fmean(a["receptions"] for a in appearances)
            if row["season_type"] == "REG" and row["season"] in (2024, 2025):
                residuals.append(row["receptions"] - b0)
        appearances.append(row)
    return residuals


def run(*, code_sha: str, slate_date: str | None = None) -> dict:
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    slate_date = slate_date or captured_at[:10]

    candidates = _load_receiving_candidates(captured_at)
    roster_rows = list(
        csv.DictReader(
            io.StringIO(requests.get(ROSTER_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).text)
        )
    )
    bound_reports = _load_real_bound_inactive_reports(roster_rows, captured_at)
    prior_by_player = _load_prior_by_player_2025()
    residuals = _load_pooled_residuals()
    challenger_model_version = f"NEGATIVE_BINOMIAL_POOLED_V1_alpha_{FROZEN_NB_FIT['alpha']}"

    sealed_records = []
    for candidate in candidates:
        bound = bind_player_prop_candidate(candidate, roster_rows, season=2026)
        if bound["binding_status"] != "BOUND":
            continue
        availability = evaluate_candidate(bound, bound_reports)
        try:
            projection_info = current_b0_projection(prior_by_player.get(bound["gsis_id"], []))
        except ValueError:
            continue
        projection = float(projection_info["projection"])
        b0_score = score_shadow_candidate(
            projection=projection, line=candidate["line"],
            over_odds=candidate["over_odds"], under_odds=candidate["under_odds"],
            residuals=residuals,
        )
        comparison = compare_b0_vs_frozen_challenger(
            projection=projection, line=candidate["line"],
            b0_over=b0_score["model_over_probability"], b0_under=b0_score["model_under_probability"],
            over_price=candidate["over_odds"], under_price=candidate["under_odds"],
        )
        decision = "SHADOW_ONLY" if availability["availability_gate_pass"] else "QUARANTINED"
        sealed_records.append(build_challenger_snapshot_record(
            event_id=candidate["event_id"], market_id=candidate["market_id"],
            gsis_id=bound["gsis_id"], player_name=candidate["player_name"], team=bound["team"],
            event_open_date=candidate["event_open_date"], line=candidate["line"],
            over_odds=candidate["over_odds"], under_odds=candidate["under_odds"],
            captured_at=captured_at, availability_status=availability["availability_status"],
            decision_status=decision, b0_score=b0_score, challenger_comparison=comparison,
            challenger_model_version=challenger_model_version,
            source_vintage="nflverse_2023_2024_2025_weekly_stats_verified",
            feature_cutoff="2025_REG_final_week",
        ))
        if len(sealed_records) >= MAX_RECORDS:
            break

    return seal_challenger_snapshot(
        sealed_records, slate_date=slate_date, code_sha=code_sha,
        source_vintage="nflverse_2023_2024_2025_weekly_stats_verified", sealed_at=captured_at,
    )


if __name__ == "__main__":
    import sys

    sealed_snapshot = run(code_sha=sys.argv[1] if len(sys.argv) > 1 else "MANUAL_RUN")
    out_path = Path(f"engineering/evidence/nfl_receptions_challenger_snapshot_{sealed_snapshot['slate_date']}.json")
    out_path.write_text(json.dumps(sealed_snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Sealed {sealed_snapshot['record_count']} real challenger-vs-B0 records to {out_path}")
    print(f"snapshot_sha256={sealed_snapshot['snapshot_sha256']}")
