"""Tests for decisive-epoch binding and the equal-volume selection contract."""

import sys
from datetime import datetime, timedelta, timezone

from backtest import prospective_eligibility as pe
from backtest import prospective_epoch as pep
from backtest import prospective_selection as ps
from backtest import prospective_source_integrity as psi

# A live.json with the two REQUIRED freshness channels present and current.
# reconciliation is deliberately absent, matching every real board measured:
# it is an enhancer, not a precondition.
from datetime import datetime as _dt, timezone as _tz
_FRESH_LIVE = {"prices_checked_at": _dt.now(_tz.utc).isoformat(),
               "grades_checked_at": _dt.now(_tz.utc).isoformat(),
               "reconciliation": None}

CLEAR = psi.evaluate(schedule={1: {}}, live_state=_FRESH_LIVE)

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
        "triggering_workflow_run_id": "111",
        "publication_cutoff_at": (datetime.fromisoformat(PREPARED)
                                  + timedelta(minutes=15)).isoformat(),
        "public_source_commit": "c" * 40,
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
# DELIBERATELY NOT AN EQUALITY CHECK. build_source_commit is the refresh run's
# GITHUB_SHA (main HEAD at trigger); deployment.source_commit is main HEAD at
# the deploy checkout, several commits later, because Dashboard Refresh itself
# pushes in between. Requiring equality made binding provably impossible and
# would have raised NoPrimaryEpoch on every date forever. Protocol section 8
# requires both to be RECORDED; the generated_at hash join is the real binding.
_diff = pep.bind_deployment(CAND, deployment(source_commit="d" * 40))
check("differing source_commit still binds (equality was unsatisfiable)",
      _diff["bound"], str(_diff.get("reasons")))
check("but BOTH commits are recorded",
      _diff["epoch"]["deployment_source_commit"] == "d" * 40
      and _diff["epoch"]["build_source_commit"] == "c" * 40)
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

print("\nCheck 5: regate_pool closes BOTH real-clock asymmetries")
prep_dt = datetime.fromisoformat(PREPARED)
conv_dt = datetime.fromisoformat(bound["deployment_converged_at"])
soon = (prep_dt + timedelta(minutes=10)).isoformat()      # inside the cutoff
far = (prep_dt + timedelta(hours=2)).isoformat()          # fine
started = (conv_dt - timedelta(minutes=1)).isoformat()    # already underway
sched = {1: {"started": False, "start": soon},
         2: {"started": False, "start": far},
         3: {"started": False, "start": started}}


def rowv(pk, start):
    r = {"game_pk": pk, "name": "B", "player_id": pk, "team": "T", "side": "home",
         "projection": {"stat": "hits", "value": 0.5, "needs": 1},
         "hit_probability": 0.6, "sample_n": 100, "reliability": "A",
         "market_odds": -140, "prop": "Over 0.5 Hits"}
    v = pe.evaluate_row(r, now=NOW, schedule={pk: {"started": False, "start": start}},
                        odds_fetched_at=(NOW - timedelta(minutes=3)).isoformat(),
                        board_generated_at=GEN, source_integrity=CLEAR)
    return (r, v)


pool = [rowv(1, soon), rowv(2, far)]
kept, dropped = pep.regate_pool(pool, bound, schedule=sched)
check("row inside the cutoff at prepared_at is dropped", len(dropped) == 1, str(dropped))
check("only the genuinely placeable row survives", len(kept) == 1)
check("the kept row is the far one", kept[0][0]["game_pk"] == 2)
reasons = {r for _row, r in dropped}
check("publication-cutoff drop is reported",
      any("publication cutoff" in r for r in reasons), str(reasons))

# THE QUESTION MISSION 1 DID NOT ASK. prepared_at can clear the strict
# 15-minute rule while PUBLIC CONVERGENCE still lands after first pitch --
# real measured refresh-to-convergence latency runs to ~21 minutes in the
# tail. Nobody could have wagered such a pick. Exercised with a genuinely
# slow deployment, which is the case that actually occurs.
slow = pep.bind_deployment(CAND, deployment(
    converged_at=(prep_dt + timedelta(minutes=40)).isoformat()))["epoch"]
mid = (prep_dt + timedelta(minutes=25)).isoformat()   # clears cutoff, starts
                                                      # before convergence
slow_pool = [rowv(4, mid), rowv(2, far)]
slow_kept, slow_dropped = pep.regate_pool(
    slow_pool, slow, schedule={4: {"started": False, "start": mid},
                               2: {"started": False, "start": far}})
slow_reasons = {r for _row, r in slow_dropped}
check("a pick clearing the cutoff but not public in time is dropped",
      any("convergence" in r for r in slow_reasons), str(slow_reasons))
check("and the genuinely usable one is kept",
      [r[0]["game_pk"] for r in slow_kept] == [2], str(slow_kept))
check("publicly_usable is symmetric, not a per-arm gate",
      pep.publicly_usable(None, conv_dt) is False
      and pep.publicly_usable(conv_dt + timedelta(hours=1), conv_dt) is True)
check("re-gate without prepared_at raises",
      raises(lambda: pep.regate_pool(pool, {}, schedule=sched), pep.NoPrimaryEpoch))

print("\nCheck 6: the champion set is what the artifact EXPOSED, not first exposures")
CUT = (datetime.fromisoformat(PREPARED) + timedelta(minutes=15)).isoformat()
LATER = (datetime.fromisoformat(PREPARED) + timedelta(hours=3)).isoformat()


def prop(pid, **over):
    p = {"game_pk": 900 + pid, "player_id": pid, "name": f"P{pid}",
         "team": "T", "side": "home", "prop": "Over 0.5 Hits",
         "projection": {"stat": "hits", "value": 0.5, "needs": 1},
         "recommendation_status": "top_pick", "game_state": "pregame",
         "game_start": LATER}
    p.update(over)
    return p


payload = {"props": [
    prop(1), prop(2),
    prop(3, recommendation_status="lean"),
    prop(4, projection={"stat": "total_bases", "value": 1.5, "needs": 2}),
    prop(5, game_state="live"),
    prop(6, game_start=(datetime.fromisoformat(PREPARED)
                        + timedelta(minutes=5)).isoformat()),
]}
champs, champ_dropped = ps.champion_hits_picks(payload, publication_cutoff_at=CUT)
check("only exposed Hits top picks survive", len(champs) == 2, str(len(champs)))
# EVERY exclusion of an exposed Hits Top Pick is RECORDED, never a bare
# `continue`. A red team proved that silently dropping one here reintroduces
# the asymmetric replacement resolve_champions exists to prevent, one function
# earlier where resolve_champions can never see it.
check("the cutoff drop is REPORTED, not silent", len(champ_dropped) >= 1,
      str(champ_dropped))
check("no publication_cutoff_at at all fails closed (never falls back to "
      "prepared_at, which is a LOOSER rule than production)",
      raises(lambda: ps.champion_hits_picks(payload, publication_cutoff_at=None),
             ps.EpochFailedClosed))
check("leans excluded", all(c["row"]["recommendation_status"] == "top_pick"
                           for c in champs))
check("non-hits excluded",
      all((c["row"]["projection"] or {}).get("stat") == "hits" for c in champs))
check("live games excluded", all(c["row"].get("game_state") == "pregame"
                                 for c in champs))
check("inside the publication cutoff excluded",
      6 not in [c["row"]["player_id"] for c in champs])
src = open("backtest/prospective_selection.py").read()
import ast as _ast
_t = _ast.parse(src)
for _n in _ast.walk(_t):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.ClassDef)):
        if (_n.body and isinstance(_n.body[0], _ast.Expr)
                and isinstance(_n.body[0].value, _ast.Constant)
                and isinstance(_n.body[0].value.value, str)):
            _n.body.pop(0)
_code = _ast.unparse(_t)
check("NO first-exposure registry filter anywhere in executable code",
      'registry["entries"]' not in _code and "registry['entries']" not in _code)
check("no 0.60 probability proxy in executable code", "0.60" not in _code)
check("champion is read from the served payload, not a manifest",
      "manifest" not in _code)

print("\nCheck 7: a champion that cannot resolve FAILS THE EPOCH CLOSED")


def fake(pid, champ_prob):
    return ({"hit_probability": champ_prob},
            {"canonical_prop_id": pid, "failed_gates": ()})


C = [{"canonical_id": "p1", "row": {}, "identity_error": False},
     {"canonical_id": "p2", "row": {}, "identity_error": False}]
universe = [fake("p1", 0.7), fake("p2", 0.65), fake("p3", 0.4), fake("p4", 0.4)]
check("champion missing from the frozen universe raises",
      raises(lambda: ps.resolve_champions(C, [fake("p1", 0.7)], [fake("p1", 0.7)]),
             ps.EpochFailedClosed))
check("an unidentifiable exposed pick raises",
      raises(lambda: ps.resolve_champions(
          [{"canonical_id": None, "row": {}, "identity_error": True}],
          universe, []), ps.EpochFailedClosed))
res = ps.resolve_champions(C, universe, [fake("p1", 0.7), fake("p2", 0.65)])
check("both champions matched -> N=2", res["n"] == 2)

print("\nCheck 8: a published-but-INELIGIBLE champion also fails closed")
# Mission 1 dropped it from N with no backfill while PA-v1 kept its own
# optimum -- asymmetric replacement, with a caller-steerable deletion lever.
check("champion exposed but failing an operational gate raises",
      raises(lambda: ps.resolve_champions(C, universe, [fake("p1", 0.7)]),
             ps.EpochFailedClosed))
try:
    ps.resolve_champions(C, universe, [fake("p1", 0.7)])
except ps.EpochFailedClosed as exc:
    check("and the failure names the offending pick", "p2" in str(exc))

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

print("\nCheck 11: build_epoch_selection ACTUALLY CALLS the re-gate")
# Mission 1 shipped regate_pool as an uncalled helper and build_epoch_selection
# did not invoke it, so the challenger ranked the capture-time pool.
_fn = next(n for n in _ast.walk(_ast.parse(src))
           if isinstance(n, _ast.FunctionDef) and n.name == "build_epoch_selection")
_calls = {n.func.id for n in _ast.walk(_fn)
          if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
check("regate_pool is called", "regate_pool" in _calls, str(sorted(_calls)))
check("champion_hits_picks is called", "champion_hits_picks" in _calls)
check("resolve_champions is called", "resolve_champions" in _calls)
check("assert_equal_volume is called", "assert_equal_volume" in _calls)

print("\nCheck 12: N == 0 produces no comparison, not a manufactured one")
GEN_OK = {"generated_at": bound["public_generated_at"], "props": []}
empty = ps.build_epoch_selection(epoch=bound, payload=GEN_OK,
                                 universe=universe, pool=[], pa_scores={},
                                 schedule={})
check("no exposed champions -> None", empty is None)

print("\nCheck 13: the champion payload must be PROVEN to be this deployment's")
# A red team demonstrated by execution that payload was an unauthenticated
# caller-supplied file: an arbitrary JSON chosen after outcomes were known
# could define the entire champion arm. The generated_at hash join bound the
# SNAPSHOT and said nothing about the champion's basis.
check("a matching generated_at binds",
      ps.verify_payload_binding(GEN_OK, bound))
check("a DIFFERENT generated_at fails closed",
      raises(lambda: ps.verify_payload_binding(
          {"generated_at": "2026-01-01T00:00:00+00:00", "props": []}, bound),
          ps.EpochFailedClosed))
check("a missing generated_at fails closed",
      raises(lambda: ps.verify_payload_binding({"props": []}, bound),
             ps.EpochFailedClosed))
check("an epoch with no public_generated_at fails closed",
      raises(lambda: ps.verify_payload_binding(GEN_OK, {}),
             ps.EpochFailedClosed))
_sel_fn = next(n for n in _ast.walk(_ast.parse(src))
               if isinstance(n, _ast.FunctionDef) and n.name == "build_epoch_selection")
_sel_calls = {n.func.id for n in _ast.walk(_sel_fn)
              if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
check("build_epoch_selection VERIFIES the binding before anything else",
      "verify_payload_binding" in _sel_calls, str(sorted(_sel_calls)))

print("\nCheck 14: designate sorts PARSED instants, not strings")
# '+' is 0x2B and 'Z' is 0x5A, so a lexicographic sort orders "+00:00" before
# "Z" at the SAME instant and mis-orders any mixed-offset stamp.
import backtest.prospective_lifecycle as _plc
_d = next(n for n in _ast.walk(_ast.parse(open(
    "backtest/prospective_lifecycle.py").read()))
    if isinstance(n, _ast.FunctionDef) and n.name == "designate")
_dtxt = _ast.unparse(_d)
check("uses the parsed comparison", "_parse" in _dtxt)
check("does not sort raw strings", "str(ep.get('deployment_prepared_at')" not in _dtxt)
check("ties break deterministically on epoch id",
      "decisive_epoch_id" in _dtxt.split("sort")[-1][:220])

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective epoch/selection checks passed.")
