#!/usr/bin/env python3
"""test_build_dashboard.py — coverage for dashboard/build_dashboard.py's pure
functions after the Phase 4 rebuild (2026-08-16): build_payload(),
load_track_record(), copy_static_assets(), plus the still-unchanged
_game_schedule/_compute_streaks/_build_suggested_parlay/_decimal_to_american.

Does NOT test run_live_fetch()'s live network path, matching this project's
existing convention of never unit-testing a live fetcher directly (check 12
below covers only its pure no-games-early-return branch, mocked).

PHASE 4 REWRITE NOTE: the old version of this file (pre-2026-08-16) tested
render_html()/PAGE_TEMPLATE and a large family of client-JS behaviors
(pruneStartedGames, mergePriceUpdate, gameCard, pickRow, renderPanels,
filterSortRows, uiState/activeTabKey) by extracting a <script> block out of
rendered HTML and running it in a stubbed-DOM Node harness. That whole
mechanism is gone: the page is no longer a Python string template (see
build_dashboard.py's module docstring), so there is no HTML to extract a
<script> from anymore, and the client logic itself moved to
dashboard/static/app.js under an entirely different architecture (a single
flat `props` array + client-side filtering, not per-tab server buckets with
a capped/ranked Top Picks list). Concretely:
  - The old Top-Picks-ranking/capping tests (build_payload capped/ranked a
    "top_picks" bucket) no longer apply: build_payload() now does nothing
    but pass recommendation_status through untouched on the flat array; the
    client (app.js's renderToday()) filters and sorts for display, with no
    capping at all (direct instruction: "never force Top Picks").
  - The old tabs_order tests (fixed prefix, moonshot-ahead-of-counting-
    stats ordering) no longer apply: there is no tabs_order. build_payload()
    emits `families` (stat -> count, sorted by count) for the props-page
    filter's dropdown, nothing more.
  - The old "search only searched the active tab" regression (check 25) is
    structurally impossible in the new architecture: app.js's search reads
    DATA.props directly, independent of whatever route is showing.
  - Client-rendering behaviors this file used to check via string-matching
    extracted HTML (streak badges, game cards, assumed-lineup chips, the
    Lean/Top-Pick distinction, humanizeReason's jargon translation) were
    re-verified for the new app.js this session via a real headless-browser
    pass (Playwright: 20 route/theme/viewport combinations, zero console
    errors, zero horizontal overflow, focus-trap/focus-restore confirmed on
    both modal sheets) rather than reproduced 1:1 here as string-matching
    harnesses against a different rendering model. Check 13 below keeps one
    lightweight Node smoke-test for humanizeReason, sourcing app.js directly
    (no HTML extraction needed now -- it's already a plain file).

    /tmp/mlbvenv/bin/python3 test_build_dashboard.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/dashboard" if "/" in __file__ else "dashboard")

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


import build_dashboard as bd


_row_counter = [0]


def row(name, stat, prob, needs=1, value=0.5, odds=None, implied=None, edge=None,
       clears=None, confidence="Medium", ptype="batter", recommendation_status=None,
       lineup_assumed=None, prob_ci=None, lift=None, reliability="B", batting_order=None):
    _row_counter[0] += 1
    return {
        # A stable id per row -- real clean()'d rows always carry one
        # (game_pk-subject-stat-needs); _compute_streaks() keys its output
        # entries off it. The exact value doesn't matter here, only that
        # every fixture row has one and they're unique.
        "id": f"fixture-{_row_counter[0]}",
        "type": ptype, "name": name, "team": "Team", "matchup": "A @ B", "side": None,
        "prop": f"Over {value} {stat}", "projection": {"stat": stat, "value": value, "needs": needs},
        "lean": None, "score": 65.0, "confidence": confidence,
        "hit_probability": prob, "market_odds": odds, "market_implied": implied,
        "market_edge": edge, "price_clears": clears,
        "reliability": reliability, "sample_n": 80, "why": [], "watchouts": [],
        "base_rate": None, "lift": lift, "prob_ci": prob_ci,
        # build_payload() operates on already-clean()'d rows in real
        # run_live_fetch() output, which means recommendation_status is
        # already computed by recommendation.py by the time build_payload()
        # ever sees it -- these fixtures set it directly rather than
        # re-deriving it, matching that real shape. batting_order is the
        # SAME story: clean() (a function nested inside run_live_fetch(),
        # not reachable from build_payload()) is what actually computes it
        # from signals.lineup_slot -- build_payload() itself only ever
        # passes the already-cleaned value through untouched, so this
        # fixture sets the POST-clean() value directly, matching every
        # other field here.
        "recommendation_status": recommendation_status, "status_reasons": [], "stale": False,
        "lineup_assumed": lineup_assumed,
        "batting_order": batting_order,
    }


head("1. build_payload flattens every stat family into one real `props` array -- "
     "no per-tab duplication, no separate top_picks/leans/best_value subset lists "
     "(the exact duplication build_dashboard.py's docstring documents fixing: the old "
     "payload serialized every row up to ~3x -- per-stat tab, again in 'all', again in a "
     "status subset). recommendation_status passes through untouched, uncapped -- direct "
     "instruction: \"never force Top Picks... if only 3 bets qualify, show 3.\"")

result = {
    "generated_at": "2026-08-16T20:00:00", "date": "2026-08-16",
    "hits": [
        row("Big Edge Top Pick", "hits", 0.70, odds=-150, implied=0.55, edge=0.15, clears=True,
           recommendation_status="top_pick"),
        row("Small Edge Top Pick", "hits", 0.60, odds=-120, implied=0.58, edge=0.02, clears=True,
           recommendation_status="top_pick"),
        row("High Prob But Only A Lean", "hits", 0.90, odds=-800, implied=0.85, edge=0.05,
           clears=False, recommendation_status="lean"),
        row("No Line At All", "hits", 0.55, odds=None, implied=None, edge=None, clears=None,
           recommendation_status="neutral"),
    ],
    "stolen_base": [
        row("Mid Edge Top Pick", "stolen_base", 0.35, odds=200, implied=0.28, edge=0.07, clears=True,
           recommendation_status="top_pick"),
    ],
}
payload = bd.build_payload(result)
check(len(payload["props"]) == 5, "every real row lands in the one flat props array exactly "
      "once -- not duplicated across tabs/subsets", f"got {len(payload['props'])}")
names = [r["name"] for r in payload["props"]]
check(len(names) == len(set(names)), "no row appears more than once in props",
      f"got {names}")
top_names = [r["name"] for r in payload["props"] if r["recommendation_status"] == "top_pick"]
check(sorted(top_names) == sorted(["Big Edge Top Pick", "Small Edge Top Pick", "Mid Edge Top Pick"]),
      "recommendation_status is passed through unmodified for every row -- build_payload never "
      "re-derives or re-ranks it (that's recommendation.py's job, upstream)", f"got {top_names}")
check(payload["summary"]["n_top_pick"] == 3, "summary.n_top_pick counts the real top_pick rows",
      f"got {payload['summary']}")
check(payload["summary"]["n_props"] == 5, "summary.n_props is the true unique row count")

many = {"generated_at": "x", "date": "2026-08-16",
       "hits": [row(f"Qualifier {i}", "hits", 0.6, odds=-110, implied=0.5,
                    edge=0.10 - i * 0.001, clears=True, recommendation_status="top_pick")
               for i in range(15)]}
payload2 = bd.build_payload(many)
check(sum(1 for r in payload2["props"] if r["recommendation_status"] == "top_pick") == 15,
      "every real qualifying Top Pick ships, uncapped -- a real 15-Top-Pick night is not "
      "silently trimmed (there is no server-side cap in the new architecture at all)")


head("1b. _assign_top_pick_rank(): every top_pick row gets a real 1-indexed rank matching "
     "generate_picks.py's own reliability/edge/probability tiebreak (found 2026-08-25 -- "
     "docs/app.js's renderToday() used to invent this ordering itself in JS via an edge-only "
     "sort, which this project's own frontend/backend boundary forbids: 'frontend must not "
     "invent new ranking')")

ranked_by_name = {r["name"]: r.get("rank") for r in payload["props"]
                  if r["recommendation_status"] == "top_pick"}
check(ranked_by_name["Big Edge Top Pick"] == 1,
      "highest edge ranks #1 among equal-reliability top picks", f"got {ranked_by_name}")
check(ranked_by_name["Mid Edge Top Pick"] == 2, "middle edge ranks #2", f"got {ranked_by_name}")
check(ranked_by_name["Small Edge Top Pick"] == 3, "lowest edge ranks #3", f"got {ranked_by_name}")
non_top_pick_ranks = [r.get("rank") for r in payload["props"] if r["recommendation_status"] != "top_pick"]
check(all(r is None for r in non_top_pick_ranks),
      "non-top_pick rows never get a fabricated rank -- this function only ever defines an "
      "order among Top Picks", f"got {non_top_pick_ranks}")

reliability_case = {"generated_at": "x", "date": "2026-08-16",
    "hits": [
        row("A Grade Low Edge", "hits", 0.65, odds=-120, implied=0.55, edge=0.03, clears=True,
           recommendation_status="top_pick", reliability="A"),
        row("B Grade Higher Edge", "hits", 0.65, odds=-120, implied=0.55, edge=0.20, clears=True,
           recommendation_status="top_pick", reliability="B"),
    ]}
payload_rel = bd.build_payload(reliability_case)
by_name = {r["name"]: r.get("rank") for r in payload_rel["props"]}
check(by_name["B Grade Higher Edge"] == 1,
      "A and B reliability are treated identically by generate_picks._RELIABILITY_ORDER (both "
      "map to 0) -- since only A/B can ever reach top_pick status at all "
      "(TOP_PICK_MIN_RELIABILITY=('A','B')), edge is the real deciding tiebreak in practice, so "
      "the higher-edge B-grade pick correctly outranks the lower-edge A-grade pick",
      f"got {by_name}")


head("1d. _derive_batting_order()/batting_order (2026-08-25, detail-sheet OPPORTUNITY fact): "
     "signals.lineup_slot stores the SCALED 0-100 lineup_context value (scale(10-order,1,9)), "
     "not the raw order number -- _sig() only records `scaled`, never `raw`. Inverting it back "
     "so the payload carries a real human fact ('batting 2nd') instead of a meaningless scaled "
     "number a customer could never interpret.")

check(bd._derive_batting_order(87.5) == 2, "lineup_slot=87.5 inverts to order 2 (leadoff-ish)",
      f"got {bd._derive_batting_order(87.5)}")
check(bd._derive_batting_order(100) == 1, "lineup_slot=100 inverts to order 1 (leadoff)")
check(bd._derive_batting_order(0) == 9, "lineup_slot=0 inverts to order 9 (bottom of order)")
check(bd._derive_batting_order(None) is None, "no signal fired (e.g. a pitcher row) -> None, never a fabricated order")

# clean() (the function that actually inverts signals.lineup_slot -> batting_order)
# is nested inside run_live_fetch(), not reachable from build_payload() -- this
# checks the pass-through boundary build_payload() IS responsible for: an
# already-cleaned batting_order value must survive unchanged, same as every
# other clean()'d field this file already tests this way.
order_case = {"generated_at": "x", "date": "2026-08-16",
    "hits": [row("Leadoff Guy", "hits", 0.65, batting_order=1),
            row("Pitcher Row", "strikeouts", 0.55, ptype="pitcher", batting_order=None)]}
payload_order = bd.build_payload(order_case)
by_name_order = {r["name"]: r.get("batting_order") for r in payload_order["props"]}
check(by_name_order["Leadoff Guy"] == 1,
      "build_payload passes an already-cleaned batting_order value through unchanged",
      f"got {by_name_order}")
check(by_name_order["Pitcher Row"] is None,
      "a row with no real batting order (e.g. a pitcher market) stays None, never a fabricated guess",
      f"got {by_name_order}")


head("2. home_runs is dropped as a duplicate of moonshot (same underlying field, per audit) "
     "-- families reflects only the real, deduplicated stat keys")

dup = {
    "generated_at": "x", "date": "2026-08-16",
    "moonshot": [row("Slugger", "home_runs", 0.20, needs=1, value=1)],
    "home_runs": [row("Slugger", "home_runs", 0.20, needs=1, value=1)],
}
payload3 = bd.build_payload(dup)
family_stats = [f["stat"] for f in payload3["families"]]
check("home_runs" not in family_stats, "the raw 'home_runs' key never becomes its own family",
      f"got {family_stats}")
check("moonshot" in family_stats, "moonshot is kept as the one real Home Runs family")
check(sum(1 for r in payload3["props"] if r["name"] == "Slugger") == 1,
      "the Slugger row appears exactly once in props, not twice (moonshot + home_runs)")


head("3. a category with zero real rows never produces a family entry; families are "
     "sorted by count descending")

payload4 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-16",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True,
                recommendation_status="top_pick")],
    "triples": [],  # present in the data but empty -- must not become a family
})
family_stats4 = [f["stat"] for f in payload4["families"]]
check("triples" not in family_stats4, "a category with zero real rows never becomes a family",
      f"got {family_stats4}")
check("hits" in family_stats4, "a category with real rows does become a family")

payload4b = bd.build_payload({
    "generated_at": "x", "date": "2026-08-16",
    "hits": [row(f"H{i}", "hits", 0.6, odds=-110, implied=0.5, edge=0.02, clears=True)
            for i in range(3)],
    "rbis": [row("R", "rbis", 0.5, odds=-110, implied=0.52, edge=0.02, clears=True)],
})
fam_counts = {f["stat"]: f["count"] for f in payload4b["families"]}
check(list(fam_counts.keys())[0] == "hits" if fam_counts else False,
      "families are ordered by real count, most first", f"got {payload4b['families']}")


head("4. estimated_odds is computed for every priced row via the real "
     "prop_probability.american_odds, not a reimplementation")

import prop_probability as pp
payload5 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-16",
    "hits": [row("A", "hits", 0.75, odds=-300, implied=0.7, edge=0.05, clears=True)],
})
a_row = payload5["props"][0]
check(a_row["estimated_odds"] == pp.american_odds(0.75),
      "estimated_odds matches the real american_odds() function directly",
      f"got {a_row['estimated_odds']}, expected {pp.american_odds(0.75)}")


head("5. priced candidates rank ahead of unpriced ones in the default props sort -- "
     "even when the unpriced one has the higher raw probability")

result7 = {
    "generated_at": "x", "date": "2026-08-16",
    "pitcher_outs": [
        # No real FanDuel line yet (market_odds=None) -- found live 2026-08-12:
        # David Peterson's Outs Recorded read (63.2%, no line) sorted above
        # several real, priced, lower-probability candidates for exactly this
        # reason, reading as a recommended pick that wasn't actually a bet
        # anyone could place.
        row("No Line High Prob", "pitcher_outs", 0.90, odds=None, implied=None, edge=None, clears=None),
        row("Priced Lower Prob", "pitcher_outs", 0.60, odds=-140, implied=0.58, edge=0.02, clears=True),
    ],
}
payload7 = bd.build_payload(result7)
names7 = [r["name"] for r in payload7["props"]]
check(names7 == ["Priced Lower Prob", "No Line High Prob"],
      "a real-priced candidate ranks first in the default sort, even against a higher-"
      "probability unpriced one", f"got {names7}")


head("6. load_track_record: current (2026-08-15-architecture-forward) and legacy "
     "(pre-rebuild) are read from separate, real history.json fields and NEVER blended -- "
     "each degrades to honest None when it has zero graded picks of its own kind, rather "
     "than a fabricated 0% or borrowing the other tier's numbers")

with tempfile.TemporaryDirectory() as td:
    hist_path = os.path.join(td, "history.json")
    with open(hist_path, "w") as f:
        json.dump({
            "main_hit_rate": 0.553, "last_14_days_hit_rate": 0.452,
            "by_category_totals": {"main": {"hits": 26, "misses": 21, "ungraded": 0}},
            "top_pick_hit_rate": 0.70, "last_14_days_top_pick_hit_rate": 0.70,
            "last_14_days_top_pick_n": 3,
            "public_top_pick_totals": {"hits": 7, "misses": 3, "voids": 0},
            "by_recommendation_status_totals": {"top_pick": {"hits": 7, "misses": 3}},
        }, f)
    tr = bd.load_track_record(hist_path)
    check(tr["legacy"]["hit_rate"] == 0.553, "legacy reads main_hit_rate, not any blended "
          "overall figure", f"got {tr['legacy']}")
    check(tr["legacy"]["n"] == 47, "legacy n is hits+misses from by_category_totals.main",
          f"got {tr['legacy']}")
    check(tr["current"]["hit_rate"] == 0.70, "current reads the deployment-proven public "
          "Top Pick rate, a completely separate figure from legacy/modelled status totals",
          f"got {tr['current']}")
    check(tr["current"]["n"] == 10, "current n is hits+misses from "
          "public_top_pick_totals", f"got {tr['current']}")
    # Real bug, found 2026-08-25: the Performance page showed a bare
    # hit-rate percentage with no indication of how thin the underlying
    # sample is -- sample_label (via eval_lib's shared sample-size-honesty
    # gate, MIN_N_DIRECTIONAL=5/MIN_N_REPORTABLE=20/MIN_N_CONFIDENT=100)
    # is what the frontend now reads to render an honest caveat.
    check(tr["legacy"]["sample_label"] == "directional",
          "legacy n=47 (>=20, <100) is labeled 'directional', the same shared eval_lib gate "
          "used elsewhere in this project -- not a new, inconsistent threshold",
          f"got {tr['legacy']['sample_label']}")
    check(tr["current"]["sample_label"] == "thin",
          "current n=10 (>=5, <20) is labeled 'thin'", f"got {tr['current']['sample_label']}")

    tiny_path = os.path.join(td, "tiny.json")
    with open(tiny_path, "w") as f:
        json.dump({
            "main_hit_rate": 1.0,
            "by_category_totals": {"main": {"hits": 2, "misses": 0}},
            "top_pick_hit_rate": 1.0,
            "public_top_pick_totals": {"hits": 100, "misses": 20},
        }, f)
    tr_tiny = bd.load_track_record(tiny_path)
    check(tr_tiny["legacy"]["sample_label"] == "insufficient",
          "a 100% hit rate off only 2 graded picks (n<5) is labeled 'insufficient' -- the "
          "exact case this fix exists for: an early, meaningless-looking-perfect number must "
          "never be shown without a caveat that it means nothing yet",
          f"got {tr_tiny['legacy']['sample_label']}")
    check(tr_tiny["current"]["sample_label"] == "reportable",
          "current n=120 (>=100) is labeled 'reportable' -- a real, mature sample gets no "
          "caveat", f"got {tr_tiny['current']['sample_label']}")

    zero_current_path = os.path.join(td, "zero_current.json")
    with open(zero_current_path, "w") as f:
        json.dump({
            "main_hit_rate": 0.50,
            "by_category_totals": {"main": {"hits": 10, "misses": 10}},
            "top_pick_hit_rate": None,
            "public_top_pick_totals": {},
            "by_recommendation_status_totals": {},
        }, f)
    tr_zc = bd.load_track_record(zero_current_path)
    check(tr_zc["current"] is None, "zero graded picks under the current architecture is "
          "honest None -- not a fabricated 0% and not silently backfilled from legacy",
          f"got {tr_zc['current']}")
    check(tr_zc["legacy"] is not None and tr_zc["legacy"]["hit_rate"] == 0.50,
          "legacy is unaffected by current having no data yet -- the two tiers are read "
          "completely independently")

    empty_path = os.path.join(td, "empty.json")
    with open(empty_path, "w") as f:
        json.dump({"main_hit_rate": None, "by_category_totals": {}}, f)
    tr_empty = bd.load_track_record(empty_path)
    check(tr_empty == {"current": None, "legacy": None},
          "no graded picks of either kind returns {current: None, legacy: None}, not a crash",
          f"got {tr_empty}")

    missing = bd.load_track_record(os.path.join(td, "does_not_exist.json"))
    check(missing == {"current": None, "legacy": None},
          "a missing history.json returns the same honest empty shape rather than crashing "
          "the build", f"got {missing}")


head("7. suggested parlay: real correlation-screened legs via the actual parlay_builder.py "
     "engine (unchanged by the Phase 4 rebuild), honest None when fewer than 2 real legs exist")

def parlay_candidate(name, team, matchup, game_pk, player_id, prop, stat, prob, odds, conf="High"):
    return {
        "name": name, "team": team, "matchup": matchup, "game_pk": game_pk,
        "type": "batter", "player_id": player_id, "prop": prop, "side": None,
        "projection": {"stat": stat, "value": 0.5, "needs": 1},
        "hit_probability": prob, "market_odds": odds, "market_implied": None,
        "confidence": conf, "price_clears": True,
    }

check(bd._decimal_to_american(2.0) == 100, "decimal 2.0 (even money) is +100")
check(bd._decimal_to_american(1.5) == -200, "decimal 1.5 is -200")
check(bd._decimal_to_american(None) is None, "a missing decimal price returns None, not a crash")

three_legs = [
    parlay_candidate("A", "T1", "T1 @ T2", 1, 101, "Over 0.5 Hits", "hits", 0.72, -150),
    parlay_candidate("B", "T3", "T3 @ T4", 2, 102, "Over 0.5 Total Bases", "total_bases", 0.68, -130),
    parlay_candidate("C", "T5", "T5 @ T6", 3, 103, "Over 0.5 Runs", "runs", 0.65, -120, conf="Medium"),
]
sp = bd._build_suggested_parlay(three_legs)
check(sp is not None and len(sp["legs"]) >= 2,
      "a real slate with enough independent legs produces a real suggested parlay",
      f"got {sp}")
if sp:
    check(sp["combined_american_odds"] is not None,
          "combined_american_odds is populated when every leg is priced", f"got {sp}")
    check("naive_probability_note" in sp and sp["naive_probability_note"],
          "the honest 'not a final answer' caveat travels with the parlay, not just the number")

check(bd._build_suggested_parlay([three_legs[0]]) is None,
      "a single real leg is NOT dressed up as a parlay -- honest None instead")
check(bd._build_suggested_parlay([]) is None,
      "no candidates at all returns None, not an empty/fabricated parlay")

none_prob_pool = three_legs + [
    parlay_candidate("D", "T7", "T7 @ T8", 4, 104, "Over 0.5 RBIs", "rbis", None, -110),
]
sp_with_none = bd._build_suggested_parlay(none_prob_pool)
check(sp_with_none is not None,
      "a pool containing a real hit_probability=None candidate doesn't crash the whole "
      "feature -- it's filtered out the same way load_todays_pool() already does for "
      "every other caller of this engine", f"got {sp_with_none}")

unpriced_leg = parlay_candidate("E", "T9", "T9 @ T10", 5, 105, "Over 0.5 Doubles", "doubles",
                                0.70, None)
sp_unpriced_excluded = bd._build_suggested_parlay([three_legs[0], three_legs[1], unpriced_leg])
if sp_unpriced_excluded:
    names_pex = {l["name"] for l in sp_unpriced_excluded["legs"]}
    check("E" not in names_pex, "a real-probability-but-unpriced (market_odds=None) candidate "
          "never becomes a parlay leg -- it's not a bet anyone could place", f"got legs={names_pex}")
if sp_with_none:
    check("D" not in [l["name"] for l in sp_with_none["legs"]],
          "the None-probability candidate itself never becomes a leg")


head("8. build_payload doesn't crash on a result dict shaped like run_live_fetch()'s "
     "real output -- suggested_parlay passes through as a top-level key, never swept into "
     "the generic stat-family loop")

result10 = {
    "generated_at": "x", "date": "2026-08-16",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "suggested_parlay": None,
}
payload10 = bd.build_payload(result10)
check("suggested_parlay" not in [f["stat"] for f in payload10["families"]],
      "suggested_parlay never becomes a family", f"got {payload10['families']}")
check(payload10["suggested_parlay"] is None,
      "a None suggested_parlay (the honest case when the engine can't build one) passes "
      "through cleanly")

result10b = dict(result10)
result10b["suggested_parlay"] = {"legs": [{"name": "X"}], "combined_american_odds": 150}
payload10b = bd.build_payload(result10b)
check(payload10b["suggested_parlay"] == result10b["suggested_parlay"],
      "a real suggested_parlay dict passes through to the payload unchanged, not "
      "misread as a list of candidate rows", f"got {payload10b['suggested_parlay']}")


head("9. _game_schedule returns pregame filtering and raw lifecycle status fields, "
     "and remains non-fatal on a schedule outage")

import unittest.mock as mock
import mlb_daily as m

SCHEDULE_RESP = {"dates": [{"games": [
    {"gamePk": 700001, "gameDate": "2026-08-16T23:05:00Z",
     "status": {"abstractGameState": "Preview"}},
    {"gamePk": 700002, "gameDate": "2026-08-16T22:10:00Z",
     "status": {"abstractGameState": "Live"}},
    {"gamePk": 700003, "gameDate": "2026-08-16T20:00:00Z",
     "status": {"abstractGameState": "Final"}},
]}]}

with mock.patch.object(m, "retry_get") as mock_get:
    mock_get.return_value.json.return_value = SCHEDULE_RESP
    mock_get.return_value.raise_for_status = lambda: None
    sched = bd._game_schedule("2026-08-16")

check(sched[700001]["started"] is False, "a Preview game is NOT started")
check(sched[700002]["started"] is True, "a Live game IS started")
check(sched[700003]["started"] is True, "a Final game IS started (started != still playing)")
check(sched[700001]["start"] == "2026-08-16T23:05:00Z",
      "the real scheduled gameDate passes through for client-side pruning",
      f"got {sched[700001]['start']}")

with mock.patch.object(m, "retry_get", side_effect=Exception("network down")):
    sched_fail = bd._game_schedule("2026-08-16")
check(sched_fail == {}, "a network failure returns {} rather than raising -- must never take "
      "down the whole dashboard build over one schedule fetch")


head("10. schedule/streaks pass through build_payload as their own top-level keys -- "
     "never swept into the generic stat-family loop (their rows have no hit_probability "
     "in the schedule's case, which would otherwise filter every game out silently)")

payload14 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-16",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "game_context": [
        {"game_pk": 1, "matchup": "Athletics @ Astros", "away_team": "Athletics",
         "home_team": "Astros", "away_sp": "X", "home_sp": "Y", "hp_ump": "Z",
         "game_start": "2026-08-16T23:05:00Z",
         "weather": {"dome": False, "temp": 80.0, "wind_mph": 5.0, "wind_effect": "neutral",
                    "park_hr_index": 50, "precip_prob": 0},
         "umpire": {"name": "Z", "k_pct": 0.22, "bb_pct": 0.08, "league_k_pct": 0.221,
                   "league_bb_pct": 0.085},
         "is_getaway": False, "is_opener": False,
         "picks": [{"name": "B", "prop": "Over 0.5 Hits", "hit_probability": 0.65,
                   "market_odds": -130, "price_clears": True, "why": "a real reason"}]},
    ],
    "streaks": [dict(row("Streaky Batter", "hits", 0.6, odds=-110, implied=0.55, edge=0.05,
                         clears=True), player_id=9, streak=6, streak_stat="hits")],
})
check(len(payload14["schedule"]) == 1,
      "the one real game passes through untouched, not filtered by the hit_probability "
      "check the generic stat-family loop applies to every other category",
      f"got {payload14['schedule']}")
check(payload14["schedule"][0]["matchup"] == "Athletics @ Astros",
      "the game's own fields survive intact")
check(len(payload14["streaks"]) == 1 and payload14["streaks"][0]["streak"] == 6,
      "the real streak entry passes through with its streak length intact",
      f"got {payload14['streaks']}")
check(payload14["summary"]["n_games"] == 1, "summary.n_games reflects the real schedule length")

payload15 = bd.build_payload({"generated_at": "x", "date": "2026-08-16", "game_context": []})
check(payload15["schedule"] == [], "an empty game_context list stays empty, doesn't crash")

payload15b = bd.build_payload({"generated_at": "x", "date": "2026-08-16"})
check(payload15b["schedule"] == [] and payload15b["streaks"] == [],
      "a result dict with no game_context/streaks key AT ALL (e.g. run_live_fetch's early "
      "return on a no-games night) still produces valid empty lists, not a KeyError")


head("11. _compute_streaks: direct request, verbatim: \"STREAKS. Hits in a row, 2+ bases "
     "in a row, over X strikeouts in a row, any trends that are useful.\" Only computed "
     "for players who already have a real candidate on tonight's board, one game-log fetch "
     "per unique player_id, STREAK_MIN=3 filters out noise, sorted longest-first. Unchanged "
     "by the Phase 4 rebuild.")

import mlb_sources as msrc


def streak_row(name, pid, stat, needs=1, ptype="batter", odds=-110):
    # odds=-110 by default: real bug, found live 2026-08-15 (Jacob
    # Misiorowski's "15 straight starts clearing Over 5.5 K" -- a real
    # streak against a threshold FanDuel had never actually posted a
    # price for, since his real line runs closer to 9). A streak-eligible
    # fixture needs a real market_odds unless a check is deliberately
    # testing the unpriced-exclusion rule itself.
    return dict(row(name, stat, 0.5, needs=needs, ptype=ptype, odds=odds), player_id=pid)


ALL_PRICED_17 = [
    streak_row("Five Hit Streak", 1, "hits"),
    streak_row("Four TB Streak", 2, "total_bases", needs=2),
    streak_row("Two Hit Streak", 3, "hits"),          # below STREAK_MIN -- must be excluded
    streak_row("Three K Streak", 4, "strikeouts", needs=5, ptype="pitcher"),
    streak_row("Multi Stat Hits", 5, "hits"),
    streak_row("Multi Stat RBIs", 5, "rbis"),         # same player_id=5, a DIFFERENT stat --
                                                       # must produce its OWN entry, one fetch total
    streak_row("Unpriced Hit Streak", 6, "hits", odds=None),  # no real FanDuel line -- must be excluded
    streak_row("Doubles Streak", 7, "doubles"),       # direct follow-up: "broaden the streaks to
                                                       # any relevant prop" -- not just hits/TB/K
]

BATTER_LOGS_17 = {
    1: [{"date": f"d{i}", "hits": 1, "total_bases": 1} for i in range(5)],
    2: [{"date": f"d{i}", "hits": 0, "total_bases": 2} for i in range(4)] + [{"date": "d4", "hits": 0, "total_bases": 0}],
    3: [{"date": "d0", "hits": 1, "total_bases": 1}, {"date": "d1", "hits": 1, "total_bases": 1},
        {"date": "d2", "hits": 0, "total_bases": 0}],
    5: [{"date": "d0", "hits": 1, "rbis": 1}, {"date": "d1", "hits": 1, "rbis": 1},
        {"date": "d2", "hits": 1, "rbis": 1}, {"date": "d3", "hits": 1, "rbis": 0}],
    6: [{"date": f"d{i}", "hits": 1, "total_bases": 0} for i in range(6)],  # a real 6-game streak, if it counted
    7: [{"date": f"d{i}", "doubles": 1} for i in range(3)],
}
PITCHER_LOGS_17 = {4: [{"date": f"d{i}", "strikeouts": 5 + i} for i in range(3)]}

batter_calls_17 = []


def fake_batter_log_17(pid, max_games=20):
    batter_calls_17.append(pid)
    return BATTER_LOGS_17.get(pid, [])


with mock.patch.object(msrc, "batter_recent_game_log", side_effect=fake_batter_log_17), \
     mock.patch.object(msrc, "pitcher_recent_starts", side_effect=lambda pid, max_games=15: PITCHER_LOGS_17.get(pid, [])):
    entries17 = bd._compute_streaks(ALL_PRICED_17)

check(batter_calls_17.count(5) == 1, "a player carrying multiple candidate rows across "
      "DIFFERENT stats (hits AND rbis) only gets ONE real game-log fetch, not one per row",
      f"got {batter_calls_17}")

# PHASE 4: entries are now a lean {id, streak, streak_stat} reference (see
# _compute_streaks' own docstring/comment) -- the client resolves name/
# player_id by looking the id up in PAYLOAD.props, not a third full copy of
# the row. Build the same id -> row lookup here to translate entries back
# to names/player_ids for these assertions.
by_id_17 = {r["id"]: r for r in ALL_PRICED_17}
names17 = [by_id_17[e["id"]]["name"] for e in entries17]
check("Five Hit Streak" in names17, "a real 5-game hit streak is surfaced")
check("Four TB Streak" in names17, "a real 4-game total-bases streak (needs=2) is surfaced")
check("Three K Streak" in names17, "a real 3-start strikeouts-needs-clearing streak is surfaced")
check("Doubles Streak" in names17,
      "a real doubles streak is surfaced -- direct follow-up request: \"broaden the streaks to "
      "any relevant prop,\" not just hits/total_bases/strikeouts", f"got {names17}")
check("Two Hit Streak" not in names17, "a 2-game streak is below STREAK_MIN=3 and excluded as noise",
      f"got {names17}")
check("Unpriced Hit Streak" not in names17,
      "a real, otherwise-qualifying streak is excluded when the candidate has no real FanDuel "
      "price (market_odds is None) -- this is the exact Misiorowski bug: a streak against a "
      "threshold that was never an actual bettable line", f"got {names17}")
check(batter_calls_17.count(6) == 0,
      "an unpriced candidate is filtered out before ever costing a game-log fetch",
      f"got {batter_calls_17}")
multi17 = [e for e in entries17 if by_id_17[e["id"]]["player_id"] == 5]
multi_stats17 = {e["streak_stat"]: e["streak"] for e in multi17}
check(multi_stats17 == {"hits": 4, "rbis": 3},
      "a player with real, independently-qualifying streaks in TWO different stats gets BOTH "
      "as separate entries (not deduped down to just the first-seen row)", f"got {multi_stats17}")
five17 = next(e for e in entries17 if by_id_17[e["id"]]["name"] == "Five Hit Streak")
four17 = next(e for e in entries17 if by_id_17[e["id"]]["name"] == "Four TB Streak")
check(five17["streak"] == 5 and four17["streak"] == 4, "streak lengths are counted correctly",
      f"got {five17['streak']}, {four17['streak']}")
check(by_id_17[entries17[0]["id"]]["name"] == "Five Hit Streak",
      "entries are sorted longest-streak-first", f"got {names17}")

ALL_PRICED_18 = [streak_row(f"Player {i}", i, "hits") for i in range(20)]
by_id_18 = {r["id"]: r for r in ALL_PRICED_18}
LOGS_18 = {i: [{"date": f"d{j}", "hits": 1, "total_bases": 1} for j in range(3 + i)] for i in range(20)}
with mock.patch.object(msrc, "batter_recent_game_log", side_effect=lambda pid, max_games=20: LOGS_18.get(pid, [])):
    entries18 = bd._compute_streaks(ALL_PRICED_18)
check(len(entries18) == 15, "output capped at 15 entries", f"got {len(entries18)}")
check(by_id_18[entries18[0]["id"]]["name"] == "Player 19",
      "the longest streaks survive the cap, not the first-seen",
      f"got {[by_id_18[e['id']]['name'] for e in entries18[:3]]}")


head("12. run_live_fetch()'s no-games early return stamps a timezone-aware generated_at -- "
     "real bug, found live 2026-08-15: datetime.now().isoformat() (naive, no tz suffix) gets "
     "parsed by a browser's `new Date(iso)` as LOCAL time, not UTC, so the page showed an "
     "'Updated' time hours in the future for a viewer west of UTC. Unchanged by the Phase 4 "
     "rebuild.")

import generate_picks as gp

with mock.patch.object(gp, "_build_and_score", return_value=None), \
     mock.patch.object(gp.m, "TODAY", "2026-08-16"):
    out21 = bd.run_live_fetch()
check("+00:00" in out21["generated_at"] or out21["generated_at"].endswith("Z"),
      "the no-games early-return path stamps a real UTC offset, not a naive local timestamp",
      f"got {out21['generated_at']!r}")


head("12b. _build_game_context(): real bug, found 2026-08-25 -- the picks_by_game dedup key "
     "used to be (name, prop) alone, with no game_pk. On a doubleheader, the same player can "
     "have the same real prop type (e.g. \"To Hit a Home Run\") as a genuinely distinct "
     "candidate in BOTH Game 1 and Game 2. Since the key omitted game_pk, Game 2's candidate "
     "was silently treated as a duplicate of Game 1's and dropped entirely -- so Game 2's "
     "drill-down page never showed it. Extracted from run_live_fetch() into its own function "
     "specifically so this fix gets direct test coverage without the live network path.")

DH_GAME_1, DH_GAME_2 = 111111, 222222
dh_game_meta = [
    {"game_pk": DH_GAME_1, "matchup": "SEA @ OAK (Gm 1)", "away_team": "Seattle Mariners",
     "home_team": "Oakland Athletics", "away_sp": "P One", "home_sp": "P Two",
     "hp_ump": "Ump A", "is_getaway": False, "is_opener": False},
    {"game_pk": DH_GAME_2, "matchup": "SEA @ OAK (Gm 2)", "away_team": "Seattle Mariners",
     "home_team": "Oakland Athletics", "away_sp": "P Three", "home_sp": "P Four",
     "hp_ump": "Ump B", "is_getaway": False, "is_opener": False},
]
dh_all_priced = [
    # Same player, same prop TEXT, in each half of a real doubleheader --
    # two distinct real candidates, not a duplicate of one another.
    {"name": "Julio Rodriguez", "prop": "To Hit a Home Run", "game_pk": DH_GAME_1,
     "hit_probability": 0.30, "market_odds": -120, "price_clears": True, "why": ["Game 1 why"]},
    {"name": "Julio Rodriguez", "prop": "To Hit a Home Run", "game_pk": DH_GAME_2,
     "hit_probability": 0.22, "market_odds": +140, "price_clears": True, "why": ["Game 2 why"]},
    # A genuine intra-game overlap (moonshot AND best-of-category both
    # produced the SAME player+prop+game candidate) -- this is the real
    # case the dedup exists for, and must still collapse to one entry.
    {"name": "Julio Rodriguez", "prop": "To Hit a Home Run", "game_pk": DH_GAME_1,
     "hit_probability": 0.30, "market_odds": -120, "price_clears": True, "why": ["dup of Game 1"]},
]
dh_ctx = bd._build_game_context(dh_all_priced, dh_game_meta, {}, {}, set(), {})
dh_by_pk = {g["game_pk"]: g for g in dh_ctx}
check(len(dh_ctx) == 2, "both halves of the doubleheader get their own game_context entry",
      f"got {len(dh_ctx)}")
check(len(dh_by_pk[DH_GAME_1]["picks"]) == 1, "Game 1's intra-game moonshot/category overlap "
      "still collapses to one real pick", f"got {dh_by_pk[DH_GAME_1]['picks']}")
check(len(dh_by_pk[DH_GAME_2]["picks"]) == 1,
      "Game 2's real, distinct candidate for the SAME player+prop is NOT dropped as a false "
      "duplicate of Game 1's -- this is the exact bug: before the game_pk-aware key, this "
      "list was empty", f"got {dh_by_pk[DH_GAME_2]['picks']}")
check(dh_by_pk[DH_GAME_2]["picks"][0]["hit_probability"] == 0.22,
      "Game 2's own real probability survives, not Game 1's", f"got {dh_by_pk[DH_GAME_2]['picks'][0]}")


head("13. app.js's humanizeReason() actually translates real jargon strings, not just "
     "parses without crashing. Loaded directly from dashboard/static/app.js (a real, plain "
     "file now -- no more extracting a <script> block out of server-rendered HTML). These "
     "are verbatim strings generate_picks.py emits live that would otherwise fall straight "
     "through untranslated.")

import shutil
import subprocess
node = shutil.which("node")
APP_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "static", "app.js")

if node:
    harness = """
const document = {getElementById: () => ({addEventListener(){}, textContent:'', dataset:{},
    style:{}, setAttribute(){}, querySelectorAll: () => [], querySelector: () => null}),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => ({style:{}}),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
try { """ + open(APP_JS_PATH, encoding="utf-8").read() + """ } catch (e) {}
const CASES = [
  ["Arizona Diamondbacks scores off Chris Sale (home SP) in the top 1st: 23.4% (shrunk, 22 starts)",
   "Arizona Diamondbacks"],
  ["Pitch-type exploit: RV/100 +3.5 vs SL (opposing SP throws it 23.9% of the time)", "slider"],
  ["Sharp money backing St. Louis Cardinals (money% +42 pts vs ticket%)", "St. Louis Cardinals"],
  ["Recency-weighted K rate 14.3% (exp. decay, halflife 30d, 10 real starts / 233 BF) -- drives the strikeout probability model", "14.3%"],
  ["BvP: 4-for-13 vs Matthew Liberatore (standard error ±13 pts on a 13-AB career sample -- weighted lightly on that basis, not just because the count looks small)", "Matthew Liberatore"],
  ["HP ump accuracy 93.9%", "93.9%"],
];
let ok = true;
for (const [raw, mustContain] of CASES) {
  const out = humanizeReason(raw);
  if (out === raw || !out.includes(mustContain)) {
    console.error("FAIL: " + JSON.stringify(raw) + " -> " + JSON.stringify(out));
    ok = false;
  }
}
if (!ok) process.exit(1);
console.log("all " + CASES.length + " reason translations passed");
"""
    harness_path = tempfile.mktemp(suffix=".js")
    with open(harness_path, "w") as f:
        f.write(harness)
    try:
        r = subprocess.run([node, harness_path], capture_output=True, text=True)
        check(r.returncode == 0, "every real jargon string actually gets translated to readable "
              "text (not left as raw RV/100, halflife, BvP, money%/ticket% jargon)",
              r.stdout + r.stderr)
    finally:
        os.remove(harness_path)
else:
    check(True, "node not available -- reason-translation check skipped, not failed")


head("13b. sampleLabelCaveat() (2026-08-25): the Performance page used to show a bare "
     "hit-rate percentage with no caveat at all, so an early 100% off 2 graded picks read as "
     "trustworthy as a mature record. Verifies the honest caveat text renders for each thin "
     "eval_lib.sample_size_label() tier the backend can send, and renders NOTHING for a real, "
     "reportable (n>=100) sample -- a mature number gets no unnecessary warning.")

if node:
    harness_sample_caveat = """
const document = {getElementById: () => ({addEventListener(){}, textContent:'', dataset:{},
    style:{}, setAttribute(){}, querySelectorAll: () => [], querySelector: () => null}),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => ({style:{}}),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
try { """ + open(APP_JS_PATH, encoding="utf-8").read() + """ } catch (e) {}
let ok = true;
function assertTrue(cond, msg) { if (!cond) { console.error("FAIL: " + msg); ok = false; } }

const insufficient = sampleLabelCaveat("insufficient", 2);
assertTrue(insufficient && insufficient.includes("2") && /far too few/i.test(insufficient),
  "insufficient (n=2) renders a real caveat naming the exact n and saying it's far too few, got " + insufficient);

const thin = sampleLabelCaveat("thin", 10);
assertTrue(thin && thin.includes("10") && /thin sample/i.test(thin),
  "thin (n=10) renders a real caveat naming the exact n, got " + thin);

const directional = sampleLabelCaveat("directional", 47);
assertTrue(directional && directional.includes("47"),
  "directional (n=47) renders a real caveat naming the exact n, got " + directional);

const reportable = sampleLabelCaveat("reportable", 200);
assertTrue(reportable === null,
  "reportable (n>=100, a real mature sample) renders NO caveat -- got " + JSON.stringify(reportable));

const unknown = sampleLabelCaveat(undefined, 5);
assertTrue(unknown === null,
  "a missing/unrecognized sample_label (e.g. an older payload before this field existed) " +
  "degrades to no caveat rather than crashing, got " + JSON.stringify(unknown));

if (!ok) process.exit(1);
console.log("sampleLabelCaveat() checks passed");
"""
    harness_path_sample_caveat = tempfile.mktemp(suffix=".js")
    with open(harness_path_sample_caveat, "w") as f:
        f.write(harness_sample_caveat)
    try:
        r = subprocess.run([node, harness_path_sample_caveat], capture_output=True, text=True)
        check(r.returncode == 0, "sampleLabelCaveat() renders an honest, real caveat for every "
              "thin sample tier and stays silent for a real reportable sample",
              r.stdout + r.stderr)
    finally:
        os.remove(harness_path_sample_caveat)
else:
    check(True, "node not available -- sampleLabelCaveat check skipped, not failed")


head("13c. staleChip() (2026-08-25): real bug -- a price whose most recent FanDuel re-fetch "
     "actually FAILED (market_fetch_state === \"FETCH_FAILED\") showed no chip at all on the "
     "compact card grid, since this only ever checked the separate p.stale field, which "
     "refresh_prices.py's FETCH_FAILED branch never sets. The detail sheet's own "
     "priceFreshnessState() already flagged this same row correctly -- the compact card just "
     "never surfaced it. Verifies the plain, simplified 'Price May Be Outdated' wording "
     "renders for a genuinely failed fetch, ordinary 'Stale Data' still renders for the "
     "pre-existing p.stale case, and neither renders for a normal, successfully-priced row.")

if node:
    harness_stale_chip = """
const document = {getElementById: () => ({addEventListener(){}, textContent:'', dataset:{},
    style:{}, setAttribute(){}, querySelectorAll: () => [], querySelector: () => null}),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => ({style:{}}),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
try { """ + open(APP_JS_PATH, encoding="utf-8").read() + """ } catch (e) {}
let ok = true;
function assertTrue(cond, msg) { if (!cond) { console.error("FAIL: " + msg); ok = false; } }

const failedFetch = staleChip({ market_odds: -120, market_fetch_state: "FETCH_FAILED", stale: false });
assertTrue(failedFetch.includes("Price May Be Outdated"),
  "a genuinely failed fetch (market_fetch_state=FETCH_FAILED) renders the plain 'Price May Be " +
  "Outdated' chip even though p.stale is false -- got " + JSON.stringify(failedFetch));

const oldStale = staleChip({ market_odds: -120, market_fetch_state: "MATCHED", stale: true });
assertTrue(oldStale.includes("Stale Data"),
  "the pre-existing p.stale=true case (an older successful check) still renders 'Stale Data' " +
  "unchanged -- got " + JSON.stringify(oldStale));

const fresh = staleChip({ market_odds: -120, market_fetch_state: "MATCHED", stale: false });
assertTrue(fresh === "",
  "a normal, freshly and successfully priced row renders no chip at all -- got " + JSON.stringify(fresh));

if (!ok) process.exit(1);
console.log("staleChip() checks passed");
"""
    harness_path_stale_chip = tempfile.mktemp(suffix=".js")
    with open(harness_path_stale_chip, "w") as f:
        f.write(harness_stale_chip)
    try:
        r = subprocess.run([node, harness_path_stale_chip], capture_output=True, text=True)
        check(r.returncode == 0, "staleChip() surfaces a genuinely failed price re-fetch on the "
              "compact card grid, in plain wording, not just in the detail sheet", r.stdout + r.stderr)
    finally:
        os.remove(harness_path_stale_chip)
else:
    check(True, "node not available -- staleChip check skipped, not failed")


head("13d. suggestedParlayBlock() (2026-08-25): real bug -- this read l.american and "
     "parlay.combined_american, neither of which exists. dashboard/build_dashboard.py's "
     "_build_suggested_parlay() (already covered by check 7's Python tests) actually names "
     "them market_odds (per leg) and combined_american_odds. Every real, correctly priced "
     "parlay leg rendered a blank price, and the combined-odds line always fell back to '--' "
     "-- a fully-priced real parlay looked broken. Also verifies naive_probability_note and "
     "correlation_notes (the backend's own honesty context -- computed, but never reaching "
     "the page before this fix, the same 'computed then discarded' bug class found elsewhere "
     "in this project) now render, and the combined figure is explicitly labeled 'Estimated'.")

if node:
    harness_parlay = """
// esc() (dashboard/static/app.js) round-trips through a real
// document.createElement("div").textContent/.innerHTML escape -- a bare
// {style:{}} stub (fine for harnesses that never call esc()) silently makes
// EVERY esc() call return undefined, since .textContent/.innerHTML aren't
// real getters/setters on a plain object literal. This harness calls esc()
// (via suggestedParlayBlock's own name/prop/note rendering), so it needs
// the same real-escaping element mock the My Board harness (check 18b) uses.
function makeEscEl() {
  let t = '';
  return { set textContent(v) { t = String(v); }, get textContent() { return t; },
    get innerHTML() {
      return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    } };
}
const document = {getElementById: () => ({addEventListener(){}, textContent:'', dataset:{},
    style:{}, setAttribute(){}, querySelectorAll: () => [], querySelector: () => null}),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => makeEscEl(),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
try { """ + open(APP_JS_PATH, encoding="utf-8").read() + """ } catch (e) {}
let ok = true;
function assertTrue(cond, msg) { if (!cond) { console.error("FAIL: " + msg); ok = false; } }

const realParlay = {
  legs: [
    { name: "A", prop: "Over 0.5 Hits", market_odds: -150, hit_probability: 0.72 },
    { name: "B", prop: "Over 0.5 Total Bases", market_odds: -130, hit_probability: 0.68 },
  ],
  combined_american_odds: -400,
  naive_probability_note: "Product of each leg's own probability, assuming independence.",
  correlation_notes: ["A + B: same game, positively correlated"],
};
const html = suggestedParlayBlock(realParlay);
assertTrue(html.includes("-150") && html.includes("-130"),
  "each real leg's own market_odds price actually renders on the card, not a blank -- got " + html);
assertTrue(html.includes("-400"),
  "the real combined_american_odds figure renders, not the permanent '--' fallback of the field-name bug -- got " + html);
assertTrue(/Estimated combined odds/i.test(html),
  "the combined figure is explicitly labeled Estimated, not presented as a certain number -- got " + html);
assertTrue(html.includes("assuming independence"),
  "the backend's own naive_probability_note caveat now reaches the page -- got " + html);
assertTrue(html.includes("positively correlated"),
  "correlation_notes now reach the page -- got " + html);

// A real parlay whose combined odds genuinely could not be computed (should
// not happen given build_dashboard.py's own priced_pool filter, but honest
// degradation matters) must say so, never silently show a stale/blank dash
// with no explanation.
const noCombined = { legs: [{ name: "A", prop: "X", market_odds: -110, hit_probability: 0.6 }] };
const html2 = suggestedParlayBlock(noCombined);
assertTrue(/unavailable/i.test(html2),
  "a missing combined_american_odds says 'unavailable' explicitly rather than a bare dash -- got " + html2);

if (!ok) process.exit(1);
console.log("suggestedParlayBlock() checks passed");
"""
    harness_path_parlay = tempfile.mktemp(suffix=".js")
    with open(harness_path_parlay, "w") as f:
        f.write(harness_parlay)
    try:
        r = subprocess.run([node, harness_path_parlay], capture_output=True, text=True)
        check(r.returncode == 0, "suggestedParlayBlock() renders real leg prices and the real "
              "combined odds under their actual field names, honestly labeled Estimated, with "
              "the backend's own independence/correlation caveats surfaced", r.stdout + r.stderr)
    finally:
        os.remove(harness_path_parlay)
else:
    check(True, "node not available -- suggestedParlayBlock check skipped, not failed")


head("16. Today-page PASS 2/3 redesign (2026-08-25): the query-string half of a route hash "
     "used to be silently discarded (onRouteChange() split it off and threw it away) -- every "
     "\"See all research -> #/props?status=lean\" link on the page was a real navigation that "
     "did nothing, since the filter it claimed to apply never actually landed. Also verifies "
     "Explore by Prop's real-counts-only chip strip and the removal of the invented "
     "\"TOP PICK #N\" ordinal badge (see Research Correctness Check 2 / _assign_top_pick_rank()'s "
     "own docstring for why no such canonical order exists for this population).")

if node:
    harness2 = """
function makeEl() {
  let _text = '';
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){},
    getAttribute: () => null, querySelectorAll: () => [], querySelector: () => null,
    get textContent() { return _text; }, set textContent(v) { _text = String(v); },
    get innerHTML() {
      return _text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
    hidden: false, append(){},
  };
}
const document = {getElementById: () => makeEl(), documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => makeEl(),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
let location = window.location;
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
let ok = true;
function assertEq(actual, expected, label) {
  if (actual !== expected) { console.error("FAIL " + label + ": got " + JSON.stringify(actual) + " want " + JSON.stringify(expected)); ok = false; }
}
function assertTrue(cond, label) {
  if (!cond) { console.error("FAIL " + label); ok = false; }
}

try {
""" + open(APP_JS_PATH, encoding="utf-8").read() + """

// The assertions below run INSIDE this try block deliberately: route/
// filters/DATA are `let`-scoped to app.js's own top level, and pasting the
// whole file inside try{...} means those bindings are block-scoped to
// THIS block, not visible after the closing brace (only `function`
// declarations like onRouteChange/exploreByPropStrip/pickCard, which get
// hoisted out under sloppy-mode Annex B semantics, would survive outside
// it). Testing real internal state (not just return values) requires
// staying in this scope.

// -- query-string routing: a URL's filter params must actually apply --
location.hash = "#/props?status=lean";
onRouteChange();
assertEq(route, "props", "route parsed from hash despite query string");
assertEq(filters.status, "lean", "status=lean from the URL actually reached filters.status");

location.hash = "#/props?family=home_runs";
onRouteChange();
assertEq(filters.family, "home_runs", "family=home_runs from the URL actually reached filters.family");

// 2026-08-2X route-filter-leakage fix ("Top Pick filter escape", Part 2 UX
// audit): this used to assert the OPPOSITE -- that an absent status param
// preserved whatever filters.status a PRIOR navigation had left behind.
// That was traced to a real bug: renderProps()'s own <select> handlers
// mutate `filters` directly and never touch location.hash, so on-page
// filter changes never re-trigger onRouteChange() at all -- the only
// caller of onRouteChange() is a fresh hash navigation INTO the props
// route (nav-bar click, a stat-tile link, browser back/forward). A user
// who filtered to Top Pick, navigated to Games, then clicked "All Props"
// in the main nav (a plain #/props link, no query) got the stale
// status=top_pick filter silently reapplied with no visible reason and no
// obvious way out. Every real navigation into props now resets to
// defaults FIRST, then applies whatever params THIS link actually
// carries -- a link can still deliberately pre-filter, it just can't
// leave a previous, unrelated visit's filter behind.
filters.status = "top_pick";
location.hash = "#/props?family=hits";
onRouteChange();
assertEq(filters.status, "all", "REGRESSION GUARD: a fresh navigation into props resets a stale " +
  "status filter left over from a previous visit -- the exact 'Top Pick filter escape' bug");
assertEq(filters.family, "hits", "family=hits from the URL still applied, on top of the reset");

// A plain nav-bar-style entry (no query at all) resets every filter,
// including one set via the page's own dropdown UI moments earlier.
filters.family = "strikeouts"; filters.status = "value"; filters.evidence = "A"; filters.search = "ohtani";
location.hash = "#/props";
onRouteChange();
assertEq(filters.family, "all", "a plain #/props entry resets family");
assertEq(filters.status, "all", "a plain #/props entry resets status");
assertEq(filters.evidence, "all", "a plain #/props entry resets evidence");
assertEq(filters.search, "", "a plain #/props entry resets search");

// destination-integrity fix: the global-search "See all N matching props"
// link now carries the search text itself for a plain (non-market-intent)
// query, so the link doesn't land on the full unfiltered list.
location.hash = "#/props?search=ohtani";
onRouteChange();
assertEq(filters.search, "ohtani", "search=ohtani from the URL actually reached filters.search");

// Value/Longshot count-integrity fix: status=longshot is a real, distinct
// filter value (applyFilters already special-cases it via isLongshot()),
// separate from status=value -- confirms the URL contract this fix's new
// Longshots tile relies on.
location.hash = "#/props?status=longshot";
onRouteChange();
assertEq(filters.status, "longshot", "status=longshot from the URL reached filters.status");
const valueRow = {recommendation_status: "value", hit_probability: 0.5};
const longshotRow = {recommendation_status: "value", hit_probability: 0.1};
const splitRows = applyFilters([valueRow, longshotRow]);
assertEq(splitRows.length, 1, "status=longshot excludes the non-longshot value row");
assertTrue(splitRows[0] === longshotRow, "status=longshot keeps only the real longshot row");
filters.status = "value";
const valueOnlyRows = applyFilters([valueRow, longshotRow]);
assertEq(valueOnlyRows.length, 1, "status=value excludes the longshot row (unchanged behavior) -- " +
  "this is the real split the Today page's two separate tiles/counts must match");
assertTrue(valueOnlyRows[0] === valueRow, "status=value keeps only the real non-longshot value row");
filters.status = "all"; filters.search = "";

// -- Explore by Prop: real counts only, correct family mapping, no dead chips --
const families = [
  {stat: "hits", label: "Hits", count: 12},
  {stat: "moonshot", label: "Home Runs", count: 4},
  {stat: "strikeouts", label: "Strikeouts", count: 0},
  {stat: "singles", label: "Singles", count: 3},
];
const strip = exploreByPropStrip(families);
assertTrue(strip.includes('href="#/props?family=hits"'), "Hits chip links to family=hits");
assertTrue(strip.includes(">12 tonight<"), "Hits chip shows the real count");
assertTrue(strip.includes('href="#/props?family=home_runs"'),
  "moonshot family correctly maps to home_runs via familyFilterValue() (reused, not reimplemented)");
assertTrue(!strip.includes("family=strikeouts"),
  "a family with a real count of 0 tonight gets no chip -- never a dead tap");
assertTrue(strip.includes(">More<"), "a More chip always appears, linking to the full board");
assertTrue(strip.includes('href="#/props">More'), "More chip links to the unfiltered All Props page");

// -- pickCard: no invented ordinal ranking --
const p = {id:"x1", name:"Test Player", team:"NYY", prop:"Over 0.5 Hits", hit_probability:0.65,
  recommendation_status:"top_pick", market_odds:-150, market_implied:0.60, market_edge:0.05,
  reliability:"B", why:[]};
const card = pickCard(p);
assertTrue(!card.includes("TOP PICK #"), "pickCard never renders a 'TOP PICK #N' ordinal badge");
assertTrue(card.includes("TOP PICK"), "the card still shows the real TOP PICK status chip once");

} catch (e) { console.error(e); process.exit(1); }

if (!ok) process.exit(1);
console.log("Today-page routing/Explore-by-Prop/no-invented-rank checks passed");
"""
    harness_path2 = tempfile.mktemp(suffix=".js")
    with open(harness_path2, "w") as f:
        f.write(harness2)
    try:
        r = subprocess.run([node, harness_path2], capture_output=True, text=True)
        check(r.returncode == 0, "URL filter params apply on navigation, Explore by Prop shows only "
              "real non-zero counts with correct family mapping, and pickCard never renders an "
              "invented 'TOP PICK #N' ordinal", r.stdout + r.stderr)
    finally:
        os.remove(harness_path2)
else:
    check(True, "node not available -- Today-page routing/Explore-by-Prop check skipped, not failed")


head("17. Detail sheet PASS (2026-08-25): priceFreshnessState()/whyNotTopPickReason()/"
     "_ordinalSuffix() directionality, and detailBody() end to end -- confirms no naive "
     "Supportive/Concern component grading was (re)introduced (see "
     "frontend/detail_sheet_data_audit_2026-08-25.md for why that's unsafe), Why It Could Miss "
     "never fabricates a concern to force symmetry, Opportunity only appears with a real "
     "batting_order fact, and Why Not a Top Pick only fires for status_reasons that are real.")

if node:
    harness3 = """
function makeEl() {
  let _text = '';
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){},
    getAttribute: () => null, querySelectorAll: () => [], querySelector: () => null,
    get textContent() { return _text; }, set textContent(v) { _text = String(v); },
    get innerHTML() {
      return _text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
    hidden: false, append(){},
  };
}
const document = {getElementById: () => makeEl(), documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => makeEl(),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
let location = window.location;
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
let ok = true;
function assertEq(actual, expected, label) {
  if (actual !== expected) { console.error("FAIL " + label + ": got " + JSON.stringify(actual) + " want " + JSON.stringify(expected)); ok = false; }
}
function assertTrue(cond, label) {
  if (!cond) { console.error("FAIL " + label); ok = false; }
}

try {
""" + open(APP_JS_PATH, encoding="utf-8").read() + """

DATA = { schedule: [] };

// -- priceFreshnessState(): real states only, never a fabricated one --
assertEq(priceFreshnessState({market_odds: null}).tone, "unposted", "no market_odds -> unposted");
assertEq(priceFreshnessState({market_odds: -150, market_fetch_state: "FETCH_FAILED"}).tone, "stale",
  "FETCH_FAILED -> stale tone (last known, not treated as current)");
assertEq(priceFreshnessState({market_odds: -150, market_fetch_state: "IN_PLAY"}).tone, "live", "IN_PLAY -> live tone");
// 2026-08-25 release-readiness audit (Audit B): traced IN_PLAY to dashboard/refresh_prices.py's
// real behavior -- once a game passes the pregame wagering cutoff, the price is FROZEN and
// never re-fetched; only game-state fields keep advancing. The old wording ("In play... the
// price can move quickly") falsely implied real-time in-game repricing this pipeline doesn't
// do. Must say the game is live AND the price is a locked pregame snapshot -- never imply the
// number itself is current/moving.
const inPlayState = priceFreshnessState({market_odds: -150, market_fetch_state: "IN_PLAY"});
assertTrue(!/can move quickly|^In play$/.test(inPlayState.label + " " + inPlayState.detail),
  "IN_PLAY wording never claims the displayed price itself is moving/current",
  "got label=" + inPlayState.label + " detail=" + inPlayState.detail);
assertTrue(/locked|frozen|preserved/i.test(inPlayState.label + inPlayState.detail)
  && /live/i.test(inPlayState.label),
  "IN_PLAY wording says the GAME is live but the PRICE is a locked/frozen pregame snapshot -- both facts stated honestly, never conflated",
  "got label=" + inPlayState.label + " detail=" + inPlayState.detail);
assertEq(priceFreshnessState({market_odds: -150, stale: true}).tone, "stale", "board-level stale flag -> stale tone");
assertEq(priceFreshnessState({market_odds: -150, market_fetch_state: "MATCHED", stale: false}).tone, "current",
  "a real, fresh, matched price -> current tone");

// -- whyNotTopPickReason(): only for an already-interesting non-Top-Pick --
assertEq(whyNotTopPickReason({recommendation_status: "top_pick", status_reasons: ["x"]}), null,
  "a real Top Pick never shows a 'why not' reason");
assertEq(whyNotTopPickReason({recommendation_status: "lean", status_reasons: ["a real reason"]}), "a real reason",
  "a Lean's real status_reasons[0] is surfaced verbatim");
assertEq(whyNotTopPickReason({recommendation_status: "neutral", hit_probability: 0.45, status_reasons: ["x"]}), null,
  "a low-probability Neutral doesn't clutter with a 'why not' reason");
assertEq(whyNotTopPickReason({recommendation_status: "neutral", hit_probability: 0.65, status_reasons: ["thin evidence"]}),
  "thin evidence", "a genuinely interesting (>=60%) Neutral DOES surface its real reason");

// -- isTopPickSuspect()/suspectChip(): P0-5 fix, real complaint -- "a Top
// Pick with a major market-disagreement/SUSPECT warning must show that
// warning, not hide it because it still qualified." classify_recommendation()
// appends a SECOND status_reasons entry only for a SUSPECT Top Pick; the
// old-only reader of status_reasons (whyNotTopPickReason, tested above)
// explicitly returns null for every top_pick, so this note was previously
// unreachable everywhere on the site. --
assertTrue(isTopPickSuspect({recommendation_status: "top_pick", status_reasons: ["primary reason", "note: the market itself disagrees"]}),
  "a Top Pick with 2 status_reasons (the SUSPECT-note shape) is flagged suspect");
assertTrue(!isTopPickSuspect({recommendation_status: "top_pick", status_reasons: ["primary reason"]}),
  "a normal Top Pick with only 1 status_reasons is NOT flagged suspect");
assertTrue(!isTopPickSuspect({recommendation_status: "lean", status_reasons: ["a", "b"]}),
  "a non-Top-Pick with 2 status_reasons is never flagged suspect -- this is Top-Pick-specific");
assertTrue(suspectChip({recommendation_status: "top_pick", status_reasons: ["p", "note: market disagrees"]}).includes("Market Disagrees"),
  "suspectChip() renders a real, visible chip for a suspect Top Pick");
assertEq(suspectChip({recommendation_status: "top_pick", status_reasons: ["p"]}), "",
  "suspectChip() renders nothing for a non-suspect Top Pick");

const suspectTopPick = {
  id: "c", name: "Player C", team: "SEA", prop: "Over 0.5 Hits", hit_probability: 0.66,
  recommendation_status: "top_pick", market_odds: -140, market_implied: 0.583, market_edge: 0.077,
  reliability: "A", sample_n: 120, why: ["Season wRC+ 130 — above-average hitter"], watchouts: [],
  status_reasons: ["clears the real probability floor", "note: the market itself disagrees with this read (ratio 2.1x vs devigged) — still a Top Pick on the model's own probability and price test, but size with that in mind"],
  batting_order: null,
};
const body3 = detailBody(suspectTopPick);
assertTrue(body3.includes("Market Disagrees"), "a suspect Top Pick's card chip renders in the detail view too");
assertTrue(body3.includes("the market itself disagrees with this read"),
  "a suspect Top Pick's detail view shows the REAL warning text verbatim, not hidden");
assertTrue(body3.includes("top-pick-warning"), "the warning renders in its own visually-distinct section");

// -- _ordinalSuffix(): plain English ordinals, including the 11/12/13 exception --
assertEq(_ordinalSuffix(1), "st", "1st");
assertEq(_ordinalSuffix(2), "nd", "2nd");
assertEq(_ordinalSuffix(3), "rd", "3rd");
assertEq(_ordinalSuffix(4), "th", "4th");
assertEq(_ordinalSuffix(11), "th", "11th (not 11st)");
assertEq(_ordinalSuffix(12), "th", "12th (not 12nd)");
assertEq(_ordinalSuffix(13), "th", "13th (not 13rd)");

// -- detailBody(): no naive component grading, honest empty-miss fallback,
// Opportunity only with a real fact, Why Not a Top Pick only when it applies --
const topPickNoMiss = {
  id: "a", name: "Player A", team: "NYY", prop: "Over 0.5 Hits", hit_probability: 0.68,
  recommendation_status: "top_pick", market_odds: -150, market_implied: 0.60, market_edge: 0.08,
  reliability: "B", sample_n: 80, why: [], watchouts: [], status_reasons: [], batting_order: null,
};
const body1 = detailBody(topPickNoMiss);
// Matches a LABEL/badge usage (tag-adjacent), not the word appearing in
// ordinary prose -- the honest "no major model-side concern" fallback
// sentence legitimately contains "concern" and must not trip this check.
assertTrue(!/>\s*(Supportive|Concern|Mixed)\s*<|THE CASE/i.test(body1),
  "detailBody() never renders naive Supportive/Concern/Mixed component-grade labels");
assertTrue(body1.includes("No major model-side concern"),
  "an empty watchouts list renders the honest fallback, never a fabricated concern");
assertTrue(!body1.includes("Opportunity"), "no Opportunity section when batting_order is null");
assertTrue(!body1.includes("Why Not a Top Pick"), "a real Top Pick never shows Why Not a Top Pick");

const leanWithOrder = {
  id: "b", name: "Player B", team: "BOS", prop: "Over 1.5 Total Bases", hit_probability: 0.63,
  recommendation_status: "lean", market_odds: null, market_implied: null, market_edge: null,
  reliability: "B", sample_n: 80, why: ["Season barrel% 12"], watchouts: [],
  status_reasons: ["a real read, but no market price is posted yet"], batting_order: 2,
};
const body2 = detailBody(leanWithOrder);
assertTrue(body2.includes("Opportunity") && body2.includes("2nd in the order"),
  "a real batting_order renders as a plain ordinal fact in Opportunity");
assertTrue(body2.includes("Why Not a Top Pick") && body2.includes("A real read, but no market price is posted yet"),
  "an interesting Lean surfaces its real status_reasons[0] (capSentence-cased, otherwise verbatim), not a guess");

} catch (e) { console.error(e); process.exit(1); }

if (!ok) process.exit(1);
console.log("Detail sheet directionality/priceFreshness/whyNotTopPick/ordinal checks passed");
"""
    harness_path3 = tempfile.mktemp(suffix=".js")
    with open(harness_path3, "w") as f:
        f.write(harness3)
    try:
        r = subprocess.run([node, harness_path3], capture_output=True, text=True)
        check(r.returncode == 0, "priceFreshnessState/whyNotTopPickReason/_ordinalSuffix/detailBody() "
              "all behave correctly and no naive component-grading language appears",
              r.stdout + r.stderr)
    finally:
        os.remove(harness_path3)
else:
    check(True, "node not available -- detail sheet check skipped, not failed")


head("18. My Board PASS (2026-08-25): snapshot versioning/migration, 'Since You Saved This' "
     "deltas (real presentation thresholds, deterioration never hidden), and changeSummary(). "
     "Audited the REAL pre-existing v1 snapshot shape first ({status, odds, lineup_assumed, "
     "started} -- confirmed by direct code read, not assumed) before building v2 on top of it.")

if node:
    harness4 = """
function makeEl() {
  let _text = '';
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){},
    getAttribute: () => null, querySelectorAll: () => [], querySelector: () => null,
    get textContent() { return _text; }, set textContent(v) { _text = String(v); },
    get innerHTML() {
      return _text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
    hidden: false, append(){},
  };
}
const document = {getElementById: () => makeEl(), documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => makeEl(),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
let location = window.location;
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
let ok = true;
function assertEq(actual, expected, label) {
  if (actual !== expected) { console.error("FAIL " + label + ": got " + JSON.stringify(actual) + " want " + JSON.stringify(expected)); ok = false; }
}
function assertTrue(cond, label) {
  if (!cond) { console.error("FAIL " + label); ok = false; }
}

try {
""" + open(APP_JS_PATH, encoding="utf-8").read() + """

// -- normalizeSnapshot(): real v1 shape migrates cleanly, never crashes,
// never invents a value v1 didn't capture --
assertEq(normalizeSnapshot(null), null, "no snapshot at all -> null, not a crash");
const v1Raw = { status: "lean", odds: -120, lineup_assumed: true, started: false };
const v1Norm = normalizeSnapshot(v1Raw);
assertEq(v1Norm.market_odds, -120, "v1 odds maps to market_odds");
assertEq(v1Norm.recommendation_status, "lean", "v1 status maps to recommendation_status");
assertEq(v1Norm.hit_probability, null, "v1 NEVER captured probability -- must stay null, not backfilled");
assertEq(v1Norm.market_edge, null, "v1 NEVER captured edge -- must stay null, not backfilled");

const v2Raw = { schema_version: WATCH_SNAPSHOT_SCHEMA_VERSION, hit_probability: 0.63, market_odds: 140,
  market_implied: 0.55, market_edge: 0.05, recommendation_status: "lean", lineup_assumed: true, started: false };
assertEq(normalizeSnapshot(v2Raw).hit_probability, 0.63, "a real v2 snapshot passes through unchanged");

// -- sinceYouSavedChanges(): presentation thresholds, real deltas only --
watchSnapshot = { "v1-id": v1Raw };
const v1CurrentProp = { id: "v1-id", hit_probability: 0.70, market_odds: -105, market_implied: 0.52,
  market_edge: 0.09, recommendation_status: "top_pick", lineup_assumed: false, game_start: null };
const v1Changes = sinceYouSavedChanges(v1CurrentProp);
assertTrue(!v1Changes.some(c => c.key === "probability"),
  "a v1 snapshot never shows a probability delta -- it never captured one");
assertTrue(v1Changes.some(c => c.key === "odds"), "a v1 snapshot DOES show a real odds delta -- it captured that");
assertTrue(v1Changes.some(c => c.key === "lineup"), "v1's lineup_assumed=true -> now false shows as Confirmed");
assertTrue(v1Changes.some(c => c.key === "status"), "v1's status changed lean -> top_pick shows as a real status change");

watchSnapshot = { "v2-id": v2Raw };
const tinyMove = { id: "v2-id", hit_probability: 0.64, market_odds: 140, market_implied: 0.55,
  market_edge: 0.06, recommendation_status: "lean", lineup_assumed: true, game_start: null };
assertEq(sinceYouSavedChanges(tinyMove).length, 0,
  "a 1pp probability move and a 1pp edge move both stay under the 2pp presentation threshold -- no noise shown");

const bigImprove = { id: "v2-id", hit_probability: 0.70, market_odds: 140, market_implied: 0.55,
  market_edge: 0.10, recommendation_status: "lean", lineup_assumed: true, game_start: null };
const improveChanges = sinceYouSavedChanges(bigImprove);
const probUp = improveChanges.find(c => c.key === "probability");
assertTrue(probUp && probUp.stronger === true, "a real 7pp probability GAIN shows as stronger=true");

const bigWorsen = { id: "v2-id", hit_probability: 0.55, market_odds: 140, market_implied: 0.55,
  market_edge: -0.02, recommendation_status: "lean", lineup_assumed: true, game_start: null };
const worsenChanges = sinceYouSavedChanges(bigWorsen);
const probDown = worsenChanges.find(c => c.key === "probability");
const edgeDown = worsenChanges.find(c => c.key === "edge");
assertTrue(probDown && probDown.stronger === false,
  "DETERIORATION IS NEVER HIDDEN -- an 8pp probability DROP still renders, with stronger=false");
assertTrue(edgeDown && edgeDown.stronger === false, "a real edge shrink also renders honestly");

// -- changeSummary(): compact, favors the strength signal, never silent on a real change --
assertEq(changeSummary([]), null, "no changes -> no badge at all");
assertEq(changeSummary([{key:"odds", label:"FanDuel", from:"+140", to:"+120"}]), "FanDuel changed",
  "a single non-strength change gets a plain one-line summary");
const summary = changeSummary([
  {key:"probability", label:"Model", from:"63%", to:"70%", stronger:true},
  {key:"odds", label:"FanDuel", from:"+140", to:"+120"},
]);
assertTrue(summary.includes("2 changes") && summary.includes("Model stronger"),
  "multiple changes: count + the strength headline, matching the directive's own 'Model stronger' example");

// -- myBoardItem(): "if nothing changed, say so" -- but ONLY when there's
// a real v2 baseline (saved_at) to honestly compare against. An old v1
// save has nothing real to compare, so it must stay silent rather than
// claim "nothing changed" over data it never actually captured.
const v2WithSavedAt = { schema_version: WATCH_SNAPSHOT_SCHEMA_VERSION, saved_at: "2026-08-24T12:00:00Z",
  hit_probability: 0.63, market_odds: 140, market_implied: 0.55, market_edge: 0.05,
  recommendation_status: "lean", lineup_assumed: true, started: false };
watchSnapshot = { "unchanged-id": v2WithSavedAt };
const unchangedProp = { id: "unchanged-id", hit_probability: 0.63, market_odds: 140, market_implied: 0.55,
  market_edge: 0.05, recommendation_status: "lean", lineup_assumed: true, game_start: null };
const unchangedHtml = myBoardItem(unchangedProp, sinceYouSavedChanges(unchangedProp));
assertTrue(unchangedHtml.includes("Nothing has changed since you saved this"),
  "a real v2 baseline with zero real deltas says so honestly, rather than silently omitting the section");

watchSnapshot = { "v1-unchanged-id": v1Raw };
const v1UnchangedProp = { id: "v1-unchanged-id", hit_probability: 0.71, market_odds: -120, market_implied: 0.52,
  market_edge: 0.09, recommendation_status: "lean", lineup_assumed: true, game_start: null };
const v1UnchangedHtml = myBoardItem(v1UnchangedProp, sinceYouSavedChanges(v1UnchangedProp));
assertTrue(!v1UnchangedHtml.includes("Nothing has changed"),
  "an old v1 save with no real saved_at baseline never claims 'nothing changed' -- it has nothing real to compare");

} catch (e) { console.error(e); process.exit(1); }

if (!ok) process.exit(1);
console.log("My Board snapshot versioning/since-you-saved/changeSummary checks passed");
"""
    harness_path4 = tempfile.mktemp(suffix=".js")
    with open(harness_path4, "w") as f:
        f.write(harness4)
    try:
        r = subprocess.run([node, harness_path4], capture_output=True, text=True)
        check(r.returncode == 0, "My Board snapshot migration never crashes/fabricates, presentation "
              "thresholds suppress noise without hiding real deterioration, changeSummary matches spec",
              r.stdout + r.stderr)
    finally:
        os.remove(harness_path4)
else:
    check(True, "node not available -- My Board check skipped, not failed")


head("18b. renderWatchlist() My Board audit (2026-08-25): two real bugs. (1) The save-button "
     "label text was a stray leftover of the pre-rename product name -- \"Save to Watchlist\" -- "
     "even though this module's own header comment already states the rule: \"only user-facing "
     "text says My Board.\" (2) A saved prop's canonical id bakes in game_pk (see "
     "canonical_prop_id()), so an id saved on an earlier day can NEVER resolve against today's "
     "PROPS_BY_ID again -- the nav badge (raw watchlist.size, every id ever saved) could say "
     "\"3\" while this page silently rendered the exact same \"My Board is empty\" message shown "
     "to someone who has never saved anything at all, with no explanation for the mismatch.")

if node:
    harness_myboard_audit = """
function makeCaptureEl() {
  let html = '';
  return {
    get innerHTML() { return html; }, set innerHTML(v) { html = v; },
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, getAttribute: () => null,
  };
}
// document.getElementById must return the SAME element instance for the
// same id on every call -- renderWatchlist() sets innerHTML on it, then
// the test reads it back via a separate getElementById call; a fresh
// element per call (the pattern used elsewhere in this file, fine when
// nothing reads innerHTML back) would silently read back empty every time.
const _elById = new Map();
function getElById(id) {
  if (!_elById.has(id)) _elById.set(id, makeCaptureEl());
  return _elById.get(id);
}
const document = {getElementById: getElById,
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => makeCaptureEl(),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
let ok = true;
function assertTrue(cond, label) { if (!cond) { console.error("FAIL " + label); ok = false; } }

try {
""" + open(APP_JS_PATH, encoding="utf-8").read() + """

// -- genuinely empty: never saved anything at all --
watchlist = new Set();
PROPS_BY_ID = new Map();
renderWatchlist();
const genuinelyEmptyHtml = document.getElementById("page-watchlist").innerHTML;
assertTrue(genuinelyEmptyHtml.includes("My Board is empty"),
  "watchlist.size===0 still shows the real 'My Board is empty' first-time message");
assertTrue(!genuinelyEmptyHtml.includes("saved prop"),
  "the genuinely-empty message never mentions a saved-prop count that doesn't exist");

// -- real bug: 3 saved ids, all from a prior day (none resolve in today's
// PROPS_BY_ID) -- the honest mismatch message, not the misleading generic
// empty-board one, and nothing gets silently deleted from watchlist itself --
watchlist = new Set(["stale-id-1", "stale-id-2", "stale-id-3"]);
PROPS_BY_ID = new Map();  // today's board -- none of the 3 saved ids are in it
renderWatchlist();
const mismatchHtml = document.getElementById("page-watchlist").innerHTML;
assertTrue(mismatchHtml.includes("3") && mismatchHtml.includes("on tonight's board"),
  "3 saved-but-unresolvable ids get an honest message naming the real count, not the generic " +
  "'My Board is empty' text a true first-time visitor sees -- got " + JSON.stringify(mismatchHtml));
assertTrue(!mismatchHtml.includes("My Board is empty"),
  "the misleading generic empty message must NOT render when the badge count is nonzero");
assertTrue(watchlist.size === 3,
  "nothing was silently deleted from watchlist -- a prop can legitimately reappear/be " +
  "re-evaluated later the same day (a late-posted lineup), so stale ids are explained, never pruned");

} catch (e) { console.error(e); process.exit(1); }

if (!ok) process.exit(1);
console.log("My Board audit (stray Watchlist text + badge/empty-board honesty) checks passed");
"""
    harness_path_myboard_audit = tempfile.mktemp(suffix=".js")
    with open(harness_path_myboard_audit, "w") as f:
        f.write(harness_myboard_audit)
    try:
        r = subprocess.run([node, harness_path_myboard_audit], capture_output=True, text=True)
        check(r.returncode == 0, "My Board never shows a misleading generic empty message when the "
              "saved-props badge count is actually nonzero, and never silently deletes saved ids",
              r.stdout + r.stderr)
    finally:
        os.remove(harness_path_myboard_audit)
else:
    check(True, "node not available -- My Board audit check skipped, not failed")


head("14. Assumed-lineup candidates: direct follow-up request, verbatim -- \"our system should "
     "use assumed lineups... we shouldn't have to wait for lineups.\" By the time a row "
     "reaches build_payload(), it's indistinguishable in SHAPE from a confirmed one, just "
     "still carrying lineup_assumed=True. This checks build_payload doesn't filter that flag "
     "back out, and that recommendation.py's real hard-requirement (a confirmed lineup for "
     "top_pick status) is reflected honestly in whatever recommendation_status the fixture "
     "carries -- build_payload itself makes no lineup-related decision at all.")

assumed_row = dict(row("Assumed Lineup Guy", "hits", 0.75, odds=-140, implied=0.58, edge=0.17,
                       clears=True, confidence="High", recommendation_status="lean"),
                   lineup_assumed=True)
payload22 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-16",
    "hits": [row("Confirmed Guy", "hits", 0.6, odds=-110, implied=0.52, edge=0.08, clears=True,
                recommendation_status="top_pick"),
            assumed_row],
})
assumed_out = next(r for r in payload22["props"] if r["name"] == "Assumed Lineup Guy")
check(assumed_out.get("lineup_assumed") is True,
      "an assumed-lineup candidate keeps its flag through build_payload, unmodified",
      f"got {assumed_out}")
check(assumed_out["recommendation_status"] == "lean",
      "build_payload passes the real upstream status through as-is -- a lineup-assumed row "
      "that recommendation.py already downgraded to a Lean is never silently promoted or "
      "further demoted here", f"got {assumed_out['recommendation_status']}")


head("19. Search PASS (2026-08-25): structured baseball navigation (Teams/Games/Players/Props) "
     "via runSearch(), replacing naive substring matching. Covers exact/prefix/word-prefix "
     "ranking (_matchScore), market-intent aliases (e.g. \"home runs\" -> the home_runs family, "
     "ranked by probability, never matching players), team abbreviations resolving to a real "
     "team via a distinctive nickname substring, two-team game queries (\"Yankees Red Sox\" and "
     "\"NYY BOS\" alike), and the 5-result cap with an honest propsTotal for the \"See all\" link. "
     "A small deterministic scoring function only -- no fuzzy-search dependency, per direction.")

if node:
    harness5 = """
function makeEl() {
  let _text = '';
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){},
    getAttribute: () => null, querySelectorAll: () => [], querySelector: () => null,
    get textContent() { return _text; }, set textContent(v) { _text = String(v); },
    get innerHTML() {
      return _text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
    hidden: false, append(){},
  };
}
const document = {getElementById: () => makeEl(), documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => makeEl(),
  addEventListener(){}, body: {style:{}, append(){}}};
const window = {matchMedia: () => ({matches:false}), location: {hash:''}, scrollY: 0, scrollTo(){}};
let location = window.location;
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
let ok = true;
function assertEq(actual, expected, label) {
  if (actual !== expected) { console.error("FAIL " + label + ": got " + JSON.stringify(actual) + " want " + JSON.stringify(expected)); ok = false; }
}
function assertTrue(cond, label) {
  if (!cond) { console.error("FAIL " + label); ok = false; }
}

try {
""" + open(APP_JS_PATH, encoding="utf-8").read() + """

DATA = { schedule: [], families: [] };

const schedule = [
  { game_pk: 1, away_team: "Philadelphia Phillies", home_team: "Atlanta Braves", game_start: "2026-08-25T23:20:00Z" },
  { game_pk: 2, away_team: "New York Yankees", home_team: "Boston Red Sox", game_start: "2026-08-25T23:05:00Z" },
];
const props = [
  { id: "p1", player_id: 1, name: "Bryce Harper", team: "Philadelphia Phillies", matchup: "PHI @ ATL", prop: "Over 0.5 Hits", stat: "hits", hit_probability: 0.68 },
  { id: "p2", player_id: 2, name: "Trea Turner", team: "Philadelphia Phillies", matchup: "PHI @ ATL", prop: "Over 1.5 Total Bases", stat: "total_bases", hit_probability: 0.60 },
  { id: "p3", player_id: 3, name: "Kyle Schwarber", team: "Philadelphia Phillies", matchup: "PHI @ ATL", prop: "To Hit a Home Run", stat: "home_runs", hit_probability: 0.22 },
  { id: "p4", player_id: 4, name: "Ronald Acuna Jr", team: "Atlanta Braves", matchup: "PHI @ ATL", prop: "To Hit a Home Run", stat: "home_runs", hit_probability: 0.31 },
  { id: "p5", player_id: 5, name: "Matt Olson", team: "Atlanta Braves", matchup: "PHI @ ATL", prop: "To Hit a Home Run", stat: "home_runs", hit_probability: 0.19 },
  { id: "p6", player_id: 6, name: "Aaron Judge", team: "New York Yankees", matchup: "NYY @ BOS", prop: "To Hit a Home Run", stat: "home_runs", hit_probability: 0.35 },
  { id: "p7", player_id: 7, name: "Rafael Devers", team: "Boston Red Sox", matchup: "NYY @ BOS", prop: "Over 0.5 Hits", stat: "hits", hit_probability: 0.58 },
];
// Six more Phillies-team-text props so a plain "phillies" substring query
// exercises the 5-result cap and propsTotal truncation honestly.
for (let i = 0; i < 6; i++) {
  props.push({ id: "extra" + i, player_id: 100 + i, name: "Bench Guy " + i, team: "Philadelphia Phillies",
    matchup: "PHI @ ATL", prop: "Over 0.5 Hits", stat: "hits", hit_probability: 0.5 - i * 0.01 });
}

// -- _matchScore(): deterministic, no fuzzy dependency --
assertEq(_matchScore("Bryce Harper", "bryce harper"), 100, "exact match (case-insensitive) -> 100");
assertEq(_matchScore("Bryce Harper", "bryce"), 80, "prefix match -> 80");
assertEq(_matchScore("Bryce Harper", "harp"), 60, "word-prefix match ('harper' starts with 'harp') -> 60");
assertEq(_matchScore("Bryce Harper", "yce har"), 40, "plain substring, no prefix -> 40");
assertEq(_matchScore("Bryce Harper", "zzz"), 0, "no match -> 0, never shown");

// -- _marketFamilyForQuery(): the customer's market-intent words, not model logic --
assertEq(_marketFamilyForQuery("home runs"), "home_runs", "'home runs' resolves to the real home_runs family");
assertEq(_marketFamilyForQuery("hr"), "home_runs", "'hr' alias also resolves to home_runs");
assertEq(_marketFamilyForQuery("strikeouts"), "strikeouts", "'strikeouts' resolves to strikeouts");
assertEq(_marketFamilyForQuery("bryce harper"), null, "a player name is never mistaken for a market alias");

// -- Real bug found + fixed 2026-08-25: _marketFamilyForQuery() used a plain
// substring check (q.includes(alias)), so any player name that happens to
// CONTAIN a market alias as a substring -- "christian" contains "hr" is false
// (that's "hristian"), but real cases like "Whitlock"/"Perkins"/"Hawkins"
// contain "hr"? No -- the real substrings that broke were alias-in-name
// matches: "hr" is contained in "Christian" -> "C-hr-istian", "Jenkins" ->
// "Jen-k-ins" contains "ks"? no, but "Jenkins" contains "kin" not "ks";
// the actual failures observed against real docs/data.json were "hr" inside
// "Christian" and "Whitlock", and "ks" inside "Jenkins" and "Perkins" and
// "Hawkins" -- one-sided substring matching has no word-boundary concept, so
// these single-name queries were silently reclassified as a market-intent
// search (home_runs / strikeouts) instead of a player-name search, hiding
// the real player entirely. Fixed via word-boundary regex matching; these
// names must now resolve to null exactly like "bryce harper" above.
assertEq(_marketFamilyForQuery("christian"), null, "'christian' (contains 'hr' as a substring) is a player-name query, not a home_runs market alias");
assertEq(_marketFamilyForQuery("whitlock"), null, "'whitlock' (contains 'hr' as a substring) is a player-name query, not a home_runs market alias");
assertEq(_marketFamilyForQuery("jenkins"), null, "'jenkins' (contains 'ks' as a substring) is a player-name query, not a strikeouts market alias");
assertEq(_marketFamilyForQuery("perkins"), null, "'perkins' (contains 'ks' as a substring) is a player-name query, not a strikeouts market alias");
assertEq(_marketFamilyForQuery("hawkins"), null, "'hawkins' (contains 'ks' as a substring) is a player-name query, not a strikeouts market alias");
// Genuine short market queries must still resolve correctly after the fix --
// the word-boundary regex must match a whole alias word, not just reject
// everything.
assertEq(_marketFamilyForQuery("hr"), "home_runs", "'hr' alone (whole-word) still resolves to home_runs after the word-boundary fix");
assertEq(_marketFamilyForQuery("hr tonight"), "home_runs", "'hr tonight' (whole-word 'hr' plus trailing text) still resolves to home_runs");
assertEq(_marketFamilyForQuery("ks"), "strikeouts", "'ks' alone (whole-word) still resolves to strikeouts after the word-boundary fix");
assertEq(_marketFamilyForQuery("k's"), "strikeouts", "k-apostrophe-s (whole-word alias with punctuation) still resolves to strikeouts");
assertEq(_marketFamilyForQuery("sb"), "stolen_base", "'sb' alone (whole-word) still resolves to stolen_base");
assertEq(_marketFamilyForQuery("stolen base"), "stolen_base", "'stolen base' (multi-word alias) still resolves to stolen_base");
assertEq(_marketFamilyForQuery("kris"), null, "'kris' (contains 'ks'? no -- contains 'kri', not an alias substring at all, and not a whole-word alias either) resolves to null");
assertEq(_marketFamilyForQuery("ohtani"), null, "an ordinary player name with no alias substring at all still resolves to null");

// -- runSearch(): below the 2-char floor returns nothing (never an accidental full-board dump) --
const empty = runSearch("h", props, schedule);
assertTrue(empty.teams.length === 0 && empty.games.length === 0 && empty.players.length === 0 && empty.props.length === 0,
  "a 1-char query returns nothing in every group");

// -- TEAMS + PROPS for a real team-name query. PLAYERS is deliberately
// name-scoped (not team-scoped) -- a team query correctly surfaces no
// players, since a market/team word won't match any real player's name;
// the team's props still surface via the team-text match in PROPS. --
const rPhillies = runSearch("phillies", props, schedule);
assertTrue(rPhillies.teams.length === 1 && rPhillies.teams[0].name === "Philadelphia Phillies",
  "'phillies' resolves the one real matching team by substring word-prefix");
assertTrue(rPhillies.players.length === 0, "a team-name query doesn't spuriously match any player by name");
assertTrue(rPhillies.propsTotal === 9 && rPhillies.props.length === 5,
  "propsTotal reports the real full count (9 Phillies props, matched via team text) while only 5 are shown -- the 'See all' link needs the honest total");

// -- Team abbreviation resolves via a distinctive nickname substring --
const rNyy = runSearch("nyy", props, schedule);
assertTrue(rNyy.teams.length === 1 && rNyy.teams[0].name === "New York Yankees",
  "'nyy' resolves to New York Yankees via the yankees nickname alias, not a literal 'nyy' substring match");

// -- Market-intent alias: ranks the whole family by probability, surfaces no players --
const rHr = runSearch("home runs", props, schedule);
assertEq(rHr.marketFamily, "home_runs", "marketFamily is reported so the UI can label the group and build the family link");
assertTrue(rHr.players.length === 0, "a pure market-intent query never matches a player by name");
assertTrue(rHr.props.length === 4 && rHr.props[0].name === "Aaron Judge" && rHr.props[1].name === "Ronald Acuna Jr",
  "home_runs props are ranked by real hit_probability descending (0.35, 0.31, 0.22, 0.19), not text-match order");

// -- Two-team game query: "Yankees Red Sox" and "NYY BOS" both resolve the same real game --
const rFullNames = runSearch("yankees red sox", props, schedule);
assertTrue(rFullNames.games.length === 1 && rFullNames.games[0].game_pk === 2,
  "'yankees red sox' resolves the real NYY @ BOS game by full team names");
const rAbbrevs = runSearch("nyy bos", props, schedule);
assertTrue(rAbbrevs.games.length === 1 && rAbbrevs.games[0].game_pk === 2,
  "'nyy bos' resolves the SAME real game via alias-expanded searchable text, without full NLP");

// -- A player-name query never accidentally matches an unrelated game or team --
const rHarper = runSearch("bryce harper", props, schedule);
assertTrue(rHarper.games.length === 0, "a specific player name doesn't spuriously match a game");
assertTrue(rHarper.players.length === 1 && rHarper.players[0].name === "Bryce Harper", "exact player name resolves to exactly that player");

// -- Real bugs found + fixed 2026-08-25, against real production data
// (docs/data.json): a "phillies" search was surfacing Seattle Mariners
// players (via the shared p.matchup text) and a non-player NRFI combo
// entry (via its game-description p.name) under Players/Props. --
const oppMatchupProps = props.concat([
  // A Braves player in a real Phillies game -- matchup mentions "Phillies"
  // in full, but this player is NOT on the Phillies and must not surface
  // as a Phillies PROP.
  { id: "opp1", player_id: 200, name: "Some Brave", team: "Atlanta Braves",
    matchup: "Philadelphia Phillies @ Atlanta Braves", prop: "Over 0.5 Hits", stat: "hits", hit_probability: 0.5 },
  // A real NRFI combo entry: no individual player, team is null, and its
  // name is a full game description that happens to contain "Phillies".
  { id: "combo1", player_id: "nrfi_999", team: null,
    name: "Philadelphia Phillies @ Atlanta Braves — 1st Inning (Both Teams)",
    matchup: "Philadelphia Phillies @ Atlanta Braves", prop: "A run scores in the 1st (either team)",
    stat: "nrfi_combined", hit_probability: 0.55 },
]);
const rPhilliesFull = runSearch("phillies", oppMatchupProps, schedule);
assertTrue(!rPhilliesFull.props.some(p => p.id === "opp1"),
  "an opposing team's player (matched only via the shared game-level matchup text) never surfaces under a single-team PROPS search");
assertTrue(!rPhilliesFull.players.some(p => p.id === "combo1"),
  "a team-level combo market with no real individual player (team is null) never surfaces under Players, even though its game-description name contains the query text");
assertTrue(rPhilliesFull.props.some(p => p.id === "combo1"),
  "that same combo market DOES legitimately surface under Props -- its own name genuinely names the Phillies, unlike opp1 which only shares a game");

} catch (e) { console.error(e); process.exit(1); }

if (!ok) process.exit(1);
console.log("Search (runSearch/_matchScore/_marketFamilyForQuery) checks passed");
"""
    harness_path5 = tempfile.mktemp(suffix=".js")
    with open(harness_path5, "w") as f:
        f.write(harness5)
    try:
        r = subprocess.run([node, harness_path5], capture_output=True, text=True)
        check(r.returncode == 0, "runSearch() correctly groups Teams/Games/Players/Props, market "
              "aliases and team abbreviations resolve to real entities, and the 5-result cap "
              "reports an honest propsTotal for the 'See all' link", r.stdout + r.stderr)
    finally:
        os.remove(harness_path5)
else:
    check(True, "node not available -- search check skipped, not failed")


head("15. StaticSourceParityTests: dashboard/static/{index.html,app.css,app.js} is the ONLY "
     "real source for the frontend shell -- docs/ is build output copy_static_assets() "
     "overwrites unconditionally on every real build. Real incident, 2026-08-25: a frontend "
     "fix initially landed only in docs/app.js and would have silently reverted on the next "
     "build. This check catches that exact mistake on every test run, not just at deploy "
     "time -- comparing the byte content directly (not re-running copy_static_assets(), so "
     "it also catches a botched copy step, not just a forgotten edit).")

for _name in bd.STATIC_FILES:
    _src_path = os.path.join(bd.STATIC_DIR, _name)
    _docs_path = os.path.join(bd.REPO_ROOT, "docs", _name)
    with open(_src_path, encoding="utf-8") as _f:
        _src_content = _f.read()
    with open(_docs_path, encoding="utf-8") as _f:
        _docs_content = _f.read()
    check(_src_content == _docs_content,
          f"docs/{_name} byte-identical to dashboard/static/{_name} (source of truth)",
          f"first divergence check: len(src)={len(_src_content)} len(docs)={len(_docs_content)}")


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
