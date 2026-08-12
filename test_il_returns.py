#!/usr/bin/env python3
"""test_il_returns.py — checks mlb_sources.fetch_recent_il_returns' and
fetch_recent_callups' transaction parsing against real and adversarial
examples. Both share one theme (a player's recent situation change makes
his season/rolling stats thinner than they look) and one underlying fetch
(_fetch_transactions), so they're tested together here.

MLB's transactions endpoint has no dedicated "injured list" transaction
type -- both IL placements and IL returns come back as the same typeDesc
("Status Change"), distinguished only by parsing the free-text description.
Getting the pattern wrong in either direction is a real risk: too loose and
a healthy player (activated off paternity/bereavement/restricted list) gets
falsely flagged as injury-fresh; too strict and real returns get missed
silently. This locks in the pattern against real examples pulled live
before it was written (see the function's own docstring) plus adversarial
ones designed to catch exactly those two failure modes. "Recalled" (minor-
league call-ups) is its own clean typeDesc and needs no such parsing, but
is covered here too since it shares the fetch helper.

    /tmp/mlbvenv/bin/python3 test_il_returns.py
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


import mlb_daily as m
import mlb_sources as src

TODAY = "2026-08-12"


def _txn(pid, date, desc, type_desc="Status Change"):
    return {"person": {"id": pid, "fullName": f"Player {pid}"}, "date": date,
            "typeDesc": type_desc, "description": desc}


head("1. real examples (pulled live before this function was written)")

TXNS = [
    # Real activations -- must match.
    _txn(1, "2026-07-22", "New York Yankees activated LHP Max Fried from the 15-day injured list."),
    _txn(2, "2026-07-25", "Athletics activated 1B Nick Kurtz from the 10-day injured list."),
    # Real non-injury activation -- must NOT match (no "injured list" clause).
    _txn(3, "2026-07-27", "Boston Red Sox activated 3B Curtis Mead."),
    # A PLACEMENT onto the IL (going out, not coming back) -- must NOT match.
    _txn(4, "2026-07-25", "Houston Astros placed RHP Spencer Arrighetti on the 15-day injured list. "
                          "Right foot nerve irritation."),
    # A trade, unrelated typeDesc entirely -- must NOT match.
    {"person": {"id": 5}, "date": "2026-07-26",
     "typeDesc": "Trade", "description": "Traded RHP Someone to the Mets."},
]

with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = {"transactions": TXNS}
    mock_get.return_value.raise_for_status = lambda: None
    result = src.fetch_recent_il_returns(days_back=21)

check(1 in result, "real 'activated ... from the 15-day injured list' matches")
check(2 in result, "real 'activated ... from the 10-day injured list' matches")
check(3 not in result,
      "'activated 3B Curtis Mead.' (no injured-list clause -- paternity/bereavement/"
      "restricted-list return) does NOT falsely match",
      f"got {result.get(3)}")
check(4 not in result,
      "a PLACEMENT onto the IL (going out) does NOT match as a return",
      f"got {result.get(4)}")
check(5 not in result, "a Trade transaction (wrong typeDesc) does not match")

check(result.get(1, {}).get("il_days") == 15, "il_days correctly parsed as 15",
      f"got {result.get(1, {}).get('il_days')}")
check(result.get(1, {}).get("days_ago") == 21,
      f"days_ago correctly computed as 21 (2026-07-22 to {TODAY})",
      f"got {result.get(1, {}).get('days_ago')}")

head("2. most-recent-activation-wins, when a player has multiple in the window")

TXNS2 = [
    _txn(10, "2026-07-26", "Team X activated RHP Someone from the 10-day injured list."),
    _txn(10, "2026-08-05", "Team X activated RHP Someone from the 10-day injured list."),  # re-injured, activated again
]
with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = {"transactions": TXNS2}
    mock_get.return_value.raise_for_status = lambda: None
    result2 = src.fetch_recent_il_returns(days_back=21)
check(result2.get(10, {}).get("date") == "2026-08-05",
      "when a player has two activations in the window, the MOST RECENT one wins",
      f"got {result2.get(10, {}).get('date')}")

head("3. adversarial: a description that mentions 'injured list' but isn't an activation")

TXNS3 = [
    _txn(20, "2026-08-01", "Team Y transferred RHP Someone to the 60-day injured list from the 15-day injured list."),
]
with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = {"transactions": TXNS3}
    mock_get.return_value.raise_for_status = lambda: None
    result3 = src.fetch_recent_il_returns(days_back=21)
check(20 not in result3,
      "an IL-to-IL transfer (still injured, not activated) does not match",
      f"got {result3.get(20)}")

head("4. fetch_recent_callups -- a clean dedicated typeDesc, no parsing needed")

TXNS4 = [
    _txn(30, "2026-08-05", "Cleveland Guardians recalled LHP Will Dion from Columbus Clippers.",
         type_desc="Recalled"),
    # Must NOT be picked up by fetch_recent_callups -- different typeDesc,
    # even though the word "recalled" isn't in play here at all; this checks
    # the filter is on typeDesc, not a text search.
    _txn(31, "2026-08-06", "New York Yankees activated LHP Max Fried from the 15-day injured list."),
    # Two recalls for the same player -- most recent should win, same as IL.
    _txn(32, "2026-07-20", "Team Z recalled RHP Someone from Triple-A.", type_desc="Recalled"),
    _txn(32, "2026-08-10", "Team Z recalled RHP Someone from Triple-A.", type_desc="Recalled"),
]
with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = {"transactions": TXNS4}
    mock_get.return_value.raise_for_status = lambda: None
    callups = src.fetch_recent_callups(days_back=21)

check(30 in callups, "a real 'Recalled' transaction is picked up",
      f"got {callups.get(30)}")
check(31 not in callups,
      "a 'Status Change' (IL activation) transaction is NOT picked up as a callup",
      f"got {callups.get(31)}")
check(callups.get(32, {}).get("date") == "2026-08-10",
      "most-recent recall wins when a player has two in the window",
      f"got {callups.get(32, {}).get('date')}")
check(callups.get(30, {}).get("days_ago") == 7,
      f"days_ago correctly computed as 7 (2026-08-05 to {TODAY})",
      f"got {callups.get(30, {}).get('days_ago')}")

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
