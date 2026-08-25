#!/usr/bin/env python3
"""shadow_policy_framework.py -- generic, non-public prospective
policy-evaluation layer, built 2026-08-25 while canonical history was
being rebuilt (Priorities 2/3 of the restart-safety-mission directive).

PURPOSE: the exact same frozen pregame candidate universe (already
captured by candidate_funnel_logger.py -- one JSONL record per candidate,
non-mutating, gitignored, research-only) must be consumable by MULTIPLE
policies, each producing its own selection, so that once a challenger
earns historical shadow testing (this session's disagreement work is the
current candidate), it can be graded prospectively against the exact same
candidate universe the CHAMPION policy sees -- without EVER touching
generate_picks.py's public output, the live board, or the Top Pick
registry. This module builds and grades selections; it does not select
for real.

NON-MUTATION GUARANTEE: every policy function here is READ-ONLY over its
input candidates (only `.get()` calls) -- mirrors
candidate_funnel_logger.py's own established discipline and is tested the
same way (deep-copy-before/after equality).

FROZEN PREGAME / NO POSTGAME LEAKAGE: a PolicySelection never contains an
outcome field -- outcomes are joined LATER, from a separate source
(mirroring candidate_funnel_grader.py's own separate-file design), via
grade_selections(). This is enforced by a test, not just documented.

    from shadow_policy_framework import CHAMPION, PROBABILITY_FIRST, run_policies
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict

import recommendation_funnel as funnel


# ---------------------------------------------------------------------------
# PolicySelection shape: a frozen, versioned record of what one policy would
# have selected from one candidate universe snapshot.
# ---------------------------------------------------------------------------

def _candidate_id(candidate):
    """Reuses the same identity shape candidate_funnel_logger.candidate_identity()
    establishes -- not re-derived independently, so a selection here and a
    funnel-log record for the same candidate always agree on identity."""
    player_key = candidate.get("combo_player_ids") or candidate.get("player_id")
    projection = candidate.get("projection") or {}
    stat = projection.get("stat") or candidate.get("stat")
    needs = projection.get("needs")
    return f"{candidate.get('date')}:{candidate.get('game_pk')}:{player_key}:{stat}:{needs}"


def _config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


def build_policy_selection(policy_name, policy_version, config, snapshot_id,
                            selected_candidate_ids_ranked, created_at=None):
    """Pure constructor -- never inspects `candidates` itself, only the ids
    a policy function already decided to select. `rejection_reason` is
    intentionally NOT modeled here (a selection is a positive statement of
    what was picked; candidate_funnel_logger.py already owns the full
    universe including why something was rejected -- this module does not
    duplicate that)."""
    return {
        "policy_name": policy_name,
        "policy_version": policy_version,
        "config_hash": _config_hash(config),
        "snapshot_id": snapshot_id,
        "selected_candidate_ids": list(selected_candidate_ids_ranked),
        "rank": {cid: i for i, cid in enumerate(selected_candidate_ids_ranked)},
        "created_at": created_at,
        "provenance": {"config": config},
    }


# ---------------------------------------------------------------------------
# Policies. Each is a pure function: (candidates, config) -> ranked list of
# candidate_ids. Never mutates `candidates`. These are RESEARCH policies --
# none of them is wired into generate_picks.py's public output.
# ---------------------------------------------------------------------------

def champion_policy(candidates, config=None):
    """Mirrors current production's real Top-Pick-funnel logic exactly --
    reuses recommendation_funnel.classify_with_trace() (the same function
    candidate_funnel_logger.py already uses), never reimplements the gate
    logic. Selects every candidate classify_recommendation() calls
    'top_pick', ranked by hit_probability descending."""
    scored = []
    for c in candidates:
        try:
            traced = funnel.classify_with_trace(c)
        except Exception:
            continue
        if traced.get("status") == "top_pick":
            scored.append((c.get("hit_probability") or 0, _candidate_id(c)))
    scored.sort(key=lambda t: -t[0])
    return [cid for _, cid in scored]


def probability_first_policy(candidates, config=None):
    """Within the safe pool (hit_probability >= min_prob), rank purely by
    hit_probability descending. config: {"min_prob": float}."""
    min_prob = (config or {}).get("min_prob", 0.60)
    scored = []
    for c in candidates:
        prob = c.get("hit_probability")
        if prob is not None and prob >= min_prob:
            scored.append((prob, _candidate_id(c)))
    scored.sort(key=lambda t: -t[0])
    return [cid for _, cid in scored]


def reliability_first_policy(candidates, config=None):
    """Within the safe pool, rank by (reliability grade, hit_probability)
    descending -- reliability is a letter grade (e.g. 'A'/'B'/'C'), sorted
    lexicographically ascending (A is best) as the primary key.
    config: {"min_prob": float}."""
    min_prob = (config or {}).get("min_prob", 0.60)
    scored = []
    for c in candidates:
        prob = c.get("hit_probability")
        if prob is None or prob < min_prob:
            continue
        reliability = c.get("reliability") or "Z"  # unknown reliability sorts last
        scored.append((reliability, -(prob or 0), _candidate_id(c)))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [cid for _, _, cid in scored]


def ci_lower_bound_policy(candidates, config=None):
    """Within the safe pool AND only among candidates that structurally
    carry a prob_ci (never fabricated for markets that don't have one),
    rank by the CI's lower bound descending. config: {"min_prob": float}."""
    min_prob = (config or {}).get("min_prob", 0.60)
    scored = []
    for c in candidates:
        prob = c.get("hit_probability")
        ci = c.get("prob_ci")
        if prob is None or prob < min_prob or not ci:
            continue
        lower = ci[0] if isinstance(ci, (list, tuple)) and len(ci) == 2 else None
        if lower is None:
            continue
        scored.append((lower, _candidate_id(c)))
    scored.sort(key=lambda t: -t[0])
    return [cid for _, cid in scored]


POLICY_REGISTRY = {
    "champion": (champion_policy, "v1"),
    "probability_first": (probability_first_policy, "v1"),
    "reliability_first": (reliability_first_policy, "v1"),
    "ci_lower_bound": (ci_lower_bound_policy, "v1"),
}


def run_policies(candidates, policy_names, config=None, snapshot_id=None, created_at=None):
    """Runs every named policy over the SAME candidate list, freezing each
    into its own PolicySelection. Every policy sees identical input --
    this is the core requirement the whole module exists for."""
    config = config or {}
    results = {}
    for name in policy_names:
        fn, version = POLICY_REGISTRY[name]
        selected_ids = fn(candidates, config)
        results[name] = build_policy_selection(
            name, version, config, snapshot_id, selected_ids, created_at=created_at)
    return results


# ---------------------------------------------------------------------------
# Grading + comparison. Outcomes are joined LATER, from a separate source
# (e.g. candidate_funnel_grader.py's outcome file) -- never embedded in a
# PolicySelection itself.
# ---------------------------------------------------------------------------

def grade_policy_selection(selection, outcomes_by_candidate_id):
    """outcomes_by_candidate_id: {candidate_id: {"grade": "hit"/"miss"/"ungraded", ...}}.
    Returns per-policy summary stats. Missing outcomes (not yet graded) are
    counted separately, never assumed to be a miss."""
    n = len(selection["selected_candidate_ids"])
    n_hit = n_miss = n_ungraded_or_missing = 0
    for cid in selection["selected_candidate_ids"]:
        outcome = outcomes_by_candidate_id.get(cid)
        if outcome is None:
            n_ungraded_or_missing += 1
            continue
        grade = outcome.get("grade")
        if grade == "hit":
            n_hit += 1
        elif grade == "miss":
            n_miss += 1
        else:
            n_ungraded_or_missing += 1
    n_decided = n_hit + n_miss
    return {
        "policy_name": selection["policy_name"], "n_selected": n,
        "n_hit": n_hit, "n_miss": n_miss, "n_ungraded_or_missing": n_ungraded_or_missing,
        "hit_rate": round(n_hit / n_decided, 4) if n_decided else None,
    }


def compare_policies(champion_selection, challenger_selection, outcomes_by_candidate_id):
    """Pairwise champion-vs-challenger comparison: overlap, champion-only
    (removed by the challenger), challenger-only (added), and each group's
    hit rate. Does NOT force equal volume -- callers wanting a matched-
    volume comparison should truncate the challenger's own ranked list to
    len(champion_selection['selected_candidate_ids']) before calling this,
    keeping that policy decision explicit at the call site rather than
    hidden in this function."""
    champ_ids = set(champion_selection["selected_candidate_ids"])
    chall_ids = set(challenger_selection["selected_candidate_ids"])
    overlap = champ_ids & chall_ids
    champion_only = champ_ids - chall_ids
    challenger_only = chall_ids - champ_ids

    def _rate(ids):
        hits = decided = 0
        for cid in ids:
            outcome = outcomes_by_candidate_id.get(cid)
            if outcome is None or outcome.get("grade") not in ("hit", "miss"):
                continue
            decided += 1
            hits += outcome.get("grade") == "hit"
        return round(hits / decided, 4) if decided else None, decided

    overlap_rate, overlap_n = _rate(overlap)
    champion_only_rate, champion_only_n = _rate(champion_only)
    challenger_only_rate, challenger_only_n = _rate(challenger_only)

    return {
        "n_champion_selected": len(champ_ids), "n_challenger_selected": len(chall_ids),
        "n_overlap": len(overlap), "overlap_hit_rate": overlap_rate, "overlap_n_graded": overlap_n,
        "n_champion_only_removed_by_challenger": len(champion_only),
        "removed_hit_rate": champion_only_rate, "removed_n_graded": champion_only_n,
        "n_challenger_only_added": len(challenger_only),
        "added_hit_rate": challenger_only_rate, "added_n_graded": challenger_only_n,
    }
