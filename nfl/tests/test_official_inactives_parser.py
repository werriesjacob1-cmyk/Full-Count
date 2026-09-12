#!/usr/bin/env python3
"""Offline contracts for parsing archived official NFL inactive-report HTML."""
import unittest

from nfl.normalize import official_inactives


REPORT = b"""
<html>
  <body>
    <h1>NFL Kickoff Game inactives: New England Patriots at Seattle Seahawks</h1>
    <h3><strong>PATRIOTS</strong></h3>
    <ul>
      <li>RB <a href="/players/treveyon-henderson">TreVeyon Henderson</a></li>
      <li>QB <a href="/players/behren-morton">Behren Morton</a>
          (emergency third QB)</li>
    </ul>
    <h3><strong>SEAHAWKS</strong></h3>
    <ul>
      <li>S <a href="/players/nick-emmanwori">Nick Emmanwori</a></li>
      <li>QB <a href="/players/jalen-milroe">Jalen Milroe</a>
          (emergency third QB)</li>
    </ul>
    <h2>Related Content</h2>
    <ul><li><a href="/news/other">Other story</a></li></ul>
  </body>
</html>
"""


class OfficialInactiveReportParser(unittest.TestCase):
    def test_publication_clock_is_not_inferred_from_modification(self):
        script = b'<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-09-13T15:30:00Z","dateModified":"2026-09-14T01:00:00Z"}</script>'
        self.assertEqual(official_inactives.parse_report(REPORT + script)['report_published_at'], '2026-09-13T15:30:00Z')
        self.assertIsNone(official_inactives.parse_report(REPORT)['report_published_at'])
        conflicting = script.replace(b'15:30', b'16:00')
        self.assertIsNone(official_inactives.parse_report(REPORT + script + conflicting)['report_published_at'])

    def test_extracts_team_sections_and_player_source_identity(self):
        parsed = official_inactives.parse_report(REPORT)

        self.assertEqual(
            parsed["report_title"],
            "NFL Kickoff Game inactives: New England Patriots at Seattle Seahawks",
        )
        self.assertEqual(parsed["player_count"], 4)
        self.assertEqual(
            [t["source_team_label"] for t in parsed["teams"]],
            ["PATRIOTS", "SEAHAWKS"],
        )

        patriots = parsed["teams"][0]["players"]
        self.assertEqual(patriots[0]["listed_position"], "RB")
        self.assertEqual(patriots[0]["player_name"], "TreVeyon Henderson")
        self.assertEqual(
            patriots[0]["source_player_href"],
            "/players/treveyon-henderson",
        )
        self.assertEqual(
            patriots[0]["source_player_slug"],
            "treveyon-henderson",
        )
        self.assertTrue(patriots[0]["listed_inactive"])
        self.assertIsNone(patriots[0]["note"])

    def test_emergency_third_qb_note_is_preserved_and_flagged(self):
        parsed = official_inactives.parse_report(REPORT)
        qb = parsed["teams"][0]["players"][1]

        self.assertEqual(qb["listed_position"], "QB")
        self.assertEqual(qb["player_name"], "Behren Morton")
        self.assertEqual(qb["note"], "(emergency third QB)")
        self.assertTrue(qb["emergency_third_qb"])
        self.assertTrue(qb["listed_inactive"])

    def test_related_content_links_are_not_players(self):
        parsed = official_inactives.parse_report(REPORT)
        names = [
            p["player_name"]
            for team in parsed["teams"]
            for p in team["players"]
        ]
        self.assertNotIn("Other story", names)

    def test_non_inactive_article_fails_closed(self):
        body = b"""
        <html><body>
          <h1>NFL Week 1 injury report</h1>
          <h3>TEAM</h3>
          <ul><li>QB <a href="/players/example">Example</a></li></ul>
        </body></html>
        """
        with self.assertRaisesRegex(ValueError, "inactive"):
            official_inactives.parse_report(body)

    def test_report_without_player_sections_fails_closed(self):
        body = b"""
        <html><body>
          <h1>Week 1 inactives</h1>
          <p>Please check back later.</p>
        </body></html>
        """
        with self.assertRaisesRegex(ValueError, "player"):
            official_inactives.parse_report(body)

    def test_duplicate_source_player_within_team_fails_closed(self):
        body = b"""
        <html><body>
          <h1>Week 1 inactives</h1>
          <h3>TEAM</h3>
          <ul>
            <li>QB <a href="/players/example">Example</a></li>
            <li>QB <a href="/players/example">Example</a></li>
          </ul>
        </body></html>
        """
        with self.assertRaisesRegex(ValueError, "duplicate"):
            official_inactives.parse_report(body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
