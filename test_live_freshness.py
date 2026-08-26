#!/usr/bin/env python3
"""test_live_freshness.py — 2026-08-19 Live Integrity PR 1.

Executes the real client-side freshness contract in Node (not a source
grep), exactly like test_frontend_lifecycle.py's own harness pattern.
Covers the deterministic staleness function with an injected fake clock
(never Date.now() read internally by liveFreshnessState itself), every
real game_state value's applicability, the per-prop chip's independence
from other props' state, and a true clock-advances-while-backend-stops
integration scenario through the actual renderFreshness()/DOM update path.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "dashboard", "static", "app.js")
THRESHOLD_SECONDS = 15 * 60


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");

const bar = { innerHTML: "" };
const RealDate = Date;
const context = {
  console,
  document: {
    addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    // esc() (app.js) relies on the real browser's textContent -> innerHTML
    // escaping to HTML-escape a string. A real DOM element does this
    // automatically; this stub must reproduce that link explicitly or
    // esc() silently returns "" for everything (a plain {textContent,
    // innerHTML} object mock, as used in test_frontend_lifecycle.py's own
    // harness, has no such link and only happens to work there because
    // that test never exercises esc()).
    createElement() {
      let text = "";
      return {
        get textContent() { return text; },
        set textContent(v) { text = v; },
        get innerHTML() {
          return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        },
      };
    },
    getElementById(id) { return id === "freshness-bar" ? bar : null; },
  },
  window: { scrollY: 0, scrollTo() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  setTimeout, clearTimeout, setInterval() {}, Intl, Map, Set, Object, Array,
  JSON, Math, Number, String,
  bar, FAKE_NOW: null, __liveDoc: null,
};
// pollLive() calls fetchJSON("live.json") -> fetch(...).json(). Reads
// whatever context.__liveDoc currently holds so a test can swap the served
// document between two pollLive() calls, exactly like a real poll seeing
// updated content on the second request.
context.fetch = async () => ({ ok: true, json: async () => context.__liveDoc });
// FakeDate.now() reads context.FAKE_NOW (a real property on the vm's own
// global object) rather than an outer closure variable, because a bare
// assignment inside vm.runInContext-executed code sets a property on the
// context object, not on any variable in this outer Node scope.
class FakeDate extends RealDate {
  static now() { return context.FAKE_NOW != null ? context.FAKE_NOW : RealDate.now(); }
}
context.Date = FakeDate;
vm.createContext(context);
vm.runInContext(source, context);
// Shadow renderRoute with a spy AFTER the real script defines it -- app.js
// declares it as a normal top-level function, so this binding is mutable
// even under "use strict" (only implicit-global creation is blocked, and
// renderRoute is already declared).
vm.runInContext("renderRoute = () => { globalThis.__renderRouteCalls = (globalThis.__renderRouteCalls||0)+1; };", context);

const result = vm.runInContext(`(() => {
  const THRESH = %(threshold)d;
  const T0 = Date.parse("2020-06-01T00:00:00Z");
  const result = {};

  // ---- pure boundary checks: liveFreshnessState(nowMs, doc, props) -----
  const liveProps = [{ id: "a", game_state: "live" }];
  const docT0 = { grades_checked_at: new Date(T0).toISOString() };
  result.boundaries = {
    fresh: liveFreshnessState(T0 + 60 * 1000, docT0, liveProps),
    justInside: liveFreshnessState(T0 + (THRESH - 1) * 1000, docT0, liveProps),
    atThreshold: liveFreshnessState(T0 + THRESH * 1000, docT0, liveProps),
    justBeyond: liveFreshnessState(T0 + (THRESH + 1) * 1000, docT0, liveProps),
    neverChecked: liveFreshnessState(T0, { grades_checked_at: null }, liveProps),
    neverCheckedMissingKey: liveFreshnessState(T0, {}, liveProps),
  };

  // ---- not applicable at all when nothing is in progress ----------------
  result.noInProgress = liveFreshnessState(
    T0 + 999999 * 1000, docT0, [{ id: "a", game_state: "pregame" }, { id: "b", game_state: "final" }],
  );

  // ---- every real game_state value's applicability -----------------------
  const STATES = ["pregame", "live", "delayed", "suspended", "postponed", "final", "cancelled", "unknown"];
  result.perGameState = {};
  for (const gs of STATES) {
    result.perGameState[gs] = liveFreshnessState(T0, { grades_checked_at: null }, [{ id: "x", game_state: gs }]).applicable;
  }

  // ---- per-prop chip independence: one live+stale prop, one final prop --
  const livePropFull = { id: "fc2:1:p1:hits:1:over", game_state: "live", stat: "hits" };
  const finalPropFull = { id: "fc2:2:p2:hits:1:over", game_state: "final", stat: "hits" };
  DATA = {
    generated_at: docT0.grades_checked_at, date: "2020-06-01",
    grades_checked_at: docT0.grades_checked_at,
    props: [livePropFull, finalPropFull], summary: {},
  };
  indexProps();
  FAKE_NOW = T0 + (THRESH + 120) * 1000; // well beyond threshold
  renderFreshness();
  result.chips = {
    liveGetsChip: liveStaleChip(livePropFull) !== "",
    finalGetsNoChipEvenWhileGlobalStale: liveStaleChip(finalPropFull) === "",
    barShowsStale: bar.innerHTML.includes('class="stale-flag"'),
  };

  // ---- integration: clock advances while backend silently stops ---------
  DATA = {
    generated_at: docT0.grades_checked_at, date: "2020-06-01",
    grades_checked_at: docT0.grades_checked_at,
    props: [livePropFull], summary: {},
  };
  indexProps();
  globalThis.__renderRouteCalls = 0;
  FAKE_NOW = T0 + 60 * 1000; // 1 minute in: fresh
  renderFreshness();
  const freshBar = bar.innerHTML;
  const freshCallsAfterFirst = globalThis.__renderRouteCalls;
  FAKE_NOW = T0 + 20 * 60 * 1000; // 20 minutes in, DATA never changed (no new poll succeeded)
  renderFreshness();
  const staleBar = bar.innerHTML;
  const callsAfterFlipToStale = globalThis.__renderRouteCalls;
  FAKE_NOW = T0 + 25 * 60 * 1000; // still stale, no flip this time
  renderFreshness();
  const callsAfterSecondStaleTick = globalThis.__renderRouteCalls;
  result.clockAdvance = {
    freshBarHadNoStale: !freshBar.includes('class="stale-flag"'),
    freshCallsAfterFirst,
    staleBarShowsStale: staleBar.includes('class="stale-flag"'),
    renderRouteCalledExactlyOnceOnFlip: callsAfterFlipToStale - freshCallsAfterFirst === 1,
    noExtraRenderRouteOnRepeatStaleTick: callsAfterSecondStaleTick === callsAfterFlipToStale,
  };

  // ---- calm, tiered freshness wording (Part 6 of the UX revamp,
  // 2026-08-26; direct product decision after a real incident where a
  // finished, LOST Top Pick still showed "Live" + an ALL-CAPS "LIVE DATA
  // STALE"/"LIVE DATA STATUS UNKNOWN" alarm). Detection thresholds are
  // UNCHANGED (still LIVE_STALE_THRESHOLD_SECONDS=15m for "delayed",
  // LIVE_INCIDENT_THRESHOLD_SECONDS=30m for the wording-only escalation)
  // -- only wording is under test here. -----------------------------------
  DATA = {
    // prices_updated_at deliberately omitted -- isolates the game/settlement
    // channel's wording tiers from the odds channel's, which is exercised
    // separately (freshnessBarMessage()'s oddsIncident/gameIncident branches).
    generated_at: docT0.grades_checked_at, date: "2020-06-01",
    grades_checked_at: docT0.grades_checked_at,
    props: [livePropFull], summary: {},
  };
  indexProps();
  FAKE_NOW = T0 + 60 * 1000; // healthy: nothing alarming
  renderFreshness();
  const healthyBar = bar.innerHTML;
  FAKE_NOW = T0 + 20 * 60 * 1000; // 20m: small delay, still calm, no "delayed" word
  renderFreshness();
  const smallDelayBar = bar.innerHTML;
  FAKE_NOW = T0 + 40 * 60 * 1000; // 40m: real incident tier
  renderFreshness();
  const incidentBar = bar.innerHTML;
  result.tieredWording = {
    healthyHasNoStaleFlag: !healthyBar.includes('class="stale-flag"'),
    smallDelayShowsPlainFact: smallDelayBar.includes("Game status checked") && smallDelayBar.includes("20m ago"),
    // Strip HTML tags/attributes first -- the .stale-flag CLASS NAME itself
    // contains the substring "stale" and must not trip this check; only the
    // visible TEXT is under test here.
    smallDelayHasNoAlarmWords: !/STALE|UNKNOWN|delayed/i.test(smallDelayBar.replace(/<[^>]*>/g, "")),
    incidentUsesCalmDelayedWording: incidentBar.includes("Game updates delayed") && incidentBar.includes("last checked"),
    incidentHasNoAllCaps: !/[A-Z]{4,}/.test(incidentBar.replace(/<[^>]*>/g, "").replace(/UTC|Aug|PM|AM/g, "")),
  };

  // ---- regression: a poll carrying ONLY a heartbeat advance (no other
  // field changed) must not be dropped by pollLive()'s dedup guard, or the
  // browser would never learn the backend recovered and the stale banner
  // would get stuck forever even once checks resume normally. -----------
  return (async () => {
    DATA = {
      generated_at: docT0.grades_checked_at, date: "2020-06-01",
      grades_checked_at: docT0.grades_checked_at,
      props: [livePropFull], summary: {},
    };
    indexProps();
    lastPollStamp = null;
    globalThis.__liveDoc = {
      updated_at: docT0.grades_checked_at, prices_updated_at: null,
      grades_updated_at: docT0.grades_checked_at, grades_checked_at: docT0.grades_checked_at,
      props: {},
    };
    await pollLive(); // first poll: establishes lastPollStamp
    const laterHeartbeat = new Date(T0 + 5 * 60 * 1000).toISOString();
    globalThis.__liveDoc = {
      // identical *_updated_at triplet to the first poll -- only the
      // heartbeat moved, simulating a genuine no-op grading cycle
      updated_at: docT0.grades_checked_at, prices_updated_at: null,
      grades_updated_at: docT0.grades_checked_at, grades_checked_at: laterHeartbeat,
      props: {},
    };
    FAKE_NOW = T0 + 6 * 60 * 1000;
    await pollLive(); // second poll: heartbeat-only change must still be ingested
    result.heartbeatOnlyPollRegression = {
      pickedUpNewHeartbeat: DATA.grades_checked_at === laterHeartbeat,
    };
    return result;
  })();
})()`, context);
result.then((r) => process.stdout.write(JSON.stringify(r)));
""" % {"threshold": THRESHOLD_SECONDS}


class LiveFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run(
            ["node", "-e", NODE_HARNESS, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        cls.assertion_stderr = completed.stderr
        cls.returncode = completed.returncode
        cls.result = json.loads(completed.stdout) if completed.returncode == 0 else None

    def setUp(self):
        self.assertEqual(self.returncode, 0, self.assertion_stderr)

    def test_fresh_and_just_inside_threshold_are_not_stale(self):
        b = self.result["boundaries"]
        self.assertFalse(b["fresh"]["stale"])
        self.assertTrue(b["fresh"]["applicable"])
        self.assertFalse(b["justInside"]["stale"])

    def test_exactly_at_threshold_is_not_yet_stale(self):
        # Boundary is exclusive: age must exceed the threshold, not merely
        # reach it, matching liveFreshnessState's `ageSeconds > THRESHOLD`.
        self.assertFalse(self.result["boundaries"]["atThreshold"]["stale"])

    def test_just_beyond_threshold_is_stale(self):
        b = self.result["boundaries"]["justBeyond"]
        self.assertTrue(b["stale"])
        self.assertEqual(b["reason"], "age_exceeded")

    def test_never_checked_is_honestly_uncertain_not_confidently_fresh(self):
        for key in ("neverChecked", "neverCheckedMissingKey"):
            with self.subTest(key=key):
                b = self.result["boundaries"][key]
                self.assertTrue(b["applicable"])
                self.assertTrue(b["stale"])
                self.assertEqual(b["reason"], "never_checked")

    def test_no_in_progress_game_means_not_applicable_regardless_of_age(self):
        r = self.result["noInProgress"]
        self.assertFalse(r["applicable"])
        self.assertFalse(r["stale"])

    def test_every_real_game_state_applicability(self):
        self.assertEqual(self.result["perGameState"], {
            "pregame": False, "live": True, "delayed": True, "suspended": True,
            "postponed": False, "final": False, "cancelled": False, "unknown": True,
        })

    def test_per_prop_chip_only_applies_to_that_props_own_in_progress_state(self):
        c = self.result["chips"]
        self.assertTrue(c["barShowsStale"])
        self.assertTrue(c["liveGetsChip"])
        self.assertTrue(c["finalGetsNoChipEvenWhileGlobalStale"])

    def test_heartbeat_only_poll_is_not_dropped_by_dedup(self):
        # Regression: pollLive()'s dedup stamp must include the heartbeat
        # fields, or a poll where ONLY grades_checked_at advanced (a real,
        # healthy no-op check cycle) would be silently dropped, and the
        # stale banner would never clear even once the backend recovers.
        self.assertTrue(self.result["heartbeatOnlyPollRegression"]["pickedUpNewHeartbeat"])

    def test_calm_tiered_freshness_wording_no_all_caps_alarm(self):
        t = self.result["tieredWording"]
        self.assertTrue(t["healthyHasNoStaleFlag"], "a healthy freshness state shows nothing alarming")
        self.assertTrue(t["smallDelayShowsPlainFact"],
                         "a small delay (20m, under the 30m incident threshold) reads as a plain fact")
        self.assertTrue(t["smallDelayHasNoAlarmWords"],
                         "a small delay must not use STALE/UNKNOWN/delayed-style alarm words")
        self.assertTrue(t["incidentUsesCalmDelayedWording"],
                         "a real incident (40m) uses calm 'delayed' wording, not an ALL-CAPS alarm")
        self.assertTrue(t["incidentHasNoAllCaps"],
                         "REGRESSION GUARD: no ALL-CAPS text anywhere in the incident-tier bar "
                         "(the literal old 'LIVE DATA STALE'/'STATUS UNKNOWN' phrasing must be gone)")

    def test_clock_advancing_with_no_backend_write_still_surfaces_staleness(self):
        c = self.result["clockAdvance"]
        self.assertTrue(c["freshBarHadNoStale"])
        self.assertTrue(c["staleBarShowsStale"])
        self.assertTrue(c["renderRouteCalledExactlyOnceOnFlip"])
        self.assertTrue(c["noExtraRenderRouteOnRepeatStaleTick"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
