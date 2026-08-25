#!/usr/bin/env python3
"""candidate_funnel_logger.py -- PROSPECTIVE, non-public, research-only
candidate-funnel log. Built 2026-08-25 because
candidate_dataset_feasibility_2026-08-25.md found that historical backtest
rows cannot answer Priority 5 (within-slate pairwise selection: "why did the
better candidate win") -- build_candidates() keeps only one candidate per
batter on the main board, discarding the alternatives.

WHAT THIS ACTUALLY EXPLOITS: generate_picks.py's own `_keep_options()` /
`line_options` mechanism (see that function's own docstring, "THE MODEL
ALREADY COMPUTES THIS AND THREW IT AWAY") already preserves every alternate
line/stat a batter's candidate could have been priced on -- `_build_and_score()`
already attaches `line_options` to every batter candidate before this module
ever runs. This module does not compute anything generate_picks.py doesn't
already compute; it PERSISTS what generate_picks.py already discards after
picking one board line per batter, plus a DECISION-layer gate trace via
recommendation_funnel.py's existing, unmodified gate_trace() (Item 8).

SAFETY CONTRACT (see test_candidate_funnel_logger.py for the enforced
version of every claim below):
  - Every function in this module that touches a `candidate` dict is
    READ-ONLY -- it only ever calls .get() on candidates/options, never
    assigns into them. Verified by a test that deep-copies input candidates
    and asserts byte-identical equality after every call in this module.
  - This module NEVER calls write_json()/write_markdown() or touches
    output/picks_*.json, docs/data.json, docs/live.json, or the public
    registry. It is a pure research artifact generator.
  - run_live_snapshot() (the one function that actually calls
    generate_picks._build_and_score()) uses the EXACT SAME isolation
    pattern dashboard/build_dashboard.py's run_live_fetch() already uses in
    production (redirect OUTPUT_DIR/PLAYERS_DIR to a scratch dir BEFORE
    importing generate_picks, since those are read once at import time) --
    it runs a SEPARATE, independent scoring pass in its own process/scratch
    directory, so it structurally cannot affect anything the real
    production pipeline computed or wrote for the same slate.

STORAGE DESIGN: one append-only gitignored JSONL file per date
(backtest/candidate_funnel_{date}.jsonl -- matches the existing
`backtest/*.jsonl` gitignore rule, no new entry needed). Snapshot/dedup
semantics: a candidate's identity is (date, game_pk, player_id or
combo_player_ids, stat, needs) -- stable across repeated runs on the same
date. `append_new_snapshots()` reads the file's own last-seen content hash
per identity and appends a new row ONLY when something about that candidate
materially changed (a new hash) since the last time it was logged that day
-- so re-running this near-hourly as odds/lineups firm up produces a real,
bounded changelog, not a byte-for-byte duplicate every run.

    /tmp/mlbvenv/bin/python3 backtest/candidate_funnel_logger.py
"""
from __future__ import annotations

import hashlib
import json
import os

DEFAULT_OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Fields that legitimately differ run-to-run without representing a
# meaningful change worth a new changelog row (timestamps/provenance-of-
# THIS-snapshot, not provenance-of-the-candidate).
_HASH_EXCLUDE_KEYS = frozenset(("generated_at",))


def candidate_identity(candidate, *, date):
    """Stable identity string: (date, game_pk, player_id or
    combo_player_ids, stat, needs). Deliberately does NOT include `line`
    (the threshold) -- line changes as odds move, and this module logs the
    CANDIDATE's identity across those changes, with the changing fields
    (including line) captured inside the snapshot content itself."""
    projection = candidate.get("projection") or {}
    stat = projection.get("stat") or candidate.get("stat")
    needs = projection.get("needs")
    player_key = candidate.get("combo_player_ids") or candidate.get("player_id")
    return f"{date}:{candidate.get('game_pk')}:{player_key}:{stat}:{needs}"


def _alt_line_record(opt):
    """One entry of a candidate's own line_options -- an alternate
    line/stat this SAME batter could have been bet on, read-only, matching
    _keep_options()'s own trimmed shape exactly (stat/needs/line/prob/
    base_rate/lift/basis/ci)."""
    return {
        "stat": opt.get("stat"), "needs": opt.get("needs"),
        "line": opt.get("line"), "prob": opt.get("prob"),
        "base_rate": opt.get("base_rate"), "lift": opt.get("lift"),
        "basis": opt.get("basis"), "ci": opt.get("ci"),
    }


def funnel_record_from_candidate(candidate, *, date, generated_at=None,
                                  code_git_sha=None, gate_trace=None,
                                  quality_control_status=None,
                                  quality_control_reason=None):
    """One research record for one candidate -- read-only over `candidate`
    (only .get() calls; see this module's own safety-contract docstring).
    `gate_trace` is recommendation_funnel.classify_with_trace()'s own
    output, reused verbatim, never reimplemented. OUTCOME is deliberately
    NOT a field this function ever populates -- outcome/grading is a
    strictly separate, later step (see this module's docstring: "outcome
    grading is separate from pregame features") so a pregame record can
    never accidentally carry postgame information."""
    projection = candidate.get("projection") or {}
    line_options = candidate.get("line_options") or []
    alt_lines = [_alt_line_record(o) for o in line_options]

    record = {
        "identity": {
            "candidate_id": candidate_identity(candidate, date=date),
            "date": date, "game_pk": candidate.get("game_pk"),
            "game_start": candidate.get("game_start"),
            "player_id": candidate.get("player_id"),
            "combo_player_ids": candidate.get("combo_player_ids"),
            "player_name": candidate.get("name"),
            "team": candidate.get("team"), "matchup": candidate.get("matchup"),
            "stat": projection.get("stat") or candidate.get("stat"),
            "side": candidate.get("bet_side") or candidate.get("market_side"),
            "threshold": projection.get("value"),
            "needs": projection.get("needs"),
        },
        "prediction": {
            "hit_probability": candidate.get("hit_probability"),
            "raw_hit_probability": candidate.get("raw_hit_probability"),
            "probability_basis": candidate.get("probability_basis"),
            "prob_ci": candidate.get("prob_ci"),
            "sample_n": candidate.get("sample_n"),
            "base_rate": candidate.get("base_rate"),
            "lift": candidate.get("lift"),
            "stable_lift": candidate.get("stable_lift"),
        },
        "market": {
            "market_odds": candidate.get("market_odds"),
            "market_implied": candidate.get("market_implied"),
            "market_edge": candidate.get("market_edge"),
            "market_hold": candidate.get("market_hold"),
            "price_clears": candidate.get("price_clears"),
            "market_fetch_state": candidate.get("market_fetch_state"),
            "market_observed_at": candidate.get("market_observed_at"),
        },
        "evidence": {
            "reliability": candidate.get("reliability"),
            "reliability_note": candidate.get("reliability_note"),
            "lineup_assumed": candidate.get("lineup_assumed"),
            "score": candidate.get("score"),
            "cat_matchup": candidate.get("cat_matchup"),
            "cat_recent_form": candidate.get("cat_recent_form"),
            "cat_environment": candidate.get("cat_environment"),
            "cat_baseline_skill": candidate.get("cat_baseline_skill"),
            "cat_context": candidate.get("cat_context"),
            "signals": candidate.get("signals"),
        },
        "decision": {
            "recommendation_status": candidate.get("status"),
            "status_reasons": candidate.get("status_reasons"),
            "quality_control_status": quality_control_status,
            "quality_control_reason": quality_control_reason,
            "gates": (gate_trace or {}).get("gates"),
            "blocking_gate": (gate_trace or {}).get("blocking_gate"),
            "alt_lines": alt_lines,
            "n_alt_lines": len(alt_lines),
        },
        "provenance": {
            "code_git_sha": code_git_sha,
            "generated_at": generated_at,
        },
    }
    return record


def build_funnel_records(candidates, *, date, generated_at=None, code_git_sha=None,
                          gate_traces=None, quality_control_index=None):
    """Pure, read-only over `candidates` -- builds one record per candidate.
    `gate_traces` and `quality_control_index`, if given, are dicts keyed by
    candidate_identity(candidate, date=date) -> that candidate's own
    gate_trace()/`(status, reason)` result. Never mutates `candidates` or
    any element of it (see the safety-contract test)."""
    gate_traces = gate_traces or {}
    quality_control_index = quality_control_index or {}
    records = []
    for c in candidates:
        cid = candidate_identity(c, date=date)
        qc_status, qc_reason = quality_control_index.get(cid, (None, None))
        records.append(funnel_record_from_candidate(
            c, date=date, generated_at=generated_at, code_git_sha=code_git_sha,
            gate_trace=gate_traces.get(cid),
            quality_control_status=qc_status, quality_control_reason=qc_reason,
        ))
    return records


def content_hash(record):
    """Stable hash of a record's substantive content, excluding fields in
    _HASH_EXCLUDE_KEYS (currently just generated_at) -- so re-running this
    logger minutes apart on an UNCHANGED candidate produces the same hash
    and is correctly treated as a duplicate, not a new changelog entry."""
    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k not in _HASH_EXCLUDE_KEYS}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj
    canonical = json.dumps(strip(record), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_last_hashes(path):
    """Last content_hash seen per candidate_id, from a real existing file.
    Missing/empty file -> empty index, not an error (first run of the day)."""
    last = {}
    if not os.path.exists(path):
        return last
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = (row.get("identity") or {}).get("candidate_id")
            if cid is not None:
                last[cid] = content_hash(row)
    return last


def append_new_snapshots(records, path):
    """Appends only records whose content_hash differs from the last-seen
    hash for that candidate_id in the existing file (or is new). Returns
    (n_written, n_skipped_duplicate). Deterministic: running this twice in
    a row on the identical `records` list writes on the first call and
    skips everything on the second."""
    last_hashes = _read_last_hashes(path)
    to_write = []
    n_skipped = 0
    for record in records:
        cid = record["identity"]["candidate_id"]
        h = content_hash(record)
        if last_hashes.get(cid) == h:
            n_skipped += 1
            continue
        to_write.append(record)
        last_hashes[cid] = h
    if to_write:
        with open(path, "a", encoding="utf-8") as fh:
            for record in to_write:
                fh.write(json.dumps(record, default=str) + "\n")
    return len(to_write), n_skipped


def default_path_for_date(date, out_dir=DEFAULT_OUT_DIR):
    return os.path.join(out_dir, f"candidate_funnel_{date}.jsonl")


def run_live_snapshot(out_dir=DEFAULT_OUT_DIR):
    """The one function in this module that actually runs a real,
    independent scoring pass -- NOT unit tested directly (matches this
    codebase's own convention: dashboard/build_dashboard.py's
    run_live_fetch() and every backtest/diagnose_*.py script are exercised
    live, not mocked, since their whole value is running the REAL pipeline
    functions). Isolation is the exact pattern run_live_fetch() already
    uses in production: redirect OUTPUT_DIR/PLAYERS_DIR to a scratch
    directory BEFORE importing generate_picks (those are read once, at
    import time), so this can never write to the real output/ directory or
    interfere with a concurrently-running production pipeline.

    HONEST GAP, live-verified 2026-08-25: this does NOT call
    fd.attach_market_prices() the way run_live_fetch() does, so every
    record's `market` section is currently None -- verified live (969 real
    candidates captured, 297 with 2+ alt lines, all `market.*` fields null
    as expected). Adding that is a real, scoped follow-up (needs the same
    prices/k_prices/fi_prices/po_prices/combined_k_prices fetch
    run_live_fetch() already does) -- not done tonight to keep this run
    fast and because MARKET-layer research has always been explicitly
    bounded to registry-covered dates anyway (see
    candidate_dataset_feasibility_2026-08-25.md's MARKET row)."""
    import sys
    import tempfile
    from datetime import datetime, timezone

    scratch = tempfile.mkdtemp(prefix="fullcount_funnel_")
    os.environ["OUTPUT_DIR"] = os.path.join(scratch, "output")
    os.environ["PLAYERS_DIR"] = os.path.join(scratch, "players")
    os.makedirs(os.environ["OUTPUT_DIR"], exist_ok=True)
    os.makedirs(os.environ["PLAYERS_DIR"], exist_ok=True)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo_root)
    prev_cwd = os.getcwd()
    os.chdir(repo_root)
    try:
        import generate_picks as gp
        import recommendation_funnel as funnel

        result = gp._build_and_score()
        if result is None:
            print("No games / nothing bettable right now -- a real, honest "
                  "null, not an error. Not fabricating a snapshot.")
            return None
        candidates, ctx = result
        game_meta = ctx["game_meta"]
        park_wx = ctx["park_wx"]
        emp_pitchers = ctx["emp_pitchers"]
        kept, rejected, assumed_lineup = gp.quality_control(
            candidates, game_meta, park_wx, emp_pitchers)

        date = gp.m.TODAY
        qc_index = {}
        for c in kept:
            qc_index[candidate_identity(c, date=date)] = ("confirmed_lineup", None)
        for c in assumed_lineup:
            qc_index[candidate_identity(c, date=date)] = ("assumed_lineup", "lineup not confirmed")
        for c in rejected:
            qc_index[candidate_identity(c, date=date)] = ("rejected", c.get("qc_reason"))

        gate_traces = {}
        for c in candidates:
            try:
                gate_traces[candidate_identity(c, date=date)] = funnel.classify_with_trace(c)
            except Exception as exc:
                # A gate-trace failure for one candidate must never drop
                # the whole snapshot -- record the record without a trace
                # rather than lose real prediction/evidence data over it.
                print(f"gate_trace failed for one candidate: {exc}")

        generated_at = datetime.now(timezone.utc).isoformat()
        records = build_funnel_records(
            candidates, date=date, generated_at=generated_at,
            code_git_sha=_current_git_sha(repo_root),
            gate_traces=gate_traces, quality_control_index=qc_index,
        )
        path = default_path_for_date(date, out_dir=out_dir)
        n_written, n_skipped = append_new_snapshots(records, path)
        print(f"Wrote {n_written} new/changed candidate snapshot(s), "
              f"skipped {n_skipped} unchanged duplicate(s) -> {path}")
        return path
    finally:
        os.chdir(prev_cwd)


def _current_git_sha(repo_root):
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root,
        ).decode().strip()
    except Exception:
        return None


if __name__ == "__main__":
    run_live_snapshot()
