#!/usr/bin/env python3
"""Execute the browser lifecycle contract in Node, not just source-grep it."""
from __future__ import annotations

import json
import os
import subprocess
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "dashboard", "static", "app.js")


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const document = {
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return { textContent: "", innerHTML: "" }; },
};
const context = {
  console, document, window: { scrollY: 0, scrollTo() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  setTimeout, clearTimeout, setInterval() {}, fetch: async () => ({}),
  Intl, Date, Map, Set, Object, Array, JSON, Math, Number, String,
};
vm.createContext(context);
vm.runInContext(source, context);
const result = vm.runInContext(`(() => {
  const visuals = {
    pregame: lifecycleClass({game_state:"pregame", settlement_state:"open"}),
    live: lifecycleClass({game_state:"live", settlement_state:"open"}),
    provisional: lifecycleClass({game_state:"live", settlement_state:"provisional_hit"}),
    hit: lifecycleClass({game_state:"final", settlement_state:"hit"}),
    miss: lifecycleClass({game_state:"final", settlement_state:"miss"}),
    void: lifecycleClass({game_state:"final", settlement_state:"void"}),
    ungraded: lifecycleClass({game_state:"final", settlement_state:"ungraded"}),
    provisionalChip: gradeChip({game_state:"live", settlement_state:"provisional_hit"}),
  };
  const published = {
    id:"fc2:1:player-1:hits:1:over", game_start:"2020-01-01T00:00:00Z",
    recommendation_status:"lean", market_odds:-140,
    published_top_pick_at:"2020-01-01T00:00:00Z", publication_artifact_id:"a".repeat(64),
    publication_snapshot:{
      id:"fc2:1:player-1:hits:1:over", game_pk:1, player_id:"player-1",
      stat:"hits", market_side:"over", needs:1,
      recommendation_status:"top_pick", market_odds:-120,
    },
    game_state:"live", game_state_observed_at:"2020-01-01T00:01:00Z",
    settlement_state:"open", settlement_authority:"none",
    settlement_observed_at:"2020-01-01T00:01:00Z", settlement_source:"fixture",
  };
  const unpublished = {...published, id:"fc2:1:player-2:hits:1:over"};
  delete unpublished.published_top_pick_at; delete unpublished.publication_artifact_id;
  DATA = {generated_at:"2020-01-01T00:00:00Z", odds_fetched_at:"2020-01-01T00:00:00Z",
          props:[published, unpublished], summary:{}};
  indexProps();
  const freshLive = {updated_at:"2020-01-01T00:03:00Z", prices_updated_at:"2020-01-01T00:02:00Z",
    grades_updated_at:"2020-01-01T00:03:00Z", props:{[published.id]:{
      market_odds:-150, recommendation_status:"lean",
      settlement_state:"provisional_hit", settlement_authority:"live_observation",
      settlement_observed_at:"2020-01-01T00:03:00Z", settlement_source:"live",
      result_actual:1, result_reason:"threshold reached",
      _field_updated_at:{market_odds:"2020-01-01T00:02:00Z",
                         recommendation_status:"2020-01-01T00:02:00Z"},
    }}};
  ingestLiveDocument(freshLive);
  applyCachedLive();
  const frozen = {odds:published.market_odds, recommendation:published.recommendation_status,
                  settlement:published.settlement_state};
  const visibleIds = publicProps().map(p => p.id);
  let duplicateRejected = false;
  DATA = {props:[published, {...published}]};
  try { indexProps(); } catch (_) { duplicateRejected = true; }
  return {visuals, frozen, visibleIds, duplicateRejected};
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


class FrontendLifecycleTests(unittest.TestCase):
    def test_visuals_visibility_freeze_and_duplicate_guard(self):
        completed = subprocess.run(
            ["node", "-e", NODE_HARNESS, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["visuals"], {
            "pregame": "lifecycle-pregame", "live": "lifecycle-live",
            "provisional": "lifecycle-hit", "hit": "lifecycle-hit",
            "miss": "lifecycle-miss", "void": "lifecycle-void",
            "ungraded": "lifecycle-ungraded",
            "provisionalChip": '<span class="chip chip-grade-hit">Cashed · Awaiting final</span>',
        })
        self.assertEqual(result["frozen"], {
            "odds": -120, "recommendation": "top_pick",
            "settlement": "provisional_hit",
        })
        self.assertEqual(result["visibleIds"], ["fc2:1:player-1:hits:1:over"])
        self.assertTrue(result["duplicateRejected"])

    def test_why_watchouts_do_not_regress_to_stale_first_publication_snapshot(self):
        # 2026-08-25 Weston Wilson investigation: freezePublishedSnapshot()
        # used to copy EVERY key in publication_snapshot onto the live row,
        # including why/watchouts -- so a Top Pick first published while
        # generate_picks.py had a real directionality bug (routing negative
        # context into "why" instead of "watchouts") kept showing that
        # stale, wrong explanation forever after the game started, even
        # after the generator itself was fixed and every subsequent rebuild
        # computed the correct why/watchouts. Real case: Weston Wilson's
        # Over 0.5 Hits+Runs+RBIs kept showing "Opposing SP ERA 2.92" as a
        # green WHY bullet instead of a watchout.
        completed = subprocess.run(
            ["node", "-e", WESTON_HARNESS, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        # PRESENTATION must reflect the CURRENT (fixed) generator, not the
        # stale snapshot from first publication -- the real fix.
        self.assertEqual(result["why"], ["wOBA vs xwOBA underperforming -- positive regression candidate"])
        self.assertEqual(result["watchouts"], ["Opposing SP ERA 2.92 -- elite pitcher, tough matchup"])
        self.assertNotIn("Opposing SP ERA 2.92 -- elite pitcher, tough matchup", result["why"])
        # AUDIT/SETTLEMENT-CRITICAL fields still freeze to first publication,
        # proving this isn't a blanket "stop freezing anything" regression.
        self.assertEqual(result["market_odds"], -120)
        self.assertEqual(result["hit_probability"], 0.7)
        self.assertEqual(result["recommendation_status"], "top_pick")

    def test_colt_keith_style_final_state_never_regresses_to_a_stale_live_poll(self):
        # Real incident, 2026-08-26 (game_pk 824234, Colt Keith, Over 0.5
        # Hits). Root cause reconstructed from the actual production data:
        # docs/data.json's own game_state (written by the periodic FULL
        # rebuild, "Dashboard refresh") had already observed this game as
        # final, but docs/live.json (the fast 5-minute channel
        # dashboard-live.yml maintains) was still carrying its own LAST
        # SUCCESSFUL check from BEFORE the game ended (game_state:"live",
        # settlement_state:"open") -- because GitHub's schedule trigger for
        # dashboard-live.yml had gone ~100+ minutes between actual runs
        # that day (confirmed via the real Actions run history, not
        # inferred). A customer whose browser polled live.json in that
        # window would merge an OLDER "still live" delta on top of an
        # ALREADY-final base -- exactly the regression this guard exists to
        # prevent. This test proves the guard holds: ingestLiveDocument()/
        # applyCachedLive() must refuse to let a game ever un-final itself,
        # and must never let a stale "open" settlement overwrite an already
        # -graded MISS. If this test ever fails, a customer WILL see a
        # finished, lost Top Pick sitting on "Live" again.
        completed = subprocess.run(
            ["node", "-e", COLT_KEITH_HARNESS, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        # The already-final, already-graded MISS must survive an older,
        # staler "still live" poll completely intact.
        self.assertEqual(result["afterStaleLivePoll"], {
            "game_state": "final", "settlement_state": "miss",
        })
        # A genuinely NEWER poll (observed after the base's own final
        # observation) reporting a corrected/confirmed final result is the
        # one case allowed to update an already-final row -- proven by
        # forcing miss -> void via a later, official-authority timestamp.
        self.assertEqual(result["afterNewerCorrection"], {
            "game_state": "final", "settlement_state": "void",
        })
        # HIT case: same chain, opposite real result, same guard exercised.
        self.assertEqual(result["hitCaseAfterStaleLivePoll"], {
            "game_state": "final", "settlement_state": "hit",
        })
        # PRICE RACE (Phase 5's explicit ask): an older price delta must not
        # overwrite a newer one, independent of the game/settlement guard.
        self.assertEqual(result["priceAfterOlderDelta"], -150,
                          "an older price observation must not overwrite a newer one")


WESTON_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const document = {
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return { textContent: "", innerHTML: "" }; },
};
const context = {
  console, document, window: { scrollY: 0, scrollTo() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  setTimeout, clearTimeout, setInterval() {}, fetch: async () => ({}),
  Intl, Date, Map, Set, Object, Array, JSON, Math, Number, String,
};
vm.createContext(context);
vm.runInContext(source, context);
const result = vm.runInContext(`(() => {
  const p = {
    id:"fc2:1:player-1:hits_runs_rbis:1:over", game_start:"2020-01-01T00:00:00Z",
    game_pk:1, player_id:1, stat:"hits_runs_rbis", market_side:"over",
    recommendation_status:"top_pick", market_odds:-120, hit_probability:0.7,
    why:["wOBA vs xwOBA underperforming -- positive regression candidate"],
    watchouts:["Opposing SP ERA 2.92 -- elite pitcher, tough matchup"],
    published_top_pick_at:"2020-01-01T00:00:00Z", publication_artifact_id:"a".repeat(64),
    publication_snapshot:{
      id:"fc2:1:player-1:hits_runs_rbis:1:over", game_pk:1, player_id:1,
      stat:"hits_runs_rbis", market_side:"over", recommendation_status:"top_pick",
      market_odds:-120, hit_probability:0.7,
      why:["Opposing SP ERA 2.92 -- elite pitcher, tough matchup"],
      watchouts:[],
    },
    game_state:"live", game_state_observed_at:"2020-01-01T00:01:00Z",
    settlement_state:"open", settlement_authority:"none",
    settlement_observed_at:"2020-01-01T00:01:00Z", settlement_source:"fixture",
  };
  freezePublishedSnapshot(p);
  return {why: p.why, watchouts: p.watchouts, market_odds: p.market_odds,
          hit_probability: p.hit_probability, recommendation_status: p.recommendation_status};
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


COLT_KEITH_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const document = {
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return { textContent: "", innerHTML: "" }; },
};
const context = {
  console, document, window: { scrollY: 0, scrollTo() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  setTimeout, clearTimeout, setInterval() {}, fetch: async () => ({}),
  Intl, Date, Map, Set, Object, Array, JSON, Math, Number, String,
};
vm.createContext(context);
vm.runInContext(source, context);
const result = vm.runInContext(`(() => {
  function baseRow(id, settlementState, authority, observedAt) {
    return {
      id, game_pk: 824234, player_id: 690993, stat: "hits", market_side: "over",
      recommendation_status: "top_pick", market_odds: -150,
      game_state: "final", game_state_observed_at: observedAt, game_state_source: "mlb_schedule",
      settlement_state: settlementState, settlement_authority: authority,
      settlement_observed_at: observedAt, settlement_source: "mlb_official_final_with_fanduel_eligibility",
      result_actual: settlementState === "hit" ? 1 : 0, result_reason: "final",
    };
  }
  const missRow = baseRow("fc2:824234:player-690993:hits:1:over", "miss", "official_final", "2020-01-01T00:10:00Z");
  const hitRow = baseRow("fc2:824234:player-691000:hits:1:over", "hit", "official_final", "2020-01-01T00:10:00Z");
  const priceRow = {...baseRow("fc2:824234:player-691001:hits:1:over", "miss", "official_final", "2020-01-01T00:10:00Z"),
                     market_odds: -150};
  priceRow._field_updated_at = { market_odds: "2020-01-01T00:10:00Z" };
  DATA = {generated_at:"2020-01-01T00:11:00Z", odds_fetched_at:"2020-01-01T00:00:00Z",
          props:[missRow, hitRow, priceRow], summary:{}};
  indexProps();
  // A stale live.json poll: last real check ran BEFORE the game went
  // final (matches the real Colt Keith incident -- dashboard-live.yml's
  // own last successful run predated MLB reporting the game final).
  const staleLiveDoc = {
    updated_at:"2020-01-01T00:06:00Z", grades_updated_at:"2020-01-01T00:06:00Z",
    prices_updated_at:"2020-01-01T00:06:00Z",
    props: {
      [missRow.id]: {
        game_state:"live", game_state_observed_at:"2020-01-01T00:05:00Z", game_state_source:"mlb_game_feed_by_game_pk",
        settlement_state:"open", settlement_authority:"live_observation",
        settlement_observed_at:"2020-01-01T00:05:00Z", settlement_source:"mlb_live_box_score",
        result_actual:0, result_reason:"awaiting authoritative final settlement",
      },
      [hitRow.id]: {
        game_state:"live", game_state_observed_at:"2020-01-01T00:05:00Z", game_state_source:"mlb_game_feed_by_game_pk",
        settlement_state:"open", settlement_authority:"live_observation",
        settlement_observed_at:"2020-01-01T00:05:00Z", settlement_source:"mlb_live_box_score",
        result_actual:0, result_reason:"awaiting authoritative final settlement",
      },
      [priceRow.id]: {
        market_odds: -200,
        _field_updated_at: { market_odds: "2020-01-01T00:03:00Z" },
      },
    },
  };
  ingestLiveDocument(staleLiveDoc);
  applyCachedLive();
  const afterStaleLivePoll = {
    game_state: PROPS_BY_ID.get(missRow.id).game_state,
    settlement_state: PROPS_BY_ID.get(missRow.id).settlement_state,
  };
  const hitCaseAfterStaleLivePoll = {
    game_state: PROPS_BY_ID.get(hitRow.id).game_state,
    settlement_state: PROPS_BY_ID.get(hitRow.id).settlement_state,
  };
  const priceAfterOlderDelta = PROPS_BY_ID.get(priceRow.id).market_odds;

  // A genuinely NEWER, equal-or-higher-authority correction (e.g. a box
  // score amendment) legitimately updates an already-final row.
  const correctionDoc = {
    updated_at:"2020-01-01T00:15:00Z", grades_updated_at:"2020-01-01T00:15:00Z",
    props: { [missRow.id]: {
      game_state:"final", game_state_observed_at:"2020-01-01T00:15:00Z", game_state_source:"mlb_schedule",
      settlement_state:"void", settlement_authority:"official_final",
      settlement_observed_at:"2020-01-01T00:15:00Z", settlement_source:"mlb_official_final_with_fanduel_eligibility",
      result_actual:0, result_reason:"corrected: game called, market voided",
    }},
  };
  ingestLiveDocument(correctionDoc);
  applyCachedLive();
  const afterNewerCorrection = {
    game_state: PROPS_BY_ID.get(missRow.id).game_state,
    settlement_state: PROPS_BY_ID.get(missRow.id).settlement_state,
  };
  return {afterStaleLivePoll, afterNewerCorrection, hitCaseAfterStaleLivePoll, priceAfterOlderDelta};
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


if __name__ == "__main__":
    unittest.main(verbosity=2)
