#!/usr/bin/env python3
"""prospective_reporting.py -- reporting tooling for the prospective
candidate-funnel track (Priority 7 of the restart-safety-mission
directive, 2026-08-25). Operates on already-built
candidate_funnel_logger.py records joined with
candidate_funnel_grader.py outcome records.

NO CONCLUSIONS DRAWN HERE: this session's earlier live-logged funnel data
(backtest/candidate_funnel_2026-08-25.jsonl) was itself lost to the same
container restarts that wiped the canonical backfill -- it was gitignored
by design, same as rows_canonical.jsonl. Every function below is tested
against synthetic fixtures only. Do not run this against fewer than many
real logged-and-graded slates before treating any single number it
produces as a real finding -- the standing instruction against drawing
conclusions from tiny samples applies especially hard here, where even
ONE full day is a small sample of a season.

    from prospective_reporting import slate_summary, gate_regret, ...
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import random

try:
    from backtest import candidate_funnel_logger as cfl
except ImportError:  # direct script/test execution with backtest/ on sys.path
    import candidate_funnel_logger as cfl


class ProspectiveIntegrityError(RuntimeError):
    """Point-in-time research evidence is missing, inconsistent, or ambiguous."""


def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_snapshot(changelog_records, snapshot_manifest):
    """Reconstruct the EXACT candidate universe named by one observation.

    Snapshot manifests store candidate_id -> content_hash rather than repeating
    every full candidate payload. This resolver fails closed if any referenced
    historical candidate state is missing or if the universe fingerprint does
    not reproduce exactly.
    """
    entries = snapshot_manifest.get("candidate_hashes") or []
    expected_n = snapshot_manifest.get("n_candidates")
    if expected_n != len(entries):
        raise ProspectiveIntegrityError(
            f"snapshot n_candidates={expected_n} but contains {len(entries)} hash entries")

    ids = [e.get("candidate_id") for e in entries]
    if None in ids or len(ids) != len(set(ids)):
        raise ProspectiveIntegrityError(
            "snapshot candidate identities are missing or duplicated")

    canonical_entries = sorted(
        (
            {"candidate_id": e["candidate_id"], "content_hash": e.get("content_hash")}
            for e in entries
        ),
        key=lambda x: x["candidate_id"],
    )
    expected_fp = hashlib.sha256(
        json.dumps(
            canonical_entries, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if snapshot_manifest.get("candidate_universe_fingerprint") != expected_fp:
        raise ProspectiveIntegrityError(
            "snapshot candidate-universe fingerprint does not match its hash ledger")

    by_key = {}
    for record in changelog_records:
        cid = (record.get("identity") or {}).get("candidate_id")
        if cid is None:
            continue
        h = cfl.content_hash(record)
        key = (cid, h)
        if key in by_key and by_key[key] != record:
            raise ProspectiveIntegrityError(
                f"ambiguous changelog state for {cid} at content hash {h}")
        by_key[key] = record

    resolved = []
    for entry in canonical_entries:
        key = (entry["candidate_id"], entry.get("content_hash"))
        record = by_key.get(key)
        if record is None:
            raise ProspectiveIntegrityError(
                f"snapshot references missing candidate state {key[0]} @ {key[1]}")
        resolved.append(record)
    return resolved


def _operational_eligibility_reason(record, *, observed_at=None):
    """None means legitimately usable at this prospective observation."""
    identity = record.get("identity") or {}
    prediction = record.get("prediction") or {}
    market = record.get("market") or {}
    decision = record.get("decision") or {}

    if decision.get("quality_control_status") != "confirmed_lineup":
        return "quality_control_not_confirmed"
    if prediction.get("hit_probability") is None:
        return "no_model_probability"
    if market.get("market_fetch_state") != "MATCHED":
        return "market_not_matched"
    if market.get("market_odds") is None:
        return "no_posted_odds"

    observed = _parse_iso(observed_at)
    start = _parse_iso(identity.get("game_start"))
    if observed is not None:
        if start is None:
            return "game_start_unknown"
        if start <= observed:
            return "game_already_started"

    if not identity.get("candidate_id"):
        return "candidate_identity_missing"
    return None


def operationally_eligible(records, *, observed_at=None):
    """Return the point-in-time candidate population a bettor could use."""
    return [
        r for r in records
        if _operational_eligibility_reason(r, observed_at=observed_at) is None
    ]


def _ranking_value(record, field):
    locations = {
        "hit_probability": ("prediction", "hit_probability"),
        "edge_vs_fair": ("market", "edge_vs_fair"),
        "score": ("evidence", "score"),
    }
    if field not in locations:
        raise ValueError(
            f"unsupported challenger ranking {field!r}; choose one of {sorted(locations)}")
    section, key = locations[field]
    return (record.get(section) or {}).get(key)


def _selection_stats(records, outcomes_by_id):
    hits = misses = 0
    unresolved = []
    for record in records:
        cid = record["identity"]["candidate_id"]
        outcome = outcomes_by_id.get(cid)
        grade = outcome.get("grade") if outcome else None
        if grade == "hit":
            hits += 1
        elif grade == "miss":
            misses += 1
        else:
            unresolved.append(cid)
    n = len(records)
    graded = hits + misses
    return {
        "selected": n,
        "graded": graded,
        "hits": hits,
        "misses": misses,
        "unresolved_candidate_ids": unresolved,
        "hit_rate": _rate(hits, graded),
        "fully_settled": graded == n,
    }


def _selection_shape(records):
    markets = Counter()
    games = set()
    players = set()
    for record in records:
        identity = record.get("identity") or {}
        markets[identity.get("stat")] += 1
        games.add(identity.get("game_pk"))
        player_key = identity.get("combo_player_ids") or identity.get("player_id")
        players.add(json.dumps(player_key, sort_keys=True, default=str))
    games.discard(None)
    return {
        "market_mix": dict(markets),
        "unique_games": len(games),
        "unique_player_entities": len(players),
    }


def equal_volume_selector_comparison(records, outcomes, *, challenger_ranking,
                                     observed_at=None, slate_date=None):
    """Champion vs one PREDECLARED challenger at the champion's exact volume.

    This is a per-observation measurement primitive, not a promotion verdict.
    It refuses to lower volume, silently lose unsettled picks, or let a public
    Top Pick survive outside the same operational population offered to the
    challenger.
    """
    eligible = operationally_eligible(records, observed_at=observed_at)
    eligible_ids = {
        (r.get("identity") or {}).get("candidate_id") for r in eligible
    }

    champion = [
        r for r in records
        if (r.get("decision") or {}).get("recommendation_status") == "top_pick"
    ]
    invalid_champion = [
        (r.get("identity") or {}).get("candidate_id")
        for r in champion
        if (r.get("identity") or {}).get("candidate_id") not in eligible_ids
    ]
    if invalid_champion:
        raise ProspectiveIntegrityError(
            "champion Top Picks fall outside the legitimate operational "
            f"population: {invalid_champion}")

    k = len(champion)
    if k == 0:
        return {
            "comparison_status": "NO_CHAMPION_VOLUME",
            "challenger_ranking": challenger_ranking,
            "slate_date": slate_date,
            "observed_at": observed_at,
            "eligible_population": len(eligible),
            "selection_volume": 0,
        }

    rankable = [
        r for r in eligible
        if _ranking_value(r, challenger_ranking) is not None
    ]
    if len(rankable) < k:
        raise ProspectiveIntegrityError(
            f"challenger has only {len(rankable)} rankable eligible candidates "
            f"for champion volume {k}; equal-volume comparison is impossible")

    challenger = sorted(
        rankable,
        key=lambda r: (
            -float(_ranking_value(r, challenger_ranking)),
            (r.get("identity") or {}).get("candidate_id") or "",
        ),
    )[:k]

    outcomes_by_id = {
        o.get("candidate_id"): o for o in outcomes if o.get("candidate_id")
    }
    champ_ids = {
        r["identity"]["candidate_id"] for r in champion
    }
    challenger_ids = {
        r["identity"]["candidate_id"] for r in challenger
    }
    overlap = champ_ids & challenger_ids
    added = challenger_ids - champ_ids
    removed = champ_ids - challenger_ids

    champion_stats = _selection_stats(champion, outcomes_by_id)
    challenger_stats = _selection_stats(challenger, outcomes_by_id)
    complete = (
        champion_stats["fully_settled"] and challenger_stats["fully_settled"]
    )

    return {
        "comparison_status": (
            "COMPLETE" if complete else "INCOMPLETE_SETTLEMENT"
        ),
        "challenger_ranking": challenger_ranking,
        "slate_date": slate_date,
        "observed_at": observed_at,
        "eligible_population": len(eligible),
        "selection_volume": k,
        "champion_candidate_ids": sorted(champ_ids),
        "challenger_candidate_ids": sorted(challenger_ids),
        "overlap_count": len(overlap),
        "overlap_candidate_ids": sorted(overlap),
        "added_candidate_ids": sorted(added),
        "removed_candidate_ids": sorted(removed),
        "champion": {
            **champion_stats,
            **_selection_shape(champion),
        },
        "challenger": {
            **challenger_stats,
            **_selection_shape(challenger),
        },
        "realized_hit_rate_delta": (
            round(
                challenger_stats["hit_rate"] - champion_stats["hit_rate"], 4
            )
            if complete else None
        ),
        "added": _selection_stats(
            [r for r in challenger if r["identity"]["candidate_id"] in added],
            outcomes_by_id,
        ),
        "removed": _selection_stats(
            [r for r in champion if r["identity"]["candidate_id"] in removed],
            outcomes_by_id,
        ),
    }


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return (
        sorted_values[lo] * (1 - frac)
        + sorted_values[hi] * frac
    )


def aggregate_equal_volume_comparisons(reports, *, bootstrap_samples=4000,
                                       seed=0):
    """Aggregate PREDECLARED per-slate comparisons without double-counting days.

    The uncertainty interval resamples whole slate/date clusters, preserving all
    within-slate player/game dependence. It is deliberately not a claim that
    within-slate correlation has been modeled perfectly; it simply avoids the
    much worse fiction that every selected prop is an independent Bernoulli
    observation.
    """
    if not reports:
        return {
            "status": "NO_REPORTS",
            "n_slates": 0,
            "n_nonzero_slates": 0,
        }

    rankings = {r.get("challenger_ranking") for r in reports}
    if len(rankings) != 1:
        raise ProspectiveIntegrityError(
            f"cannot aggregate multiple challenger definitions: {sorted(rankings)}")

    dates = [r.get("slate_date") for r in reports]
    if any(d is None for d in dates):
        raise ProspectiveIntegrityError(
            "every report needs an explicit slate_date before aggregation")
    if len(dates) != len(set(dates)):
        raise ProspectiveIntegrityError(
            "multiple prospective observations from the same slate/date cannot "
            "be pooled as independent evidence; predeclare one observation rule first")

    bad = [
        r for r in reports
        if r.get("comparison_status")
        not in ("COMPLETE", "NO_CHAMPION_VOLUME")
    ]
    if bad:
        raise ProspectiveIntegrityError(
            "unsettled/incomplete per-slate comparisons cannot enter a realized "
            "hit-rate aggregate")

    nonzero = [r for r in reports if r.get("selection_volume", 0) > 0]
    for r in nonzero:
        k = r["selection_volume"]
        if r["champion"]["selected"] != k or r["challenger"]["selected"] != k:
            raise ProspectiveIntegrityError(
                f"equal-volume invariant broken on {r['slate_date']}")

    champ_hits = sum(r["champion"]["hits"] for r in nonzero)
    chal_hits = sum(r["challenger"]["hits"] for r in nonzero)
    champ_n = sum(r["champion"]["selected"] for r in nonzero)
    chal_n = sum(r["challenger"]["selected"] for r in nonzero)
    if champ_n != chal_n:
        raise ProspectiveIntegrityError(
            f"aggregate volume mismatch champion={champ_n}, challenger={chal_n}")

    champ_rate = _rate(champ_hits, champ_n)
    chal_rate = _rate(chal_hits, chal_n)
    delta = (
        round(chal_rate - champ_rate, 4)
        if champ_rate is not None and chal_rate is not None else None
    )

    slate_wins = slate_losses = slate_ties = 0
    for r in nonzero:
        diff = r["challenger"]["hits"] - r["champion"]["hits"]
        if diff > 0:
            slate_wins += 1
        elif diff < 0:
            slate_losses += 1
        else:
            slate_ties += 1

    bootstrap_deltas = []
    if nonzero and bootstrap_samples:
        rng = random.Random(seed)
        n_slates = len(nonzero)
        for _ in range(int(bootstrap_samples)):
            sample = [nonzero[rng.randrange(n_slates)] for _ in range(n_slates)]
            c_hits = sum(r["champion"]["hits"] for r in sample)
            x_hits = sum(r["challenger"]["hits"] for r in sample)
            n_sel = sum(r["selection_volume"] for r in sample)
            if n_sel:
                bootstrap_deltas.append((x_hits - c_hits) / n_sel)
        bootstrap_deltas.sort()

    champion_market_mix = Counter()
    challenger_market_mix = Counter()
    for r in nonzero:
        champion_market_mix.update(r["champion"].get("market_mix") or {})
        challenger_market_mix.update(r["challenger"].get("market_mix") or {})

    return {
        "status": "COMPLETE",
        "challenger_ranking": next(iter(rankings)),
        "n_slates": len(reports),
        "n_nonzero_slates": len(nonzero),
        "n_zero_volume_slates": len(reports) - len(nonzero),
        "selection_volume": champ_n,
        "champion_hits": champ_hits,
        "challenger_hits": chal_hits,
        "champion_hit_rate": champ_rate,
        "challenger_hit_rate": chal_rate,
        "realized_hit_rate_delta": delta,
        "overlap_count": sum(r.get("overlap_count", 0) for r in nonzero),
        "added_hits": sum(r["added"]["hits"] for r in nonzero),
        "added_selected": sum(r["added"]["selected"] for r in nonzero),
        "removed_hits": sum(r["removed"]["hits"] for r in nonzero),
        "removed_selected": sum(r["removed"]["selected"] for r in nonzero),
        "slate_wins": slate_wins,
        "slate_losses": slate_losses,
        "slate_ties": slate_ties,
        "champion_market_mix": dict(champion_market_mix),
        "challenger_market_mix": dict(challenger_market_mix),
        "cluster_bootstrap_95pct_delta": (
            [
                round(_quantile(bootstrap_deltas, 0.025), 4),
                round(_quantile(bootstrap_deltas, 0.975), 4),
            ]
            if bootstrap_deltas else None
        ),
        "bootstrap_samples": len(bootstrap_deltas),
        "uncertainty_cluster": "slate_date",
        "uncertainty_note": (
            "Bootstrap resamples whole slates, preserving within-slate game/player "
            "dependence. It does not separately model residual dependence inside a slate."
        ),
        "slate_dates": sorted(dates),
    }


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def slate_summary(records):
    """Per-slate (one date's funnel records) candidate-universe overview.
    `records` are candidate_funnel_logger.py's own record shape --
    top-level identity/prediction/market/evidence/decision/provenance."""
    n_total = len(records)
    by_qc = defaultdict(int)
    n_with_alt_lines = 0
    for r in records:
        qc = (r.get("decision") or {}).get("quality_control_status")
        by_qc[qc] += 1
        if (r.get("decision") or {}).get("n_alt_lines", 0) > 1:
            n_with_alt_lines += 1
    return {
        "n_total_candidates": n_total,
        "by_quality_control_status": dict(by_qc),
        "n_with_multiple_alt_lines": n_with_alt_lines,
    }


def join_outcomes(records, outcomes):
    """outcomes: list of candidate_funnel_grader.py outcome records
    (candidate_id/grade/...). Returns {candidate_id: (record, outcome_or_None)}."""
    outcomes_by_id = {o["candidate_id"]: o for o in outcomes}
    joined = {}
    for r in records:
        cid = (r.get("identity") or {}).get("candidate_id")
        if cid is None:
            continue
        joined[cid] = (r, outcomes_by_id.get(cid))
    return joined


def highest_probability_rejected(records, outcomes, n=10):
    """The N highest-hit_probability candidates that were REJECTED by
    quality_control (not assumed_lineup, not confirmed/kept) -- the
    clearest "what did the board miss out on" view. Includes outcome if
    graded, explicitly None if not."""
    joined = join_outcomes(records, outcomes)
    rejected = []
    for cid, (r, outcome) in joined.items():
        qc = (r.get("decision") or {}).get("quality_control_status")
        if qc != "rejected":
            continue
        prob = (r.get("prediction") or {}).get("hit_probability")
        if prob is None:
            continue
        rejected.append({
            "candidate_id": cid, "hit_probability": prob,
            "qc_reason": (r.get("decision") or {}).get("quality_control_reason"),
            "grade": outcome.get("grade") if outcome else None,
        })
    rejected.sort(key=lambda x: -x["hit_probability"])
    return rejected[:n]


def alternate_line_winner_comparison(records):
    """For every candidate with >1 alt_lines, compares the SELECTED board
    line's probability against the highest-probability alternate that was
    NOT selected -- i.e. was the model's own final line choice actually
    the highest-probability option it had computed? `_pick_line()`'s own
    selection logic is not re-derived here -- this only reads what's
    already in `decision.alt_lines`, checking presence, not recomputing
    or second-guessing the pick."""
    results = []
    for r in records:
        decision = r.get("decision") or {}
        alt_lines = decision.get("alt_lines") or []
        if len(alt_lines) < 2:
            continue
        board_prob = (r.get("prediction") or {}).get("hit_probability")
        if board_prob is None:
            continue
        best_alt = max(alt_lines, key=lambda a: a.get("prob") or 0)
        board_was_best = round(board_prob, 6) >= round((best_alt.get("prob") or 0) - 1e-9, 6)
        results.append({
            "candidate_id": (r.get("identity") or {}).get("candidate_id"),
            "board_prob": board_prob, "best_alt_prob": best_alt.get("prob"),
            "n_alt_lines": len(alt_lines), "board_was_highest_prob_option": board_was_best,
        })
    return results


def gate_failure_counts(records):
    """Tally of decision.blocking_gate across all records (the FIRST gate,
    in recommendation_funnel.GATE_ORDER, that blocked each candidate)."""
    counts = defaultdict(int)
    for r in records:
        bg = (r.get("decision") or {}).get("blocking_gate")
        counts[bg] += 1
    return dict(counts)


def gate_regret(records, outcomes):
    """For each gate, the realized hit rate of candidates blocked SOLELY
    by that gate -- i.e. every OTHER gate in their own `decision.gates`
    dict passed. This is stricter than gate_failure_counts (which credits
    a candidate to whichever gate failed first in GATE_ORDER, even if it
    would also have failed a later one) -- gate_regret only counts a
    candidate toward a gate if that gate is the ONLY reason it was
    blocked, so the reported hit rate is a real answer to "if we ONLY
    relaxed this one gate, what would we have gotten." Requires the full
    `decision.gates` dict (recommendation_funnel.gate_trace()'s own
    output), not just blocking_gate -- both are already captured by
    candidate_funnel_logger.py, per backtest/selection_information_loss_audit_2026-08-25.md."""
    joined = join_outcomes(records, outcomes)
    by_gate = defaultdict(lambda: {"n": 0, "hits": 0, "n_graded": 0})
    for cid, (r, outcome) in joined.items():
        gates = (r.get("decision") or {}).get("gates") or {}
        if not gates:
            continue
        failing = [g for g, passed in gates.items() if passed is False]
        if len(failing) != 1:
            continue  # blocked by 0 or >1 gates -- not attributable to exactly one
        gate = failing[0]
        by_gate[gate]["n"] += 1
        if outcome and outcome.get("grade") in ("hit", "miss"):
            by_gate[gate]["n_graded"] += 1
            by_gate[gate]["hits"] += outcome["grade"] == "hit"
    return {
        gate: {"n_blocked_solely_by_this_gate": v["n"], "n_graded": v["n_graded"],
               "hit_rate": _rate(v["hits"], v["n_graded"])}
        for gate, v in sorted(by_gate.items())
    }
