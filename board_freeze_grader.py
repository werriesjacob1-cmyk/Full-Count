#!/usr/bin/env python3
"""board_freeze_grader.py -- attach real outcomes to every candidate in a
sealed board_freeze_{date}.json artifact (kept, QC-rejected, AND
lineup-assumed-holdout alike), not just the tiny published-Top-Pick subset
grade_results.py's own results/grades_*.json already covers.

WHY THIS EXISTS: board_freeze.py freezes the complete candidate universe
pregame, by design, so no outcome can contaminate it -- but that also means
the frozen artifact carries zero outcome information on its own. Nobody has
ever measured whether the model's stated probabilities are well-calibrated
on the FULL candidate pool (only on the published Top Pick subset), because
no per-candidate outcome has ever been attached to the full pool. This is
the direct, and only, prerequisite for that rank/argmax calibration analysis
(rank #1 vs #2/#3/top-K/rest, predicted vs realized, same-day candidate
universe, equal-volume, market-specific breakdown) -- board_freeze.py's own
"Next" note names it explicitly. Building that analysis is NOT this module's
job; it only produces the graded population the analysis needs to run.

THIS MODULE ADDS NO NEW GRADING LOGIC. Every hit/miss/ungraded decision is
made by grade_results.grade_pick -- the exact same function that already
grades the published board -- via an adapter that reconstructs, from one
frozen CandidateRecord, the pick-shaped dict grade_pick was written against.
board_freeze.py's own record schema (top-level stat/needs/line, prop_label,
no side/lean field) differs from generate_picks.py's live candidate shape
(nested projection, prop, side, lean) on purpose -- board_freeze.py is a
read-only capture module and reconstructing a gradeable shape is a later,
separate concern, matching this codebase's own game_market_snapshot.py /
game_market_grader.py split for NFL.

READ-ONLY IN, NEW FILE OUT. grade_frozen_board() never mutates its input; it
returns a brand-new, additive mapping keyed back to the frozen board's own
board_sha256 so the two artifacts can always be joined and the sealed
original is never at risk of being overwritten. The source board's own seal
is verified with board_freeze.verify_board_seal() before anything is graded
-- a board that does not verify (tampered, hand-edited, or simply not a real
sealed board) is refused outright, never silently graded.
"""
from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Tuple

import board_freeze as bf
import grade_results as gr
from dashboard.live_state import atomic_write_json

SCHEMA_VERSION = 1
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")

# grade_pick() only reads pick["type"] in its generic fallback branch (the
# `is_pitcher = pick["type"] == "pitcher"` line reached once every earlier
# special-cased stat -- combined_strikeouts/hard_hit_105/hard_hit_110/
# moonshot_420/first_inning_run/nrfi_combined -- has already returned).
# Verified against every score_*() function in generate_picks.py that emits
# one of these stats: score_pitcher() ("strikeouts") and score_pitcher_outs()
# ("pitcher_outs") are the only two that set "type": "pitcher"; every other
# stat reaching the generic branch (hits, hits_runs_rbis, home_runs, runs,
# rbis, singles, doubles, triples, total_bases, walks, stolen_base) is set by
# a "type": "batter" scorer. test_grade_results.py's own `_grade_stat` helper
# hardcodes this identical rule independently, which is corroborating
# evidence this mapping is stable, not just convenient.
_PITCHER_STATS = frozenset(("strikeouts", "pitcher_outs"))
_PITCHER_COMBO_STATS = frozenset(("combined_strikeouts",))

# dashboard/live_state.py's market_side_token() stores exactly these two
# stats' `lean` field (lowercased) as the frozen record's top-level
# `market_side` -- see its own `_FIXED_HALF_RUN_STATS` special case, reused
# verbatim below rather than re-declared with a chance to drift from it.
_FIXED_HALF_RUN_STATS = frozenset(("nrfi_combined", "first_inning_run"))


def _coerce_int(value):
    """board_freeze.py stringifies every game_pk/player_id/combo_player_ids
    for JSON-safe, collision-proof identity (see build_candidate_snapshot).
    grade_results.py's real box-score/schedule data is keyed by native MLB
    integer ids (gamePk from the schedule API, personId from boxscore_data)
    -- a bare string "745001" would never match an int-keyed dict, silently
    stranding every frozen record as permanently ungraded. A synthetic,
    non-numeric id (e.g. nrfi_combined's "nrfi_{game_pk}" player_id, which
    grade_pick's nrfi_combined branch never dereferences) is left untouched
    rather than raising."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _numeric_needs(needs_raw):
    """board_freeze.py stores `needs` as prop_identity_key's STRING
    threshold token (dashboard.live_state._threshold_token: Decimal-
    normalized, trailing zeros stripped, e.g. "1", "0.5"). Parsed back to a
    number here for projection["value"]'s derivation below; grade_pick's own
    threshold math (`float(needs) - 0.5`) parses the string directly either
    way, so this is not independently load-bearing for that half."""
    try:
        return float(needs_raw)
    except (TypeError, ValueError):
        return None


def _to_gradeable_pick(record: Mapping[str, Any]) -> Tuple[Optional[dict], Optional[str]]:
    """Reconstruct a grade_results.grade_pick()-compatible dict from one
    board_freeze.py CandidateRecord.

    Returns (pick, reason). On success, reason is None and pick is ready to
    hand to grade_pick(). When the record's market family needs a field
    grade_pick reads that cannot be safely derived from what board_freeze.py
    actually kept, pick is None and reason is a short, honest explanation --
    grade_frozen_board() turns that into an `ungraded` record with this exact
    reason. Nothing here ever guesses a value grade_pick would treat as real
    settlement evidence.

    FIELD MAPPING (frozen CandidateRecord -> grade_pick pick), and why each
    one is safe:

    * game_pk, player_id, combo_player_ids -- string -> int (see
      _coerce_int). Otherwise identical to the frozen record's own field.
    * team, matchup -- passed through verbatim. NOT used here to derive
      `side` for first_inning_run: grade_pick's own branch (grade_results.py
      ~line 607) already reconstructs `side` from exactly these two fields
      ("pick['team'] must equal one half of pick['matchup']") the moment
      `side` itself is absent, so duplicating that derivation here would
      just be a second place for the identical logic to silently diverge
      from grade_pick's if either one ever changed. Passing team/matchup
      through is therefore sufficient and deliberately does not duplicate
      the derivation.
    * stat, needs (string token) -> projection: {stat, needs (numeric),
      value}. `value` exists only to satisfy grade_pick's non-None guard
      (grade_results.py ~line 741, `if proj is None: ... ungraded`); the
      actual grading threshold always comes from `needs` when present
      (~line 756). value is set to `needs - 0.5` because every single
      `c["projection"] = {...}` assignment across generate_picks.py's
      score_*() functions sets its own "value" to exactly that (e.g.
      score_pitcher_outs: `"value": best["threshold"] - 0.5, "needs":
      best["threshold"]`) -- this is the documented "Over X.5" convention
      the codebase already commits to, not an invented number.
      NOTE (real gap found while building this adapter): board_freeze.py's
      own build_candidate_snapshot reads `projection.get("line")` into the
      frozen record's top-level `line` field, but no score_*() function in
      generate_picks.py ever puts the threshold under a "line" key on the
      FINAL candidate.projection dict -- every one of them uses "value".
      ("line" only ever appears on the per-option dicts _pick_line()/
      _batter_options() choose BETWEEN, before one is copied into
      c["projection"]["value"].) A real frozen board's `line` field is
      therefore expected to be None for every candidate, and is NOT read by
      this adapter -- `needs` (which IS a required, fail-closed field on
      every sealed record; see board_freeze.seal_board's own required-field
      check) is used instead, exactly as described above.
    * prop_label -> prop.
    * market_side -> market_side, verbatim. grade_results._is_under_pick
      checks this exact key already, so passing it through reproduces
      correct Over/Under detection with no new logic.
    * type -- NOT stored on the frozen record at all. Derived purely from
      `stat`: "pitcher" for strikeouts/pitcher_outs, "pitcher_combo" for
      combined_strikeouts, "batter" otherwise. Only the generic fallback
      branch of grade_pick reads pick["type"] (see _PITCHER_STATS' own
      docstring above), and only for stats where this mapping is exhaustive.
    * lean -- NOT stored on the frozen record as its own field, but for
      first_inning_run/nrfi_combined specifically, dashboard.live_state.
      market_side_token() computes the frozen record's `market_side` AS
      `str(candidate.get("lean")).lower()` for exactly these two stats (see
      its own `_FIXED_HALF_RUN_STATS` branch) -- so market_side IS the
      original lean, lowercased, for this family, and uppercasing it back
      ("yrfi"->"YRFI", "nrfi"->"NRFI") is a faithful reconstruction, not a
      guess. If market_side is ever something else for one of these two
      stats (a truly malformed/legacy record), lean cannot be safely
      recovered and this returns an honest ungraded reason instead of
      defaulting to either side.

    NOT SUPPORTED FROM THE FROZEN RECORD ALONE, BY DESIGN: `side` (the
    away/home STARTER identity used to label pitcher_outs/first_inning_run
    candidates -- a completely different concept from `market_side`'s
    over/under/yrfi/nrfi token, despite the similar name) is never
    reconstructed directly here. It happens to be unnecessary: grade_pick
    only ever consults `side` for first_inning_run, and only as a fallback
    already covered by team/matchup above.
    """
    stat = record.get("stat")
    needs_num = _numeric_needs(record.get("needs"))
    market_side = record.get("market_side")

    pick: dict = {
        "candidate_id": record.get("candidate_id"),
        "game_pk": _coerce_int(record.get("game_pk")),
        "player_id": _coerce_int(record.get("player_id")),
        "team": record.get("team"),
        "matchup": record.get("matchup"),
        "prop": record.get("prop_label"),
        "market_side": market_side,
        "projection": {
            "stat": stat,
            "needs": needs_num,
            "value": None if needs_num is None else needs_num - 0.5,
        },
    }
    combo_ids = record.get("combo_player_ids")
    if combo_ids:
        pick["combo_player_ids"] = [_coerce_int(pid) for pid in combo_ids]

    if stat in _PITCHER_STATS:
        pick["type"] = "pitcher"
    elif stat in _PITCHER_COMBO_STATS:
        pick["type"] = "pitcher_combo"
    else:
        pick["type"] = "batter"

    if stat in _FIXED_HALF_RUN_STATS:
        if market_side in ("yrfi", "nrfi"):
            pick["lean"] = market_side.upper()
        else:
            return None, (
                f"cannot safely reconstruct lean for stat={stat!r}: frozen "
                f"market_side={market_side!r} is not 'yrfi'/'nrfi'"
            )

    return pick, None


# Grading-outcome keys grade_pick()/opportunity_context() can add to a pick.
# Listed explicitly (rather than diffed against the adapter's own pick keys)
# so the merge below is obviously an allow-list, never accidentally
# overwriting a frozen record's own field with adapter scaffolding
# (projection/type/market_side/lean/combo_player_ids/candidate_id -- all of
# which grade_pick's return value still carries unchanged from the input
# pick, via its own `{**pick, ...}` construction).
_OUTCOME_KEYS = (
    "grade", "actual", "actual_stat", "threshold", "reason",
    "game_innings", "shortened_game", "fair_test", "opportunity",
    "actual_ip", "pitch_count", "actual_ab", "actual_pa_est",
    "was_substitute", "batting_order",
)


def _grade_one_record(record: Mapping[str, Any], game_statuses, *, date,
                       allow_in_progress: bool) -> dict:
    """Grade one frozen CandidateRecord. Never raises -- an adapter or
    grader failure on a single record degrades to an honest `ungraded`
    entry on that record alone, the same "one bad record must not take down
    the whole run" discipline grade_results.grade_day() already applies to
    generate_picks.py's own picks."""
    out = copy.deepcopy(dict(record))
    try:
        pick, reason = _to_gradeable_pick(record)
    except Exception as exc:  # pragma: no cover - defensive, mirrors grade_day()
        out["grade"] = "ungraded"
        out["reason"] = f"adapter error: {exc}"
        return out
    if pick is None:
        out["grade"] = "ungraded"
        out["reason"] = reason
        return out
    try:
        result = gr.grade_pick(pick, game_statuses, date=date,
                                allow_in_progress=allow_in_progress)
    except Exception as exc:  # pragma: no cover - defensive, mirrors grade_day()
        out["grade"] = "ungraded"
        out["reason"] = f"grader error: {exc}"
        return out
    for key in _OUTCOME_KEYS:
        if key in result:
            out[key] = result[key]
    return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def grade_frozen_board(frozen_board: Mapping[str, Any], *, allow_in_progress: bool = False,
                        game_statuses: Optional[Mapping[Any, Any]] = None,
                        date: Optional[str] = None) -> dict:
    """Grade every CandidateRecord of one sealed board_freeze_{date}.json
    artifact against real outcomes.

    Fails closed: board_freeze.verify_board_seal(frozen_board) is called
    first and its BoardFreezeError propagates unchanged on any mismatch
    (missing/short board_sha256, rebuilt-hash mismatch, or content that
    doesn't byte-match its own canonical seal) -- an unverified or tampered
    board is never graded, not even partially.

    Read-only in, new artifact out: `frozen_board` (and every record inside
    it) is deep-copied before any grading field is attached, so the caller's
    own in-memory object -- and, by extension, the sealed file it was loaded
    from -- is left untouched. The returned mapping is a SEPARATE, additive
    artifact keyed back to the source board via `source_board_sha256`.

    game_statuses/date let a caller supply already-fetched game statuses, as
    grade_results.grade_day() already does for the published board, instead
    of this function re-fetching them; when omitted, this fetches via
    grade_results.fetch_game_statuses(date) -- the exact same helper
    grade_day() uses, so this introduces no second box-score-fetching path.
    allow_in_progress is passed straight through to grade_pick, unchanged.
    """
    board_sha256 = bf.verify_board_seal(frozen_board)  # raises on any tamper/mismatch

    resolved_date = date or frozen_board.get("date")
    if game_statuses is None:
        game_statuses = gr.fetch_game_statuses(resolved_date) if resolved_date else {}

    records: Sequence[Mapping[str, Any]] = frozen_board.get("records") or []
    graded_records = [
        _grade_one_record(record, game_statuses, date=resolved_date,
                          allow_in_progress=allow_in_progress)
        for record in records
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "sport": "MLB",
        "evidence_class": "PROSPECTIVE_FULL_BOARD_GRADED",
        "date": frozen_board.get("date"),
        "source_board_sha256": board_sha256,
        "graded_at": _utc_now_iso(),
        "record_count": len(graded_records),
        "records": graded_records,
    }


def graded_board_path(date: str) -> str:
    """Matches generate_picks.py's own board_freeze output-path convention
    (`os.path.join(OUTPUT_DIR, f"board_freeze_{date}.json")`) and
    grade_results.py's own OUTPUT_DIR env override, one path segment over."""
    return os.path.join(OUTPUT_DIR, f"board_freeze_graded_{date}.json")


def write_graded_board(graded_board: Mapping[str, Any], path: str) -> None:
    """Atomic write -- same tempfile+fsync+os.replace discipline as
    board_freeze.write_frozen_board and dashboard/live_state.py's own
    atomic_write_json, so a partially-written graded artifact is never
    observable by a concurrent reader. This writes a NEW file; it never
    touches the sealed board_freeze_{date}.json this was graded from."""
    atomic_write_json(path, dict(graded_board))
