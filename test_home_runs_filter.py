#!/usr/bin/env python3
"""Regression for a real production bug found 2026-08-21: selecting the
"Home Runs" filter on the All Props page returned zero results, even
though real home-run props existed on the board.

ROOT CAUSE: dashboard/build_dashboard.py's build_payload() pops the
"home_runs" key out of the scoring result to avoid double-counting against
select_best_by_category's own list (see that function's own comment), and
counts family sizes by iterating the RESULT DICT'S remaining top-level
keys -- one of which is "moonshot" (select_moonshots()'s own internal
grouping key, mapped to the display label "Home Runs" via
CATEGORY_LABELS). That internal key leaked into DATA.families as the
family's *filterable* "stat" value ({"stat": "moonshot", "label": "Home
Runs", ...}), even though every real home-run PROP ROW's own p.stat field
is genuinely "home_runs" (see score_batter/select_moonshots' real row
construction -- confirmed directly against the live production payload:
82 real rows, all with stat == "home_runs", zero with stat == "moonshot").

app.js's applyFilters() filters by strict equality: `p.stat ===
filters.family`. Selecting "Home Runs" set filters.family = "moonshot"
(the <option value> taken directly from DATA.families[].stat), which
never equals any real row's "home_runs" -- so the filter matched nothing.

FIX: dashboard/static/app.js's familyFilterValue() maps the one
known-mismatched grouping key ("moonshot") to the real, row-level value
("home_runs") at both places a family's filter value is derived (the
desktop <select> and the mobile filter sheet), so the selectable value
always matches what's actually on p.stat. No backend file touched -- the
publicly-labeled family list and its counts are unchanged, only which
value the frontend filters by once "Home Runs" is chosen.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "dashboard", "static", "app.js")


def _hr_prop(player_id, name):
    return {
        "id": f"fc2:1:player-{player_id}:home_runs:1:over",
        "type": "batter", "name": name, "team": "A", "matchup": "A @ B",
        "prop": "Home Run", "stat": "home_runs",
        "projection": {"stat": "home_runs", "value": 1, "needs": 1},
        "recommendation_status": "lean", "hit_probability": 0.15,
        "market_odds": 450, "market_edge": 0.05, "game_pk": 1,
        "game_start": "2099-01-01T00:00:00Z",
    }


def _hits_prop(player_id, name):
    return {
        "id": f"fc2:1:player-{player_id}:hits:1:over",
        "type": "batter", "name": name, "team": "A", "matchup": "A @ B",
        "prop": "Over 0.5 Hits", "stat": "hits",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "recommendation_status": "lean", "hit_probability": 0.65,
        "market_odds": -140, "market_edge": 0.03, "game_pk": 1,
        "game_start": "2099-01-01T00:00:00Z",
    }


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
const fixtureJSON = fs.readFileSync(process.argv[2], "utf8");
const result = vm.runInContext(`(() => {
  const fixture = ${fixtureJSON};
  DATA = fixture;
  indexProps();

  // 1. Real HR props exist on the board (sanity on the fixture itself).
  const allIds = publicProps().map(p => p.id);
  const hrIdsPresent = fixture.props.filter(p => p.stat === "home_runs").map(p => p.id);

  // 2. familyFilterValue() maps the backend's family entry to the value
  //    real rows actually carry -- this IS the fix under test.
  const hrFamily = fixture.families.find(f => f.label === "Home Runs");
  const hrFilterValue = familyFilterValue(hrFamily.stat);

  // 3. Selecting Home Runs (as the real filter-dropdown/filter-sheet wiring
  //    does -- multi-select fix, Part 2 2026-08-26: filters.family is now
  //    filters.families, a Set, not a single string) returns exactly the
  //    real HR props, excluding non-HR ones.
  filters.families = new Set([hrFilterValue]);
  const selected = applyFilters(publicProps()).map(p => p.id);

  // 4. Clearing the filter restores the full board.
  filters.families = new Set();
  const cleared = applyFilters(publicProps()).map(p => p.id);

  return { allIds, hrIdsPresent, hrFilterValue, selected, cleared };
})()`, context);
process.stdout.write(JSON.stringify(result));
"""


class HomeRunsFilterTests(unittest.TestCase):
    def _run(self, fixture):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(fixture, f)
            fixture_path = f.name
        try:
            completed = subprocess.run(
                ["node", "-e", NODE_HARNESS, APP, fixture_path], cwd=ROOT,
                check=False, text=True, capture_output=True,
            )
        finally:
            os.unlink(fixture_path)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_home_runs_filter_returns_real_hr_props_and_excludes_others(self):
        hr_props = [_hr_prop(1, "Aaron Judge"), _hr_prop(2, "Shohei Ohtani")]
        hits_props = [_hits_prop(3, "Someone Else"), _hits_prop(4, "Another Batter")]
        fixture = {
            "generated_at": "2099-01-01T00:00:00Z", "odds_fetched_at": "2099-01-01T00:00:00Z",
            "summary": {},
            "props": hr_props + hits_props,
            # Real production shape: the "Home Runs" family entry's own
            # "stat" is the backend's internal grouping key ("moonshot"),
            # never the real per-row value.
            "families": [
                {"stat": "hits", "label": "Hits", "count": len(hits_props)},
                {"stat": "moonshot", "label": "Home Runs", "count": len(hr_props)},
            ],
        }
        result = self._run(fixture)

        # 1. Real/representative home-run props exist.
        hr_ids = {p["id"] for p in hr_props}
        self.assertEqual(set(result["hrIdsPresent"]), hr_ids)
        self.assertTrue(hr_ids.issubset(set(result["allIds"])))

        # familyFilterValue() must translate the backend's "moonshot"
        # grouping key to the real row-level value.
        self.assertEqual(result["hrFilterValue"], "home_runs")

        # 2. Selecting Home Runs returns them.
        self.assertEqual(set(result["selected"]), hr_ids)

        # 3. Non-home-run props are excluded.
        hits_ids = {p["id"] for p in hits_props}
        self.assertFalse(hits_ids & set(result["selected"]))

        # 4. Clearing the filter restores the full board.
        self.assertEqual(set(result["cleared"]), hr_ids | hits_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
