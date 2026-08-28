#!/usr/bin/env python3
"""LINE_MOVED: the book posts this subject, just not at our number.

Real incident, 2026-08-28. Drew Anderson's board row read

    "Over 11.5 Outs Recorded"   market_fetch_state = NOT_POSTED
                                estimated_odds     = -485

at the same moment FanDuel was posting

    Drew Anderson - Outs Recorded - Over 14.5 at -132

The row was built at 06:31 UTC, before FanDuel had posted any outs market
for him, so score_pitcher_outs correctly fell back to a model-anchored
threshold (his average workload) and correctly flagged it. What was missing
is what happens NEXT: refresh_prices re-prices the row every cycle, but
attach_market_prices matches on the exact published threshold, so needs=12
against a book offering needs=15 simply fails to match -- and every
non-match was reported as NOT_POSTED.

NOT_POSTED means "the book offers nothing here." That was false. The book
offered plenty; it just wasn't our number. The distinction matters because
the two failures need opposite responses: a genuine absence is nothing to
act on, while a line that has moved off ours means the price we display
cannot be bought at the line we display.

The fix deliberately does NOT re-point the row at the book's line. A
different threshold is a different prediction carrying a different
probability, and silently migrating one would let the board be graded on a
bet it never actually made.
"""
import copy
import unittest

import odds_fanduel as fd
import dashboard.refresh_prices as rp
from dashboard.live_state import PRICE_FIELDS


def po_row(needs=12, name="Drew Anderson"):
    return {"type": "pitcher", "name": name, "stat": "pitcher_outs",
            "prop": f"Over {needs - 0.5} Outs Recorded",
            "projection": {"stat": "pitcher_outs", "value": needs - 0.5, "needs": needs},
            "matchup": "Los Angeles Dodgers @ Detroit Tigers"}


# Exact shape returned by fetch_pitcher_outs(), captured live 2026-08-28.
PO_VALUES = {"drew anderson": {
    "player": "Drew Anderson", "line": 14.5, "needs": 15,
    "over": -132, "under": -102, "true_over": 0.5298, "true_under": 0.4702,
    "hold": 0.0739, "game": "Los Angeles Dodgers (T Skubal) @ Detroit Tigers (D Anderson)"}}


class TestTheRealIncident(unittest.TestCase):
    def test_our_line_absent_but_book_posts_this_pitcher(self):
        moved = fd.posted_line_for_subject("pitcher_outs", PO_VALUES, po_row(12))
        self.assertIsNotNone(moved, "book posts Drew Anderson; this is not an absence")
        self.assertEqual(moved["our_needs"], 12)
        self.assertEqual(moved["posted_needs"], [15])
        self.assertEqual(moved["posted_line"], 14.5)
        self.assertEqual(moved["posted_over"], -132)

    def test_exact_match_is_not_a_mismatch(self):
        self.assertIsNone(fd.posted_line_for_subject("pitcher_outs", PO_VALUES, po_row(15)))

    def test_absent_pitcher_is_a_true_absence_not_a_moved_line(self):
        row = po_row(12, name="Nobody Atall")
        self.assertIsNone(fd.posted_line_for_subject("pitcher_outs", PO_VALUES, row),
                          "a pitcher the book never posted must stay NOT_POSTED")


class TestStateSelection(unittest.TestCase):
    """The branch refresh_prices actually takes, exercised directly."""

    def _state(self, values, row, matched=False, absence_proven=True):
        if matched:
            return "MATCHED"
        if not absence_proven:
            return "FETCH_FAILED"
        return "LINE_MOVED" if fd.posted_line_for_subject(
            rp._market_family(row), values, row) else "NOT_POSTED"

    def test_moved_line_reports_line_moved(self):
        self.assertEqual(self._state(PO_VALUES, po_row(12)), "LINE_MOVED")

    def test_true_absence_still_reports_not_posted(self):
        self.assertEqual(self._state({}, po_row(12)), "NOT_POSTED")

    def test_failed_fetch_outranks_both(self):
        self.assertEqual(
            self._state(PO_VALUES, po_row(12), absence_proven=False), "FETCH_FAILED")

    def test_line_moved_is_a_declared_observation_state(self):
        self.assertIn("LINE_MOVED", rp.OBSERVATION_STATES)


class TestEvidenceSurvivesTheLiveBoundary(unittest.TestCase):
    """An unregistered field is computed and then silently dropped.

    live_state.py already carries two separate comment blocks about this
    exact failure. A LINE_MOVED row whose posted-line evidence never
    reaches live.json is worse than no state change at all: it claims the
    line moved and cannot say to what.
    """

    def test_mismatch_fields_are_registered_for_the_overlay(self):
        for field in rp.LINE_MISMATCH_FIELDS:
            self.assertIn(field, PRICE_FIELDS, f"{field} would be dropped at the overlay")

    def test_mismatch_fields_are_written_by_the_refresh(self):
        for field in rp.LINE_MISMATCH_FIELDS:
            self.assertIn(field, rp.LIVE_FIELDS)

    def test_stale_mismatch_is_cleared_before_each_attempt(self):
        """A line that moves BACK onto ours must not leave the old mismatch."""
        row = po_row(15)
        row["market_posted_line"] = 14.5
        row["market_posted_needs"] = [15]
        row["market_posted_over"] = -132
        working = copy.deepcopy(row)
        for field in rp.MARKET_VALUE_FIELDS:
            working[field] = None
        for field in rp.LINE_MISMATCH_FIELDS:
            working[field] = None
        _, matched = fd.attach_market_prices(
            [working], **rp._family_args("pitcher_outs", PO_VALUES))
        self.assertTrue(matched)
        for field in rp.LINE_MISMATCH_FIELDS:
            self.assertIsNone(working[field],
                              f"{field} survived a now-matching re-price")


class TestNoSilentLineMigration(unittest.TestCase):
    def test_the_published_threshold_is_never_rewritten(self):
        """Grading integrity: we may report the book's line, never adopt it."""
        row = po_row(12)
        before = copy.deepcopy(row)
        fd.posted_line_for_subject("pitcher_outs", PO_VALUES, row)
        self.assertEqual(row["projection"], before["projection"])
        self.assertEqual(row["prop"], before["prop"])


class TestOtherFamilies(unittest.TestCase):
    def test_strikeouts_share_the_two_sided_shape(self):
        values = {"jackson kent": {"player": "Jackson Kent", "line": 3.5, "needs": 4,
                                   "over": -170, "under": 130}}
        row = {"name": "Jackson Kent", "stat": "strikeouts",
               "projection": {"stat": "strikeouts", "needs": 6}}
        moved = fd.posted_line_for_subject("strikeouts", values, row)
        self.assertEqual(moved["posted_needs"], [4])

    def test_batter_ladder_only_counts_rungs_of_the_same_stat(self):
        """A posted HITS rung is not evidence a DOUBLES market exists."""
        values = {"some batter": {("hits", 1): -150, ("hits", 2): 320}}
        doubles = {"name": "Some Batter", "stat": "doubles",
                   "projection": {"stat": "doubles", "needs": 1}}
        self.assertIsNone(fd.posted_line_for_subject("general_batter", values, doubles))
        hits = {"name": "Some Batter", "stat": "hits",
                "projection": {"stat": "hits", "needs": 3}}
        moved = fd.posted_line_for_subject("general_batter", values, hits)
        self.assertEqual(moved["posted_needs"], [1, 2])

    def test_first_inning_has_no_ladder_to_move_along(self):
        row = {"matchup": "A @ B", "stat": "nrfi_combined",
               "projection": {"stat": "nrfi_combined", "needs": 1}}
        self.assertIsNone(fd.posted_line_for_subject("first_inning", {"A @ B": {}}, row))


class TestWhitelistParity(unittest.TestCase):
    """app.js keeps a HAND-SYNCED copy of live_state.PRICE_FIELDS.

    Three whitelists stand between a computed field and the customer:
    refresh_prices.LIVE_FIELDS, live_state.PRICE_FIELDS, and app.js's
    LIVE_PRICE_FIELDS. The first two are Python and at least visible to
    each other. The third is a hand-maintained duplicate in another
    language, and its own comment admits it ("kept in sync ... by hand").
    Missing it has exactly one symptom: the backend is right, the overlay
    is right, and the browser silently never sees the field.

    Asserting the sets match turns the next omission into a failing test
    instead of a field that disappears at the last boundary.
    """

    def test_app_js_live_price_fields_match_live_state(self):
        import os
        import re
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dashboard", "static", "app.js")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        block = re.search(r"const LIVE_PRICE_FIELDS = new Set\(\[(.*?)\]\);",
                          source, re.S)
        self.assertIsNotNone(block, "LIVE_PRICE_FIELDS block not found in app.js")
        # Strip // comments before harvesting the quoted names.
        body = re.sub(r"//[^\n]*", "", block.group(1))
        js_fields = set(re.findall(r'"([^"]+)"', body))
        self.assertEqual(js_fields, set(PRICE_FIELDS),
                         "app.js LIVE_PRICE_FIELDS has drifted from "
                         "live_state.PRICE_FIELDS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
