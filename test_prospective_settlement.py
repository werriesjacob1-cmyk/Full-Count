"""Tests for prospective settlement and the frozen bootstrap contract."""

import random
import sys

from backtest import prospective_bootstrap as pb
from backtest import prospective_settlement as pset

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


RECEIPT = {
    "receipt_id": "r" * 64, "receipt_content_sha256": "s" * 64,
    "decisive_epoch_id": "E1", "canonical_prop_id": "P1",
    "slate_date": "2026-09-03", "game_pk": 776001, "player_id": 99001,
    "player_name": "Test Batter", "team": "Testers", "matchup": "A @ B",
    "game_start": "2026-09-03T23:10:00+00:00",
    "stat": "hits", "team_side": "home", "market_side": "over",
    "line": 0.5, "needs": 1, "prop_label": "Over 0.5 Hits",
    "recommendation_status": "top_pick",
    "champion_member": True, "pa_v1_member": False,
}

print("Check 1: the wager is reconstructed from the RECEIPT alone")
pick = pset.reconstruct_pick(RECEIPT)
check("game_pk from receipt", pick["game_pk"] == 776001)
check("needs from receipt", pick["projection"]["needs"] == 1)
check("line from receipt", pick["projection"]["value"] == 0.5)
check("stat from receipt", pick["projection"]["stat"] == "hits")
check("market_side carried under its own key (grade_results reads it)",
      pick["market_side"] == "over")
check("team_side is NOT passed as a wager direction",
      pick.get("side") is None and pick.get("bet_side") is None)

# grade_results._is_under_pick reads market_side directly and refuses to infer
# direction from a display string. Prove the receipt's sealed direction wins.
from grade_results import _is_under_pick
check("an 'over' receipt is not an under",
      not _is_under_pick(pset.reconstruct_pick(RECEIPT)))
under_receipt = dict(RECEIPT, market_side="under", prop_label="Under 1.5 Hits",
                     line=1.5, needs=2)
check("an 'under' receipt IS an under",
      _is_under_pick(pset.reconstruct_pick(under_receipt)))
check("direction survives even with a misleading label",
      _is_under_pick(pset.reconstruct_pick(
          dict(RECEIPT, market_side="under", prop_label="Over 0.5 Hits"))))

print("\nCheck 2: load_latest_records is never reachable from this path")
src = open("backtest/prospective_settlement.py").read()
check("no import of candidate_funnel_grader",
      "candidate_funnel_grader" not in src.split('"""', 2)[2])
check("no load_latest_records call",
      "load_latest_records" not in src.split('"""', 2)[2])


def fake_grader(result):
    def _g(pick, context, date=None):
        return {**pick, **result}
    return _g


print("\nCheck 3: all four outcomes are recorded distinctly")
for grade in ("hit", "miss", "void", "ungraded"):
    ev = pset.settle(RECEIPT, {}, grader=fake_grader({"grade": grade}))
    check(f"{grade} recorded", ev["outcome"] == grade)
    check(f"{grade} decided flag correct",
          ev["decided"] == (grade in ("hit", "miss")))
unknown = pset.settle(RECEIPT, {}, grader=fake_grader({"grade": "weird"}))
check("an unknown grade falls back to ungraded, not to a decision",
      unknown["outcome"] == "ungraded" and unknown["decided"] is False)

print("\nCheck 4: the pregame receipt is never mutated")
before = dict(RECEIPT)
pset.settle(RECEIPT, {}, grader=fake_grader({"grade": "hit"}))
check("receipt unchanged after settlement", RECEIPT == before)

print("\nCheck 5: settlement pairing is provable, not asserted")
ev = pset.settle(RECEIPT, {}, grader=fake_grader({"grade": "hit"}))
check("carries receipt_id", ev["receipt_id"] == RECEIPT["receipt_id"])
check("carries receipt content hash",
      ev["receipt_content_sha256"] == RECEIPT["receipt_content_sha256"])
check("pairing verifies", pset.verify_pairing(RECEIPT, ev))
check("a different receipt id is rejected",
      raises(lambda: pset.verify_pairing(dict(RECEIPT, receipt_id="x"), ev),
             pset.ReceiptMismatch))
check("an EDITED receipt (same id, new hash) is rejected",
      raises(lambda: pset.verify_pairing(
          dict(RECEIPT, receipt_content_sha256="z" * 64), ev),
          pset.ReceiptMismatch))

print("\nCheck 6: summarize uses the decided denominator and reports the rest")
rows = ([{"champion_member": True, "pa_v1_member": False, "outcome": o}
         for o in ["hit"] * 6 + ["miss"] * 4 + ["void"] * 2 + ["ungraded"] * 1]
        + [{"champion_member": False, "pa_v1_member": True, "outcome": o}
           for o in ["hit"] * 7 + ["miss"] * 3])
champ = pset.summarize(rows, arm="champion")
pa = pset.summarize(rows, arm="pa_v1")
check("champion selected 13", champ["selected_n"] == 13)
check("champion decided 10", champ["decided_n"] == 10)
check("champion hit rate uses decided only (6/10)", champ["hit_rate"] == 0.6)
check("void rate uses SELECTED denominator (2/13)",
      abs(champ["void_rate"] - 2 / 13) < 1e-12)
check("ungraded rate uses SELECTED denominator (1/13)",
      abs(champ["ungraded_rate"] - 1 / 13) < 1e-12)
check("pa hit rate 7/10", pa["hit_rate"] == 0.7)
# A challenger that simply decides less often must not look better on hit rate
# alone -- the non-decision rates are what expose it.
check("non-decision rates are reported for both arms",
      champ["void_rate"] is not None and pa["void_rate"] is not None)
check("empty arm yields None, not 0.0",
      pset.summarize([], arm="champion")["hit_rate"] is None)

print("\nCheck 7: the bootstrap contract is FROZEN at the locked values")
check("unit is slate date cluster", pb.BOOTSTRAP_UNIT == "slate_date")
check("5000 replicates", pb.BOOTSTRAP_REPLICATES == 5000)
check("seed 20260901", pb.BOOTSTRAP_SEED == 20260901)
check("95% CI", pb.BOOTSTRAP_CI == 0.95)
check("deterministic Python RNG", "random" in pb.BOOTSTRAP_RNG.lower())
check("statistic is PA-v1 minus champion at matched volume",
      pb.BOOTSTRAP_STATISTIC ==
      "pa_v1_hit_rate_minus_champion_hit_rate_at_matched_volume")
check("decided-only denominator",
      pb.BOOTSTRAP_DENOMINATOR == "decided_only_hit_plus_miss")
check("seed redraw explicitly not permitted",
      pb.CONTRACT["seed_redraw_permitted"] is False)
check("unit change after outcomes explicitly not permitted",
      pb.CONTRACT["unit_change_after_outcomes_permitted"] is False)
check("game/player clustering is SECONDARY only",
      pb.SECONDARY_UNITS == ("game_pk", "player_id"))
# The contract lives in an unpinned file and the LOCKED protocol never states
# a seed, a replicate count or an RNG -- so a one-line edit to BOOTSTRAP_SEED
# would otherwise be undetectable in the evidence record.
_h = pb.contract_file_sha256()
check("the contract file hash is recorded", len(_h) == 64)
check("every result carries it", pb.run([])["contract_file_sha256"] == _h)
# The VERIFIED pin is over the frozen contract VALUES, so a seed edit is
# refused while a diagnostic-only addition is not a false alarm.
check("the contract VALUES are pinned and verified", pb.verify_contract_unmodified())
check("pin is over the values, not the file",
      pb.EXPECTED_CONTRACT_SHA256 == pb.contract_sha256())
check("a populated result carries it too",
      pb.run([{"slate_date": "d", "champion_member": True, "pa_v1_member": False,
               "outcome": "hit"},
              {"slate_date": "d", "champion_member": False, "pa_v1_member": True,
               "outcome": "hit"}])["contract_file_sha256"] == _h)

print("\nCheck 8: run() offers NO lever to move the interval")
import inspect
params = list(inspect.signature(pb.run).parameters)
check("run takes only the settlements", params == ["settlements"])
check("no seed parameter", "seed" not in params)
check("no replicates parameter", "replicates" not in params)
check("no ci parameter", "ci" not in params)


def synth(n_dates=30, per=6, champ_p=0.60, pa_p=0.66, seed=7):
    r = random.Random(seed)
    out = []
    for d in range(n_dates):
        date = f"2026-09-{d + 1:02d}"
        for _ in range(per):
            out.append({"slate_date": date, "champion_member": True,
                        "pa_v1_member": False,
                        "outcome": "hit" if r.random() < champ_p else "miss"})
            out.append({"slate_date": date, "champion_member": False,
                        "pa_v1_member": True,
                        "outcome": "hit" if r.random() < pa_p else "miss"})
    return out


print("\nCheck 9: the bootstrap is deterministic and reproducible")
rows = synth()
a, b = pb.run(rows), pb.run(rows)
check("two runs are byte-identical", a == b)
check("5000 successful replicates", a["successful_replicates"] == 5000)
check("CI brackets the observed estimate",
      a["ci_low"] <= a["observed"] <= a["ci_high"],
      f"{a['ci_low']} {a['observed']} {a['ci_high']}")
check("30 dates clustered", a["n_dates"] == 30)
check("contract is embedded in the result", a["contract"]["seed"] == 20260901)
check("shuffled input gives the same answer (order-independent)",
      pb.run(list(reversed(rows)))["ci_low"] == a["ci_low"])

print("\nCheck 10: DATE clustering, not pick resampling")
# Same picks, same marginal counts, but concentrated into ONE date. Clustered
# resampling can then only ever draw that single date, so every replicate is
# identical and the interval collapses to a point -- which is the honest
# answer with one cluster, and is exactly what pick-level resampling would
# have hidden behind a falsely narrow spread.
one_date = [dict(r, slate_date="2026-09-01") for r in rows]
collapsed = pb.run(one_date)
check("one cluster -> degenerate interval",
      collapsed["ci_low"] == collapsed["ci_high"])
check("30 clusters -> a real interval", a["ci_low"] < a["ci_high"])
check("one cluster reports n_dates 1", collapsed["n_dates"] == 1)

print("\nCheck 11: undefined replicates are skipped and counted, never zeroed")
# Only one date has any DECIDED champion pick; most replicates will not draw
# it, so point_estimate is undefined for them.
sparse = ([{"slate_date": "2026-09-01", "champion_member": True,
            "pa_v1_member": False, "outcome": "hit"},
           {"slate_date": "2026-09-01", "champion_member": False,
            "pa_v1_member": True, "outcome": "hit"}]
          + [{"slate_date": f"2026-09-{d:02d}", "champion_member": True,
              "pa_v1_member": False, "outcome": "void"} for d in range(2, 12)])
res = pb.run(sparse)
check("some replicates are undefined", res["undefined_replicates"] > 0)
check("successful + undefined == attempted",
      res["successful_replicates"] + res["undefined_replicates"]
      == res["attempted_replicates"])
check("successful replicate count is reported",
      isinstance(res["successful_replicates"], int))

print("\nCheck 12: no data at all is honest, not zero")
empty = pb.run([])
check("no dates -> None interval",
      empty["ci_low"] is None and empty["ci_high"] is None)
check("no dates -> 0 successful replicates", empty["successful_replicates"] == 0)
check("point estimate is None, not 0.0", empty["observed"] is None)

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective settlement/bootstrap checks passed.")
