"""Tier 1 NFL intelligence: matched B0-vs-challenger evaluation spine.

One harness for every Tier 1 consumer, so results are comparable and every
report states which rows a factor actually changed.

* Population: player-weeks from the pinned, audited nflverse weekly corpus
  (byte size + SHA-256 verified against
  `engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`),
  optionally plus a current-season file whose SHA-256 is recorded, never
  trusted blindly.
* B0 per market mirrors the live receptions B0: mean of the last five role
  appearances, minimum three (`b0_rolling_mean`). Receptions reproduces the
  pinned `EXPECTED_ACTIVE_B0` drift guard of `receptions_baseline_research`.
* Partitions are fixed here, not per factor:
    DEV_2016_2022            -- feature/consumer development allowed
    HOLDOUT_2023_2025        -- already inspected by earlier NFL experiments
                                (PRs #184, #186, #191, #193 ...): reported as
                                EXPLORATORY, never as confirmation
    FRESH_2026               -- current season to date; the freshest
                                out-of-time evidence (small)
* Scoring is paired on matched rows (B0 and challenger both present):
  mean |challenger - actual| - |B0 - actual| (negative = challenger better)
  with a game-clustered bootstrap CI, plus activation coverage (share of
  rows the challenger actually changed) and bias. Binary markets use Brier
  score and log loss.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

PARTITIONS = {
    "DEV_2016_2022": (2016, 2022),
    "HOLDOUT_2023_2025": (2023, 2025),
    "FRESH_2026": (2026, 2026),
}
PREVIOUSLY_INSPECTED = {"HOLDOUT_2023_2025"}
BOOT_B = 1000
BOOT_SEED = 20260924

# market -> (actual_fn, role_fn): role>0 defines a relevant appearance.
MARKETS: dict[str, tuple[Callable[[Mapping], float], Callable[[Mapping], float]]] = {
    "receptions": (lambda r: r["receptions"], lambda r: max(r["targets"], r["receptions"])),
    "receiving_yards": (lambda r: r["receiving_yards"], lambda r: max(r["targets"], r["receptions"])),
    "rushing_yards": (lambda r: r["rushing_yards"], lambda r: r["carries"]),
    "passing_yards": (lambda r: r["passing_yards"], lambda r: r["attempts"]),
    "anytime_td": (lambda r: 1.0 if (r["rushing_tds"] + r["receiving_tds"]) > 0 else 0.0,
                   lambda r: r["carries"] + max(r["targets"], r["receptions"])),
}
BINARY_MARKETS = {"anytime_td"}

NUMERIC = ("targets", "receptions", "receiving_yards", "receiving_tds", "receiving_air_yards",
           "target_share", "air_yards_share", "wopr", "carries", "rushing_yards",
           "rushing_tds", "attempts", "completions", "passing_yards", "passing_tds")


class HarnessError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _num(value: str) -> float:
    if value in ("", "NA", None):
        return 0.0
    out = float(value)
    if not math.isfinite(out):
        raise HarnessError(f"non-finite value {value!r}")
    return out


def _read_rows(path: Path, first_season: int) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            if not (raw.get("player_id") or "").strip():
                continue  # audited structural-zero rows without identity
            season = int(float(raw["season"]))
            if season < first_season:
                continue
            row = {
                "player_id": raw["player_id"].strip(),
                "player_name": raw.get("player_display_name") or "",
                "position": (raw.get("position") or "").strip().upper() or "OTHER",
                "season": season, "week": int(float(raw["week"])),
                "season_type": raw["season_type"], "game_id": raw["game_id"],
                "team": raw.get("team") or "", "opponent_team": raw.get("opponent_team") or "",
            }
            for name in NUMERIC:
                row[name] = _num(raw.get(name, ""))
            rows.append(row)
    return rows


def load_player_weeks(cache: Path, audit_manifest: Path, *, first_season: int = 2012,
                      current_season_csv: Path | None = None) -> tuple[list[dict], dict]:
    """Audited player-week rows (every position), plus optional current season.

    Returns (rows, provenance). Historical files must match the audit's byte
    size and SHA-256; the current-season file's SHA-256 is recorded.
    """
    audit = json.loads(audit_manifest.read_text(encoding="utf-8"))
    provenance: dict[str, Any] = {"audit_manifest_sha256": sha256_file(audit_manifest),
                                  "seasons": {}}
    rows: list[dict] = []
    for source in audit["seasons"]:
        season = int(source["season"])
        if season < first_season:
            continue
        path = cache / f"stats_player_week_{season}.csv"
        if path.stat().st_size != int(source["bytes"]) or sha256_file(path) != source["sha256"]:
            raise HarnessError(f"{season}: cached file does not match the audit manifest")
        rows.extend(_read_rows(path, first_season))
        provenance["seasons"][str(season)] = source["sha256"]
    if current_season_csv is not None:
        current = _read_rows(current_season_csv, first_season)
        seasons = {row["season"] for row in current}
        if seasons & {int(s) for s in provenance["seasons"]}:
            raise HarnessError("current-season file overlaps an audited season")
        rows.extend(current)
        provenance["current_season"] = {"path": current_season_csv.name,
                                        "sha256": sha256_file(current_season_csv),
                                        "seasons": sorted(seasons), "rows": len(current)}
    rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"], r["player_id"]))
    return rows, provenance


def row_key(row: Mapping[str, Any]) -> tuple[int, int, str, str]:
    return (row["season"], row["week"], row["game_id"], row["player_id"])


def b0_rolling_mean(rows: list[dict], market: str, *, window: int = 5,
                    min_appearances: int = 3) -> list[dict]:
    """Score REG role appearances of `market` with the live B0 rule.

    History includes POST appearances (as the live B0 does); only REG rows
    are scored. Each scored row: key fields, actual, b0 (None when history
    is too short), role.
    """
    actual_fn, role_fn = MARKETS[market]
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
    scored = []
    for row in rows:
        role = role_fn(row)
        if role <= 0:
            continue
        past = history[row["player_id"]]
        b0 = None
        if len(past) >= min_appearances and statistics.fmean(p[1] for p in past) > 0:
            b0 = statistics.fmean(p[0] for p in past)
        if row["season_type"] == "REG":
            scored.append({"season": row["season"], "week": row["week"],
                           "game_id": row["game_id"], "player_id": row["player_id"],
                           "team": row["team"], "opponent_team": row["opponent_team"],
                           "position": row["position"], "actual": actual_fn(row),
                           "b0": b0, "role": role})
        past.append((actual_fn(row), role))
    return scored


def _cluster_bootstrap(values_by_cluster: Mapping[str, list[float]], b: int = BOOT_B,
                       seed: int = BOOT_SEED) -> list[float] | None:
    keys = sorted(values_by_cluster)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(b):
        total = count = 0.0
        for _k in keys:
            vals = values_by_cluster[keys[rng.randrange(len(keys))]]
            total += sum(vals)
            count += len(vals)
        draws.append(total / count)
    draws.sort()
    return [draws[int(0.025 * (b - 1))], draws[int(0.975 * (b - 1))]]


def _loss(market: str, pred: float, actual: float) -> dict[str, float]:
    if market in BINARY_MARKETS:
        p = min(max(pred, 1e-6), 1 - 1e-6)
        return {"brier": (p - actual) ** 2,
                "log_loss": -(actual * math.log(p) + (1 - actual) * math.log(1 - p))}
    return {"abs_error": abs(pred - actual), "error": pred - actual}


def evaluate(scored: Iterable[Mapping[str, Any]], challenger: Mapping[tuple, float],
             market: str, *, partitions: Mapping[str, tuple[int, int]] = PARTITIONS) -> dict:
    """Paired B0-vs-challenger comparison on matched rows, per partition.

    `challenger` maps row_key -> prediction. Rows where either is missing
    are excluded and counted, never silently scored.
    """
    scored = list(scored)
    primary = "log_loss" if market in BINARY_MARKETS else "abs_error"
    out: dict[str, Any] = {"market": market, "primary_metric": primary, "partitions": {}}
    for name, (start, end) in partitions.items():
        rows = [r for r in scored if start <= r["season"] <= end]
        matched, diffs, changed = [], defaultdict(list), 0
        agg = defaultdict(lambda: {"b0": 0.0, "challenger": 0.0})
        n_b0_only = n_ch_only = 0
        for r in rows:
            c = challenger.get(row_key(r))
            if r["b0"] is None or c is None:
                n_b0_only += r["b0"] is not None and c is None
                n_ch_only += r["b0"] is None and c is not None
                continue
            matched.append(r)
            lb, lc = _loss(market, r["b0"], r["actual"]), _loss(market, c, r["actual"])
            for metric in lb:
                agg[metric]["b0"] += lb[metric]
                agg[metric]["challenger"] += lc[metric]
            diffs[f"{r['season']}:{r['game_id']}"].append(lc[primary] - lb[primary])
            changed += abs(c - r["b0"]) > 1e-9
        n = len(matched)
        entry: dict[str, Any] = {
            "n_rows": len(rows), "n_matched": n, "n_b0_without_challenger": n_b0_only,
            "n_challenger_without_b0": n_ch_only,
            "previously_inspected": name in PREVIOUSLY_INSPECTED,
            "evidence_label": ("EXPLORATORY_PREVIOUSLY_INSPECTED" if name in PREVIOUSLY_INSPECTED
                               else "DEVELOPMENT" if name.startswith("DEV") else "FRESH_OUT_OF_TIME"),
        }
        if n:
            entry["activation_share"] = changed / n
            entry["metrics"] = {m: {k: v / n for k, v in d.items()} for m, d in agg.items()}
            flat = [x for vals in diffs.values() for x in vals]
            entry["paired_delta_mean"] = statistics.fmean(flat)
            entry["paired_delta_ci95"] = _cluster_bootstrap(diffs)
            entry["n_game_clusters"] = len(diffs)
        out["partitions"][name] = entry
    return out


def attribution(changed_by: Mapping[tuple, Iterable[str]]) -> dict[str, int]:
    """Count of rows each factor changed, from a consumer's row -> factor ids map."""
    counts: dict[str, int] = defaultdict(int)
    for factors in changed_by.values():
        for factor in set(factors):
            counts[factor] += 1
    return dict(sorted(counts.items()))


def fit_scale_control(scored: Iterable[Mapping[str, Any]], market: str, *,
                      fit_partition: tuple[int, int] = PARTITIONS["DEV_2016_2022"]) -> float:
    """Best single multiplier k for B0 on the development partition only.

    Mandatory control: B0 is known to be scale-biased (PR #193), so ANY
    shrinkage lowers MAE. A factor consumer must beat `k * B0`, not just B0,
    before its information can be credited. Grid-searched minimum of the
    primary loss; binary markets use a multiplier on the probability.
    """
    rows = [r for r in scored if fit_partition[0] <= r["season"] <= fit_partition[1]
            and r["b0"] is not None]
    if not rows:
        raise HarnessError("no development rows to fit the scale control")
    primary = "log_loss" if market in BINARY_MARKETS else "abs_error"
    best_k, best = 1.0, None
    for step in range(50, 151):
        k = step / 100.0
        loss = statistics.fmean(_loss(market, k * r["b0"], r["actual"])[primary] for r in rows)
        if best is None or loss < best:
            best_k, best = k, loss
    return best_k


def scale_control_predictions(scored: Iterable[Mapping[str, Any]], k: float) -> dict[tuple, float]:
    return {row_key(r): k * r["b0"] for r in scored if r["b0"] is not None}


def evaluate_against_controls(scored: list[Mapping[str, Any]], challenger: Mapping[tuple, float],
                              market: str) -> dict:
    """The standard Tier 1 report: challenger vs B0, and vs the scale control.

    Credit a factor only where it beats the scale control on matched rows.
    """
    k = fit_scale_control(scored, market)
    control = scale_control_predictions(scored, k)
    shifted = [{**r, "b0": control.get(row_key(r))} for r in scored]
    return {"market": market, "scale_control_k": k,
            "vs_b0": evaluate(scored, challenger, market),
            "vs_scale_control": evaluate(shifted, challenger, market)}
