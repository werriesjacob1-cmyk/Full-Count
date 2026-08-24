#!/usr/bin/env python3
"""Execute the empty-Top-Picks explainer in Node, not just source-grep it.

Proves topPickGapExplainer() surfaces real, already-computed status_reasons
counts and the real earliest game_start -- never invented data -- when there
are zero Top Picks on the board.
"""
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

function makeEl() {
  let _text = "";
  return {
    get textContent() { return _text; },
    set textContent(v) { _text = String(v); },
    get innerHTML() {
      return _text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                   .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
  };
}
const document = {
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return makeEl(); },
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
  function prop(id, statusReasons) {
    return {
      id, name: "Player " + id, prop: "Over 0.5 Hits", stat: "hits",
      recommendation_status: "lean", status_reasons: statusReasons,
      game_start: "2026-08-24T23:10:00Z",
    };
  }
  const props = [
    prop("a", ["lineup slot is still a projection (Rotowire/last-known), not a confirmed lineup — cannot be an official Top Pick until it is"]),
    prop("b", ["lineup slot is still a projection (Rotowire/last-known), not a confirmed lineup — cannot be an official Top Pick until it is"]),
    prop("c", ["a real read, but no market price is posted yet to grade a Top Pick's price/value requirement against"]),
    prop("d", ["a real, positive read that doesn't clear every Top Pick requirement"]),
    prop("e", ["reliability grade C is too thin a sample to stand behind as a Top Pick yet, even though the read itself is real"]),
    prop("f", ["no meaningful evidence either direction — this is a real 'no opinion,' not a gap in coverage"]),
  ];
  DATA = {
    generated_at:"2026-08-24T20:00:00Z", odds_fetched_at:"2026-08-24T20:00:00Z",
    props, summary:{},
    schedule: [
      { game_pk: 1, game_start: "2026-08-25T00:05:00Z" },
      { game_pk: 2, game_start: "2026-08-24T22:40:00Z" },
    ],
  };
  const summary = topPickGapSummary(props);
  const html = topPickGapExplainer(props);
  return { summary, html };
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


NODE_EMPTY_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
function makeEl() {
  let _text = "";
  return {
    get textContent() { return _text; },
    set textContent(v) { _text = String(v); },
    get innerHTML() {
      return _text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                   .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
  };
}
const document = {
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return makeEl(); },
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
  const props = [];
  DATA = { generated_at:"2026-08-24T20:00:00Z", odds_fetched_at:"2026-08-24T20:00:00Z",
           props, summary:{}, schedule: [] };
  const summary = topPickGapSummary(props);
  const html = topPickGapExplainer(props);
  return { summary, html };
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


class EmptyBoardTransparencyTests(unittest.TestCase):
    def _run(self):
        completed = subprocess.run(
            ["node", "-e", NODE_HARNESS, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_counts_match_real_status_reasons_not_invented(self):
        result = self._run()
        self.assertEqual(result["summary"]["counts"], {
            "lineupPending": 2, "pricePending": 1, "closeRead": 1,
            "thinSample": 1, "other": 1,
        })

    def test_explainer_html_mentions_each_real_count_and_earliest_start(self):
        result = self._run()
        html = result["html"]
        self.assertIn("<b>2</b> props are waiting on a confirmed starting lineup", html)
        self.assertIn("<b>1</b> real, positive reads have no live sportsbook price", html)
        self.assertIn("<b>1</b> props have a real, positive read that falls just short", html)
        self.assertIn("<b>1</b> props look promising but rest on too thin a track record", html)
        # Earliest of the two real schedule entries is 22:40Z, not the later one.
        self.assertIn("First pitch tonight is", html)
        self.assertIn("No bets currently meet Full Count's Top Pick standards.", html)

    def test_no_reasons_present_still_renders_without_crashing(self):
        harness = NODE_EMPTY_HARNESS
        completed = subprocess.run(
            ["node", "-e", harness, APP], cwd=ROOT,
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["summary"]["counts"], {
            "lineupPending": 0, "pricePending": 0, "closeRead": 0,
            "thinSample": 0, "other": 0,
        })
        self.assertNotIn("<ul", result["html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
