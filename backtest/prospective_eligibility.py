"""Primary operational candidate pool for the prospective Hits PA-v1 shadow.

Locked protocol reference:
  engineering/locked-protocols/
    FULL_COUNT_PROSPECTIVE_HITS_PA_SHADOW_PROTOCOL_V1_LOCKED_2026-09-01.md
  sha256 5ce1ae95c4d3034d7948eb0ad7bc2441efcf2cabb234944e36bc315b2b355de7

WHY THIS MODULE EXISTS AT ALL, stated first because it is the single easiest
thing to get wrong here: ``by_category_full["hits"]`` at the capture tap is the
RAW CAPTURE SOURCE. It is not the eligible pool. That list is deliberately
built with ``n_per_category=9999, min_score=0`` so the public site can always
show a research row for every market -- it therefore contains assumed-lineup
rows, rows with no real posted FanDuel price, rows with reliability C/D, and
rows with no evidence sample at all. Every one of those is a legitimate
research row and an illegitimate wager.

Protocol section 5 defines the decisive cohort as the subset of that raw
population for which ALL policy-independent operational requirements hold.
"Policy-independent" is load-bearing in both directions:

  * it EXCLUDES the current model's own opinion. There is no
    ``predicted_prob >= 0.60`` gate and no ROI/value gate here, because both
    are mathematically conditional on the champion's own probability and
    would hand the champion its own selection rule as an entry requirement.
  * it INCLUDES everything that decides whether a human could actually have
    placed this exact wager at this exact moment: a confirmed lineup, a real
    posted price for this exact expression, a game that has not started, a
    settleable market, and real evidence behind the number.

Nothing in this module reads, writes, or influences production
recommendations. It is a pure predicate over already-built rows.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.live_state import (  # noqa: E402
    canonical_prop_id,
    market_side_token,
    prop_identity_key,
)
from dashboard.publication_registry import (  # noqa: E402
    PUBLICATION_DEPLOYMENT_LEAD_SECONDS,
)
from recommendation import (  # noqa: E402
    MAX_BOARD_AGE_SECONDS,
    MAX_PRICE_AGE_SECONDS,
)

PROTOCOL_VERSION = "prospective-hits-pa-v1"
PROTOCOL_SHA256 = "5ce1ae95c4d3034d7948eb0ad7bc2441efcf2cabb234944e36bc315b2b355de7"

# Only Hits. The shadow is a single-market experiment by construction; a
# second market is a second protocol, not a wider filter here.
SHADOW_STAT = "hits"

# grade_results.py:662 settles `hits` off the real box score. This set is a
# deliberate allow-list, not a convenience default: a market this pipeline
# cannot structurally settle can never produce a decided prospective receipt,
# so it must not enter the denominator.
SETTLEMENT_SUPPORTED_STATS = frozenset({"hits"})

# attach_reliability()'s own grades. A/B is the same bar the production
# recommendation layer already requires for real exposure.
ELIGIBLE_RELIABILITY = frozenset({"A", "B"})

# refresh_prices.py's observation vocabulary. Only MATCHED is a real current
# price for THIS exact expression. LINE_MOVED explicitly means the book is no
# longer offering the threshold this row was scored on -- accepting it would
# silently substitute a different wager, which the identity contract forbids.
# NOT_POSTED / FETCH_FAILED / IN_PLAY are, respectively, a real absence, an
# unknown, and a live game.
ACCEPTABLE_MARKET_FETCH_STATES = frozenset({"MATCHED"})

# Protocol section 7: preserve the EXISTING publication cutoff contract. This
# is imported, never redeclared, so a change to the production rule moves the
# shadow with it instead of silently forking a looser shadow cutoff.
PUBLICATION_LEAD_SECONDS = PUBLICATION_DEPLOYMENT_LEAD_SECONDS

GATES = (
    "stat_is_shadow_market",
    "canonical_identity_valid",
    "wager_expression_complete",
    "settlement_supported",
    "game_start_known",
    "before_publication_cutoff",
    "commencement_not_occurred",
    "not_prior_date_resumption",
    "lineup_confirmed",
    "evidence_sample_nonzero",
    "reliability_a_or_b",
    "real_current_price",
    "price_freshness_valid",
    "board_freshness_valid",
    "no_source_integrity_hold",
)


class _Absent:
    """Sentinel: a field that is missing is not a field that is False."""


ABSENT = _Absent()


def _parse_ts(value):
    """Parse an ISO-8601 instant to an aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def wager_expression(row):
    """The four distinct identity-bearing facts, never collapsed into one.

    This project has already been bitten by exactly this conflation, so it is
    spelled out rather than left to the reader:

      team_side  -- ``row["side"]`` is "home"/"away", i.e. WHICH TEAM the
                    subject plays for (generate_picks.py:2692). It is NOT the
                    direction of the wager and must never be used as one.
      market_side -- the real over/under (or nrfi/yrfi) direction, derived by
                    dashboard.live_state.market_side_token().
      line       -- ``projection.value``, the number printed on the ticket.
      needs      -- ``projection.needs``, the integer outcome required to win.

    line and needs are related but not equal (Over 0.5 Hits has line 0.5 and
    needs 1), and neither is the side. All four are recorded separately.
    """
    projection = row.get("projection") or {}
    return {
        "team_side": row.get("side"),
        "market_side": market_side_token(row),
        "line": projection.get("value"),
        "needs": projection.get("needs"),
        "stat": projection.get("stat") or row.get("stat"),
    }


def evaluate_row(row, *, now, schedule, freshness=None, odds_fetched_at=None,
                 board_generated_at=None, source_integrity_holds=frozenset(),
                 shadow_stat=SHADOW_STAT,
                 publication_lead_seconds=PUBLICATION_LEAD_SECONDS):
    """Evaluate one raw captured row against every protocol section 5 gate.

    Every gate is evaluated, never short-circuited, so the returned trace is a
    complete audit record of WHY a row was excluded rather than only the first
    reason found. Returns a dict with ``eligible``, an ordered ``gates`` map,
    and the resolved identity/expression fields.

    Fail-closed throughout: an unknown is a rejection, never a pass. A gate
    whose input is missing evaluates False.
    """
    gates = {}
    notes = {}

    projection = row.get("projection") or {}
    stat = projection.get("stat") or row.get("stat")
    gates["stat_is_shadow_market"] = (stat == shadow_stat)

    # -- identity ---------------------------------------------------------
    identity = None
    prop_id = None
    try:
        identity = prop_identity_key(row)
        prop_id = canonical_prop_id(row)
        gates["canonical_identity_valid"] = True
    except (ValueError, KeyError, TypeError) as exc:
        gates["canonical_identity_valid"] = False
        notes["canonical_identity_valid"] = str(exc)

    expression = None
    try:
        expression = wager_expression(row)
        gates["wager_expression_complete"] = (
            expression["line"] is not None
            and expression["needs"] is not None
            and bool(expression["market_side"])
            and expression["stat"] is not None
        )
    except (ValueError, KeyError, TypeError) as exc:
        gates["wager_expression_complete"] = False
        notes["wager_expression_complete"] = str(exc)

    gates["settlement_supported"] = stat in SETTLEMENT_SUPPORTED_STATS

    # -- game state -------------------------------------------------------
    game_pk = row.get("game_pk")
    info = (schedule or {}).get(game_pk) or {}
    game_start = _parse_ts(info.get("start"))
    gates["game_start_known"] = game_start is not None

    if game_start is None:
        gates["before_publication_cutoff"] = False
    else:
        cutoff = game_start - timedelta(seconds=publication_lead_seconds)
        gates["before_publication_cutoff"] = now <= cutoff
        notes["publication_cutoff"] = cutoff.isoformat()

    # `started` is the production commencement signal: MLB abstractGameState
    # has left "Preview". Absent schedule information is treated as commenced,
    # because "we could not tell whether the game had started" is not evidence
    # that it had not.
    if not info:
        gates["commencement_not_occurred"] = False
        notes["commencement_not_occurred"] = "no schedule entry for game_pk"
    else:
        gates["commencement_not_occurred"] = not bool(info.get("started"))

    # A resumption of a game that already commenced on an earlier date is a
    # continuation of live play, not a pregame opportunity, however "Preview"
    # the feed may look. Any of MLB's own back-references disqualifies it.
    resumed_from = (info.get("resumed_from") or info.get("resumedFrom")
                    or info.get("resume_game_date") or info.get("resumeGameDate"))
    gates["not_prior_date_resumption"] = not bool(resumed_from)
    if resumed_from:
        notes["not_prior_date_resumption"] = str(resumed_from)

    # -- lineup -----------------------------------------------------------
    # THE ABSENT-IS-NOT-FALSE TRAP. quality_control() sets
    # ``lineup_assumed = True`` on assumed rows and leaves the key entirely
    # UNSET on confirmed ones -- it never writes False. Writing the protocol's
    # prose literally as ``row.get("lineup_assumed") == False`` would compare
    # None == False and reject every genuinely confirmed candidate, emptying
    # the pool while looking like a correct transcription of the rule.
    lineup_assumed = row.get("lineup_assumed", ABSENT)
    gates["lineup_confirmed"] = (lineup_assumed is ABSENT
                                 or lineup_assumed is None
                                 or lineup_assumed is False)
    notes["lineup_assumed_raw"] = (
        "ABSENT" if lineup_assumed is ABSENT else repr(lineup_assumed))

    # -- evidence ---------------------------------------------------------
    sample_n = row.get("sample_n")
    # None is absent evidence, not nonzero evidence. Both fail.
    gates["evidence_sample_nonzero"] = isinstance(sample_n, int) and sample_n != 0
    gates["reliability_a_or_b"] = row.get("reliability") in ELIGIBLE_RELIABILITY

    # -- market -----------------------------------------------------------
    odds = row.get("market_odds")
    fetch_state = row.get("market_fetch_state")
    if odds is None:
        gates["real_current_price"] = False
        notes["real_current_price"] = "no posted FanDuel price on this expression"
    elif fetch_state is None:
        # At full-build time the price was looked up from the FanDuel maps
        # keyed by this row's own (stat, needs), so it is the exact
        # expression by construction; refresh_prices.py (which is what writes
        # market_fetch_state) has not run against this row yet. A real price
        # with no observation state is accepted; a real price carrying a
        # NON-MATCHED state is not.
        gates["real_current_price"] = True
        notes["real_current_price"] = "full-build price, no incremental observation state"
    else:
        gates["real_current_price"] = fetch_state in ACCEPTABLE_MARKET_FETCH_STATES
        notes["market_fetch_state"] = fetch_state

    # -- freshness --------------------------------------------------------
    fresh = freshness or {}
    priced_at = _parse_ts(odds_fetched_at or fresh.get("market_prices_at"))
    built_at = _parse_ts(board_generated_at or fresh.get("model_basis_at"))

    if priced_at is None:
        gates["price_freshness_valid"] = False
        notes["price_freshness_valid"] = "no odds_fetched_at"
    else:
        age = (now - priced_at).total_seconds()
        gates["price_freshness_valid"] = 0 <= age <= MAX_PRICE_AGE_SECONDS
        notes["price_age_seconds"] = age

    if built_at is None:
        gates["board_freshness_valid"] = False
        notes["board_freshness_valid"] = "no board_generated_at"
    else:
        age = (now - built_at).total_seconds()
        gates["board_freshness_valid"] = 0 <= age <= MAX_BOARD_AGE_SECONDS
        notes["board_age_seconds"] = age

    # -- source integrity -------------------------------------------------
    # HONEST STATE OF THE WORLD, recorded rather than papered over: this
    # repository has NO production mechanism named "source-integrity hold".
    # The gate is therefore implemented as a real, injectable check against a
    # hold set that is EMPTY by default. It is a functioning gate over an
    # empty registry, not a satisfied requirement -- see
    # engineering/PROSPECTIVE_LIVE_UNIVERSE_AUDIT.md, which records this as an
    # open gap. A hold may be keyed by game_pk, team, or canonical prop id.
    holds = set(source_integrity_holds or ())
    held = [k for k in (game_pk, row.get("team"), prop_id)
            if k is not None and k in holds]
    gates["no_source_integrity_hold"] = not held
    if held:
        notes["no_source_integrity_hold"] = held

    ordered = {name: bool(gates.get(name, False)) for name in GATES}
    return {
        "eligible": all(ordered.values()),
        "gates": ordered,
        "failed_gates": tuple(n for n, ok in ordered.items() if not ok),
        "notes": notes,
        "canonical_prop_id": prop_id,
        "identity_key": list(identity) if identity else None,
        "expression": expression,
        "game_start": game_start.isoformat() if game_start else None,
    }


def partition(rows, **kwargs):
    """Split raw captured rows into (eligible, rejected-with-trace).

    Both halves are returned. The rejected half is evidence too: a shadow
    cohort that silently shrinks is indistinguishable from one that was
    correctly gated, and protocol section 12 requires the funnel to be
    reportable.
    """
    eligible, rejected = [], []
    for row in rows or ():
        verdict = evaluate_row(row, **kwargs)
        (eligible if verdict["eligible"] else rejected).append((row, verdict))
    return eligible, rejected


def funnel_counts(rejected):
    """Per-gate rejection counts across the raw population."""
    counts = {name: 0 for name in GATES}
    for _row, verdict in rejected:
        for name in verdict["failed_gates"]:
            counts[name] += 1
    return counts
