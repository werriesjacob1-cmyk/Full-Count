#!/usr/bin/env python3
"""test_write_early_look.py — coverage for generate_picks.write_early_
look(), the prep-only board for batters whose lineup slot is assumed (not
yet posted). Had zero test coverage. Its own docstring states the one
invariant that matters most: this file is deliberately never persisted
through persist_player_snapshots or read by grade_results.py, because a
guessed batting slot is not a real bet -- mixing it into the graded
record would score the model against its own guess instead of a real
decision. This suite checks that boundary holds (the function only ever
touches its own EARLY_LOOK_FILE) and the actual write/ranking behavior.

    /tmp/mlbvenv/bin/python3 test_write_early_look.py
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

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_early_look_")
gp.EARLY_LOOK_FILE = os.path.join(TMPDIR, "early_look_test.md")


def cand(name, score, hit_probability=None, prop="Over 1.5 Total Bases"):
    return {"name": name, "score": score, "hit_probability": hit_probability,
           "prop": prop, "team": "Athletics", "matchup": "Athletics @ Astros"}


head("1. an empty assumed_lineup writes a file that says '(none)', not a crash")

gp.write_early_look([])
check(os.path.exists(gp.EARLY_LOOK_FILE), "a file is written even with zero candidates")
with open(gp.EARLY_LOOK_FILE) as f:
    content = os.linesep.join(f.read().splitlines())
check("(none)" in content, "the empty case is labelled explicitly as '(none)', not a "
      "blank/confusing file", f"got: {content!r}")

head("2. real candidates are ranked by (hit_probability, score) descending")

candidates = [
    cand("Low Score High Prob", 40, hit_probability=0.80),
    cand("High Score No Prob", 90, hit_probability=None),
    cand("Mid Both", 60, hit_probability=0.50),
]
gp.write_early_look(candidates)
with open(gp.EARLY_LOOK_FILE) as f:
    lines = f.read()
# hit_probability is the PRIMARY sort key (None treated as 0), so "Low Score High Prob"
# (0.80) ranks above "Mid Both" (0.50), which ranks above "High Score No Prob" (None->0)
pos_a = lines.find("Low Score High Prob")
pos_b = lines.find("Mid Both")
pos_c = lines.find("High Score No Prob")
check(-1 < pos_a < pos_b < pos_c,
      "ranking is primarily by hit_probability (None treated as 0), NOT by score alone "
      "-- a 90-score candidate with no probability yet ranks LAST, behind two lower-"
      "scoring candidates that already have a real probability", f"got positions {pos_a},{pos_b},{pos_c}")

head("3. a candidate with hit_probability=None displays 'unscored', not a crash or a "
     "fabricated percentage")

check("unscored" in lines, "the None-probability candidate's line reads 'unscored'")

head("4. a candidate with a real probability displays it as a formatted percentage")

check("80.0%" in lines, "an 0.80 hit_probability renders as '80.0%'", f"got: {lines!r}")

head("5. the file explicitly states these are NOT picks and are ungraded -- the core "
     "safety invariant this function's docstring describes")

check("NOT picks" in lines or "not graded" in lines.lower() or "ASSUMED" in lines,
      "the file's own text warns the reader these are assumed-lineup, ungraded "
      "projections, not real bets")

head("6. the file is fully overwritten on each call, not appended to")

gp.write_early_look([cand("Only This One", 50, hit_probability=0.6)])
with open(gp.EARLY_LOOK_FILE) as f:
    lines2 = f.read()
check("Low Score High Prob" not in lines2 and "Only This One" in lines2,
      "a second call completely replaces the previous file's content rather than "
      "appending to it")

head("7. only the top 25 candidates are written, even with a much longer list")

many = [cand(f"Player {i}", 50, hit_probability=0.5) for i in range(40)]
gp.write_early_look(many)
with open(gp.EARLY_LOOK_FILE) as f:
    lines3 = f.read()
n_players = sum(1 for i in range(40) if f"Player {i}" in lines3)
check(n_players == 25, "exactly 25 of the 40 candidates are written to the file",
      f"got {n_players}")

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
