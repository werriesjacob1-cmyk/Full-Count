#!/usr/bin/env python3
"""The first sealed, prospectively gradeable B0-versus-frozen-challenger
receptions comparison record.

Connects, for one real receptions candidate:

  strictly-prior player history -> B0 projection & score (existing,
  unmodified `receptions_shadow.py`, the live control)
       +
  the same real projection/line -> frozen NB challenger comparison
  (existing, unmodified `receptions_frozen_challenger.py`, research-only)
       ->
  one sealed record via the existing, unmodified `shadow_snapshot.seal_snapshot`.

This module does NOT fetch live data itself, does NOT get imported by any
`.github/workflows/` file, and does NOT alter B0's own live behavior in any
way -- it takes B0's already-computed real score as an input and never
recomputes it. It is the connective layer the market itself was missing:
`receptions_shadow.py` (B0 control) and `receptions_frozen_challenger.py`
(NB challenger) each work standalone; nothing joined them into one sealed,
paired, gradeable evidence record until now.

Sealed via the EXACT same `shadow_snapshot.seal_snapshot` function and
schema the live B0 board itself uses (`market="receptions"`,
`decision_status` in {SHADOW_ONLY, QUARANTINED}) -- so this reuses the
live evidence-sealing contract exactly rather than inventing a second one,
while every record carries explicit `challenger_*`/`prediction_source`
fields so it can never be confused with a live B0-only record downstream.
Records built here are written to their own separate, clearly-labeled
research output path -- never mixed into the live board's own sealed
evidence file.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nfl.prospective.shadow_snapshot import seal_snapshot

CHALLENGER_PREDICTION_SOURCE = "B0_VS_NEGATIVE_BINOMIAL_POOLED_CHALLENGER_V1"


class ChallengerSnapshotError(ValueError):
    """Raised on malformed input. Never silently substitutes a guess."""


def _is_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def _validate_real_b0_score(b0_score: Any) -> None:
    """Validate the FULL real shape of `receptions_shadow.score_shadow_candidate`'s
    output, not just one key's presence -- a dict with a lone plausible-looking
    `model_over_probability` and nothing else real is still rejected."""
    if not isinstance(b0_score, Mapping):
        raise ChallengerSnapshotError("b0_score must be a real score_shadow_candidate result")
    required_keys = (
        "model_over_probability",
        "model_under_probability",
        "market_fair_over_probability",
        "market_fair_under_probability",
        "probability_method",
        "residual_n",
    )
    missing = [k for k in required_keys if k not in b0_score]
    if missing:
        raise ChallengerSnapshotError(
            f"b0_score must be a real score_shadow_candidate result (missing: {missing})"
        )
    if not _is_probability(b0_score["model_over_probability"]) or not _is_probability(
        b0_score["model_under_probability"]
    ):
        raise ChallengerSnapshotError(
            "b0_score.model_over_probability/model_under_probability must each be in [0, 1]"
        )
    b0_mass = b0_score["model_over_probability"] + b0_score["model_under_probability"]
    if abs(b0_mass - 1.0) > 1e-6:
        raise ChallengerSnapshotError(
            f"b0_score.model_over_probability + model_under_probability must sum to "
            f"1.0 (got {b0_mass!r}) -- receptions_shadow.empirical_side_probabilities "
            f"always produces under = 1.0 - over exactly, so a mismatch here can only "
            f"be a fabricated pair, never a real score_shadow_candidate result"
        )


def _validate_real_challenger_comparison(challenger_comparison: Any) -> None:
    """Validate the FULL real shape of
    `receptions_frozen_challenger.compare_b0_vs_frozen_challenger`'s output,
    including the nested `challenger` dict's own real shape -- rejects a
    `challenger` key holding a non-dict or a dict missing its real fields."""
    if not isinstance(challenger_comparison, Mapping) or "challenger" not in challenger_comparison:
        raise ChallengerSnapshotError(
            "challenger_comparison must be a real compare_b0_vs_frozen_challenger result"
        )
    if "b0" not in challenger_comparison or not isinstance(challenger_comparison["b0"], Mapping):
        raise ChallengerSnapshotError(
            "challenger_comparison.b0 must be a real dict with over/under probabilities"
        )
    challenger = challenger_comparison["challenger"]
    if not isinstance(challenger, Mapping):
        raise ChallengerSnapshotError("challenger_comparison.challenger must be a real dict")
    required_keys = ("over", "under", "push", "distribution_family", "alpha_used")
    missing = [k for k in required_keys if k not in challenger]
    if missing:
        raise ChallengerSnapshotError(
            f"challenger_comparison.challenger must be a real negative_binomial_side_"
            f"probabilities result (missing: {missing})"
        )
    if not all(_is_probability(challenger[k]) for k in ("over", "under", "push")):
        raise ChallengerSnapshotError(
            "challenger_comparison.challenger.over/under/push must each be in [0, 1]"
        )
    mass = challenger["over"] + challenger["under"] + challenger["push"]
    if abs(mass - 1.0) > 1e-6:
        raise ChallengerSnapshotError(
            f"challenger_comparison.challenger.over+under+push must sum to 1.0 (got {mass!r})"
        )


def build_challenger_snapshot_record(
    *,
    event_id: str,
    market_id: str,
    gsis_id: str,
    player_name: str,
    team: str,
    event_open_date: str,
    line: float,
    over_odds: Any,
    under_odds: Any,
    captured_at: str,
    availability_status: str,
    decision_status: str,
    b0_score: Mapping[str, Any],
    challenger_comparison: Mapping[str, Any],
    challenger_model_version: str,
    source_vintage: str,
    feature_cutoff: str,
) -> dict[str, Any]:
    """Assemble one `shadow_snapshot`-ready record pairing B0's real,
    already-computed score (`receptions_shadow.score_shadow_candidate`'s
    real output, supplied by the caller -- never recomputed here) with the
    frozen NB challenger's comparison for the IDENTICAL real candidate.

    Every field the mission requires is recorded explicitly: exact model
    version (`challenger_model_version`, plus B0's own `b0_score` carries
    its own real numbers), source identity (`gsis_id`, `event_id`,
    `market_id`), feature cutoff (`feature_cutoff` -- the last real
    strictly-prior game this candidate's history actually used),
    sportsbook offer (`line`/`over_odds`/`under_odds`), prediction (both
    sides' probabilities, inside `b0_score`/`challenger_comparison`),
    decision (`decision_status`), and evidence hash (attached by
    `seal_snapshot` itself as `snapshot_sha256` once sealed).
    """
    for name, value in (
        ("event_id", event_id), ("market_id", market_id), ("gsis_id", gsis_id),
        ("player_name", player_name), ("team", team),
        ("event_open_date", event_open_date), ("captured_at", captured_at),
        ("availability_status", availability_status),
        ("decision_status", decision_status),
        ("challenger_model_version", challenger_model_version),
        ("source_vintage", source_vintage), ("feature_cutoff", feature_cutoff),
    ):
        if not str(value or "").strip():
            raise ChallengerSnapshotError(f"missing required field: {name}")

    _validate_real_b0_score(b0_score)
    _validate_real_challenger_comparison(challenger_comparison)

    return {
        "event_id": str(event_id),
        "market_id": str(market_id),
        "gsis_id": str(gsis_id),
        "market": "receptions",
        "player_name": str(player_name),
        "team": str(team),
        "event_open_date": str(event_open_date),
        "line": float(line),
        "over_odds": over_odds,
        "under_odds": under_odds,
        "captured_at": str(captured_at),
        "availability_status": str(availability_status),
        "decision_status": str(decision_status),
        "b0_score": dict(b0_score),
        "challenger_comparison": dict(challenger_comparison),
        "prediction_source": CHALLENGER_PREDICTION_SOURCE,
        "challenger_model_version": str(challenger_model_version),
        "source_vintage": str(source_vintage),
        "feature_cutoff": str(feature_cutoff),
        "evidence_status": "RESEARCH_ONLY_NOT_PROMOTED",
    }


def seal_challenger_snapshot(
    records: Sequence[Mapping[str, Any]],
    *,
    slate_date: str,
    code_sha: str,
    source_vintage: str,
    sealed_at: str,
) -> dict[str, Any]:
    """Seal one or more challenger snapshot records via the real, unmodified
    `shadow_snapshot.seal_snapshot` -- the exact same sealing contract the
    live B0 board itself uses. Returns the sealed snapshot, including its
    real `snapshot_sha256` evidence hash."""
    return seal_snapshot(
        records, slate_date=slate_date, code_sha=code_sha,
        source_vintage=source_vintage, sealed_at=sealed_at,
    )


def write_challenger_evidence_atomically(path: Path | str, payload: Mapping[str, Any]) -> None:
    """Write the sealed challenger-comparison evidence to `path` atomically.

    Serializes to a temporary file in the same directory, validates it by
    reading the JSON back, then `os.replace()`s it onto `path` -- a single
    atomic rename on the same filesystem. A crash, disk-full error, or any
    other exception at any point before that replace leaves `path`
    completely untouched -- it is never created, truncated, or overwritten
    with a partial document -- and the temp file is always removed on
    failure so it never lingers as a stray artifact either. This is what
    stops a mid-write failure from leaving a corrupted file at the exact
    path `actions/upload-artifact` later globs indiscriminately, which
    would otherwise be indistinguishable from valid sealed evidence.
    """
    path = Path(path)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        with tmp_path.open("r", encoding="utf-8") as f:
            json.load(f)  # validate before it can ever become the final path
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


__all__ = [
    "CHALLENGER_PREDICTION_SOURCE",
    "ChallengerSnapshotError",
    "build_challenger_snapshot_record",
    "seal_challenger_snapshot",
    "write_challenger_evidence_atomically",
]
