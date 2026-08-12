#!/usr/bin/env python3
"""test_grade_value.py — direct coverage for grade_value.py, which had zero
before this. This is the one module that answers "is the value screen
actually making money" (its own docstring), and it already had one severe,
silent bug found by manual read-through: an untracked stat like
hard_hit_105 or pitcher_outs (this file only reads the batter hitting
gameLog) used to default to 0 via act.get(stat, 0), which is never >= a
real needs threshold -- so every such bet graded a GUARANTEED LOSS instead
of being skipped, baked directly into the ROI number. Fixed already (see
settle()'s own "THE BUG THIS REPLACES" comment); this file locks it in so
it can't silently regress, plus covers closing_prices() and actual_results().

    /tmp/mlbvenv/bin/python3 test_grade_value.py
"""
import json
import os
import sys
import tempfile
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


import prop_probability as pp
import grade_value as gv

# ══════════════════════════════════════════════════════════════════════════
head("1. closing_prices: in_play skip, overwrite-by-time, partial-sweep flag")
# ══════════════════════════════════════════════════════════════════════════

_tmpdir = tempfile.mkdtemp()
_props_dir = os.path.join(_tmpdir, "props")
os.makedirs(_props_dir, exist_ok=True)
DATE = "2026-08-06"

payload = {"snapshots": [
    {"taken_at": "2026-08-06T18:00:00", "coverage": {"complete": True},
     "rows": [
         {"player_norm": "yordan alvarez", "stat": "hits", "needs": 1,
          "american": -150, "player": "Yordan Alvarez", "game": "HOU@TEX"},
         {"player_norm": "will smith", "stat": "hits", "needs": 1,
          "american": -110, "player": "Will Smith", "in_play": True},
     ]},
    # Later snapshot for Alvarez overwrites the earlier price -- this is the
    # "closing" price, the last one taken before first pitch.
    {"taken_at": "2026-08-06T19:30:00", "coverage": {"complete": False},
     "rows": [
         {"player_norm": "yordan alvarez", "stat": "hits", "needs": 1,
          "american": -175, "player": "Yordan Alvarez", "game": "HOU@TEX"},
     ]},
]}
with open(os.path.join(_props_dir, f"props_{DATE}.json"), "w") as f:
    json.dump(payload, f)

with mock.patch.object(gv, "PROPS_DIR", _props_dir):
    prices = gv.closing_prices(DATE)

check(("will smith", "hits", 1) not in prices,
      "an in_play row is excluded -- that's a different (live) bet from the screened one")
key = ("yordan alvarez", "hits", 1)
check(key in prices, "the real pregame row survives")
check(prices[key]["american"] == -175,
      "the LATER snapshot's price wins (the closing price), not the earlier one",
      f"got {prices[key]['american']}")
check(prices[key]["from_partial_sweep"] is True,
      "a row from a sweep flagged coverage.complete=False is marked from_partial_sweep",
      f"got {prices[key]['from_partial_sweep']}")

missing = gv.closing_prices("2099-01-01")
check(missing == {}, "a date with no captured file returns {} rather than raising")

# ══════════════════════════════════════════════════════════════════════════
head("2. actual_results: field extraction, singles/hits_runs_rbis derivation")
# ══════════════════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _gamelog_payload(date, stat):
    return {"stats": [{"splits": [{"date": date, "stat": stat}]}]}


with mock.patch("mlb_sources.batter_pa_composition") as mock_comp, \
     mock.patch("requests.get") as mock_get:
    mock_comp.return_value = {660271: {"name": "Yordan Alvarez"}}
    mock_get.return_value = _FakeResp(_gamelog_payload(
        DATE, {"hits": 3, "totalBases": 7, "homeRuns": 1, "doubles": 1,
               "triples": 0, "runs": 2, "rbi": 4, "stolenBases": 0,
               "plateAppearances": 5}))
    res = gv.actual_results(DATE, {"yordan alvarez"})

r = res.get("yordan alvarez")
check(r is not None, "a real player with a game that date is returned")
check(r["hits"] == 3 and r["total_bases"] == 7 and r["home_runs"] == 1,
      "hits/total_bases/home_runs pulled straight off the gameLog stat block",
      f"got {r}")
check(r["singles"] == 1,
      "singles is derived: hits(3) - doubles(1) - triples(0) - home_runs(1) = 1",
      f"got {r['singles']}")
check(r["hits_runs_rbis"] == 3 + 2 + 4,
      "hits_runs_rbis is the literal sum, including the double-count on a solo-driving HR",
      f"got {r['hits_runs_rbis']}")
check(r["stolen_bases"] == 0,
      "stolen_bases uses the plural key matching MARKET_MAP/value_board's convention")

with mock.patch("mlb_sources.batter_pa_composition") as mock_comp, \
     mock.patch("requests.get") as mock_get:
    mock_comp.return_value = {660271: {"name": "Yordan Alvarez"}}
    # A date this player did not play -- no splits match the requested date.
    mock_get.return_value = _FakeResp(_gamelog_payload("2026-08-05", {"hits": 1}))
    res2 = gv.actual_results(DATE, {"yordan alvarez"})
check("yordan alvarez" not in res2,
      "a player with no game log entry for this exact date is absent, not zero")

# ══════════════════════════════════════════════════════════════════════════
head("3. settle(): THE BUG THIS REPLACES -- untracked stat must be SKIPPED, not lost")
# ══════════════════════════════════════════════════════════════════════════


def _entry(player, stat, needs, american, prob, prob_lo):
    norm = player.lower()
    return (norm, stat, needs), {
        "player": player, "stat": stat, "needs": needs, "american": american,
        "prob": prob, "prob_lo": prob_lo, "player_norm": norm,
    }


# Alvarez: a real, gradeable hits bet that WINS (3 hits >= needs=1).
k1, e1 = _entry("Yordan Alvarez", "hits", 1, -150, 0.75, 0.68)
# Witt: a real, gradeable hits bet that LOSES (0 hits < needs=2).
k2, e2 = _entry("Bobby Witt Jr.", "hits", 2, -120, 0.62, 0.55)
# Judge: hard_hit_105 -- NOT tracked by actual_results (no Statcast source
# here). Must be skipped entirely, not counted as a loss.
k3, e3 = _entry("Aaron Judge", "hard_hit_105", 1, +150, 0.55, 0.45)
# Ohtani: did not play this date (absent from actual_results' return) --
# must also be skipped, not counted as a loss.
k4, e4 = _entry("Shohei Ohtani", "hits", 1, -140, 0.72, 0.65)

fake_prices = {k1: {"american": e1["american"]}, k2: {"american": e2["american"]},
               k3: {"american": e3["american"]}, k4: {"american": e4["american"]}}
fake_reads = {k1: e1, k2: e2, k3: e3, k4: e4}
fake_actuals = {
    "yordan alvarez": {"hits": 3, "total_bases": 7},
    # odds_fanduel.normalize_name strips suffixes -- "Bobby Witt Jr." really
    # normalizes to "bobby witt", not "bobby witt jr.". settle() calls the
    # REAL normalize_name (only actual_results itself is mocked here), so
    # this key has to match what that function actually produces.
    "bobby witt": {"hits": 0, "total_bases": 0},
    "aaron judge": {"total_bases": 5},  # note: NO "hard_hit_105" key at all
    # "shohei ohtani" absent entirely -- did not play
}

with mock.patch.object(gv, "closing_prices", return_value=fake_prices), \
     mock.patch.object(gv, "board_reads", return_value=fake_reads), \
     mock.patch.object(gv, "actual_results", return_value=fake_actuals):
    result = gv.settle(DATE, min_roi=0.0)

settled_stats = {(s["player"], s["stat"]) for s in result["bets"]}
check(("Aaron Judge", "hard_hit_105") not in settled_stats,
      "an untracked stat (hard_hit_105) is SKIPPED, not graded a loss",
      f"settled: {settled_stats}")
check(("Shohei Ohtani", "hits") not in settled_stats,
      "a player absent from actual_results (did not play) is skipped, not graded a loss",
      f"settled: {settled_stats}")
check(("Yordan Alvarez", "hits") in settled_stats,
      "a real, gradeable bet is present in the settled list")
check(result["staked"] == 2,
      "staked counts only the 2 real gradeable bets (Alvarez, Witt), not the 2 skipped ones",
      f"got {result['staked']}")

alvarez_row = next(s for s in result["bets"] if s["player"] == "Yordan Alvarez")
witt_row = next(s for s in result["bets"] if s["player"] == "Bobby Witt Jr.")
check(alvarez_row["won"] is True, "Alvarez's 3 hits clears needs=1 -- a real win")
check(witt_row["won"] is False, "Witt's 0 hits does not clear needs=2 -- a real loss")

want_returned = pp.decimal_odds(-150) * 1.0 + 0.0
check(abs(result["returned"] - want_returned) < 1e-9,
      "returned is the winner's decimal payout plus zero for the loser, nothing else",
      f"got {result['returned']}, want {want_returned}")
want_roi = (want_returned - 2.0) / 2.0
check(abs(result["roi"] - want_roi) < 1e-9,
      "roi = (returned - staked) / staked over the 2 real bets only",
      f"got {result['roi']}, want {want_roi}")
check(abs(result["hit_rate"] - 0.5) < 1e-9,
      "hit_rate is 1 win / 2 settled = 0.5 (the 2 skipped bets don't dilute it)",
      f"got {result['hit_rate']}")

head("4. settle(): no prices / no reads short-circuit cleanly")

with mock.patch.object(gv, "closing_prices", return_value={}):
    check(gv.settle(DATE) is None, "no captured prices for the date returns None outright")

with mock.patch.object(gv, "closing_prices", return_value=fake_prices), \
     mock.patch.object(gv, "board_reads", return_value=None):
    r_noreads = gv.settle(DATE)
check(r_noreads == {"date": DATE, "no_reads": True},
      "prices exist but no board_reads file -- flagged no_reads, not a fabricated 0-bet result",
      f"got {r_noreads}")

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
