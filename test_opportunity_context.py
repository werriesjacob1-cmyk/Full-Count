#!/usr/bin/env python3
"""test_opportunity_context.py — direct coverage for grade_results.is_final()
and grade_results.opportunity_context(). Both had zero DIRECT test coverage:
test_grade_results.py exercises grade_pick() but mocks opportunity_context()
out entirely on every call (mock.patch.object(gr, "opportunity_context",
return_value={...})), so its real fairness logic has never actually been
tested.

This matters specifically because of what opportunity_context is FOR, per
its own docstring: distinguishing a genuine miss from a pick that never got
a real chance to be right (a pinch-hitter making one out vs a starter going
0-for-5). Get this wrong and the accuracy record blames signals for
outcomes they never had a chance to influence -- exactly the kind of
silent corruption that would mislead every future decision about which
signals to trust.

_game_innings (network) is monkeypatched throughout to avoid real calls.

    /tmp/mlbvenv/bin/python3 test_opportunity_context.py
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


import grade_results as gr

gr._game_innings = lambda game_pk: 9  # a normal, full 9-inning game unless overridden

head("== is_final ==")
head("1. real MLB status shapes")

check(gr.is_final({"codedGameState": "F", "detailedState": "Final"}) is True,
      "codedGameState='F' + detailedState='Final' is final")
check(gr.is_final({"codedGameState": "O", "detailedState": "Game Over"}) is True,
      "codedGameState='O' (Game Over) is also treated as final")
check(gr.is_final({"codedGameState": "I", "detailedState": "In Progress"}) is False,
      "an in-progress game is not final")
check(gr.is_final({"codedGameState": "S", "detailedState": "Scheduled"}) is False,
      "a scheduled (not yet started) game is not final")

head("2. the text fallback: 'final'/'completed' anywhere in detailedState, case-insensitive, "
     "even with an unrecognized codedGameState")

check(gr.is_final({"codedGameState": "X", "detailedState": "Completed Early"}) is True,
      "an unrecognized coded state still counts as final if detailedState says 'Completed'")
check(gr.is_final({"codedGameState": "X", "detailedState": "FINAL: TIED"}) is True,
      "case-insensitive: 'FINAL' in caps still matches")

head("3. missing/empty status is honestly not final, never a crash")

check(gr.is_final(None) is False, "status=None is not final")
check(gr.is_final({}) is False, "an empty status dict is not final")

head("== opportunity_context ==")
head("1. nrfi_combined always gets a full/fair test -- the first inning always happens")

ctx = gr.opportunity_context({"projection": {"stat": "nrfi_combined"}}, None, 1001)
check(ctx["fair_test"] is True and "full" in ctx["opportunity"],
      "nrfi_combined is always a fair test regardless of row/game_pk", f"got {ctx}")

head("2. first_inning_run (pitcher side) is also always a fair test, same reasoning")

ctx2 = gr.opportunity_context({"type": "pitcher", "projection": {"stat": "first_inning_run"}},
                              {"ip": "0.1"}, 1001)  # even a 0.1 IP outing
check(ctx2["fair_test"] is True and "full" in ctx2["opportunity"],
      "a first_inning_run pick is a fair test even for a pitcher pulled after 0.1 IP -- "
      "the first inning already happened regardless of how the rest of his start went",
      f"got {ctx2}")

head("3. a pitcher strikeout pick pulled early (IP < 4.0) is flagged NOT a fair test")

short_start = gr.opportunity_context({"type": "pitcher", "projection": {"stat": "strikeouts"}},
                                     {"ip": "2.1"}, 1001)
check(short_start["fair_test"] is False,
      "a starter pulled after only 2.1 IP did not get a fair test for a strikeout prop",
      f"got {short_start}")
check("2.1" in short_start["opportunity"], "the real IP figure is named in the explanation")

head("4. a pitcher who completed a real start (IP >= 4.0) IS a fair test")

full_start = gr.opportunity_context({"type": "pitcher", "projection": {"stat": "strikeouts"}},
                                    {"ip": "6.0"}, 1001)
check(full_start["fair_test"] is True, "a 6.0 IP start counts as a full, fair test")

head("5. a pitcher with no IP recorded at all (row present but blank) is honestly 'unknown', "
     "not silently guessed either way")

no_ip = gr.opportunity_context({"type": "pitcher", "projection": {"stat": "strikeouts"}},
                               {}, 1001)
check(no_ip["fair_test"] is None,
      "no IP data at all reports fair_test=None (unknown), never a guessed True/False",
      f"got {no_ip}")

head("6. THE EXACT BOUNDARY: the docstring's own example -- 8.5 innings pitched by the "
     "home team is normal (they don't bat in the bottom 9th when already leading), "
     "so a game_innings reading of 8 is NOT automatically flagged as shortened, but a "
     "real 7-inning doubleheader game genuinely is")

gr._game_innings = lambda game_pk: 8
ctx_8 = gr.opportunity_context({"type": "pitcher", "projection": {"stat": "strikeouts"}},
                               {"ip": "6.0"}, 1001)
check(ctx_8["shortened_game"] is False,
      "8 innings (the home team not batting in the bottom 9th while leading) is NOT "
      "flagged as a shortened game -- the threshold is strictly < 8", f"got {ctx_8}")

gr._game_innings = lambda game_pk: 7
ctx_7 = gr.opportunity_context({"type": "pitcher", "projection": {"stat": "strikeouts"}},
                               {"ip": "6.0"}, 1001)
check(ctx_7["shortened_game"] is True,
      "7 innings (a real doubleheader-length or rain-shortened game) IS flagged as "
      "shortened", f"got {ctx_7}")
gr._game_innings = lambda game_pk: 9

head("7. a batter who never appears in the box score at all gets 'none'")

no_row = gr.opportunity_context({"type": "batter", "projection": {"stat": "hits"}}, None, 1001)
check(no_row["fair_test"] is False and "did not appear" in no_row["opportunity"],
      "a batter with no box score row at all (scratched/DNP) is flagged as no opportunity "
      "at all", f"got {no_row}")

head("8. a batter who appeared but recorded zero plate appearances (ab=0, bb=0) is flagged")

zero_pa = gr.opportunity_context({"type": "batter", "projection": {"stat": "hits"}},
                                 {"ab": "0", "bb": "0"}, 1001)
check(zero_pa["fair_test"] is False and "no plate appearance" in zero_pa["opportunity"],
      "a batter who appeared in the box score but with 0 AB and 0 BB (e.g. pinch-ran, "
      "defensive replacement) is flagged as not a fair test", f"got {zero_pa}")

head("9. THE SUBSTITUTE CASE: a pinch hitter with only 1-2 PA carries a DIFFERENT, more "
     "specific reason than a generic thin-PA starter")

sub_1pa = gr.opportunity_context({"type": "batter", "projection": {"stat": "hits"}},
                                 {"ab": "1", "bb": "0", "substitution": True}, 1001)
check(sub_1pa["fair_test"] is False and "substitute" in sub_1pa["opportunity"],
      "a substitute batter with only 1 PA gets the substitute-specific reason (the pick "
      "assumed a starter's workload), not the generic thin-PA one", f"got {sub_1pa}")

head("10. a NON-substitute starter with only 1-2 PA (early exit, rain, pinch-hit-for) still "
     "gets flagged, with the GENERIC reason since he wasn't a substitute himself")

starter_thin = gr.opportunity_context({"type": "batter", "projection": {"stat": "hits"}},
                                      {"ab": "2", "bb": "0", "substitution": False}, 1001)
check(starter_thin["fair_test"] is False and "substitute" not in starter_thin["opportunity"],
      "a starter (not a sub) who only got 2 PA is still flagged unfair, but with the "
      "generic early-exit/rain/pinch-hit-for reason, not the substitute-specific one",
      f"got {starter_thin}")

head("11. a real, normal plate-appearance count (3+ PA) for a non-substitute batter IS a "
     "fair test")

normal_game = gr.opportunity_context({"type": "batter", "projection": {"stat": "hits"}},
                                     {"ab": "4", "bb": "1", "substitution": False}, 1001)
check(normal_game["fair_test"] is True and "full" in normal_game["opportunity"],
      "a batter with 5 real plate appearances (4 AB + 1 BB) got a full, fair test",
      f"got {normal_game}")

head("12. pa is estimated from AB+BB only (HBP/SF not exposed per-row) -- verify that "
     "documented approximation is really what's computed")

check(normal_game["actual_pa_est"] == 5, "actual_pa_est is exactly ab+bb (4+1=5), the "
      "documented approximation", f"got {normal_game['actual_pa_est']}")

head("13. battingOrder is parsed from MLB's packed integer format (divided by 100)")

with_order = gr.opportunity_context({"type": "batter", "projection": {"stat": "hits"}},
                                    {"ab": "4", "bb": "0", "battingOrder": "300"}, 1001)
check(with_order.get("batting_order") == 3,
      "MLB's packed battingOrder=300 correctly decodes to lineup slot 3",
      f"got {with_order.get('batting_order')}")

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
