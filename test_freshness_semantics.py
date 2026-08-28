#!/usr/bin/env python3
"""Four clocks, stated separately.

A board carries four different ages and they are not interchangeable:

    model_basis_at        when the projections were computed
    lineups_observed_at   when anyone last LOOKED at the lineups
    market_prices_at      when the prices were read off FanDuel
    live_game_observed_at when the game itself was last checked

On 2026-08-28 all four were collapsed into generated_at (plus a price
stamp), and the freshness bar printed the friendly one. The board's model
basis was 10.1 hours old, the price overlay on top of it was 2 minutes
old, and the bar said "Board built 10 hours ago · odds updated 2 minutes
ago" -- both true, neither answering the question that mattered.

The clock with NO representation at all was lineups. Sal Stewart and Pete
Crow-Armstrong were still badged "lineup not confirmed" nine hours after
MLB's own API had posted both lineups for game 824638, and no field in
the payload could express "nobody has looked since 06:31".

lineups_observed_at is captured at the real fetch in
generate_picks._build_and_score, not at the end of the scoring pass -- an
end-of-pass stamp would report lineups as fresher than they are by the
duration of the pass, which is the wrong direction for a freshness claim.
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

FRESHNESS_KEYS = {"model_basis_at", "lineups_observed_at",
                  "market_prices_at", "live_game_observed_at"}


class TestPayloadCarriesFourClocks(unittest.TestCase):
    def test_build_dashboard_emits_all_four(self):
        with open(os.path.join(ROOT, "dashboard", "build_dashboard.py"), encoding="utf-8") as fh:
            src = fh.read()
        for key in FRESHNESS_KEYS:
            self.assertIn(f'"{key}"', src, f"{key} is never written by the builder")

    def test_freshness_survives_the_v3_payload_boundary(self):
        """A field computed in run_live_fetch and dropped in the v3 build is
        the same 'computed, then discarded' failure this codebase has now hit
        at three separate whitelists."""
        with open(os.path.join(ROOT, "dashboard", "build_dashboard.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"freshness": result.get("freshness")', src,
                      "the v3 payload does not carry freshness through")
        self.assertRegex(src, r'meta_keys = \{[^}]*"freshness"',
                         "freshness is not registered as a meta key, so it would "
                         "be mistaken for a prop family")

    def test_lineups_stamped_at_the_fetch_not_after_scoring(self):
        with open(os.path.join(ROOT, "generate_picks.py"), encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(
            r"lineups_observed_at = datetime\.now\(timezone\.utc\)\.isoformat\(\)\s*\n"
            r"\s*lineup_text, game_meta, player_ids = m\.fetch_lineups", src)
        self.assertIsNotNone(
            m, "lineups_observed_at must be captured immediately before "
               "fetch_lineups, not after the scoring pass")
        self.assertIn('"lineups_observed_at": lineups_observed_at', src,
                      "lineups_observed_at is never returned in ctx")


class TestFrontendResolvesEachClock(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
            self.js = fh.read()

    def test_board_clocks_resolves_all_four(self):
        self.assertIn("function boardClocks(", self.js)
        for key in FRESHNESS_KEYS:
            self.assertIn(key, self.js, f"{key} is never read by the frontend")

    def test_staleness_is_judged_on_the_model_basis(self):
        """Not on the price stamp. A 2-minute-old price on a 10-hour-old
        projection is the exact shape of the incident; judging freshness on
        the price would call that board fresh."""
        m = re.search(r"function boardFreshnessState\(nowMs, doc\) \{\s*\n\s*(.+)", self.js)
        self.assertIsNotNone(m)
        self.assertIn("model_basis_at", m.group(1),
                      "board age must be measured from the model basis")

    def test_bar_names_each_clock_for_what_it_measures(self):
        for label in ("Projections ", "lineups checked ", "odds ", "live scores "):
            self.assertIn(label, self.js, f"the freshness bar never states {label!r}")


class TestServedPayloadShape(unittest.TestCase):
    """The currently-checked-in docs/data.json predates this field. That is
    expected and must degrade gracefully rather than crash a consumer."""

    def test_frontend_falls_back_when_freshness_is_absent(self):
        with open(os.path.join(ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        m = re.search(r"function boardClocks\(doc\) \{(.*?)\n\}", js, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("doc.generated_at", body,
                      "no fallback for a payload built before freshness existed")

    def test_current_payload_is_still_readable(self):
        path = os.path.join(ROOT, "docs", "data.json")
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertIn("generated_at", payload)
        fresh = payload.get("freshness")
        if fresh is not None:
            self.assertEqual(set(fresh), FRESHNESS_KEYS,
                             "freshness block has drifted from the declared four clocks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
