#!/usr/bin/env python3
"""Contract for authoritative NFL inactive-list archival.

NFL Football Operations says clubs exchange inactive lists at the 90-minute
officiating meeting. NFL.com exposes a public /inactives/ surface. FULL COUNT
must archive that official surface directly rather than treating an aggregator
as authoritative.
"""
import unittest

from nfl.archive.sources import official_nfl


class OfficialInactiveSurface(unittest.TestCase):
    def test_official_inactives_page_is_a_first_class_capture_artifact(self):
        rows = {
            artifact: (url, carries)
            for artifact, url, carries in official_nfl.PAGES
        }
        self.assertIn("inactives", rows)
        url, carries = rows["inactives"]
        self.assertEqual(url, "https://www.nfl.com/inactives/")
        self.assertIn("inactive", carries.lower())

    def test_inactives_are_not_mislabeled_as_injury_report(self):
        artifacts = [artifact for artifact, _, _ in official_nfl.PAGES]
        self.assertEqual(artifacts.count("inactives"), 1)
        self.assertIn("injuries_report", artifacts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
