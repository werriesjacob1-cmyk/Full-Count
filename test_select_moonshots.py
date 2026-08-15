#!/usr/bin/env python3
"""test_select_moonshots.py — coverage for generate_picks.select_
moonshots(), the "Moonshots" (home run) board category. Had zero test
coverage despite its own docstring documenting WHY this category has to
exist as a separate pool: home runs never win a batter's own _pick_line
selection (hits/total_bases are always more likely), so without this
function real HR value would be computed and then silently thrown away
every single night.

    /tmp/mlbvenv/bin/python3 test_select_moonshots.py
"""
import sys

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
import odds_fanduel as fd


def batter_c(name="Slugger", score=70, hr_prob=0.18, base_rate=0.10, player_id=5, **over):
    c = {
        "type": "batter", "name": name, "player_id": player_id, "team": "Athletics",
        "matchup": "Athletics @ Astros", "game_pk": 900001, "score": score,
        "confidence": "Medium", "notable_signals": 1, "signals": {},
        "line_options": [
            {"stat": "home_runs", "needs": 1, "prob": hr_prob, "base_rate": base_rate,
             "lift": round(hr_prob - base_rate, 4), "basis": "modelled",
             "empirical": None, "modelled": hr_prob},
            {"stat": "hits", "needs": 1, "prob": 0.68, "base_rate": 0.60, "lift": 0.08,
             "basis": "empirical_shrunk"},
        ],
    }
    c.update(over)
    return c


head("1. a batter with no line_options at all is skipped, not a crash")

check(gp.select_moonshots([{"type": "batter", "name": "No Options", "score": 70}], {}, fd) == [],
      "a batter candidate with no line_options key returns nothing for that player")

head("2. a pitcher candidate is never eligible, regardless of its own fields")

pitcher_c = {"type": "pitcher", "name": "Some SP", "score": 90, "line_options": [
    {"stat": "home_runs", "needs": 1, "prob": 0.5, "base_rate": 0.1}]}
check(gp.select_moonshots([pitcher_c], {}, fd) == [],
      "only type='batter' candidates are ever considered")

head("3. a batter whose line_options has no home_runs/needs=1 entry is skipped")

no_hr = batter_c()
del no_hr["line_options"][0]
check(gp.select_moonshots([no_hr], {}, fd) == [],
      "a batter with line_options present but no home_runs needs=1 entry produces nothing")

head("4. MIN_QUALITY_SCORE still gates moonshots -- deliberately, per the docstring "
     "('only the quality gate still applies')")

below_floor = batter_c(score=gp.MIN_QUALITY_SCORE - 1)
check(gp.select_moonshots([below_floor], {}, fd) == [],
      "a batter scoring just under MIN_QUALITY_SCORE is excluded even with a real HR read")

at_floor = batter_c(score=gp.MIN_QUALITY_SCORE)
check(len(gp.select_moonshots([at_floor], {}, fd)) == 1,
      "a batter scoring exactly at MIN_QUALITY_SCORE is included")

head("5. a well-formed moonshot candidate carries the real HR probability, not the "
     "batter's OWN chosen (hits/TB) score basis")

out = gp.select_moonshots([batter_c(hr_prob=0.22, base_rate=0.11)], {}, fd)
check(len(out) == 1, "one qualifying batter produces one moonshot entry")
c = out[0]
check(c["prop"] == "Home Run" and c["projection"] == {"stat": "home_runs", "value": 1, "needs": 1},
      "the moonshot entry is always labelled as a Home Run prop, regardless of the "
      "batter's own main-board prop", f"got {c['prop']} {c['projection']}")
check(c["hit_probability"] == 0.22, "hit_probability is the HR line's own probability (22%), "
      "not the batter's hits/TB probability (68%)")
check(c["category"] == "moonshot", "category is tagged 'moonshot'")

head("6. MIN_LINE_PROB is deliberately NOT applied -- a real, low HR probability still ships")

low_hr = batter_c(hr_prob=0.14, base_rate=0.11)  # 14%, nowhere near MIN_LINE_PROB=0.60
out = gp.select_moonshots([low_hr], {}, fd)
check(len(out) == 1 and out[0]["hit_probability"] == 0.14,
      "a 14% HR probability (would never clear the main board's 60% floor) still produces "
      "a moonshot entry, unfiltered by that floor -- this category exists specifically "
      "to bypass it", f"got {out}")

head("7. real market price lookup via fd.normalize_name, and price_clears computed against it")

prices = {fd.normalize_name("Aaron Judge Jr."): {("home_runs", 1): 350}}
c_priced = batter_c(name="Aaron Judge Jr.", hr_prob=0.24, base_rate=0.11)
out = gp.select_moonshots([c_priced], prices, fd)
check(out[0]["market_odds"] == 350, "market odds are found via normalize_name matching "
      "('Aaron Judge Jr.' -> the suffix-stripped key)", f"got {out[0]['market_odds']}")
check(out[0]["market_implied"] is not None, "market_implied is computed from the real odds")

c_unpriced = batter_c(name="Nobody Priced", hr_prob=0.24, base_rate=0.11)
out2 = gp.select_moonshots([c_unpriced], prices, fd)
check(out2[0]["market_odds"] is None and out2[0]["market_implied"] is None,
      "a batter with no matching FanDuel price gets market_odds=None honestly, not a "
      "fabricated number")

head("8. ranked by hit_probability descending, and truncated to n")

batters = [batter_c(name=f"B{i}", player_id=i, hr_prob=p, base_rate=0.10)
           for i, p in enumerate([0.30, 0.15, 0.25, 0.40, 0.10, 0.35], start=1)]
out = gp.select_moonshots(batters, {}, fd, n=3)
check(len(out) == 3, "the result is truncated to n=3 even though 6 batters qualified")
check([o["hit_probability"] for o in out] == [0.40, 0.35, 0.30],
      "the top 3 by hit_probability descending are kept, in the right order",
      f"got {[o['hit_probability'] for o in out]}")

head("9. an empty candidate list returns an empty list")

check(gp.select_moonshots([], {}, fd) == [], "no candidates at all returns an empty list")

head("10. lineup_assumed survives into the output row -- real bug, found live 2026-08-15: "
     "this function's own output dict was a fixed field list that silently dropped "
     "lineup_assumed, so a batter whose lineup slot is a guess (quality_control()'s "
     "assumed=True tag) came out of here indistinguishable from a fully confirmed one, with "
     "no way for a caller to badge it")

assumed_out = gp.select_moonshots([batter_c(lineup_assumed=True)], {}, fd)
check(len(assumed_out) == 1 and assumed_out[0]["lineup_assumed"] is True,
      "an assumed-lineup candidate's flag survives into the moonshot row", f"got {assumed_out}")
confirmed_out = gp.select_moonshots([batter_c(name="Confirmed Guy", player_id=6)], {}, fd)
check(confirmed_out[0].get("lineup_assumed") is None,
      "a normal (confirmed) candidate with no lineup_assumed key stays honestly absent, "
      "not fabricated as False")

head("11. moonshot confidence is computed independently of the batter's own (hits/TB) "
     "confidence -- direct request: \"does the math support it? Or is it just because we "
     "have an edge?\" Real bug, found live 2026-08-15: Gleyber Torres' Home Run pick showed "
     "'High' confidence (borrowed from his HITS read) with only a 0.73-point HR-specific "
     "lift and no why/reliability/sample_n at all.")

# A batter's own (hits/TB) confidence is "High" in every case below -- if the
# bug were still there, every one of these would show High. The real
# HR-specific reliability/lift are what should actually decide it now.
torres_case = batter_c(hr_prob=0.1107, base_rate=0.1034, confidence="High",
                       reliability="B", sample_n=80)  # lift = 0.0073 -- the real Torres case
out11 = gp.select_moonshots([torres_case], {}, fd)
check(out11[0]["confidence"] == "Low",
      "a thin (0.73-point) lift never earns High or Medium confidence, no matter how "
      "reliable the player's own track record is or what his HITS confidence says",
      f"got {out11[0]['confidence']}")
check(out11[0]["why"] and "0.73" not in out11[0]["why"][0] and "11.1%" in out11[0]["why"][0],
      "a real, HR-specific why sentence is generated with the real computed numbers",
      f"got {out11[0]['why']}")

real_lock = batter_c(hr_prob=0.20, base_rate=0.15, reliability="A", sample_n=120)  # lift=0.05
out_lock = gp.select_moonshots([real_lock], {}, fd)
check(out_lock[0]["confidence"] == "High",
      "a genuinely reliable player (grade A) with a real 5-point HR-specific lift "
      "(>= MOONSHOT_LOCK_LIFT) earns real High confidence", f"got {out_lock[0]['confidence']}")

medium_case = batter_c(hr_prob=0.14, base_rate=0.11, reliability="C", sample_n=30)  # lift=0.03, but C
out_medium = gp.select_moonshots([medium_case], {}, fd)
check(out_medium[0]["confidence"] == "Medium",
      "a positive lift with only a C reliability grade (thin sample) caps out at Medium, "
      "never High", f"got {out_medium[0]['confidence']}")

thin_case = batter_c(hr_prob=0.12, base_rate=0.11, reliability="D", sample_n=10)  # lift=0.01
out_thin = gp.select_moonshots([thin_case], {}, fd)
check(out_thin[0]["confidence"] == "Low",
      "a D reliability grade (very thin sample) never clears Medium even with a positive lift",
      f"got {out_thin[0]['confidence']}")

negative_lift = batter_c(hr_prob=0.08, base_rate=0.11, reliability="A", sample_n=150)  # lift=-0.03
out_neg = gp.select_moonshots([negative_lift], {}, fd)
check(out_neg[0]["confidence"] == "Low",
      "a negative lift (the model actually likes this LESS than his own base rate) is Low "
      "regardless of how reliable his track record is", f"got {out_neg[0]['confidence']}")

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
