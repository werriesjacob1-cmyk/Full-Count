#!/usr/bin/env python3
"""Reproduce the NFL game-market C2 challenger from pinned local inputs.

Like `game_market_b0_research.py` and `game_market_c1_research.py`, this
runner is offline-input-only: every input file's exact byte count and
SHA-256 are checked before anything is parsed, and no network access
happens here. The schedule/closing-market source is the exact same pin B0/
C1 use (`PINNED_SCHEDULE_SOURCE`, reused unchanged). The two new inputs
(`team_offense_week.csv`, `pbp_plays_filtered.csv`) are produced by
`game_market_c2_data_prep.py`, which *does* fetch nflverse releases over the
network (a disclosed, digest-checked departure -- see that module's
docstring) because no pinned local source for team/defense/PBP-tendency
history exists anywhere in this repository.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nfl.research.game_market_b0 import (
    build_b0_predictions,
    evaluate_against_closing_market,
)
from nfl.research.game_market_b0_research import (
    PINNED_SCHEDULE_SOURCE,
    load_pinned_historical_rows,
    sha256_file,
)
from nfl.research.game_market_c2_directional import (
    build_market_spread_index,
    compute_equal_volume_directional_accuracy,
)
from nfl.research.game_market_c2_features import (
    MARGIN_FEATURES,
    OL_CONTINUITY_EXCLUSION_REASON,
    TOTAL_FEATURES,
    build_c2_game_rows,
    filter_team_offense_rows_for_negative_value_bug,
)
from nfl.research.game_market_c2_model import (
    C2_NAME,
    HELD_END,
    HELD_START,
    VALID_END,
    VALID_START,
    evaluate_c2,
    evaluate_promotion_gate,
    fit_c2_model,
    apply_c2,
    pair_with_b0,
)
from nfl.research.scoring_prior_features import build_prior_scoring_features


PINNED_TEAM_OFFENSE_SOURCE = {
    "derived_via": "nfl/research/game_market_c2_data_prep.py",
    "upstream": "nflverse/nflverse-data release 'pbp', play_by_play_<season>.csv.gz, seasons 1999-2025",
    "bytes": 1031542,
    "sha256": "cbf47080fa575f2f40cfb0ca0fd2f7fc52d708264aca21020cea33ddc0377d53",
    "point_in_time_feature_eligible": True,
}
PINNED_PBP_PLAY_SOURCE = {
    "derived_via": "nfl/research/game_market_c2_data_prep.py",
    "upstream": "nflverse/nflverse-data release 'pbp', play_by_play_<season>.csv.gz, seasons 1999-2025",
    "bytes": 77094861,
    "sha256": "1115f9979d55fcdbac76e8a59823860145884b56b6608d5581a60de717cd8836",
    "point_in_time_feature_eligible": True,
}


class GameMarketC2ResearchError(ValueError):
    pass


def _verify_digest(path: Path, expected: dict[str, Any], *, label: str) -> None:
    observed_bytes = path.stat().st_size
    if observed_bytes != expected["bytes"]:
        raise GameMarketC2ResearchError(
            f"{label} byte drift: {observed_bytes} != {expected['bytes']}"
        )
    observed_sha = sha256_file(path)
    if observed_sha != expected["sha256"]:
        raise GameMarketC2ResearchError(
            f"{label} SHA-256 drift: {observed_sha} != {expected['sha256']}"
        )


def load_pinned_team_offense_rows(path: Path) -> list[dict[str, Any]]:
    _verify_digest(path, PINNED_TEAM_OFFENSE_SOURCE, label="team_offense_week.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_pinned_pbp_play_rows(path: Path) -> list[dict[str, Any]]:
    _verify_digest(path, PINNED_PBP_PLAY_SOURCE, label="pbp_plays_filtered.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _b0_style_rows(c2_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reshape ELIGIBLE C2 rows into B0's prediction schema for market reuse."""
    output = []
    for row in c2_rows:
        if row.get("eligibility") != "ELIGIBLE" or row.get("c2_margin") is None:
            continue
        output.append({
            "game_id": row["game_id"],
            "season": row["season"],
            "week": row["week"],
            "game_type": row["game_type"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "target_final_status": row["target_final_status"],
            "eligibility": "ELIGIBLE",
            "predicted_home_margin": row["c2_margin"],
            "predicted_total": row["c2_total"],
        })
    return output


def run_research(schedules_csv: Path, team_offense_csv: Path, pbp_plays_csv: Path) -> dict[str, Any]:
    scoring_rows, market_rows, source_counts = load_pinned_historical_rows(schedules_csv)
    team_offense_rows_raw = load_pinned_team_offense_rows(team_offense_csv)
    pbp_play_rows = load_pinned_pbp_play_rows(pbp_plays_csv)

    team_offense_rows, excluded_offense_rows = filter_team_offense_rows_for_negative_value_bug(
        team_offense_rows_raw
    )
    excluded_game_ids = sorted({row["game_id"] for row in excluded_offense_rows})

    c2_rows = build_c2_game_rows(
        scoring_rows, team_offense_rows, pbp_play_rows, rolling_window=5, min_prior_games=3
    )
    model = fit_c2_model(
        c2_rows, margin_features=MARGIN_FEATURES, total_features=TOTAL_FEATURES
    )
    c2_rows = apply_c2(c2_rows, model)

    scoring_features = build_prior_scoring_features(scoring_rows, rolling_window=5)
    b0_predictions = build_b0_predictions(scoring_features, min_prior_games=3)

    b0_eligible_ids = {
        row["game_id"] for row in b0_predictions
        if row["eligibility"] == "ELIGIBLE" and row["target_final_status"] == "FINAL"
    }
    c2_eligible_ids = {row["game_id"] for row in c2_rows if row["eligibility"] == "ELIGIBLE"}
    only_b0 = sorted(b0_eligible_ids - c2_eligible_ids)
    only_c2 = sorted(c2_eligible_ids - b0_eligible_ids)

    paired = pair_with_b0(c2_rows, b0_predictions)
    evaluation = evaluate_c2(paired)
    gate = evaluate_promotion_gate(evaluation)

    market_game_ids = {row["game_id"] for row in market_rows}
    b0_market_predictions = [row for row in b0_predictions if row["game_id"] in market_game_ids]
    b0_vs_closing = evaluate_against_closing_market(b0_market_predictions, market_rows)

    c2_style_predictions = _b0_style_rows(c2_rows)
    c2_market_predictions = [row for row in c2_style_predictions if row["game_id"] in market_game_ids]
    c2_vs_closing = evaluate_against_closing_market(c2_market_predictions, market_rows)

    market_spread_index = build_market_spread_index(market_rows)
    directional = {}
    for name, (start, end) in (
        ("validation_2020_2022", (VALID_START, VALID_END)),
        ("held_2023_2025", (HELD_START, HELD_END)),
    ):
        subset = [row for row in paired if start <= row["season"] <= end]
        directional[name] = compute_equal_volume_directional_accuracy(subset, market_spread_index)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "challenger_name": C2_NAME,
        "sources": {
            "schedule": dict(PINNED_SCHEDULE_SOURCE),
            "team_offense_week": dict(PINNED_TEAM_OFFENSE_SOURCE),
            "pbp_plays_filtered": dict(PINNED_PBP_PLAY_SOURCE),
        },
        "source_counts": source_counts,
        "known_data_issues": {
            "team_offense_negative_value_rows_excluded": len(excluded_offense_rows),
            "team_offense_negative_value_games_excluded": excluded_game_ids,
            "ol_continuity_prior_excluded_reason": OL_CONTINUITY_EXCLUSION_REASON,
        },
        "population": {
            "b0_eligible_final_games": len(b0_eligible_ids),
            "c2_eligible_games": len(c2_eligible_ids),
            "common_paired_games": len(paired),
            "eligible_in_b0_only": only_b0,
            "eligible_in_c2_only": only_c2,
        },
        "margin_features": list(MARGIN_FEATURES),
        "total_features": list(TOTAL_FEATURES),
        "model": {
            "ridge_lambda": model["ridge_lambda"],
            "development_games": model["development_games"],
            "margin_coefficients": model["margin_model"]["coefficients"],
            "margin_training_mae": model["margin_model"]["training_mae"],
            "total_coefficients": model["total_model"]["coefficients"],
            "total_training_mae": model["total_model"]["training_mae"],
        },
        "evaluation_vs_b0": evaluation,
        "promotion_gate": gate,
        "evaluation_vs_closing_market": {
            "b0": b0_vs_closing,
            "c2": c2_vs_closing,
        },
        "equal_volume_directional_accuracy_vs_b0": directional,
        "status": (
            "RESEARCH_CHALLENGER_GATE_PASSED_PENDING_REVIEW"
            if gate["promotion_eligible"]
            else "RESEARCH_CHALLENGER_REJECTED"
        ),
        "research_limitations": [
            "Closing lines are retrospective Pro-Football-Reference controls via nflverse, not timestamped sportsbook observations, and are never used as a C2 model input.",
            "team_offense_week.csv/pbp_plays_filtered.csv are derived from nflverse play-by-play by game_market_c2_data_prep.py, not pinned raw upstream files; see that module's docstring for the exact, digest-checked fetch/aggregation and its disclosed departure from B0/C1's offline-only rule.",
            "attempts/passing_epa follow nflfastR's own pass_attempt convention, which marks a sacked dropback as a pass attempt (differs from a traditional box-score attempts count).",
            "ol_continuity_prior (PFR snap counts) is excluded from the fitted feature set; see known_data_issues.ol_continuity_prior_excluded_reason.",
            "A handful of real team-games have negative passing_yards/rushing_yards that team_prior_features/defense_prior_features reject outright; those games are excluded and listed in known_data_issues, not clipped or fabricated.",
            "Three 1999-2000 games have zero rows in nflverse's own play-by-play release and are therefore ineligible for C2 despite being B0-eligible; see population.eligible_in_b0_only.",
            "C2 has no probability calibration, selector, prospective capture, or production eligibility. This evaluation does not establish sportsbook profitability.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedules-csv", type=Path, required=True)
    parser.add_argument("--team-offense-csv", type=Path, required=True)
    parser.add_argument("--pbp-plays-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_research(args.schedules_csv, args.team_offense_csv, args.pbp_plays_csv)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "promotion_gate": report["promotion_gate"],
        "held": report["evaluation_vs_b0"]["held_2023_2025"],
        "validation": report["evaluation_vs_b0"]["validation_2020_2022"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
