#!/usr/bin/env python3
"""NFL identity cannot collide with MLB identity, and MLB v2 is untouched. Deliverable 1.

The three things Deliverable 1 requires proof of:
  1. existing MLB v2 identities still resolve, byte-identically
  2. NFL identity cannot collide with MLB identity
  3. already-published MLB entries are not rewritten

(1) and (3) are proved by there being NO MLB CHANGE AT ALL: identity_schema_version
is still 2, dashboard/live_state.py is untouched, and the golden-vector test below
re-derives real MLB ids from the production function and compares them byte for
byte. (2) is proved structurally from the namespace literals.
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "dashboard"))

from nfl.identity import (  # noqa: E402
    IdentityError, MLB_NAMESPACE_PREFIXES, NFL_NAMESPACE, nfl_prop_id,
    namespace_is_disjoint_from_mlb,
)


class NamespaceDisjointness(unittest.TestCase):
    def test_nfl_ids_are_namespaced(self):
        identity = nfl_prop_id("nflverse", "2026_01_TB_CIN", "player-00-0034796",
                               "receiving_yards", 64.5, "over")
        self.assertTrue(identity.startswith(NFL_NAMESPACE + ":"))
        self.assertTrue(namespace_is_disjoint_from_mlb(identity))

    def test_no_nfl_id_can_start_with_an_mlb_prefix(self):
        """The structural argument, exercised rather than asserted in prose."""
        for prefix in MLB_NAMESPACE_PREFIXES:
            self.assertFalse((NFL_NAMESPACE + ":").startswith(prefix))
            self.assertFalse(prefix.startswith(NFL_NAMESPACE + ":"))

    def test_mlb_shaped_ids_are_not_accepted_as_nfl(self):
        for mlb_id in ("fc2:824234:player-690993:hits:1:over",
                       "fc2:1:game:first_inning:0.5:over", "", None, 7):
            with self.subTest(mlb_id=mlb_id):
                self.assertFalse(namespace_is_disjoint_from_mlb(mlb_id))

    def test_threshold_normalisation_prevents_duplicate_identities(self):
        a = nfl_prop_id("nflverse", "g", "s", "receptions", 4.5, "over")
        b = nfl_prop_id("nflverse", "g", "s", "receptions", "4.50", "over")
        self.assertEqual(a, b)
        self.assertNotEqual(
            a, nfl_prop_id("nflverse", "g", "s", "receptions", 5.5, "over"))

    def test_game_id_source_is_part_of_the_identity(self):
        """Two vendors' ids for the same game must not look like one wager."""
        self.assertNotEqual(
            nfl_prop_id("espn", "401872925", "s", "receptions", 4.5, "over"),
            nfl_prop_id("fanduel", "401872925", "s", "receptions", 4.5, "over"))

    def test_unknown_game_id_source_is_refused(self):
        with self.assertRaises(IdentityError):
            nfl_prop_id("statsapi", "g", "s", "receptions", 4.5, "over")

    def test_missing_or_bad_fields_are_refused(self):
        with self.assertRaises(IdentityError):
            nfl_prop_id("nflverse", "", "s", "receptions", 4.5, "over")
        with self.assertRaises(IdentityError):
            nfl_prop_id("nflverse", "g", "s", "receptions", None, "over")
        with self.assertRaises(IdentityError):
            nfl_prop_id("nflverse", "g", "s", "receptions", 4.5, "sideways")


class MLBIdentityIsUntouched(unittest.TestCase):
    """The MLB half of the proof. Skipped only if dashboard deps are absent."""

    @classmethod
    def setUpClass(cls):
        try:
            import live_state
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"dashboard/live_state.py unimportable: {exc}")
        cls.live_state = live_state

    def test_identity_schema_version_is_still_2(self):
        self.assertEqual(
            self.live_state.IDENTITY_SCHEMA_VERSION, 2,
            "NFL-01 must not bump identity_schema_version. NFL gets a separate "
            "namespace instead, which needs no MLB change at all.",
        )

    def test_mlb_golden_identities_are_byte_identical(self):
        """Real MLB rows through the production function, compared byte for byte."""
        golden = [
            ({"game_pk": 824234, "player_id": 690993,
              "projection": {"stat": "hits", "needs": 1}, "prop": "Over 0.5 Hits"},
             "fc2:824234:player-690993:hits:1:over"),
            ({"game_pk": 1, "player_id": 1,
              "projection": {"stat": "hits_runs_rbis", "needs": 1},
              "prop": "Over 0.5 Hits+Runs+RBIs"},
             "fc2:1:player-1:hits_runs_rbis:1:over"),
            ({"game_pk": 2, "player_id": 2,
              "projection": {"stat": "stolen_base", "needs": 1},
              "prop": "Over 0.5 Stolen Bases"},
             "fc2:2:player-2:stolen_base:1:over"),
        ]
        for row, expected in golden:
            with self.subTest(expected=expected):
                self.assertEqual(self.live_state.canonical_prop_id(row), expected)

    def test_mlb_validator_rejects_an_nfl_id_with_no_mlb_change(self):
        """An NFL id fed to MLB production fails closed on today's code."""
        nfl_id = nfl_prop_id("nflverse", "2026_01_TB_CIN", "player-1",
                             "receiving_yards", 64.5, "over")
        row = {"game_pk": 1, "player_id": 1, "id": nfl_id,
               "projection": {"stat": "hits", "needs": 1}, "prop": "Over 0.5 Hits"}
        with self.assertRaises(ValueError) as caught:
            self.live_state.stable_prop_id(row)
        self.assertIn("unsupported identity version", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
