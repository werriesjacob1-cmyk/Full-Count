#!/usr/bin/env python3
"""Reproduce the NFL game-market C3 challenger from pinned local inputs.

Like `game_market_c2_research.py`, this runner is offline-input-only: every
input file's exact byte count and SHA-256 are checked before anything is
parsed, and no network access happens here. The schedule/closing-market
source and the C2 team-offense/PBP-play sources are the exact same pins
`game_market_c2_research.py` uses, reused unchanged. The two new inputs
(`qb_player_stats_week.csv`, `injuries_week.csv`) are produced by
`game_market_c3_data_prep.py`, which *does* fetch nflverse releases over the
network (a disclosed, digest-checked departure -- see that module's
docstring), for the same reason C2's own data prep does: no pinned local
source for QB-continuity/availability history exists anywhere in this
repository.

IMPORTANT interpretive caveat (Issue #91 comment 5734109184, "CRITICAL
SCIENTIFIC CORRECTION BEFORE C2-TOTALS/C3"): C3 was selected as a follow-up
*because* C2's already-inspected 2020-2025 result showed a specific margin
weakness. That means 2020-2025 -- including the `held_2023_2025` partition
this runner reports below, for direct structural comparability with C2's
own report -- is POST-SELECTION EXPLORATORY CHARACTERIZATION for C3, not a
fresh, untouched confirmatory promotion holdout. `status` below is therefore
never `RESEARCH_CHALLENGER_PROMOTED`; a passing predeclared gate on this
population is reported as `RESEARCH_CHALLENGER_GATE_PASSED_EXPLORATORY_ONLY`
(promotion requires the prospective Week 3+ 2026 confirmation path that
comment names, which is out of this task's scope), and a failing gate is
`RESEARCH_CHALLENGER_REJECTED` exactly like C2's own reporting.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nfl.research.game_market_b0 import build_b0_predictions
from nfl.research.game_market_b0_research import (
    PINNED_SCHEDULE_SOURCE,
    load_pinned_historical_rows,
    sha256_file,
)
from nfl.research.game_market_c2_features import (
    MARGIN_FEATURES as C2_MARGIN_FEATURES,
    TOTAL_FEATURES as C2_TOTAL_FEATURES,
    build_c2_game_rows,
    filter_team_offense_rows_for_negative_value_bug,
)
from nfl.research.game_market_c2_model import (
    fit_c2_model,
    apply_c2,
)
from nfl.research.game_market_c3_features import (
    INJURY_REPORT_DUPLICATE_STATUS_UPDATE_NOTE,
    INJURY_REPORT_PRE_2016_PROBABLE_VOCABULARY_NOTE,
    NEW_MARGIN_FEATURES,
    QB_PLAYER_STAT_FRANCHISE_RELOCATION_NORMALIZATION_NOTE,
    QB_PLAYER_STAT_MISSING_TEAM_IDENTITY_BUG_NOTE,
    build_c3_game_rows,
    filter_injury_rows_keep_latest_status_update,
    filter_injury_rows_to_known_status_vocabulary,
    filter_qb_player_stat_rows_for_missing_team_identity,
    normalize_qb_player_stat_team_identity,
    summarize_c3_exclusions,
)
from nfl.research.game_market_c3_model import (
    C3_NAME,
    DEV_END,
    DEV_START,
    HELD_END,
    HELD_START,
    VALID_END,
    VALID_START,
    evaluate_c3,
    evaluate_promotion_gate,
    fit_c3_model,
    apply_c3,
    pair_predictions,
)
from nfl.research.injury_availability_features import build_prior_starter_availability_features
from nfl.research.qb_continuity_features import build_prior_qb_continuity_features
from nfl.research.scoring_prior_features import build_prior_scoring_features

ALL_C3_MARGIN_FEATURES = tuple(C2_MARGIN_FEATURES) + tuple(NEW_MARGIN_FEATURES)

# Reused unchanged from game_market_c2_research.py.
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
PINNED_QB_PLAYER_STATS_SOURCE = {
    "derived_via": "nfl/research/game_market_c3_data_prep.py",
    "upstream": "nflverse/nflverse-data release 'stats_player', stats_player_week_<season>.csv, seasons 1999-2025 (QB, REG rows only)",
    "bytes": 874431,
    "sha256": "0f0803ef506c2b34bd61737fae15cacc6af55d5c87b510ba5685caddeea9dfbe",
    "point_in_time_feature_eligible": True,
}
PINNED_INJURY_REPORT_SOURCE = {
    "derived_via": "nfl/research/game_market_c3_data_prep.py",
    "upstream": "nflverse/nflverse-data release 'injuries', injuries_<season>.csv, seasons 2009-2025 (REG rows only)",
    "bytes": 4807542,
    "sha256": "ad9c8e29cf30dc63aca23f11075277ca29229c95874db816537fddbad6234041",
    "point_in_time_feature_eligible": True,
}


class GameMarketC3ResearchError(ValueError):
    pass


def _verify_digest(path: Path, expected: dict[str, Any], *, label: str) -> None:
    observed_bytes = path.stat().st_size
    if observed_bytes != expected["bytes"]:
        raise GameMarketC3ResearchError(f"{label} byte drift: {observed_bytes} != {expected['bytes']}")
    observed_sha = sha256_file(path)
    if observed_sha != expected["sha256"]:
        raise GameMarketC3ResearchError(f"{label} SHA-256 drift: {observed_sha} != {expected['sha256']}")


def load_pinned_team_offense_rows(path: Path) -> list[dict[str, Any]]:
    _verify_digest(path, PINNED_TEAM_OFFENSE_SOURCE, label="team_offense_week.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_pinned_pbp_play_rows(path: Path) -> list[dict[str, Any]]:
    _verify_digest(path, PINNED_PBP_PLAY_SOURCE, label="pbp_plays_filtered.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_pinned_qb_player_stats_rows(path: Path) -> list[dict[str, Any]]:
    _verify_digest(path, PINNED_QB_PLAYER_STATS_SOURCE, label="qb_player_stats_week.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_pinned_injury_rows(path: Path) -> list[dict[str, Any]]:
    _verify_digest(path, PINNED_INJURY_REPORT_SOURCE, label="injuries_week.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_research(
    schedules_csv: Path,
    team_offense_csv: Path,
    pbp_plays_csv: Path,
    qb_player_stats_csv: Path,
    injuries_csv: Path,
) -> dict[str, Any]:
    scoring_rows, market_rows, source_counts = load_pinned_historical_rows(schedules_csv)
    team_offense_rows_raw = load_pinned_team_offense_rows(team_offense_csv)
    pbp_play_rows = load_pinned_pbp_play_rows(pbp_plays_csv)
    qb_player_stats_rows = load_pinned_qb_player_stats_rows(qb_player_stats_csv)
    injury_rows = load_pinned_injury_rows(injuries_csv)

    team_offense_rows, excluded_offense_rows = filter_team_offense_rows_for_negative_value_bug(
        team_offense_rows_raw
    )

    # --- C2's own base rows and its own (unmodified) fitted margin control ---
    c2_rows = build_c2_game_rows(
        scoring_rows, team_offense_rows, pbp_play_rows, rolling_window=5, min_prior_games=3
    )
    c2_model = fit_c2_model(
        c2_rows, margin_features=C2_MARGIN_FEATURES, total_features=C2_TOTAL_FEATURES
    )
    c2_rows_scored = apply_c2(c2_rows, c2_model)

    # --- QB-continuity + availability substrate (PR #133, reused unchanged) ---
    qb_player_stats_rows, unresolved_franchise_rows = normalize_qb_player_stat_team_identity(
        qb_player_stats_rows, scoring_rows
    )
    qb_player_stats_rows, excluded_qb_stat_rows = filter_qb_player_stat_rows_for_missing_team_identity(
        qb_player_stats_rows
    )
    qb_continuity_rows = build_prior_qb_continuity_features(qb_player_stats_rows, rolling_window=5)
    injury_rows, excluded_vocabulary_injury_rows = filter_injury_rows_to_known_status_vocabulary(injury_rows)
    injury_rows, dropped_injury_status_updates = filter_injury_rows_keep_latest_status_update(injury_rows)
    availability_rows = build_prior_starter_availability_features(injury_rows, qb_continuity_rows)

    # --- C3 join layer ---
    c3_rows = build_c3_game_rows(c2_rows_scored, qb_continuity_rows, availability_rows)
    exclusions = summarize_c3_exclusions(c3_rows)

    c3_model = fit_c3_model(c3_rows, margin_features=ALL_C3_MARGIN_FEATURES)
    c3_rows_scored = apply_c3(c3_rows, c3_model)

    # --- B0 control, exactly as C2's own runner builds it ---
    scoring_features = build_prior_scoring_features(scoring_rows, rolling_window=5)
    b0_predictions = build_b0_predictions(scoring_features, min_prior_games=3)

    paired = pair_predictions(c3_rows_scored, b0_predictions)
    evaluation = evaluate_c3(paired)
    gate = evaluate_promotion_gate(evaluation)

    c2_eligible_ids = {r["game_id"] for r in c2_rows if r["eligibility"] == "ELIGIBLE"}
    c3_eligible_ids = {r["game_id"] for r in c3_rows if r["c3_eligibility"] == "ELIGIBLE"}

    dev_c2_eligible = sum(
        1 for r in c2_rows
        if r["eligibility"] == "ELIGIBLE" and DEV_START <= r["season"] <= DEV_END
    )
    dev_c3_eligible = sum(
        1 for r in c3_rows
        if r["c3_eligibility"] == "ELIGIBLE" and DEV_START <= r["season"] <= DEV_END
    )
    dev_seasons_present = sorted({
        r["season"] for r in c3_rows
        if r["c3_eligibility"] == "ELIGIBLE" and DEV_START <= r["season"] <= DEV_END
    })

    if gate["promotion_eligible"]:
        status = "RESEARCH_CHALLENGER_GATE_PASSED_EXPLORATORY_ONLY_NOT_A_PROSPECTIVE_CONFIRMATION"
    else:
        status = "RESEARCH_CHALLENGER_REJECTED"

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "challenger_name": C3_NAME,
        "named_hypothesis": (
            "C2's margin weakness is partly due to missing starter/regime/"
            "availability information; adding QB-continuity tenure and "
            "starter-availability features to C2's existing 8 margin "
            "features closes some of that specific gap."
        ),
        "post_selection_evidence_caveat": (
            "C3 was selected as a follow-up BECAUSE C2's already-inspected "
            "2020-2025 result showed this specific margin weakness (driven "
            "largely by 2022). Evaluating C3's margin fix against that same "
            "validation_2020_2022/held_2023_2025 population is therefore "
            "NOT an independent held-out test in the usual sense -- the "
            "hypothesis was informed by inspecting results on this exact "
            "population -- see Issue #91 comment 5734109184. Whatever this "
            "report's gate verdict is below, it is an EXPLORATORY/"
            "DIAGNOSTIC RESULT ON A PREVIOUSLY-INSPECTED POPULATION, not a "
            "prospective confirmation, and does not by itself prove or "
            "disprove the named hypothesis. Genuine prospective confirmation "
            "would require evaluating on new data not yet used to select or "
            "diagnose this hypothesis -- real future games going forward, "
            "under the same PREDICT -> FREEZE -> GRADE discipline used "
            "elsewhere in this project (e.g. the Week 3+ 2026 live evidence "
            "path) -- which is out of this task's scope."
        ),
        "sources": {
            "schedule": dict(PINNED_SCHEDULE_SOURCE),
            "team_offense_week": dict(PINNED_TEAM_OFFENSE_SOURCE),
            "pbp_plays_filtered": dict(PINNED_PBP_PLAY_SOURCE),
            "qb_player_stats_week": dict(PINNED_QB_PLAYER_STATS_SOURCE),
            "injuries_week": dict(PINNED_INJURY_REPORT_SOURCE),
        },
        "source_counts": source_counts,
        "known_data_issues": {
            "qb_player_stat_rows_excluded_missing_team_identity": len(excluded_qb_stat_rows),
            "qb_player_stat_rows_excluded_detail": [
                {k: row.get(k) for k in ("player_id", "season", "week", "attempts")}
                for row in excluded_qb_stat_rows
            ],
            "qb_player_stat_missing_team_identity_bug_note": QB_PLAYER_STAT_MISSING_TEAM_IDENTITY_BUG_NOTE,
            "qb_player_stat_rows_unresolved_franchise_identity": len(unresolved_franchise_rows),
            "qb_player_stat_franchise_relocation_normalization_note": QB_PLAYER_STAT_FRANCHISE_RELOCATION_NORMALIZATION_NOTE,
            "injury_rows_resolved_duplicate_status_updates": len(dropped_injury_status_updates),
            "injury_rows_resolved_duplicate_status_updates_detail": [
                {k: row.get(k) for k in ("season", "week", "team", "gsis_id", "report_status", "date_modified")}
                for row in dropped_injury_status_updates
            ],
            "injury_report_duplicate_status_update_note": INJURY_REPORT_DUPLICATE_STATUS_UPDATE_NOTE,
            "injury_rows_excluded_unknown_status_vocabulary": len(excluded_vocabulary_injury_rows),
            "injury_rows_excluded_unknown_status_vocabulary_by_status": {
                status: sum(
                    1 for row in excluded_vocabulary_injury_rows
                    if str(row.get("report_status") or "").strip().upper() == status
                )
                for status in sorted({
                    str(row.get("report_status") or "").strip().upper()
                    for row in excluded_vocabulary_injury_rows
                })
            },
            "injury_report_pre_2016_probable_vocabulary_note": INJURY_REPORT_PRE_2016_PROBABLE_VOCABULARY_NOTE,
        },
        "population": {
            "c2_eligible_games": len(c2_eligible_ids),
            "c3_eligible_games": len(c3_eligible_ids),
            "c3_eligible_as_share_of_c2_eligible": (
                len(c3_eligible_ids) / len(c2_eligible_ids) if c2_eligible_ids else None
            ),
            "development_2000_2019_c2_eligible_games": dev_c2_eligible,
            "development_2000_2019_c3_eligible_games": dev_c3_eligible,
            "development_2000_2019_c3_eligible_as_share_of_c2_eligible": (
                dev_c3_eligible / dev_c2_eligible if dev_c2_eligible else None
            ),
            "development_2000_2019_c3_eligible_seasons_present": dev_seasons_present,
            "population_note": (
                "C3's development population is smaller than C2's own "
                "development_2000_2019 population because "
                "injury_availability_features.py only covers seasons 2009+ "
                "(SEASON_NOT_COVERED_BY_SOURCE for 2000-2008); this mirrors "
                "C2's own honest reporting of its 9-game exclusion versus B0."
            ),
        },
        "c3_exclusion_reasons": {reason: len(ids) for reason, ids in exclusions.items()},
        "c3_exclusion_game_ids_sample": {
            reason: ids[:25] for reason, ids in exclusions.items()
        },
        "margin_features": {
            "c2_original_8": list(C2_MARGIN_FEATURES),
            "c3_new_3": list(NEW_MARGIN_FEATURES),
            "c3_all_11": list(ALL_C3_MARGIN_FEATURES),
        },
        "c2_control_model": {
            "ridge_lambda": c2_model["ridge_lambda"],
            "development_games": c2_model["development_games"],
            "margin_coefficients": c2_model["margin_model"]["coefficients"],
            "margin_training_mae": c2_model["margin_model"]["training_mae"],
        },
        "c3_model": {
            "ridge_lambda": c3_model["ridge_lambda"],
            "development_games": c3_model["development_games"],
            "margin_coefficients": c3_model["margin_model"]["coefficients"],
            "margin_training_mae": c3_model["margin_model"]["training_mae"],
        },
        "evaluation_vs_b0_and_c2": evaluation,
        "promotion_gate": gate,
        "status": status,
        "research_limitations": [
            "Closing lines are never used to fit any of B0/C2/C3.",
            "qb_player_stats_week.csv/injuries_week.csv are derived from real nflverse releases by game_market_c3_data_prep.py, digest-checked on fetch; see that module's docstring.",
            "qb_tenure_starts and games_since_qb_change are numerically identical in this dataset (see game_market_c3_features.QB_TENURE_GAMES_SINCE_CHANGE_DUPLICATE_NOTE); C3 retains both per the task's literal feature list.",
            "'Starter' is a usage proxy (most pass attempts that week), not a roster/depth-chart designation, and covers QB only -- see qb_continuity_features.py/injury_availability_features.py's own docstrings.",
            "The weekly injury report reflects the last FILED Wed-Fri practice/game status, not the literal final inactive list.",
            "C3 has no probability calibration, selector, prospective capture, or production eligibility. This evaluation does not establish sportsbook profitability.",
            "See 'post_selection_evidence_caveat' above: this report's validation/held numbers are post-selection exploratory characterization, not a fresh confirmatory promotion holdout.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedules-csv", type=Path, required=True)
    parser.add_argument("--team-offense-csv", type=Path, required=True)
    parser.add_argument("--pbp-plays-csv", type=Path, required=True)
    parser.add_argument("--qb-player-stats-csv", type=Path, required=True)
    parser.add_argument("--injuries-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_research(
        args.schedules_csv, args.team_offense_csv, args.pbp_plays_csv,
        args.qb_player_stats_csv, args.injuries_csv,
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "population": report["population"],
        "promotion_gate": report["promotion_gate"],
        "held": report["evaluation_vs_b0_and_c2"]["held_2023_2025"],
        "validation": report["evaluation_vs_b0_and_c2"]["validation_2020_2022"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
