#!/usr/bin/env python3
"""
final_card.py — turns the day's board into a final betting card once real
sportsbook prices are known.

WHY THIS IS A SEPARATE, MANUAL STEP.

No free source of player-prop PRICES exists. This was verified rather than
assumed: Action Network's public scoreboard exposes only game markets
(moneyline, spread, total, team runs) and 404s on every props endpoint tried,
and The Odds API charges for player props. Everything else in this pipeline
is automatic; this one step cannot be, and pretending otherwise would mean
inventing prices, which is worse than asking for thirty seconds of typing.

WHAT THIS FIXES.

The board previously refused to consider any prop the model put above 75%,
on the grounds that such a prop would be priced around -350 or worse. That
solved a price problem by throwing away a probability estimate, and it was
wrong twice over: a 90% read is a fact about the player rather than about the
book, and books frequently post derivative lines at friendlier prices than a
naive conversion suggests. The estimate is now kept in full, and the price
question is settled here against the real posted number instead of a guess.

THE TEST A PICK HAS TO PASS.

Two independent constraints, and the tighter one binds:

  1. FAIR VALUE. Betting worse than the model's own fair odds is negative
     expectation by construction, no matter how likely the bet is. A 68.4%
     shot is fair at -216; taking it at -300 loses money over time even
     though it wins about two thirds of the time.
  2. THE HARD PRICE LIMIT (default -350). An independent preference for
     near-even prices, applied regardless of how strong the read is.

USAGE

    python3 final_card.py                      # prints the board + the price to beat
    python3 final_card.py --odds odds.txt      # applies real prices, writes the card
    echo "1 -145
    2 +105
    3 -400" | python3 final_card.py --odds -

Odds input is deliberately forgiving, one pick per line, in any of:

    1 -145                  (board rank, then price)
    Bobby Witt Jr. -145     (player name, then price)
    3 skip                  (not offered / not available)

Blank lines and anything after a '#' are ignored.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

import prop_probability as pp

OUT_DIR = "output"


def picks_path(date):
    return os.path.join(OUT_DIR, f"picks_{date}.json")


def card_path(date):
    return os.path.join(OUT_DIR, f"final_card_{date}.md")


def load_picks(date):
    path = picks_path(date)
    if not os.path.exists(path):
        raise SystemExit(f"No picks file for {date} ({path}). Run generate_picks.py first.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_odds(text, picks):
    """Map free-form odds lines onto picks, by rank or by player name.

    Returns (mapping, problems). Unmatched lines are reported rather than
    silently dropped -- a typo that quietly discards a price would produce a
    card missing a bet the user thought they had entered."""
    by_rank = {str(p["rank"]): p for p in picks}
    by_name = {p["name"].lower(): p for p in picks}
    mapping, problems = {}, []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        mtok = re.search(r"([+-]?\d{2,5})\s*$", line)
        skipped = re.search(r"\b(skip|none|n/?a|unavailable)\b", line, re.I)
        if not mtok and not skipped:
            problems.append(f"could not find a price in: {raw.strip()!r}")
            continue
        key = line[:mtok.start()].strip() if mtok else re.sub(
            r"\b(skip|none|n/?a|unavailable)\b", "", line, flags=re.I).strip()
        key = key.rstrip(":,-").strip()
        pick = by_rank.get(key) or by_name.get(key.lower())
        if pick is None:
            # Allow a partial name, e.g. "Witt" for "Bobby Witt Jr."
            hits = [p for n, p in by_name.items() if key.lower() and key.lower() in n]
            if len(hits) == 1:
                pick = hits[0]
            elif len(hits) > 1:
                problems.append(f"{key!r} matches {len(hits)} picks — be more specific")
                continue
        if pick is None:
            problems.append(f"no pick matches {key!r}")
            continue
        mapping[pick["rank"]] = None if skipped else int(mtok.group(1))
    return mapping, problems


def evaluate(picks, odds_map, user_limit, margin):
    """Judge each pick against the real posted price."""
    rows = []
    for p in picks:
        prob = p.get("hit_probability")
        limit = pp.max_acceptable_price(prob, user_limit, margin) if prob is not None else None
        posted = odds_map.get(p["rank"], "__absent__")
        if prob is None:
            verdict, note = "NO BET", "no calibrated probability for this pick"
        elif posted == "__absent__":
            verdict, note = "PENDING", "no price entered yet"
        elif posted is None:
            verdict, note = "NO BET", "not offered at the book"
        elif limit is None:
            verdict, note = "NO BET", "could not compute a fair price"
        elif posted >= limit:
            edge = prob - pp.implied_probability(posted)
            verdict = "BET"
            note = f"{edge*100:+.1f} pts of edge over the posted price"
        else:
            # WHICH constraint rejected this matters, and reporting the wrong
            # one is actively misleading. Two very different cases land here:
            # a bet that is genuinely negative expectation, and a bet with
            # real edge that simply costs more than the hard price limit
            # allows. Collapsing them into one message produced output like
            # "would need -6.7 more pts of true probability" for a pick the
            # model actually liked.
            implied = pp.implied_probability(posted)
            verdict = "NO BET"
            if prob > implied:
                note = (f"has {(prob-implied)*100:+.1f} pts of edge, but {posted:+d} "
                        f"is worse than your {user_limit:+d} limit — the price rule "
                        f"rejected this, not the model")
            else:
                note = (f"negative expectation: the price implies "
                        f"{implied*100:.1f}% and the model says {prob*100:.1f}%, "
                        f"so it needs {(implied-prob)*100:.1f} more pts to break even")
        rows.append({"pick": p, "prob": prob, "limit": limit,
                     "posted": None if posted == "__absent__" else posted,
                     "verdict": verdict, "note": note})
    return rows


def render(date, rows, user_limit, margin):
    bet = [r for r in rows if r["verdict"] == "BET"]
    pending = [r for r in rows if r["verdict"] == "PENDING"]
    L = [f"# Final Card — {date}", ""]
    L.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
             f"A pick is only recommended if the REAL posted price clears both "
             f"the model's fair value and a hard limit of {user_limit}"
             + (f", with a {margin*100:.0f}-point cushion" if margin else "")
             + ". No free source of prop prices exists, so prices are entered by "
               "hand — every number below marked 'posted' came from you, not "
               "from a model._")
    L.append("")
    if bet:
        L.append(f"## Bet ({len(bet)})")
        L.append("")
        L.append("| # | pick | model | posted | price to beat | edge |")
        L.append("|---|---|---|---|---|---|")
        for r in bet:
            p = r["pick"]
            L.append(f"| {p['rank']} | {p['name']} — {p['prop']} | "
                     f"{r['prob']*100:.1f}% | {r['posted']:+d} | {r['limit']:+d} | "
                     f"{r['note'].split(' pts')[0]} pts |")
        L.append("")
    else:
        L.append("## Bet (0)")
        L.append("")
        L.append("_Nothing cleared both tests today. That is a real result, not a "
                 "failure — a card with no bets beats a card with bad ones._")
        L.append("")

    no_bet = [r for r in rows if r["verdict"] == "NO BET"]
    if no_bet:
        L.append(f"## Passed on ({len(no_bet)})")
        L.append("")
        for r in no_bet:
            p = r["pick"]
            posted = f"{r['posted']:+d}" if r["posted"] is not None else "n/a"
            L.append(f"- **{p['name']} — {p['prop']}** · model "
                     + (f"{r['prob']*100:.1f}%" if r["prob"] is not None else "n/a")
                     + f" · posted {posted} · {r['note']}")
        L.append("")
    if pending:
        L.append(f"## Awaiting a price ({len(pending)})")
        L.append("")
        L.append("| # | pick | model | price to beat |")
        L.append("|---|---|---|---|")
        for r in pending:
            p = r["pick"]
            lim = f"{r['limit']:+d}" if r["limit"] is not None else "n/a"
            L.append(f"| {p['rank']} | {p['name']} — {p['prop']} | "
                     f"{r['prob']*100:.1f}% | {lim} |")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--odds", help="file with posted odds, or '-' for stdin")
    ap.add_argument("--max-price", type=int, default=pp.USER_MAX_PRICE,
                    help="worst acceptable American price (default %(default)s)")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="require this much probability cushion below fair value, "
                         "e.g. 0.05 for 5 points")
    args = ap.parse_args()

    payload = load_picks(args.date)
    picks = payload.get("picks", [])
    if not picks:
        raise SystemExit(f"No picks in {picks_path(args.date)}.")

    odds_map = {}
    if args.odds:
        text = sys.stdin.read() if args.odds == "-" else open(args.odds, encoding="utf-8").read()
        odds_map, problems = parse_odds(text, picks)
        for p in problems:
            print(f"  warning: {p}", file=sys.stderr)

    rows = evaluate(picks, odds_map, args.max_price, args.margin)

    if not args.odds:
        # No prices yet: print what to shop for, in the order to shop for it.
        print(f"Board for {args.date} — the price each pick has to beat:\n")
        print(f"  {'#':>2s}  {'pick':<52s} {'model':>7s}  {'beat this':>9s}")
        for r in rows:
            p = r["pick"]
            label = f"{p['name']} — {p['prop']}"
            prob = f"{r['prob']*100:.1f}%" if r["prob"] is not None else "n/a"
            lim = f"{r['limit']:+d}" if r["limit"] is not None else "n/a"
            print(f"  {p['rank']:>2d}  {label[:52]:<52s} {prob:>7s}  {lim:>9s}")
        print("\nEnter prices with:  python3 final_card.py --odds odds.txt")
        print("One per line, e.g.  '1 -145'  or  'Witt -145'  or  '3 skip'")
        return 0

    card = render(args.date, rows, args.max_price, args.margin)
    with open(card_path(args.date), "w", encoding="utf-8") as f:
        f.write(card)
    n_bet = sum(1 for r in rows if r["verdict"] == "BET")
    print(card)
    print(f"\nWrote {card_path(args.date)} — {n_bet} bet(s) of {len(rows)} candidates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
