#!/usr/bin/env python3
"""Shared, outcome-agnostic primitives for locked equal-volume experiments.

This module removes three repeated research-drift risks:
1. aggregate holdout top-N can silently move volume between dates;
2. incomplete/duplicate candidate identity can corrupt overlap anatomy;
3. prediction/selection state needs a deterministic freeze before outcomes.

Selection functions never read an outcome field. Evaluation helpers are meant
to run only after an outcome-free prediction freeze has been hashed.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict

IDENTITY_FIELDS = ("date", "game_pk", "player_id", "prop_type", "line")


class ExperimentIntegrityError(ValueError):
    """Fail-closed integrity error for an experiment population/selection."""


def candidate_identity(row):
    missing = [
        field for field in IDENTITY_FIELDS
        if field not in row or row.get(field) is None or row.get(field) == ""
    ]
    if missing:
        raise ExperimentIntegrityError(
            "incomplete candidate identity: missing " + ", ".join(missing)
        )
    return tuple(row[field] for field in IDENTITY_FIELDS)


def require_unique_population(rows):
    by_id = {}
    for row in rows:
        cid = candidate_identity(row)
        if cid in by_id:
            raise ExperimentIntegrityError(f"duplicate candidate identity: {cid!r}")
        by_id[cid] = row
    return by_id


def _finite_score(row, key):
    value = row.get(key)
    if value is None or isinstance(value, bool):
        raise ExperimentIntegrityError(
            f"{key} is missing/non-numeric for {candidate_identity(row)!r}"
        )
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentIntegrityError(
            f"{key} is non-numeric for {candidate_identity(row)!r}: {value!r}"
        ) from exc
    if not math.isfinite(value):
        raise ExperimentIntegrityError(
            f"{key} is non-finite for {candidate_identity(row)!r}: {value!r}"
        )
    return value


def _identity_sort_key(row):
    return tuple(repr(v) for v in candidate_identity(row))


def _rank(rows, score_key):
    return sorted(
        rows,
        key=lambda r: (-_finite_score(r, score_key), _identity_sort_key(r)),
    )


def _group_by_date(rows):
    require_unique_population(rows)
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    return dict(by_date)


def _selection_result(per_date, population_rows):
    champion_ids = []
    challenger_ids = []
    dates = {}

    for date in sorted(per_date):
        champion = per_date[date]["champion"]
        challenger = per_date[date]["challenger"]
        if len(champion) != len(challenger):
            raise ExperimentIntegrityError(
                f"per-date volume mismatch on {date}: "
                f"champion={len(champion)} challenger={len(challenger)}"
            )
        champ_ids = [candidate_identity(r) for r in champion]
        chal_ids = [candidate_identity(r) for r in challenger]
        champion_ids.extend(champ_ids)
        challenger_ids.extend(chal_ids)
        dates[date] = {
            "population_n": per_date[date]["population_n"],
            "selected_n": len(champ_ids),
            "champion_ids": champ_ids,
            "challenger_ids": chal_ids,
        }

    pop_ids = set(require_unique_population(population_rows))
    if not set(champion_ids).issubset(pop_ids):
        raise ExperimentIntegrityError("champion selection escaped frozen population")
    if not set(challenger_ids).issubset(pop_ids):
        raise ExperimentIntegrityError("challenger selection escaped frozen population")

    return {
        "dates": dates,
        "population_n": len(pop_ids),
        "champion_ids": champion_ids,
        "challenger_ids": challenger_ids,
    }


def select_top_k_per_date(
    rows,
    k,
    champion_score_key="current_prob",
    challenger_score_key="challenger_prob",
):
    """Matched top-K selection independently on every date."""
    if not isinstance(k, int) or k < 0:
        raise ExperimentIntegrityError(
            f"k must be a non-negative integer, got {k!r}"
        )

    grouped = _group_by_date(rows)
    per_date = {}
    for date, date_rows in grouped.items():
        n = min(k, len(date_rows))
        per_date[date] = {
            "population_n": len(date_rows),
            "champion": _rank(date_rows, champion_score_key)[:n],
            "challenger": _rank(date_rows, challenger_score_key)[:n],
        }
    return _selection_result(per_date, rows)


def select_floor_matched_per_date(
    rows,
    floor,
    champion_score_key="current_prob",
    challenger_score_key="challenger_prob",
):
    """Match challenger volume to the champion probability floor per date."""
    try:
        floor = float(floor)
    except (TypeError, ValueError) as exc:
        raise ExperimentIntegrityError(
            f"floor must be numeric, got {floor!r}"
        ) from exc
    if not math.isfinite(floor):
        raise ExperimentIntegrityError(f"floor must be finite, got {floor!r}")

    grouped = _group_by_date(rows)
    per_date = {}
    for date, date_rows in grouped.items():
        champion = [
            row for row in _rank(date_rows, champion_score_key)
            if _finite_score(row, champion_score_key) >= floor
        ]
        n = len(champion)
        per_date[date] = {
            "population_n": len(date_rows),
            "champion": champion,
            "challenger": _rank(date_rows, challenger_score_key)[:n],
        }
    return _selection_result(per_date, rows)


def deterministic_sha256(payload):
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_prediction_freeze(population_rows, selection, metadata=None):
    """Create and hash an outcome-free prediction/selection artifact."""
    frozen_rows = []
    for row in population_rows:
        if "outcome" in row:
            raise ExperimentIntegrityError(
                f"prediction freeze received outcome for {candidate_identity(row)!r}"
            )
        frozen_rows.append(dict(row))

    require_unique_population(frozen_rows)
    frozen_rows.sort(key=_identity_sort_key)
    payload = {
        "metadata": dict(metadata or {}),
        "population": frozen_rows,
        "selection": selection,
    }
    return {"payload": payload, "sha256": deterministic_sha256(payload)}


def _hit_rate(rows):
    if not rows:
        return None
    return sum(int(row["outcome"]) for row in rows) / len(rows)


def selection_anatomy(evaluation_rows, selection):
    """Evaluate already-frozen champion/challenger selections."""
    by_id = require_unique_population(evaluation_rows)
    champion_ids = list(selection["champion_ids"])
    challenger_ids = list(selection["challenger_ids"])

    for cid in champion_ids + challenger_ids:
        if cid not in by_id:
            raise ExperimentIntegrityError(
                f"selected identity missing from evaluation rows: {cid!r}"
            )

    def rows_for(ids):
        rows = [by_id[cid] for cid in ids]
        for row in rows:
            if row.get("outcome") not in (0, 1):
                raise ExperimentIntegrityError(
                    f"invalid outcome for {candidate_identity(row)!r}: "
                    f"{row.get('outcome')!r}"
                )
        return rows

    champion = rows_for(champion_ids)
    challenger = rows_for(challenger_ids)
    champion_set = set(champion_ids)
    challenger_set = set(challenger_ids)
    overlap_ids = [cid for cid in champion_ids if cid in challenger_set]
    removed_ids = [cid for cid in champion_ids if cid not in challenger_set]
    added_ids = [cid for cid in challenger_ids if cid not in champion_set]
    overlap = rows_for(overlap_ids)
    removed = rows_for(removed_ids)
    added = rows_for(added_ids)

    champion_hits = sum(row["outcome"] for row in champion)
    challenger_hits = sum(row["outcome"] for row in challenger)
    champion_rate = _hit_rate(champion)
    challenger_rate = _hit_rate(challenger)
    added_rate = _hit_rate(added)
    removed_rate = _hit_rate(removed)

    return {
        "n_selected": len(champion),
        "champion_hits": champion_hits,
        "challenger_hits": challenger_hits,
        "realized_winner_delta": challenger_hits - champion_hits,
        "champion_hit_rate": champion_rate,
        "challenger_hit_rate": challenger_rate,
        "hit_rate_delta": (
            challenger_rate - champion_rate
            if champion_rate is not None and challenger_rate is not None
            else None
        ),
        "overlap_n": len(overlap),
        "overlap_hit_rate": _hit_rate(overlap),
        "removed_n": len(removed),
        "removed_hit_rate": removed_rate,
        "added_n": len(added),
        "added_hit_rate": added_rate,
        "added_minus_removed_hit_rate": (
            added_rate - removed_rate
            if added_rate is not None and removed_rate is not None
            else None
        ),
        "changed_fraction": len(added) / len(champion) if champion else 0.0,
        "overlap_ids": overlap_ids,
        "removed_ids": removed_ids,
        "added_ids": added_ids,
    }


def _percentile(values, q):
    if not values:
        return None
    if not 0 <= q <= 1:
        raise ValueError("q must be in [0,1]")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    weight = pos - lo
    return xs[lo] * (1 - weight) + xs[hi] * weight


def paired_game_cluster_bootstrap(
    evaluation_rows,
    selection,
    n_replicates=5000,
    seed=20260829,
    min_changed_valid_fraction=0.95,
):
    """Paired game-cluster bootstrap over frozen selected identities."""
    if not isinstance(n_replicates, int) or n_replicates <= 0:
        raise ExperimentIntegrityError(
            "n_replicates must be a positive integer"
        )
    if not 0 <= min_changed_valid_fraction <= 1:
        raise ExperimentIntegrityError(
            "min_changed_valid_fraction must be in [0,1]"
        )

    by_id = require_unique_population(evaluation_rows)
    champion_ids = list(selection["champion_ids"])
    challenger_ids = list(selection["challenger_ids"])
    champion_set = set(champion_ids)
    challenger_set = set(challenger_ids)
    union_ids = champion_set | challenger_set

    for cid in union_ids:
        row = by_id.get(cid)
        if row is None:
            raise ExperimentIntegrityError(
                f"selected identity missing from evaluation rows: {cid!r}"
            )
        if row.get("outcome") not in (0, 1):
            raise ExperimentIntegrityError(
                f"invalid outcome for selected identity {cid!r}"
            )

    games = sorted(
        {by_id[cid]["game_pk"] for cid in union_ids},
        key=repr,
    )
    if not games:
        raise ExperimentIntegrityError(
            "cannot bootstrap an empty selection"
        )

    champion_by_game = defaultdict(list)
    challenger_by_game = defaultdict(list)
    added_by_game = defaultdict(list)
    removed_by_game = defaultdict(list)

    for cid in champion_ids:
        game = by_id[cid]["game_pk"]
        champion_by_game[game].append(by_id[cid])
        if cid not in challenger_set:
            removed_by_game[game].append(by_id[cid])

    for cid in challenger_ids:
        game = by_id[cid]["game_pk"]
        challenger_by_game[game].append(by_id[cid])
        if cid not in champion_set:
            added_by_game[game].append(by_id[cid])

    rng = random.Random(seed)
    overall_deltas = []
    changed_deltas = []
    invalid_changed = 0

    for _ in range(n_replicates):
        sampled_games = [
            rng.choice(games)
            for _ in range(len(games))
        ]
        champion = []
        challenger = []
        added = []
        removed = []

        for game in sampled_games:
            champion.extend(champion_by_game.get(game, ()))
            challenger.extend(challenger_by_game.get(game, ()))
            added.extend(added_by_game.get(game, ()))
            removed.extend(removed_by_game.get(game, ()))

        if not champion or not challenger:
            raise ExperimentIntegrityError(
                "bootstrap replicate lost an entire selected side"
            )

        overall_deltas.append(
            _hit_rate(challenger) - _hit_rate(champion)
        )

        if added and removed:
            changed_deltas.append(
                _hit_rate(added) - _hit_rate(removed)
            )
        else:
            invalid_changed += 1

    valid_fraction = len(changed_deltas) / n_replicates
    changed_estimable = (
        bool(changed_deltas)
        and valid_fraction >= min_changed_valid_fraction
    )

    return {
        "seed": seed,
        "n_replicates": n_replicates,
        "unique_game_clusters": len(games),
        "overall_delta_ci95": [
            _percentile(overall_deltas, 0.025),
            _percentile(overall_deltas, 0.975),
        ],
        "p_overall_delta_le_zero": (
            sum(1 for value in overall_deltas if value <= 0)
            / len(overall_deltas)
        ),
        "changed_valid_replicates": len(changed_deltas),
        "changed_invalid_replicates": invalid_changed,
        "changed_valid_fraction": valid_fraction,
        "changed_estimable": changed_estimable,
        "added_minus_removed_ci95": (
            [
                _percentile(changed_deltas, 0.025),
                _percentile(changed_deltas, 0.975),
            ]
            if changed_estimable
            else None
        ),
        "p_added_minus_removed_le_zero": (
            sum(1 for value in changed_deltas if value <= 0)
            / len(changed_deltas)
            if changed_estimable
            else None
        ),
    }
