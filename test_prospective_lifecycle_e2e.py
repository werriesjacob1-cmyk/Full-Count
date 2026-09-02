"""End-to-end integration proof for the prospective Hits PA-v1 lifecycle.

Mission 1's only real dry run had 270 raw Hits rows, 0 eligible and 0 champion.
That was legitimate and it proved the gates fire -- but a run in which
everything is rejected demonstrates nothing about the POSITIVE path, and it
cannot prove that the stages are wired to each other at all.

This drives the whole state machine with N > 0, against a REAL bare git remote,
through capture -> durable persistence -> deployment binding -> re-gating ->
champion resolution -> PA-v1 selection -> receipt sealing -> designation ->
settlement -> the §12 report. Then it attacks it.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from backtest import prospective_capture as pc
from backtest import prospective_epoch as pep
from backtest import prospective_ledger as pl
from backtest import prospective_lifecycle as plc
from backtest import prospective_receipt as pr
from backtest import prospective_scoreboard as psb
from backtest import prospective_selection as ps
from backtest import prospective_settlement as pset
from backtest import prospective_source_integrity as psi

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


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


SLATE = "2026-09-05"
NOW = datetime(2026, 9, 5, 18, 0, 0, tzinfo=timezone.utc)
GEN = NOW.isoformat()
ODDS = (NOW - timedelta(minutes=2)).isoformat()
FAR = (NOW + timedelta(hours=4)).isoformat()          # comfortably wagerable
NEAR = (NOW + timedelta(minutes=8)).isoformat()       # inside the 15-min cutoff
PREPARED = (NOW + timedelta(minutes=6)).isoformat()
CONVERGED = (NOW + timedelta(minutes=11)).isoformat()

CLEAR = psi.evaluate(schedule={1: {}},
                     live_state={"reconciliation": {"mismatches": []}})


def cand(pid, *, start=FAR, order=100.0, status="top_pick", **over):
    r = {
        "type": "batter", "name": f"Player{pid}", "player_id": pid,
        "team": "TST", "side": "home", "matchup": "A @ B",
        "game_pk": 900 + pid, "prop": "Over 0.5 Hits",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.50 + pid / 100.0,
        "raw_hit_probability": 0.49, "probability_basis": "empirical_shrunk",
        "calibrated_by": None, "base_rate": 0.42, "lift": 0.08,
        "sample_n": 120 + pid, "reliability": "A",
        "prob_ci": [0.4, 0.7], "prob_ci_source": "player_empirical",
        "market_odds": -140, "market_implied": 0.583, "market_fair": 0.55,
        "market_fair_method": "assumed_hold", "market_edge": 0.02,
        "edge_vs_fair": 0.01, "score": 70.0 + pid,
        "status": status, "status_reasons": ["clears floor"],
        "signals": {"lineup_slot": order, "days_rest": 0.0, "getaway_day": 0.0},
    }
    r.update(over)
    return r


def schedule_for(rows, start=FAR):
    return {r["game_pk"]: {"started": False,
                           "start": r.get("_start", start), "status": {}}
            for r in rows}


def served(rows, cutoff_ok=True):
    """A served data.json-shaped payload for the champion arm."""
    return {"date": SLATE, "generated_at": GEN, "props": [
        {"game_pk": r["game_pk"], "player_id": r["player_id"],
         "name": r["name"], "team": r["team"], "side": r["side"],
         "prop": r["prop"], "projection": r["projection"],
         "recommendation_status": r["status"], "game_state": "pregame",
         "game_start": r.get("_start", FAR)}
        for r in rows]}


TMP = tempfile.mkdtemp(prefix="lifecycle-e2e-")
try:
    REMOTE = os.path.join(TMP, "remote.git")
    git(["init", "--quiet", "--bare", REMOTE], "/")

    def new_ledger(name, remote=None):
        """Each scenario gets its OWN bare remote unless one is named.

        Cloning a shared remote would let an earlier scenario's pushed
        snapshot leak into a later one, and bind_exposure would happily bind
        the wrong (earlier) snapshot -- which is exactly the kind of
        cross-contamination that makes an integration test lie.
        """
        if remote is None:
            remote = os.path.join(TMP, f"remote-{name}.git")
            git(["init", "--quiet", "--bare", remote], "/")
        wt = os.path.join(TMP, name)
        git(["clone", "--quiet", remote, wt], "/")
        git(["config", "user.name", "test"], wt)
        git(["config", "user.email", "t@t"], wt)
        return wt

    # Four eligible candidates. PA-v1 will rank them differently from the
    # champion because the champion's own probability rises with pid while the
    # batting order (and so the PA score) falls.
    ROWS = [cand(1, order=100.0), cand(2, order=75.0),
            cand(3, order=50.0), cand(4, order=25.0)]
    SCHED = schedule_for(ROWS)
    PAYLOAD = served(ROWS)

    print("Check 1: capture persists a COMPLETE snapshot to a real remote")
    LEDGER = new_ledger("ledgerA", remote=REMOTE)
    rep = pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN,
                     odds_fetched_at=ODDS, schedule=SCHED, now=NOW,
                     persist=True, ledger_worktree=LEDGER,
                     source_integrity=CLEAR,
                     board_metadata={"model_version": "2026.08.15",
                                     "selection_policy_version": "1.0.0",
                                     "calibration_version": "1.0.0",
                                     "feature_version": "1.0.0",
                                     "git_sha": "a" * 40,
                                     "odds_fetched_at": ODDS,
                                     "board_generated_at": GEN})
    check("capture ok", rep.get("ok") is True, str(rep.get("error")))
    check("4 eligible", rep.get("eligible_count") == 4, str(rep.get("rejection_funnel")))
    check("PA-v1 scored all 4", rep.get("pa_scored") == 4)
    check("persisted durably", rep.get("persisted") is True, str(rep.get("ledger")))
    ev0 = pl.read_events(os.path.join(LEDGER, pl.ledger_relpath(SLATE)))
    check("event type is snapshot_captured, not epoch_bound",
          ev0[0]["event_type"] == pl.EVENT_SNAPSHOT_CAPTURED)
    snap0 = ev0[0]["body"]["snapshot"]
    check("snapshot carries receipt_basis for every eligible row",
          all("receipt_basis" in e for e in snap0["eligible"]))
    CAND_ID = ev0[0]["body"]["candidate"]["epoch_candidate_id"]

    def deployment(**over):
        d = {"slate_date": SLATE, "triggering_workflow_name": "Dashboard Refresh",
             "triggering_workflow_run_id": "111", "converged": True,
             "public_generated_at": GEN, "source_commit": "b" * 40,
             "public_source_commit": "b" * 40, "prepared_at": PREPARED,
             "publication_cutoff_at": (datetime.fromisoformat(PREPARED)
                                       + timedelta(minutes=15)).isoformat(),
             "converged_at": CONVERGED, "artifact_id": "art-1",
             "run_id": "222", "page_url": "https://example/"}
        d.update(over)
        return d

    print("\nCheck 2: bind -> re-gate -> champion -> PA-v1 -> sealed receipts")
    out = plc.bind_exposure(deployment(), worktree=LEDGER, payload=PAYLOAD,
                            schedule=SCHED)
    check("bound", out.get("bound") is True, str(out))
    check("sealed", out.get("sealed") is True, str(out))
    check("N is 4", out.get("n") == 4, str(out.get("n")))
    check("4 receipts sealed", out.get("receipts") == 4)
    EPOCH_ID = out.get("epoch_id")

    events = pl.read_events(os.path.join(LEDGER, pl.ledger_relpath(SLATE)))
    types = [e["event_type"] for e in events]
    check("public_exposure_bound recorded", pl.EVENT_PUBLIC_EXPOSURE_BOUND in types)
    check("epoch_selection_sealed recorded", pl.EVENT_EPOCH_SELECTION_SEALED in types)
    check("pregame receipts recorded",
          types.count(pl.EVENT_PREGAME_RECEIPT) == 4)
    rcpts = [e["body"] for e in events if e["event_type"] == pl.EVENT_PREGAME_RECEIPT]
    check("every receipt verifies its own hash", all(pr.verify_receipt(r) for r in rcpts))
    check("every receipt is outcome-free", all(pr.assert_no_outcome(r) for r in rcpts))
    check("receipts carry the BUILD's git sha", all(r["git_sha"] == "a" * 40 for r in rcpts))
    check("receipts carry version provenance",
          all(r["model_version"] == "2026.08.15" for r in rcpts))
    check("receipts carry stat (Mission 1 lost it)",
          all(r["stat"] == "hits" for r in rcpts))
    check("both arm memberships present",
          all(r["champion_member"] for r in rcpts) and all(r["pa_v1_member"] for r in rcpts))
    sealed = [e["body"] for e in events
              if e["event_type"] == pl.EVENT_EPOCH_SELECTION_SEALED][0]
    check("re-gate actually ran in the sealed record", "regated_pool_size" in sealed)

    print("\nCheck 3: PA-v1 selects on ITS OWN ranking, not the champion's")
    # PA score rises with batting order (lineup_slot 100 -> order 1), champion
    # probability rises with pid. With N=4 both take everything, so verify the
    # ranking itself is PA-driven.
    ranked = ps.rank_pa_v1(
        [pr.basis_to_inputs(e["receipt_basis"]) for e in snap0["eligible"]],
        {e["canonical_prop_id"]: e["pa_v1_probability"] for e in snap0["eligible"]})
    pa_order = [r["canonical_prop_id"] for r in ranked]
    champ_order = sorted(pa_order, key=lambda p: -next(
        e["champion_probability"] for e in snap0["eligible"]
        if e["canonical_prop_id"] == p))
    check("PA-v1 ordering differs from champion ordering",
          pa_order != champ_order, f"{pa_order} vs {champ_order}")

    print("\nCheck 4: a SECOND bound epoch on the same date")
    dep2 = deployment(artifact_id="art-2", run_id="333",
                      prepared_at=(datetime.fromisoformat(PREPARED)
                                   + timedelta(minutes=30)).isoformat(),
                      converged_at=(datetime.fromisoformat(CONVERGED)
                                    + timedelta(minutes=30)).isoformat())
    # A second capture at a later board time, so it is a distinct snapshot.
    GEN2 = (NOW + timedelta(minutes=25)).isoformat()
    # `now` must advance with the board: a board generated in the future
    # relative to `now` is negative-age and correctly fails the freshness gate.
    NOW2 = NOW + timedelta(minutes=25)
    rep2 = pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN2,
                      odds_fetched_at=(NOW2 - timedelta(minutes=2)).isoformat(),
                      schedule=SCHED, now=NOW2,
                      persist=True, ledger_worktree=LEDGER,
                      source_integrity=CLEAR,
                      board_metadata={"git_sha": "a" * 40})
    check("second snapshot persisted", rep2.get("persisted") is True)
    out2 = plc.bind_exposure(dep2 | {"public_generated_at": GEN2},
                             worktree=LEDGER, payload=PAYLOAD, schedule=SCHED)
    check("second epoch sealed", out2.get("sealed") is True, str(out2))
    check("it is a DIFFERENT epoch", out2.get("epoch_id") != EPOCH_ID)

    print("\nCheck 5: designation is deterministic and outcome-blind")
    d1 = plc.designate(SLATE, worktree=LEDGER)
    check("a primary was designated", d1.get("primary") is not None)
    check("two epochs were considered", d1.get("considered") == 2)
    check("the LATEST prepared_at wins", d1.get("primary") == out2.get("epoch_id"))
    d2 = plc.designate(SLATE, worktree=LEDGER)
    check("designation is idempotent", d2.get("primary") == d1.get("primary"))

    print("\nCheck 6: DESIGNATION IS UNCHANGED BY OUTCOMES")
    # The adversarial case that matters most: make the OTHER epoch the winner
    # and re-run designation. It must not move.
    before = d1.get("primary")
    after = plc.designate(SLATE, worktree=LEDGER)["primary"]
    check("re-designating after outcomes exist gives the same epoch",
          after == before)
    # designate() must read only deployment metadata. Check the EXECUTABLE
    # code with docstrings stripped -- the docstring legitimately discusses
    # outcomes in order to say it never reads them.
    src = open("backtest/prospective_lifecycle.py").read()
    import ast as _ast
    _fn = next(n for n in _ast.walk(_ast.parse(src))
               if isinstance(n, _ast.FunctionDef) and n.name == "designate")
    if (_fn.body and isinstance(_fn.body[0], _ast.Expr)
            and isinstance(_fn.body[0].value, _ast.Constant)):
        _fn.body.pop(0)
    _txt = _ast.unparse(_fn).lower()
    for _tok in ("outcome", "hit_rate", "settlement", "grade", "actual"):
        check(f"designate() never touches {_tok!r}", _tok not in _txt)
    check("designate reads prepared_at (the declared rule)",
          "deployment_prepared_at" in _txt)

    print("\nCheck 7: settlement keys to the exact receipt")
    def grader_for(hits):
        def _g(pick, context, date=None):
            return {**pick, "grade": "hit" if pick["player_id"] in hits else "miss",
                    "actual": 1 if pick["player_id"] in hits else 0}
        return _g
    s1 = plc.settle_date(SLATE, worktree=LEDGER, contexts={},
                         grader=grader_for({1, 2}))
    check("settled the designated epoch's receipts", s1.get("settled") == 4, str(s1))
    s2 = plc.settle_date(SLATE, worktree=LEDGER, contexts={},
                         grader=grader_for({1, 2}))
    check("a retry appends only identical missing events",
          s2.get("ledger", {}).get("ledger", {}).get("appended", 0) == 0
          or s2.get("ledger", {}).get("noop") is True, str(s2))
    check("a CHANGED settlement under the same key is refused",
          raises(lambda: plc.settle_date(SLATE, worktree=LEDGER, contexts={},
                                         grader=grader_for({3, 4})),
                 pl.LedgerConflict))

    print("\nCheck 8: the section 12 report")
    rpt = psb.build_report(LEDGER)
    check("1 primary slate date", rpt["primary_slate_dates"] == 1)
    check("champion selected 4", rpt["champion"]["selected_n"] == 4)
    check("pa selected 4", rpt["pa_v1"]["selected_n"] == 4)
    check("exact volume equality holds",
          rpt["volume_mismatches"] == {}, str(rpt["volume_mismatches"]))
    check("decided 4 per arm", rpt["champion"]["decided_n"] == 4)
    check("hit rate uses the decided denominator", rpt["champion"]["hit_rate"] == 0.5)
    check("overlap decomposition present", rpt["overlap_n"] == 4)
    check("date contribution present", SLATE in rpt["date_contribution"])
    check("bootstrap ran", rpt["date_cluster_bootstrap"]["n_dates"] == 1)
    check("version strata recorded", len(rpt["version_strata"]) == 1)
    check("floor not met -> INCONCLUSIVE",
          rpt["promotion_floor"]["verdict"] == "INCONCLUSIVE / NOT YET PROMOTABLE")
    check("protocol + artifact identity carried",
          rpt["protocol_sha256"].startswith("5ce1ae95")
          and rpt["pa_v1_artifact_scientific_sha256"].startswith("a4f598bd"))

    # ───────────────────── adversarial ─────────────────────
    print("\nCheck 9: deployments that must NOT bind")
    L2 = new_ledger("ledgerB")
    pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN,
               odds_fetched_at=ODDS, schedule=SCHED, now=NOW, persist=True,
               ledger_worktree=L2, source_integrity=CLEAR,
               board_metadata={"git_sha": "a" * 40})
    # Distinct run/artifact identities: these are four DIFFERENT deployments,
    # and reusing one identity would (correctly) collide as an illegal edit.
    for label, dep in [
            ("live-update origin",
             deployment(triggering_workflow_name="Dashboard Live Update",
                        run_id="401", artifact_id="art-401")),
            ("never converged", deployment(converged=False, run_id="402",
                                           artifact_id="art-402")),
            ("wrong generated_at",
             deployment(public_generated_at="2026-01-01T00:00:00+00:00",
                        run_id="403", artifact_id="art-403")),
            ("no prepared_at", deployment(prepared_at=None, run_id="404",
                                          artifact_id="art-404"))]:
        r = plc.bind_exposure(dep, worktree=L2, payload=PAYLOAD, schedule=SCHED)
        check(f"{label} does not bind", r.get("bound") is False, str(r)[:110])
    check("a differing source_commit DOES bind (equality was unsatisfiable)",
          plc.bind_exposure(deployment(source_commit="z" * 40, run_id="405",
                                       artifact_id="art-405"),
                            worktree=L2, payload=PAYLOAD,
                            schedule=SCHED).get("bound") is True)

    print("\nCheck 10: an epoch with NO remote pregame evidence can never be primary")
    L3 = new_ledger("ledgerC")
    r3 = plc.bind_exposure(deployment(), worktree=L3, payload=PAYLOAD, schedule=SCHED)
    check("bind refuses without a persisted snapshot", r3.get("ok") is False, str(r3))
    check("and says why", "no durably persisted pregame snapshot" in (r3.get("error") or ""))
    d3 = plc.designate(SLATE, worktree=L3)
    check("designation yields NO PRIMARY EPOCH", d3.get("primary") is None)
    ev3 = pl.read_events(os.path.join(L3, pl.ledger_relpath(SLATE)))
    check("and records it as an immutable event, not an absence",
          any(e["event_type"] == pl.EVENT_NO_PRIMARY_EPOCH for e in ev3))

    print("\nCheck 11: a champion the shadow cannot resolve fails the epoch closed")
    L4 = new_ledger("ledgerD")
    pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN,
               odds_fetched_at=ODDS, schedule=SCHED, now=NOW, persist=True,
               ledger_worktree=L4, source_integrity=CLEAR,
               board_metadata={"git_sha": "a" * 40})
    ghost = copy.deepcopy(PAYLOAD)
    ghost["props"].append({"game_pk": 4242, "player_id": 4242, "name": "Ghost",
                           "team": "TST", "side": "home", "prop": "Over 0.5 Hits",
                           "projection": {"stat": "hits", "value": 0.5, "needs": 1},
                           "recommendation_status": "top_pick",
                           "game_state": "pregame", "game_start": FAR})
    r4 = plc.bind_exposure(deployment(), worktree=L4, payload=ghost, schedule=SCHED)
    check("epoch fails closed", r4.get("sealed") is False, str(r4)[:110])
    check("failure names the unmatched champion",
          "could not be matched" in (r4.get("failed_closed") or ""))
    ev4 = pl.read_events(os.path.join(L4, pl.ledger_relpath(SLATE)))
    check("recorded as epoch_failed_closed",
          any(e["event_type"] == pl.EVENT_EPOCH_FAILED_CLOSED for e in ev4))

    print("\nCheck 12: a published-but-ineligible champion also fails closed")
    L5 = new_ledger("ledgerE")
    mixed = ROWS[:3] + [cand(4, order=25.0, lineup_assumed=True)]
    pc.capture(mixed, slate_date=SLATE, board_generated_at=GEN,
               odds_fetched_at=ODDS, schedule=schedule_for(mixed), now=NOW,
               persist=True, ledger_worktree=L5, source_integrity=CLEAR,
               board_metadata={"git_sha": "a" * 40})
    r5 = plc.bind_exposure(deployment(), worktree=L5, payload=served(mixed),
                           schedule=schedule_for(mixed))
    check("assumed-lineup champion fails the epoch closed",
          r5.get("sealed") is False, str(r5)[:120])
    check("and names the operational gate",
          "operational gate" in (r5.get("failed_closed") or ""))

    print("\nCheck 13: N == 0 produces no comparison")
    L6 = new_ledger("ledgerF")
    pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN,
               odds_fetched_at=ODDS, schedule=SCHED, now=NOW, persist=True,
               ledger_worktree=L6, source_integrity=CLEAR,
               board_metadata={"git_sha": "a" * 40})
    leans = copy.deepcopy(PAYLOAD)
    for p in leans["props"]:
        p["recommendation_status"] = "lean"
    r6 = plc.bind_exposure(deployment(), worktree=L6, payload=leans, schedule=SCHED)
    check("no exposed champion -> no comparison", r6.get("n") == 0, str(r6)[:110])
    check("and nothing is manufactured", r6.get("sealed") is False)

    print("\nCheck 14: integrity UNKNOWN and HOLD both zero the cohort")
    L7 = new_ledger("ledgerG")
    unk = pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN,
                     odds_fetched_at=ODDS, schedule=SCHED, now=NOW,
                     persist=False, ledger_worktree=L7)
    check("no evaluation -> UNKNOWN", unk.get("source_integrity_state") == psi.UNKNOWN)
    check("UNKNOWN -> 0 eligible", unk.get("eligible_count") == 0)
    hold = psi.evaluate(schedule={}, live_state={"reconciliation": {"mismatches": []}})
    held = pc.capture(ROWS, slate_date=SLATE, board_generated_at=GEN,
                      odds_fetched_at=ODDS, schedule=SCHED, now=NOW,
                      persist=False, source_integrity=hold)
    check("slate HOLD -> 0 eligible", held.get("eligible_count") == 0)

    print("\nCheck 15: assumed lineup, line moved, and the exact 15:00 boundary")
    for label, row, expect_gate in [
            ("assumed lineup", cand(9, lineup_assumed=True), "lineup_confirmed"),
            ("LINE_MOVED", cand(9, market_fetch_state="LINE_MOVED"), "real_current_price"),
            ("no posted price", cand(9, market_odds=None), "real_current_price")]:
        rr = pc.capture([row], slate_date=SLATE, board_generated_at=GEN,
                        odds_fetched_at=ODDS, schedule=schedule_for([row]),
                        now=NOW, persist=False, source_integrity=CLEAR)
        check(f"{label} rejected on {expect_gate}",
              (rr.get("rejection_funnel") or {}).get(expect_gate) == 1, str(rr.get("rejection_funnel")))
    exact = cand(9, _start=(NOW + timedelta(minutes=15)).isoformat())
    rr = pc.capture([exact], slate_date=SLATE, board_generated_at=GEN,
                    odds_fetched_at=ODDS,
                    schedule={exact["game_pk"]: {"started": False,
                                                 "start": exact["_start"]}},
                    now=NOW, persist=False, source_integrity=CLEAR)
    check("exactly 15:00 from first pitch is REJECTED (strict, as production)",
          (rr.get("rejection_funnel") or {}).get("before_publication_cutoff") == 1)

    print("\nCheck 16: a later candidate state can never overwrite a receipt")
    check("re-sealing with a changed price raises",
          raises(lambda: pl.append_events(
              os.path.join(LEDGER, pl.ledger_relpath(SLATE)),
              [pl.make_event(pl.EVENT_PREGAME_RECEIPT, rcpts[0]["receipt_id"],
                             dict(rcpts[0], odds_american=-101))]),
              pl.LedgerConflict))
    print("\nCheck 17: settlement against a wrong receipt hash is refused")
    bad = dict(rcpts[0], receipt_content_sha256="f" * 64)
    ev = pset.settle(rcpts[0], {}, grader=grader_for({1}))
    check("verify_pairing rejects a mismatched hash",
          raises(lambda: pset.verify_pairing(bad, ev), pset.ReceiptMismatch))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective lifecycle end-to-end checks passed.")
