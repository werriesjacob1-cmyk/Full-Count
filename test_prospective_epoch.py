"""Tests for decisive-epoch binding and the equal-volume selection contract."""

import sys
from datetime import datetime, timedelta, timezone

from backtest import prospective_eligibility as pe
from backtest import prospective_epoch as pep
from backtest import prospective_selection as ps

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


NOW = datetime(2026, 9, 2, 21, 0, 0, tzinfo=timezone.utc)
GEN = (NOW - timedelta(minutes=8)).isoformat()
PREPARED = (NOW - timedelta(minutes=4)).isoformat()

CAND = pep.build_epoch_candidate(
    slate_date="2026-09-02", board_generated_at=GEN,
    odds_fetched_at=(NOW - timedelta(minutes=3)).isoformat(),
    snapshot_sha="a" * 64,
    identity={"GITHUB_WORKFLOW": None, "workflow_name": "Dashboard Refresh",
              "workflow_run_id": "111", "source_commit": "c" * 40,
              "runner_environment": "github_actions"})


def deployment(**over):
    d = {
        "triggering_workflow_name": "Dashboard Refresh",
        "converged": True,
        "public_generated_at": GEN,
        "source_commit": "c" * 40,
        "prepared_at": PREPARED,
        "converged_at": (NOW - timedelta(minutes=2)).isoformat(),
        "artifact_id": "art-1", "run_id": "222",
        "candidate_ids": ["p1", "p2"],
    }
    d.update(over)
    return d


print("Check 1: origin classification")
check("Dashboard Refresh -> full_refresh",
      pep.origin_of("Dashboard Refresh") == pep.ORIGIN_FULL_REFRESH)
check("Dashboard Live Update -> live_update",
      pep.origin_of("Dashboard Live Update") == pep.ORIGIN_LIVE_UPDATE)
check("anything else -> unknown", pep.origin_of("Something") == pep.ORIGIN_UNKNOWN)
check("candidate records its origin", CAND["origin"] == pep.ORIGIN_FULL_REFRESH)
check("candidate starts NOT converged", CAND["publicly_converged"] is False)

print("\nCheck 2: binding requires real public convergence")
check("clean deployment binds", pep.bind_deployment(CAND, deployment())["bound"])
check("live-update-originated deployment does NOT bind",
      not pep.bind_deployment(CAND, deployment(
          triggering_workflow_name="Dashboard Live Update"))["bound"])
check("unconverged deployment does NOT bind",
      not pep.bind_deployment(CAND, deployment(converged=False))["bound"])
check("generated_at mismatch does NOT bind (hash binding, not correlation)",
      not pep.bind_deployment(CAND, deployment(
          public_generated_at="2026-09-02T00:00:00+00:00"))["bound"])
check("source_commit mismatch does NOT bind",
      not pep.bind_deployment(CAND, deployment(source_commit="d" * 40))["bound"])
check("missing prepared_at does NOT bind",
      not pep.bind_deployment(CAND, deployment(prepared_at=None))["bound"])
bound = pep.bind_deployment(CAND, deployment())["epoch"]
check("bound epoch is marked converged", bound["publicly_converged"] is True)
check("bound epoch carries a decisive id", bool(bound["decisive_epoch_id"]))

print("\nCheck 3: NO PRIMARY EPOCH is raised, not silently degraded")
check("no deployments -> NoPrimaryEpoch",
      raises(lambda: pep.select_decisive_epoch([CAND], [], "2026-09-02"),
             pep.NoPrimaryEpoch))
check("only a live-update deployment -> NoPrimaryEpoch (no fallback)",
      raises(lambda: pep.select_decisive_epoch(
          [CAND], [deployment(triggering_workflow_name="Dashboard Live Update")],
          "2026-09-02"), pep.NoPrimaryEpoch))
check("a date with no candidates -> NoPrimaryEpoch",
      raises(lambda: pep.select_decisive_epoch([CAND], [deployment()], "2026-09-03"),
             pep.NoPrimaryEpoch))

print("\nCheck 4: LATEST converged epoch wins, by prepared_at")
early_gen = (NOW - timedelta(hours=3)).isoformat()
early = pep.build_epoch_candidate(
    slate_date="2026-09-02", board_generated_at=early_gen,
    odds_fetched_at=early_gen, snapshot_sha="b" * 64,
    identity={"workflow_name": "Dashboard Refresh", "workflow_run_id": "110",
              "source_commit": "c" * 40, "runner_environment": "github_actions"})
deps = [deployment(public_generated_at=early_gen, artifact_id="art-0",
                   prepared_at=(NOW - timedelta(hours=2, minutes=50)).isoformat()),
        deployment()]
picked = pep.select_decisive_epoch([early, CAND], deps, "2026-09-02")
check("picks the later prepared_at", picked["deployment_artifact_id"] == "art-1")
check("exactly one epoch is returned", isinstance(picked, dict))
check("selection reads no outcome field",
      not any(k in str(picked).lower() for k in ("hit", "miss", "won")))

print("\nCheck 5: regate_pool closes the capture-time/prepared-at asymmetry")
# A game starting 20 min after CAPTURE but only 10 min after PREPARATION passed
# the capture-time gate and must NOT survive the re-gate: the site could not
# have published it.
prep_dt = datetime.fromisoformat(PREPARED)
soon = (prep_dt + timedelta(minutes=10)).isoformat()
far = (prep_dt + timedelta(hours=2)).isoformat()
sched = {1: {"started": False, "start": soon}, 2: {"started": False, "start": far}}


def rowv(pk, start):
    r = {"game_pk": pk, "name": "B", "player_id": pk, "team": "T", "side": "home",
         "projection": {"stat": "hits", "value": 0.5, "needs": 1},
         "hit_probability": 0.6, "sample_n": 100, "reliability": "A",
         "market_odds": -140, "prop": "Over 0.5 Hits"}
    v = pe.evaluate_row(r, now=NOW, schedule={pk: {"started": False, "start": start}},
                        odds_fetched_at=(NOW - timedelta(minutes=3)).isoformat(),
                        board_generated_at=GEN)
    return (r, v)


pool = [rowv(1, soon), rowv(2, far)]
kept, dropped = pep.regate_pool(pool, bound, schedule=sched)
check("row inside the cutoff at prepared_at is dropped", len(dropped) == 1)
check("row still outside the cutoff is kept", len(kept) == 1)
check("the kept row is the far one", kept[0][0]["game_pk"] == 2)
check("re-gate without prepared_at raises",
      raises(lambda: pep.regate_pool(pool, {}, schedule=sched), pep.NoPrimaryEpoch))

print("\nCheck 6: champion set comes from the manifest, not a probability proxy")
manifest = {"candidates": [
    {"canonical_id": "p1", "settlement_identity": {"stat": "hits"}, "snapshot": {}},
    {"canonical_id": "p2", "settlement_identity": {"stat": "hits"}, "snapshot": {}},
    {"canonical_id": "px", "settlement_identity": {"stat": "total_bases"}, "snapshot": {}},
]}
champs = ps.champion_hits_picks(manifest)
check("only hits candidates", [c["canonical_id"] for c in champs] == ["p1", "p2"])
# Grep the EXECUTABLE code, not the prose: the module docstring names the
# forbidden `predicted_prob >= 0.60` proxy precisely in order to say it is not
# used, so a naive whole-file grep would flag the documentation itself.
import ast as _ast
_tree = _ast.parse(open("backtest/prospective_selection.py").read())
for _n in _ast.walk(_tree):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                       _ast.ClassDef)):
        if (_n.body and isinstance(_n.body[0], _ast.Expr)
                and isinstance(_n.body[0].value, _ast.Constant)
                and isinstance(_n.body[0].value.value, str)):
            _n.body.pop(0)
_code = _ast.unparse(_tree)
check("no 0.60 probability proxy in executable code", "0.60" not in _code)
check("no probability threshold gate in executable code",
      "hit_probability >=" not in _code and "predicted_prob" not in _code)


def fake(pid, champ_prob):
    row = {"hit_probability": champ_prob}
    verdict = {"canonical_prop_id": pid, "failed_gates": ()}
    return (row, verdict)


print("\nCheck 7: an unmatched champion FAILS THE EPOCH CLOSED")
universe = [fake("p1", 0.7), fake("p2", 0.65), fake("p3", 0.4), fake("p4", 0.4)]
check("champion missing from the frozen universe raises",
      raises(lambda: ps.resolve_champions(champs, [fake("p1", 0.7)], [fake("p1", 0.7)]),
             ps.EpochFailedClosed))
res = ps.resolve_champions(champs, universe, [fake("p1", 0.7), fake("p2", 0.65)])
check("both champions matched -> N=2", res["n"] == 2)

print("\nCheck 8: a published-but-ineligible champion is recorded, not hidden")
res2 = ps.resolve_champions(champs, universe, [fake("p1", 0.7)])
check("N counts only pool members", res2["n"] == 1)
check("the excluded champion is reported",
      [pid for pid, _ in res2["out_of_pool"]] == ["p2"])

print("\nCheck 9: PA-v1 ranking follows the stated order exactly")
pool2 = [fake("p1", 0.50), fake("p2", 0.90), fake("p3", 0.10), fake("p4", 0.10)]
ranked = ps.rank_pa_v1(pool2, {"p1": 0.8, "p2": 0.6, "p3": 0.9, "p4": None})
order = [r["canonical_prop_id"] for r in ranked]
check("higher PA-v1 score first", order[0] == "p3")
check("then next PA-v1 score", order[1] == "p1")
check("unscored row ranked LAST but not dropped", order[-1] == "p4")
check("unscored row still present", len(ranked) == 4)
tied = ps.rank_pa_v1([fake("pb", 0.5), fake("pa", 0.5)], {"pa": 0.7, "pb": 0.7})
check("exact tie broken by canonical identity, deterministically",
      [r["canonical_prop_id"] for r in tied] == ["pa", "pb"])
tie2 = ps.rank_pa_v1([fake("pb", 0.9), fake("pa", 0.5)], {"pa": 0.7, "pb": 0.7})
check("champion probability breaks a PA tie before identity does",
      [r["canonical_prop_id"] for r in tie2] == ["pb", "pa"])

print("\nCheck 10: equal volume is enforced per epoch, and raises")
check("select exactly n", len(ps.select_pa_v1(ranked, 2)) == 2)
check("cannot select beyond the frozen pool",
      raises(lambda: ps.select_pa_v1(ranked, 99), ps.EpochFailedClosed))
check("unequal volume raises",
      raises(lambda: ps.assert_equal_volume("E", [1, 2], [1]), ps.EpochFailedClosed))
check("equal volume returns n", ps.assert_equal_volume("E", [1, 2], [3, 4]) == 2)

print("\nCheck 11: N(date)==0 produces no comparison, not a manufactured one")
empty = ps.build_epoch_selection(epoch={"decisive_epoch_id": "E", "slate_date": "d"},
                                 manifest={"candidates": []},
                                 universe=universe, pool=pool2, pa_scores={})
check("no champions -> None", empty is None)

print("\nCheck 12: a full epoch selection is volume-matched end to end")
sel = ps.build_epoch_selection(
    epoch={"decisive_epoch_id": "E1", "slate_date": "2026-09-02"},
    manifest=manifest, universe=universe,
    pool=[fake("p1", 0.7), fake("p2", 0.65), fake("p3", 0.4), fake("p4", 0.4)],
    pa_scores={"p1": 0.5, "p2": 0.4, "p3": 0.95, "p4": 0.90})
check("N is 2", sel["n"] == 2)
check("champion selected 2", len(sel["champion_selected"]) == 2)
check("PA-v1 selected exactly 2", len(sel["pa_v1_selected"]) == 2)
check("PA-v1 picked its own top 2, not the champion's",
      [r["canonical_prop_id"] for r in sel["pa_v1_selected"]] == ["p3", "p4"])
check("champion ranks recorded", sel["champion_ranks"] == {"p1": 1, "p2": 2})

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective epoch/selection checks passed.")
