#!/usr/bin/env python3
"""Totals-only, independently-gated re-evaluation of the NFL game-market C2 challenger.

Background (see the 2026-09-18 `ENGINEERING_HANDOFF.md` entry "NFL C2: first
feature-based game-market challenger, REJECTED"): C2 fits two *separate*
ridge models from the same 8 strictly-prior features -- one on
`actual_margin` (`MARGIN_FEATURES`), one on `actual_total`
(`TOTAL_FEATURES`) -- and its predeclared combined promotion gate
(`game_market_c2_model.PROMOTION_GATE_DESCRIPTION`) required BOTH axes to
clear predeclared bars. Margin failed (held bootstrap 97.5th percentile
+0.151, crosses zero); total passed cleanly by every measure C2 itself
computed: held total MAE improved (10.719 -> 10.436), its bootstrap interval
sat entirely below zero in both validation ([-0.438, -0.017]) and held
([-0.475, -0.094]), and the improvement held in every one of the six
2020-2025 seasons individually. The combined AND-gate buried that
independent, repeatable total finding inside one failed margin bootstrap.

This module does not build a new model. `game_market_c2_model.fit_c2_model`
already fits the margin and total ridge models independently (different
target column, different feature set, same development partition, same
lambda) and `apply_c2`/`pair_with_b0` already attach both `c2_total` and
`c2_margin` predictions to the same paired rows -- confirmed by reading
`game_market_c2_model.py` and `game_market_c2_features.py` in full before
writing a single line here. There is therefore nothing to refit: this module
imports C2's existing feature assembly (`game_market_c2_features`), ridge
fit (`game_market_c2_model.fit_c2_model`/`apply_c2`), and B0-pairing logic
(`game_market_c2_model.pair_with_b0`) verbatim, and only adds a NEW,
total-only promotion gate plus the additional total-specific evaluations the
combined C2 report did not isolate on their own (equal-volume total
directional accuracy, season-stability robustness, and the total slice of
the retrospective closing-market comparison). No feature is added, removed,
or reweighted relative to C2's existing `MARGIN_FEATURES`/`TOTAL_FEATURES`.

Promotion gate discipline: `TOTAL_PROMOTION_GATE_DESCRIPTION` is declared as
a module-level constant immediately below, before any function in this file
that reads a held-partition number, mirroring `game_market_c2_model.py`'s
own gate-before-evaluation ordering. Its four conditions are the direct
total-only analog of C2's own combined gate (drop the margin legs, keep the
total legs) plus one new season-stability robustness check the combined
gate never isolated. The stability thresholds ("at least 5 of 6 seasons",
"holds under leave-one-held-season-out") are picked as generically
defensible bars for a six-season sample with unavoidable season-to-season
noise, not reverse-fitted to this specific dataset's realized 6-of-6 season
count -- that count is reported honestly either way once computed below.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from nfl.research.game_market_b0 import (
    REQUIRED_CLOSING_CONTROL,
    build_b0_predictions,
    evaluate_against_closing_market,
)
from nfl.research.game_market_b0_research import (
    PINNED_SCHEDULE_SOURCE,
    load_pinned_historical_rows,
)
from nfl.research.game_market_c2_features import (
    MARGIN_FEATURES,
    TOTAL_FEATURES,
    build_c2_game_rows,
    filter_team_offense_rows_for_negative_value_bug,
)
from nfl.research.game_market_c2_model import (
    C2_NAME,
    DEV_END,
    DEV_START,
    HELD_END,
    HELD_START,
    VALID_END,
    VALID_START,
    apply_c2,
    evaluate_c2,
    fit_c2_model,
    pair_with_b0,
)
from nfl.research.game_market_c2_research import (
    PINNED_PBP_PLAY_SOURCE,
    PINNED_TEAM_OFFENSE_SOURCE,
    _b0_style_rows,
    load_pinned_pbp_play_rows,
    load_pinned_team_offense_rows,
)
from nfl.research.scoring_prior_features import build_prior_scoring_features

TOTALS_ONLY_CHALLENGER_NAME = f"{C2_NAME}_TOTALS_ONLY"

# Seasons the season-stability leg of the gate is judged over. This is
# exactly the `season_by_season_2020_2025` block `evaluate_c2` already
# computes (validation_2020_2022 + held_2023_2025 seasons); nothing new is
# derived here.
STABILITY_SEASONS = (2020, 2021, 2022, 2023, 2024, 2025)
STABILITY_MIN_FAVORABLE_SEASONS = 5  # of 6 -- "most", not "every", predeclared as a
# generic majority-with-one-exception bar for a six-season sample, independent
# of whatever the realized favorable-season count turns out to be.

TOTAL_PROMOTION_GATE_DESCRIPTION = (
    "held total MAE(C2) < held total MAE(B0) (strict win) AND the paired "
    "bootstrap 97.5th percentile of (C2-B0) held total MAE delta is < 0 "
    "(not attributable to chance) AND validation total MAE(C2) <= validation "
    "total MAE(B0) (directionally consistent second check, not just a "
    "held-only fluke) AND season stability: at least "
    f"{STABILITY_MIN_FAVORABLE_SEASONS} of the {len(STABILITY_SEASONS)} "
    "seasons 2020-2025 individually show a negative (C2-B0) total MAE delta "
    "AND excluding any single held season (2023, 2024, or 2025) individually "
    "still leaves the remaining held total MAE delta negative (the held "
    "result is not solely attributable to one season). This gate is "
    "evaluated purely on total-axis numbers; it never reads C2's margin "
    "MAE, margin bootstrap, or margin promotion-gate outcome. All "
    "conditions must hold for RESEARCH_CHALLENGER_PROMOTION_ELIGIBLE; any "
    "failure yields RESEARCH_CHALLENGER_REJECTED, reported with the "
    "specific unmet condition(s), with the same honesty regardless of which "
    "way the result comes out."
)


class GameMarketC2TotalsOnlyError(ValueError):
    """Raised on a malformed input to the totals-only evaluation."""


def _total_mae(rows: list[Mapping[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return statistics.fmean(abs(float(r[key]) - float(r["actual_total"])) for r in rows)


def compute_held_leave_one_season_out(
    paired_rows: Iterable[Mapping[str, Any]],
) -> dict[str, float | None]:
    """Held total MAE(C2-B0) delta after excluding one held season at a time.

    Uses the exact same paired rows `evaluate_c2` consumes (from C2's own
    `pair_with_b0`); no refitting, no new feature, no new prediction. This
    answers "is the held-partition total win still there if any single held
    season is removed", which the combined C2 evaluation never isolated on
    its own.
    """
    held = [r for r in paired_rows if HELD_START <= int(r["season"]) <= HELD_END]
    out: dict[str, float | None] = {}
    for excluded_season in range(HELD_START, HELD_END + 1):
        remaining = [r for r in held if int(r["season"]) != excluded_season]
        b0_mae = _total_mae(remaining, "b0_total")
        c2_mae = _total_mae(remaining, "c2_total")
        out[str(excluded_season)] = (
            None if b0_mae is None or c2_mae is None else c2_mae - b0_mae
        )
    return out


def compute_season_stability(
    evaluation: Mapping[str, Any], paired_rows: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Season-by-season total stability, reusing `evaluate_c2`'s own per-season block."""
    season_block = evaluation["season_by_season_2020_2025"]
    favorable_seasons = []
    unfavorable_seasons = []
    for season in STABILITY_SEASONS:
        delta = season_block.get(str(season), {}).get("total", {}).get("mae_delta_c2_minus_b0")
        if delta is not None and delta < 0:
            favorable_seasons.append(season)
        else:
            unfavorable_seasons.append(season)

    leave_one_out = compute_held_leave_one_season_out(paired_rows)
    leave_one_out_all_negative = all(
        value is not None and value < 0 for value in leave_one_out.values()
    )

    return {
        "seasons_considered": list(STABILITY_SEASONS),
        "favorable_seasons": favorable_seasons,
        "unfavorable_seasons": unfavorable_seasons,
        "favorable_season_count": len(favorable_seasons),
        "min_favorable_seasons_required": STABILITY_MIN_FAVORABLE_SEASONS,
        "majority_threshold_met": len(favorable_seasons) >= STABILITY_MIN_FAVORABLE_SEASONS,
        "held_leave_one_season_out_total_mae_delta": leave_one_out,
        "held_leave_one_season_out_all_negative": leave_one_out_all_negative,
        "stable": (
            len(favorable_seasons) >= STABILITY_MIN_FAVORABLE_SEASONS
            and leave_one_out_all_negative
        ),
    }


def evaluate_total_promotion_gate(
    evaluation: Mapping[str, Any], stability: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply `TOTAL_PROMOTION_GATE_DESCRIPTION` to total-axis numbers only.

    Deliberately takes no margin input of any kind -- there is no way for
    this function to read, and therefore no way for it to depend on, C2's
    margin MAE, margin bootstrap, or `evaluate_promotion_gate`'s combined
    verdict.
    """
    held = evaluation["held_2023_2025"]
    validation = evaluation["validation_2020_2022"]

    held_total_b0 = held["total"]["b0_mae"]
    held_total_c2 = held["total"]["c2_mae"]
    held_total_bootstrap_p97_5 = held["total"]["game_bootstrap"]["p97_5"]
    val_total_b0 = validation["total"]["b0_mae"]
    val_total_c2 = validation["total"]["c2_mae"]

    failures = []
    if held_total_c2 is None or held_total_b0 is None or not held_total_c2 < held_total_b0:
        failures.append("held total MAE is not strictly better than B0")
    if held_total_bootstrap_p97_5 is None or not held_total_bootstrap_p97_5 < 0:
        failures.append("held total bootstrap 97.5th percentile does not stay below zero")
    if val_total_c2 is None or val_total_b0 is None or not val_total_c2 <= val_total_b0:
        failures.append("validation total MAE is worse than B0 (not directionally consistent)")
    if not stability["majority_threshold_met"]:
        failures.append(
            f"fewer than {stability['min_favorable_seasons_required']} of "
            f"{len(stability['seasons_considered'])} seasons individually favor C2 on total MAE"
        )
    if not stability["held_leave_one_season_out_all_negative"]:
        failures.append(
            "held total MAE win does not survive excluding a single held season "
            "(leave-one-season-out)"
        )

    promotion_eligible = not failures
    reason = (
        "All predeclared total-only gate conditions met on held 2023-2025 and "
        "validation 2020-2022 total-axis data, independent of margin."
        if promotion_eligible
        else "Gate failed: " + "; ".join(failures) + "."
    )
    return {
        "promotion_eligible": promotion_eligible,
        "gate_description": TOTAL_PROMOTION_GATE_DESCRIPTION,
        "reason": reason,
        "failed_conditions": failures,
    }


# --- Total-specific equal-volume directional accuracy -----------------------
#
# `game_market_c2_directional.py` hardcodes margin field names (`b0_margin`,
# `c2_margin`, `actual_margin`) and HOME/AWAY side labels against
# `spread_line`; it is not touched or modified here (out of scope). The
# functions below reuse its exact method -- pick a side against the closing
# line, restrict to the disagreement set, rank each model's own picks by its
# own edge, compare hit rate at equal pick volume -- adapted to the total
# market: OVER/UNDER against `total_line`, using `c2_total`/`b0_total`.

DEFAULT_VOLUME_FRACTIONS = (0.25, 0.5, 0.75, 1.0)


def _pick_total_side(predicted_total: float, total_line: float) -> str:
    if predicted_total > total_line:
        return "OVER"
    if predicted_total < total_line:
        return "UNDER"
    return "PUSH"


def build_market_total_index(market_rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Return {game_id: total_line} for rows meeting the closing-control contract."""
    index: dict[str, float] = {}
    for source in market_rows:
        row = dict(source)
        for field, expected in REQUIRED_CLOSING_CONTROL.items():
            if row.get(field) != expected:
                raise GameMarketC2TotalsOnlyError(f"closing control {field} must be {expected!r}")
        game_id = row["game_id"]
        if game_id in index:
            raise GameMarketC2TotalsOnlyError(f"duplicate market row: {game_id}")
        index[game_id] = float(row["total_line"])
    return index


def compute_equal_volume_total_directional_accuracy(
    paired_rows: Iterable[Mapping[str, Any]],
    market_total_index: Mapping[str, float],
    *,
    volume_fractions: tuple[float, ...] = DEFAULT_VOLUME_FRACTIONS,
) -> dict[str, Any]:
    market_matched = []
    for row in paired_rows:
        total_line = market_total_index.get(row["game_id"])
        if total_line is None:
            continue
        actual_side = _pick_total_side(row["actual_total"], total_line)
        if actual_side == "PUSH":
            continue
        b0_side = _pick_total_side(row["b0_total"], total_line)
        c2_side = _pick_total_side(row["c2_total"], total_line)
        market_matched.append({
            "game_id": row["game_id"],
            "season": row["season"],
            "total_line": total_line,
            "actual_side": actual_side,
            "b0_side": b0_side,
            "c2_side": c2_side,
            "b0_edge": abs(row["b0_total"] - total_line),
            "c2_edge": abs(row["c2_total"] - total_line),
        })

    disagreement = [
        row for row in market_matched
        if row["b0_side"] != "PUSH" and row["c2_side"] != "PUSH"
        and row["b0_side"] != row["c2_side"]
    ]
    for row in disagreement:
        row["b0_correct"] = row["b0_side"] == row["actual_side"]
        row["c2_correct"] = row["c2_side"] == row["actual_side"]

    n = len(disagreement)
    by_fraction = []
    b0_by_edge = sorted(disagreement, key=lambda r: r["b0_edge"], reverse=True)
    c2_by_edge = sorted(disagreement, key=lambda r: r["c2_edge"], reverse=True)
    for fraction in volume_fractions:
        take = math.ceil(fraction * n) if n else 0
        b0_top = b0_by_edge[:take]
        c2_top = c2_by_edge[:take]
        by_fraction.append({
            "volume_fraction": fraction,
            "games": take,
            "b0_hit_rate": (
                sum(1 for r in b0_top if r["b0_correct"]) / take if take else None
            ),
            "c2_hit_rate": (
                sum(1 for r in c2_top if r["c2_correct"]) / take if take else None
            ),
        })

    return {
        "market_matched_games": len(market_matched),
        "disagreement_games": n,
        "disagreement_share_of_market_matched": (
            n / len(market_matched) if market_matched else None
        ),
        "by_volume_fraction": by_fraction,
    }


def run_totals_only_evaluation(
    paired_rows: list[dict[str, Any]],
    market_rows: Iterable[Mapping[str, Any]],
    *,
    closing_market_total: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose the full totals-only research report from already-computed inputs.

    `paired_rows` must be C2's own `pair_with_b0` output (unchanged). This
    function fits nothing, predicts nothing, and never touches
    `evaluate_promotion_gate`'s (margin-coupled) verdict.
    `closing_market_total` is an optional pre-extracted retrospective
    total-vs-closing-market comparison (e.g. the `total` sub-block of
    `game_market_b0.evaluate_against_closing_market`'s `held_2023_2025`
    partition for C2's own predictions) -- passed through unchanged when the
    caller already has it (see `main`/`run_research`), never recomputed here
    since `evaluate_against_closing_market` already exists and is reused
    verbatim by the caller.
    """
    evaluation = evaluate_c2(paired_rows)
    stability = compute_season_stability(evaluation, paired_rows)
    gate = evaluate_total_promotion_gate(evaluation, stability)

    market_total_index = build_market_total_index(market_rows)
    directional: dict[str, Any] = {}
    for name, (start, end) in (
        ("validation_2020_2022", (VALID_START, VALID_END)),
        ("held_2023_2025", (HELD_START, HELD_END)),
    ):
        subset = [row for row in paired_rows if start <= row["season"] <= end]
        directional[name] = compute_equal_volume_total_directional_accuracy(
            subset, market_total_index
        )

    report: dict[str, Any] = {
        "challenger_name": TOTALS_ONLY_CHALLENGER_NAME,
        "parent_challenger": C2_NAME,
        "axis": "TOTAL_ONLY",
        "gate_evaluated_independent_of_margin": True,
        "development_seasons": (DEV_START, DEV_END),
        "validation_seasons": (VALID_START, VALID_END),
        "held_seasons": (HELD_START, HELD_END),
        "total_axis_evaluation": {
            "development_2000_2019": evaluation["development_2000_2019"]["total"],
            "validation_2020_2022": evaluation["validation_2020_2022"]["total"],
            "held_2023_2025": evaluation["held_2023_2025"]["total"],
        },
        "season_by_season_total_2020_2025": {
            season: block["total"]
            for season, block in evaluation["season_by_season_2020_2025"].items()
        },
        "season_stability": stability,
        "equal_volume_total_directional_accuracy_vs_b0": directional,
        "promotion_gate": gate,
    }
    if closing_market_total is not None:
        # `game_market_b0.evaluate_against_closing_market` always labels the
        # model under test with the literal key "b0" internally, regardless
        # of which model's predictions were actually passed in (C2's own
        # research runner reproduces the identical ambiguity for its own
        # `c2_vs_closing` block). The caller here passes C2's total
        # predictions, so `closing_market_total["b0"]` is really C2's total
        # performance against the closing market, not B0's; relabeled below
        # for this report only, values otherwise untouched.
        block = dict(closing_market_total)
        block["c2"] = block.pop("b0")
        if "mae_delta_b0_minus_close" in block:
            block["mae_delta_c2_minus_close"] = block.pop("mae_delta_b0_minus_close")
        report["evaluation_vs_closing_market_total"] = block

    report["status"] = (
        "RESEARCH_CHALLENGER_PROMOTION_ELIGIBLE"
        if gate["promotion_eligible"]
        else "RESEARCH_CHALLENGER_REJECTED"
    )
    report["research_limitations"] = [
        "This is a research-only status classification, not a production, "
        "selector, prospective-shadow, or public-pick authorization; only "
        "Jacob can grant those separately.",
        "This report evaluates only the total axis. C2's margin result "
        "(RESEARCH_CHALLENGER_REJECTED, held margin bootstrap 97.5th "
        "percentile +0.151) is unchanged and is not reconsidered, softened, "
        "or overridden by a passing total-only verdict here.",
        "Closing lines are retrospective Pro-Football-Reference controls via "
        "nflverse, not timestamped sportsbook observations, and are never a "
        "model input; see game_market_c2_research.py's own documented "
        "closing-market caveats, which apply unchanged here.",
        "No new feature was added to TOTAL_FEATURES; this module only "
        "re-evaluates C2's existing total-target predictions against a new, "
        "independently predeclared gate.",
    ]
    return report


# --- End-to-end reproduction from pinned inputs ------------------------------
#
# Every loader/builder/fitter called below is an existing C2/B0 function,
# imported unchanged: `load_pinned_historical_rows` (schedule + closing
# market), `load_pinned_team_offense_rows`/`load_pinned_pbp_play_rows` (C2's
# own digest-checked nflverse derivations), `filter_team_offense_rows_for_...`
# / `build_c2_game_rows` (C2's feature assembly), `fit_c2_model`/`apply_c2`
# (C2's own ridge fit -- both margin and total targets, never touched or
# retuned here), `build_b0_predictions` (B0, unchanged), `pair_with_b0` (C2's
# own B0-pairing join), and `evaluate_against_closing_market` (B0's own
# retrospective closing-market comparator, reused for C2's total predictions
# exactly as `game_market_c2_research.py` already does for its own report).
# This module adds no new fitting, joining, or feature logic of its own.


def run_research(
    schedules_csv: Path, team_offense_csv: Path, pbp_plays_csv: Path
) -> dict[str, Any]:
    scoring_rows, market_rows, source_counts = load_pinned_historical_rows(schedules_csv)
    team_offense_rows_raw = load_pinned_team_offense_rows(team_offense_csv)
    pbp_play_rows = load_pinned_pbp_play_rows(pbp_plays_csv)

    team_offense_rows, excluded_offense_rows = filter_team_offense_rows_for_negative_value_bug(
        team_offense_rows_raw
    )

    c2_rows = build_c2_game_rows(
        scoring_rows, team_offense_rows, pbp_play_rows, rolling_window=5, min_prior_games=3
    )
    model = fit_c2_model(c2_rows, margin_features=MARGIN_FEATURES, total_features=TOTAL_FEATURES)
    c2_rows = apply_c2(c2_rows, model)

    scoring_features = build_prior_scoring_features(scoring_rows, rolling_window=5)
    b0_predictions = build_b0_predictions(scoring_features, min_prior_games=3)

    paired = pair_with_b0(c2_rows, b0_predictions)

    market_game_ids = {row["game_id"] for row in market_rows}
    c2_style_predictions = _b0_style_rows(c2_rows)
    c2_market_predictions = [row for row in c2_style_predictions if row["game_id"] in market_game_ids]
    c2_vs_closing = evaluate_against_closing_market(c2_market_predictions, market_rows)
    closing_market_total = c2_vs_closing["partitions"]["held_2023_2025"]["total"]

    report = run_totals_only_evaluation(
        paired, market_rows, closing_market_total=closing_market_total
    )
    report["schema_version"] = 1
    report["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report["sources"] = {
        "schedule": dict(PINNED_SCHEDULE_SOURCE),
        "team_offense_week": dict(PINNED_TEAM_OFFENSE_SOURCE),
        "pbp_plays_filtered": dict(PINNED_PBP_PLAY_SOURCE),
    }
    report["source_counts"] = source_counts
    report["known_data_issues"] = {
        "team_offense_negative_value_rows_excluded": len(excluded_offense_rows),
    }
    report["margin_features_unchanged_from_c2"] = list(MARGIN_FEATURES)
    report["total_features_unchanged_from_c2"] = list(TOTAL_FEATURES)
    report["common_paired_games"] = len(paired)
    return report


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
        "season_stability": report["season_stability"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
