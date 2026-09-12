"""PA-v1 historical-semantics compatibility adapter.

═══════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
═══════════════════════════════════════════════════════════════════════════

PA-v1's `days_rest` feature does NOT mean the same thing live and historically.
Mission 1.1 claimed parity on the grounds that both regimes use the same
`_sig()` call site and the same decoding function. That reasoning was
insufficient and the conclusion was wrong: the two paths differ in the
REFERENCE CLOCK the raw value is measured against.

    mlb_sources.py:891   today = strptime(m.TODAY)
    mlb_sources.py:914   days_since_last_game = (today - last).days

    backtest/engine.py:420  self.cutoff = shift(self.date, -1)   # D-1
    backtest/engine.py:476  ("TODAY", self.cutoff)               # m.TODAY = D-1

So during historical replay `m.TODAY` is **D-1**, while live it is **D**. The
same real circumstance therefore yields values one apart, and two of them land
in different PA-v1 fitted cells:

    last game    historical            live
    D-1          0  -> 0_days_rest     1  -> 0_days_rest      same
    D-2          1  -> 0_days_rest     2  -> 2-3_days_rest    DIFFERENT
    D-3          2  -> 2-3_days_rest   3  -> 4plus_days_rest  DIFFERENT
    D-4+         3+ -> 4plus           4+ -> 4plus            same

D-2 -- a single off day -- is among the most common circumstances in baseball.

═══════════════════════════════════════════════════════════════════════════
WHAT THE FROZEN ARTIFACT ACTUALLY LEARNED
═══════════════════════════════════════════════════════════════════════════

Proven from the path, not assumed. Historical value = (D-1) - last_game_date,
which is exactly the count of OFF DAYS between the last game and the slate
date:

    last = D-1 -> 0 off days      last = D-3 -> 2 off days (D-2, D-1)
    last = D-2 -> 1 off day       last = D-k -> k-1 off days

So the frozen feature is `off_days_since_last_game`, NOT live calendar
`days_since_last_game`.

═══════════════════════════════════════════════════════════════════════════
THE TRANSFORM, AND WHY IT NEEDS THE RAW VALUE
═══════════════════════════════════════════════════════════════════════════

The stored signal is the SCALED value clamp((n-1)*2, -3, 4), and the clamp is
lossy: v_live == 4 means n_live in {3,4,5,...}, whose historical images span
BOTH `2-3_days_rest` (n_live=3) and `4plus_days_rest` (n_live>=4). The scaled
signal therefore CANNOT be inverted, and the raw `days_since_last_game` is
required. Verified by enumeration in test_pa_v1_compat.py.

    n_live is None  ->  None      (absent, exactly as today)
    n_live == 0     ->  None      (see the doubleheader rule below)
    n_live >= 1     ->  n_hist = n_live - 1, then clamp((n_hist - 1) * 2, -3, 4)

DOUBLEHEADER RULE, EXPLICITLY DEFINED. `n_live == 0` means the player's most
recent game is on the slate date itself -- game 1 of a doubleheader, captured
before game 2. The historical path could never observe this, because its
`asof = D-1` filter (mlb_sources.py:906-907) drops every game after D-1, so it
would have measured from the PREVIOUS game instead. That previous game's date
is not recoverable from a bare day count, so the historical-equivalent value is
UNDERIVABLE. It returns None and PA-v1 falls back to the batting-order marginal
-- the artifact's own declared fallback -- rather than guessing. Fail closed.

This adapter is deterministic, outcome-blind, derived solely from pregame
information, and changes NOTHING about the fitted tables or the training
artifact. The PA-v1 artifact hash is unaffected.
"""

from __future__ import annotations

COMPAT_VERSION = "pa-v1-rest-semantics-compat-v1"

# The frozen artifact's true feature, named for what it actually measures.
HISTORICAL_FEATURE = "off_days_since_last_game"

# Reference clocks, recorded so a receipt states them rather than implying them.
HISTORICAL_REFERENCE_CLOCK = "slate_date_minus_1 (PointInTime cutoff)"
LIVE_REFERENCE_CLOCK = "slate_date"

FALLBACK_SAME_DAY_GAME = "same_day_game_historically_unobservable"


def historical_days_rest_raw(live_days_since_last_game):
    """Live raw -> the raw value the frozen artifact was fitted on.

    Returns (value, note). `value` is None when no historical-equivalent
    exists, and `note` says why.
    """
    n = live_days_since_last_game
    if n is None:
        return None, "absent"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None, "unparsable"
    if n < 0:
        return None, "negative"
    if n == 0:
        return None, FALLBACK_SAME_DAY_GAME
    return n - 1, "ok"


def historical_days_rest_signal(live_days_since_last_game, *, clamp_fn=None):
    """The STORED signal value PA-v1 must see, in historical semantics.

    Uses generate_picks.clamp so the arithmetic is the production one, never a
    re-implementation that could drift.
    """
    if clamp_fn is None:
        from generate_picks import clamp as clamp_fn
    raw, note = historical_days_rest_raw(live_days_since_last_game)
    if raw is None:
        return None, note
    return clamp_fn((raw - 1) * 2, -3, 4), note


def adapt_signals(signals, live_days_since_last_game, *, clamp_fn=None):
    """Return a COPY of `signals` with days_rest in historical semantics.

    Never mutates the input. The production candidate keeps its own live-clock
    signal untouched -- this rewrite is visible only to PA-v1 scoring, so no
    champion probability, score, weight or threshold is affected.

    When no historical equivalent exists the key is REMOVED rather than set to
    None, because `_sig()`'s contract is that an absent input is an absent key,
    and `joint_key()` reads presence.
    """
    out = dict(signals or {})
    value, note = historical_days_rest_signal(live_days_since_last_game,
                                              clamp_fn=clamp_fn)
    if value is None:
        out.pop("days_rest", None)
    else:
        out["days_rest"] = value
    return out, note


def provenance(live_days_since_last_game):
    """The block a receipt records so the transform is auditable."""
    raw, note = historical_days_rest_raw(live_days_since_last_game)
    return {
        "compat_version": COMPAT_VERSION,
        "historical_feature": HISTORICAL_FEATURE,
        "historical_reference_clock": HISTORICAL_REFERENCE_CLOCK,
        "live_reference_clock": LIVE_REFERENCE_CLOCK,
        "live_days_since_last_game": live_days_since_last_game,
        "historical_equivalent_raw": raw,
        "note": note,
    }
