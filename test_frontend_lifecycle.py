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


if __name__ == "__main__":
    unittest.main(verbosity=2)
