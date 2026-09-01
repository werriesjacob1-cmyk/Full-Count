"""Immutable pregame receipts for the prospective Hits PA-v1 shadow.

Locked protocol section 9. A receipt is the frozen wager expression AND the
frozen decision state at one decisive epoch. It contains no outcome, ever.

Two properties are non-negotiable and are enforced in code rather than by
convention:

  1. NO OUTCOME FIELD MAY APPEAR. A pregame receipt that can hold an outcome
     is a receipt that can be quietly completed after the fact, and the whole
     prospective claim rests on the receipt having been sealed before play.
     ``assert_no_outcome()`` walks the receipt recursively and raises.

  2. A LATER PRICE IS A DIFFERENT RECEIPT STATE, NOT AN EDIT. The content SHA
     covers the full wager expression including the exact odds and the odds
     timestamp, so a re-observed price cannot round-trip to the same content
     hash. The ledger refuses to overwrite; see prospective_ledger.py.

The receipt deliberately records the four identity-bearing facts separately
(team side, wager direction, line, needs). See prospective_eligibility.
wager_expression() for why collapsing any pair of them is a real defect this
project has already hit.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.prospective_eligibility import (  # noqa: E402
    PROTOCOL_SHA256,
    PROTOCOL_VERSION,
)

RECEIPT_SCHEMA_VERSION = 1

# The frozen PA-v1 authoritative artifact this shadow is bound to. Pinned here
# so a receipt can never silently be produced against a refit model: the
# capture asserts the loaded artifact's scientific hash equals this value.
PA_V1_SCIENTIFIC_SHA256 = (
    "a4f598bd4138305d8da4d85767eb873781b10e918dd1e402d536d9cd13fadf4a")
PA_V1_SERIALIZED_FILE_SHA256 = (
    "112517321e562ee25f46140cf8ce52e2ef48b40447235cf9b22e50dec9870750")

ARM_CHAMPION = "champion"
ARM_PA_V1 = "pa_v1"

# Anything whose NAME suggests a settled result. Matched as a substring on the
# lowercased key so "actual_hits", "final_outcome" and "was_graded" are all
# caught, not just exact spellings. Deliberately aggressive: a false positive
# here costs one renamed field, a false negative costs the experiment.
_OUTCOME_TOKENS = (
    "outcome", "actual", "result", "graded", "grade", "settle", "settled",
    "settlement", "final_", "did_hit", "was_hit", "hit_flag", "won", "lost",
    "box_score", "boxscore", "realized",
)

# Explicit allow-list for names that contain an outcome token but are pregame
# facts. Each entry is here because it is genuinely known before first pitch.
_OUTCOME_TOKEN_EXEMPT = frozenset({
    # "hit_probability"/"raw_hit_probability" are predictions, but they do not
    # contain a token above; listed keys are the real collisions only.
    "reliability_grade",       # attach_reliability()'s A/B/C/D letter
    "grade_authority",         # WHO will settle it, declared pregame
    # The settlement IDENTITY is a pregame fact -- it names WHICH wager will
    # later be settled, and carries no result. Caught by the "settle" token
    # and exempted deliberately rather than by loosening the token list.
    "settlement_identity_key",
    # An eligibility GATE NAME: "is this market structurally settleable at
    # all", decided pregame from an allow-list. Carries no result.
    "settlement_supported",
})


def canonical_json(obj):
    """Deterministic serialization: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


class OutcomeLeakError(ValueError):
    """A pregame receipt was built or stored carrying an outcome-shaped field."""


def assert_no_outcome(obj, *, path="receipt"):
    """Recursively refuse any outcome-shaped key. Raises OutcomeLeakError."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            if lowered not in _OUTCOME_TOKEN_EXEMPT:
                for token in _OUTCOME_TOKENS:
                    if token in lowered:
                        raise OutcomeLeakError(
                            f"{path}.{key} looks like an outcome field "
                            f"(matched {token!r}); a pregame receipt may not "
                            f"carry one")
            assert_no_outcome(value, path=f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            assert_no_outcome(value, path=f"{path}[{i}]")
    return True


def git_sha(repo_root=None):
    """HEAD of the repository that produced this receipt, or None."""
    root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def receipt_id(epoch_id, canonical_prop_id):
    """The IDEMPOTENT LEDGER KEY: one receipt per (epoch, wager expression).

    Deliberately NOT keyed by arm. A prop can be selected by the champion, by
    PA-v1, or by both; three keys for one wager at one epoch would let the
    same observation be counted more than once. Arm membership is recorded as
    two flags INSIDE the single receipt instead.
    """
    return hashlib.sha256(
        f"{epoch_id}\x1f{canonical_prop_id}".encode("utf-8")).hexdigest()


def content_sha(receipt):
    """Hash of the complete receipt body, excluding only the self-referential
    hash field itself. Everything else -- odds, odds timestamp, epoch binding,
    memberships, versions -- is inside the hash, so a re-observed price is
    provably a different receipt state rather than a silent edit."""
    body = {k: v for k, v in receipt.items() if k != "receipt_content_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def build_receipt(row, verdict, *, epoch, snapshot_id, snapshot_sha256,
                  slate_date, pa_probability, pa_fallback_state,
                  champion_member, champion_rank, pa_member, pa_rank,
                  board_metadata=None, source_integrity_state="none_declared",
                  pa_artifact_sha256=PA_V1_SCIENTIFIC_SHA256,
                  repo_git_sha=None):
    """Build one immutable pregame receipt from an already-gated row.

    ``verdict`` is prospective_eligibility.evaluate_row()'s output for this
    same row, carried in whole: the gate trace IS part of the decision state,
    and reconstructing it later from a different code version would not be the
    same fact.
    """
    projection = row.get("projection") or {}
    expression = verdict.get("expression") or {}
    meta = board_metadata or {}

    receipt = {
        # -- schema / protocol identity ---------------------------------
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,

        # -- epoch / snapshot binding -----------------------------------
        "decisive_epoch_id": epoch.get("decisive_epoch_id"),
        "epoch_binding": dict(epoch),
        "snapshot_id": snapshot_id,
        "snapshot_content_sha256": snapshot_sha256,

        # -- canonical settlement identity ------------------------------
        "canonical_prop_id": verdict.get("canonical_prop_id"),
        "settlement_identity_key": verdict.get("identity_key"),

        # -- date / game / player / team --------------------------------
        "slate_date": slate_date,
        "game_pk": row.get("game_pk"),
        "matchup": row.get("matchup"),
        "player_id": row.get("player_id"),
        "player_name": row.get("name"),
        "team": row.get("team"),
        "game_start": verdict.get("game_start"),

        # -- THE WAGER EXPRESSION, four distinct facts ------------------
        # team_side is home/away. market_side is the wager direction. line is
        # the printed number. needs is the outcome required to win. None of
        # these is a synonym for another; see the module docstring.
        "stat": projection.get("stat"),
        "team_side": expression.get("team_side"),
        "market_side": expression.get("market_side"),
        "line": expression.get("line"),
        "needs": expression.get("needs"),
        "prop_label": row.get("prop"),

        # -- decision state ---------------------------------------------
        "lineup_confirmed": verdict["gates"].get("lineup_confirmed"),
        "lineup_assumed_raw": verdict.get("notes", {}).get("lineup_assumed_raw"),
        "source_integrity_state": source_integrity_state,
        "eligibility_gates": verdict.get("gates"),

        # -- champion (current Full Count) model state ------------------
        "champion_probability": row.get("hit_probability"),
        "champion_raw_probability": row.get("raw_hit_probability"),
        "champion_probability_basis": row.get("probability_basis"),
        "champion_calibrated_by": row.get("calibrated_by"),
        "champion_score": row.get("score"),
        "base_rate": row.get("base_rate"),
        "lift": row.get("lift"),
        "signals": row.get("signals") or {},

        # -- challenger (frozen PA-v1) state ----------------------------
        "pa_v1_probability": pa_probability,
        "pa_v1_fallback_state": pa_fallback_state,
        "pa_v1_artifact_scientific_sha256": pa_artifact_sha256,

        # -- evidence provenance ----------------------------------------
        "reliability_grade": row.get("reliability"),
        "sample_n": row.get("sample_n"),
        "prob_ci": row.get("prob_ci"),
        "prob_ci_source": row.get("prob_ci_source"),

        # -- market -----------------------------------------------------
        "book": "fanduel",
        "odds_american": row.get("market_odds"),
        "odds_observed_at": meta.get("odds_fetched_at"),
        "market_fetch_state": row.get("market_fetch_state"),
        "market_implied": row.get("market_implied"),
        "market_fair": row.get("market_fair"),
        "market_fair_method": row.get("market_fair_method"),
        "market_edge": row.get("market_edge"),
        "edge_vs_fair": row.get("edge_vs_fair"),

        # -- production recommendation trace ----------------------------
        "recommendation_status": row.get("status"),
        "recommendation_status_reasons": row.get("status_reasons"),

        # -- arm membership ---------------------------------------------
        "champion_member": bool(champion_member),
        "champion_rank": champion_rank,
        "pa_v1_member": bool(pa_member),
        "pa_v1_rank": pa_rank,

        # -- code / model provenance ------------------------------------
        "model_version": meta.get("model_version"),
        "selection_policy_version": meta.get("selection_policy_version"),
        "calibration_version": meta.get("calibration_version"),
        "feature_version": meta.get("feature_version"),
        "board_generated_at": meta.get("board_generated_at"),
        "git_sha": repo_git_sha if repo_git_sha is not None else git_sha(),
    }

    receipt["receipt_id"] = receipt_id(receipt["decisive_epoch_id"],
                                       receipt["canonical_prop_id"])
    assert_no_outcome(receipt)
    receipt["receipt_content_sha256"] = content_sha(receipt)
    return receipt


def verify_receipt(receipt):
    """Recompute the content hash and re-assert outcome-freedom."""
    assert_no_outcome({k: v for k, v in receipt.items()
                       if k != "receipt_content_sha256"})
    return content_sha(receipt) == receipt.get("receipt_content_sha256")
