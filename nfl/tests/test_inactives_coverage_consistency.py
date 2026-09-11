#!/usr/bin/env python3
"""Consistency contract for the official NFL inactive-list coverage state.

The official raw surface is now a first-class capture target. Metadata elsewhere
in the capture plane must not continue to call that same raw source unresolved
or unavailable: doing so would create contradictory completeness evidence.

This test deliberately does NOT claim downstream per-game inactive parsing is
solved. It only protects the distinction:
    raw authoritative surface captured != semantic normalization complete.
"""
import inspect
import unittest

from nfl.archive import capture
from nfl.archive.sources import media_discovery, official_nfl


class OfficialInactiveCoverageConsistency(unittest.TestCase):
    def test_media_discovery_no_longer_calls_official_inactives_unavailable(self):
        artifacts = {artifact for artifact, *_ in media_discovery.NOT_ATTEMPTED}
        self.assertNotIn("official_inactives_list", artifacts)

    def test_capture_known_gaps_do_not_claim_authoritative_source_is_missing(self):
        source = inspect.getsource(capture.run_capture).lower()
        self.assertNotIn(
            "no authoritative machine-readable source for the official pregame",
            source,
        )
        self.assertIn("per-game", source)
        self.assertIn("inactive", source)

    def test_raw_official_inactives_surface_remains_first_class(self):
        pages = {artifact: url for artifact, url, _ in official_nfl.PAGES}
        self.assertEqual(
            pages.get("inactives"),
            "https://www.nfl.com/inactives/",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
