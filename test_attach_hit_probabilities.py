#!/usr/bin/env python3
"""test_attach_hit_probabilities.py — coverage for generate_picks.attach_
hit_probabilities(), the function that gives every candidate its real
chance-of-cashing number and lets batter props re-choose their threshold
to maximize it. Had zero test coverage despite being the single highest
bet-outcome-risk function in the file -- everything the board ranks and
prices flows through here.

Does not re-derive every one of _batter_options' nine prop families (that
would duplicate the function). Focuses on attach_hit_probabilities' OWN
dispatch logic per stat, and the two specific historical bugs its own
comments document: (1) the projection/prop label had to be rewritten to
match the recommended line exactly, or grade_results.py could grade a
double as a miss against a stale threshold; (2) first_inning_run's
yrfi_rate is a PERCENTAGE (0-100), and reading it as a fraction used to
produce a "10000% to hit" pick with the NRFI side computing 1-100=-99.

    /tmp/mlbvenv/bin/python3 test_attach_hit_probabilities.py
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

COMP = {"singles_rate": 0.15, "double_rate": 0.05, "triple_rate": 0.004, "hr_rate": 0.035}

TRUE_LEAGUE = {
    "hits_1plus": 0.62, "hits_2plus": 0.20,
    "total_bases_2plus": 0.42, "total_bases_3plus": 0.20,
    "home_runs_1plus": 0.10,
}


def batter_c(pid=5, stat="total_bases", pa=4.3, **over):
    c = {"type": "batter", "player_id": pid, "projected_pa": pa,
         "projection": {"stat": stat, "value": 1.5, "needs": 2}, "prop": "Over 1.5 Total Bases"}
    c.update(over)
    return c


head("1. total_bases: the recommended line REWRITES prop/projection to match exactly "
     "(THE BUG THIS PREVENTS: grade_results.py grades on projection.needs, which used to "
     "disagree with the displayed prop label)")

c = batter_c(pid=5)
emp = {5: {"games": 100, "rates": {
    "total_bases_2plus": {"p_hat": 0.75, "p": 0.75, "league_p": 0.30, "n": 100},
    "total_bases_3plus": {"p_hat": 0.30, "p": 0.30, "league_p": 0.15, "n": 100},
}}}
out = gp.attach_hit_probabilities([c], {5: COMP}, emp, {}, league_rates=TRUE_LEAGUE)
c_out = out[0]
check(c_out["hit_probability"] is not None, "a batter with real comp+emp data gets a "
      "real hit_probability, not None", f"got {c_out}")
check(str(c_out["projection"]["value"]) in c_out["prop"],
      "the prop label's line and projection.value are the SAME number -- the exact "
      "disagreement this function's comments describe as a real, previously-shipped bug",
      f"prop={c_out['prop']!r} projection={c_out['projection']}")
check("line_options" in c_out and len(c_out["line_options"]) > 0,
      "the full probability curve is preserved in line_options, not thrown away")
check(isinstance(c_out.get("alternatives"), list),
      "alternatives carries the non-chosen lines")

head("2. total_bases: no comp/emp data at all still returns a well-formed (if unscored) result")

c_totally_bare = batter_c(pid=999)
out_totally_bare = gp.attach_hit_probabilities([c_totally_bare], {}, {}, {}, league_rates=None)
check(out_totally_bare[0]["hit_probability"] is None,
      "with NEITHER real data NOR a league_rates fallback, hit_probability is honestly "
      "None -- never a fabricated number", f"got {out_totally_bare[0]}")

head("3. stolen_base: needs=1 is added to the projection -- THE BUG THIS FIXES: "
     "attach_market_prices() keys on (stat, needs), and this projection never carried "
     "'needs' before, so it was always looked up as (stat, None) and silently unpriceable")

sb_c = {"type": "batter", "player_id": 7, "projected_pa": 4.5,
       "projection": {"stat": "stolen_base", "value": 1}}
comp_sb = {7: {"attempt_rate": 0.08, "success_rate": 0.75, "obp": 0.34}}
emp_sb = {7: {"games": 30, "rates": {"stolen_bases_1plus": {"p_hat": 0.22, "p": 0.22, "n": 30}}}}
out_sb = gp.attach_hit_probabilities([sb_c], {}, emp_sb, {}, league_rates=None)
check(out_sb[0]["projection"].get("needs") == 1,
      "stolen_base's projection gets needs=1 added -- required for attach_market_prices "
      "to ever find FanDuel's real TO_RECORD_A_STOLEN_BASE line", f"got {out_sb[0]['projection']}")
check(out_sb[0]["hit_probability"] is not None,
      "a real empirical rate (30 games, above MIN_EMPIRICAL_GAMES=25) produces a real "
      "hit_probability, not None")

sb_c2 = {"type": "batter", "player_id": 8, "projected_pa": 4.5,
        "projection": {"stat": "stolen_base", "value": 1}}
out_sb2 = gp.attach_hit_probabilities([sb_c2], {8: comp_sb[7]}, {}, {}, league_rates=None)
check(out_sb2[0]["hit_probability"] is not None,
      "with no empirical data but real comp (attempt_rate/success_rate) data, the "
      "modelled term alone still produces a real hit_probability", f"got {out_sb2[0]}")

head("4. strikeouts: base_rate/lift are now populated -- THE BUG THIS FIXES: strikeout "
     "props used to ship with no base_rate at all, making the board's top picks "
     "incomparable to every other market")

k_c = {"type": "pitcher", "player_id": 501, "expected_bf": 24.0, "k_rate": 0.27,
       "projection": {"stat": "strikeouts"}}
league_k = {"strikeouts_4plus": 0.85, "strikeouts_5plus": 0.70, "strikeouts_6plus": 0.50,
            "strikeouts_7plus": 0.30, "strikeouts_8plus": 0.15}
out_k = gp.attach_hit_probabilities([k_c], {}, {}, {}, league_rates=league_k)
c_k = out_k[0]
check(c_k["hit_probability"] is not None, "a pitcher with real expected_bf/k_rate gets a "
      "real strikeouts hit_probability")
check(c_k.get("base_rate") is not None and c_k.get("lift") is not None,
      "base_rate and lift are both populated -- the exact fields this function's own "
      "comment says used to ship null", f"got base_rate={c_k.get('base_rate')} lift={c_k.get('lift')}")
check(abs(c_k["lift"] - round(c_k["hit_probability"] - c_k["base_rate"], 4)) < 1e-6,
      "lift is exactly hit_probability - base_rate, internally consistent")

head("5. strikeouts: with a true league rate present, the modelled probability is SHRUNK "
     "toward it (STRIKEOUT_SHRINK_K=0.5), not used raw -- verified by comparing against "
     "the same call with league_rates=None (falls back to the raw blend)")

out_k_noleague = gp.attach_hit_probabilities(
    [dict(k_c, projection={"stat": "strikeouts"})], {}, {}, {}, league_rates=None)
check(out_k[0]["hit_probability"] != out_k_noleague[0]["hit_probability"],
      "the shrunk-toward-league result differs from the no-league-rate fallback, "
      "proving the shrink path is actually taken when a true league rate exists",
      f"shrunk={out_k[0]['hit_probability']} raw_fallback={out_k_noleague[0]['hit_probability']}")

head("6. first_inning_run: THE SCALE BUG THIS PREVENTS -- yrfi_pct is stored as a "
     "PERCENTAGE (e.g. 60.0), and must be divided by 100 before use as a probability, "
     "never read as a raw fraction")

fi_c = {"type": "pitcher", "player_id": 502, "side": "home", "fi_opp_team": "Athletics",
       "projection": {"stat": "first_inning_run", "value": 60.0},
       "signals": {"fi_n_starts": 20}, "lean": "YRFI"}
out_fi = gp.attach_hit_probabilities([fi_c], {}, {}, {})
# first_inning_run candidates never survive to the final list -- see check 8 below --
# so this has to be checked via a pre-filter capture instead of reading `out_fi` directly.
captured = []
orig_build = gp._build_combined_nrfi
def _capture_build(cands):
    captured.extend([dict(c) for c in cands if (c.get("projection") or {}).get("stat") == "first_inning_run"])
    return orig_build(cands)
gp._build_combined_nrfi = _capture_build
try:
    gp.attach_hit_probabilities([dict(fi_c)], {}, {}, {})
finally:
    gp._build_combined_nrfi = orig_build
check(len(captured) == 1, "the first_inning_run candidate reaches the combined-NRFI builder "
      "with its hit_probability already computed")
check(0.0 <= captured[0]["hit_probability"] <= 1.0,
      "hit_probability is a genuine 0-1 probability, never a raw percentage like 60.0 or "
      "a negative number like 1-60=-59 (the exact historical bug)", f"got {captured[0]}")

head("7. first_inning_run: THE SIDE-FLIP BUG THIS PREVENTS -- the lean is chosen from the "
     "SHRUNK rate, not the raw one, since shrinkage moves the 50/50 point substantially "
     "at low n")

# raw=37.2% at n=... documented as flipping: shrunk >= 0.5 despite a raw YRFI-looking rate
# under score_first_inning's own 38% threshold once n is high enough for shrinkage to pull
# it toward a genuinely high league rate scenario -- here we construct the documented
# n=2 case where the YRFI side is UNREACHABLE at any raw rate.
fi_thin = {"type": "pitcher", "player_id": 503, "side": "home", "fi_opp_team": "Astros",
          "projection": {"stat": "first_inning_run", "value": 100.0},  # "perfect" 2-start read
          "signals": {"fi_n_starts": 2}, "lean": "YRFI"}
captured2 = []
def _capture_build2(cands):
    captured2.extend([dict(c) for c in cands if (c.get("projection") or {}).get("stat") == "first_inning_run"])
    return orig_build(cands)
gp._build_combined_nrfi = _capture_build2
try:
    gp.attach_hit_probabilities([dict(fi_thin)], {}, {}, {})
finally:
    gp._build_combined_nrfi = orig_build
c_thin_fi = captured2[0]
check(c_thin_fi["lean"] == "NRFI",
      "a 2-start '100% scored on' read is UNREACHABLE for a real YRFI recommendation once "
      "shrunk (FI_PRIOR_STARTS=52 dominates at n=2) -- the side flips to NRFI even though "
      "the raw rate and the original score_first_inning lean both said YRFI",
      f"got lean={c_thin_fi['lean']} hit_probability={c_thin_fi['hit_probability']}")

head("8. first_inning_run candidates NEVER survive as standalone picks -- only "
     "nrfi_combined (built from complete game pairs) reaches the final list")

fi_away = {"type": "pitcher", "player_id": 504, "name": "JP Sears", "side": "away", "team": "Athletics",
          "fi_opp_team": "Astros", "game_pk": 1, "matchup": "Athletics @ Astros",
          "projection": {"stat": "first_inning_run", "value": 30.0},
          "signals": {"fi_n_starts": 10}, "lean": "NRFI"}
fi_home = {"type": "pitcher", "player_id": 505, "name": "Framber Valdez", "side": "home", "team": "Astros",
          "fi_opp_team": "Athletics", "game_pk": 1, "matchup": "Athletics @ Astros",
          "projection": {"stat": "first_inning_run", "value": 25.0},
          "signals": {"fi_n_starts": 10}, "lean": "NRFI"}
out_pair = gp.attach_hit_probabilities([fi_away, fi_home], {}, {}, {})
stats_out = {(c.get("projection") or {}).get("stat") for c in out_pair}
check("first_inning_run" not in stats_out,
      "no first_inning_run candidate ever appears in the returned list, even when both "
      "sides of a real pair were present", f"got stats={stats_out}")
check("nrfi_combined" in stats_out,
      "the real, both-teams nrfi_combined candidate replaces the two one-sided reads",
      f"got stats={stats_out}")

head("9. an unrecognized stat gets hit_probability defaulted to None, without overwriting "
     "an existing value")

other_c = {"type": "batter", "projection": {"stat": "some_future_market"}}
out_other = gp.attach_hit_probabilities([other_c], {}, {}, {})
check(out_other[0]["hit_probability"] is None,
      "a stat this function doesn't handle gets hit_probability=None via setdefault")

other_c_prefilled = {"type": "batter", "projection": {"stat": "some_future_market"},
                     "hit_probability": 0.42}
out_other2 = gp.attach_hit_probabilities([other_c_prefilled], {}, {}, {})
check(out_other2[0]["hit_probability"] == 0.42,
      "setdefault means a PRE-EXISTING hit_probability on an unhandled stat is never "
      "clobbered back to None")

head("10. an empty candidate list returns an empty list")

check(gp.attach_hit_probabilities([], {}, {}, {}) == [], "no candidates returns an empty list")

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
