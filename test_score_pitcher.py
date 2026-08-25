#!/usr/bin/env python3
"""test_score_pitcher.py — smoke/edge-case coverage for generate_picks.
score_pitcher(). Had zero real test coverage (one existing test file
mentions its name in a comment, but never calls it).

Same philosophy as test_score_batter.py: doesn't re-derive every signal
formula inside score_pitcher, checks that it never crashes and always
returns a well-formed candidate given minimal, missing, or edge-case
real-world inputs (a TBD-adjacent starter with no season stats yet, an
opposing lineup with unknown handedness, no L14 form, no umpire data).

    /tmp/mlbvenv/bin/python3 test_score_pitcher.py
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

GM = {"matchup": "Athletics @ Astros", "away_team": "Athletics", "home_team": "Astros",
      "game_pk": 900001, "series_game": 1}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "signals", "expected_bf", "k_rate", "score", "why",
                 "watchouts", "notable_signals", "confidence",
                 "cat_matchup", "cat_recent_form", "cat_environment",
                 "cat_baseline_skill", "cat_context"}

REAL_LINEUP = [{"name": f"Batter {i}", "id": i, "bats": "R" if i % 2 else "L"} for i in range(1, 10)]


def call(sp_name="Framber Valdez", sp_id=501, sp_hand="L", side="home",
        pit_season_lookup=None, l14_form=None, opp_lineup=None, opp_team_k_pct=None,
        ump_scores=None, **kw):
    return gp.score_pitcher(
        sp_name, sp_id, sp_hand, GM, side,
        pit_season_lookup or {}, l14_form or {}, opp_lineup or REAL_LINEUP,
        opp_team_k_pct, ump_scores or {}, **kw)


head("1. a normal, complete-ish call returns a well-formed candidate")

c = call(pit_season_lookup={"Framber Valdez": {"K%": 24.5, "CSW%": 29.0, "ERA": 3.10}},
        l14_form={"Framber Valdez": {"l14_pa": 90, "l14_k_pct": 25.0}}, opp_team_k_pct=23.0)
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["type"] == "pitcher" and c["name"] == "Framber Valdez" and c["player_id"] == 501,
      "identity fields pass through correctly")
check(0 <= c["score"] <= 100, "score is bounded to [0, 100]", f"got {c['score']}")
check(0 < c["k_rate"] < 1, "k_rate is a real fraction, not a raw percentage or out of range",
      f"got {c['k_rate']}")

# PROMOTED 2026-08-14: score_pitcher no longer uses the original hand-set
# 35/25/15/15/10 -- see the comment above its own `score = clamp(...)` line
# for the measured findings (cleared the old formula's CI on 5 of 5
# independent train/held-out splits, the most robust finding of the
# night). ENVIRONMENT/CONTEXT weights deliberately kept at their original
# 0.15/0.10 -- both are functionally constant for this market, so the fit
# had nothing real to say about them.
rebuilt = gp.clamp(c["cat_matchup"] * 0.11 + c["cat_recent_form"] * -0.16 + c["cat_environment"] * 0.15
                   + c["cat_baseline_skill"] * 0.48 + c["cat_context"] * 0.10)
check(abs(round(rebuilt, 1) - c["score"]) < 0.15,
      "score == clamp(0.11*matchup + -0.16*recent_form + 0.15*environment + "
      "0.48*baseline_skill + 0.10*context)",
      f"rebuilt={rebuilt:.2f} vs recorded score={c['score']}")

head("2. every optional input at its default doesn't crash")

c2 = call()  # no season stats, no L14 form, no opp_team_k_pct, empty ump_scores
check(REQUIRED_KEYS.issubset(c2.keys()), "a call with nothing but name/id/hand/lineup still "
      "returns a well-formed candidate", f"got keys={sorted(c2.keys())}")
check(0 <= c2["score"] <= 100, "score stays bounded with no season data at all")
check(0 < c2["k_rate"] < 1, "k_rate falls back to the documented default (22.5%) rather than "
      "crashing or returning None", f"got {c2['k_rate']}")

head("3. a starter with no season stats on record at all (early-season call-up)")

c3 = call(sp_name="Rookie Starter", sp_id=999, pit_season_lookup={})
check(REQUIRED_KEYS.issubset(c3.keys()), "a starter absent from the season lookup entirely still "
      "produces a well-formed candidate rather than a KeyError")

head("4. an opposing lineup with unknown handedness on every batter")

unknown_hand_lineup = [{"name": f"Batter {i}", "id": i, "bats": "?"} for i in range(1, 10)]
c4 = call(opp_lineup=unknown_hand_lineup)
check(REQUIRED_KEYS.issubset(c4.keys()),
      "an opposing lineup with no known handedness anywhere falls back gracefully "
      "(same_hand_ratio defaults rather than dividing by zero)")

head("5. an empty opposing lineup (posted but somehow zero entries)")

c5 = call(opp_lineup=[])
check(REQUIRED_KEYS.issubset(c5.keys()), "an empty opposing lineup list doesn't crash "
      "(known=0 case in the same-hand ratio must not divide by zero)")

head("6. ump_kbb/il_returns/callups all None (the pre-this-session call shape)")

c6 = call(ump_kbb=None, il_returns=None, callups=None)
check(REQUIRED_KEYS.issubset(c6.keys()),
      "the three newest optional kwargs all at None reproduce the original call shape safely")

head("7. away side vs home side both resolve team correctly")

c7a = call(side="away")
c7b = call(side="home")
check(c7a["team"] == "Athletics" and c7b["team"] == "Astros",
      "side='away'/'home' resolve to the correct team from game_meta, not swapped",
      f"got away->{c7a['team']}, home->{c7b['team']}")

head("8. 2026-08-24 explanation-quality fix: the 'why' note reports the real opposing K% "
     "number without leaking which internal source/failure produced it -- direct complaint, "
     "Jose Urquidy card, raw text like 'MLB Stats API -- FanGraphs team page unreachable' "
     "showing up in a public explanation. opp_k_source is one of: 'team' (FanGraphs), "
     "'mlb_team' (the MLB Stats API fallback -- reads identically to the user now, since "
     "both are genuine full-team K% numbers), an int (lineup-average N, a real methodology "
     "difference so it's still called out), or None (nothing at all matched, which is "
     "missing data and belongs in watchouts, not why).")

c8_fg = call(opp_team_k_pct=23.0, opp_k_source="team")
check(any("Opposing team K% 23.0" in w and "MLB Stats API" not in w and "FanGraphs" not in w
          for w in c8_fg["why"]),
      "opp_k_source='team' (FanGraphs) produces the plain team-K% note, no source caveat",
      f"got {c8_fg['why']}")

c8_mlb = call(opp_team_k_pct=21.0, opp_k_source="mlb_team")
check(any(w == "Opposing team K% 21.0" for w in c8_mlb["why"]),
      "opp_k_source='mlb_team' now reads identically to the FanGraphs case -- both are real "
      "full-team K% numbers, so the internal source/outage detail is no longer surfaced",
      f"got {c8_mlb['why']}")
check(not any("MLB Stats API" in w or "unreachable" in w for w in c8_mlb["why"]),
      "no raw provider/outage text leaks into the public why list", f"got {c8_mlb['why']}")

c8_lineup = call(opp_team_k_pct=19.5, opp_k_source=6)
check(any("Opposing lineup K% 19.5" in w and "6 confirmed lineup batters" in w for w in c8_lineup["why"]),
      "opp_k_source=<int> still calls out the real methodology difference (a partial-lineup "
      "proxy, not the full team rate) but phrased as what the number IS, not why the "
      "preferred source failed", f"got {c8_lineup['why']}")
check(not any("unreachable" in w for w in c8_lineup["why"]), "no outage language leaks here either",
      f"got {c8_lineup['why']}")

c8_none = call(opp_team_k_pct=None)
check(not any("Opposing team K%" in w for w in c8_none["why"]),
      "opp_team_k_pct=None (every real source came up empty) is missing data, not a reason "
      "to like the pick -- it must not land in `why` at all", f"got {c8_none['why']}")
check(any("Opposing team strikeout tendency unavailable" in w for w in c8_none["watchouts"]),
      "the same information now lands in watchouts instead, still honest about what's missing, "
      "without naming internal providers by name", f"got {c8_none['watchouts']}")

head("9. 2026-08-25 explanation-quality fix (release-readiness audit): L14 K% must not land "
     "unqualified in `why` when it's actually a COLD recent-form reading -- real production "
     "bug found via docs/data.json: Clay Holmes' L14 K% of 6.7% rendered under 'Why It Could "
     "Hit' with no qualifying language. form_l14_raw = scale(l14_k_pct, 15, 32) is the "
     "RECENT FORM component's own already-computed directional value -- same class of fix as "
     "check 8's opposing-K% routing and the batter-side ev_note/barrel_note pattern.")

c9_hot = call(l14_form={"Framber Valdez": {"l14_pa": 90, "l14_k_pct": 30.0}})
check(any("L14 K% 30.0" in w and "hot recent form" in w for w in c9_hot["why"]),
      "a genuinely hot L14 K% (scale>=65) is labeled 'hot recent form' in why",
      f"got {c9_hot['why']}")

c9_cold = call(l14_form={"Framber Valdez": {"l14_pa": 90, "l14_k_pct": 6.7}})
check(not any("L14 K%" in w for w in c9_cold["why"]),
      "REGRESSION GUARD: a cold L14 K% (Clay Holmes' real 6.7%) must NOT appear anywhere in "
      "why -- the positive-reasons list -- since it isn't a reason to like the pick",
      f"got {c9_cold['why']}")
check(any("L14 K% 6.7" in w and "cold recent form" in w for w in c9_cold["watchouts"]),
      "that same cold L14 K% instead lands in watchouts, honestly labeled",
      f"got {c9_cold['watchouts']}")

c9_neutral = call(l14_form={"Framber Valdez": {"l14_pa": 90, "l14_k_pct": 23.5}})
check(any(w == "L14 K% 23.5 (90 PA)" for w in c9_neutral["why"]),
      "a neutral-middle L14 K% stays an unqualified plain fact in why, exactly as before -- "
      "no invented judgment where the real number doesn't clearly support one",
      f"got {c9_neutral['why']}")

c9_thin = call(l14_form={"Framber Valdez": {"l14_pa": 10, "l14_k_pct": 5.0}})
check(any(w == "L14 K% 5.0 (10 PA)" for w in c9_thin["why"]),
      "a thin-sample L14 K% (below the 15-PA low_sample_form floor) stays plain and "
      "unqualified in why -- the separate 'L14 Statcast sample too thin' watchout already "
      "covers the caveat, so this doesn't double up or over-interpret a noisy number",
      f"got {c9_thin['why']}")
check(not any("cold recent form" in w or "hot recent form" in w for w in c9_thin["why"] + c9_thin["watchouts"]),
      "a thin sample never gets a hot/cold label at all -- form_l14_raw is None there, so "
      "neither threshold branch can fire", f"got why={c9_thin['why']} watchouts={c9_thin['watchouts']}")

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
