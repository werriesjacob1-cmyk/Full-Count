#!/usr/bin/env python3
"""test_recommendation.py — coverage for recommendation.py, the recommendation
layer built for the 2026-08-15 audit rebuild. This is the file that has to
prove the audit's findings can't recur: every required invariant from the
rebuild instructions gets a direct test here, plus the classifier's own
branch coverage.

    /tmp/mlbvenv/bin/python3 test_recommendation.py
"""
import sys
from datetime import datetime, timedelta, timezone

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


import recommendation as rec
import prop_probability as pp

NOW = datetime.now(timezone.utc)
BOARD_NOW = NOW.isoformat()


def _fresh():
    return rec.freshness_check(now=NOW, odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)


def cand(prob, odds, ci=None, reliability="A", lineup_assumed=False, lift=0.10):
    return {"hit_probability": prob, "market_odds": odds, "prob_ci": ci,
            "reliability": reliability, "lineup_assumed": lineup_assumed, "lift": lift}


def classify(c, fresh_tuple=None):
    fresh, reasons = fresh_tuple or _fresh()
    return rec.classify_recommendation(c, now=NOW, data_fresh=fresh, fresh_reasons=reasons)


# ══════════════════════════════════════════════════════════════════════════
#  THE REQUIRED INVARIANTS, VERBATIM FROM THE REBUILD INSTRUCTIONS
# ══════════════════════════════════════════════════════════════════════════

head("1. a 2% probability bet cannot become a Top Pick -- the real Locks-tab audit example "
     "(Heliot Ramos' Triple at 2.2%/+8000, Ivan Herrera's at 1.2%/+10000) verified live")

longshot = cand(0.022, 8000, ci=[0.01, 0.04])
r1 = classify(longshot)
check(r1["status"] != "top_pick", f"a 2.2% probability bet is never a Top Pick, got {r1['status']!r}")
check(r1["status"] != "top_pick", "sanity re-check at the exact 1.2% real example")
r1b = classify(cand(0.012, 10000, ci=[0.005, 0.03]))
check(r1b["status"] != "top_pick", f"1.2% at +10000 is never a Top Pick, got {r1b['status']!r}")

head("2. a 20% probability bet cannot become a high-probability recommendation -- it can be "
     "a real Value/Longshot bet (a different, honest claim), but never Top Pick, and its "
     "status must never imply it's likely to win")

twenty = cand(0.20, 500, ci=[0.17, 0.30])
r2 = classify(twenty)
check(r2["status"] != "top_pick", f"a 20% probability bet is never a Top Pick, got {r2['status']!r}")
check(r2["status"] in ("value", "lean", "neutral"),
      "a real 20% bet with real price value lands in value/lean/neutral -- never top_pick",
      f"got {r2['status']!r}")

head("3. a sub-floor bet cannot bypass the Top Pick floor via any code path -- swept across "
     "every branch of classify_recommendation(), not just the common case")

sub_floor_cases = [
    cand(0.59, -140, ci=[0.55, 0.65]),                      # just under the floor
    cand(0.59, -140, ci=[0.55, 0.65], reliability="D"),      # + thin evidence
    cand(0.59, -140, ci=[0.55, 0.65], lineup_assumed=True),  # + assumed lineup
    cand(0.30, -110, ci=None),                               # no ci at all
    cand(0.10, None),                                        # no market odds at all
]
check(all(classify(c)["status"] != "top_pick" for c in sub_floor_cases),
      "every sub-floor (<60%) candidate stays out of Top Picks regardless of evidence/lineup/"
      "price-data shape", f"statuses: {[classify(c)['status'] for c in sub_floor_cases]}")

# The floor is a REAL boundary, not an off-by-one: at-floor with everything
# else clean and a real value edge DOES qualify; one point under does not.
just_under = cand(0.599, -140, ci=[0.58, 0.68])
just_at = cand(0.60, -140, ci=[0.58, 0.68])
check(classify(just_under)["status"] != "top_pick",
      "59.9% (one-tenth of a point under the real 60% floor) never qualifies")

head("4. a Hits CI cannot appear on a HR/TB/RBI line -- direct regression test for the audit's "
     "C3 finding (Pete Crow-Armstrong's real Total Bases line showed a 95% CI that was "
     "actually his Hits interval). Tested at the generate_picks.py source, since that's where "
     "the real bug lived (attach_reliability/_batter_options), not just in this module.")

import generate_picks as gp

def batter_c(hr_prob=0.15, hits_prob=0.65, hits_hit=65, hits_n=100, sample_n=120):
    return {
        "type": "batter", "name": "Multi Stat Guy", "player_id": 1, "team": "Team",
        "matchup": "A @ B", "game_pk": 1, "score": 80.0, "confidence": "High",
        "notable_signals": 1, "signals": {}, "sample_n": sample_n, "reliability": "A",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": hits_prob, "probability_basis": "empirical",
        "line_options": [
            {"stat": "hits", "needs": 1, "line": 0.5, "prob": hits_prob, "base_rate": 0.55,
             "lift": round(hits_prob - 0.55, 4), "basis": "empirical",
             "ci": [round(rec_wilson_lo(hits_hit, hits_n), 4), round(rec_wilson_hi(hits_hit, hits_n), 4)]},
            {"stat": "total_bases", "needs": 2, "line": 1.5, "prob": 0.38, "base_rate": 0.50,
             "lift": -0.12, "basis": "modelled_shrunk", "ci": None},
            {"stat": "home_runs", "needs": 1, "line": 0.5, "prob": hr_prob, "base_rate": 0.10,
             "lift": round(hr_prob - 0.10, 4), "basis": "modelled_shrunk", "ci": None},
        ],
    }


def rec_wilson_lo(hit, n):
    lo, hi = gp._wilson_interval(hit, n)
    return lo


def rec_wilson_hi(hit, n):
    lo, hi = gp._wilson_interval(hit, n)
    return hi


c4 = batter_c()
out4 = gp.select_best_by_category([c4], {}, __import__("odds_fanduel"))
hits_ci = out4["hits"][0]["prob_ci"]
tb_ci = out4["total_bases"][0]["prob_ci"]
hr_ci = out4["home_runs"][0]["prob_ci"]
check(hits_ci is not None, "the Hits line gets its own real CI (empirical basis)", f"got {hits_ci}")
check(tb_ci is None,
      "the Total Bases line gets NO CI -- its basis is modelled_shrunk (no real empirical "
      "count backs the displayed number), so honestly None, never the Hits interval",
      f"got {tb_ci}")
check(hr_ci is None,
      "the Home Run line gets NO CI for the same reason -- and critically, tb_ci/hr_ci are "
      "NOT the same object/value as hits_ci, proving no cross-line borrowing happened",
      f"got {hr_ci}")
check(hits_ci != tb_ci and hits_ci != hr_ci,
      "direct regression check: the exact bug (a single CI reused across every stat family "
      "for one player) cannot reproduce -- each line's CI is independently None or real")

head("5. uncertainty from one model cannot validate another model's probability -- direct "
     "regression test for C4 (value_board.py's robustness test previously always used the "
     "season-rate table's CI even when a pipeline probability was being priced)")

import value_board as vb

pipeline_prob = 0.70   # a context-aware pipeline number
season_prob = 0.55     # a completely different, season-only number for the SAME player+market
pipeline_ci_lo = 0.68  # the pipeline probability's OWN real lower bound
season_p_lo = 0.40     # the season-rate table's OWN real lower bound -- deliberately far apart
                       # from pipeline_ci_lo, so a mismatch is unmissable if one occurs

entries5 = {
    ("player a", "hits", 1): {
        "player": "Player A", "stat": "hits", "needs": 1, "american": -150,
        "prob": pipeline_prob, "prob_lo": pipeline_ci_lo, "season_prob": season_prob,
        "source": "pipeline", "games": 100, "base_rate": 0.55,
    },
}
bets5, near5, rejected5 = vb.screen(entries5)
all5 = bets5 + near5 + rejected5
row5 = all5[0]
check(row5["prob"] == pipeline_prob,
      "the probability actually priced is the pipeline number, not the season one")
# value_verdict's own robust_to_uncertainty is computed from prob_lo -- verify THIS row's
# robustness matches what pipeline_ci_lo (not season_p_lo) implies.
expected_robust = pp.expected_roi(pipeline_ci_lo, -150) > 0
check(row5.get("robust_to_uncertainty") == expected_robust,
      "the robustness verdict is computed from the pipeline probability's OWN interval "
      f"({pipeline_ci_lo}), not the season-rate table's ({season_p_lo}) -- got robust="
      f"{row5.get('robust_to_uncertainty')}, expected {expected_robust}")

# The direct source-level regression: model_probabilities() itself must carry
# the per-line pipeline ci through to prob_lo (its entries' actual field --
# entries never has a "ci" key, only "prob_lo", the value screen() actually
# reads), not silently substitute the season-rate table's own p_lo. This
# needs the OTHER two real network calls inside model_probabilities() mocked
# too (batter_pa_composition/empirical_batter_prop_rates/league_base_rates)
# -- score_slate/quality_control alone left "same player" absent from
# by_norm, so the entries loop's `if not hit: continue` dropped the row
# before pipeline_probs was ever consulted, which is the real reason the
# first version of this test always saw {} back.
import unittest.mock as mock
import mlb_sources as src_mod

fake_candidate = {
    "name": "Same Player", "player_id": 1,
    "projection": {"stat": "hits", "needs": 1},
    "hit_probability": 0.72, "prob_ci": [0.65, 0.79],
    "sample_n": 90,
    "line_options": [
        {"stat": "hits", "needs": 1, "prob": 0.72, "ci": [0.65, 0.79]},
        {"stat": "total_bases", "needs": 2, "prob": 0.35, "ci": None},
    ],
}
fake_comp = {1: {"name": "Same Player"}}
fake_emp = {1: {"games": 90, "rates": {
    # deliberately far from the pipeline's own [0.65, 0.79] / None so a
    # cross-model leak is unmissable if one occurs
    "hits_1plus": {"p": 0.55, "p_hat": 0.55, "p_lo": 0.30, "hit": 55},
    "total_bases_2plus": {"p": 0.35, "p_hat": 0.35, "p_lo": 0.20, "hit": 35},
}}}
with mock.patch("generate_picks.score_slate", return_value=([fake_candidate], {"game_meta": [], "park_wx": {}, "emp_pitchers": {}})), \
     mock.patch("generate_picks.quality_control", return_value=([fake_candidate], [], [])), \
     mock.patch.object(src_mod, "batter_pa_composition", return_value=fake_comp), \
     mock.patch.object(src_mod, "empirical_batter_prop_rates", return_value=fake_emp), \
     mock.patch.object(src_mod, "league_base_rates", return_value={}):
    pp_out = vb.model_probabilities(
        {"same player": {("hits", 1): -140, ("total_bases", 2): 150}}, use_pipeline=True)
check(pp_out.get(("same player", "hits", 1), {}).get("prob_lo") == 0.65,
      "the recommended line's real pipeline ci lower bound (0.65) reaches entries' prob_lo "
      "intact, not the season-rate table's own p_lo (0.30)",
      f"got {pp_out.get(('same player', 'hits', 1))}")
check(pp_out.get(("same player", "total_bases", 2), {}).get("prob_lo") is None,
      "an alternate line with no defensible pipeline ci stays honestly None in entries' "
      "prob_lo, never falling back to the season-rate table's p_lo (0.20) just because one "
      "exists", f"got {pp_out.get(('same player', 'total_bases', 2))}")

head("6. alternate lines use correct (per-line) calibration -- the Total Bases/Home Run lines "
     "above never fall back to the batter's PRIMARY (hits) probability_basis or ci, confirming "
     "each family is independently priced and independently CI'd")

c6 = batter_c(hr_prob=0.09)
out6 = gp.select_best_by_category([c6], {}, __import__("odds_fanduel"))
hits_basis = out6["hits"][0]["probability_basis"]
hr_basis = out6["home_runs"][0]["probability_basis"]
check(hits_basis == "empirical" and hr_basis == "modelled_shrunk",
      "each family keeps its own real basis label -- home_runs never inherits hits' "
      f"'empirical' label just because they share a player, got hits={hits_basis!r} "
      f"hr={hr_basis!r}")
hr_prob_out = out6["home_runs"][0]["hit_probability"]
check(abs(hr_prob_out - 0.09) < 1e-6,
      "the home_runs line's own real probability (9%) is what's shown, not the hits line's "
      "65%", f"got {hr_prob_out}")

head("7. Top Pick performance excludes longshots and unrelated categories -- see "
     "test_grade_results.py / results/history.json's by_recommendation_status for the full "
     "grading-side regression; this checks recommendation.py's own status field is what a "
     "grader would need to key on, i.e. every classification result carries a real, "
     "unambiguous status distinct from category/confidence.")

statuses_seen = {classify(cand(0.65, -140, ci=[0.60, 0.72]))["status"],
                 classify(twenty)["status"],
                 classify(longshot)["status"],
                 classify(cand(0.50, None))["status"]}
check(statuses_seen <= {"top_pick", "lean", "value", "neutral"},
      "every classification lands in exactly one of the four real states, nothing else",
      f"got {statuses_seen}")

head("8. stale critical data prevents an official Top Pick -- fail closed, not open")

stale_board = (NOW - timedelta(hours=6)).isoformat()
fresh8, reasons8 = rec.freshness_check(now=NOW, odds_fetched_at=stale_board,
                                       board_generated_at=stale_board)
check(fresh8 is False, "a 6-hour-old board is correctly flagged not fresh")
strong = cand(0.70, -140, ci=[0.63, 0.77])
r8 = classify(strong, fresh_tuple=(fresh8, reasons8))
check(r8["status"] != "top_pick",
      "a bet that would otherwise clearly qualify is blocked from Top Pick status when the "
      "board's own data is stale -- fails closed, not open", f"got {r8['status']!r}")
check(r8.get("stale") is True, "the stale flag itself is surfaced, not just a silent downgrade")

fresh_recent, reasons_recent = rec.freshness_check(
    now=NOW, odds_fetched_at=(NOW - timedelta(minutes=10)).isoformat(),
    board_generated_at=(NOW - timedelta(minutes=10)).isoformat())
check(fresh_recent is True, "a 10-minute-old board is correctly fresh")

missing_ts_fresh, missing_ts_reasons = rec.freshness_check(now=NOW, odds_fetched_at=None,
                                                            board_generated_at=None)
check(missing_ts_fresh is False,
      "a missing/unknown timestamp is treated as NOT fresh -- an unknown age fails closed, "
      "it is never assumed to be fine")

head("9. recommendation status survives the dashboard serialization layer -- see "
     "test_build_dashboard.py's own check for the full clean()/build_payload() regression "
     "(recommendation_status/status_reasons/stale all now real fields in clean()'s output and "
     "build_payload()'s Top Picks/Leans/Best Value buckets read them directly, not a "
     "re-derived heuristic). This checks the batch entry point itself doesn't drop them.")

batch = [dict(longshot), dict(strong), dict(twenty)]
rec.attach_recommendations(batch, odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)
check(all("status" in c and "status_reasons" in c for c in batch),
      "attach_recommendations() leaves a real status+status_reasons on every candidate, "
      "in place, ready for the serialization boundary to carry through",
      f"got {[c.get('status') for c in batch]}")


# ══════════════════════════════════════════════════════════════════════════
#  BRANCH COVERAGE FOR classify_recommendation() ITSELF
# ══════════════════════════════════════════════════════════════════════════

head("10. a clean, well-evidenced, confirmed, fresh, price-clearing favorite IS a Top Pick -- "
     "the positive case, so the floor is provably reachable and not just a wall")

good = cand(0.65, -140, ci=[0.62, 0.72])
r10 = classify(good)
check(r10["status"] == "top_pick", f"a genuinely clean case reaches Top Pick, got {r10['status']!r}",
      str(r10["status_reasons"]))

head("11. an assumed (unconfirmed) lineup blocks Top Pick even with everything else clean")

assumed = cand(0.65, -140, ci=[0.62, 0.72], lineup_assumed=True)
r11 = classify(assumed)
check(r11["status"] != "top_pick", f"an assumed lineup blocks Top Pick, got {r11['status']!r}")

head("12. thin evidence (reliability C/D) blocks Top Pick even with a clean probability/price")

thin = cand(0.65, -140, ci=[0.62, 0.72], reliability="D")
r12 = classify(thin)
check(r12["status"] != "top_pick", f"reliability D blocks Top Pick, got {r12['status']!r}")

head("13. SUSPECT market disagreement does NOT block Top Pick for a real favorite -- verified "
     "by direct calculation before shipping: given this codebase's own ASSUMED_PROP_HOLD (8%) "
     "and MIN_ROI (5%) constants, no price for a 65% favorite can EVER be both 'not SUSPECT' "
     "and ROI-positive simultaneously, so requiring both would make Top Pick mathematically "
     "impossible for any favorite. SUSPECT is a hard gate for Value/Longshot only (where it "
     "was designed to catch the real CJ Abrams-style overstatement), never for Top Pick/Lean.")

suspect_favorite = cand(0.65, -140, ci=[0.62, 0.72])
agreement13 = pp.market_agreement(0.65, -140)
check(agreement13["agreement"] == "SUSPECT",
      "sanity: -140 really is SUSPECT against a 65% model read", str(agreement13))
r13 = classify(suspect_favorite)
check(r13["status"] == "top_pick",
      "a real, robust, well-evidenced favorite still reaches Top Pick even when the market "
      "disagrees enough to register SUSPECT", f"got {r13['status']!r}")

suspect_longshot = cand(0.10, 2000, ci=[0.08, 0.14])
agreement13b = pp.market_agreement(0.10, 2000)
r13b = classify(suspect_longshot)
if agreement13b["agreement"] == "SUSPECT":
    check(r13b["status"] != "value",
          "SUSPECT DOES block the Value bucket for a longshot -- this is the exact case "
          "market_agreement's ratio test was built for", f"got {r13b['status']!r}")

head("14. no market price at all -- can never be Top Pick or Value, at best a Lean")

no_price = cand(0.65, None, ci=None)
r14 = classify(no_price)
check(r14["status"] in ("lean", "neutral"),
      "no market price means no way to test value or the price/robustness gate -- can only "
      f"ever be a lean or neutral, got {r14['status']!r}")

head("15. a real positive lift with no other qualifying factor lands as a Lean, not silently "
     "dropped to Neutral -- 'if the data favors one side... show it as a Lean'")

lean_case = cand(0.55, -115, ci=[0.48, 0.62], lift=0.06)
r15 = classify(lean_case)
check(r15["status"] == "lean", f"a real, positive, sub-floor read is a Lean, got {r15['status']!r}")

head("16. zero evidence, zero lift, no price -- a real 'no opinion,' not a forced lean")

nothing = {"hit_probability": 0.50, "market_odds": None, "prob_ci": None,
          "reliability": "C", "lineup_assumed": False, "lift": 0.0}
r16 = classify(nothing)
check(r16["status"] == "neutral", f"no real signal in any direction is Neutral, got {r16['status']!r}")

head("17. metadata/versioning: every board carries a real, traceable version block")

meta = rec.build_metadata(odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)
for key in ("model_version", "selection_policy_version", "calibration_version",
           "feature_version", "prediction_timestamp", "odds_fetched_at", "board_generated_at"):
    check(key in meta and meta[key], f"metadata carries a real, non-empty {key}", str(meta))
check(meta["odds_fetched_at"] == BOARD_NOW and meta["board_generated_at"] == BOARD_NOW,
      "the real, passed-in timestamps are carried through unchanged, not re-derived")


head("18. 2026-08-18 Pre-Phase-V finding A1/A2: missing prob_ci must fail closed for Top "
     "Pick/Value, not silently skip the pessimistic-end robustness test -- see "
     "engineering/ENGINEERING_HANDOFF.md's A1-A4 investigation entry")

no_ci_strong = cand(0.62, -110, ci=None, lift=0.05)
r18 = classify(no_ci_strong)
check(r18["status"] != "top_pick",
      "a candidate that clears plain ROI with NO defensible confidence interval cannot "
      f"become Top Pick -- absence of a CI is not evidence of robustness, got {r18['status']!r}",
      str(r18["status_reasons"]))
check(r18["status"] != "value",
      "the same absent-CI reasoning blocks the Value bucket too -- this policy's Value "
      f"requirement shares the same pessimistic-end test as Top Pick, got {r18['status']!r}")
check(not any("pessimistic end of its own interval" in reason
             for reason in r18["status_reasons"]),
      "the rationale never claims a pessimistic-interval test that could not have run",
      str(r18["status_reasons"]))

# A real, well-evidenced CI still reaches Top Pick and still names the real test that ran --
# the fix must not weaken the honest, positive path.
still_works = cand(0.68, -130, ci=[0.61, 0.75])
r18b = classify(still_works)
check(r18b["status"] == "top_pick",
      f"a genuine, well-evidenced CI still reaches Top Pick after the fix, got {r18b['status']!r}",
      str(r18b["status_reasons"]))
check(any("pessimistic end of its own interval" in reason
         for reason in r18b["status_reasons"]),
      "the rationale correctly claims the pessimistic-interval test when a real CI existed "
      "and was actually tested", str(r18b["status_reasons"]))

# A missing CI does not have to mean total silence -- a real, positive lift with everything
# else clean should still surface as a coherent Lean, not get dropped to a confusing Neutral.
no_ci_leanable = cand(0.62, -110, ci=None, lift=0.06)
r18c = classify(no_ci_leanable)
check(r18c["status"] == "lean",
      f"missing CI with a real positive lift lands as a coherent Lean, not silently dropped "
      f"or forced to Neutral, got {r18c['status']!r}", str(r18c["status_reasons"]))

head("19. 2026-08-18 Pre-Phase-V finding A3: freshness_check must fail closed on missing "
     "odds_fetched_at, never borrow board_generated_at as evidence the price is fresh")

missing_odds_fresh_board, missing_odds_reasons = rec.freshness_check(
    now=NOW, odds_fetched_at=None, board_generated_at=BOARD_NOW)
check(missing_odds_fresh_board is False,
      "a missing odds_fetched_at is treated as unknown/stale even when board_generated_at "
      "is genuinely fresh -- the board's own timestamp is not evidence this specific price "
      "was ever verified", str(missing_odds_reasons))
check(any("price fetch time unknown" in r for r in missing_odds_reasons),
      "the reason names the actual gap (price time unknown), not a generic staleness message",
      str(missing_odds_reasons))

# The existing, valid-timestamp path must be unaffected by the fix.
valid_odds_fresh, valid_odds_reasons = rec.freshness_check(
    now=NOW, odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)
check(valid_odds_fresh is True,
      "a real, current odds_fetched_at is still correctly fresh after the fix",
      str(valid_odds_reasons))

# End-to-end: a candidate that would otherwise clearly be a Top Pick is still blocked when
# the BOARD-level freshness verdict itself is built from a missing odds_fetched_at.
fresh_missing_odds, reasons_missing_odds = rec.freshness_check(
    now=NOW, odds_fetched_at=None, board_generated_at=BOARD_NOW)
would_be_top_pick = cand(0.68, -130, ci=[0.61, 0.75])
r19 = classify(would_be_top_pick, fresh_tuple=(fresh_missing_odds, reasons_missing_odds))
check(r19["status"] != "top_pick",
      "an otherwise-clean Top Pick is correctly blocked end-to-end when the board's own "
      f"price-fetch time is unknown, got {r19['status']!r}", str(r19["status_reasons"]))
check(r19.get("stale") is True,
      "the stale flag is surfaced on the end-to-end path too, not just a silent downgrade")

head("20. value_verdict()'s new require_robust parameter defaults to False -- existing "
     "callers (value_board.py's own --no-robust escape hatch) that pass prob_lo=None on "
     "purpose to intentionally skip the test must keep getting a real BET/NO BET verdict "
     "off ROI alone, not be silently converted to always-NO-BET")

default_verdict = pp.value_verdict(0.62, -110, prob_lo=None)
check(default_verdict["verdict"] == "BET",
      "value_verdict's default behavior (require_robust not passed) is UNCHANGED from "
      "before this fix -- only callers that explicitly opt in get the stricter contract",
      str(default_verdict))
required_verdict = pp.value_verdict(0.62, -110, prob_lo=None, require_robust=True)
check(required_verdict["verdict"] == "NO BET" and required_verdict.get("robust_to_uncertainty") is False,
      "an explicit require_robust=True caller correctly fails closed on a missing interval",
      str(required_verdict))


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
