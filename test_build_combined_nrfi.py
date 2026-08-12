#!/usr/bin/env python3
"""test_build_combined_nrfi.py — coverage for generate_picks._build_
combined_nrfi(), the real, books-comparable NRFI/YRFI market (both
starters' halves of the 1st combined). Had zero test coverage despite its
own docstring stating up front, deliberately, that the honest combined
number should sit close to a coinflip for nearly every game -- a
regression that silently made this look like a strong signal again would
be easy to miss without a test pinning the actual math down.

    /tmp/mlbvenv/bin/python3 test_build_combined_nrfi.py
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

GAME_PK = 900001


def fi_candidate(side, team, lean, hit_probability, n_starts=8, name="SP", matchup="Athletics @ Astros"):
    return {
        "projection": {"stat": "first_inning_run"}, "hit_probability": hit_probability,
        "side": side, "team": team, "name": name, "matchup": matchup, "game_pk": GAME_PK,
        "lean": lean, "signals": {"fi_n_starts": float(n_starts)},
    }


head("1. a game with only ONE side's first_inning_run candidate is dropped, not guessed")

only_away = [fi_candidate("away", "Athletics", "NRFI", 0.75)]
check(gp._build_combined_nrfi(only_away) == [],
      "only the away starter's read present -- no combined candidate is manufactured "
      "for the missing home half")

head("2. non-first_inning_run candidates and ones with no hit_probability are ignored")

mixed = [fi_candidate("away", "Athletics", "NRFI", 0.75),
         {"projection": {"stat": "hits"}, "hit_probability": 0.9, "side": "home", "game_pk": GAME_PK},
         fi_candidate("home", "Astros", "NRFI", None)]  # home present but unpriced
check(gp._build_combined_nrfi(mixed) == [],
      "a home-side entry with hit_probability=None doesn't count as 'both sides present' "
      "-- still needs a real number, not just a slot")

head("3. two real, complete sides combine into one game-level candidate")

both_nrfi = [
    fi_candidate("away", "Athletics", "NRFI", 0.75, n_starts=10, name="JP Sears"),
    fi_candidate("home", "Astros", "NRFI", 0.70, n_starts=8, name="Framber Valdez"),
]
out = gp._build_combined_nrfi(both_nrfi)
check(len(out) == 1, "two complete sides for the same game produce exactly one combined candidate")
c = out[0]
check(c["type"] == "game" and c["player_id"] == f"nrfi_{GAME_PK}",
      "the combined candidate is typed 'game', not 'batter'/'pitcher'")

head("4. THE CORE MATH: P(NRFI) = (1 - P(home scores)) * (1 - P(away scores)), reconstructed "
     "correctly from EACH side's own lean, not assumed to already be 'scores' probabilities")

# away_c (JP Sears, NRFI lean, 0.75) -> he faces Astros in bottom 1st, NRFI lean means
# hit_probability=0.75 already IS P(Astros do NOT score) -- so P(home team scores) = 1-0.75 = 0.25
# home_c (Framber, NRFI lean, 0.70) -> P(away team scores) = 1-0.70 = 0.30
p_home_scores = 1 - 0.75
p_away_scores = 1 - 0.70
expected_p_nrfi = (1 - p_home_scores) * (1 - p_away_scores)
check(abs(c["signals"]["home_team_scores_p"] - p_home_scores) < 1e-9,
      "home_team_scores_p is correctly reconstructed as 1-0.75=0.25 from the away "
      "starter's NRFI-lean read", f"got {c['signals']['home_team_scores_p']}")
check(abs(c["signals"]["away_team_scores_p"] - p_away_scores) < 1e-9,
      "away_team_scores_p is correctly reconstructed as 1-0.70=0.30 from the home "
      "starter's NRFI-lean read", f"got {c['signals']['away_team_scores_p']}")
check(c["lean"] == "NRFI" and abs(c["hit_probability"] - round(expected_p_nrfi, 4)) < 1e-4,
      "the combined P(NRFI) matches the documented formula exactly",
      f"got hit_probability={c['hit_probability']}, want ~{round(expected_p_nrfi, 4)}")

head("5. a YRFI lean on a side means its hit_probability is already P(scores), not P(NRFI) "
     "-- the reconstruction must branch on lean, not always assume NRFI framing")

yrfi_side = [
    fi_candidate("away", "Athletics", "YRFI", 0.60, n_starts=10),  # hit_probability IS P(home scores)
    fi_candidate("home", "Astros", "NRFI", 0.70, n_starts=8),      # hit_probability IS P(away doesn't score)
]
out2 = gp._build_combined_nrfi(yrfi_side)
c2 = out2[0]
check(abs(c2["signals"]["home_team_scores_p"] - 0.60) < 1e-9,
      "a YRFI-leaning away starter's hit_probability (0.60) is used DIRECTLY as "
      "P(home team scores), not inverted -- the lean must gate which branch runs",
      f"got {c2['signals']['home_team_scores_p']}")

head("6. sample penalty: min(both starters' starts) drives the penalty, not either alone "
     "or their average -- the WEAKER read should gate confidence")

thin_one_side = [
    fi_candidate("away", "Athletics", "NRFI", 0.75, n_starts=2),   # thin
    fi_candidate("home", "Astros", "NRFI", 0.70, n_starts=15),     # thick
]
out3 = gp._build_combined_nrfi(thin_one_side)
c3 = out3[0]
check(c3["sample_n"] == 2, "sample_n is the MINIMUM of the two starters' starts (2), "
      "not their average or the max", f"got {c3['sample_n']}")
check(c3["score"] <= 55, "with min(n)=2 (under 3), the score is capped at 55 regardless "
      "of how strong the combined probability looks", f"got {c3['score']}")
check(any("Thin" in w for w in c3["watchouts"]),
      "the thin-sample-on-either-side case carries an explicit watchout")

head("7. HONEST EXPECTATION: with two starters each already shrunk close to the league "
     "rate, the combined number sits close to a coinflip, not an inflated 'strong lean'")

both_thin_neutral = [
    fi_candidate("away", "Athletics", "NRFI", 1 - gp.LEAGUE_YRFI_RATE, n_starts=1),
    fi_candidate("home", "Astros", "NRFI", 1 - gp.LEAGUE_YRFI_RATE, n_starts=1),
]
out4 = gp._build_combined_nrfi(both_thin_neutral)
c4 = out4[0]
check(abs(c4["hit_probability"] - 0.5) < 0.02,
      "two starters both reading exactly at the league-average scoreless rate combine "
      "to a number within 2 points of a genuine coinflip, matching the function's own "
      "documented expectation (~0.498)", f"got {c4['hit_probability']}")

head("8. base_rate reflects the SELECTED lean's own baseline, not always the NRFI baseline")

check(abs(c["base_rate"] - round((1 - gp.LEAGUE_YRFI_RATE) ** 2, 4)) < 1e-4,
      "an NRFI-leaning combined candidate's base_rate is (1-LEAGUE_YRFI_RATE)^2")

yrfi_lean_combined = [
    fi_candidate("away", "Athletics", "YRFI", 0.90, n_starts=10),
    fi_candidate("home", "Astros", "YRFI", 0.90, n_starts=10),
]
out5 = gp._build_combined_nrfi(yrfi_lean_combined)
c5 = out5[0]
check(c5["lean"] == "YRFI",
      "two strongly YRFI-leaning starters combine to an overall YRFI lean", f"got {c5['lean']}")
check(abs(c5["base_rate"] - round(1 - (1 - gp.LEAGUE_YRFI_RATE) ** 2, 4)) < 1e-4,
      "a YRFI-leaning combined candidate's base_rate correctly flips to "
      "1-(1-LEAGUE_YRFI_RATE)^2, not the NRFI-side base rate")

head("9. multiple independent games each produce their own combined candidate")

game_a = [fi_candidate("away", "Athletics", "NRFI", 0.75, n_starts=10),
          fi_candidate("home", "Astros", "NRFI", 0.70, n_starts=10)]
game_b_pk = 900002
game_b = [dict(fi_candidate("away", "Rangers", "NRFI", 0.65, n_starts=10), game_pk=game_b_pk),
          dict(fi_candidate("home", "Mariners", "NRFI", 0.68, n_starts=10), game_pk=game_b_pk)]
out6 = gp._build_combined_nrfi(game_a + game_b)
check(len(out6) == 2, "two separate complete games produce two separate combined candidates")
check({c["game_pk"] for c in out6} == {GAME_PK, game_b_pk},
      "each combined candidate carries its own real game_pk")

head("10. an empty candidate list returns an empty list")

check(gp._build_combined_nrfi([]) == [], "no candidates at all returns an empty list")

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
