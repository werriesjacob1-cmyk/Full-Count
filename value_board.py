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


def model_probabilities(prices, min_games=40, use_pipeline=True):
    """Model probability for every player the book prices.

    TWO PATHS, AND THE DEFAULT ONE IS THE PIPELINE.

    This screen originally read season game-log rates directly. That made it
    blind to everything the daily pipeline already knows about tonight -- who
    is pitching, the park, the weather, the batting-order slot, the bullpen --
    and it is why every disagreement with a price looked like the market
    knowing something we did not. It was not a data gap; it was this module
    ignoring data the system already had.

    The pipeline path scores each batter through generate_picks.score_batter,
    which consumes the opposing starter, his handedness and arsenal, park and
    weather, projected plate appearances from the lineup slot and implied team
    total, bullpen quality and fatigue -- then calibrates. That is the number
    to compare against a price.

    The season-rate path remains as a fallback for players the pipeline could
    not score (not in a confirmed lineup, no game context), because a
    backward-looking rate beats no read at all. Which path produced each
    number is recorded on every row rather than blended silently away.
    """
    import mlb_sources as src_mod

    entries = {}
    pipeline_probs = {}
    if use_pipeline:
        try:
            import generate_picks as gp
            cands, _ctx = gp.score_slate()
            for c in cands:
                nm = fd.normalize_name(c.get("name") or "")
                p = c.get("hit_probability")
                proj = c.get("projection") or {}
                if nm and p is not None and proj.get("stat"):
                    # Keyed by the exact market so a 2+ hits price is never
                    # compared against a 1+ hits probability.
                    pipeline_probs[(nm, proj["stat"], proj.get("needs"))] = {
                        "prob": p, "ci": c.get("prob_ci"),
                        "games": c.get("sample_n"), "source": "pipeline",
                    }
        except Exception as e:
            print(f"  (pipeline scoring unavailable: {e} — falling back to season rates)")

    # Exit-velocity markets live in Statcast, not in game logs, so they are
    # fetched separately and merged into the same rate structure.
    hard_hit = {}
    if any(stat.startswith("hard_hit") for mk in prices.values() for stat, _ in mk):
        try:
            hard_hit = src_mod.hard_hit_game_rates()
        except Exception as e:
            print(f"  (hard-hit rates unavailable: {e})")

    comp = src_mod.batter_pa_composition()
    by_norm = {fd.normalize_name(v.get("name") or ""): (pid, v)
               for pid, v in comp.items() if v.get("name")}
    ids = [by_norm[n][0] for n in prices if n in by_norm]
    emp = src_mod.empirical_batter_prop_rates(ids)
    league = src_mod.league_base_rates()

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
            if not r and stat.startswith("hard_hit"):
                r = ((hard_hit.get(pid) or {}).get("rates") or {}).get(f"{stat}_{needs}plus")
            if not r:
                continue
            # A rate needs EVENTS behind it, not just games. On rare markets a
            # player often has zero or one occurrence all season, so the shrunk
            # rate is the prior wearing a player's name -- and against a +17500
            # price that produced apparent edges above +200%.
            if r.get("hit", 0) < MIN_EVENTS:
                continue
            season_p = r.get("p_hat", r["p"])
            pl = pipeline_probs.get((name_n, stat, needs))
            prob = pl["prob"] if pl else season_p
            source = "pipeline" if pl else "season-rate"
            entries[(name_n, stat, needs)] = {
                "player": meta.get("name"), "stat": stat, "needs": needs,
                "american": american, "prob": prob,
                "prob_lo": r.get("p_lo"), "season_prob": season_p,
                "source": source, "games": e.get("games"),
                "base_rate": (league or {}).get(f"{stat}_{needs}plus"),
            }
    n_pipe = sum(1 for v in entries.values() if v["source"] == "pipeline")
    if entries:
        print(f"  {n_pipe}/{len(entries)} scored with full game context "
              f"(opposing SP, park, lineup slot); the rest on season rates")
    return entries


# TIERS, NOT A PASS/FAIL GATE.
#
# A single BET/NO-BET verdict throws away most of what the screen knows. On a
# night when nothing clears every test, "0 bets" is technically honest and
# practically useless -- it hides a ranking that was computed anyway. The
# information is not binary, so the output should not be either.
TIER_NOTE = {
    "A": "model, price and market all agree",
    "B": "priced acceptably by the model, but the market disagrees — size down",
    "C": "positive return, but the edge is inside our margin of error",
    "D": "price is worse than the tolerance allows",
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
         "doubles": "2B", "triples": "3B", "hits_runs_rbis": "H+R+RBI",
         "hard_hit_110": "110+mph", "hard_hit_105": "105+mph"}


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
    ap.add_argument("--mode", choices=("value", "reasonable"), default="reasonable",
                    help="'value' demands positive expected return (beat the book). "
                         "'reasonable' (default) demands a real read at a price that "
                         "is not offensive — achievable, and does NOT claim +EV.")
    ap.add_argument("--max-tax", type=float, default=pp.MAX_ACCEPTABLE_TAX,
                    help="worst return accepted in reasonable mode (default %(default)s)")
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

    if args.mode == "reasonable":
        # A read the model actually likes, at a price that is not the reason
        # to decline. Explicitly not a claim of positive expectation.
        min_roi_used = args.max_tax
    else:
        min_roi_used = args.min_roi
    bets, near, rejected = screen(entries, min_roi_used, not args.no_robust,
                                  reject_suspect=not args.allow_suspect)
    if args.mode == "reasonable":
        print(f"\n  MODE: reasonable — a real read at a price no worse than "
              f"{args.max_tax*100:.0f}% return.")
        print(f"  This does NOT claim positive expectation. Prop markets hold "
              f"6-15%; beating that")
        print(f"  consistently is a different and much harder claim. These are "
              f"strong reads whose")
        print(f"  price is not the reason to pass.")

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
            # EVERY SCREENED PROP, not just the ones that cleared.
            #
            # grade_value.py settles this screen forward, and to do that it
            # needs the model's read AS IT WAS ON THE DAY. It used to re-derive
            # those reads by scoring the CURRENT slate, which meant settling a
            # past date against today's lineups, starters and weather — for
            # players who may not even have been playing. Persisting the reads
            # here is what makes an honest settlement possible.
            #
            # Rejected rows are included because settlement re-screens at
            # CLOSING prices: a prop that missed at the generation price can
            # clear at the close, and dropping it would quietly bias the record
            # toward whatever the earlier price happened to favour.
            json.dump({"generated": datetime.now().isoformat(),
                       "bets": bets, "near": near,
                       "entries": [{**v, "player_norm": k[0]}
                                   for k, v in entries.items()]},
                      f, indent=2, default=str)
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
