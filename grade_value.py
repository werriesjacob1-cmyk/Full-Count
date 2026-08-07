#!/usr/bin/env python3
"""
grade_value.py — settles the value screen against real results and real prices.

THE QUESTION THIS ANSWERS.

The value screen claims certain props are mispriced. That claim is testable
in exactly one way: take the props it flagged, at the prices that were
actually available, look up what happened, and add up the money. Hit rate is
not the measure here -- a screen betting +800 longshots should lose most of
its bets and still make money, while one betting -300 favourites can win 70%
and lose steadily. Only return on stake settles it.

WHY THIS IS A FORWARD TEST AND NOT A BACKTEST.

Prices cannot be backfilled. Everything else in this project was validated by
replaying history, but nothing publishes what a prop cost last Tuesday, so
the screen can only be judged on prices captured live by prop_snapshot.py.
That capture began 2026-08-06. Any verdict before a few hundred settled bets
have accumulated is noise, and this tool says so rather than printing an
encouraging number.

WHAT COUNTS AS THE PRICE.

The last pregame snapshot before first pitch -- the closing price. It is the
market's final and sharpest word, and it is the number a bettor placing a bet
shortly before the game would actually have got. Using an earlier, softer
price would flatter the screen by crediting it with edge that the market
subsequently removed.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import requests

import prop_probability as pp

PROPS_DIR = os.environ.get("PROPS_DIR", "data/props")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
UA = {"User-Agent": "Mozilla/5.0"}
STATS_API = "https://statsapi.mlb.com/api/v1"


def closing_prices(date):
    """Last PREGAME price for every prop on a date, keyed by (player, stat, needs)."""
    path = os.path.join(PROPS_DIR, f"props_{date}.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    best = {}
    for snap in sorted(payload.get("snapshots", []), key=lambda s: s.get("taken_at", "")):
        # A sweep that ran out of its time budget covers only part of the
        # slate (see prop_snapshot.SWEEP_BUDGET_S). It is still worth keeping
        # — a partial capture beats none — but the games it never reached keep
        # whatever price an earlier sweep left, so their "closing" price is
        # really a stale one. Recorded on the row so a settled bet can be
        # traced back to how fresh its price actually was.
        cov = snap.get("coverage") or {}
        partial = cov.get("complete") is False
        for r in snap.get("rows", []):
            if r.get("in_play"):
                continue  # in-play is a different bet from the one screened
            key = (r["player_norm"], r["stat"], r["needs"])
            # Later pregame snapshots overwrite earlier ones, so what remains
            # after the sweep is the closing number.
            best[key] = {"american": r["american"], "player": r["player"],
                         "game": r.get("game"), "taken_at": snap.get("taken_at"),
                         "from_partial_sweep": partial}
    return best


def actual_results(date, player_names):
    """What each player actually did that day, from MLB game logs."""
    import mlb_sources as src
    comp = src.batter_pa_composition()
    import odds_fanduel as fd
    by_norm = {fd.normalize_name(v.get("name") or ""): pid
               for pid, v in comp.items() if v.get("name")}
    out = {}
    for norm in player_names:
        pid = by_norm.get(norm)
        if not pid:
            continue
        try:
            r = requests.get(f"{STATS_API}/people/{pid}/stats",
                             params={"stats": "gameLog", "group": "hitting",
                                     "season": date[:4], "sportId": 1},
                             headers=UA, timeout=25)
            splits = [s for s in r.json().get("stats", [{}])[0].get("splits", [])
                      if s.get("date") == date]
        except Exception:
            continue
        if not splits:
            continue  # did not play: no bet settles, rather than a loss
        st = splits[0].get("stat") or {}
        out[norm] = {"hits": int(st.get("hits") or 0),
                     "total_bases": int(st.get("totalBases") or 0),
                     "home_runs": int(st.get("homeRuns") or 0),
                     "pa": int(st.get("plateAppearances") or 0)}
    return out


def board_reads(date):
    """The model's reads for a date, as they were recorded THAT DAY.

    THE BUG THIS REPLACES. settle() used to call value_board.model_probabilities,
    which calls generate_picks.score_slate() — and score_slate scores the
    CURRENT slate. Settling 2026-08-06 therefore took today's lineups, today's
    starting pitchers, today's weather and today's batting orders, matched them
    to that past date's prices, and graded them against that past date's box
    scores. For a player who was not even in a game today it produced a read
    from nothing at all.

    That would not have failed loudly. It would have produced a plausible ROI
    number that meant nothing, which is the worst possible outcome for the one
    tool whose job is to say whether the screen works.

    It was also unbounded work: one full pipeline scoring per date settled, so
    the daily job's cost grew with every day of captured prices.

    Reads are now taken from output/value_board_DATE.json, written by the daily
    run at the time the board was actually made. No file means no settlement —
    a date is skipped rather than invented.
    """
    path = os.path.join(os.environ.get("OUTPUT_DIR", "output"),
                        f"value_board_{date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    out = {}
    for row in payload.get("entries") or []:
        norm, stat, needs = row.get("player_norm"), row.get("stat"), row.get("needs")
        if norm and stat and needs is not None and row.get("prob") is not None:
            out[(norm, stat, needs)] = row
    return out or None


def settle(date, min_roi=pp.MIN_ROI):
    """Grade every prop the screen would have flagged on this date."""
    import value_board as vb
    prices = closing_prices(date)
    if not prices:
        return None
    reads = board_reads(date)
    if not reads:
        return {"date": date, "no_reads": True}
    # Re-screen the day's OWN reads against the closing price. The price is
    # the market's final word and the number a bettor would really have got;
    # the read has to be the one that was actually made at the time.
    entries = {}
    for key, info in prices.items():
        r = reads.get(key)
        if r:
            entries[key] = {**r, "american": info["american"]}
    if not entries:
        return {"date": date, "no_reads": True}
    bets, near, _ = vb.screen(entries, min_roi, require_robust=True)

    results = actual_results(date, {b["player"] and vb.fd.normalize_name(b["player"])
                                    for b in bets})
    settled, staked, returned = [], 0.0, 0.0
    for b in bets:
        norm = vb.fd.normalize_name(b["player"])
        act = results.get(norm)
        if not act:
            continue  # did not play; no action
        won = act.get(b["stat"], 0) >= b["needs"]
        d = pp.decimal_odds(b["american"])
        staked += 1.0
        returned += d if won else 0.0
        settled.append({**b, "actual": act.get(b["stat"]), "won": won})
    return {"date": date, "flagged": len(bets), "settled": len(settled),
            "staked": staked, "returned": returned,
            "roi": (returned - staked) / staked if staked else None,
            "hit_rate": (sum(1 for s in settled if s["won"]) / len(settled)
                         if settled else None),
            "bets": settled}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="settle one date (default: every captured date)")
    ap.add_argument("--min-roi", type=float, default=pp.MIN_ROI)
    args = ap.parse_args()

    dates = ([args.date] if args.date else
             sorted(os.path.basename(p)[6:-5]
                    for p in glob.glob(os.path.join(PROPS_DIR, "props_*.json"))))
    if not dates:
        print(f"No captured prices in {PROPS_DIR}. Capture begins when "
              f"prop_snapshot.py first runs — there is nothing to settle yet.")
        return 0

    all_bets, total_staked, total_returned = [], 0.0, 0.0
    print(f"{'date':12s}{'flagged':>9s}{'settled':>9s}{'hit':>8s}{'ROI':>9s}")
    for d in dates:
        r = settle(d, args.min_roi)
        if r and r.get("no_reads"):
            # Prices were captured but no board was persisted that day, so
            # there is no point-in-time read to settle. Named explicitly
            # rather than shown as a dash, because it is a gap in OUR record
            # and not a quiet day in the market.
            print(f"{d:12s}{'no board':>9s}{'0':>9s}{'—':>8s}{'—':>9s}")
            continue
        if not r or not r["settled"]:
            print(f"{d:12s}{'—':>9s}{'0':>9s}{'—':>8s}{'—':>9s}")
            continue
        total_staked += r["staked"]; total_returned += r["returned"]
        all_bets += r["bets"]
        print(f"{d:12s}{r['flagged']:9d}{r['settled']:9d}"
              f"{r['hit_rate']*100:7.1f}%{r['roi']*100:+8.1f}%")

    print()
    if not all_bets:
        print("Nothing has settled yet. The screen cannot be judged until real")
        print("bets at real captured prices have resolved.")
        return 0

    roi = (total_returned - total_staked) / total_staked
    hits = sum(1 for b in all_bets if b["won"])
    print(f"OVERALL: {len(all_bets)} bets, {hits} won ({hits/len(all_bets)*100:.1f}%), "
          f"ROI {roi*100:+.1f}%")
    # Honest sample-size guidance. Prop edges are small and variance is large;
    # a few dozen bets cannot distinguish a real edge from a lucky week.
    if len(all_bets) < 200:
        print(f"\n  NOT YET MEANINGFUL. {len(all_bets)} settled bets is far too few to")
        print(f"  separate edge from variance -- a 5% edge needs several hundred")
        print(f"  bets before its confidence interval excludes zero. Treat this")
        print(f"  number as a progress indicator, not a verdict.")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, "value_screen_record.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().isoformat(), "n_bets": len(all_bets),
                   "hit_rate": hits / len(all_bets), "roi": roi,
                   "staked": total_staked, "returned": total_returned}, f, indent=2)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
