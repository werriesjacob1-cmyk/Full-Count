"""Settlement of prospective Hits PA-v1 receipts.

Locked protocol section 11.

THE WAGER IS RECONSTRUCTED FROM THE RECEIPT, AND ONLY FROM THE RECEIPT.
reconstruct_pick() builds the settlement input out of the sealed receipt's own
fields -- game_pk, player_id, stat, needs, line, market_side -- and nothing
else. It never reads a candidate, a board, or a live payload.

That is the whole point, and it is why
``candidate_funnel_grader.load_latest_records()`` is explicitly unusable here:
that reduction resolves a prop to its LATEST observed candidate state, so a
line or side that moved after the decisive epoch would silently settle a
DIFFERENT wager than the one the receipt sealed -- and it would do so
invisibly, producing a number that looks like a hit rate and is not one. This
module imports nothing from it, and a test asserts that.

THE PREGAME RECEIPT IS NEVER MUTATED. Settlement is a separate ledger event
type carrying the receipt's id AND its content hash, so a settlement can always
be proven to refer to the exact sealed state and to no other.

FOUR OUTCOMES, NOT TWO. hit / miss / void / ungraded are recorded distinctly.
The hit-rate denominator is decided (hit + miss) only, matching real wager
settlement semantics -- but selection N, void rate and ungraded rate are
reported alongside it, because a challenger that produces more non-decisions
would otherwise appear superior simply by deciding less often.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DECIDED = ("hit", "miss")
ALL_OUTCOMES = ("hit", "miss", "void", "ungraded")

# WHICH OUTCOMES MAY BE WRITTEN TO AN IMMUTABLE LEDGER. `ungraded` is a
# not-yet-knowable state ("game not final yet"), not a verdict, so appending it
# would permanently seal a non-answer that the real box score could never
# afterwards replace. Callers settle terminal outcomes and retry the rest.
TERMINAL_OUTCOMES = ("hit", "miss", "void")


class ReceiptMismatch(ValueError):
    """A settlement was offered for a receipt it does not match."""


def reconstruct_pick(receipt):
    """Rebuild the exact sealed wager as a settlement input.

    Every field comes from the receipt. `market_side` is carried through under
    its own name because grade_results._is_under_pick() reads exactly that key
    and refuses to infer direction from a display string -- so the receipt's
    recorded wager direction, not a re-parsed label, decides over vs under.
    """
    return {
        "game_pk": receipt.get("game_pk"),
        "player_id": receipt.get("player_id"),
        "type": "batter",
        "name": receipt.get("player_name"),
        "team": receipt.get("team"),
        "matchup": receipt.get("matchup"),
        "game_start": receipt.get("game_start"),
        "stat": receipt.get("stat"),
        "projection": {
            "stat": receipt.get("stat"),
            "value": receipt.get("line"),
            "needs": receipt.get("needs"),
        },
        # The wager DIRECTION, sealed pregame. Not the team side, not the line.
        "market_side": receipt.get("market_side"),
        "prop": receipt.get("prop_label"),
        "recommendation_status": receipt.get("recommendation_status"),
    }


def settle(receipt, context, *, date=None, grader=None):
    """Settle one receipt through the production public settlement path.

    Returns a settlement EVENT body. The receipt itself is never touched -- the
    input is read, never written -- and the event carries both the receipt id
    and its content hash so the pairing is provable rather than asserted.
    """
    if grader is None:
        from grade_results import grade_public_pick as grader

    pick = reconstruct_pick(receipt)
    graded = grader(pick, context, date=date)
    outcome = graded.get("grade") or "ungraded"
    if outcome not in ALL_OUTCOMES:
        outcome = "ungraded"

    eligibility = graded.get("eligibility") or {}
    return {
        "settlement_schema_version": 1,
        # The exact pairing. Both, always: an id alone could point at a
        # receipt whose content differs from the one actually settled.
        "receipt_id": receipt.get("receipt_id"),
        "receipt_content_sha256": receipt.get("receipt_content_sha256"),
        "decisive_epoch_id": receipt.get("decisive_epoch_id"),
        "canonical_prop_id": receipt.get("canonical_prop_id"),
        "slate_date": receipt.get("slate_date"),

        # The wager as sealed, restated so the settlement is self-describing
        # and can be audited without re-opening the receipt.
        "stat": receipt.get("stat"),
        "market_side": receipt.get("market_side"),
        "line": receipt.get("line"),
        "needs": receipt.get("needs"),

        "outcome": outcome,
        "decided": outcome in DECIDED,
        "settlement_reason": graded.get("reason"),
        "settlement_authority": "full_count_public_settlement",
        "settlement_source": "mlb_statsapi_boxscore",
        "settlement_eligibility": eligibility.get("eligibility"),
        "settlement_eligibility_reason": eligibility.get("reason_code"),
        "actual_value": graded.get("actual"),
        "graded_at": graded.get("graded_at"),
        "champion_member": receipt.get("champion_member"),
        "pa_v1_member": receipt.get("pa_v1_member"),
    }


def verify_pairing(receipt, settlement):
    """Prove a settlement belongs to this exact sealed receipt."""
    if settlement.get("receipt_id") != receipt.get("receipt_id"):
        raise ReceiptMismatch(
            f"settlement receipt_id {settlement.get('receipt_id')} != "
            f"{receipt.get('receipt_id')}")
    if (settlement.get("receipt_content_sha256")
            != receipt.get("receipt_content_sha256")):
        raise ReceiptMismatch(
            "settlement content hash does not match the sealed receipt; the "
            "receipt was edited or a different state was settled")
    return True


def summarize(settlements, *, arm):
    """Protocol section 12's per-arm reporting block.

    hit_rate uses the DECIDED denominator only. void_rate and ungraded_rate use
    the SELECTED denominator, so a challenger cannot look better merely by
    producing more non-decisions -- a difference that is invisible if only the
    decided hit rate is reported.
    """
    key = "champion_member" if arm == "champion" else "pa_v1_member"
    rows = [s for s in settlements if s.get(key)]
    counts = {o: sum(1 for s in rows if s.get("outcome") == o)
              for o in ALL_OUTCOMES}
    selected = len(rows)
    decided = counts["hit"] + counts["miss"]
    return {
        "arm": arm,
        "selected_n": selected,
        "decided_n": decided,
        "hit": counts["hit"],
        "miss": counts["miss"],
        "void": counts["void"],
        "ungraded": counts["ungraded"],
        "hit_rate": (counts["hit"] / decided) if decided else None,
        "void_rate": (counts["void"] / selected) if selected else None,
        "ungraded_rate": (counts["ungraded"] / selected) if selected else None,
    }
