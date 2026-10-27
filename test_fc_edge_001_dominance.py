"""FC-EDGE-001 Phase 3 regression: exact-dominance counting on a synthetic FanDuel props file.

Runs as `python3 test_fc_edge_001_dominance.py` (the repo's CI convention); exits non-zero on failure.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fc_edge_001_dominance", Path(__file__).resolve().parent / "research" / "fc_edge_001" / "dominance.py")
dom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dom)

_results = []


def check(ok, msg):
    _results.append((bool(ok), msg))
    print(("PASS " if ok else "FAIL ") + msg)


def row(stat, needs, american, taken="2026-09-01T17:00:00Z", start="2026-09-01T23:05:00Z", in_play=False):
    return {"event_id": "e1", "player_norm": "a b", "stat": stat, "needs": needs, "american": american,
            "taken_at": taken, "start_time": start, "in_play": in_play}


def run(rows):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "data" / "props").mkdir(parents=True)
        (root / "out").mkdir()
        (root / "data" / "props" / "props_2026-09-01.json").write_text(
            json.dumps({"date": "2026-09-01", "snapshots": [{"rows": rows}]}))
        saved, dom.HERE = dom.HERE, root          # keep fixture output out of the real out/ directory
        try:
            return dom.main(root)["rules"]
        finally:
            dom.HERE = saved


check(abs(dom.imp(-150) - 0.6) < 1e-12 and abs(dom.imp(150) - 0.4) < 1e-12, "american -> raw implied probability")

r = run([row("hits", 1, -250), row("hits_runs_rbis", 1, -400), row("hits", 2, 250)])
check(r["hits1=>hrr1"]["dominance_observations"] == 0, "superset priced shorter is not dominance")
check(r["monotone_threshold"]["dominance_observations"] == 0, "consistent threshold ladder is not dominance")

r = run([row("home_runs", 1, 300), row("total_bases", 4, 320)])  # HR implies TB>=4; TB4 longer
check(r["hr1=>tb4"]["dominance_observations"] == 1 and r["hr1=>tb4"]["strict_dominance_observations"] == 1,
      "implied superset priced longer is strict dominance")

r = run([row("home_runs", 1, 300), row("total_bases", 4, 300)])
check(r["hr1=>tb4"]["dominance_observations"] == 1 and r["hr1=>tb4"]["strict_dominance_observations"] == 0,
      "equal price is (weak) dominance, not strict")

r = run([row("home_runs", 1, 300, in_play=True), row("total_bases", 4, 320),
         row("hits", 1, -200, taken="2026-09-02T00:00:00Z"), row("hits_runs_rbis", 1, -150)])
check("hr1=>tb4" not in r and "hits1=>hrr1" not in r, "in-play and post-start rows are excluded")

n_pass = sum(ok for ok, _ in _results)
print(f"RESULT: {n_pass}/{len(_results)} checks passed")
sys.exit(0 if n_pass == len(_results) else 1)
