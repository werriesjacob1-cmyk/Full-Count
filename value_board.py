#!/usr/bin/env python3
"""
value_board.py — the props where a good read and a fair price actually meet.

WHAT THIS IS FOR.

The daily board answers "which props are most likely to hit". That is the
right question for picking, and the wrong question for betting, because the
book prices likelihood into the number. Every one of the 2026-08-06 board's
ten picks was a genuinely strong read at 63-67% to record a hit, and every
one was unbettable: FanDuel posted them at -250 to -380, implying 71-79%,
against a league base rate of 55%.

This screen asks the other question. It walks every prop the book actually
prices, attaches this pipeline's calibrated probability, and keeps only the
ones where the model's number beats the price by enough to survive the
model's own uncertainty.

TWO TESTS, BOTH REQUIRED.

1. RETURN, not edge in probability points. A fixed "+5 points" rule is far
   too loose at -500 and needlessly strict at +200, because return is
   p*d - 1 and the stake grows as the price shortens. Screening on ROI
   applies the correct standard at every price automatically. This is also
   the answer to whether a near-certain prop earns flexibility on price: it
   does not, it earns LESS. Break-even at -300 is 75% and at -500 is 83.3%,
   so the edge required to make the same return grows from +3.8 points to
   +4.2 as the price shortens, and a four-point error that leaves a +200 bet
   at +9% ROI turns a -300 bet from +4% into -1.3%.

2. ROBUSTNESS TO OUR OWN ERROR. A season-long empirical rate carries a 95%
   interval about eight points wide. An edge of five points against a number
   that uncertain is noise, not information. So a prop must still be positive
   expectation when evaluated at the PESSIMISTIC end of its own interval.
   This is the test that throws out most of the apparent edge, and it should.

WHY THE OUTPUT IS USUALLY SHORT, AND WHY THAT IS CORRECT.

Prop markets hold 10-15%. Most props are not bettable by anyone, and a screen
that returns twenty plays a night is finding estimation error rather than
value. An empty board is a real answer.
"""
import argparse
import json
import sys
from datetime import datetime

import prop_probability as pp
import odds_fanduel as fd

# Minimum real occurrences before a player's rate for a market is trusted.
MIN_EVENTS = 4


def model_probabilities(prices, min_games=40):
    """Calibrated model probability for every player the book prices."""
    import mlb_sources as src

    # THE PIPELINE'S CALIBRATOR MUST NOT BE APPLIED HERE. It was fitted to map
    # generate_picks.py's OWN predicted probabilities onto outcomes, and that
    # distribution is concentrated between roughly 0.2 and 0.9. Raw empirical
    # game-log rates are a different distribution entirely, and feeding the
    # rare ones through a sigmoid fitted elsewhere produces nonsense:
    #
    #     market      raw     through the calibrator
    #     2+ hits    20.9%          40.8%
    #     3+ hits     4.2%          33.1%
    #     home run   11.2%          18.4%
    #
    # A hitter who records three hits in 4.2% of his games is not a 33% shot,
    # and that single misapplication turned 22 plausible edges into 195 fake
    # ones -- a screen claiming to beat a 10-15% hold market on 38% of its
    # props, which is on its face impossible.
    #
    # Raw empirical rates are used instead. They are backward-looking and take
    # no account of tonight's pitcher or park, which is a real limitation
    # stated plainly rather than papered over with a correction that does not
    # apply. A calibrator FOR these rates would have to be fitted on these
    # rates against outcomes; that is a separate piece of work and until it
    # exists the honest number is the measured one.
    comp = src.batter_pa_composition()
    by_norm = {fd.normalize_name(v.get("name") or ""): (pid, v)
               for pid, v in comp.items() if v.get("name")}
    ids = [by_norm[n][0] for n in prices if n in by_norm]
    emp = src.empirical_batter_prop_rates(ids)
    league = src.league_base_rates()

    out = {}
    for name_n, markets in prices.items():
        hit = by_norm.get(name_n)
        if not hit:
            continue
        pid, meta = hit
        e = emp.get(pid)
        if not e or e.get("games", 0) < min_games:
            continue
        for (stat, needs), american in markets.items():
            r = (e.get("rates") or {}).get(f"{stat}_{needs}plus")
            if not r:
                continue
            # A RATE NEEDS EVENTS BEHIND IT, not just games. For rare markets
            # (4+ RBIs, 5+ total bases) a player often has zero or one
            # occurrence all season, so p_hat is essentially the shrinkage
            # prior wearing a player's name. Comparing that to a +17500 price
            # produced "edges" of +200% -- pure artefact. Requiring a handful
            # of real occurrences is what separates a measured rate from a
            # prior with a decoration.
            if r.get("hit", 0) < MIN_EVENTS:
                continue
            raw = r.get("p_hat", r["p"])
            prob = raw
            prob_lo = r.get("p_lo")
            out[(name_n, stat, needs)] = {
                "player": meta.get("name"), "stat": stat, "needs": needs,
                "american": american, "prob": prob, "prob_lo": prob_lo,
                "raw": raw, "games": e.get("games"),
                "base_rate": (league or {}).get(f"{stat}_{needs}plus"),
            }
    return out


# TIERS, NOT A PASS/FAIL GATE.
#
# A single BET/NO-BET verdict throws away most of what the screen knows. On a
# night when nothing clears every test, "0 bets" is technically honest and
# practically useless -- it hides the ranking that was computed anyway. The
# information is not binary, so the output should not be either.
#
#   A  clears the return floor, survives its own confidence interval, and
#      agrees with the de-vigged market. Everything lines up.
#   B  clears the return floor and the interval, but the market disagrees.
#      The market is usually right, so these are shown with that stated
#      rather than suppressed.
#   C  positive expected return, but the edge sits inside the model's own
#      margin of error. Real candidates, thin evidence.
#   D  negative expected return at the posted price. Shown only on request.
#
# Tiers A and B are bets. C is a watchlist. Nothing here is hidden, and the
# reason each pick sits where it does travels with it.
TIER_NOTE = {
    "A": "model, price and market all agree",
    "B": "priced well by the model, but the market disagrees — size down",
    "C": "positive return, but the edge is inside our margin of error",
    "D": "negative expected return at this price",
}


def screen(entries, min_roi=pp.MIN_ROI, require_robust=True, reject_suspect=True):
    bets, near, rejected = [], [], []
    for k, e in entries.items():
        v = pp.value_verdict(e["prob"], e["american"],
                             prob_lo=e["prob_lo"] if require_robust else None,
                             min_roi=min_roi)
        # THE MARKET IS A SHARPER ESTIMATOR THAN THIS MODEL, and a large
        # disagreement with it is evidence against the model rather than an
        # opportunity. A firm pricing thousands of these with money at risk
        # knows tonight's pitcher, park and lineup slot; a season-long
        # empirical rate does not. Gaps above 7 points against the de-vigged
        # price are therefore rejected, not celebrated -- this is the check
        # that was missing when CJ Abrams' home run showed 23.2% against a
        # de-vigged 10.3% and was nearly presented as a bet.
        agree = pp.market_agreement(e["prob"], e["american"])
        row = {**e, **v, **{"agreement": agree["agreement"],
                            "market_fair": agree["market_fair"],
                            "gap_vs_market": agree["gap"]}}
        clears_roi = v["roi"] >= min_roi
        robust = v.get("robust_to_uncertainty", True)
        if clears_roi and robust and agree["agreement"] != "SUSPECT":
            row["tier"] = "A"
        elif clears_roi and robust:
            row["tier"] = "B"
        elif v["roi"] > 0:
            row["tier"] = "C"
        else:
            row["tier"] = "D"
        row["tier_note"] = TIER_NOTE[row["tier"]]
        (bets if row["tier"] in ("A", "B") else
         near if row["tier"] == "C" else rejected).append(row)
    for lst in (bets, near, rejected):
        lst.sort(key=lambda r: (-{"A": 2, "B": 1}.get(r.get("tier"), 0), -r["roi"]))
    return bets, near, rejected


SHORT = {"hits": "H", "total_bases": "TB", "home_runs": "HR", "runs": "R",
         "rbis": "RBI", "stolen_bases": "SB", "walks": "BB", "singles": "1B",
         "doubles": "2B", "triples": "3B", "hits_runs_rbis": "H+R+RBI"}


def label(stat, needs):
    return f"{needs}+ {SHORT.get(stat, stat)}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-roi", type=float, default=pp.MIN_ROI)
    ap.add_argument("--no-robust", action="store_true",
                    help="skip the confidence-interval test (shows more, trust less)")
    ap.add_argument("--max-price", type=int, default=None,
                    help="ignore anything priced worse than this")
    ap.add_argument("--allow-suspect", action="store_true",
                    help="keep props that disagree wildly with the de-vigged market")
    ap.add_argument("--json", help="write results here")
    args = ap.parse_args()

    print("Fetching live prices from FanDuel...")
    prices = fd.fetch_prop_prices()
    n_markets = sum(len(v) for v in prices.values())
    print(f"  {len(prices)} players, {n_markets} priced markets")
    if args.max_price is not None:
        prices = {n: {k: v for k, v in mk.items() if v >= args.max_price}
                  for n, mk in prices.items()}
        print(f"  {sum(len(v) for v in prices.values())} after the "
              f"{args.max_price:+d} price filter")

    print("Scoring against the model...")
    entries = model_probabilities(prices)
    print(f"  {len(entries)} props have both a price and a usable model read")

    bets, near, rejected = screen(entries, args.min_roi, not args.no_robust,
                                  reject_suspect=not args.allow_suspect)

    print(f"\n{'='*78}")
    print(f"VALUE BOARD — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*78}")

    def show(rows, title, note):
        if not rows:
            return
        print(f"\n{title} ({len(rows)}) — {note}\n")
        print(f"  {'player':22s}{'prop':11s}{'price':>7s}{'model':>8s}{'mkt':>7s}"
              f"{'ROI':>8s}{'kelly':>7s}  n")
        for b in rows:
            print(f"  {b['player'][:22]:22s}{label(b['stat'],b['needs']):11s}"
                  f"{b['american']:+7d}{b['prob']*100:7.1f}%"
                  f"{b['market_fair']*100:6.1f}%{b['roi']*100:+7.1f}%"
                  f"{b['kelly']*100:6.1f}%  {b.get('games','?')}")

    a = [b for b in bets if b["tier"] == "A"]
    bb = [b for b in bets if b["tier"] == "B"]
    show(a, "TIER A", TIER_NOTE["A"])
    show(bb, "TIER B", TIER_NOTE["B"])
    show(near[:12], "TIER C", TIER_NOTE["C"] + " — watchlist, size small")
    if not a and not bb:
        print("\n  No tier A or B tonight. Tier C is what the model likes on price")
        print("  where the evidence is thinner than we would want.")

    # Home runs get their own section on request: they are the market with the
    # widest prices, so a small probability error moves ROI a long way, and
    # they deserve to be read with their sample size in view.
    hrs = sorted((r for r in (bets + near) if r["stat"] == "home_runs"),
                 key=lambda r: -r["roi"])
    if hrs:
        print(f"\n{'-'*78}\nHOME RUNS — best available, all tiers\n")
        print(f"  {'player':22s}{'line':8s}{'price':>7s}{'model':>8s}{'mkt':>7s}"
              f"{'ROI':>8s}  tier  games")
        for h in hrs[:10]:
            print(f"  {h['player'][:22]:22s}{label(h['stat'],h['needs']):8s}"
                  f"{h['american']:+7d}{h['prob']*100:7.1f}%{h['market_fair']*100:6.1f}%"
                  f"{h['roi']*100:+7.1f}%   {h['tier']}    {h.get('games','?')}")

    print(f"\nScreened {len(entries)} priced props: {len(a)} tier A, {len(bb)} tier B, "
          f"{len(near)} tier C, {len(rejected)} negative.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"generated": datetime.now().isoformat(),
                       "bets": bets, "near": near}, f, indent=2, default=str)
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
