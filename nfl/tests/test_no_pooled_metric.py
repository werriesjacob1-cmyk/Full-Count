#!/usr/bin/env python3
"""A performance metric must refuse mixed MLB/NFL rows. Enforcement 4.

Every test here is also the mutation: it hands a real mixed collection to a
gated metric and requires the refusal. See nfl/sport_partition.py for why this
is a refusal rather than a warning.
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from nfl.identity import nfl_prop_id  # noqa: E402
from nfl.sport_partition import (  # noqa: E402
    MLB, NFL, PooledSportsError, UnknownSportError, assert_single_sport,
    partition_by_sport, sport_of, sports_present,
)

MLB_ROW = {"id": "fc2:824234:player-690993:hits:1:over", "hit": True}
MLB_ROW_2 = {"id": "fc2:824234:player-691000:hits:1:over", "hit": False}
NFL_ROW = {"id": nfl_prop_id("nflverse", "2026_01_TB_CIN", "player-1",
                             "receiving_yards", 64.5, "over"), "hit": True}


def hit_rate(rows, administrative=False, reason=""):
    """A representative gated performance metric."""
    assert_single_sport(rows, "hit_rate", administrative, reason)
    rows = list(rows)
    return (sum(1 for r in rows if r.get("hit")) / len(rows)) if rows else None


class SportDetection(unittest.TestCase):
    def test_identity_namespace_determines_sport(self):
        self.assertEqual(sport_of(MLB_ROW), MLB)
        self.assertEqual(sport_of(NFL_ROW), NFL)

    def test_declared_sport_is_honoured_when_consistent(self):
        self.assertEqual(sport_of({**NFL_ROW, "sport": "nfl"}), NFL)

    def test_declared_sport_contradicting_the_identity_raises(self):
        """A mislabelled row must not be silently assigned to either sport."""
        with self.assertRaises(UnknownSportError) as caught:
            sport_of({**MLB_ROW, "sport": "nfl"})
        self.assertIn("mislabelled", str(caught.exception))

    def test_undeterminable_sport_fails_closed(self):
        for row in ({"id": "something-else"}, {}, {"id": None}):
            with self.subTest(row=row):
                with self.assertRaises(UnknownSportError):
                    sport_of(row)

    def test_non_mapping_row_raises(self):
        with self.assertRaises(UnknownSportError):
            sport_of(["not", "a", "row"])


class PoolingIsRefused(unittest.TestCase):
    def test_MUTATION_mixed_mlb_and_nfl_rows_raise(self):
        with self.assertRaises(PooledSportsError) as caught:
            hit_rate([MLB_ROW, NFL_ROW])
        message = str(caught.exception)
        self.assertIn("hit_rate", message)
        self.assertIn("mlb", message)
        self.assertIn("nfl", message)
        # The refusal must teach why, not merely refuse.
        self.assertIn("0.748", message)
        self.assertIn("0.492", message)

    def test_single_sport_rows_compute_normally(self):
        self.assertEqual(hit_rate([MLB_ROW, MLB_ROW_2]), 0.5)
        self.assertEqual(hit_rate([NFL_ROW]), 1.0)

    def test_empty_rows_do_not_raise(self):
        self.assertIsNone(hit_rate([]))

    def test_one_nfl_row_hidden_among_many_mlb_rows_still_raises(self):
        """The dangerous shape: pooling that looks almost single-sport."""
        rows = [dict(MLB_ROW, id=f"fc2:1:player-{i}:hits:1:over") for i in range(200)]
        rows.append(NFL_ROW)
        with self.assertRaises(PooledSportsError):
            hit_rate(rows)

    def test_administrative_operations_may_span_sports_with_a_reason(self):
        total = len([MLB_ROW, NFL_ROW])
        self.assertIsNone(assert_single_sport(
            [MLB_ROW, NFL_ROW], "row_count", administrative=True,
            reason="counting rows for an integrity check; asserts nothing about accuracy",
        ))
        self.assertEqual(total, 2)

    def test_administrative_without_a_reason_is_refused(self):
        """The escape hatch must not be usable silently."""
        with self.assertRaises(ValueError) as caught:
            assert_single_sport([MLB_ROW, NFL_ROW], "row_count", administrative=True)
        self.assertIn("requires a reason", str(caught.exception))

    def test_partition_lets_each_sport_be_measured_on_its_own_terms(self):
        parts = partition_by_sport([MLB_ROW, MLB_ROW_2, NFL_ROW])
        self.assertEqual(sorted(parts), [MLB, NFL])
        self.assertEqual(hit_rate(parts[MLB]), 0.5)
        self.assertEqual(hit_rate(parts[NFL]), 1.0)

    def test_sports_present_reports_both(self):
        self.assertEqual(sports_present([MLB_ROW, NFL_ROW]), {MLB, NFL})


class HonestyAboutWiring(unittest.TestCase):
    def test_module_states_it_is_not_wired_into_mlb(self):
        """Prevents the docstring from later implying coverage it lacks."""
        with open(os.path.join(REPO, "nfl", "sport_partition.py"), encoding="utf-8") as fh:
            doc = fh.read()
        # Whitespace-normalised: these phrases are line-wrapped in the source,
        # and a raw substring check fails on the newline rather than on the
        # claim. The first version of this test did exactly that.
        flat = " ".join(doc.split())
        self.assertIn("deliberately NOT wired into MLB", flat)
        self.assertIn("there are no NFL rows anywhere yet", flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
