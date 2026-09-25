#!/usr/bin/env python3
"""Freeze the complete full-board MLB candidate universe -- every candidate
considered tonight, eligible AND rejected -- at the exact generation/
selection boundary in generate_picks.py's main(), before grading or any
postgame process can touch it.

WHY THIS EXISTS: PR #131's two independent investigations (a selector/argmax
reconstruction and a hits_runs_rbis mechanism trace) converged on the same
missing artifact. results/grades_*.json's `picks` field is regenerated at
GRADING time, not preserved from generation time -- proven via a failed join
(16% match rate) and a direct spot-check (a real published Top Pick's own
player_id was entirely absent from that date's `picks` array). Every past
calibration evaluation therefore measured the wrong population: the market's
Platt calibrator, and its 2026-09-14 recheck, were both fit/evaluated on the
full candidate pool, never on the argmax-selected/published subset that the
audited 371-grade record shows is the one that's actually overconfident.
Testing that winner's-curse/selection-effect hypothesis requires being able
to replay, for any past slate, exactly which candidates existed, which were
rejected and why, how they ranked, and which one(s) were selected -- which
requires freezing that state BEFORE any of it can be overwritten or
contaminated by outcomes.

THIS MODULE IS INSTRUMENTATION ONLY. It performs NO new scoring, no new
probability computation, no new calibration, no new ranking, and no new
eligibility decision. Every field on every record is copied verbatim from a
value some earlier stage of generate_picks.py's pipeline already computed
(score_batter/score_pitcher, attach_hit_probabilities, apply_calibration,
attach_market_prices, quality_control, rank_for_board, select_main_board,
recommendation.attach_recommendations). Selection-surface membership
(top pick / category board / moonshot / shadow) is read by recomputing each
candidate's own stable identity and checking pool membership -- never by
re-deciding who belongs where. Nothing here can change what generate_picks.py
already decided; it can only fail closed if that decision cannot be safely
replayed later.

Identity reuses dashboard/live_state.py's proven v2 canonical_prop_id scheme
verbatim -- the same "fc2:{game_pk}:player-{player_id}:{stat}:{needs}:{side}"
identity already used for public_top_picks -- rather than inventing a second
identity scheme for the same candidates. Matching by that content-derived
identity (not Python object identity) is deliberate: by_category/moonshots/
deep_moonshots/shadow_tracking are built as fresh copied dicts with no shared
object identity to the `candidates` list they were drawn from (see
generate_picks.main()'s own comment on this), so `id()`-based membership
would silently under-count every selection surface except the base pool.

Sealed with a SHA-256 over canonical JSON, following the same discipline as
nfl/prospective/game_market_snapshot.py's seal_game_market_snapshot and
nfl/prospective/shadow_snapshot.py's seal_snapshot: the artifact must be
sealed strictly before the earliest first pitch on the slate (so no outcome
could exist yet for any game it covers), and re-verifying a sealed board
means rebuilding it byte-for-byte from its own stored content and requiring
an exact hash match, never trusting a stored hash blindly.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dashboard.live_state import atomic_write_json, canonical_prop_id, prop_identity_key

SCHEMA_VERSION = 1
BOARD_FREEZE_VERSION = "1.0.0"

QC_STATUSES = frozenset(("kept", "qc_rejected", "lineup_assumed_holdout"))
REQUIRED_PROVENANCE_FIELDS = (
    "model_version", "selection_policy_version", "calibration_version",
    "feature_version",
)


class BoardFreezeError(ValueError):
    """Raised when the full-board candidate universe cannot be sealed safely."""


def _require(value, field):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise BoardFreezeError(f"board freeze missing required field: {field}")
    return value


def _dt(value, field):
    raw = _require(value, field)
    normalized = raw[:-1] + "+00:00" if isinstance(raw, str) and raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except (ValueError, TypeError) as exc:
        raise BoardFreezeError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ids(pool: Sequence[Mapping[str, Any]]) -> set:
    return {canonical_prop_id(c) for c in pool}


def build_candidate_snapshot(candidate, *, qc_status, rank_lookup, gated_ids,
                              positive_read_ids, no_read_ids, top10_ids,
                              category_ids, moonshot_ids, deep_moonshot_ids,
                              shadow_ids, provenance, board_generated_at):
    """One CandidateRecord: the complete replayable state of a single
    candidate at the generation/selection boundary. Pure/read-only -- every
    field is copied, never derived, from the already-scored candidate dict."""
    if qc_status not in QC_STATUSES:
        raise BoardFreezeError(f"unsupported qc_status: {qc_status}")

    game_pk, subject, stat, needs, side = prop_identity_key(candidate)
    candidate_id = canonical_prop_id(candidate)
    projection = candidate.get("projection") or {}

    cleared_quality_gate = candidate_id in gated_ids
    if candidate_id in positive_read_ids:
        cleared_positive_read_floor = True
    elif candidate_id in no_read_ids:
        cleared_positive_read_floor = False
    else:
        # Never reached the positive-read-floor check at all -- either
        # QC-rejected/lineup-assumed-holdout before scoring gates run, or
        # did not clear the quality gate in the first place.
        cleared_positive_read_floor = None

    rejection_reason = None
    if qc_status == "qc_rejected":
        rejection_reason = _require(candidate.get("qc_reason"), "qc_reason")
    elif qc_status == "lineup_assumed_holdout":
        rejection_reason = ("lineup assumed from last-known batting order -- held out "
                             "of the graded board by generate_picks.quality_control, not "
                             "a real confirmed lineup read")
    elif not cleared_quality_gate:
        rejection_reason = "score below MIN_QUALITY_SCORE gate"
    elif cleared_positive_read_floor is False:
        rejection_reason = ("did not clear the positive-read floor (lift below "
                             "MIN_POSITIVE_LIFT for this market)")

    return {
        "candidate_id": candidate_id,
        "identity_version": 2,
        "game_pk": str(game_pk),
        "player_id": (str(candidate["player_id"])
                      if candidate.get("player_id") is not None else None),
        "combo_player_ids": ([str(p) for p in candidate["combo_player_ids"]]
                             if candidate.get("combo_player_ids") else None),
        "player_name": candidate.get("name"),
        "team": candidate.get("team"),
        "matchup": candidate.get("matchup"),
        "stat": stat,
        "needs": needs,
        # generate_picks.py's score_*() functions universally set
        # projection["value"], never "line" -- "line" only exists on the
        # pre-selection option dicts _pick_line()/_batter_options() choose
        # between, not the final candidate. Reading "line" here left this
        # field None on every real frozen record; found by the board-freeze
        # grader's own adapter work (see test_real_projection_schema_never_
        # carries_a_line_key_so_frozen_line_is_none in test_board_freeze_
        # grader.py) and fixed here at the source instead of leaving every
        # consumer to work around it independently.
        "line": projection.get("value"),
        "market_side": side,
        "prop_label": candidate.get("prop"),
        "prediction": {
            "hit_probability": candidate.get("hit_probability"),
            "raw_hit_probability": candidate.get("raw_hit_probability"),
            "calibrated_by": candidate.get("calibrated_by"),
            "prob_ci": candidate.get("prob_ci"),
            "prob_ci_source": candidate.get("prob_ci_source"),
            "reliability": candidate.get("reliability"),
            "sample_n": candidate.get("sample_n"),
            "lift": candidate.get("lift"),
            "stable_lift": candidate.get("stable_lift"),
            "base_rate": candidate.get("base_rate"),
            "score": candidate.get("score"),
        },
        "market": {
            "market_odds": candidate.get("market_odds"),
            "market_implied": candidate.get("market_implied"),
            "market_edge": candidate.get("market_edge"),
            "market_hold": candidate.get("market_hold"),
            "estimated_odds": candidate.get("estimated_odds"),
            "max_acceptable_price": candidate.get("max_acceptable_price"),
            "price_clears": candidate.get("price_clears"),
        },
        "eligibility": {
            "qc_status": qc_status,
            "qc_reason": candidate.get("qc_reason"),
            "lineup_assumed": bool(candidate.get("lineup_assumed")),
            "cleared_quality_gate": cleared_quality_gate,
            "cleared_positive_read_floor": cleared_positive_read_floor,
            "rejection_reason": rejection_reason,
        },
        "selector": {
            "final_rank": rank_lookup.get(candidate_id),
            "recommendation_status": candidate.get("status"),
            "status_reasons": candidate.get("status_reasons"),
            "selected_top_pick": candidate_id in top10_ids,
            "selected_category_board": candidate_id in category_ids,
            "selected_moonshot": candidate_id in moonshot_ids,
            "selected_deep_moonshot": candidate_id in deep_moonshot_ids,
            "selected_shadow": candidate_id in shadow_ids,
        },
        "provenance": {
            "model_version": provenance.get("model_version"),
            "selection_policy_version": provenance.get("selection_policy_version"),
            "calibration_version": provenance.get("calibration_version"),
            "feature_version": provenance.get("feature_version"),
            "git_sha": provenance.get("git_sha"),
        },
        "generation_timestamp": board_generated_at,
    }


def freeze_board(*, date, board_generated_at, candidates, qc_rejected,
                  assumed_lineup, gated, with_read, no_read, ranked, top10,
                  by_category, moonshots, deep_moonshots, shadow_tracking,
                  provenance):
    """Build every CandidateRecord for one slate from already-computed pool
    memberships. Raises BoardFreezeError on any identity collision or
    missing required field rather than silently dropping a candidate --
    the frozen universe must be complete or the freeze must fail, never
    partially succeed."""
    rank_lookup = {canonical_prop_id(c): i + 1 for i, c in enumerate(ranked)}
    gated_ids = _ids(gated)
    positive_read_ids = _ids(with_read)
    no_read_ids = _ids(no_read)
    top10_ids = _ids(top10)
    category_ids = _ids([c for entries in by_category.values() for c in entries])
    moonshot_ids = _ids(moonshots)
    deep_moonshot_ids = _ids(deep_moonshots)
    shadow_ids = _ids([c for entries in shadow_tracking.values() for c in entries])

    seen: dict[str, str] = {}
    records = []
    for qc_status, pool in (("kept", candidates),
                             ("qc_rejected", qc_rejected),
                             ("lineup_assumed_holdout", assumed_lineup)):
        for candidate in pool:
            record = build_candidate_snapshot(
                candidate, qc_status=qc_status, rank_lookup=rank_lookup,
                gated_ids=gated_ids, positive_read_ids=positive_read_ids,
                no_read_ids=no_read_ids, top10_ids=top10_ids,
                category_ids=category_ids, moonshot_ids=moonshot_ids,
                deep_moonshot_ids=deep_moonshot_ids, shadow_ids=shadow_ids,
                provenance=provenance, board_generated_at=board_generated_at,
            )
            cid = record["candidate_id"]
            if cid in seen:
                raise BoardFreezeError(
                    f"duplicate candidate_id {cid!r} across board buckets "
                    f"({seen[cid]!r} and {qc_status!r}) -- identity is not unique"
                )
            seen[cid] = qc_status
            records.append(record)

    if not records:
        raise BoardFreezeError("board freeze requires at least one candidate record")

    return records


def seal_board(*, date, board_generated_at, sealed_at, game_start_times,
               records, provenance):
    """Seal one slate's frozen candidate universe. game_start_times must map
    every game_pk referenced by `records` to its ISO-8601 scheduled first
    pitch -- embedded verbatim in the sealed body so verify_board_seal can
    rebuild the chronology check from the artifact alone, with no external
    re-fetch. Fails closed on a missing start time, a seal that isn't
    strictly pregame, or a record missing required provenance."""
    slate = _require(date, "date")
    board_dt = _dt(board_generated_at, "board_generated_at")
    sealed_dt = _dt(sealed_at, "sealed_at")
    if sealed_dt < board_dt:
        raise BoardFreezeError("sealed_at cannot precede board_generated_at")
    if not records:
        raise BoardFreezeError("board freeze requires at least one candidate record")

    ids = [r["candidate_id"] for r in records]
    if len(ids) != len(set(ids)):
        raise BoardFreezeError("duplicate candidate_id in frozen board")

    referenced_games = sorted({r["game_pk"] for r in records})
    embedded_starts: dict[str, str] = {}
    starts = []
    for game_pk in referenced_games:
        raw = (game_start_times or {}).get(game_pk)
        if not raw:
            raise BoardFreezeError(
                f"missing game_start_utc for game_pk {game_pk!r} -- cannot verify "
                f"pregame chronology for this candidate"
            )
        embedded_starts[game_pk] = raw
        starts.append(_dt(raw, f"game_start_utc[{game_pk}]"))
    earliest_start = min(starts)
    if sealed_dt >= earliest_start:
        raise BoardFreezeError(
            "board must be sealed strictly before the earliest first pitch on the slate "
            "-- a later seal could already reflect outcome information for that game"
        )

    for field in REQUIRED_PROVENANCE_FIELDS:
        if not provenance.get(field):
            raise BoardFreezeError(f"board freeze missing required provenance field: {field}")

    for record in records:
        for field in ("candidate_id", "game_pk", "stat", "needs", "market_side",
                      "generation_timestamp"):
            if record.get(field) is None:
                raise BoardFreezeError(
                    f"candidate {record.get('candidate_id')!r} missing required "
                    f"replay field: {field}"
                )
        for field in REQUIRED_PROVENANCE_FIELDS:
            if not (record.get("provenance") or {}).get(field):
                raise BoardFreezeError(
                    f"candidate {record.get('candidate_id')!r} missing required "
                    f"provenance field: {field}"
                )
        if record.get("eligibility", {}).get("qc_status") not in QC_STATUSES:
            raise BoardFreezeError(
                f"candidate {record.get('candidate_id')!r} missing a valid qc_status"
            )

    body = {
        "schema_version": SCHEMA_VERSION,
        "board_freeze_version": BOARD_FREEZE_VERSION,
        "sport": "MLB",
        "evidence_class": "PROSPECTIVE_FULL_BOARD",
        "date": slate,
        "board_generated_at": board_generated_at,
        "sealed_at": sealed_at,
        "game_start_times": embedded_starts,
        "record_count": len(records),
        "records": sorted(records, key=lambda r: r["candidate_id"]),
        "provenance": {field: provenance.get(field) for field in REQUIRED_PROVENANCE_FIELDS + ("git_sha",)},
        "research_only": True,
        "public_eligible": False,
    }
    return {**body, "board_sha256": _canonical_hash(body)}


def verify_board_seal(frozen: Mapping[str, Any]) -> str:
    """Rebuild a sealed board from its own stored content and require
    byte-semantic equality -- never trust a stored hash without rebuilding."""
    if not isinstance(frozen, Mapping):
        raise BoardFreezeError("frozen board must be a mapping")
    expected = frozen.get("board_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise BoardFreezeError("frozen board missing board_sha256")
    rebuilt = seal_board(
        date=frozen.get("date"),
        board_generated_at=frozen.get("board_generated_at"),
        sealed_at=frozen.get("sealed_at"),
        game_start_times=frozen.get("game_start_times"),
        records=frozen.get("records"),
        provenance=frozen.get("provenance") or {},
    )
    if rebuilt["board_sha256"] != expected:
        raise BoardFreezeError("frozen board hash mismatch")
    if dict(frozen) != rebuilt:
        raise BoardFreezeError("frozen board content does not match canonical seal")
    return expected


def write_frozen_board(frozen: Mapping[str, Any], path: str) -> None:
    """Atomic write, matching dashboard/live_state.py's own tempfile+fsync+
    os.replace pattern -- a partially-written frozen board must never be
    observable by a concurrent reader."""
    atomic_write_json(path, dict(frozen))
