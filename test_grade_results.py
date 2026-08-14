#!/usr/bin/env python3
"""test_grade_results.py — direct coverage for grade_results.grade_pick(),
the function that decides whether every pick on the MAIN board actually won
or lost. Had zero test coverage despite being exactly the kind of function
this project has repeatedly found real, silent grading bugs in (its own
comments document several: total_bases graded against the wrong threshold
for "2+ Total Bases" picks, hard_hit_105 always coming back fair_test=False,
first_inning_run picks stuck ungraded because side/lean weren't on the
pick). This locks in the fixed behavior for every branch so none of those
regress, and exercises the branches most likely to hide the next one.

    /tmp/mlbvenv/bin/python3 test_grade_results.py
"""
import sys
import unittest.mock as mock

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


import grade_results as gr

FINAL = {"codedGameState": "F", "detailedState": "Final"}
NOT_FINAL = {"codedGameState": "I", "detailedState": "In Progress"}
_gpk = [1000]


def new_pk():
    _gpk[0] += 1
    return _gpk[0]


def base_pick(**over):
    p = {"game_pk": new_pk(), "player_id": 12345, "type": "batter",
         "projection": {"stat": "hits", "value": 1.5, "needs": 2}}
    p.update(over)
    return p


head("1. top-level guards")

p = {"game_pk": None, "player_id": 1}
r = gr.grade_pick(p, {})
check(r["grade"] == "ungraded" and "missing" in r["reason"], "missing game_pk -> ungraded")

p2 = base_pick()
r2 = gr.grade_pick(p2, {p2["game_pk"]: NOT_FINAL})
check(r2["grade"] == "ungraded" and "not final" in r2["reason"], "game not Final -> ungraded, not graded as a loss")

head("2. combined_strikeouts")

with mock.patch.object(gr, "get_box_line") as mgb, \
     mock.patch.object(gr, "opportunity_context", return_value={}):
    pk = new_pk()
    p = base_pick(game_pk=pk, projection={"stat": "combined_strikeouts", "needs": 9},
                  combo_player_ids=[111, 222])
    mgb.side_effect = [({"k": 5}, None), ({"k": 5}, None)]  # 5+5=10 >= 9
    r = gr.grade_pick(p, {pk: FINAL})
    check(r["grade"] == "hit" and r["actual"] == 10.0,
          "combined_strikeouts sums BOTH starters' real K counts", f"got {r.get('actual')} / {r['grade']}")

    pk2 = new_pk()
    p2 = base_pick(game_pk=pk2, projection={"stat": "combined_strikeouts", "needs": 9},
                   combo_player_ids=[111, 222])
    mgb.side_effect = [({"k": 3}, None), ({"k": 4}, None)]  # 3+4=7 < 9
    r2 = gr.grade_pick(p2, {pk2: FINAL})
    check(r2["grade"] == "miss", "combined_strikeouts: 7 < needs=9 grades a miss")

pk3 = new_pk()
p3 = base_pick(game_pk=pk3, projection={"stat": "combined_strikeouts", "needs": 9},
               combo_player_ids=[111])  # only one id -- malformed
r3 = gr.grade_pick(p3, {pk3: FINAL})
check(r3["grade"] == "ungraded" and "combo_player_ids" in r3["reason"],
      "combined_strikeouts with a malformed combo_player_ids (len != 2) -> ungraded, not a false loss")

head("3. hard_hit_105 / hard_hit_110")

pk4 = new_pk()
p4 = base_pick(game_pk=pk4, projection={"stat": "hard_hit_105"})
r4 = gr.grade_pick(p4, {pk4: FINAL}, date=None)
check(r4["grade"] == "ungraded" and "date" in r4["reason"],
      "hard_hit_105 with no date supplied -> ungraded (Statcast lookup needs the date)")

# REAL BUG FIXED 2026-08-14: grading used to key off peak exit velocity
# alone (_date_batter_peak_ev, now removed) -- a hard-hit groundout or
# single graded identically to a home run at the same speed. FanDuel's
# own "Full details" text for this market reads "Laser = HR with
# Specified MPH Exit Velocity" -- confirmed against real live market
# odds the same night (Tatis +650, Adell +900, both far too long to be
# "any hard-hit ball"). Now grades off _date_batter_hr_ev(date,
# threshold), which returns True/False -- did he hit a HOME RUN at that
# exit velocity that game -- mirroring _date_batter_moonshot's identical
# shape.
with mock.patch.object(gr, "_date_batter_hr_ev") as mev, \
     mock.patch.object(gr, "get_box_line", return_value=({"ab": "4"}, None)), \
     mock.patch.object(gr, "opportunity_context", return_value={}):
    pk5 = new_pk()
    p5 = base_pick(game_pk=pk5, player_id=999, projection={"stat": "hard_hit_105"})
    mev.return_value = {(999, pk5): True}
    r5 = gr.grade_pick(p5, {pk5: FINAL}, date="2026-08-06")
    check(r5["grade"] == "hit" and r5["actual"] is True,
          "hard_hit_105: a real HR at 105+ mph exit velocity grades a hit", f"got {r5}")
    check(mev.call_args[0] == ("2026-08-06", 105),
          "_date_batter_hr_ev called with the real date and the 105 threshold",
          f"got {mev.call_args}")

    pk6 = new_pk()
    p6 = base_pick(game_pk=pk6, player_id=999, projection={"stat": "hard_hit_110"})
    mev.return_value = {(999, pk6): False}
    r6 = gr.grade_pick(p6, {pk6: FINAL}, date="2026-08-06")
    check(r6["grade"] == "miss",
          "hard_hit_110: no qualifying HR that game (even if he hit hard-hit non-HRs) grades a miss")
    check(mev.call_args[0] == ("2026-08-06", 110), "called with the 110 threshold this time",
          f"got {mev.call_args}")

    pk7 = new_pk()
    p7 = base_pick(game_pk=pk7, player_id=888, projection={"stat": "hard_hit_105"})
    mev.return_value = {}  # no Statcast row for this player/game at all
    r7 = gr.grade_pick(p7, {pk7: FINAL}, date="2026-08-06")
    check(r7["grade"] == "ungraded" and "Statcast" in r7["reason"],
          "hard_hit_105 with no batted-ball Statcast data -> ungraded, not a false miss")

head("4. first_inning_run -- side/lean recovered from matchup+team+prop text")

with mock.patch.object(gr, "fetch_first_inning_linescore") as mls, \
     mock.patch.object(gr, "opportunity_context", return_value={}):
    pk8 = new_pk()
    # side/lean deliberately ABSENT from the pick -- this is the real fix's
    # own scenario (picks written before side/lean were persisted).
    p8 = base_pick(game_pk=pk8, projection={"stat": "first_inning_run"},
                   matchup="Athletics @ Astros", team="Athletics",
                   prop="NRFI lean (his starts)")
    mls.return_value = {"away": {"runs": 0}, "home": {"runs": 0}}
    r8 = gr.grade_pick(p8, {pk8: FINAL})
    # team == away half of matchup -> side="away" -> runs_against = home's runs = 0 -> NRFI actual, lean NRFI -> hit
    check(r8["grade"] == "hit",
          "first_inning_run: side recovered from team-vs-matchup, lean recovered from prop text -> correct hit",
          f"got {r8}")

    pk9 = new_pk()
    p9 = base_pick(game_pk=pk9, projection={"stat": "first_inning_run"},
                   matchup="Athletics @ Astros", team="Astros",
                   prop="YRFI lean (his starts)")
    mls.return_value = {"away": {"runs": 1}, "home": {"runs": 0}}
    r9 = gr.grade_pick(p9, {pk9: FINAL})
    # team==home half -> side="home" -> runs_against = away's runs = 1 -> actual_yrfi True, lean YRFI -> hit
    check(r9["grade"] == "hit", "first_inning_run: home-side YRFI lean against a real away-half run -> hit",
          f"got {r9}")

    pk10 = new_pk()
    p10 = base_pick(game_pk=pk10, projection={"stat": "first_inning_run"},
                    matchup="Athletics @ Astros", team="Athletics",
                    prop="NRFI lean (his starts)")
    mls.return_value = {"away": {"runs": 0}, "home": {"runs": 2}}
    r10 = gr.grade_pick(p10, {pk10: FINAL})
    # side="away" -> runs_against = home's runs = 2 -> actual_yrfi True, lean NRFI -> miss
    check(r10["grade"] == "miss", "first_inning_run: NRFI lean against a real run allowed -> miss", f"got {r10}")

    pk11 = new_pk()
    p11 = base_pick(game_pk=pk11, projection={"stat": "first_inning_run"},
                    matchup="Athletics @ Astros", team="Athletics", prop="NRFI lean")
    mls.return_value = None
    r11 = gr.grade_pick(p11, {pk11: FINAL})
    check(r11["grade"] == "ungraded", "first_inning_run with no linescore available -> ungraded")

head("5. nrfi_combined -- both halves must match the lean")

with mock.patch.object(gr, "fetch_first_inning_linescore") as mls, \
     mock.patch.object(gr, "opportunity_context", return_value={}):
    pk12 = new_pk()
    p12 = base_pick(game_pk=pk12, projection={"stat": "nrfi_combined"}, lean="NRFI")
    mls.return_value = {"away": {"runs": 0}, "home": {"runs": 0}}
    r12 = gr.grade_pick(p12, {pk12: FINAL})
    check(r12["grade"] == "hit", "nrfi_combined: both halves scoreless, NRFI lean -> hit")

    pk13 = new_pk()
    p13 = base_pick(game_pk=pk13, projection={"stat": "nrfi_combined"}, lean="NRFI")
    mls.return_value = {"away": {"runs": 0}, "home": {"runs": 1}}
    r13 = gr.grade_pick(p13, {pk13: FINAL})
    check(r13["grade"] == "miss",
          "nrfi_combined: ONE half scores, NRFI lean -> miss (needs BOTH scoreless, not just its own side)")

    pk14 = new_pk()
    p14 = base_pick(game_pk=pk14, projection={"stat": "nrfi_combined"}, lean="YRFI")
    mls.return_value = {"away": {"runs": 0}, "home": {"runs": 1}}
    r14 = gr.grade_pick(p14, {pk14: FINAL})
    check(r14["grade"] == "hit", "nrfi_combined: either half scores, YRFI lean -> hit")

head("6. generic per-stat extraction from a real box line")

GENERIC_ROW = {"k": 6, "sb": 1, "bb": 2, "h": 3, "hr": 1, "r": 2, "rbi": 4,
               "doubles": 1, "triples": 0, "ip": "6.2", "ab": "5"}

def _grade_stat(stat, value=1.5, needs=None, prop="", row=None):
    pk = new_pk()
    proj = {"stat": stat, "value": value}
    if needs is not None:
        proj["needs"] = needs
    p = base_pick(game_pk=pk, projection=proj, prop=prop,
                  type="pitcher" if stat in ("strikeouts", "pitcher_outs") else "batter")
    with mock.patch.object(gr, "get_box_line", return_value=(row or GENERIC_ROW, None)), \
         mock.patch.object(gr, "opportunity_context", return_value={}):
        return gr.grade_pick(p, {pk: FINAL})

r = _grade_stat("strikeouts", needs=6)
check(r["actual"] == 6.0 and r["grade"] == "hit", "strikeouts pulls row['k'] directly", f"got {r}")

r = _grade_stat("stolen_base", needs=1)
check(r["actual"] == 1.0 and r["actual_stat"] == "stolen_bases", "stolen_base pulls row['sb']")

r = _grade_stat("walks", needs=2)
check(r["actual"] == 2.0, "walks pulls row['bb']")

r = _grade_stat("home_runs", needs=1)
check(r["actual"] == 1.0, "home_runs pulls row['hr']")

r = _grade_stat("runs", needs=2)
check(r["actual"] == 2.0, "runs pulls row['r']")

r = _grade_stat("rbis", needs=4)
check(r["actual"] == 4.0, "rbis pulls row['rbi']")

r = _grade_stat("hits_runs_rbis", needs=9)
check(r["actual"] == 3.0 + 2.0 + 4.0,
      "hits_runs_rbis is the literal sum (h+r+rbi), including the double-count on a self-driven-in HR",
      f"got {r['actual']}")

r = _grade_stat("singles", needs=1)
# h=3, doubles=1, triples=0, hr=1 -> singles = 3-1-0-1 = 1
check(r["actual"] == 1.0, "singles derived as h - doubles - triples - hr", f"got {r['actual']}")

r = _grade_stat("doubles", needs=1)
check(r["actual"] == 1.0, "doubles pulls row['doubles'] directly")

r = _grade_stat("pitcher_outs", needs=20)
# ip "6.2" -> 6 innings * 3 + 2 partial outs = 20
check(r["actual"] == 20.0, "pitcher_outs converts IP notation (6.2 -> 20 outs, not 6.2*3)", f"got {r['actual']}")

head("7. total_bases: THE REAL BUG THIS FIXED -- routes on the DISPLAYED prop text")

# "Over 1.5 Hits" is tagged stat=total_bases internally but the box must
# grade against literal HITS, not the total-bases formula, because that is
# what the displayed line actually promises.
r = _grade_stat("total_bases", needs=2, prop="Over 1.5 Hits")
check(r["actual_stat"] == "hits" and r["actual"] == 3.0,
      "a 'total_bases'-tagged pick whose PROP TEXT says 'Hits' grades against real hit count, not TB",
      f"got {r}")

# A real "2+ Total Bases" pick must grade against the real TB formula:
# h=3 (1 double, 0 triple, 1 HR -> 1 single) -> 1*1 + 1*2 + 0*3 + 1*4 = 7
r = _grade_stat("total_bases", needs=2, prop="2+ Total Bases")
check(r["actual_stat"] == "total_bases" and r["actual"] == 7.0,
      "a real Total Bases prop grades against the real TB formula (1x1B + 2x2B + 3x3B + 4xHR)",
      f"got {r}")

head("8. threshold: needs-based vs legacy proj-0.5 vs legacy total_bases-fixed-1.5")

# needs present -> threshold = needs - 0.5, independent of `value`.
r = _grade_stat("hits", value=1.5, needs=3, row={"h": 2, "doubles": 0, "triples": 0, "hr": 0})
check(r["grade"] == "miss" and r["threshold"] == 2.5,
      "needs=3 -> threshold 2.5 -- 2 real hits does not clear it", f"got {r}")

# legacy (no needs) total_bases -> fixed threshold 1.5 regardless of `value`.
r = _grade_stat("total_bases", value=3.9, needs=None, prop="2+ Total Bases",
               row={"h": 2, "doubles": 1, "triples": 0, "hr": 0})
# TB = (2-1-0-0)*1 + 1*2 = 3
check(r["threshold"] == 1.5 and r["actual"] == 3.0 and r["grade"] == "hit",
      "legacy (no needs) total_bases always uses the fixed 1.5 threshold, not value-0.5",
      f"got {r}")

# legacy (no needs), non-total_bases -> threshold = value - 0.5.
r = _grade_stat("strikeouts", value=5.5, needs=None, row={"k": 5})
check(r["threshold"] == 5.0 and r["grade"] == "miss",
      "legacy (no needs) strikeouts uses value-0.5 as the threshold", f"got {r}")

head("9. failure paths that must not become false losses")

pk15 = new_pk()
p15 = base_pick(game_pk=pk15, projection={"stat": "hits", "needs": 1})
with mock.patch.object(gr, "get_box_line", return_value=(None, "player not found in box score (scratched or DNP)")), \
     mock.patch.object(gr, "opportunity_context", return_value={"fair_test": False}):
    r15 = gr.grade_pick(p15, {pk15: FINAL})
check(r15["grade"] == "ungraded" and "scratched" in r15["reason"],
      "a scratched player (no box line) grades ungraded, never a loss")

pk16 = new_pk()
p16 = base_pick(game_pk=pk16, projection={"stat": "totally_made_up_market", "needs": 1})
with mock.patch.object(gr, "get_box_line", return_value=({}, None)):
    r16 = gr.grade_pick(p16, {pk16: FINAL})
check(r16["grade"] == "ungraded" and "unrecognized" in r16["reason"],
      "an unrecognized projection stat grades ungraded rather than crashing or defaulting to a loss")

pk17 = new_pk()
p17 = base_pick(game_pk=pk17, projection={"stat": "hits"})  # no "value" key at all -> proj is None
with mock.patch.object(gr, "get_box_line", return_value=({"h": 2, "doubles": 0, "triples": 0, "hr": 0}, None)):
    r17 = gr.grade_pick(p17, {pk17: FINAL})
check(r17["grade"] == "ungraded" and "no projection" in r17["reason"],
      "a pick with no projection value at all grades ungraded, not a fabricated loss")

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
