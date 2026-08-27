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
_HASH_EXCLUDE_KEYS = frozenset((
    "generated_at",
    "market_observed_at",
    "prediction_timestamp",
    "odds_fetched_at",
    "board_generated_at",
))


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


def _market_family(candidate):
    stat = ((candidate.get("projection") or {}).get("stat")
            or candidate.get("stat"))
    if stat == "strikeouts":
        return "pitcher_strikeouts"
    if stat == "pitcher_outs":
        return "pitcher_outs"
    if stat == "nrfi_combined":
        return "first_inning"
    if stat == "combined_strikeouts":
        return "combined_strikeouts"
    return "batter_props"


def _market_fetch_state(candidate, market_context):
    """Honest candidate-level price state from one run-level fetch context.

    A successful family fetch plus no attached line is only NOT_MATCHED: the
    book may not have posted this player/threshold, or it may post a different
    line. We never upgrade that ambiguity into a fabricated NOT_POSTED fact.
    """
    if candidate.get("market_odds") is not None:
        return "MATCHED"
    ctx = market_context or {}
    family = _market_family(candidate)
    family_state = (ctx.get("family_states") or {}).get(family)
    if family_state == "FETCH_FAILED":
        return "FETCH_FAILED"
    if family_state == "AVAILABLE":
        return "NOT_MATCHED"
    return "UNKNOWN"


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
                                  quality_control_reason=None,
                                  market_context=None, run_metadata=None):
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
    market_context = market_context or {}
    run_metadata = run_metadata or {}
    market_family = _market_family(candidate)

    traced_status = (gate_trace or {}).get("status")
    traced_reasons = (gate_trace or {}).get("status_reasons")
    qc_rejected = quality_control_status == "rejected"
    # Production never advances QC-rejected candidates into the recommendation
    # layer. We still run a counterfactual trace for regret research, but it
    # must not masquerade as the champion's actual operational decision.
    operational_status = None if qc_rejected else (
        candidate.get("status") or traced_status
    )
    operational_reasons = None if qc_rejected else (
        candidate.get("status_reasons")
        if candidate.get("status_reasons") is not None
        else traced_reasons
    )

    record = {
        "identity": {
            "candidate_id": candidate_identity(candidate, date=date),
            "date": date, "game_pk": candidate.get("game_pk"),
            "game_start": candidate.get("game_start"),
            "type": candidate.get("type"),
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
            "book": market_context.get("book"),
            "feed_family": market_family,
            "market_odds": candidate.get("market_odds"),
            "posted_implied": candidate.get("posted_implied"),
            "market_implied": candidate.get("market_implied"),
            "market_fair": candidate.get("market_fair"),
            "market_fair_method": candidate.get("market_fair_method"),
            "market_edge": candidate.get("market_edge"),
            "edge_vs_fair": candidate.get("edge_vs_fair"),
            "market_hold": candidate.get("market_hold"),
            "price_clears": candidate.get("price_clears"),
            "market_fetch_state": _market_fetch_state(candidate, market_context),
            "market_observed_at": market_context.get("observed_at"),
            "family_fetch_state": (
                (market_context.get("family_states") or {}).get(market_family)
            ),
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
            "signal_weight_adjustment": candidate.get("signal_weight_adjustment"),
        },
        "decision": {
            "recommendation_status": operational_status,
            "status_reasons": operational_reasons,
            "quality_control_status": quality_control_status,
            "quality_control_reason": quality_control_reason,
            "recommendation_stage": (
                "not_reached_qc_reject" if qc_rejected
                else "operational"
            ),
            "counterfactual_recommendation_status": (
                traced_status if qc_rejected else None
            ),
            "counterfactual_status_reasons": (
                traced_reasons if qc_rejected else None
            ),
            "gate_trace_scope": (
                "counterfactual_after_qc_rejection" if qc_rejected
                else "operational"
            ),
            "gates": (gate_trace or {}).get("gates"),
            "blocking_gate": (gate_trace or {}).get("blocking_gate"),
            "alt_lines": alt_lines,
            "n_alt_lines": len(alt_lines),
        },
        "provenance": {
            "code_git_sha": code_git_sha,
            "generated_at": generated_at,
            "model_version": run_metadata.get("model_version"),
            "selection_policy_version": run_metadata.get("selection_policy_version"),
            "calibration_version": run_metadata.get("calibration_version"),
            "feature_version": run_metadata.get("feature_version"),
            "prediction_timestamp": run_metadata.get("prediction_timestamp"),
            "odds_fetched_at": run_metadata.get("odds_fetched_at"),
            "board_generated_at": run_metadata.get("board_generated_at"),
        },
    }
    return record


def build_funnel_records(candidates, *, date, generated_at=None, code_git_sha=None,
                          gate_traces=None, quality_control_index=None,
                          market_context=None, run_metadata=None):
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
            market_context=market_context, run_metadata=run_metadata,
        ))
    return records


def canonical_content_record(record):
    """Timestamp-neutral substantive candidate state used for dedup/storage.

    Observation timing lives in the separate snapshot manifest. Keeping
    run-level timestamps out of this content object lets one immutable candidate
    state be referenced by many real observation events without rewriting it.
    """
    def strip(obj):
        if isinstance(obj, dict):
            return {
                k: strip(v) for k, v in obj.items()
                if k not in _HASH_EXCLUDE_KEYS
            }
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj
    return strip(record)


def content_hash(record):
    """Stable SHA-256 of the substantive, timestamp-neutral candidate state."""
    canonical = json.dumps(
        canonical_content_record(record),
        sort_keys=True, separators=(",", ":"), default=str)
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


def build_snapshot_manifest(records, *, date, observed_at,
                            code_git_sha=None, market_context=None,
                            run_metadata=None):
    """One immutable observation event for a full candidate-universe snapshot.

    Candidate rows remain a deduplicated changelog, but this manifest is written
    for EVERY run. It preserves the fact that candidate X with content hash H
    was actually observed at time T even when X did not materially change since
    the prior run.
    """
    market_context = market_context or {}
    run_metadata = run_metadata or {}
    items = sorted(
        (
            {
                "candidate_id": r["identity"]["candidate_id"],
                "content_hash": content_hash(r),
            }
            for r in records
        ),
        key=lambda x: x["candidate_id"],
    )
    ids = [x["candidate_id"] for x in items]
    if len(ids) != len(set(ids)):
        raise ValueError("snapshot contains duplicate candidate_id values")
    universe_fingerprint = hashlib.sha256(
        json.dumps(items, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot_id = hashlib.sha256(
        json.dumps(
            {
                "date": date,
                "observed_at": observed_at,
                "code_git_sha": code_git_sha,
                "candidate_universe_fingerprint": universe_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "record_type": "candidate_funnel_snapshot_manifest",
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "date": date,
        "observed_at": observed_at,
        "n_candidates": len(items),
        "candidate_universe_fingerprint": universe_fingerprint,
        "candidate_hashes": items,
        "market_context": market_context,
        "provenance": {
            "code_git_sha": code_git_sha,
            "model_version": run_metadata.get("model_version"),
            "selection_policy_version": run_metadata.get("selection_policy_version"),
            "calibration_version": run_metadata.get("calibration_version"),
            "feature_version": run_metadata.get("feature_version"),
            "prediction_timestamp": run_metadata.get("prediction_timestamp"),
            "odds_fetched_at": run_metadata.get("odds_fetched_at"),
            "board_generated_at": run_metadata.get("board_generated_at"),
        },
    }


def append_snapshot_manifest(manifest, path):
    """Append one observation event; refuse accidental duplicate snapshot ids."""
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = row.get("snapshot_id")
                if sid:
                    seen.add(sid)
    if manifest["snapshot_id"] in seen:
        return 0
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, default=str) + "\n")
    return 1


def default_snapshot_path_for_date(date, out_dir=DEFAULT_OUT_DIR):
    return os.path.join(out_dir, f"candidate_funnel_snapshots_{date}.jsonl")


def default_path_for_date(date, out_dir=DEFAULT_OUT_DIR):
    return os.path.join(out_dir, f"candidate_funnel_{date}.jsonl")


def fetch_live_market_snapshot(ctx, *, fd=None, observed_at=None):
    """Fetch/reuse the exact FanDuel families needed to price funnel candidates.

    Returns (feeds, market_context). Feed-state labels are deliberately
    conservative: an empty reused/fetched dict is UNKNOWN_EMPTY, not a claim
    that no market was posted. Only an exception we directly observe earns
    FETCH_FAILED.
    """
    from datetime import datetime, timezone

    if fd is None:
        import odds_fanduel as fd

    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    states = {}

    def _fetch(name, fn):
        try:
            value = fn() or {}
        except Exception:
            states[name] = "FETCH_FAILED"
            return {}
        states[name] = "AVAILABLE" if value else "UNKNOWN_EMPTY"
        return value

    def _reuse(name, value):
        value = value or {}
        states[name] = "AVAILABLE" if value else "UNKNOWN_EMPTY"
        return value

    feeds = {
        "prices": _fetch("batter_props", fd.fetch_prop_prices),
        "k_prices": _reuse("pitcher_strikeouts", ctx.get("k_prices")),
        "fi_prices": _fetch("first_inning", fd.fetch_first_inning_totals),
        "po_prices": _reuse("pitcher_outs", ctx.get("po_prices")),
        "combined_k_prices": _reuse(
            "combined_strikeouts", ctx.get("combined_k_prices")),
    }
    market_context = {
        "book": "fanduel",
        "observed_at": observed_at,
        "family_states": states,
    }
    return feeds, market_context


def prepare_research_candidates(candidates, ctx, *, gp, fd, date,
                                observed_at=None):
    """Mirror live QC/signal/market mutation on a deep research copy only."""
    import copy

    research_candidates = copy.deepcopy(candidates)
    kept, rejected, assumed_lineup = gp.quality_control(
        research_candidates, ctx["game_meta"], ctx["park_wx"],
        ctx["emp_pitchers"])

    signal_trust = gp.load_signal_trust()
    gp.apply_signal_weights(kept, trust=signal_trust)

    feeds, market_context = fetch_live_market_snapshot(
        ctx, fd=fd, observed_at=observed_at)
    fd.attach_market_prices(
        research_candidates,
        prices=feeds["prices"],
        k_prices=feeds["k_prices"],
        fi_prices=feeds["fi_prices"],
        po_prices=feeds["po_prices"],
        combined_k_prices=feeds["combined_k_prices"],
    )

    qc_index = {}
    for candidate in kept:
        qc_index[candidate_identity(candidate, date=date)] = (
            "confirmed_lineup", None)
    for candidate in assumed_lineup:
        qc_index[candidate_identity(candidate, date=date)] = (
            "assumed_lineup", "lineup not confirmed")
    for candidate in rejected:
        qc_index[candidate_identity(candidate, date=date)] = (
            "rejected", candidate.get("qc_reason"))

    return research_candidates, qc_index, market_context, feeds


def _subject_key(candidate):
    combo = candidate.get("combo_player_ids")
    if combo:
        subject = ("combo", tuple(combo))
    elif candidate.get("player_id") is not None:
        subject = ("player", candidate.get("player_id"))
    else:
        subject = ("game", candidate.get("game_pk"), candidate.get("type"))
    return (candidate.get("game_pk"), subject)


def build_operational_opportunities(research_candidates, qc_index, feeds, *,
                                    gp, fd, date):
    """Expand raw scored players into the same per-market rows the dashboard uses.

    Raw _build_and_score() keeps one primary projection per batter and tucks the
    other market families into line_options. The live dashboard expands those
    options through select_best_by_category(..., n=9999, min_score=0) before
    recommendation classification. Selector research must compare those actual
    bet opportunities, not pretend the batter's primary projection is the whole
    market universe.
    """
    qc_by_subject = {}
    raw_by_subject = {}
    for candidate in research_candidates:
        raw_cid = candidate_identity(candidate, date=date)
        qc = qc_index.get(raw_cid, (None, None))
        key = _subject_key(candidate)
        if key in qc_by_subject and qc_by_subject[key] != qc:
            raise ValueError(
                f"conflicting QC states for prospective subject {key}: "
                f"{qc_by_subject[key]} vs {qc}")
        qc_by_subject[key] = qc
        raw_by_subject[key] = candidate

    confirmed = []
    assumed = []
    rejected = []
    for candidate in research_candidates:
        status, _reason = qc_by_subject.get(
            _subject_key(candidate), (None, None))
        if status == "confirmed_lineup":
            confirmed.append(candidate)
        elif status == "assumed_lineup":
            assumed.append(candidate)
        elif status == "rejected":
            rejected.append(candidate)

    def expand(pool):
        if not pool:
            return []
        by_category = gp.select_best_by_category(
            pool,
            feeds["prices"],
            fd,
            n_per_category=9999,
            k_prices=feeds["k_prices"],
            min_score=0,
        )
        return [
            row
            for _stat, rows in sorted(by_category.items())
            for row in rows
        ]

    # Production dashboard advances confirmed + assumed candidates into the
    # category/recommendation layer; rejected candidates are expanded
    # separately only so QC-regret research can ask what was left on the table.
    operational_rows = expand(confirmed + assumed)
    rejected_counterfactual_rows = expand(rejected)
    rows = operational_rows + rejected_counterfactual_rows

    expanded_qc = {}
    seen = set()
    for row in rows:
        key = _subject_key(row)
        status_reason = qc_by_subject.get(key)
        if status_reason is None:
            raise ValueError(
                f"expanded prospective opportunity has no source QC state: {key}")
        cid = candidate_identity(row, date=date)
        if cid in seen:
            raise ValueError(
                f"duplicate expanded prospective candidate identity {cid}")
        seen.add(cid)
        expanded_qc[cid] = status_reason

    represented_subjects = {_subject_key(r) for r in rows}
    diagnostics = {
        "raw_candidates": len(research_candidates),
        "expanded_opportunities": len(rows),
        "operational_opportunities": len(operational_rows),
        "rejected_counterfactual_opportunities": len(
            rejected_counterfactual_rows),
        "raw_subjects": len(raw_by_subject),
        "represented_subjects": len(represented_subjects),
        "unrepresented_subjects": len(
            set(raw_by_subject) - represented_subjects),
    }
    return rows, expanded_qc, diagnostics


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

    Prospective-truth hardening (SUPERCHAD quarantine branch): this pass now
    performs the same FanDuel family attachment needed to preserve actual
    point-in-time prices, but only inside this already-isolated research
    process. It prices a deep-copied research candidate universe; it never
    writes or mutates the production board. It also writes one compact
    snapshot-manifest event on EVERY observation, so an unchanged candidate
    can be proven present at 13:00 even when its deduplicated changelog row
    was last written at 12:00."""
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
        import odds_fanduel as fd
        import recommendation as rec
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

        date = gp.m.TODAY
        odds_observed_at = datetime.now(timezone.utc).isoformat()
        research_candidates, raw_qc_index, market_context, feeds = (
            prepare_research_candidates(
                candidates, ctx, gp=gp, fd=fd, date=date,
                observed_at=odds_observed_at)
        )
        research_candidates, qc_index, capture_diagnostics = (
            build_operational_opportunities(
                research_candidates, raw_qc_index, feeds,
                gp=gp, fd=fd, date=date)
        )

        generated_at = datetime.now(timezone.utc).isoformat()
        now = datetime.now(timezone.utc)
        fresh, fresh_reasons = rec.freshness_check(
            now=now, odds_fetched_at=odds_observed_at,
            board_generated_at=generated_at)

        gate_traces = {}
        for c in research_candidates:
            try:
                gate_traces[candidate_identity(c, date=date)] = (
                    funnel.classify_with_trace(
                        c, now=now, data_fresh=fresh,
                        fresh_reasons=fresh_reasons)
                )
            except Exception as exc:
                # A gate-trace failure for one candidate must never drop
                # the whole snapshot -- record the record without a trace
                # rather than lose real prediction/evidence data over it.
                print(f"gate_trace failed for one candidate: {exc}")

        code_git_sha = _current_git_sha(repo_root, short=False)
        run_metadata = rec.build_metadata(
            odds_fetched_at=odds_observed_at,
            board_generated_at=generated_at)
        # build_metadata() independently reads git; use the exact SHA captured
        # for this logger when available so the candidate and snapshot manifest
        # share one code identity rather than two differently-shortened forms.
        if code_git_sha:
            run_metadata["git_sha"] = code_git_sha

        records = build_funnel_records(
            research_candidates, date=date, generated_at=generated_at,
            code_git_sha=code_git_sha,
            gate_traces=gate_traces, quality_control_index=qc_index,
            market_context=market_context, run_metadata=run_metadata,
        )
        path = default_path_for_date(date, out_dir=out_dir)
        n_written, n_skipped = append_new_snapshots(records, path)

        snapshot = build_snapshot_manifest(
            records, date=date, observed_at=generated_at,
            code_git_sha=code_git_sha, market_context=market_context,
            run_metadata=run_metadata)
        snapshot["capture_diagnostics"] = capture_diagnostics
        # capture_diagnostics is part of snapshot identity evidence; refresh the
        # id after adding it rather than attaching unauthenticated metadata.
        snapshot_identity = {
            "date": snapshot["date"],
            "observed_at": snapshot["observed_at"],
            "code_git_sha": code_git_sha,
            "candidate_universe_fingerprint": (
                snapshot["candidate_universe_fingerprint"]
            ),
            "capture_diagnostics": capture_diagnostics,
        }
        snapshot["snapshot_id"] = hashlib.sha256(
            json.dumps(
                snapshot_identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        snapshot_path = default_snapshot_path_for_date(date, out_dir=out_dir)
        snapshot_written = append_snapshot_manifest(snapshot, snapshot_path)

        print(f"Wrote {n_written} new/changed candidate snapshot(s), "
              f"skipped {n_skipped} unchanged duplicate(s) -> {path}")
        print(f"Recorded observation manifest {snapshot['snapshot_id'][:12]} "
              f"({snapshot['n_candidates']} candidates; appended={bool(snapshot_written)}) "
              f"-> {snapshot_path}")
        return path
    finally:
        os.chdir(prev_cwd)


def _current_git_sha(repo_root, *, short=True):
    import subprocess
    try:
        args = ["git", "rev-parse"]
        if short:
            args.append("--short")
        args.append("HEAD")
        return subprocess.check_output(
            args, cwd=repo_root,
        ).decode().strip()
    except Exception:
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="directory for gitignored candidate/snapshot JSONL spool files")
    args = parser.parse_args()
    run_live_snapshot(out_dir=args.out_dir)
