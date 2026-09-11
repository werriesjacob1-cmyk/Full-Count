#!/usr/bin/env python3
"""Coordinator identity is obtainable and point-in-time. Offline tests only.

These run in CI, so they must not depend on Wikipedia being reachable. The live
measurements are recorded in nfl/docs/PLAY_CALLER.md; what is locked in here is
the CONTRACT: URL construction, the as-of semantics, and the two claims that are
easiest to quietly weaken later -- that `asked_as_of` and the revision timestamp
stay distinct, and that a 200 carrying no revision is not a success.
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from nfl.archive.provenance import CHECKED_AND_FOUND, SOURCE_FAILED  # noqa: E402
from nfl.archive.sources import coaching_staff  # noqa: E402


class Coverage(unittest.TestCase):
    def test_all_32_teams_are_listed(self):
        self.assertEqual(len(coaching_staff.TEAMS), 32)
        self.assertEqual(len(set(coaching_staff.TEAMS)), 32)

    def test_every_team_maps_to_a_staff_template(self):
        for team in coaching_staff.TEAMS:
            url = coaching_staff._point_in_time_url(
                f"Template:{team} staff", "2026-09-01T00:00:00Z")
            self.assertIn("Template", url)
            self.assertIn("prop=revisions", url)


class AsOfSemantics(unittest.TestCase):
    def test_no_as_of_uses_one_batched_request_for_all_32(self):
        """Measured: per-page fetching was rate-limited to 23/32 failures."""
        url = coaching_staff._batch_url(
            [f"Template:{t} staff" for t in coaching_staff.TEAMS])
        self.assertNotIn("rvstart", url)
        self.assertNotIn("rvdir", url)
        # Count separators in the TITLES parameter only: rvprop itself carries
        # two %7C, which is what made the first version of this assertion read
        # 33 where it expected 31.
        titles = url.split("titles=")[1].split("&")[0]
        self.assertEqual(titles.count("%7C"), len(coaching_staff.TEAMS) - 1)
        self.assertLessEqual(len(coaching_staff.TEAMS), coaching_staff.BATCH_LIMIT)

    def test_as_of_requests_the_newest_revision_at_or_before_it(self):
        """rvdir=older is what makes this point-in-time rather than nearest."""
        url = coaching_staff._point_in_time_url(
            "Template:X staff", "2024-09-01T00:00:00Z")
        self.assertIn("rvstart=", url)
        self.assertIn("rvdir=older", url)

    def test_asked_as_of_is_recorded_separately_from_the_revision(self):
        """The two timestamps must never be collapsed.

        A revision timestamp says when Wikipedia was EDITED, not when the
        appointment happened. Merging them would turn "when we asked" into a
        false claim about when a coordinator changed.

        Asserted against a PRODUCED RECORD, not by grepping module source. The
        first version of this test grepped the source and therefore passed when
        the context key was renamed, because the phrase still appeared in the
        docstring -- a checker that cannot tell code from prose.
        """
        class FakeResponse:
            status_code = 200
            content = (b'{"query":{"pages":{"1":{"revisions":[{"revid":1,'
                       b'"timestamp":"2026-07-27T00:00:00Z","slots":{"main":'
                       b'{"*":"x"}}}]}}}}')
            headers = {"content-type": "application/json"}

        class FakeSession:
            def get(self, *a, **k):
                return FakeResponse()

        asked = "2024-09-01T00:00:00Z"
        record = coaching_staff.capture(
            session=FakeSession(), teams=("Cincinnati Bengals",),
            as_of=asked, pause=0)[0]
        self.assertIn("asked_as_of", record.context)
        self.assertEqual(record.context["asked_as_of"], asked)
        # observed_at is when WE looked; it must not be the requested instant.
        self.assertNotEqual(record.observed_at, asked)


class FailClosed(unittest.TestCase):
    def test_a_200_with_no_revisions_is_not_reported_as_found(self):
        """A missing template must not look like a successful observation."""
        class FakeResponse:
            status_code = 200
            content = b'{"query":{"pages":{"-1":{"missing":""}}}}'
            headers = {"content-type": "application/json"}

        class FakeSession:
            def get(self, *a, **k):
                return FakeResponse()

        records = coaching_staff.capture(
            session=FakeSession(), teams=("Cincinnati Bengals",), pause=0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].outcome, SOURCE_FAILED)
        self.assertIn("no `revisions` array", records[0].failure_reason)
        self.assertIsNone(records[0].body)

    def test_a_real_revision_payload_is_reported_as_found(self):
        class FakeResponse:
            status_code = 200
            content = (b'{"query":{"pages":{"1":{"revisions":[{"revid":1,'
                       b'"timestamp":"2026-07-27T00:00:00Z","slots":{"main":'
                       b'{"*":"*Head coach \\u2013 Zac Taylor"}}}]}}}}')
            headers = {"content-type": "application/json"}

        class FakeSession:
            def get(self, *a, **k):
                return FakeResponse()

        records = coaching_staff.capture(
            session=FakeSession(), teams=("Cincinnati Bengals",), pause=0)
        self.assertEqual(records[0].outcome, CHECKED_AND_FOUND)
        self.assertIn(b"Zac Taylor", records[0].body)


class LicenceIsCarried(unittest.TestCase):
    def test_every_record_carries_the_licence_and_the_caveat(self):
        class FakeResponse:
            status_code = 200
            content = (b'{"query":{"pages":{"1":{"revisions":[{"revid":1,'
                       b'"timestamp":"2026-07-27T00:00:00Z","slots":{"main":'
                       b'{"*":"x"}}}]}}}}')
            headers = {"content-type": "application/json"}

        class FakeSession:
            def get(self, *a, **k):
                return FakeResponse()

        record = coaching_staff.capture(
            session=FakeSession(), teams=("Chicago Bears",), pause=0)[0]
        self.assertIn("CC BY-SA", record.context["licence"])
        self.assertIn("share-alike", record.context["licence"])
        self.assertIn("EDITED", record.context["caveat"])


class PlayCallerHonesty(unittest.TestCase):
    def test_the_module_does_not_claim_to_know_the_play_caller(self):
        import inspect
        source = inspect.getsource(coaching_staff)
        self.assertIn("does not decide who calls plays", source)

    def test_play_caller_doc_records_the_gap_and_the_correction(self):
        path = os.path.join(REPO, "nfl", "docs", "PLAY_CALLER.md")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as handle:
            doc = " ".join(handle.read().split())
        self.assertIn("No public machine-readable source for play-caller "
                      "identity was established", doc)
        self.assertIn("One file is not a category", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
