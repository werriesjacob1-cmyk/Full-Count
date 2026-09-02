"""Tests for the prospective Hits PA-v1 primary eligible pool gates.

Every check here exists because getting it wrong produces a shadow cohort
that looks plausible and is scientifically void.
"""

import sys
from datetime import datetime, timedelta, timezone

from backtest import prospective_eligibility as pe
from backtest import prospective_source_integrity as psi

# A genuinely-ran, genuinely-clean evaluation. Tests that are not ABOUT source
# integrity must supply one, because a missing evaluation is now UNKNOWN and
# correctly fails closed.
CLEAR = psi.evaluate(schedule={1: {}}, live_state={"reconciliation": {"mismatches": []}})

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


NOW = datetime(2026, 9, 2, 21, 0, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=2)
GAME_PK = 776001

SCHEDULE = {GAME_PK: {"started": False, "start": START.isoformat(), "status": {}}}


def base_row(**over):
    row = {
        "type": "batter",
        "name": "Test Batter",
        "player_id": 99001,
        "team": "Testers",
        "side": "home",              # TEAM side, not wager direction
        "matchup": "A @ B",
        "game_pk": GAME_PK,
        "prop": "Over 0.5 Hits",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.62,
        "sample_n": 140,
        "reliability": "A",
        "market_odds": -145,
        # lineup_assumed deliberately ABSENT -> confirmed
    }
    row.update(over)
    return row


def verdict(row, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("schedule", SCHEDULE)
    kw.setdefault("odds_fetched_at", (NOW - timedelta(minutes=3)).isoformat())
    kw.setdefault("board_generated_at", (NOW - timedelta(minutes=8)).isoformat())
    kw.setdefault("source_integrity", CLEAR)
    return pe.evaluate_row(row, **kw)


print("Check 1: a fully clean confirmed-lineup Hits row is eligible")
v = verdict(base_row())
check("clean row eligible", v["eligible"], f"failed={v['failed_gates']}")
check("all 15 gates evaluated", len(v["gates"]) == 15)

print("\nCheck 2: THE ABSENT-IS-NOT-FALSE TRAP")
# quality_control() never writes lineup_assumed=False; it leaves the key unset
# on confirmed rows. A literal `== False` transcription would empty the pool.
check("absent lineup_assumed reads as CONFIRMED",
      verdict(base_row())["gates"]["lineup_confirmed"])
check("explicit None reads as CONFIRMED",
      verdict(base_row(lineup_assumed=None))["gates"]["lineup_confirmed"])
check("explicit False reads as CONFIRMED",
      verdict(base_row(lineup_assumed=False))["gates"]["lineup_confirmed"])
check("True reads as ASSUMED -> rejected",
      not verdict(base_row(lineup_assumed=True))["gates"]["lineup_confirmed"])
check("assumed-lineup row is not eligible overall",
      not verdict(base_row(lineup_assumed=True))["eligible"])

print("\nCheck 3: evidence gates fail closed on absence")
check("sample_n None rejected",
      not verdict(base_row(sample_n=None))["gates"]["evidence_sample_nonzero"])
check("sample_n 0 rejected",
      not verdict(base_row(sample_n=0))["gates"]["evidence_sample_nonzero"])
check("sample_n 1 accepted",
      verdict(base_row(sample_n=1))["gates"]["evidence_sample_nonzero"])
check("reliability C rejected",
      not verdict(base_row(reliability="C"))["gates"]["reliability_a_or_b"])
check("reliability D rejected",
      not verdict(base_row(reliability="D"))["gates"]["reliability_a_or_b"])
check("reliability None rejected",
      not verdict(base_row(reliability=None))["gates"]["reliability_a_or_b"])
check("reliability B accepted",
      verdict(base_row(reliability="B"))["gates"]["reliability_a_or_b"])

print("\nCheck 4: no LINE_MOVED substitution")
check("no price rejected",
      not verdict(base_row(market_odds=None))["gates"]["real_current_price"])
check("LINE_MOVED rejected",
      not verdict(base_row(market_fetch_state="LINE_MOVED"))["gates"]["real_current_price"])
check("NOT_POSTED rejected",
      not verdict(base_row(market_fetch_state="NOT_POSTED"))["gates"]["real_current_price"])
check("FETCH_FAILED rejected",
      not verdict(base_row(market_fetch_state="FETCH_FAILED"))["gates"]["real_current_price"])
check("IN_PLAY rejected",
      not verdict(base_row(market_fetch_state="IN_PLAY"))["gates"]["real_current_price"])
check("MATCHED accepted",
      verdict(base_row(market_fetch_state="MATCHED"))["gates"]["real_current_price"])

print("\nCheck 5: publication cutoff is the PRODUCTION 15-minute contract")
check("imported, not redeclared", pe.PUBLICATION_LEAD_SECONDS == 15 * 60)
inside = verdict(base_row(), now=START - timedelta(minutes=14))
check("14 min from first pitch rejected",
      not inside["gates"]["before_publication_cutoff"])
# STRICT, matching production: before_betting_cutoff() is
# `publication_cutoff_at < game_start`, so exactly 15:00 is REJECTED. An
# inclusive comparison here would let the shadow hold a wager the site could
# not have published.
edge = verdict(base_row(), now=START - timedelta(minutes=15))
check("exactly 15 min from first pitch REJECTED (strict, as production)",
      not edge["gates"]["before_publication_cutoff"])
check("15 min + 1 second accepted",
      verdict(base_row(), now=START - timedelta(minutes=15, seconds=1)
              )["gates"]["before_publication_cutoff"])
check("16 min from first pitch accepted",
      verdict(base_row(), now=START - timedelta(minutes=16))["gates"]["before_publication_cutoff"])

print("\nCheck 6: commencement and resumption")
started_sched = {GAME_PK: {"started": True, "start": START.isoformat(), "status": {}}}
check("started game rejected",
      not verdict(base_row(), schedule=started_sched)["gates"]["commencement_not_occurred"])
check("missing schedule entry treated as commenced (fail closed)",
      not verdict(base_row(), schedule={})["gates"]["commencement_not_occurred"])
resumed = {GAME_PK: {"started": False, "start": START.isoformat(),
                     "resumedFrom": "2026-09-01", "status": {}}}
check("prior-date resumption rejected",
      not verdict(base_row(), schedule=resumed)["gates"]["not_prior_date_resumption"])
check("normal game passes resumption gate",
      verdict(base_row())["gates"]["not_prior_date_resumption"])

print("\nCheck 7: freshness bound to production constants")
check("stale price rejected",
      not verdict(base_row(),
                  odds_fetched_at=(NOW - timedelta(seconds=pe.MAX_PRICE_AGE_SECONDS + 60)).isoformat()
                  )["gates"]["price_freshness_valid"])
check("stale board rejected",
      not verdict(base_row(),
                  board_generated_at=(NOW - timedelta(seconds=pe.MAX_BOARD_AGE_SECONDS + 60)).isoformat()
                  )["gates"]["board_freshness_valid"])
check("missing odds_fetched_at rejected",
      not verdict(base_row(), odds_fetched_at=None, freshness={})["gates"]["price_freshness_valid"])
check("future-dated price rejected (negative age)",
      not verdict(base_row(),
                  odds_fetched_at=(NOW + timedelta(minutes=5)).isoformat()
                  )["gates"]["price_freshness_valid"])

print("\nCheck 8: market restriction and settlement support")
tb = base_row(projection={"stat": "total_bases", "value": 1.5, "needs": 2})
check("non-hits market rejected", not verdict(tb)["gates"]["stat_is_shadow_market"])
check("non-hits market not settlement-supported here",
      not verdict(tb)["gates"]["settlement_supported"])

print("\nCheck 9: NO model-policy gate leaks into the pool")
# A 0.31-probability row with everything operational still belongs in the
# challenger pool: predicted_prob >= 0.60 is champion SELECTION, not usability.
low = verdict(base_row(hit_probability=0.31))
check("low-probability row still eligible", low["eligible"], f"failed={low['failed_gates']}")
check("no probability gate exists",
      not any("prob" in g or "roi" in g or "value" in g for g in pe.GATES))

print("\nCheck 10: the four identity facts are never conflated")
e = verdict(base_row())["expression"]
check("team_side is home/away", e["team_side"] == "home")
check("market_side is a wager direction", e["market_side"] == "over")
check("team_side != market_side", e["team_side"] != e["market_side"])
check("line is projection.value (0.5)", e["line"] == 0.5)
check("needs is projection.needs (1)", e["needs"] == 1)
check("line != needs", e["line"] != e["needs"])
under = base_row(prop="Under 1.5 Hits",
                 projection={"stat": "hits", "value": 1.5, "needs": 2})
check("Under prop yields side 'under'",
      verdict(under)["expression"]["market_side"] == "under")
check("over and under are different identities",
      verdict(base_row())["canonical_prop_id"] != verdict(under)["canonical_prop_id"])

print("\nCheck 11: source integrity is CLEAR/HOLD/UNKNOWN and fails closed")
# THE ASYMMETRY THAT IS THE WHOLE CONTRACT: "we did not look" must never read
# as "we looked and it was fine".
check("NO evaluation supplied -> blocked (never defaults to CLEAR)",
      not pe.evaluate_row(base_row(), now=NOW, schedule=SCHEDULE,
                          odds_fetched_at=(NOW - timedelta(minutes=3)).isoformat(),
                          board_generated_at=(NOW - timedelta(minutes=8)).isoformat()
                          )["gates"]["no_source_integrity_hold"])
check("an evaluation that RAN clean -> CLEAR", CLEAR["state"] == psi.CLEAR)
check("CLEAR passes", verdict(base_row())["gates"]["no_source_integrity_hold"])
unk = psi.evaluate(schedule=SCHEDULE, live_state=None)
check("unreadable live.json -> UNKNOWN", unk["state"] == psi.UNKNOWN)
check("UNKNOWN blocks",
      not verdict(base_row(), source_integrity=unk)["gates"]["no_source_integrity_hold"])
check("UNKNOWN is not evaluated", unk["evaluated"] is False)
missing_recon = psi.evaluate(schedule=SCHEDULE, live_state={})
check("absent reconciliation block -> UNKNOWN", missing_recon["state"] == psi.UNKNOWN)
outage = psi.evaluate(schedule={}, live_state={"reconciliation": {"mismatches": []}})
check("whole-slate schedule outage -> HOLD", outage["state"] == psi.HOLD)
check("slate HOLD blocks every candidate",
      not verdict(base_row(), source_integrity=outage)["gates"]["no_source_integrity_hold"])
game_hold = psi.evaluate(schedule=SCHEDULE, live_state={"reconciliation": {
    "mismatches": [{"kind": "lineup", "game_pk": GAME_PK}]}})
check("lineup reconciliation mismatch -> HOLD", game_hold["state"] == psi.HOLD)
check("game-scoped HOLD blocks that game",
      not verdict(base_row(), source_integrity=game_hold)["gates"]["no_source_integrity_hold"])
check("game-scoped HOLD does NOT block a different game",
      verdict(base_row(game_pk=999),
              schedule={999: {"started": False, "start": START.isoformat(), "status": {}}},
              source_integrity=game_hold)["gates"]["no_source_integrity_hold"])
# LINE_MOVED is a real successful observation and already the price gate's
# job; counting it twice would attribute one rejection to two gates.
lm = psi.evaluate(schedule=SCHEDULE, live_state={"reconciliation": {
    "mismatches": [{"kind": "line_moved", "prop_id": "x"}]}})
check("line_moved is NOT a source-integrity hold", lm["state"] == psi.CLEAR)
check("every hold carries scope/key/reason/observed_at/authority",
      all({"scope", "key", "reason_code", "observed_at", "authority"} <= set(h)
          for h in outage["holds"] + game_hold["holds"]))
check("contract is versioned", CLEAR["contract_version"] == psi.CONTRACT_VERSION)

print("\nCheck 12: partition/funnel reports the full rejection funnel")
rows = [base_row(), base_row(lineup_assumed=True), base_row(reliability="C"),
        base_row(market_odds=None), base_row(sample_n=0)]
ok, bad = pe.partition(rows, now=NOW, schedule=SCHEDULE,
                       odds_fetched_at=(NOW - timedelta(minutes=3)).isoformat(),
                       board_generated_at=(NOW - timedelta(minutes=8)).isoformat(),
                       source_integrity=CLEAR)
check("1 eligible of 5", len(ok) == 1, f"got {len(ok)}")
check("4 rejected of 5", len(bad) == 4, f"got {len(bad)}")
counts = pe.funnel_counts(bad)
check("funnel counts lineup rejection", counts["lineup_confirmed"] == 1)
check("funnel counts reliability rejection", counts["reliability_a_or_b"] == 1)
check("funnel counts price rejection", counts["real_current_price"] == 1)
check("funnel counts evidence rejection", counts["evidence_sample_nonzero"] == 1)
check("every gate name present in funnel", set(counts) == set(pe.GATES))

print("\nCheck 13: gates are evaluated, not short-circuited")
broken = base_row(lineup_assumed=True, reliability="D", market_odds=None, sample_n=0)
v = verdict(broken)
check("multiple simultaneous failures all reported", len(v["failed_gates"]) >= 4,
      f"got {v['failed_gates']}")

print("\nCheck 14: protocol identity is pinned in code")
check("protocol sha pinned",
      pe.PROTOCOL_SHA256 ==
      "5ce1ae95c4d3034d7948eb0ad7bc2441efcf2cabb234944e36bc315b2b355de7")
check("protocol version pinned", pe.PROTOCOL_VERSION == "prospective-hits-pa-v1")

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective eligibility checks passed.")
