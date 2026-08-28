#!/usr/bin/env python3
"""price_join.py -- attach real FanDuel prices to canonical model rows.

WHY THIS EXISTS.

Every measurement this project makes on canonical rows answers "did it
hit". None of them answer "would it have made money", because canonical
rows carry no price. That gap has a specific and expensive consequence:
maximising hit rate has a trivial degenerate solution -- bet the shortest
prices -- and the board's own history shows where that leads. Picks
shipped at an average of -254, needing 71.3% to break even, hit 56.1%,
and returned -22.1% (see generate_picks.py's own note above
select_main_board).

It also hides the opposite failure. MIN_LINE_PROB = 0.60 excludes every
prop the model prices below 60%, which is 85.1% of what FanDuel actually
offers -- measured over data/props/: 2,817,401 priced rows, of which
2,398,594 are plus money and 1,647,480 are +300 or longer. Whether the
model is any good there is currently unmeasured, not unmeasurable.

This module is the join. It does no scoring and no selection.

THE INVARIANT THAT MATTERS MOST.

A price quoted after first pitch already knows what happened. Joining one
to a historical row is lookahead of the purest kind -- it would show a
spectacular fake edge on exactly the longshot markets this work is aimed
at, because a +6500 triple gets marked down the moment the batter singles.

So exclusion of in-play quotes is an INVARIANT here, not a filter the
caller may pass. assert_no_inplay() raises, and the price selected for a
row is always the last quote taken STRICTLY BEFORE that game's start.
Both conditions are checked, not one: the archive's own in_play flag
agreed with the timestamp test on 1,023,166 rows with zero disagreements
either way, but agreement measured over ten days is not a guarantee, and
the cost of being wrong here is a fabricated result.

WHICH QUOTE, AND WHY IT IS PRE-COMMITTED.

Prices move a lot intraday: median within-day range 150 American points,
p90 2,000, and 70.1% of props move at least 50 points. That means the
snapshot you choose materially decides whether a strategy looks
profitable, which makes it exactly the kind of parameter that must be
fixed in advance rather than chosen after seeing results.

LAST_PREGAME is the default and the pre-registered rule: the final quote
strictly before first pitch, the closest honest analogue to "the price
you could actually have taken". OPENING is offered for a pre-declared
line-movement comparison. There is deliberately no "best price" option --
it would be a post-hoc maximiser wearing a policy's clothes.

WHAT THIS CANNOT DO.

Batter props are archived one-sided (`american` only), so true no-vig
probability is not recoverable for them; realized ROI at the posted price
is. Pitcher props are archived two-sided with the hold already removed
(see prop_snapshot.capture_two_sided), and are richer as a result.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPS_DIR = os.path.join(REPO_ROOT, "data", "props")

LAST_PREGAME = "last_pregame"
OPENING = "opening"
QUOTE_RULES = (LAST_PREGAME, OPENING)

# Canonical prop_type -> archived stat name. Only genuine spelling
# differences belong here; a market absent from one side must stay absent
# rather than be aliased onto a near neighbour.
STAT_ALIASES = {"home_run": "home_runs"}


class PriceJoinError(Exception):
    """The price data cannot be used as asked."""


class LookaheadError(PriceJoinError):
    """An in-play or post-first-pitch quote reached the join."""


def _ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def normalize_player(name):
    """The archive's OWN normalizer, reused rather than reimplemented.

    A first version approximated it and disagreed on 11 of 321 names for
    one day -- it kept generational suffixes and stripped apostrophes,
    where the archive does the reverse. The names it lost were Witt,
    Tatis, Acuna, Guerrero, Harris and O'Neill: the biggest home-run bats
    on the slate, silently absent from exactly the longshot analysis this
    module exists for. An unmatched name is indistinguishable from an
    unpriced market, so approximating the rule is not good enough --
    delegating to it makes agreement hold by construction.
    """
    from odds_fanduel import normalize_name
    return normalize_name(str(name or ""))


def assert_no_inplay(rows):
    """Raise if any quote was taken at or after its game's start, or is
    flagged in_play. Belt and braces, deliberately."""
    bad = []
    for r in rows:
        st, ta = r.get("start_time"), r.get("taken_at")
        if r.get("in_play"):
            bad.append((r.get("player_norm"), r.get("stat"), "in_play flag"))
        elif st and ta and _ts(ta) >= _ts(st):
            bad.append((r.get("player_norm"), r.get("stat"), f"taken {ta} >= start {st}"))
    if bad:
        raise LookaheadError(
            f"{len(bad)} quote(s) were taken at or after first pitch and would "
            f"leak the outcome into the price (e.g. {bad[:3]}). A post-start "
            f"price already knows what happened.")
    return True


def load_price_index(dates=None, props_dir=PROPS_DIR, quote_rule=LAST_PREGAME,
                     include_two_sided=True):
    """Build {(date, player_norm, stat, needs): quote} under quote_rule.

    Every in-play/post-start quote is dropped BEFORE selection, so the
    chosen quote can never be one of them. Returns (index, report).
    """
    if quote_rule not in QUOTE_RULES:
        raise PriceJoinError(
            f"unknown quote_rule {quote_rule!r}; choose one of {QUOTE_RULES} "
            f"in advance -- picking a rule after seeing results is not a rule")

    files = sorted(glob.glob(os.path.join(props_dir, "props_*.json")))
    want = set(dates) if dates else None
    cand = defaultdict(list)
    report = {"files": 0, "rows_seen": 0, "dropped_inplay": 0,
              "dropped_no_price": 0, "two_sided_rows": 0, "dates": []}

    for path in files:
        day = os.path.basename(path)[6:16]
        if want is not None and day not in want:
            continue
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
        report["files"] += 1
        report["dates"].append(day)

        groups = [("snapshots", False)] + ([("two_sided_snapshots", True)]
                                           if include_two_sided else [])
        for key, two_sided in groups:
            for snap in (blob.get(key) or []):
                for r in (snap.get("rows") or []):
                    report["rows_seen"] += 1
                    st, ta = r.get("start_time"), r.get("taken_at") or snap.get("taken_at")
                    if r.get("in_play") or (st and ta and _ts(ta) >= _ts(st)):
                        report["dropped_inplay"] += 1
                        continue
                    price = r.get("american") if not two_sided else r.get("over_odds")
                    if price is None:
                        report["dropped_no_price"] += 1
                        continue
                    if two_sided:
                        report["two_sided_rows"] += 1
                    # Two-sided (pitcher) rows name the market "market";
                    # one-sided (batter) rows name it "stat". Reading only
                    # "stat" keyed every pitcher prop under None and made
                    # all 9,229 of them unfindable -- caught by the test
                    # that asserts strikeouts/pitcher_outs are present.
                    stat = r.get("stat") or r.get("market")
                    cand[(day, r.get("player_norm"), stat, r.get("needs"))].append({
                        "taken_at": ta, "start_time": st, "american": price,
                        "two_sided": two_sided,
                        "true_over": r.get("true_over"), "hold": r.get("hold"),
                        "in_play": False,
                    })

    index = {}
    for k, quotes in cand.items():
        quotes.sort(key=lambda q: q["taken_at"])
        index[k] = quotes[-1] if quote_rule == LAST_PREGAME else quotes[0]
    report["priced_props"] = len(index)
    return index, report


def american_to_decimal(a):
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def implied_prob(a):
    a = float(a)
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def join_rows(canonical_rows, index):
    """Attach a price to each canonical row. Unmatched rows are RETURNED
    as unmatched, never silently dropped -- a join that quietly loses the
    rows it cannot price reports the coverage of whatever it kept."""
    matched, unmatched = [], []
    for r in canonical_rows:
        stat = STAT_ALIASES.get(r.get("prop_type"), r.get("prop_type"))
        key = (r.get("date"), normalize_player(r.get("player_name")),
               stat, r.get("needs"))
        q = index.get(key)
        if q is None:
            unmatched.append(r)
            continue
        row = dict(r)
        row["american"] = q["american"]
        row["decimal"] = american_to_decimal(q["american"])
        row["posted_implied"] = implied_prob(q["american"])
        row["price_taken_at"] = q["taken_at"]
        row["price_two_sided"] = q["two_sided"]
        row["market_true_over"] = q.get("true_over")
        row["market_hold"] = q.get("hold")
        matched.append(row)
    return matched, unmatched


def realized_roi(rows, stake=1.0):
    """Flat-stake ROI at the posted price. The number hit rate cannot give."""
    if not rows:
        return {"n": 0, "hit_rate": None, "roi": None, "profit": None,
                "avg_american": None, "avg_posted_implied": None}
    profit = 0.0
    for r in rows:
        won = bool(r.get("outcome"))
        profit += stake * (r["decimal"] - 1.0) if won else -stake
    n = len(rows)
    return {
        "n": n,
        "hit_rate": round(sum(1 for r in rows if r.get("outcome")) / n, 4),
        "roi": round(profit / (n * stake), 4),
        "profit": round(profit, 2),
        "avg_american": round(sum(r["american"] for r in rows) / n, 1),
        "avg_posted_implied": round(sum(r["posted_implied"] for r in rows) / n, 4),
        "breakeven_hit_rate": round(sum(r["posted_implied"] for r in rows) / n, 4),
    }


def roi_by_price_band(rows):
    """ROI split by price band -- the question 'are we only good at -300?'
    is exactly this table."""
    bands = [("+300 or longer", lambda a: a >= 300),
             ("+200..+299", lambda a: 200 <= a < 300),
             ("+100..+199", lambda a: 100 <= a < 200),
             ("+1..+99", lambda a: 0 < a < 100),
             ("-1..-150", lambda a: -150 <= a < 0),
             ("-151..-300", lambda a: -300 <= a < -150),
             ("worse than -300", lambda a: a < -300)]
    out = []
    for label, pred in bands:
        sub = [r for r in rows if pred(r["american"])]
        if sub:
            out.append({"band": label, **realized_roi(sub)})
    return out
