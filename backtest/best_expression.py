#!/usr/bin/env python3
"""best_expression.py -- correlation-aware selection: one thesis, one bet.

THE OBSERVATION THIS ADDRESSES. The real public Top Pick ledger ran 39
settled wagers drawn from roughly 18 unique games, and repeatedly bet the
same underlying idea twice: Jared Young's Hits AND his H+R+RBI (both
missed); Cole Young's Hits AND his H+R+RBI (both missed). Those are not
two independent reads. They are one read -- "this batter has a good
offensive game" -- expressed twice, so they win together and lose
together. A customer holding both did not receive two pieces of
information, and the portfolio's realized variance is larger than its
pick count suggests.

THE HYPOTHESIS, stated before any measurement: if a slate's strongest
expression of each thesis is kept and the redundant expressions are
replaced by the next-best INDEPENDENT candidate, equal-volume realized
hit rate improves -- because the freed slots buy exposure to new games
and players instead of re-buying one already-owned outcome.

That is a hypothesis. This module builds the machinery and the accounting
to test it. It makes no claim about the answer, and nothing here should be
quoted as evidence until it has been run against a validated canonical
artifact through backtest/equal_volume.py.

THE KEY DESIGN DECISION: SUPPRESSION IS DEMOTION, NOT DELETION.

The obvious implementation -- drop redundant expressions from the
candidate list -- is precisely the failure the equal-volume contract
exists to prevent. Dropping picks shrinks N, and a smaller, more
confident portfolio will usually post a better hit rate for reasons that
have nothing to do with the idea being tested. So a suppressed expression
is not removed; it is moved DOWN the ranking, below every unsuppressed
candidate. The consequences fall out for free:

  * the ranking still covers the entire eligible population, so
    equal_volume.SelectionPolicy accepts it and slices exactly N;
  * the slots freed at the top are filled by the next-best independent
    candidates, automatically, from within the declared population --
    refill can never reach outside it because there is nowhere else to
    reach;
  * if the population genuinely lacks enough independent candidates, the
    demoted ones flow back into the top N rather than the experiment
    quietly running short. The portfolio is then honestly no more
    diversified than the champion's, which the accounting reports rather
    than hides.

Ranking remains deterministic and independent of input order, because a
non-reproducible selector cannot be evidence of anything.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from backtest.equal_volume import _identity_sort_key, candidate_identity

# How several wagers get recognised as one underlying idea.
THESIS_PLAYER_GAME = "player_game"   # same batter, same game -> one offensive read
THESIS_GAME = "game"                 # anything in the same game
THESIS_PLAYER_DATE = "player_date"   # same player that day (doubleheader-tolerant)

THESIS_MODES = (THESIS_PLAYER_GAME, THESIS_GAME, THESIS_PLAYER_DATE)


def thesis_identity(row, mode=THESIS_PLAYER_GAME):
    """The underlying idea a wager expresses.

    player_game is the default because it is the tightest defensible
    grouping and the one the observed ledger failures actually shared: a
    batter's Hits and his H+R+RBI in the SAME game resolve off the same
    plate appearances. Grouping by game alone would also suppress two
    genuinely different reads about opposing players, which is a
    materially stronger claim and is offered separately rather than
    assumed."""
    if mode == THESIS_PLAYER_GAME:
        return ("player_game", row.get("game_pk"), row.get("player_id"))
    if mode == THESIS_GAME:
        return ("game", row.get("game_pk"))
    if mode == THESIS_PLAYER_DATE:
        return ("player_date", row.get("date"), row.get("player_id"))
    raise ValueError(f"unknown thesis mode {mode!r}")


def best_expression_rank_fn(base_key_fn, *, thesis_mode=THESIS_PLAYER_GAME,
                            max_per_thesis=1, reverse=True):
    """Build a ranking that keeps the best `max_per_thesis` expressions of
    each thesis at full strength and demotes the rest below everything
    else.

    `base_key_fn(row)` supplies the underlying preference (the same score
    a non-correlation-aware selector would rank on), so this composes with
    any existing policy rather than replacing its judgement.
    """
    if max_per_thesis < 1:
        raise ValueError("max_per_thesis must be at least 1")

    def _rank(population):
        scored = []
        for ident in population.identities:
            r = population.row(ident)
            v = base_key_fn(r)
            scored.append((v is not None, v if v is not None else 0,
                           _identity_sort_key(ident), ident,
                           thesis_identity(r, thesis_mode)))

        # Total, input-order-independent ordering: primary preference,
        # then identity as a deterministic tiebreak.
        scored.sort(key=lambda t: (t[2],))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=reverse)

        kept, demoted, seen = [], [], Counter()
        for has, val, tie, ident, thesis in scored:
            if seen[thesis] < max_per_thesis:
                kept.append(ident)
            else:
                demoted.append(ident)
            seen[thesis] += 1
        # Demoted candidates keep their relative order and sit strictly
        # below every kept one -- never discarded (see module docstring).
        return kept + demoted

    return _rank


def describe_suppression(population, base_key_fn, volume, *,
                         thesis_mode=THESIS_PLAYER_GAME, max_per_thesis=1,
                         reverse=True):
    """Transparent accounting of what correlation-awareness actually did.

    Reports what the base ranking would have taken, what suppression
    removed from that top-N, what refilled the freed slots, and -- the
    number that decides whether the comparison is honest -- whether the
    population contained enough independent candidates to refill at all.
    """
    base_order = []
    scored = []
    for ident in population.identities:
        r = population.row(ident)
        v = base_key_fn(r)
        scored.append((v is not None, v if v is not None else 0,
                       _identity_sort_key(ident), ident))
    scored.sort(key=lambda t: (t[2],))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=reverse)
    base_order = [t[3] for t in scored]

    be_order = best_expression_rank_fn(
        base_key_fn, thesis_mode=thesis_mode, max_per_thesis=max_per_thesis,
        reverse=reverse)(population)

    base_top, be_top = base_order[:volume], be_order[:volume]
    base_set, be_set = set(base_top), set(be_top)

    def _theses(idents):
        return Counter(thesis_identity(population.row(i), thesis_mode) for i in idents)

    base_theses, be_theses = _theses(base_top), _theses(be_top)
    # How many of the base top-N were redundant expressions
    redundant_in_base = sum(c - max_per_thesis for c in base_theses.values()
                            if c > max_per_thesis)

    # Could the population actually refill? Count candidates whose thesis
    # is not already represented in the base top-N.
    represented = set(base_theses)
    independent_available = sum(
        1 for i in base_order[volume:]
        if thesis_identity(population.row(i), thesis_mode) not in represented)

    removed = sorted(base_set - be_set, key=_identity_sort_key)
    refilled = sorted(be_set - base_set, key=_identity_sort_key)

    return {
        "thesis_mode": thesis_mode,
        "max_per_thesis": max_per_thesis,
        "volume": volume,
        "base_unique_theses": len(base_theses),
        "best_expression_unique_theses": len(be_theses),
        "redundant_expressions_in_base_top_n": redundant_in_base,
        "suppressed_from_top_n": [list(i) for i in removed],
        "refilled_into_top_n": [list(i) for i in refilled],
        "n_suppressed": len(removed),
        "n_refilled": len(refilled),
        "exact_volume_preserved": len(be_top) == volume,
        "independent_candidates_available_below_cut": independent_available,
        "fully_refillable": independent_available >= redundant_in_base,
        "note": ("If fully_refillable is False the population simply did not contain "
                 "enough independent candidates; demoted expressions flow back into the "
                 "top N and the challenger is honestly no more diversified than the "
                 "champion. Volume is preserved either way -- it is never reduced to "
                 "manufacture a better rate."),
    }
