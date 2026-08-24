#!/usr/bin/env python3
"""Regression for a real bug found 2026-08-24: a player can carry several
distinct streak entries -- dashboard/build_dashboard.py's _compute_streaks()
correctly tracks each real stat (hits, singles, hits_runs_rbis, ...)
separately, but app.js's streakChip() rendered identical text for all of
them ("14 straight — Chandler Simpson"), with no way to tell them apart.
Misleading, not just repetitive: a viewer has no way to know these are
three different real streaks rather than a duplicate-rendering bug.

FIX: streakChip() now includes a market label derived from the streak's
own linked prop's real `prop` text (p.prop, e.g. "Over 0.5 Hits" ->
"Hits") -- not a new value, just reusing the real text that already
uniquely identifies each streak entry's market.
"""
from __future__ import annotations

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
  // esc() in app.js relies on a real element's textContent -> innerHTML
  // escaping (div.textContent = s; return div.innerHTML). A createElement()
  // returning a plain, unlinked {textContent, innerHTML} object -- as a
  // naive fake would -- makes esc() silently return "" always, which
  // would make this test pass for the wrong reason (chip HTML strings
  // only differing by their id attribute, never by real visible text).
  createElement() {
    let text = "";
    return {
      get textContent() { return text; },
      set textContent(v) { text = v; },
      get innerHTML() {
        return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;")
          .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
      },
    };
  },
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
  // Real-shaped fixture: one player, three distinct real streaks on
  // three real, differently-priced markets -- exactly the Chandler
  // Simpson case found live.
  const hitsProp = {id: "fc2:1:player-1:hits:1:over", name: "Chandler Simpson",
    team: "A", matchup: "A @ B", prop: "Over 0.5 Hits", stat: "hits",
    recommendation_status: "neutral", hit_probability: 0.7, market_odds: -140,
    game_pk: 1, game_start: "2099-01-01T00:00:00Z"};
  const singlesProp = {...hitsProp, id: "fc2:1:player-1:singles:1:over",
    prop: "Over 0.5 Singles", stat: "singles"};
  const hrrProp = {...hitsProp, id: "fc2:1:player-1:hits_runs_rbis:1:over",
    prop: "Over 0.5 Hits+Runs+RBIs", stat: "hits_runs_rbis"};
  const sbProp = {...hitsProp, id: "fc2:2:player-2:stolen_base:1:over",
    name: "Speedy Runner", prop: "To Steal a Base", stat: "stolen_base"};

  DATA = {generated_at: "2099-01-01T00:00:00Z", odds_fetched_at: "2099-01-01T00:00:00Z",
          props: [hitsProp, singlesProp, hrrProp, sbProp], summary: {}};
  indexProps();

  const streaks = [
    {id: hitsProp.id, streak: 14, streak_stat: "hits"},
    {id: singlesProp.id, streak: 14, streak_stat: "singles"},
    {id: hrrProp.id, streak: 14, streak_stat: "hits_runs_rbis"},
    {id: sbProp.id, streak: 17, streak_stat: "stolen_base"},
  ];
  const rendered = streaks.map(streakChip);
  return { rendered, marketLabels: streaks.map(s => streakMarketLabel(PROPS_BY_ID.get(s.id))) };
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


class StreakChipClarityTests(unittest.TestCase):
    def _run(self):
        completed = subprocess.run(
            ["node", "-e", NODE_HARNESS, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        import json
        return json.loads(completed.stdout)

    def test_same_player_different_stat_streaks_render_distinctly(self):
        result = self._run()
        rendered = result["rendered"]
        self.assertEqual(len(rendered), 4)

        # The three same-player, same-length, different-market streaks
        # must NOT render identically -- that's the exact bug found live.
        same_player_streaks = rendered[:3]
        self.assertEqual(len(set(same_player_streaks)), 3,
                         "same-player streaks on different markets must render distinctly")

        # Each chip's real market label is exactly what its own linked
        # prop's `prop` text says, with only the "Over/Under <line> "
        # prefix stripped -- no invented label.
        self.assertEqual(result["marketLabels"], [
            "Hits", "Singles", "Hits+Runs+RBIs", "To Steal a Base",
        ])

        # The market label must actually appear in the rendered chip text.
        for chip_html, label in zip(rendered, result["marketLabels"]):
            self.assertIn(label, chip_html)

        # The real streak count and player name must still be present.
        self.assertIn("14", rendered[0])
        self.assertIn("Chandler Simpson", rendered[0])
        self.assertIn("17", rendered[3])
        self.assertIn("Speedy Runner", rendered[3])

    def test_market_label_strips_only_the_over_under_line_prefix(self):
        result = self._run()
        # A prop with no "Over/Under <line> " prefix (e.g. a moneyline-
        # style market) must pass through unchanged, not be mangled.
        self.assertEqual(result["marketLabels"][3], "To Steal a Base")


if __name__ == "__main__":
    unittest.main(verbosity=2)
