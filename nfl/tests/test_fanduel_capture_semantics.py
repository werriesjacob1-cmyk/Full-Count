#!/usr/bin/env python3
"""Regression tests for two NFL-01 FanDuel capture defects found in live evidence.

1) A tab request can return HTTP 200 + a plausible payload that is merely the
   no-tab default. That is not CHECKED_AND_FOUND and not SOURCE_FAILED: bytes
   were preserved, but expected semantic coverage is ambiguous. It must be
   PARTIAL and non-conclusive.

2) Hard-coded slug guesses alone can miss newly exposed FanDuel tabs. Event
   layout titles must contribute discovered slugs, while empirical fallback
   slugs such as d-st and game-specials remain in the union because those were
   observed working even when omitted from the layout list.
"""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from nfl.archive import provenance  # noqa: E402
from nfl.archive.provenance import CHECKED_AND_FOUND, Fetched  # noqa: E402
from nfl.archive.sources import fanduel_nfl  # noqa: E402


def payload(markets=None, tabs=None):
    obj = {"attachments": {"markets": markets or {}}}
    if tabs is not None:
        obj["layout"] = {"tabs": tabs}
    return json.dumps(obj).encode("utf-8")


class PartialCoverageState(unittest.TestCase):
    def test_partial_exists_and_is_not_conclusive(self):
        self.assertTrue(hasattr(provenance, "PARTIAL"))
        self.assertIn(provenance.PARTIAL, provenance.OUTCOMES)
        self.assertNotIn(provenance.PARTIAL, provenance.CONCLUSIVE_OUTCOMES)

    def test_echoed_default_payload_is_partial_not_found(self):
        baseline = frozenset({"m1", "m2"})
        rec = Fetched(
            source_id="fanduel_nfl",
            artifact="event_1_tab_scoring",
            url="https://example.invalid",
            outcome=CHECKED_AND_FOUND,
            body=payload({"m1": {}, "m2": {}}),
            context={"tab": "scoring"},
        )
        out = fanduel_nfl._classify_tab_payload(rec, baseline)
        self.assertEqual(out.outcome, provenance.PARTIAL)
        self.assertTrue(out.context["tab_echoed_no_tab_baseline"])
        self.assertIn("silent", out.context["warning"].lower())

    def test_real_distinct_tab_remains_found(self):
        baseline = frozenset({"m1", "m2"})
        rec = Fetched(
            source_id="fanduel_nfl",
            artifact="event_1_tab_passing-props",
            url="https://example.invalid",
            outcome=CHECKED_AND_FOUND,
            body=payload({"p1": {}, "p2": {}}),
            context={"tab": "passing-props"},
        )
        out = fanduel_nfl._classify_tab_payload(rec, baseline)
        self.assertEqual(out.outcome, CHECKED_AND_FOUND)
        self.assertFalse(out.context["tab_echoed_no_tab_baseline"])

    def test_partial_surfaces_in_coverage_summary(self):
        rec = Fetched(
            source_id="fanduel_nfl",
            artifact="ambiguous",
            url="https://example.invalid",
            outcome=provenance.PARTIAL,
            body=b"{}",
            context={},
        )
        summary = provenance.coverage_summary([rec])
        self.assertEqual(summary["totals"][provenance.PARTIAL], 1)
        self.assertIn("fanduel_nfl", summary["sources_with_partial_observation"])
        self.assertIn("fanduel_nfl", summary["sources_with_no_conclusive_observation"])


class DynamicTabDiscovery(unittest.TestCase):
    def test_layout_dict_titles_are_slugged_and_unioned_with_fallbacks(self):
        body = payload(
            {"m": {}},
            tabs={
                "217": {"title": "Passing Props"},
                "991": {"title": "Kicking Props"},
                "1001": {"title": "Anytime Touchdown Scorer"},
            },
        )
        slugs = fanduel_nfl._tab_slugs_for_event(
            body, fallback_tabs=("d-st", "game-specials")
        )
        self.assertIn("passing-props", slugs)
        self.assertIn("kicking-props", slugs)
        self.assertIn("anytime-touchdown-scorer", slugs)
        self.assertIn("d-st", slugs)
        self.assertIn("game-specials", slugs)

    def test_layout_list_shape_is_supported(self):
        body = payload(
            {"m": {}},
            tabs=[
                {"title": "Receiving Props"},
                {"name": "Rushing Props"},
                {"label": "Player Specials"},
            ],
        )
        slugs = fanduel_nfl._tab_slugs_for_event(body, fallback_tabs=())
        self.assertEqual(
            set(slugs),
            {"receiving-props", "rushing-props", "player-specials"},
        )

    def test_layout_discovery_excludes_ui_and_period_tabs(self):
        body = payload(
            {"m": {}},
            tabs=[
                {"title": "Passing Props"},
                {"title": "Team Yards"},
                {"title": "1st Half"},
                {"title": "1st Quarter"},
                {"title": "Quick Bets"},
                {"title": "Parlays"},
                {"title": "Same Game Parlay"},
            ],
        )
        slugs = fanduel_nfl._tab_slugs_for_event(body, fallback_tabs=())
        self.assertIn("passing-props", slugs)
        self.assertIn("team-yards", slugs)
        self.assertNotIn("1st-half", slugs)
        self.assertNotIn("1st-quarter", slugs)
        self.assertNotIn("quick-bets", slugs)
        self.assertNotIn("parlays", slugs)
        self.assertNotIn("same-game-parlay", slugs)

    def test_numeric_tab_ids_are_never_used_as_tokens(self):
        body = payload(
            {"m": {}},
            tabs={
                "217": {"title": "Passing Props"},
                "300": {"title": "Receiving Props"},
            },
        )
        slugs = fanduel_nfl._tab_slugs_for_event(body, fallback_tabs=())
        self.assertNotIn("217", slugs)
        self.assertNotIn("300", slugs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
