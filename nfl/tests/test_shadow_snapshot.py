#!/usr/bin/env python3
"""Contracts for immutable-style prospective NFL shadow snapshots."""
import copy
import unittest

from nfl.prospective import shadow_snapshot


BASE = {
    "event_id": "999",
    "market_id": "734.1",
    "gsis_id": "00-0036389",
    "market": "passing_yards",
    "player_name": "Jalen Hurts",
    "team": "PHI",
    "line": 214.5,
    "over_odds": -114,
    "under_odds": -114,
    "captured_at": "2026-09-13T16:50:00Z",
    "decision_status": "SHADOW_ONLY",
    "availability_status": "NOT_LISTED_INACTIVE",
}


class ShadowSnapshotTests(unittest.TestCase):
    def test_live_freeze_cannot_cross_kickoff(self):
        row = {**BASE, 'event_open_date': '2026-09-13T17:00:00Z'}
        shadow_snapshot.validate_pregame_timing([row], '2026-09-13T16:50:01Z')
        for sealed in ('2026-09-13T17:00:00Z', '2026-09-13T17:00:01Z', '2026-09-13T16:49:00Z', '2026-09-13T16:50:01'):
            with self.subTest(sealed=sealed), self.assertRaises(ValueError):
                shadow_snapshot.validate_pregame_timing([row], sealed)

    def test_observation_id_is_deterministic(self):
        a = shadow_snapshot.observation_id(BASE)
        b = shadow_snapshot.observation_id(dict(reversed(list(BASE.items()))))
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("fcnfl-shadow1:"))

    def test_snapshot_hash_is_deterministic_across_input_order(self):
        r1 = {**BASE, "player_name": "Jalen Hurts", "gsis_id": "00-1"}
        r2 = {**BASE, "player_name": "Patrick Mahomes", "gsis_id": "00-2"}
        a = shadow_snapshot.seal_snapshot(
            [r1, r2],
            slate_date="2026-09-13",
            code_sha="abc",
            source_vintage="pre-lock",
            sealed_at="2026-09-13T16:50:01Z",
        )
        b = shadow_snapshot.seal_snapshot(
            [r2, r1],
            slate_date="2026-09-13",
            code_sha="abc",
            source_vintage="pre-lock",
            sealed_at="2026-09-13T16:50:01Z",
        )
        self.assertEqual(a["snapshot_sha256"], b["snapshot_sha256"])
        self.assertEqual(a["records"], b["records"])

    def test_pregame_snapshot_forbids_outcome_fields(self):
        bad = {**BASE, "actual": 300}
        with self.assertRaisesRegex(ValueError, "outcome"):
            shadow_snapshot.seal_snapshot(
                [bad],
                slate_date="2026-09-13",
                code_sha="abc",
                source_vintage="pre-lock",
                sealed_at="2026-09-13T16:50:01Z",
            )

    def test_only_shadow_or_quarantined_decisions_allowed(self):
        bad = {**BASE, "decision_status": "PLAY"}
        with self.assertRaisesRegex(ValueError, "decision_status"):
            shadow_snapshot.seal_snapshot(
                [bad],
                slate_date="2026-09-13",
                code_sha="abc",
                source_vintage="pre-lock",
                sealed_at="2026-09-13T16:50:01Z",
            )

    def test_duplicate_observation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            shadow_snapshot.seal_snapshot(
                [BASE, copy.deepcopy(BASE)],
                slate_date="2026-09-13",
                code_sha="abc",
                source_vintage="pre-lock",
                sealed_at="2026-09-13T16:50:01Z",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
