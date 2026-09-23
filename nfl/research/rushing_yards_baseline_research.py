#!/usr/bin/env python3
"""Predeclared rolling-origin NFL rushing-yard baseline + feature-informed challenger.

This is a genuinely new NFL predictive capability: at the time this module
was written, `nfl/research/` had baseline research for passing yards
(`passing_yards_baseline_research.py`) and receptions
(`receptions_baseline_research.py`, `receptions_team_opportunity_challenger.py`,
and related modules), plus extensive game-level market work
(`game_market_b0*`/`game_market_c1*`/`game_market_c2*`/`game_market_c3*`), but
no rushing-yards module of any kind existed anywhere in the repository
(confirmed by grep across `nfl/research/`, `nfl/tests/`, and
`.github/workflows/` before writing this file). Rushing yards is priced as a
sportsbook player-prop market and is not covered by any of those existing
modules, so it is a real, previously-uncovered market rather than a
duplicate of passing-yards or receptions work.

Data source: this module deliberately does NOT add a new ingestion path. It
reuses the exact same already-ingested, already-audited nflverse
`stats_player_week_<season>.csv` weekly corpus that
`passing_yards_baseline_research.py` and `receptions_baseline_research.py`
already consume (same audit-manifest verification contract as those two
modules: season count, missing-identity population, then a per-season byte
size + SHA-256 check against the cached CSV). `nflverse_full_audit.py`'s own
`NUMERIC` field list already includes `carries` and `rushing_yards` -- this
source has always covered rushing volume and production; nothing about this
module required extending ingestion.

Two rushing-specific modeling decisions are made explicit here because this
is not a mechanical port of either reference script:

1. Population/eligibility gate: like receptions (WR/TE/RB/FB) and unlike
   passing yards (an unambiguous `position == "QB"` label), rushing
   production comes from RB/QB/WR/FB and is not well captured by a single
   position label. This module therefore follows the receptions precedent:
   no position-label filter, a role/opportunity gate instead. A row is
   rushing-role-relevant when `carries > 0` for that player-week.
2. Data-quality comparison to the two reference markets: unlike receptions'
   confirmed 2003-2008 `targets`-column blackout (see
   `receptions_baseline_research.py`'s own docstring), a full scan of the
   pinned 1999-2025 corpus found **no season-level coverage gap** in either
   `carries` or `rushing_yards` (zero blank/invalid numeric fields for both
   columns in every one of the 27 audited seasons -- see
   `carries_rushing_yards_coverage_by_season` in the output and
   `RUSHING_YARDS_BASELINE_RESEARCH_20260923/README.md` for the full
   per-season table). A real, small (12-row, 1999-2025) anomaly was found and
   is counted rather than hidden or silently dropped: `carries == 0` while
   `rushing_yards != 0`, a known nflverse quirk (e.g. a lateral or a
   fumble-recovery return credited as rushing yardage without a play charted
   as a carry). These 12 rows are excluded from the role-positive population
   by the same `carries > 0` gate that defines it (consistent with the
   population definition, not a special-cased removal) and are reported
   under `rushing_stat_invariant_failures["production_with_zero_carries"]`
   so the exclusion is visible rather than silent.

Two real models are compared on the SAME matched population in this module
(unlike `receptions_baseline_research.py`, which established B0 alone with
no challenger yet):

- `b0`: the simpler control -- mean rushing yards over the player's last five
  rushing-role-positive appearances, gated on at least three such
  appearances. This mirrors the B0 pattern in both reference scripts exactly.
- `c1_carries3_times_ypc8`: a feature-informed challenger, structurally
  identical to `passing_yards_baseline_research.py`'s
  `c2_attempts3_times_ypa8` (recent-workload x aggregate-efficiency
  decomposition) applied to rushing: mean carries over the three most recent
  rushing-role appearances, multiplied by aggregate rushing yards per carry
  over up to eight most recent rushing-role appearances. This is a genuinely
  different signal than B0's raw yards rolling mean -- it separately models
  recent workload (a player getting more/fewer carries) and standing
  per-carry efficiency, then recombines them -- not a relabeling of the same
  quantity.

Status stays `RESEARCH_ONLY_NOT_PROMOTED`; nothing here is wired into any
capture pipeline, selector, or public artifact. This module does not touch
`receptions_team_opportunity_challenger.py`, `qb_change_team_dropbacks.py`,
`role_regime_redistribution.py`, `price_aware_offers.py`, the B0 selector, or
any workflow YAML.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


# Pinned reproduction of the real B0 output on the 1999-2025 audited corpus,
# restricted to rows from 2023 onward (i.e. what a from-2023 live cold start
# would actually compute). This is a drift guard, not a live production
# board: no capture pipeline consumes it yet. Regenerate deliberately if the
# pinned audit manifest or its underlying CSVs are ever intentionally
# refreshed.
EXPECTED_ACTIVE_B0: dict = {
    2024: {"n": 1972, "mae": 18.647785665990536},
    2025: {"n": 1987, "mae": 18.307037409830567},
}


class RushingYardsDataError(ValueError):
    """Raised when the pinned rushing-yards corpus fails a required invariant.

    Fail-closed, matching this repository's established error style: a
    corrupted/drifted/impossible source is surfaced as an exception rather
    than silently coerced, clipped, or treated as zero.
    """


def mean(values):
    return statistics.fmean(values)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metrics(rows, key):
    errors = [row[key] - row["actual"] for row in rows if row.get(key) is not None]
    absolute = [abs(value) for value in errors]
    return {
        "n": len(errors),
        "mae": mean(absolute) if absolute else None,
        "median_absolute_error": statistics.median(absolute) if absolute else None,
        "rmse": math.sqrt(mean(value * value for value in errors)) if errors else None,
        "bias_prediction_minus_actual": mean(errors) if errors else None,
    }


def paired_delta(rows, challenger):
    paired = [row for row in rows if row.get("b0") is not None and row.get(challenger) is not None]
    if not paired:
        return {"n": 0, "mae_delta_vs_b0": None}
    b0 = mean(abs(row["b0"] - row["actual"]) for row in paired)
    alt = mean(abs(row[challenger] - row["actual"]) for row in paired)
    return {"n": len(paired), "b0_mae": b0, "challenger_mae": alt, "mae_delta_vs_b0": alt - b0}


def cluster_bootstrap(rows, challenger, iterations=2000, seed=20260923):
    paired = [row for row in rows if row.get("b0") is not None and row.get(challenger) is not None]
    by_player = defaultdict(list)
    for row in paired:
        by_player[row["player_id"]].append(row)
    players = sorted(by_player)
    rng = random.Random(seed)
    deltas = []
    for _ in range(iterations):
        sampled = [rng.choice(players) for _ in players]
        b0_error = []
        challenger_error = []
        for player_id in sampled:
            for row in by_player[player_id]:
                b0_error.append(abs(row["b0"] - row["actual"]))
                challenger_error.append(abs(row[challenger] - row["actual"]))
        deltas.append(mean(challenger_error) - mean(b0_error))
    deltas.sort()
    return {
        "cluster": "player_id",
        "players": len(players),
        "iterations": iterations,
        "seed": seed,
        "mae_delta_vs_b0_p2_5": deltas[int(iterations * 0.025)],
        "mae_delta_vs_b0_p50": statistics.median(deltas),
        "mae_delta_vs_b0_p97_5": deltas[int(iterations * 0.975)],
    }


def load_rusher_rows(cache: Path, audit: dict):
    """Load role-positive rushing rows from the pinned nflverse corpus.

    Mirrors `passing_yards_baseline_research.load_qb_rows` and
    `receptions_baseline_research.load_receiver_rows`'s audit-manifest
    verification exactly (season count, missing-identity population, then a
    per-season byte-size + SHA-256 check against the cached CSV) before any
    row is trusted. Raises `RushingYardsDataError` (fail-closed) rather than
    silently proceeding when any of those checks fail.
    """
    rows = []
    invariant_failures = defaultdict(int)
    carries_coverage_by_season = {}
    seasons = audit.get("seasons")
    if not isinstance(seasons, list) or len(seasons) != 27:
        raise RushingYardsDataError("full audit must contain 27 seasons")
    if audit.get("summary", {}).get("missing_identity_rows_with_offense") != 7:
        raise RushingYardsDataError("full audit missing-identity population drift")
    for source in seasons:
        season = int(source["season"])
        path = cache / f"stats_player_week_{season}.csv"
        if not path.exists():
            raise RushingYardsDataError(f"{season}: cached source CSV missing at {path}")
        if path.stat().st_size != int(source["bytes"]):
            raise RushingYardsDataError(f"{season}: cached byte size drift")
        if sha256_file(path) != source["sha256"]:
            raise RushingYardsDataError(f"{season}: cached SHA-256 drift")
        season_role_rows = 0
        season_total_rows = 0
        season_blank_or_invalid = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                season_total_rows += 1
                raw_carries = str(row.get("carries") or "").strip()
                raw_rushing_yards = str(row.get("rushing_yards") or "").strip()
                if not raw_carries or not raw_rushing_yards:
                    season_blank_or_invalid += 1
                try:
                    carries = float(row["carries"] or 0)
                    rushing_yards = float(row["rushing_yards"] or 0)
                except ValueError:
                    season_blank_or_invalid += 1
                    invariant_failures["nonfinite"] += 1
                    continue
                if not (math.isfinite(carries) and math.isfinite(rushing_yards)):
                    invariant_failures["nonfinite"] += 1
                    continue
                if carries < 0:
                    invariant_failures["negative_carries"] += 1
                if carries == 0 and rushing_yards != 0:
                    invariant_failures["production_with_zero_carries"] += 1
                    # Consistent with the role-positive population definition
                    # below (`carries > 0`): a row with zero charted carries
                    # is not rushing-role-relevant even if some yardage was
                    # attributed to it (e.g. a lateral/fumble-return quirk).
                    continue
                if carries <= 0:
                    continue
                season_role_rows += 1
                rows.append({
                    "player_id": str(row["player_id"]).strip(),
                    "season": int(float(row["season"])),
                    "week": int(float(row["week"])),
                    "season_type": str(row["season_type"]),
                    "game_id": str(row["game_id"]),
                    "carries": carries,
                    "rushing_yards": rushing_yards,
                })
        carries_coverage_by_season[str(season)] = {
            "total_rows": season_total_rows,
            "rushing_role_positive_rows": season_role_rows,
            "blank_or_invalid_numeric_rows": season_blank_or_invalid,
        }
    rows.sort(key=lambda row: (row["season"], row["week"], row["game_id"], row["player_id"]))
    return rows, dict(sorted(invariant_failures.items())), carries_coverage_by_season


def rolling_predictions(rows):
    """Compute B0 and the feature-informed challenger with strict PIT safety.

    For every row, both predictions are derived exclusively from that
    player's STRICTLY PRIOR rushing-role-positive appearances (the history
    deque is only appended to AFTER the prediction for the current row has
    been computed), so no target-week or later information can leak into
    its own prediction.
    """
    history = defaultdict(lambda: deque(maxlen=8))
    scored = []
    for row in rows:
        appearances = history[row["player_id"]]
        b0 = None
        c1 = None
        if len(appearances) >= 3:
            last_five = list(appearances)[-5:]
            b0 = mean(item["rushing_yards"] for item in last_five)
            last_three = list(appearances)[-3:]
            carries_3 = mean(item["carries"] for item in last_three)
            carries_8 = sum(item["carries"] for item in appearances)
            if carries_8 > 0:
                c1 = carries_3 * sum(item["rushing_yards"] for item in appearances) / carries_8
        if row["season_type"] == "REG":
            scored.append({
                **row,
                "actual": row["rushing_yards"],
                "b0": b0,
                "c1_carries3_times_ypc8": c1,
            })
        appearances.append(row)
    return scored


def report_partition(rows):
    models = ("b0", "c1_carries3_times_ypc8")
    return {
        "native": {model: metrics(rows, model) for model in models},
        "paired": {model: paired_delta(rows, model) for model in models[1:]},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--pin-active-b0",
        action="store_true",
        help="Print the observed active_b0_reproduction_2024_2025 block as a "
        "Python literal ready to paste into EXPECTED_ACTIVE_B0, instead of "
        "checking it against a pinned value. Use only when deliberately "
        "re-pinning after an intentional corpus refresh.",
    )
    args = parser.parse_args()
    audit = json.loads(args.audit_manifest.read_text(encoding="utf-8"))
    rows, invariants, carries_coverage_by_season = load_rusher_rows(args.cache, audit)
    long_scored = rolling_predictions(rows)
    active_scored = rolling_predictions([row for row in rows if row["season"] >= 2023])
    partitions = {
        "development_2000_2019": (2000, 2019),
        "validation_2020_2022": (2020, 2022),
        "held_2023_2025": (2023, 2025),
    }
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "source_audit_manifest_sha256": sha256_file(args.audit_manifest),
        "predeclared_models": {
            "b0": "mean rushing yards over the last five rushing-role-positive appearances (carries > 0); minimum three such appearances",
            "c1_carries3_times_ypc8": "mean carries over three prior rushing-role appearances multiplied by aggregate rushing yards per carry over up to eight prior rushing-role appearances",
        },
        "eligibility_rule": (
            "Role/opportunity gate, not a position-label filter: a row is "
            "rushing-role-relevant when carries > 0 for that player-week, "
            "across every position (RB/QB/WR/FB/etc.). See module docstring "
            "for the confirmed absence of a targets-style coverage gap for "
            "carries/rushing_yards, and for the small (12-row, 1999-2025) "
            "carries==0-with-nonzero-yards anomaly that this gate excludes."
        ),
        "population_limit": "All rushing-role-positive rows across every position label, not a sportsbook-listed starter population; current-game carries/rushing_yards are not used for eligibility.",
        "rusher_source_rows": len(rows),
        "rushing_stat_invariant_failures": invariants,
        "carries_rushing_yards_coverage_by_season": carries_coverage_by_season,
        "partitions": {},
        "by_season": {},
        "active_b0_reproduction_2024_2025": {},
    }
    for name, (start, end) in partitions.items():
        subset = [row for row in long_scored if start <= row["season"] <= end]
        output["partitions"][name] = report_partition(subset)
    held = [row for row in long_scored if 2023 <= row["season"] <= 2025]
    output["partitions"]["held_2023_2025"]["paired"]["c1_carries3_times_ypc8"]["player_cluster_bootstrap"] = (
        cluster_bootstrap(held, "c1_carries3_times_ypc8")
    )
    for season in range(1999, 2026):
        subset = [row for row in long_scored if row["season"] == season]
        if not subset:
            continue
        output["by_season"][str(season)] = report_partition(subset)
    for season in (2024, 2025):
        output["active_b0_reproduction_2024_2025"][str(season)] = metrics(
            [row for row in active_scored if row["season"] == season], "b0"
        )
        observed = output["active_b0_reproduction_2024_2025"][str(season)]
        if args.pin_active_b0:
            continue
        expected = EXPECTED_ACTIVE_B0.get(season)
        if expected is None:
            continue
        if observed["n"] != expected["n"] or not math.isclose(
            observed["mae"], expected["mae"], rel_tol=0.0, abs_tol=1e-9
        ):
            raise RushingYardsDataError(f"{season}: active B0 reproduction drift")
    validation = output["partitions"]["validation_2020_2022"]["paired"]["c1_carries3_times_ypc8"]
    held_paired = output["partitions"]["held_2023_2025"]["paired"]["c1_carries3_times_ypc8"]
    rejected = validation["mae_delta_vs_b0"] >= 0 and held_paired["mae_delta_vs_b0"] >= 0
    output["research_decisions"] = {
        "c1_carries3_times_ypc8": {
            "decision": "REJECTED_RESEARCH_CHALLENGER" if rejected else "REVIEW_REQUIRED",
            "reason": (
                "Paired MAE was worse than B0 in both validation and held partitions."
                if rejected
                else "The fixed rejection rule was not satisfied; no promotion is implied."
            ),
            "validation_mae_delta_vs_b0": validation["mae_delta_vs_b0"],
            "held_mae_delta_vs_b0": held_paired["mae_delta_vs_b0"],
        }
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.pin_active_b0:
        print(json.dumps(output["active_b0_reproduction_2024_2025"], sort_keys=True))
        return
    print(json.dumps({
        "active": output["active_b0_reproduction_2024_2025"],
        "held": output["partitions"]["held_2023_2025"],
        "invariants": invariants,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
