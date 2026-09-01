"""Tests for the same-production-event prospective shadow capture tap.

The single most important property under test is negative: this tap cannot
affect the customer board, no matter what goes wrong inside it.
"""

import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from backtest import prospective_capture as pc

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime(2026, 9, 3, 21, 0, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=2)
GEN = (NOW - timedelta(minutes=8)).isoformat()
ODDS = (NOW - timedelta(minutes=3)).isoformat()
SCHEDULE = {1: {"started": False, "start": START.isoformat(), "status": {},
                "resumed_from": None}}


def row(**over):
    r = {
        "type": "batter", "name": "Test Batter", "player_id": 99001,
        "team": "Testers", "side": "home", "matchup": "A @ B", "game_pk": 1,
        "prop": "Over 0.5 Hits",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.62, "sample_n": 140, "reliability": "A",
        "market_odds": -145, "score": 71.2, "status": "top_pick",
        # lineup_slot 100.0 decodes to batting order 1; days_rest/getaway_day
        # present so joint_key() resolves to a real cell.
        "signals": {"lineup_slot": 100.0, "days_rest": 0.0, "getaway_day": 0.0},
    }
    r.update(over)
    return r


def cap(rows, **kw):
    kw.setdefault("slate_date", "2026-09-03")
    kw.setdefault("board_generated_at", GEN)
    kw.setdefault("odds_fetched_at", ODDS)
    kw.setdefault("schedule", SCHEDULE)
    kw.setdefault("persist", False)
    kw.setdefault("now", NOW)
    return pc.capture(rows, **kw)


print("Check 1: the artifact must be the AUTHORITATIVE freeze")
art = pc.load_artifact()
check("artifact loads and verifies",
      art["scientific_content_sha256"] ==
      "a4f598bd4138305d8da4d85767eb873781b10e918dd1e402d536d9cd13fadf4a")
check("effective_from is present and explicit", bool(art.get("effective_from")))
bad = os.path.join(HERE, "backtest", "_bad_artifact_test.json")
try:
    tampered = copy.deepcopy(art)
    tampered["tables"]["hit_rate_given_pa"]["1"] = 0.99
    with open(bad, "w") as fh:
        json.dump(tampered, fh)
    try:
        pc.load_artifact(bad)
        check("an edited artifact is refused", False, "no error raised")
    except ValueError as exc:
        check("an edited artifact is refused", "does not verify" in str(exc))
finally:
    if os.path.exists(bad):
        os.remove(bad)

print("\nCheck 2: capture NEVER raises, whatever it is handed")
for label, rows in [("None", None), ("empty", []),
                    ("garbage strings", ["not a row", 7]),
                    ("row with no projection", [{"game_pk": 1}]),
                    ("row with a None game_pk", [row(game_pk=None)]),
                    ("deeply broken row", [{"projection": None, "signals": 5}])]:
    try:
        rep = cap(rows)
        check(f"survives {label}", isinstance(rep, dict))
    except BaseException as exc:
        check(f"survives {label}", False, f"raised {type(exc).__name__}: {exc}")
try:
    rep = cap([row()], schedule=None)
    check("survives a None schedule", isinstance(rep, dict))
except BaseException as exc:
    check("survives a None schedule", False, str(exc))
try:
    rep = cap([row()], artifact_path="/nonexistent/artifact.json")
    check("survives a missing artifact", rep["ok"] is False and rep["error"])
except BaseException as exc:
    check("survives a missing artifact", False, str(exc))

print("\nCheck 3: capture does not mutate the rows it observes")
rows = [row(), row(player_id=99002, game_pk=1)]
before = copy.deepcopy(rows)
cap(rows)
check("input rows are byte-identical afterwards", rows == before)

print("\nCheck 4: a clean row is captured and PA-v1 scored")
rep = cap([row()])
check("capture ok", rep.get("ok") is True, str(rep.get("error")))
check("1 raw row", rep.get("raw_count") == 1)
check("1 eligible", rep.get("eligible_count") == 1, str(rep.get("rejection_funnel")))
check("PA-v1 produced a score", rep.get("pa_scored") == 1)
check("snapshot hash recorded", len(rep.get("snapshot_content_sha256") or "") == 64)
check("epoch candidate id recorded", bool(rep.get("epoch_candidate_id")))
check("dry run did not persist", rep.get("persisted") is False)

print("\nCheck 5: ineligible rows are captured as rejections, not dropped silently")
rep = cap([row(), row(player_id=2, lineup_assumed=True),
           row(player_id=3, market_odds=None)])
check("3 raw rows seen", rep.get("raw_count") == 3)
check("1 eligible", rep.get("eligible_count") == 1)
check("2 rejected", rep.get("rejected_count") == 2)
funnel = rep.get("rejection_funnel") or {}
check("assumed lineup counted in the funnel", funnel.get("lineup_confirmed") == 1)
check("missing price counted in the funnel", funnel.get("real_current_price") == 1)

print("\nCheck 6: effective_from is a hard preregistration boundary")
eff = art["effective_from"]
before_eff = (datetime.fromisoformat(eff.replace("Z", "+00:00"))
              - timedelta(days=1)).isoformat()
rep = cap([row()], board_generated_at=before_eff)
check("a slate before effective_from is SKIPPED", rep.get("skipped") is True)
check("skip is reported, not silent", "effective_from" in (rep.get("reason") or ""))
check("skip is not an error", rep.get("ok") is True)

print("\nCheck 7: PA-v1 fallback state is explicit, never collapsed to null")
from backtest import prospective_eligibility as _pe
pool, _rej = _pe.partition([row()], now=NOW, schedule=SCHEDULE,
                           odds_fetched_at=ODDS, board_generated_at=GEN)
scores, states = pc.score_pool(pool, art)
pid = pool[0][1]["canonical_prop_id"]
check("a full joint cell is labelled as such", states[pid] == "joint_cell")
check("the score is a real probability", 0.0 < scores[pid] < 1.0)
# No batting order at all -> unscorable, and labelled as unscorable rather
# than silently indistinguishable from a confident score.
pool2, _ = _pe.partition([row(signals={})], now=NOW, schedule=SCHEDULE,
                         odds_fetched_at=ODDS, board_generated_at=GEN)
s2, st2 = pc.score_pool(pool2, art)
pid2 = pool2[0][1]["canonical_prop_id"]
check("no batting order -> None score", s2[pid2] is None)
check("no batting order -> labelled unscorable",
      st2[pid2] == "unscorable_no_batting_order")
# Order present but a joint dimension missing -> order marginal fallback.
pool3, _ = _pe.partition([row(signals={"lineup_slot": 100.0})], now=NOW,
                         schedule=SCHEDULE, odds_fetched_at=ODDS,
                         board_generated_at=GEN)
s3, st3 = pc.score_pool(pool3, art)
pid3 = pool3[0][1]["canonical_prop_id"]
check("partial signals -> order marginal fallback",
      st3[pid3] == "order_marginal_fallback")
check("fallback still yields a score", s3[pid3] is not None)

print("\nCheck 8: the tap sits at the protocol section 4 boundary")
src = open(os.path.join(HERE, "dashboard", "build_dashboard.py")).read()
i_rec = src.index("gprec.attach_recommendations(all_candidates_for_rec")
i_tap = src.index("prospective_capture as _shadow")
i_clean = src.index("def clean(rows):")
i_started = src.index("Game-start filter: removed")
check("tap is AFTER attach_recommendations", i_rec < i_tap)
check("tap is BEFORE clean() strips fields", i_tap < i_clean)
check("tap is AFTER the game-start filter", i_started < i_tap)
check("tap reads by_category_full['hits']",
      'by_category_full.get("hits")' in src)

print("\nCheck 9: the tap cannot affect production output")
tap = src[i_tap - 2000:i_clean]
check("tap is inside a try/except", "except BaseException as _shadow_exc" in tap)
check("tap assigns nothing back into the board",
      "by_category_full =" not in tap and "moonshots_full =" not in tap)
check("tap does not call select_ or attach_ again",
      "gp.select_" not in tap and "attach_market_prices" not in tap)
check("persistence is opt-in via env, off by default in a plain build",
      'FULLCOUNT_SHADOW_PERSIST' in tap)

print("\nCheck 10: schedule resumption fields are additive and inert")
check("resumed_from captured", '"resumed_from": g.get("resumedFrom")' in src)
check("existing started key preserved", '"started": g.get("status", {})' in src)
check("existing start key preserved", '"start": g.get("gameDate")' in src)

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective capture checks passed.")
