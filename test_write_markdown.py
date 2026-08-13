#!/usr/bin/env python3
"""test_write_markdown.py — smoke/structural coverage for generate_picks.
write_markdown(), the human-facing board renderer. Had zero test coverage.
Does not re-verify every line of formatting (that would duplicate the
function); checks that it never crashes on realistic/edge-case inputs and
that each of its five sections (top10, best-in-market, skipped, moonshots,
best-of-category) appears exactly when its input data says it should,
with the specific real invariants called out in its own comments: the
"Best in each market" section excludes anything already shown in top10,
and "Best of Every Category" shows an explicit placeholder row for a
missing category rather than silently omitting it.

    /tmp/mlbvenv/bin/python3 test_write_markdown.py
"""
import sys
import os
import tempfile
import shutil

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

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


import generate_picks as gp

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_write_markdown_")
gp.PICKS_FILE = os.path.join(TMPDIR, "picks_test.md")


def read():
    with open(gp.PICKS_FILE, encoding="utf-8") as f:
        return f.read()


def cand(name="Player", prop="Over 1.5 Total Bases", hit_probability=0.68, score=72.0,
        stat="total_bases", **over):
    c = {"name": name, "team": "Athletics", "matchup": "Athletics @ Astros",
         "prop": prop, "hit_probability": hit_probability, "score": score,
         "confidence": "Medium", "notable_signals": 1, "why": ["a real reason"],
         "watchouts": [], "projection": {"stat": stat, "value": 1.5, "needs": 2},
         "base_rate": 0.55, "lift": 0.13, "alternatives": []}
    c.update(over)
    return c


head("1. an entirely empty board doesn't crash and writes a real file")

gp.write_markdown([], [], [{"matchup": "Athletics @ Astros"}] * 15, {})
check(os.path.exists(gp.PICKS_FILE), "an all-empty call still writes a real file")
content0 = read()
check("No candidate genuinely cleared the market's price" in content0,
      "the empty-top10 case explicitly explains why, not a silently blank board")

head("2. a real top10 candidate renders its core fields")

top10 = [cand(name="Julio Rodriguez", prop="Over 1.5 Total Bases", hit_probability=0.72, score=78.5)]
gp.write_markdown(top10, [], [{"matchup": "Athletics @ Astros"}] * 15, {})
content = read()
check("Julio Rodriguez" in content and "72%" in content,
      "the candidate's name and rounded probability both render", f"got content: {content!r}")
check("78.5/100" in content, "the quality score renders")

head("3. a candidate with hit_probability=None renders the honest 'not priced' fallback, "
     "never a fabricated percentage")

unpriced = [cand(name="Unpriced Guy", hit_probability=None)]
gp.write_markdown(unpriced, [], [{"matchup": "Athletics @ Astros"}] * 15, {})
content2 = read()
check("not priced" in content2, "an unpriced candidate's line says so explicitly")

head("4. 'Best in each market' EXCLUDES anything already shown in top10 -- THE INVARIANT "
     "this section's own comment describes as its whole reason for existing")

shown_pick = cand(name="Already Shown", stat="hits", hit_probability=0.75)
other_pick_same_market = cand(name="Also Hits", stat="hits", hit_probability=0.65)
gp.write_markdown([shown_pick], [], [{"matchup": "Athletics @ Astros"}] * 15, {},
                  all_ranked=[shown_pick, other_pick_same_market])
content3 = read()
best_market_section = content3[content3.find("Best in each market"):] if "Best in each market" in content3 else ""
check("Also Hits" in best_market_section,
      "a different hits candidate not already on top10 appears in Best in each market")
check("Already Shown" not in best_market_section,
      "the SAME candidate already shown on top10 is excluded from Best in each market, "
      "even though it's the single best in its own market", f"got section: {best_market_section!r}")

head("5. skipped candidates render with a real reason, and a fallback reason when watchouts "
     "is empty")

skipped_with_reason = cand(name="Skipped With Reason", watchouts=["bad matchup context"])
skipped_no_reason = cand(name="Skipped No Reason", watchouts=[])
gp.write_markdown([], [skipped_with_reason, skipped_no_reason],
                  [{"matchup": "Athletics @ Astros"}] * 15, {})
content4 = read()
check("bad matchup context" in content4, "a skipped candidate's real watchout text renders")
check("did not converge" in content4,
      "a skipped candidate with NO watchouts gets the honest fallback explanation, not "
      "a blank or fabricated reason")

head("6. moonshots render with real odds when priced, and 'unpriced' honestly otherwise")

moonshot_priced = cand(name="Priced Moonshot", hit_probability=0.18, market_odds=450)
moonshot_unpriced = cand(name="Unpriced Moonshot", hit_probability=0.15, market_odds=None)
gp.write_markdown([], [], [{"matchup": "Athletics @ Astros"}] * 15, {},
                  moonshots=[moonshot_priced, moonshot_unpriced])
content5 = read()
check("+450" in content5, "a priced moonshot's real FanDuel odds render")
check("unpriced" in content5, "an unpriced moonshot honestly says so, not a fabricated price")

head("7. by_category renders an explicit placeholder for a category with no real candidate "
     "tonight, not a silently missing row -- and flags sub-floor entries with the warning icon")

below_floor = cand(name="Below Floor Pick", hit_probability=0.45, clears_main_board_floor=False)
by_category = {"hits": [below_floor]}  # every OTHER CATEGORY_LABELS key has no entry
gp.write_markdown([], [], [{"matchup": "Athletics @ Astros"}] * 15, {}, by_category=by_category)
content6 = read()
check("no candidate tonight" in content6,
      "a category with no real entry gets an explicit '_no candidate tonight_' row, not "
      "a missing/blank line")
check("Below Floor Pick" in content6 and "⚠" in content6,
      "a below-floor category entry is shown WITH the warning flag, not hidden or "
      "silently unflagged", f"got: {content6[content6.find('Below Floor'):content6.find('Below Floor')+120]!r}")

head("8. a candidate carrying real alternatives renders them in the 'other lines' note")

with_alts = cand(name="Multi Line Player", stat="strikeouts",
                 alternatives=[{"label": "Over 5.5 Strikeouts", "prob": 0.55}])
gp.write_markdown([with_alts], [], [{"matchup": "Athletics @ Astros"}] * 15, {})
content7 = read()
check("Other lines on this player" in content7,
      "a candidate with real alternatives gets the 'other lines' note rendered")

shutil.rmtree(TMPDIR, ignore_errors=True)

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
