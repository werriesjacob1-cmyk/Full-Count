"""FC-ZERO-001 regression: frozen tercile assignment, D statistic and equal-volume picking in ladder_gap.py.

Runs as `python3 test_fc_zero_001_ladder_gap.py` (the repo's CI convention); exits non-zero on failure.
Offline: no StatsAPI call is made.
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fc_zero_001_ladder_gap", Path(__file__).resolve().parent / "research" / "fc_zero_001" / "ladder_gap.py")
lg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lg)

_results = []


def check(ok, msg):
    _results.append((bool(ok), msg))
    print(("PASS " if ok else "FAIL ") + msg)


def row(i, hrr1, g, y, game=1, player=None):
    return {"event_id": game, "player_norm": player or f"p{i}", "hrr1": hrr1, "g": g, "y": y, "game_pk": game}


check(abs(lg.payout(-200) - 0.5) < 1e-12 and abs(lg.payout(150) - 1.5) < 1e-12, "payout per unit stake")

rows = [row(i, -250, g, y) for i, (g, y) in enumerate([(0.01, 1), (0.02, 1), (0.03, 0), (0.04, 0), (0.05, 0), (0.06, 1)])]
lg.assign_terciles(rows)
check([r["terc"] for r in rows] == ["LOW", "LOW", "MID", "MID", "HIGH", "HIGH"], "terciles by within-price rank of g")

small = [row(i, -110, 0.05, 1) for i in range(5)]
check(lg.assign_terciles(small) == 5 and all(r["terc"] is None for r in small), "strata under 6 rows are dropped")

tied = [row(i, -300, 0.05, 1) for i in range(6)]
lg.assign_terciles(tied)
check(all(r["terc"] == "MID" for r in tied), "fully tied gaps land in MID, never split arbitrarily")

check(abs(lg.D_stat(rows) - (1.0 - 0.5)) < 1e-12, "D = LOW hit rate minus HIGH hit rate within a price stratum")

cands = [row(0, -400, 0.05, 1, game=1, player="a"), row(1, -380, 0.05, 1, game=1, player="b"),
         row(2, -350, 0.05, 1, game=1, player="c"), row(3, -300, 0.05, 1, game=2, player="a")]
sel = lg.pick(cands, 3, key=lambda r: (-lg.imp(r["hrr1"]), r["player_norm"]))
check([r["player_norm"] for r in sel] == ["a", "b"], "at most 2 per game and 1 per player in equal-volume picks")

n_pass = sum(ok for ok, _ in _results)
print(f"RESULT: {n_pass}/{len(_results)} checks passed")
sys.exit(0 if n_pass == len(_results) else 1)
