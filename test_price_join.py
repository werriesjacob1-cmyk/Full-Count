#!/usr/bin/env python3
"""Tests for backtest/price_join.py.

The load-bearing one is lookahead. A price quoted after first pitch knows
the outcome, and joining one would manufacture a spectacular fake edge on
exactly the longshot markets this work targets -- a +6500 triple is
marked down the instant the batter singles. So in-play exclusion is
tested as an invariant that RAISES, not as a filter that can be forgotten.

Name matching is measured against the real archive rather than asserted,
because a normalizer that silently fails to match is indistinguishable
from a market we do not price.
"""
import glob
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import price_join as pj

HAVE_ARCHIVE = bool(glob.glob(os.path.join(pj.PROPS_DIR, "props_*.json")))


def _q(taken, start, **kw):
    r = {"player_norm": "test player", "stat": "hits", "needs": 1,
         "american": 150, "taken_at": taken, "start_time": start,
         "in_play": False}
    r.update(kw)
    return r


def _fixture(tmp, day="2026-08-20", snaps=None):
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, f"props_{day}.json"), "w") as fh:
        json.dump({"date": day, "snapshots": snaps or []}, fh)
    return tmp


class TestLookaheadInvariant(unittest.TestCase):
    def test_in_play_flag_raises(self):
        with self.assertRaises(pj.LookaheadError):
            pj.assert_no_inplay([_q("2026-08-20T18:00:00+00:00",
                                    "2026-08-20T23:00:00+00:00", in_play=True)])

    def test_quote_after_first_pitch_raises_even_without_the_flag(self):
        """The flag agreed with timestamps on 1,023,166 archived rows, but
        agreement is not a guarantee -- both are checked."""
        with self.assertRaises(pj.LookaheadError) as ctx:
            pj.assert_no_inplay([_q("2026-08-21T01:00:00+00:00",
                                    "2026-08-20T23:00:00+00:00", in_play=False)])
        self.assertIn("already knows what happened", str(ctx.exception))

    def test_quote_exactly_at_first_pitch_raises(self):
        t = "2026-08-20T23:00:00+00:00"
        with self.assertRaises(pj.LookaheadError):
            pj.assert_no_inplay([_q(t, t, in_play=False)])

    def test_pregame_quote_passes(self):
        self.assertTrue(pj.assert_no_inplay(
            [_q("2026-08-20T18:00:00+00:00", "2026-08-20T23:00:00+00:00")]))

    def test_index_never_selects_an_in_play_quote(self):
        """Even when the in-play quote is the LATEST one -- which is the
        case that would otherwise win under last_pregame."""
        with tempfile.TemporaryDirectory() as td:
            d = _fixture(td, snaps=[{"taken_at": "x", "rows": [
                _q("2026-08-20T18:00:00+00:00", "2026-08-20T23:00:00+00:00", american=150),
                _q("2026-08-21T01:00:00+00:00", "2026-08-20T23:00:00+00:00",
                   american=9999, in_play=True)]}])
            idx, rep = pj.load_price_index(props_dir=d)
            self.assertEqual(rep["dropped_inplay"], 1)
            self.assertEqual(len(idx), 1)
            self.assertEqual(next(iter(idx.values()))["american"], 150)


class TestQuoteRule(unittest.TestCase):
    def _two(self, td):
        return _fixture(td, snaps=[{"taken_at": "x", "rows": [
            _q("2026-08-20T12:00:00+00:00", "2026-08-20T23:00:00+00:00", american=100),
            _q("2026-08-20T22:00:00+00:00", "2026-08-20T23:00:00+00:00", american=250)]}])

    def test_last_pregame_is_the_default(self):
        with tempfile.TemporaryDirectory() as td:
            idx, _ = pj.load_price_index(props_dir=self._two(td))
            self.assertEqual(next(iter(idx.values()))["american"], 250)

    def test_opening_is_available_for_a_declared_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            idx, _ = pj.load_price_index(props_dir=self._two(td), quote_rule=pj.OPENING)
            self.assertEqual(next(iter(idx.values()))["american"], 100)

    def test_there_is_no_best_price_rule(self):
        """A post-hoc maximiser must not be reachable by passing a string."""
        self.assertNotIn("best", " ".join(pj.QUOTE_RULES))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(pj.PriceJoinError):
                pj.load_price_index(props_dir=self._two(td), quote_rule="best")


class TestPricingMath(unittest.TestCase):
    def test_american_to_decimal(self):
        self.assertAlmostEqual(pj.american_to_decimal(100), 2.0)
        self.assertAlmostEqual(pj.american_to_decimal(400), 5.0)
        self.assertAlmostEqual(pj.american_to_decimal(-200), 1.5)

    def test_implied_prob(self):
        self.assertAlmostEqual(pj.implied_prob(100), 0.5)
        self.assertAlmostEqual(pj.implied_prob(300), 0.25)
        self.assertAlmostEqual(pj.implied_prob(-300), 0.75)

    def test_roi_is_the_number_hit_rate_cannot_give(self):
        """20% at +400 wins; 20% at +300 loses. Same hit rate."""
        # +450 implies 18.2%, so 20% is genuinely profitable. An earlier
        # version used +400, which implies exactly 20% -- break-even, not a
        # win. The module was right; the test's arithmetic was not.
        good = [{"outcome": i < 2, "decimal": 5.5, "american": 450,
                 "posted_implied": 0.1818} for i in range(10)]
        bad = [{"outcome": i < 2, "decimal": 4.0, "american": 300,
                "posted_implied": 0.25} for i in range(10)]
        g, b = pj.realized_roi(good), pj.realized_roi(bad)
        self.assertEqual(g["hit_rate"], b["hit_rate"])
        self.assertGreater(g["roi"], 0)
        self.assertLess(b["roi"], 0)

    def test_breakeven_is_reported(self):
        rows = [{"outcome": True, "decimal": 4.0, "american": 300,
                 "posted_implied": 0.25}]
        self.assertEqual(pj.realized_roi(rows)["breakeven_hit_rate"], 0.25)


class TestJoin(unittest.TestCase):
    def test_unmatched_rows_are_returned_not_dropped(self):
        idx = {("2026-08-20", "real player", "hits", 1):
               {"american": 150, "taken_at": "t", "two_sided": False}}
        rows = [{"date": "2026-08-20", "player_name": "Real Player",
                 "prop_type": "hits", "needs": 1, "outcome": 1},
                {"date": "2026-08-20", "player_name": "Nobody",
                 "prop_type": "hits", "needs": 1, "outcome": 0}]
        m, u = pj.join_rows(rows, idx)
        self.assertEqual(len(m), 1)
        self.assertEqual(len(u), 1)

    def test_home_run_alias(self):
        idx = {("2026-08-20", "aaron judge", "home_runs", 1):
               {"american": 350, "taken_at": "t", "two_sided": False}}
        m, u = pj.join_rows([{"date": "2026-08-20", "player_name": "Aaron Judge",
                              "prop_type": "home_run", "needs": 1, "outcome": 1}], idx)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["american"], 350)

    def test_accents_are_folded(self):
        self.assertEqual(pj.normalize_player("Ronald Acuña"), "ronald acuna")
        self.assertEqual(pj.normalize_player("J.T. Realmuto"), "jt realmuto")


@unittest.skipUnless(HAVE_ARCHIVE, "data/props archive not present")
class TestAgainstRealArchive(unittest.TestCase):
    def test_real_archive_loads_and_drops_in_play(self):
        idx, rep = pj.load_price_index(dates=["2026-08-25"])
        self.assertGreater(rep["priced_props"], 1000)
        self.assertGreater(rep["dropped_inplay"], 0)

    def test_no_selected_quote_is_in_play(self):
        idx, _ = pj.load_price_index(dates=["2026-08-25"])
        pj.assert_no_inplay([dict(q, player_norm="x", stat="y") for q in idx.values()])

    def test_normalizer_matches_the_archives_own_player_norm(self):
        """Measured, not assumed: the archive stores both raw player and
        its own player_norm, so the normalizer can be checked directly."""
        path = os.path.join(pj.PROPS_DIR, "props_2026-08-25.json")
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
        pairs = {(r["player"], r["player_norm"])
                 for s in blob["snapshots"] for r in s["rows"]}
        agree = sum(1 for raw, norm in pairs if pj.normalize_player(raw) == norm)
        rate = agree / len(pairs)
        self.assertGreaterEqual(
            rate, 1.0,
            f"normalizer must reproduce the archive.s own "
            f"player_norm ({agree}/{len(pairs)}) -- unmatched names look "
            f"identical to unpriced markets")

    def test_two_sided_pitcher_quotes_are_included(self):
        idx, rep = pj.load_price_index(dates=["2026-08-25"])
        self.assertGreater(rep["two_sided_rows"], 0)
        self.assertTrue(any(k[2] in ("strikeouts", "pitcher_outs") for k in idx))


if __name__ == "__main__":
    unittest.main(verbosity=2)
