#!/usr/bin/env python3
"""test_correlation.py — checks correlation.py's classification rules against
hand-built cases with a known right answer, and against the real board.

    /tmp/mlbvenv/bin/python3 test_correlation.py
    python3 test_correlation.py -v
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import correlation as corr

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


def batter(name, team, matchup, game_pk, stat, player_id=None):
    return {"name": name, "team": team, "matchup": matchup, "game_pk": game_pk,
            "type": "batter", "player_id": player_id or name,
            "projection": {"stat": stat}}


def pitcher(name, team, matchup, game_pk, side, stat, player_id=None):
    return {"name": name, "team": team, "matchup": matchup, "game_pk": game_pk,
            "type": "pitcher", "side": side, "player_id": player_id or name,
            "projection": {"stat": stat}}


head("1. structural rules, hand-built cases")

a = batter("A. Batter", "Mets", "Mets @ Pirates", 1, "hits")
b = batter("B. Batter", "Pirates", "Mets @ Pirates", 1, "hits")
c = batter("C. Batter", "Dodgers", "Dodgers @ Giants", 2, "hits")
check(corr.classify(a, c).label == "independent",
      "different games classify independent")

d = batter("D. Batter", "Mets", "Mets @ Pirates", 1, "total_bases")
check(corr.classify(a, d).label == "positive",
      "same team, same game, different players classifies positive")

e = dict(a, projection={"stat": "total_bases"})
check(corr.classify(a, e).label == "redundant",
      "same player, overlapping families (hits + total_bases) classifies redundant",
      f"got {corr.classify(a, e).label}")

f = dict(a, projection={"stat": "stolen_base"})
v = corr.classify(a, f)
check(v.label == "redundant" or v.label == "positive",
      "same player, non-overlapping-family stolen_base is not classified negative",
      f"got {v.label}")

# Bug found during a sweep: runs/rbis are hits_runs_rbis' own component
# stats (h+r+rbi), and hits_runs_rbis was already in the overlapping set --
# runs and rbis themselves were not, so a same-player Runs+RBIs pair was
# scored merely "positive" instead of "redundant".
runs_pick = dict(a, projection={"stat": "runs"})
rbis_pick = dict(a, projection={"stat": "rbis"})
check(corr.classify(runs_pick, rbis_pick).label == "redundant",
      "same player, runs + rbis classifies redundant (both are hits_runs_rbis' own components)",
      f"got {corr.classify(runs_pick, rbis_pick).label}")

# Pitcher facing the batter's team -- strikeouts and first_inning_run should
# both count as "works against this lineup".
p_home = pitcher("Home SP", "Pirates", "Mets @ Pirates", 1, "home", "strikeouts")
check(corr.classify(p_home, a).label == "negative",
      "home pitcher's strikeout prop vs a batter on the AWAY (facing) team is negative",
      f"got {corr.classify(p_home, a).label}")

p_away = pitcher("Away SP", "Mets", "Mets @ Pirates", 1, "away", "first_inning_run")
check(corr.classify(p_away, b).label == "negative",
      "away pitcher's first_inning_run prop vs a batter on the HOME (facing) team is negative",
      f"got {corr.classify(p_away, b).label}")

# Bug found during a sweep: pitcher_outs (Outs Recorded) wasn't in
# _PITCHER_STATS_OPPOSE_HITTERS even though the reasoning is identical to
# strikeouts -- every extra out he gets is an at-bat the facing lineup did
# not turn into a hit.
p_outs = pitcher("Home SP", "Pirates", "Mets @ Pirates", 1, "home", "pitcher_outs")
check(corr.classify(p_outs, a).label == "negative",
      "home pitcher's pitcher_outs prop vs a batter on the facing team is negative",
      f"got {corr.classify(p_outs, a).label}")

# A pitcher's strikeout prop should NOT be flagged negative against a batter
# on his OWN team (they're on the same side, not facing off).
p_home_own_team = pitcher("Home SP", "Pirates", "Mets @ Pirates", 1, "home", "strikeouts")
same_team_batter = batter("Teammate", "Pirates", "Mets @ Pirates", 1, "hits")
v2 = corr.classify(p_home_own_team, same_team_batter)
check(v2.label != "negative",
      "pitcher's strikeout prop vs his OWN team's batter is not negative",
      f"got {v2.label}")

# Bug found during this audit: combined_strikeouts (score_combined_strikeouts,
# "type": "pitcher_combo", team explicitly None) wasn't covered by the
# pitcher-vs-hitter check at all, since that check required "type": "pitcher".
# A "2 combined strikeouts, 2 hits" parlay request would have classified the
# pair "independent" -- verified live before this fix -- exactly the "K prop
# + opposing hitter" case this module exists to catch. Both teams' hitters
# are opposed (no `team` to match against, since the prop is both starters
# combined), so this should fire against a batter on EITHER side.
combo = dict(name="SP A & SP B", team=None, matchup="Mets @ Pirates", game_pk=1,
            type="pitcher_combo", player_id="sp_a",
            projection={"stat": "combined_strikeouts"})
for team_name, team_batter in (("away", a), ("home", b)):
    v_combo = corr.classify(combo, team_batter)
    check(v_combo.label == "negative",
          f"combined_strikeouts vs a {team_name}-side batter in the same game is negative",
          f"got {v_combo.label}")

# Bug found during this audit: combined_strikeouts' own player_id carries
# only the AWAY starter's id (persistence needs a single real id), so a
# solo strikeouts pick on that same away starter fell into the same_player
# branch and scored merely "positive" (not "redundant" -- combined_
# strikeouts isn't in _OVERLAPPING_BATTER_FAMILIES, which is batter-only),
# while a solo pick on the HOME starter -- whose id the combo never carries
# -- matched no player_id at all and fell through to "independent". Both
# wrong the same way: a starter's own strikeout total is a strict subset of
# the combined total.
combo_with_ids = dict(combo, combo_player_ids=["sp_a", "sp_b"])
solo_away = pitcher("SP A", "Mets", "Mets @ Pirates", 1, "away", "strikeouts", player_id="sp_a")
solo_home = pitcher("SP B", "Pirates", "Mets @ Pirates", 1, "home", "strikeouts", player_id="sp_b")
for label, solo in (("away starter (same id combo carries)", solo_away),
                    ("home starter (id combo never carries)", solo_home)):
    v_solo = corr.classify(combo_with_ids, solo)
    check(v_solo.label == "redundant",
          f"combined_strikeouts vs a solo strikeouts pick on its own {label} is redundant",
          f"got {v_solo.label}")

# A solo strikeouts pick on a pitcher who is NOT one of the combo's two
# starters must not be flagged -- only the actual overlapping pair.
unrelated = pitcher("SP C", "Dodgers", "Dodgers @ Giants", 2, "away", "strikeouts", player_id="sp_c")
v_unrelated = corr.classify(combo_with_ids, unrelated)
check(v_unrelated.label == "independent",
      "combined_strikeouts vs an unrelated pitcher in a different game is independent",
      f"got {v_unrelated.label}")

# Same game, opposing teams, no pitcher-vs-hitter relationship -> independent,
# not a fabricated negative.
g = batter("G. Batter", "Pirates", "Mets @ Pirates", 1, "home_runs")
v3 = corr.classify(a, g)
check(v3.label == "independent",
      "same game, opposing teams, two position players -> independent (no fabricated call)",
      f"got {v3.label}")

head("2. screen_parlay")

ok, violations = corr.screen_parlay([a, d])
check(ok, "two same-team same-game props with no violations screens OK")

ok2, violations2 = corr.screen_parlay([a, e])
check(not ok2 and len(violations2) == 1,
      "a redundant pair is caught by screen_parlay",
      f"ok={ok2} violations={len(violations2)}")

ok3, violations3 = corr.screen_parlay([p_home, a])
check(not ok3, "a negative-correlation pair is caught by screen_parlay")

ok4, violations4 = corr.screen_parlay([a, d, c, p_home])
# p_home (Pirates pitcher facing the Mets) correctly flags negative against
# BOTH Mets batters (a and d), not just one -- they're both on the team he's
# facing. Only the positive (a,d) and independent (c) pairs stay clean.
check(not ok4 and len(violations4) == 2,
      "a 4-leg parlay flags both real negative pairs, not the positive (a,d) "
      "or independent (c) ones",
      f"violations={len(violations4)}")

head("3. sanity against the real board, if it exists")
try:
    import json
    d_board = json.load(open("output/picks_2026-08-07.json"))
    picks = d_board.get("picks", [])
    if picks:
        import itertools
        labels = set()
        for x, y in itertools.combinations(picks, 2):
            labels.add(corr.classify(x, y).label)
        check(labels.issubset({"independent", "positive", "negative", "redundant"}),
              "every real pair on tonight's board gets a valid label",
              f"labels seen: {labels}")
    else:
        print("  (no picks on today's board -- skipped)")
except (FileNotFoundError, json.JSONDecodeError):
    print("  (no board file available -- skipped)")

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
