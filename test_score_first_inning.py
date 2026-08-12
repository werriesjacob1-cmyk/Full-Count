#!/usr/bin/env python3
"""test_score_first_inning.py — coverage for generate_picks.score_first_
inning(), the NRFI/YRFI-lean scorer. Had zero test coverage despite its
own docstring documenting a real, previously-shipped bug: scale()'s linear
extrapolation wasn't clamped on the input side, so a 0% yrfi_rate on a
thin L14 sample extrapolated past 100 and clamped there -- every
scoreless-first-inning starter tied at a perfect 100 and swept the top 10
regardless of real sample size.

    /tmp/mlbvenv/bin/python3 test_score_first_inning.py
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
      "game_pk": 900001}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "fi_opp_team", "signals", "lean", "score", "why", "watchouts",
                 "notable_signals", "confidence"}


def fi(yrfi_rate, n_starts=8, runs_per=0.4):
    return {"Framber Valdez": {"yrfi_rate": yrfi_rate, "n_starts": n_starts,
                                "runs_per_1st_inning": runs_per}}


head("1. no fi_form entry for this starter -> None, not a crash")

check(gp.score_first_inning("Framber Valdez", 502, GM, "home", {}) is None,
      "a starter absent from fi_form entirely returns None")
check(gp.score_first_inning("Unknown Guy", 999, GM, "home", fi(30)) is None,
      "a starter present in fi_form but under a different name key returns None "
      "(fi_form.get(sp_name), keyed by name)")

head("2. the previously-shipped bug: a 0% yrfi_rate on a thin sample does NOT auto-cap at 100")

c_zero_thin = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(0, n_starts=2))
check(c_zero_thin["score"] < 100,
      "a 0% yrfi_rate (perfect scoreless streak) on only 2 starts does not extrapolate past "
      "100 and clamp there -- the exact bug this function's docstring documents",
      f"got score={c_zero_thin['score']}")
check(c_zero_thin["score"] <= 55,
      "a 2-start read is capped at 55 (never more than low/medium confidence), regardless "
      "of how perfect the tiny sample looks", f"got {c_zero_thin['score']}")
check(c_zero_thin["confidence"] != "High",
      "a 2-start sample never reaches High confidence even at a 'perfect' 0% rate")

head("3. lean selection: yrfi_rate >= 38 -> YRFI, otherwise NRFI")

c_yrfi = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(60, n_starts=10))
check(c_yrfi["lean"] == "YRFI", "yrfi_rate=60 (>=38) leans YRFI", f"got {c_yrfi['lean']}")
check("to score in the 1st" in c_yrfi["prop"], "a YRFI lean's prop label says 'to score'",
      f"got {c_yrfi['prop']!r}")

c_nrfi = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(15, n_starts=10))
check(c_nrfi["lean"] == "NRFI", "yrfi_rate=15 (<38) leans NRFI", f"got {c_nrfi['lean']}")
check("scoreless in the 1st" in c_nrfi["prop"], "an NRFI lean's prop label says 'scoreless'",
      f"got {c_nrfi['prop']!r}")

c_boundary = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(38, n_starts=10))
check(c_boundary["lean"] == "YRFI", "yrfi_rate=38 exactly (the boundary) leans YRFI, not NRFI",
      f"got {c_boundary['lean']}")

head("4. this is the ONE-SIDED market, not the standard both-teams NRFI -- the market label "
     "must reflect that")

check("Athletics" in c_yrfi["fi_opp_team"] or c_yrfi["fi_opp_team"] == "Athletics",
      "an AWAY starter's fi_opp_team is the HOME team's lineup he actually faces in the "
      "bottom of the 1st... wait, side='home' means the starter IS on the home team, "
      "so he faces the away lineup", f"got {c_yrfi['fi_opp_team']}")
check(c_yrfi["fi_opp_team"] == "Athletics",
      "a HOME starter (Framber, side='home') faces the AWAY team's lineup in the top of "
      "the 1st, so fi_opp_team correctly resolves to Athletics, not Astros",
      f"got {c_yrfi['fi_opp_team']}")

c_away_side = gp.score_first_inning("JP Sears", 501, GM, "away", fi(60, n_starts=10))
check(c_away_side is None or True, "away-side call doesn't crash")  # JP Sears not in fi dict above
c_away_side2 = gp.score_first_inning(
    "Framber Valdez", 502, GM, "away",
    {"Framber Valdez": {"yrfi_rate": 60, "n_starts": 10, "runs_per_1st_inning": 0.5}})
check(c_away_side2["fi_opp_team"] == "Astros",
      "an AWAY-side starter faces the HOME team's lineup in the bottom of the 1st, so "
      "fi_opp_team correctly resolves to Astros", f"got {c_away_side2['fi_opp_team']}")

head("5. sample_penalty scales down smoothly with more starts, not a cliff")

scores_by_n = {}
for n in (2, 3, 4, 5, 10):
    c = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(60, n_starts=n))
    scores_by_n[n] = c["score"]
check(scores_by_n[2] < scores_by_n[3] < scores_by_n[4] < scores_by_n[5],
      "more real starts at the identical yrfi_rate monotonically raises the score up through "
      "the 5-start no-penalty floor", f"got {scores_by_n}")
check(scores_by_n[5] == scores_by_n[10],
      "5+ starts carries no further sample penalty (max(0, (5-n)*15) floors at 0)",
      f"got 5={scores_by_n[5]} 10={scores_by_n[10]}")

head("6. notable_signals requires BOTH an extreme rate AND n_starts >= 3")

c_extreme_thin = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(60, n_starts=2))
check(c_extreme_thin["notable_signals"] == 0,
      "an extreme 60% yrfi_rate on only 2 starts does NOT count as notable -- the n>=3 "
      "gate must hold even for an extreme rate", f"got {c_extreme_thin['notable_signals']}")

c_extreme_thick = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(60, n_starts=5))
check(c_extreme_thick["notable_signals"] == 1,
      "the same extreme 60% rate WITH n_starts=5 does count as notable")

c_mild_thick = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(25, n_starts=5))
check(c_mild_thick["notable_signals"] == 0,
      "a mild 25% yrfi_rate (neither >=55 nor <=10) never counts as notable regardless of sample")

head("7. ump_run_impact and park_hr_index are RECORDED signals only -- they must never move the score")

base = gp.score_first_inning("Framber Valdez", 502, GM, "home", fi(60, n_starts=10))
with_signals = gp.score_first_inning(
    "Framber Valdez", 502, GM, "home", fi(60, n_starts=10),
    ump_env={"Athletics @ Astros": {"run_impact_magnitude": 2.10}},
    park_wx={"Athletics @ Astros": {"park_hr_index": 80}})
check(base["score"] == with_signals["score"],
      "adding real ump_env/park_wx data changes the recorded signals but must NOT move the "
      "score itself -- both are explicitly unvalidated per the function's own comments",
      f"base={base['score']} with_signals={with_signals['score']}")
check("ump_run_impact" in with_signals["signals"] and "ump_run_impact" not in base["signals"],
      "ump_run_impact is recorded (as its SCALED 0-100 form, per _sig's contract) only when "
      "real ump_env data is supplied, absent otherwise", f"got {with_signals['signals']}")
check(with_signals["signals"].get("park_hr_index") == 80,
      "park_hr_index is recorded as-is (already a 0-100 index, not re-scaled) even though "
      "it doesn't score")

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
