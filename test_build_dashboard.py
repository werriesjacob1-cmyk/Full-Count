#!/usr/bin/env python3
"""test_build_dashboard.py — coverage for dashboard/build_dashboard.py's pure
functions (build_payload, render_html). Does NOT test run_live_fetch() --
that's a live re-run of generate_picks.py's real scoring pass (network calls
to MLB/Statcast/FanDuel), out of scope for a fast unit test the same way the
rest of this project never unit-tests a live fetcher directly. What's tested
here is the part that's actually pure logic: given a scored-candidates dict
shaped like run_live_fetch()'s real output, does the payload it builds for
the page (tabs, Top Picks ranking, the home_runs/moonshot dedup) come out
right, and does the HTML it renders actually embed that payload.

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


def row(name, stat, prob, needs=1, value=0.5, odds=None, implied=None, edge=None,
       clears=None, confidence="Medium", ptype="batter"):
    return {
        "type": ptype, "name": name, "team": "Team", "matchup": "A @ B", "side": None,
        "prop": f"Over {value} {stat}", "projection": {"stat": stat, "value": value, "needs": needs},
        "lean": None, "score": 65.0, "confidence": confidence,
        "hit_probability": prob, "market_odds": odds, "market_implied": implied,
        "market_edge": edge, "price_clears": clears,
        "reliability": "B", "sample_n": 80, "why": [], "watchouts": [],
        "base_rate": None, "lift": None,
    }


head("1. Top Picks ranks by market_edge among price_clears==True only, capped at 10")

result = {
    "generated_at": "2026-08-12T20:00:00", "date": "2026-08-12",
    "hits": [
        row("Big Edge Clears", "hits", 0.70, odds=-150, implied=0.55, edge=0.15, clears=True),
        row("Small Edge Clears", "hits", 0.60, odds=-120, implied=0.58, edge=0.02, clears=True),
        row("Doesnt Clear", "hits", 0.90, odds=-800, implied=0.85, edge=0.05, clears=False),
        row("No Line At All", "hits", 0.55, odds=None, implied=None, edge=None, clears=None),
    ],
    "stolen_base": [
        row("Mid Edge Clears", "stolen_base", 0.35, odds=200, implied=0.28, edge=0.07, clears=True),
    ],
}
payload = bd.build_payload(result)
tp = payload["data"]["top_picks"]
check(len(tp) == 3, "only the 3 genuinely price_clears==True rows make Top Picks",
      f"got {[r['name'] for r in tp]}")
check([r["name"] for r in tp] == ["Big Edge Clears", "Mid Edge Clears", "Small Edge Clears"],
      "Top Picks is ordered by market_edge descending, not by probability",
      f"got {[r['name'] for r in tp]}")
check("Doesnt Clear" not in [r["name"] for r in tp],
      "a big-probability pick that fails price_clears is excluded from Top Picks")
check("No Line At All" not in [r["name"] for r in tp],
      "an unpriced candidate (price_clears is None, not True) is excluded from Top Picks")


head("2. Top Picks caps at 10 even when more than 10 clear")

many = {"generated_at": "x", "date": "2026-08-12",
       "hits": [row(f"Clearer {i}", "hits", 0.6, odds=-110, implied=0.5,
                    edge=0.10 - i * 0.001, clears=True) for i in range(15)]}
payload2 = bd.build_payload(many)
check(len(payload2["data"]["top_picks"]) == 10,
      "Top Picks never exceeds 10 even with 15 real qualifying candidates",
      f"got {len(payload2['data']['top_picks'])}")
check(payload2["data"]["top_picks"][0]["name"] == "Clearer 0",
      "still the genuinely highest-edge ones that make the cut")


head("3. home_runs is dropped as a duplicate of moonshot (same underlying field, per audit)")

dup = {
    "generated_at": "x", "date": "2026-08-12",
    "moonshot": [row("Slugger", "home_runs", 0.20, needs=1, value=1)],
    "home_runs": [row("Slugger", "home_runs", 0.20, needs=1, value=1)],
}
payload3 = bd.build_payload(dup)
check("home_runs" not in payload3["tabs_order"],
      "the raw 'home_runs' key never becomes its own tab",
      f"got {payload3['tabs_order']}")
check("moonshot" in payload3["tabs_order"],
      "moonshot is kept as the one real Home Runs tab")
check(payload3["labels"]["moonshot"] == "Home Runs",
      "moonshot's display label is still the human name, not the internal key")


head("4. tabs_order always starts with the three fixed tabs, then only categories with real rows")

payload4 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-12",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "triples": [],  # present in the data but empty -- must not become a tab
})
check(payload4["tabs_order"][:4] == ["top_picks", "schedule", "streaks", "all"],
      "top_picks, schedule, streaks, and all are always first, in that order",
      f"got {payload4['tabs_order'][:4]}")
check("triples" not in payload4["tabs_order"],
      "a category with zero real rows never becomes an empty tab")
check("hits" in payload4["tabs_order"],
      "a category with real rows does become a tab")


head("5. estimated_odds is computed for every priced row via the real prop_probability.american_odds")

import prop_probability as pp
payload5 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-12",
    "hits": [row("A", "hits", 0.75, odds=-300, implied=0.7, edge=0.05, clears=True)],
})
a_row = payload5["data"]["hits"][0]
check(a_row["estimated_odds"] == pp.american_odds(0.75),
      "estimated_odds matches the real american_odds() function directly, not a reimplementation",
      f"got {a_row['estimated_odds']}, expected {pp.american_odds(0.75)}")


head("6. render_html embeds the payload and every font placeholder is substituted")

fonts = {"archivo": "QUJD", "plexsans": "REVG", "plexmono500": "R0hJ", "plexmono600": "SktM"}
html = bd.render_html(payload5, fonts)
check("{payload_json}" not in html and "{archivo}" not in html,
      "no leftover template placeholders survive into the rendered page")
check("QUJD" in html and "REVG" in html and "R0hJ" in html and "SktM" in html,
      "all four font payloads actually landed in the output")
embedded = json.loads(html.split("const PAYLOAD = ", 1)[1].split(";\n", 1)[0])
check(embedded["data"]["hits"][0]["name"] == "A",
      "the embedded PAYLOAD is genuinely the same data build_payload produced, not a stale copy")
check(html.count("<style>") == 1 and html.count("</style>") == 1,
      "style block is well-formed (exactly one open/close)")
check(html.count("<script>") == 1 and html.count("</script>") == 1,
      "script block is well-formed (exactly one open/close)")

import shutil
import subprocess
node = shutil.which("node")
if node:
    js = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    js_path = tempfile.mktemp(suffix=".js")
    with open(js_path, "w") as f:
        f.write(js)
    try:
        r = subprocess.run([node, "--check", js_path], capture_output=True, text=True)
        check(r.returncode == 0, "the embedded <script> is syntactically valid JavaScript "
              "-- Python's .format() only checks brace-escaping, never actual JS syntax, so a "
              "broken function body here would only ever surface as a silent, unreported "
              "runtime failure in a real browser", r.stderr)
    finally:
        os.remove(js_path)
else:
    check(True, "node not available in this environment -- JS syntax check skipped, not failed")

head("6b. REASON_RULES/humanizeReason actually translate real jargon strings, not just "
     "parse without crashing -- direct request: \"refine the notes... clean understandable "
     "language.\" These are verbatim strings generate_picks.py emits live (checked against "
     "2026-08-14's real board) that previously fell straight through untranslated.")

if node:
    js = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness = """
const document = {getElementById: () => ({addEventListener(){}, textContent:'', dataset:{}}),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => ({})};
const window = {matchMedia: () => ({matches:false}), location: {reload(){}}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const PAYLOAD = {generated_at:"x", date:"x", tabs_order:[], labels:{},
  data:{all:[], top_picks:[]}, track_record:null, suggested_parlay:null};
const setInterval = () => {};
try { """ + js + """ } catch (e) {}
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


head("7. priced candidates rank ahead of unpriced ones within a tab, and in All Props -- "
     "even when the unpriced one has the higher raw probability")

result7 = {
    "generated_at": "x", "date": "2026-08-12",
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
tab_names = [r["name"] for r in payload7["data"]["pitcher_outs"]]
check(tab_names == ["Priced Lower Prob", "No Line High Prob"],
      "within a tab, a real-priced candidate ranks first even against a higher-probability "
      "unpriced one", f"got {tab_names}")
all_names = [r["name"] for r in payload7["data"]["all"]]
check(all_names == ["Priced Lower Prob", "No Line High Prob"],
      "the same priced-first rule applies to the All Props tab", f"got {all_names}")


head("8. load_track_record reads the real main_hit_rate, not the blended overall one, "
     "and degrades to None honestly rather than a fabricated 0%")

with tempfile.TemporaryDirectory() as td:
    hist_path = os.path.join(td, "history.json")
    with open(hist_path, "w") as f:
        json.dump({
            "overall_hit_rate": 0.452, "main_hit_rate": 0.553,
            "last_14_days_hit_rate": 0.452,
            "by_category_totals": {"main": {"hits": 26, "misses": 21, "ungraded": 0}},
        }, f)
    tr = bd.load_track_record(hist_path)
    check(tr["main_hit_rate"] == 0.553,
          "reads main_hit_rate, not overall_hit_rate", f"got {tr}")
    check(tr["main_n"] == 47, "main_n is hits+misses from by_category_totals.main", f"got {tr}")

    empty_path = os.path.join(td, "empty.json")
    with open(empty_path, "w") as f:
        json.dump({"main_hit_rate": None, "by_category_totals": {}}, f)
    check(bd.load_track_record(empty_path) is None,
          "no graded main-board picks yet returns None, not a fabricated 0% record")

    check(bd.load_track_record(os.path.join(td, "does_not_exist.json")) is None,
          "a missing history.json returns None rather than crashing the build")


head("9. suggested parlay: real correlation-screened legs via the actual parlay_builder.py "
     "engine, honest None when fewer than 2 real legs exist")

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

# REAL BUG, found live 2026-08-12 running the actual pipeline end to end:
# quality_control() rejects candidates on lineup/rain/opener grounds, not on
# whether a probability could be computed, so real pools reaching this
# function routinely contain hit_probability=None candidates -- and
# parlay_builder's risk_band comparison crashed on the first one, silently
# no-oping the whole feature via the caller's except every single real run.
none_prob_pool = three_legs + [
    parlay_candidate("D", "T7", "T7 @ T8", 4, 104, "Over 0.5 RBIs", "rbis", None, -110),
]
sp_with_none = bd._build_suggested_parlay(none_prob_pool)
check(sp_with_none is not None,
      "a pool containing a real hit_probability=None candidate doesn't crash the whole "
      "feature -- it's filtered out the same way load_todays_pool() already does for "
      "every other caller of this engine", f"got {sp_with_none}")

# REAL BUG, found live 2026-08-14 from a direct report: "why are we
# recommending a parlay that has props that are not available? Can't bet
# on that at all." A candidate can carry a real model hit_probability
# with no FanDuel line posted for it yet (market_odds=None) -- distinct
# from the hit_probability=None bug above, and not caught by it.
unpriced_leg = parlay_candidate("E", "T9", "T9 @ T10", 5, 105, "Over 0.5 Doubles", "doubles",
                                0.70, None)
sp_unpriced_excluded = bd._build_suggested_parlay([three_legs[0], three_legs[1], unpriced_leg])
if sp_unpriced_excluded:
    names = {l["name"] for l in sp_unpriced_excluded["legs"]}
    check("E" not in names, "a real-probability-but-unpriced (market_odds=None) candidate "
          "never becomes a parlay leg -- it's not a bet anyone could place", f"got legs={names}")
if sp_with_none:
    check("D" not in [l["name"] for l in sp_with_none["legs"]],
          "the None-probability candidate itself never becomes a leg")


head("10. build_payload doesn't crash on a result dict shaped like run_live_fetch()'s "
     "real output -- suggested_parlay included as a top-level key, not a stat category")

result10 = {
    "generated_at": "x", "date": "2026-08-12",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "suggested_parlay": None,
}
payload10 = bd.build_payload(result10)
check("suggested_parlay" not in payload10["tabs_order"],
      "suggested_parlay never becomes its own tab", f"got {payload10['tabs_order']}")
check(payload10["suggested_parlay"] is None,
      "a None suggested_parlay (the honest case when the engine can't build one) passes "
      "through cleanly")

result10b = dict(result10)
result10b["suggested_parlay"] = {"legs": [{"name": "X"}], "combined_american_odds": 150}
payload10b = bd.build_payload(result10b)
check(payload10b["suggested_parlay"] == result10b["suggested_parlay"],
      "a real suggested_parlay dict passes through to the payload unchanged, not "
      "misread as a list of candidate rows", f"got {payload10b['suggested_parlay']}")


head("11. _game_schedule -- direct request: \"as games start I want those "
     "props removed\". Parses MLB's schedule endpoint into "
     "{game_pk: {started, start}}, non-fatal on failure.")

import unittest.mock as mock
import mlb_daily as m

SCHEDULE_RESP = {"dates": [{"games": [
    {"gamePk": 700001, "gameDate": "2026-08-14T23:05:00Z",
     "status": {"abstractGameState": "Preview"}},
    {"gamePk": 700002, "gameDate": "2026-08-14T22:10:00Z",
     "status": {"abstractGameState": "Live"}},
    {"gamePk": 700003, "gameDate": "2026-08-14T20:00:00Z",
     "status": {"abstractGameState": "Final"}},
]}]}

with mock.patch.object(m, "retry_get") as mock_get:
    mock_get.return_value.json.return_value = SCHEDULE_RESP
    mock_get.return_value.raise_for_status = lambda: None
    sched = bd._game_schedule("2026-08-14")

check(sched[700001]["started"] is False, "a Preview game is NOT started")
check(sched[700002]["started"] is True, "a Live game IS started")
check(sched[700003]["started"] is True, "a Final game IS started (started != still playing)")
check(sched[700001]["start"] == "2026-08-14T23:05:00Z",
      "the real scheduled gameDate passes through for client-side pruning",
      f"got {sched[700001]['start']}")

head("12. _game_schedule fails soft (empty dict, not an exception) when the fetch itself breaks")

with mock.patch.object(m, "retry_get", side_effect=Exception("network down")):
    sched_fail = bd._game_schedule("2026-08-14")
check(sched_fail == {}, "a network failure returns {} rather than raising -- must never take "
      "down the whole dashboard build over one schedule fetch")

head("13. live grading (pruneStartedGames/mergePriceUpdate): direct request, \"for the top "
     "picks, them to show when it's cashed... make the pick yellow when the game is "
     "happening... green if it cashes, red if it doesn't.\" A top pick must survive its own "
     "game start (in every tab it appears in) and keep its grade through a price-merge cycle, "
     "even once price_clears flips false because FanDuel's line closed.")

if node:
    past = "2020-01-01T00:00:00+00:00"
    future = "2099-01-01T00:00:00+00:00"
    result13 = {
        "generated_at": "x", "date": "2026-08-14",
        "hits": [
            dict(row("Started Pick", "hits", 0.6, odds=-110, implied=0.52, edge=0.08, clears=True),
                 game_pk=1, game_start=past),
            dict(row("Not Started Other", "hits", 0.5, odds=100, implied=0.5, edge=-0.02, clears=False),
                 game_pk=2, game_start=future),
            dict(row("Started Not Top", "hits", 0.4, odds=100, implied=0.5, edge=-0.05, clears=False),
                 game_pk=3, game_start=past),
        ],
    }
    html13 = bd.render_html(bd.build_payload(result13), fonts)
    js13 = html13.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness13 = """
function stubEl() {
  return {addEventListener(){}, textContent:'', innerHTML:'', dataset:{}, style:{},
    setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){}};
}
const document = {getElementById: () => stubEl(),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => stubEl()};
const window = {matchMedia: () => ({matches:false}), location: {reload(){}}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
const requestAnimationFrame = (fn) => {};
""" + js13 + """
// PAYLOAD is a genuine top-level const declared by the script above (this
// harness does NOT wrap it in a try/catch block, unlike check 6b's --
// pruneStartedGames()/mergePriceUpdate() below need to read/mutate its
// REAL runtime state, not just call a pure string-in-string-out function
// the way check 6b's humanizeReason does, so PAYLOAD has to survive as a
// real reference, not get swallowed inside a block scope).
const P = PAYLOAD;

if (!P.data.top_picks.some(p => p.name === "Started Pick")) {
  console.error("FAIL: build_payload didn't put the sole price_clears=true row into top_picks "
    + "-- test setup is wrong, not the feature under test");
  process.exit(1);
}

// 1. pruneStartedGames() must NOT remove the started top pick from either
//    tab it's in, but MUST still remove the started-but-never-a-top-pick
//    row from "all" (unchanged behavior for ordinary prop browsing).
pruneStartedGames();
if (!P.data.top_picks.some(p => p.name === "Started Pick")) {
  console.error("FAIL: started top pick was pruned from top_picks"); process.exit(1);
}
if (!P.data.all.some(p => p.name === "Started Pick")) {
  console.error("FAIL: started top pick was pruned from all"); process.exit(1);
}
if (P.data.all.some(p => p.name === "Started Not Top")) {
  console.error("FAIL: a started prop that was never a top pick should still be pruned");
  process.exit(1);
}
if (!P.data.all.some(p => p.name === "Not Started Other")) {
  console.error("FAIL: a prop whose game hasn't started should never be pruned"); process.exit(1);
}

// 2. mergePriceUpdate(): the started top pick's price_clears flips to
//    false (FanDuel closed the line) and it's now graded "hit" -- it must
//    survive the top_picks rebuild anyway, and the grade must merge in.
const freshAll = P.data.all.map(p => Object.assign({}, p));
const startedFresh = freshAll.find(p => p.name === "Started Pick");
startedFresh.price_clears = false;
startedFresh.grade = "hit";
mergePriceUpdate(freshAll);
const survivor = P.data.top_picks.find(p => p.name === "Started Pick");
if (!survivor) {
  console.error("FAIL: started+graded top pick was dropped from top_picks by the price rebuild "
    + "once price_clears went false -- this is the exact bug that would make a cashed pick "
    + "vanish right as it turns green");
  process.exit(1);
}
if (survivor.grade !== "hit") {
  console.error("FAIL: grade field did not merge onto the top pick ('" + survivor.grade + "')");
  process.exit(1);
}
console.log("live grading: all checks passed");
"""
    harness_path13 = tempfile.mktemp(suffix=".js")
    with open(harness_path13, "w") as f:
        f.write(harness13)
    try:
        r13 = subprocess.run([node, harness_path13], capture_output=True, text=True)
        check(r13.returncode == 0, "a started top pick survives pruning in every tab, and keeps "
              "its live-graded 'hit' state through a price-refresh cycle even once price_clears "
              "goes false", r13.stdout + r13.stderr)
    finally:
        os.remove(harness_path13)
else:
    check(True, "node not available -- live-grading JS check skipped, not failed")

head("14. game schedule breakdown: direct request, \"I want people to be able to click on a "
     "game on the schedule, and get a breakdown of why X props might be best for A B C "
     "reasons. Think time, weather, etc.\" game_context passes through as the 'schedule' tab, "
     "not swept into the generic stat-category loop (its rows have no hit_probability, which "
     "would otherwise filter every game out silently).")

payload14 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-15",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "game_context": [
        {"game_pk": 1, "matchup": "Athletics @ Astros", "away_team": "Athletics",
         "home_team": "Astros", "away_sp": "X", "home_sp": "Y", "hp_ump": "Z",
         "game_start": "2026-08-15T23:05:00Z",
         "weather": {"dome": False, "temp": 80.0, "wind_mph": 5.0, "wind_effect": "neutral",
                    "park_hr_index": 50, "precip_prob": 0},
         "umpire": {"name": "Z", "k_pct": 0.22, "bb_pct": 0.08, "league_k_pct": 0.221,
                   "league_bb_pct": 0.085},
         "is_getaway": False, "is_opener": False,
         "picks": [{"name": "B", "prop": "Over 0.5 Hits", "hit_probability": 0.65,
                   "market_odds": -130, "price_clears": True, "why": "a real reason"}]},
    ],
})
check("schedule" in payload14["tabs_order"], "schedule is a real tab", f"got {payload14['tabs_order']}")
check(len(payload14["data"]["schedule"]) == 1,
      "the one real game passes through untouched, not filtered by the hit_probability check "
      "the generic stat-category loop applies to every other tab",
      f"got {payload14['data']['schedule']}")
check(payload14["data"]["schedule"][0]["matchup"] == "Athletics @ Astros",
      "the game's own fields survive intact")
check("schedule" not in payload14["labels"] or payload14["labels"]["schedule"] == "Schedule",
      "schedule has a real human label")

head("15. an empty game_context (no games left tonight) is a clean, honest empty tab, not "
     "a missing key or a crash")

payload15 = bd.build_payload({"generated_at": "x", "date": "2026-08-15", "game_context": []})
check(payload15["data"]["schedule"] == [], "an empty game_context list stays empty, doesn't crash")

payload15b = bd.build_payload({"generated_at": "x", "date": "2026-08-15"})
check(payload15b["data"]["schedule"] == [],
      "a result dict with no game_context key AT ALL (e.g. run_live_fetch's early return on a "
      "no-games night) still produces a valid empty schedule tab, not a KeyError")

head("16. the schedule tab renders real game cards client-side, with weather/umpire/picks "
     "detail behind the same click-to-expand interaction .pick rows already use")

if node:
    html16 = bd.render_html(bd.build_payload({
        "generated_at": "x", "date": "2026-08-15",
        "game_context": [
            {"game_pk": 1, "matchup": "Athletics @ Astros", "away_team": "Athletics",
             "home_team": "Astros", "away_sp": "Sean Murphy", "home_sp": "Framber Valdez",
             "hp_ump": "Angel Hernandez", "game_start": "2026-08-15T23:05:00Z",
             "weather": {"dome": False, "temp": 91.0, "wind_mph": 12.0, "wind_effect": "blowing_out",
                        "park_hr_index": 70, "precip_prob": 5},
             "umpire": {"name": "Angel Hernandez", "k_pct": 0.24, "bb_pct": 0.09,
                       "league_k_pct": 0.221, "league_bb_pct": 0.085},
             "is_getaway": False, "is_opener": True,
             "picks": [{"name": "Yordan Alvarez", "prop": "Over 1.5 Total Bases",
                       "hit_probability": 0.61, "market_odds": -135, "price_clears": True,
                       "why": None}]},
        ],
    }), fonts)
    js16 = html16.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness16 = """
function stubEl() {
  return {addEventListener(){}, textContent:'', innerHTML:'', dataset:{}, style:{},
    setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){}};
}
// esc() (build_dashboard.py) creates a real <div>, sets .textContent, and
// reads back .innerHTML to get browser-correct HTML escaping -- this stub
// element needs that exact round-trip to actually work, not just exist,
// or every esc()'d string (every player/team/prop name on the page)
// silently comes back empty in this harness, which would look like a
// real bug in the feature rather than an incomplete DOM stub.
function escapingEl() {
  let raw = "";
  const ESCAPE_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;"};
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){},
    get textContent() { return raw; },
    set textContent(v) { raw = v; },
    get innerHTML() { return raw.replace(/[&<>]/g, ch => ESCAPE_MAP[ch]); },
  };
}
const document = {getElementById: () => stubEl(),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => escapingEl()};
const window = {matchMedia: () => ({matches:false}), location: {reload(){}}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
const requestAnimationFrame = (fn) => {};
""" + js16 + """
const card = gameCard(PAYLOAD.data.schedule[0]);
if (!card.includes("Athletics @ Astros")) { console.error("FAIL: matchup missing from card"); process.exit(1); }
if (!card.includes("91")) { console.error("FAIL: real temperature missing from weather line"); process.exit(1); }
if (!card.includes("Angel Hernandez")) { console.error("FAIL: umpire name missing"); process.exit(1); }
if (!card.includes("Yordan Alvarez")) { console.error("FAIL: the real pick tied to this game is missing"); process.exit(1); }
if (!card.includes("class=\\"game-card\\"")) { console.error("FAIL: not rendered as a game-card"); process.exit(1); }
console.log("gameCard: all checks passed");
"""
    harness_path16 = tempfile.mktemp(suffix=".js")
    with open(harness_path16, "w") as f:
        f.write(harness16)
    try:
        r16 = subprocess.run([node, harness_path16], capture_output=True, text=True)
        check(r16.returncode == 0, "gameCard() renders the real matchup, weather, umpire, and "
              "the picks tied to that specific game_pk", r16.stdout + r16.stderr)
    finally:
        os.remove(harness_path16)
else:
    check(True, "node not available -- gameCard JS check skipped, not failed")

head("17. _compute_streaks: direct request, verbatim: \"STREAKS. Hits in a row, 2+ bases "
     "in a row, over X strikeouts in a row, any trends that are useful.\" Only computed "
     "for players who already have a real candidate on tonight's board, one game-log fetch "
     "per unique player_id, STREAK_MIN=3 filters out noise, sorted longest-first.")

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
    # A real 4-game hit streak AND a real 3-game RBI streak on the SAME
    # player -- both must surface as independent entries off one fetch.
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
names17 = [e["name"] for e in entries17]
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
multi17 = [e for e in entries17 if e["player_id"] == 5]
multi_stats17 = {e["streak_stat"]: e["streak"] for e in multi17}
check(multi_stats17 == {"hits": 4, "rbis": 3},
      "a player with real, independently-qualifying streaks in TWO different stats gets BOTH "
      "as separate entries (not deduped down to just the first-seen row) -- direct follow-up "
      "request: \"more substantial streak setting and more data\"", f"got {multi_stats17}")
five17 = next(e for e in entries17 if e["name"] == "Five Hit Streak")
four17 = next(e for e in entries17 if e["name"] == "Four TB Streak")
check(five17["streak"] == 5 and four17["streak"] == 4, "streak lengths are counted correctly",
      f"got {five17['streak']}, {four17['streak']}")
check(entries17[0]["name"] == "Five Hit Streak",
      "entries are sorted longest-streak-first", f"got {names17}")

head("18. _compute_streaks caps output at 15 entries, keeping the longest streaks")

ALL_PRICED_18 = [streak_row(f"Player {i}", i, "hits") for i in range(20)]
LOGS_18 = {i: [{"date": f"d{j}", "hits": 1, "total_bases": 1} for j in range(3 + i)] for i in range(20)}
with mock.patch.object(msrc, "batter_recent_game_log", side_effect=lambda pid, max_games=20: LOGS_18.get(pid, [])):
    entries18 = bd._compute_streaks(ALL_PRICED_18)
check(len(entries18) == 15, "output capped at 15 entries", f"got {len(entries18)}")
check(entries18[0]["name"] == "Player 19", "the longest streaks survive the cap, not the first-seen",
      f"got {[e['name'] for e in entries18[:3]]}")

head("19. streaks tab: build_payload wires _compute_streaks' output through as its own real "
     "tab (not swept into the generic stat-category loop -- its rows carry a 'streak' key, "
     "not a per-category hit_probability filter that would otherwise behave correctly by "
     "accident but for the wrong reason), ordered right after schedule.")

payload19 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-15",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "streaks": [dict(row("Streaky Batter", "hits", 0.6, odds=-110, implied=0.55, edge=0.05, clears=True),
                     player_id=9, streak=6, streak_stat="hits")],
})
check(payload19["tabs_order"][:4] == ["top_picks", "schedule", "streaks", "all"],
      "streaks sits right after schedule in the fixed tab prefix", f"got {payload19['tabs_order']}")
check(payload19["labels"].get("streaks") == "Streaks", "streaks has a real human label")
check(len(payload19["data"]["streaks"]) == 1 and payload19["data"]["streaks"][0]["streak"] == 6,
      "the real streak entry passes through with its streak length intact",
      f"got {payload19['data']['streaks']}")

payload19b = bd.build_payload({"generated_at": "x", "date": "2026-08-15"})
check(payload19b["data"]["streaks"] == [],
      "a result dict with no streaks key at all still produces a valid empty streaks tab, "
      "not a KeyError")

head("20. streak badge renders client-side on a pick row: direct request, \"any trends that "
     "are useful\" -- a streak entry must be visually flagged the same way a live/hit/miss "
     "grade badge already is, on the actual .pick markup, not just present in the data.")

if node:
    html20 = bd.render_html(bd.build_payload({
        "generated_at": "x", "date": "2026-08-15",
        "streaks": [dict(row("Ronald Acuna", "hits", 0.65, odds=-120, implied=0.55, edge=0.1,
                             clears=True), player_id=9, streak=7, streak_stat="hits")],
    }), fonts)
    js20 = html20.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness20 = """
function stubEl() {
  return {addEventListener(){}, textContent:'', innerHTML:'', dataset:{}, style:{},
    setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){}};
}
function escapingEl() {
  let raw = "";
  const ESCAPE_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;"};
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){},
    get textContent() { return raw; },
    set textContent(v) { raw = v; },
    get innerHTML() { return raw.replace(/[&<>]/g, ch => ESCAPE_MAP[ch]); },
  };
}
const document = {getElementById: () => stubEl(),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => escapingEl()};
const window = {matchMedia: () => ({matches:false}), location: {reload(){}}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
const requestAnimationFrame = (fn) => {};
""" + js20 + """
const html = pickRow(PAYLOAD.data.streaks[0], 1);
if (!html.includes("streak-badge")) { console.error("FAIL: no streak-badge chip rendered"); process.exit(1); }
if (!html.includes("7")) { console.error("FAIL: the real streak length is missing"); process.exit(1); }
if (!html.includes("7-game streak")) { console.error("FAIL: the streak length text is missing"); process.exit(1); }
console.log("streak badge: all checks passed");
"""
    harness_path20 = tempfile.mktemp(suffix=".js")
    with open(harness_path20, "w") as f:
        f.write(harness20)
    try:
        r20 = subprocess.run([node, harness_path20], capture_output=True, text=True)
        check(r20.returncode == 0, "pickRow() renders a real streak badge with the streak "
              "length and a human-readable description of what's streaking",
              r20.stdout + r20.stderr)
    finally:
        os.remove(harness_path20)
else:
    check(True, "node not available -- streak badge JS check skipped, not failed")

head("21. run_live_fetch()'s no-games early return stamps a timezone-aware generated_at -- "
     "real bug, found live 2026-08-15: datetime.now().isoformat() (naive, no tz suffix) gets "
     "parsed by a browser's `new Date(iso)` as LOCAL time, not UTC, so the page showed an "
     "'Updated' time hours in the future for a viewer west of UTC.")

import generate_picks as gp

with mock.patch.object(gp, "_build_and_score", return_value=None), \
     mock.patch.object(gp.m, "TODAY", "2026-08-15"):
    out21 = bd.run_live_fetch()
check("+00:00" in out21["generated_at"] or out21["generated_at"].endswith("Z"),
      "the no-games early-return path stamps a real UTC offset, not a naive local timestamp",
      f"got {out21['generated_at']!r}")

head("22. Assumed-lineup candidates: direct follow-up request, verbatim -- \"our system should "
     "use assumed lineups. The players lines are still out there, we would just scratch the "
     "ones who don't end up on the final roster... we shouldn't have to wait for lineups.\" "
     "run_live_fetch() now merges quality_control()'s assumed_lineup pool into the same "
     "candidate flow as confirmed picks (see combined_candidates there) -- by the time a row "
     "reaches build_payload(), it's indistinguishable in SHAPE from a confirmed one, just "
     "still carrying lineup_assumed=True so the client can badge it. This checks build_payload "
     "doesn't do anything to filter that flag back out.")

assumed_row = dict(row("Assumed Lineup Guy", "hits", 0.75, odds=-140, implied=0.58, edge=0.17,
                       clears=True, confidence="High"), lineup_assumed=True)
payload22 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-15",
    "hits": [row("Confirmed Guy", "hits", 0.6, odds=-110, implied=0.52, edge=0.08, clears=True),
            assumed_row],
})
check(any(r.get("name") == "Assumed Lineup Guy" and r.get("lineup_assumed") is True
          for r in payload22["data"]["hits"]),
      "an assumed-lineup candidate appears in its real category tab, still flagged",
      f"got {payload22['data']['hits']}")
check(any(r.get("name") == "Assumed Lineup Guy" for r in payload22["data"]["all"]),
      "an assumed-lineup candidate is included in All Props, not walled off",
      f"got {[r.get('name') for r in payload22['data']['all']]}")
check(any(r.get("name") == "Assumed Lineup Guy" and r.get("lineup_assumed") is True
          for r in payload22["data"]["top_picks"]),
      "a High-confidence, price_clears==True assumed-lineup candidate DOES reach Top Picks now "
      "(direct request: use assumed lineups as real board data) -- but still carries "
      "lineup_assumed=True so the client renders its badge there too",
      f"got {payload22['data']['top_picks']}")

head("23. Assumed-lineup rows never earn the 'Lock' badge client-side, even with a real "
     "posted price and High confidence in Top Picks itself -- a Lock badge is a strong "
     "recommendation signal, and an unconfirmed lineup slot is still a guess no matter how "
     "good the price looks")

if node:
    html23 = bd.render_html(bd.build_payload({
        "generated_at": "x", "date": "2026-08-15",
        "hits": [dict(row("Guessed Slot Guy", "hits", 0.8, odds=-200, implied=0.6, edge=0.2,
                          clears=True, confidence="High"), lineup_assumed=True)],
    }), fonts)
    js23 = html23.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness23 = """
function stubEl() {
  return {addEventListener(){}, textContent:'', innerHTML:'', dataset:{}, style:{},
    setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){}};
}
function escapingEl() {
  let raw = "";
  const ESCAPE_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;"};
  return {
    addEventListener(){}, dataset:{}, style:{}, setAttribute(){}, removeAttribute(){}, value:'',
    classList:{add(){}, remove(){}, toggle(){return false;}}, querySelectorAll: () => [],
    querySelector: () => null, remove(){},
    get textContent() { return raw; },
    set textContent(v) { raw = v; },
    get innerHTML() { return raw.replace(/[&<>]/g, ch => ESCAPE_MAP[ch]); },
  };
}
const document = {getElementById: () => stubEl(),
  documentElement: {setAttribute(){}, removeAttribute(){}, getAttribute: () => null},
  querySelectorAll: () => [], querySelector: () => null, createElement: () => escapingEl()};
const window = {matchMedia: () => ({matches:false}), location: {reload(){}}};
const localStorage = {getItem: () => null, setItem(){}};
const fetch = () => Promise.reject(new Error("no network in test"));
const setInterval = () => {};
const requestAnimationFrame = (fn) => {};
""" + js23 + """
const html = pickRow(PAYLOAD.data.hits[0], 1);
if (html.includes("lock-badge")) { console.error("FAIL: an assumed-lineup row earned a Lock badge -- looks like a real recommendation"); process.exit(1); }
if (!html.includes("assumed-badge")) { console.error("FAIL: no unconfirmed-lineup badge rendered"); process.exit(1); }
if (!html.includes("Lineup not confirmed")) { console.error("FAIL: the unconfirmed-lineup disclaimer text is missing"); process.exit(1); }
if (!html.includes("lineup-assumed")) { console.error("FAIL: the .pick row isn't visually flagged as lineup-assumed"); process.exit(1); }
console.log("assumed-lineup badge: all checks passed");
"""
    harness_path23 = tempfile.mktemp(suffix=".js")
    with open(harness_path23, "w") as f:
        f.write(harness23)
    try:
        r23 = subprocess.run([node, harness_path23], capture_output=True, text=True)
        check(r23.returncode == 0, "an assumed-lineup row never renders a Lock badge and always "
              "renders the unconfirmed-lineup disclaimer badge, even inside a normal category "
              "tab", r23.stdout + r23.stderr)
    finally:
        os.remove(harness_path23)
else:
    check(True, "node not available -- early-look badge JS check skipped, not failed")

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
