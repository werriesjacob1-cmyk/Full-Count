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


class InactiveReportDiscovery(unittest.TestCase):
    def test_discovery_is_same_origin_news_only_and_deduplicated(self):
        body = b"""
        <html><body>
          <a href="/news/week-1-inactives-a-at-b">game one</a>
          <a href="https://www.nfl.com/news/nfl-kickoff-game-inactives-c-at-d?utm_source=x#top">
            game two
          </a>
          <a href="/news/week-1-inactives-a-at-b">duplicate</a>
          <a href="https://example.com/news/game-inactives-x-at-y">external</a>
          <a href="/injuries/">not news</a>
          <a href="/news/week-1-injury-report">not inactive report</a>
        </body></html>
        """
        urls = official_nfl._inactive_report_urls(body)
        self.assertEqual(
            urls,
            (
                "https://www.nfl.com/news/week-1-inactives-a-at-b",
                "https://www.nfl.com/news/nfl-kickoff-game-inactives-c-at-d",
            ),
        )

    def test_capture_archives_discovered_report_bytes_separately(self):
        from nfl.archive.provenance import CHECKED_AND_FOUND

        inactive_index = b"""
        <html><body>
          <a href="/news/week-1-inactives-team-a-at-team-b">official report</a>
        </body></html>
        """
        report_body = b"<html><h3>TEAM A</h3><ul><li>QB Example Player</li></ul></html>"

        class FakeResponse:
            def __init__(self, body, status=200):
                self.content = body
                self.status_code = status
                self.headers = {"content-type": "text/html"}

        class FakeSession:
            def get(self, url, **kwargs):
                if url == "https://www.nfl.com/inactives/":
                    return FakeResponse(inactive_index)
                if url == (
                    "https://www.nfl.com/news/"
                    "week-1-inactives-team-a-at-team-b"
                ):
                    return FakeResponse(report_body)
                return FakeResponse(b"<html>base official page</html>")

        records = official_nfl.capture(session=FakeSession())

        base = next(r for r in records if r.artifact == "inactives")
        self.assertEqual(base.outcome, CHECKED_AND_FOUND)
        self.assertEqual(base.context["discovered_report_count"], 1)

        reports = [
            r for r in records
            if r.artifact.startswith("inactive_report_")
        ]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].outcome, CHECKED_AND_FOUND)
        self.assertEqual(reports[0].body, report_body)
        self.assertEqual(
            reports[0].url,
            "https://www.nfl.com/news/week-1-inactives-team-a-at-team-b",
        )
        self.assertEqual(
            reports[0].context["discovered_from"],
            "https://www.nfl.com/inactives/",
        )
        self.assertEqual(
            reports[0].context["stored"],
            "raw HTML, unparsed",
        )

    def test_failed_index_fetch_does_not_invent_game_reports(self):
        class FakeResponse:
            content = b"blocked"
            status_code = 503
            headers = {"content-type": "text/html"}

        class FakeSession:
            def get(self, url, **kwargs):
                if url == "https://www.nfl.com/inactives/":
                    return FakeResponse()
                class OK:
                    content = b"<html>ok</html>"
                    status_code = 200
                    headers = {"content-type": "text/html"}
                return OK()

        records = official_nfl.capture(session=FakeSession())
        self.assertFalse(
            any(r.artifact.startswith("inactive_report_") for r in records)
        )
        base = next(r for r in records if r.artifact == "inactives")
        self.assertEqual(base.context["discovered_report_count"], 0)
        self.assertIn("skipped", base.context["report_discovery_status"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
