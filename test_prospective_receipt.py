"""Tests for prospective Hits PA-v1 receipts and the append-only ledger."""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from backtest import prospective_eligibility as pe
from backtest import prospective_ledger as pl
from backtest import prospective_receipt as pr

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
START = NOW + timedelta(hours=2)
GAME_PK = 776001
SCHEDULE = {GAME_PK: {"started": False, "start": START.isoformat(), "status": {}}}

EPOCH = {
    "decisive_epoch_id": "2026-09-02:run-1234567890",
    "workflow_run_id": "1234567890",
    "source_commit": "a" * 40,
    "board_generated_at": (NOW - timedelta(minutes=8)).isoformat(),
    "odds_fetched_at": (NOW - timedelta(minutes=3)).isoformat(),
}

META = {
    "model_version": "2026.08.15", "selection_policy_version": "1.0.0",
    "calibration_version": "1.0.0", "feature_version": "1.0.0",
    "odds_fetched_at": EPOCH["odds_fetched_at"],
    "board_generated_at": EPOCH["board_generated_at"],
}


def row(**over):
    r = {
        "type": "batter", "name": "Test Batter", "player_id": 99001,
        "team": "Testers", "side": "home", "matchup": "A @ B",
        "game_pk": GAME_PK, "prop": "Over 0.5 Hits",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.62, "sample_n": 140, "reliability": "A",
        "market_odds": -145, "score": 71.2, "status": "top_pick",
        "status_reasons": ["clears floor"],
        "signals": {"lineup_slot": 100.0, "days_rest": 0.0, "getaway_day": 0.0},
    }
    r.update(over)
    return r


def build(r=None, **over):
    r = r if r is not None else row()
    v = pe.evaluate_row(r, now=NOW, schedule=SCHEDULE,
                        odds_fetched_at=META["odds_fetched_at"],
                        board_generated_at=META["board_generated_at"])
    kw = dict(epoch=EPOCH, snapshot_id="snap-1", snapshot_sha256="b" * 64,
              slate_date="2026-09-02", pa_probability=0.5731,
              pa_fallback_state="joint_cell", champion_member=True,
              champion_rank=1, pa_member=True, pa_rank=2,
              board_metadata=META, repo_git_sha="c" * 40)
    kw.update(over)
    return pr.build_receipt(r, v, **kw)


print("Check 1: a receipt builds and self-verifies")
rec = build()
check("verify_receipt true", pr.verify_receipt(rec))
check("has content sha", len(rec["receipt_content_sha256"]) == 64)
check("has receipt id", len(rec["receipt_id"]) == 64)
check("schema version pinned", rec["receipt_schema_version"] == 1)
check("protocol sha carried", rec["protocol_sha256"] == pe.PROTOCOL_SHA256)
check("PA artifact sha is the AUTHORITATIVE freeze",
      rec["pa_v1_artifact_scientific_sha256"] ==
      "a4f598bd4138305d8da4d85767eb873781b10e918dd1e402d536d9cd13fadf4a")

print("\nCheck 2: NO OUTCOME FIELD, enforced in code not by convention")
for bad in ("outcome", "actual_hits", "final_result", "graded_at",
            "settlement_status", "did_hit", "realized_hits", "box_score_hits"):
    check(f"scanner blocks {bad!r}",
          raises(lambda b=bad: pr.assert_no_outcome({b: 1}), pr.OutcomeLeakError))
check("scanner walks nested dicts",
      raises(lambda: pr.assert_no_outcome({"a": {"b": {"outcome": 1}}}),
             pr.OutcomeLeakError))
check("scanner walks lists",
      raises(lambda: pr.assert_no_outcome({"a": [{"outcome": 1}]}),
             pr.OutcomeLeakError))
check("settlement_identity_key is exempt (pregame fact)",
      pr.assert_no_outcome({"settlement_identity_key": ["1", "x"]}))
check("reliability_grade is exempt", pr.assert_no_outcome({"reliability_grade": "A"}))
# build_receipt is an ALLOW-LIST projection: it copies named fields only, so a
# stray outcome field on the source row cannot reach the receipt at all. That
# is stronger than raising -- but it only covers the named fields. `signals` is
# copied WHOLESALE, so that is the one real leak surface, and it does raise.
leaky = build(row(outcome=1, actual_hits=3))
check("stray row outcome field is DROPPED, not copied",
      "outcome" not in leaky and "actual_hits" not in leaky)
check("dropped-field receipt is still outcome-free", pr.assert_no_outcome(leaky))
check("an outcome-shaped key inside signals (copied wholesale) RAISES",
      raises(lambda: build(row(signals={"actual_pa": 4})), pr.OutcomeLeakError))
check("no outcome-shaped key in a real receipt", pr.assert_no_outcome(rec))

print("\nCheck 3: the four identity facts survive into the receipt separately")
check("team_side home", rec["team_side"] == "home")
check("market_side over", rec["market_side"] == "over")
check("line 0.5", rec["line"] == 0.5)
check("needs 1", rec["needs"] == 1)
check("line and needs are different fields", rec["line"] != rec["needs"])
check("team_side and market_side are different fields",
      rec["team_side"] != rec["market_side"])

print("\nCheck 4: a later price is a DIFFERENT receipt state, not an edit")
moved = build(row(market_odds=-120))
check("same receipt_id (same epoch, same wager)",
      moved["receipt_id"] == rec["receipt_id"])
check("DIFFERENT content sha",
      moved["receipt_content_sha256"] != rec["receipt_content_sha256"])
check("odds are inside the hash", moved["odds_american"] == -120)

print("\nCheck 5: receipt_id is idempotent and arm-independent")
check("same inputs -> same id",
      pr.receipt_id("E", "P") == pr.receipt_id("E", "P"))
check("different epoch -> different id",
      pr.receipt_id("E1", "P") != pr.receipt_id("E2", "P"))
check("different prop -> different id",
      pr.receipt_id("E", "P1") != pr.receipt_id("E", "P2"))
champ_only = build(champion_member=True, pa_member=False, pa_rank=None)
check("arm membership does NOT change the id (no double counting)",
      champ_only["receipt_id"] == rec["receipt_id"])
check("both memberships live in one receipt",
      rec["champion_member"] is True and rec["pa_v1_member"] is True)

print("\nCheck 6: ledger appends, dedupes, and refuses overwrite")
tmp = tempfile.mkdtemp(prefix="ledger-test-")
path = os.path.join(tmp, "sub", "receipts.jsonl")
try:
    ev = pl.make_event(pl.EVENT_PREGAME_RECEIPT, rec["receipt_id"], rec)
    r1 = pl.append_events(path, [ev])
    check("first append writes", r1 == {"appended": 1, "duplicates": 0}, str(r1))
    r2 = pl.append_events(path, [ev])
    check("identical re-append is a no-op duplicate",
          r2 == {"appended": 0, "duplicates": 1}, str(r2))
    check("ledger still has exactly 1 event", len(pl.read_events(path)) == 1)

    conflict = pl.make_event(pl.EVENT_PREGAME_RECEIPT, rec["receipt_id"], moved)
    check("changed content under same key RAISES",
          raises(lambda: pl.append_events(path, [conflict]), pl.LedgerConflict))
    check("conflict left the ledger untouched", len(pl.read_events(path)) == 1)

    check("event hash is timestamp-independent",
          pl.make_event(pl.EVENT_PREGAME_RECEIPT, "k", {"a": 1})["event_content_sha256"]
          == pl.make_event(pl.EVENT_PREGAME_RECEIPT, "k", {"a": 1})["event_content_sha256"])

    print("\nCheck 7: settlement is a SEPARATE event type, never an edit")
    settle = pl.make_event(pl.EVENT_SETTLEMENT, rec["receipt_id"],
                           {"receipt_id": rec["receipt_id"],
                            "receipt_content_sha256": rec["receipt_content_sha256"],
                            "decision": "hit"})
    r3 = pl.append_events(path, [settle])
    check("settlement appends alongside the receipt",
          r3 == {"appended": 1, "duplicates": 0}, str(r3))
    events = pl.read_events(path)
    check("ledger now has 2 events", len(events) == 2)
    check("pregame receipt event is unchanged",
          events[0]["event_content_sha256"] == ev["event_content_sha256"])
    check("settlement carries no outcome INTO the pregame event",
          pr.assert_no_outcome(events[0]["body"]))
    check("a settlement event may carry a decision (different event type)",
          events[1]["body"]["decision"] == "hit")
    check("make_event refuses an outcome in a PREGAME event",
          raises(lambda: pl.make_event(pl.EVENT_PREGAME_RECEIPT, "k",
                                       {"outcome": 1}), pr.OutcomeLeakError))
    check("unknown event type rejected",
          raises(lambda: pl.make_event("whatever", "k", {}), ValueError))

    print("\nCheck 8: crash safety -- no partial line is ever left behind")
    for i in range(20):
        pl.append_events(path, [pl.make_event(pl.EVENT_EPOCH_BOUND, f"e{i}", {"i": i})])
    raw = open(path, "rb").read()
    check("file ends with a newline", raw.endswith(b"\n"))
    check("every line parses", len(pl.read_events(path)) == 22)
    check("no temp files left behind",
          not [f for f in os.listdir(os.path.dirname(path)) if f.startswith(".ledger-")])

    print("\nCheck 9: corrupt ledger fails loudly, not silently")
    with open(path, "a") as fh:
        fh.write("{not json\n")
    check("bad line raises LedgerConflict",
          raises(lambda: pl.read_events(path), pl.LedgerConflict))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nCheck 10: ledger targets a research branch, never main or the registry")
check("dedicated research branch",
      pl.LEDGER_BRANCH == "research-ledger/prospective-hits-pa-v1")
check("ledger path is namespaced",
      pl.LEDGER_RELPATH.startswith("prospective/hits_pa_v1/"))
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "backtest", "prospective_ledger.py")).read()
check("never writes public_top_picks/registry.json",
      "public_top_picks" not in src.split('"""', 2)[2])

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective receipt/ledger checks passed.")
