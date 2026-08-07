#!/usr/bin/env python3
"""test_render_board.py — checks render_board.py's HTML output against
synthetic picks payloads, focused on the exact bug class already found and
fixed by hand once in this file (HTML being double-escaped into literal
text) plus the sorting/grouping behavior the board is actually FOR.

    /tmp/mlbvenv/bin/python3 test_render_board.py
    python3 test_render_board.py -v
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import render_board as rb

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


def pick(name, prop, stat, hit_probability, confidence, category=None, **kw):
    p = {
        "name": name, "team": kw.get("team", "Mets"), "matchup": kw.get("matchup", "Mets @ Pirates"),
        "game_pk": kw.get("game_pk", 1), "prop": prop, "projection": {"stat": stat},
        "hit_probability": hit_probability, "confidence": confidence,
        "score": kw.get("score", 60.0), "notable_signals": kw.get("notable_signals", 1),
        "category": category, "sample_n": kw.get("sample_n"), "reliability": kw.get("reliability"),
        "lift": kw.get("lift"), "market_odds": kw.get("market_odds"),
        "market_implied": kw.get("market_implied"), "price_clears": kw.get("price_clears"),
        "estimated_odds": kw.get("estimated_odds"),
        "why": kw.get("why"), "watchouts": kw.get("watchouts"),
    }
    return p


head("1. no HTML-escaping leaks -- the exact bug class already found once here")

priced = pick("Kevin McGonigle", "Over 0.5 Hits+Runs+RBIs", "hits_runs_rbis", 0.82, "High",
             market_odds=-425, market_implied=0.81, price_clears=False, lift=0.148, sample_n=111,
             reliability="A", why=["Projected 4.5 PA, favorable platoon"], watchouts=["thin BvP sample"])
card = rb._card(priced)
check("&lt;span" not in card and "&quot;" not in card,
      "the market-price badge's own HTML renders as a real tag, not escaped literal text",
      card)
check(card.count("<span") == card.count("</span>"), "span tags balance in a single card")

head("2. sorting -- best pick in every category ranked high to low confidence")

payload = {
    "generated": "2026-08-07T18:29:43.969351",
    "picks": [
        pick("Low Conf", "Over 0.5 Hits", "hits", 0.55, "Low", category="best_of_category"),
        pick("High Conf", "Over 0.5 Strikeouts", "strikeouts", 0.70, "High", category="best_of_category"),
        pick("Mid Conf", "Over 0.5 Walks", "walks", 0.62, "Medium", category="best_of_category"),
        pick("Board 2nd", "Over 0.5 Hits", "hits", 0.65, "Medium"),
        pick("Board 1st", "Over 0.5 Hits", "hits", 0.75, "High"),
        pick("Moon B", "Home Run", "home_runs", 0.20, "Low", category="moonshot"),
        pick("Moon A", "Home Run", "home_runs", 0.35, "Medium", category="moonshot"),
    ],
}
out = rb.render(payload, "2026-08-07")

cat_order = [m for m in ("Hits", "Strikeouts", "Walks") if m in out]
positions = [out.index(f">{name}<") for name in ("High Conf", "Mid Conf", "Low Conf")]
check(positions == sorted(positions),
      "category cards themselves are ordered by their pick's confidence, highest first",
      f"positions={positions}")

board_pos = [out.index(f">{name}<") for name in ("Board 1st", "Board 2nd")]
check(board_pos == sorted(board_pos), "top board is ordered highest hit_probability first")

moon_pos = [out.index(f">{name}<") for name in ("Moon A", "Moon B")]
check(moon_pos == sorted(moon_pos), "moonshots are ordered highest hit_probability first")

head("3. why/watchouts vs the fallback")

with_reason = pick("Has Reason", "Over 0.5 Hits", "hits", 0.7, "High",
                   why=["real reasoning here"], notable_signals=3)
without_reason = pick("No Reason", "Over 0.5 Hits", "hits", 0.7, "High",
                     why=None, watchouts=None, notable_signals=3)
check("real reasoning here" in rb._card(with_reason), "real why-text renders when present")
check("converging signal" in rb._card(without_reason) and "real reasoning here" not in rb._card(without_reason),
      "falls back to a signal count when why/watchouts are absent (older file), not silence")

no_signals_either = pick("Nothing", "Over 0.5 Hits", "hits", 0.7, "High",
                        why=None, watchouts=None, notable_signals=0)
check("<details" not in rb._card(no_signals_either),
      "no fabricated reasoning block when there is genuinely nothing to show")

head("4. graceful handling of empty/missing sections")

empty_payload = {"generated": "2026-08-07T18:00:00", "picks": []}
empty_out = rb.render(empty_payload, "2026-08-07")
check("No picks yet" in empty_out or "empty" in empty_out,
      "an empty board says so rather than rendering a blank page")
check("&lt;" not in empty_out, "no escaping leaks in the empty-state path")

head("5. unknown stat falls back to a readable label instead of a raw key")

unknown_payload = {
    "generated": "2026-08-07T18:00:00",
    "picks": [pick("X", "Some New Prop", "brand_new_stat_key", 0.6, "Medium", category="best_of_category")],
}
unknown_out = rb.render(unknown_payload, "2026-08-07")
check("brand_new_stat_key" in unknown_out,
      "an unrecognised stat key (e.g. a newly added family not yet in CATEGORY_LABELS) "
      "still renders its own name rather than crashing or vanishing silently")

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
