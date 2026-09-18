#!/usr/bin/env python3
"""Reproduce C1 from the pinned B0 source without using market-line inputs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from nfl.research.game_market_b0 import build_b0_predictions
from nfl.research.game_market_b0_research import (
    PINNED_SCHEDULE_SOURCE,
    load_pinned_historical_rows,
)
from nfl.research.game_market_c1_dev_bias import (
    C1_NAME,
    evaluate_c1,
    fit_development_corrections,
    prepare_rows,
)
from nfl.research.scoring_prior_features import build_prior_scoring_features


def run_research(schedules_csv: Path) -> dict:
    scoring_rows, _market_rows, source_counts = load_pinned_historical_rows(
        schedules_csv
    )
    scoring_features = build_prior_scoring_features(scoring_rows, rolling_window=5)
    predictions = build_b0_predictions(scoring_features, min_prior_games=3)

    # C1 deliberately uses explicit-final outcomes from the scoring substrate,
    # not the closing-market subset. Market availability cannot select its fit
    # population.
    paired = prepare_rows(predictions, scoring_rows)
    corrections = fit_development_corrections(paired)
    evaluation = evaluate_c1(paired, corrections)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "RESEARCH_CHALLENGER_REJECTED",
        "challenger_name": C1_NAME,
        "source": dict(PINNED_SCHEDULE_SOURCE),
        "source_counts": source_counts,
        "prediction_eligibility": dict(
            sorted(Counter(row["eligibility"] for row in predictions).items())
        ),
        "outcome_population_is_market_independent": True,
        "evaluation": evaluation,
        "decision": {
            "promotion_eligible": False,
            "reason": (
                "Held margin improvement is small and its paired bootstrap "
                "interval crosses zero; total correction worsens point estimates."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedules-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_research(args.schedules_csv)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "source_sha256": report["source"]["sha256"],
                "corrections": report["evaluation"]["corrections"],
                "held": report["evaluation"]["partitions"]["held_2023_2025"],
                "decision": report["decision"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
