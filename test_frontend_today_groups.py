#!/usr/bin/env python3
"""Today page: Top Picks grouped by Central slate day; started picks labelled.

Companion to test_published_today_central.py (the build side). Runs app.js in
the same Node harness as test_frontend_history_games.py.
"""
from __future__ import annotations

import unittest

from test_frontend_history_games import run_node

SETUP = r"""
function tp(id, extra) {
  return Object.assign({ id, name: "P " + id, prop: "Over 0.5 Hits", game_pk: 1,
    recommendation_status: "top_pick", hit_probability: 0.7, market_odds: -150 }, extra || {});
}
"""


class TodayGroupsTests(unittest.TestCase):
    def test_evening_after_utc_rollover_groups_today_early_and_carried(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-24", display_date: "2026-09-23", display_timezone: "America/Chicago",
                   props: [], summary: {} };
          const groups = topPickGroups([
            tp("a", { published_slate_date: "2026-09-23" }),
            tp("b"),                                          // new build slate, not yet published
            tp("c", { published_slate_date: "2026-09-22" }),
            tp("d", { published_slate_date: "2026-09-23" }),
          ]);
          return groups.map(g => ({ kind: g.kind, heading: g.heading, ids: g.picks.map(p => p.id) }));
        """)
        self.assertEqual([g["kind"] for g in r], ["today", "early", "carried"])
        self.assertEqual(r[0]["ids"], ["a", "d"])
        self.assertEqual(r[0]["heading"], "Today · Wed, Sep 23")
        self.assertEqual(r[1]["heading"], "Early picks for Thu, Sep 24")
        self.assertEqual(r[2]["heading"], "In progress from Tue, Sep 22")

    def test_ordinary_day_is_one_unlabelled_group(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-23", display_date: "2026-09-23", props: [], summary: {} };
          const groups = topPickGroups([tp("a"), tp("b", { published_slate_date: "2026-09-23" })]);
          return { n: groups.length, kind: groups[0].kind };
        """)
        self.assertEqual(r, {"n": 1, "kind": "today"})

    def test_legacy_payload_without_display_date_still_renders(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-23", props: [], summary: {} };
          return topPickGroups([tp("a")]).map(g => g.kind);
        """)
        self.assertEqual(r, ["today"])

    def test_started_published_pick_says_it_is_not_a_current_offer(self):
        r = run_node(SETUP + r"""
          const started = { game_state: "live", game_start: "2026-09-24T00:40:00Z" };
          const published = { published_top_pick_at: "2026-09-23T20:00:00Z", publication_artifact_id: "x" };
          return {
            startedPublished: publishedStartedNote(tp("a", Object.assign({}, started, published))),
            pregamePublished: publishedStartedNote(tp("b", Object.assign({ game_state: "pregame",
                                game_start: "2099-01-01T00:00:00Z" }, published))),
            startedUnpublished: publishedStartedNote(tp("c", started)),
          };
        """)
        self.assertIn("original pregame odds shown, not a current offer", r["startedPublished"])
        self.assertEqual(r["pregamePublished"], "")
        self.assertEqual(r["startedUnpublished"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
