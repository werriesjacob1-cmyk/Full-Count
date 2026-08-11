#!/usr/bin/env python3
"""test_render_full_board.py — checks render_full_board.py's HTML output
against a synthetic pool, focused on the exact bug class already found and
fixed in render_board.py/render_parlay.py (HTML being double-escaped into
literal text) plus the filter/sort behavior the whole page exists for.

    /tmp/mlbvenv/bin/python3 test_render_full_board.py
    python3 test_render_full_board.py -v
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import render_full_board as rfb

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


def cand(name, prop, stat, hit_probability, score, **kw):
    return {
        "name": name, "team": kw.get("team", "Mets"), "matchup": kw.get("matchup", "Mets @ Pirates"),
        "game_pk": kw.get("game_pk", 1), "prop": prop, "projection": {"stat": stat},
        "hit_probability": hit_probability, "score": score,
        "lift": kw.get("lift"), "sample_n": kw.get("sample_n"), "reliability": kw.get("reliability"),
    }


head("1. no HTML-escaping leaks -- the exact bug class already found twice this session")

pool = [
    cand("Low Conf", "Over 0.5 Hits", "hits", 0.55, 50),
    cand("High Conf", "Over 3.5 Strikeouts", "strikeouts", 0.70, 75),
    cand("Mid A", "Over 0.5 Hits", "hits", 0.62, 60, lift=0.05, sample_n=80, reliability="A"),
    cand("Mid B", "Over 0.5 Hits", "hits", 0.65, 62),
]
out = rfb.render(pool, "2026-08-11")
check("&lt;span" not in out and "&quot;" not in out, "no double-escaped HTML in visible text", out[:200])
check(out.count("<div") == out.count("</div>"), "div tags balance")
check(out.count("<article") == out.count("</article>"), "article tags balance")
check(out.count("<button") == out.count("</button>"), "button tags balance")

head("2. filtering -- each prop-type tab/panel exists and is sorted high to low")

check('data-tab="hits"' in out and 'data-tab="strikeouts"' in out, "a tab exists per real prop type present")
check('data-tab="all"' in out, "an All tab exists")

# Extract the hits panel and confirm descending order: Mid B (0.65), Mid A (0.62), Low Conf (0.55)
import re
panel = re.search(r'data-panel="hits".*?</div>\s*</div>', out, re.S)
check(panel is not None, "the hits panel is present in the output")
if panel:
    names_in_order = re.findall(r'class="player">([^<]+)<', panel.group(0))
    check(names_in_order == ["Mid B", "Mid A", "Low Conf"],
          "candidates within the hits filter are ordered highest probability first",
          f"got {names_in_order}")

head("3. confidence bucketing matches the rest of the codebase's own convention")

check(rfb._confidence(75) == "High" and rfb._confidence(60) == "Medium" and rfb._confidence(40) == "Low",
      "score >=70/>=55/else maps to High/Medium/Low, same thresholds every scorer in generate_picks.py uses")
check(rfb._confidence(None) == "Low", "a missing score degrades to Low rather than crashing")

head("4. candidates with no real hit_probability are excluded, not shown as blank cards")

pool_with_gap = pool + [cand("No Prob", "Over 0.5 Hits", "hits", None, 50)]
out2 = rfb.render(pool_with_gap, "2026-08-11")
check("No Prob" not in out2, "a candidate with hit_probability=None never renders a card")

head("5. an empty pool renders without crashing")

empty_out = rfb.render([], "2026-08-11")
check("0 candidates" in empty_out or "0 </span>" in empty_out or 'data-tab="all"' in empty_out,
      "an empty pool still renders a valid page (All tab present, zero count)")
check("&lt;" not in empty_out, "no escaping leaks in the empty-pool path")

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
