#!/usr/bin/env python3
"""test_odds_snapshot.py — direct coverage for odds_snapshot.fetch_snapshot(),
the hourly capture that is the ONLY way this project can ever see market line
movement (its own docstring: "impossible to recover later"). Had zero test
coverage. A silent extraction bug here doesn't cause a visible failure --
it just quietly corrupts or drops data that can never be re-captured.

    /tmp/mlbvenv/bin/python3 test_odds_snapshot.py
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


import odds_snapshot as osn

REAL_PAYLOAD = {"games": [
    {"id": 1, "start_time": "2026-08-06T23:00:00Z", "status": "scheduled",
     "teams": [{"id": 100, "full_name": "Houston Astros"},
               {"id": 200, "full_name": "Texas Rangers"}],
     "markets": {"15": {"event": {
         "moneyline": [
             {"team_id": 100, "side": "home", "value": None, "odds": -140,
              "is_live": False, "line_status": "normal",
              "bet_info": {"tickets": {"percent": 62}, "money": {"percent": 71}}},
             {"team_id": 200, "side": "away", "value": None, "odds": 120,
              "is_live": False, "line_status": "normal",
              "bet_info": {"tickets": {"percent": 38}, "money": {"percent": 29}}},
         ],
         "total": [
             {"team_id": None, "side": "over", "value": 8.5, "odds": -110,
              "is_live": True, "line_status": "normal", "bet_info": {}},
         ],
     }}}},
    # A second game with NO entry for our book at all (BOOK_ID=15 absent) --
    # must be skipped cleanly, not crash the whole snapshot.
    {"id": 2, "start_time": "2026-08-06T23:05:00Z", "status": "scheduled",
     "teams": [{"id": 300, "full_name": "New York Yankees"}],
     "markets": {"99": {"event": {"moneyline": []}}}},
]}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


head("1. real fields extracted correctly, market by market")

with mock.patch("requests.get", return_value=_FakeResp(REAL_PAYLOAD)):
    taken_at, rows = osn.fetch_snapshot("2026-08-06")

ml_rows = [r for r in rows if r["market"] == "moneyline"]
check(len(ml_rows) == 2, "both moneyline sides for the real game are captured", f"got {len(ml_rows)}")

home = next(r for r in ml_rows if r["team"] == "Houston Astros")
check(home["odds"] == -140 and home["tickets_pct"] == 62 and home["money_pct"] == 71,
      "team name resolved via the teams list, odds/tickets_pct/money_pct pulled from "
      "the right nested bet_info fields", f"got {home}")

away = next(r for r in ml_rows if r["team"] == "Texas Rangers")
check(away["odds"] == 120, "the second side's odds are captured independently of the first")

total_rows = [r for r in rows if r["market"] == "total"]
check(len(total_rows) == 1 and total_rows[0]["is_live"] is True,
      "the total market is captured separately, and is_live is recorded per-entry",
      f"got {total_rows}")
check(total_rows[0]["tickets_pct"] is None and total_rows[0]["money_pct"] is None,
      "a market with no bet_info at all (total, here) gets None rather than a KeyError",
      f"got {total_rows[0]}")

head("2. a game with no entry for our book is skipped, not a crash")

check(all(r["game_id"] != 2 for r in rows),
      "game 2 (no bookId=15 entry) contributes zero rows and does not raise",
      f"game_ids present: {sorted({r['game_id'] for r in rows})}")

head("3. every row in one sweep shares the same taken_at")

check(len({r["taken_at"] for r in rows}) == 1 and rows[0]["taken_at"] == taken_at,
      "all rows from one fetch_snapshot() call share exactly one taken_at timestamp")

head("4. a slate with zero games returns an empty row list, not a crash")

with mock.patch("requests.get", return_value=_FakeResp({"games": []})):
    _, empty_rows = osn.fetch_snapshot("2026-08-06")
check(empty_rows == [], "an empty slate returns [] cleanly")

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
