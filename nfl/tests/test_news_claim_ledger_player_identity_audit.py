#!/usr/bin/env python3
"""Audit item 2: player-identity (GSIS id) resolution for the News Brain.

Confirms, by reading the actual code path rather than trusting a docstring,
that PR #146's real claims never infer a GSIS id from name alone (every
`player.gsis_id` on the real captured claims is `None`), and then tests
whether the already-existing, already-production-used
`nfl.normalize.inactive_roster_binding.bind_player` can safely resolve them
as a read-only downstream enrichment.

Fixtures are the real PR #146 claims (independently reproduced by this
audit workstream via a live `official_nfl.capture()` on 2026-09-19 --
`nfl/tests/fixtures/news_brain_real_captured_claims_2026-09-19.json`) and a
13-row subset of the real, live-refetched, digest-matched
`roster_2026.csv` release asset already used by the receptions/
passing-yards live-shadow workflows
(`nfl/tests/fixtures/nflverse_roster_2026_buf_det_subset.json`).
"""
import json
import unittest
from pathlib import Path

from nfl.intelligence.news_claim_ledger_player_identity_audit import (
    bind_claims_to_roster,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CLAIMS = json.loads(
    (FIXTURE_DIR / "news_brain_real_captured_claims_2026-09-19.json").read_text()
)["claims"]
ROSTER_ROWS = json.loads(
    (FIXTURE_DIR / "nflverse_roster_2026_buf_det_subset.json").read_text()
)["roster_rows"]


class RealClaimsNeverCarryAnInferredGsisIdTests(unittest.TestCase):
    def test_every_real_captured_claim_has_gsis_id_none(self):
        self.assertEqual(len(CLAIMS), 13)
        for claim in CLAIMS:
            self.assertIsNone(claim["player"]["gsis_id"])


class RealRosterBindingCountsTests(unittest.TestCase):
    def test_all_13_real_claims_resolve_unambiguously_against_the_real_roster(self):
        result = bind_claims_to_roster(CLAIMS, ROSTER_ROWS, season=2026)
        self.assertEqual(result["claim_count"], 13)
        self.assertEqual(result["bound_count"], 13)
        self.assertEqual(result["ambiguous_count"], 0)
        self.assertEqual(result["unmatched_count"], 0)
        # Spot-check one real row end to end.
        blake = next(r for r in result["rows"] if r["player_name"] == "Blake Miller")
        self.assertEqual(blake["binding_status"], "BOUND")
        self.assertEqual(blake["gsis_id"], "00-0041439")
        self.assertEqual(blake["team"], "DET")

    def test_no_claim_is_silently_dropped_even_when_unresolved(self):
        # Construct one genuinely unresolvable claim (name not on the real
        # roster subset) and confirm it is still reported, not dropped.
        unmatched_claim = dict(CLAIMS[0])
        unmatched_claim = {
            **unmatched_claim,
            "claim_id": "nc_test_unmatched_player",
            "player": {
                **unmatched_claim["player"],
                "player_name": "Nobody On This Roster",
            },
        }
        result = bind_claims_to_roster(
            CLAIMS + [unmatched_claim], ROSTER_ROWS, season=2026
        )
        self.assertEqual(result["claim_count"], 14)
        self.assertEqual(result["bound_count"], 13)
        self.assertEqual(result["unmatched_count"], 1)
        row = next(
            r for r in result["rows"] if r["claim_id"] == "nc_test_unmatched_player"
        )
        self.assertIsNone(row["gsis_id"])

    def test_real_ambiguous_name_collision_is_reported_ambiguous_not_guessed(self):
        # Construct a genuine name/team/position collision by duplicating a
        # real roster row under a different gsis_id, on purpose, to prove
        # the binder refuses to guess between two same-name-team-position
        # candidates.
        blake_row = next(
            r for r in ROSTER_ROWS if r["full_name"] == "Blake Miller"
        )
        duplicate = {**blake_row, "gsis_id": "00-0099999", "esb_id": "FAKE000000"}
        collided_roster = ROSTER_ROWS + [duplicate]
        result = bind_claims_to_roster(CLAIMS, collided_roster, season=2026)
        self.assertEqual(result["ambiguous_count"], 1)
        self.assertEqual(result["bound_count"], 12)
        blake = next(r for r in result["rows"] if r["player_name"] == "Blake Miller")
        self.assertEqual(blake["binding_status"], "AMBIGUOUS_PLAYER")
        self.assertIsNone(blake["gsis_id"])


if __name__ == "__main__":
    unittest.main()
