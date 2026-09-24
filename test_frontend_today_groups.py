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
          // No display_timezone: exercises grouping only, independent of the
          // real clock (the browser-side Central expiry has its own test).
          DATA = { date: "2026-09-24", display_date: "2026-09-23", props: [], summary: {} };
          const groups = topPickGroups([
            tp("a", { published_slate_date: "2026-09-23" }),
            tp("b"),                                          // new build slate, not yet published
            tp("c", { published_slate_date: "2026-09-22", game_state: "live" }),
            tp("e", { published_slate_date: "2026-09-22", game_state: "final" }),  // settled: gone
            tp("d", { published_slate_date: "2026-09-23" }),
          ]);
          return groups.map(g => ({ kind: g.kind, heading: g.heading, ids: g.picks.map(p => p.id) }));
        """)
        self.assertEqual([g["kind"] for g in r], ["today", "early", "carried"])
        self.assertEqual(r[0]["ids"], ["a", "d"])
        self.assertEqual(r[0]["heading"], "Today · Wed, Sep 23")
        self.assertEqual(r[1]["heading"], "Early picks for Thu, Sep 24")
        self.assertEqual(r[2]["heading"], "Still open from Tue, Sep 22")
        self.assertEqual(r[2]["ids"], ["c"])

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

    def test_browser_enforces_central_midnight_when_the_deploy_is_late(self):
        # A payload deployed on a long-past Central day: the browser's own
        # Central date is later, so that day's settled picks are gone and
        # only its still-live pick remains, as "still open".
        r = run_node(SETUP + r"""
          DATA = { date: "2020-01-02", display_date: "2020-01-01", display_timezone: "America/Chicago",
                   props: [], summary: {} };
          const groups = topPickGroups([
            tp("settled", { published_slate_date: "2020-01-01", game_state: "final" }),
            tp("pregame", { published_slate_date: "2020-01-01", game_state: "pregame" }),
            tp("live", { published_slate_date: "2020-01-01", game_state: "live" }),
          ]);
          return { today: displayToday() > "2020-01-01",
                   groups: groups.map(g => ({ kind: g.kind, ids: g.picks.map(p => p.id) })) };
        """)
        self.assertTrue(r["today"])
        self.assertEqual(r["groups"], [{"kind": "carried", "ids": ["live"]}])

    def test_top_pick_tile_counts_today_only(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-24", display_date: "2026-09-23", summary: {}, props: [
            tp("a", { published_slate_date: "2026-09-23", game_state: "final",
                      published_top_pick_at: "x", publication_artifact_id: "y" }),
            tp("b", { game_state: "pregame", game_start: "2099-01-01T00:00:00Z" }),
            tp("c", { published_slate_date: "2026-09-22", game_state: "live",
                      published_top_pick_at: "x", publication_artifact_id: "y" }),
          ] };
          refreshSummary();
          return { n: DATA.summary.n_top_pick, other: DATA.summary.n_top_pick_other };
        """)
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["other"], 2)  # the early pick and the still-open pick

    def test_carried_pregame_pick_says_odds_are_as_of_publication(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-24", props: [], summary: {} };
          const published = { published_top_pick_at: "x", publication_artifact_id: "y" };
          return {
            carried: publishedStartedNote(tp("a", Object.assign({ published_slate_date: "2026-09-23",
                      game_state: "pregame" }, published))),
            postponed: publishedStartedNote(tp("b", Object.assign({ published_slate_date: "2026-09-24",
                      game_state: "postponed", game_start: "2020-01-01T00:00:00Z" }, published))),
          };
        """)
        self.assertIn("odds as of publication, not a current quote", r["carried"])
        self.assertEqual(r["postponed"], "")

    def test_today_page_renders_when_every_top_pick_has_expired(self):
        # Round-2 review BLOCKER: after Central midnight and before the next
        # deploy every Top Pick can expire in the browser; renderToday used to
        # read groups[0].kind of an empty list and never finish booting.
        r = run_node(SETUP + r"""
          SHOW_UNVERIFIED = true;
          DATA = { date: "2020-01-02", display_date: "2020-01-01", display_timezone: "America/Chicago",
                   generated_at: "2020-01-01T12:00:00Z", summary: {}, schedule: [], families: [],
                   props: [tp("old", { published_slate_date: "2020-01-01", game_state: "final",
                                       game_start: "2020-01-01T18:00:00Z",
                                       published_top_pick_at: "x", publication_artifact_id: "y" })] };
          indexProps();
          refreshSummary();
          let error = null;
          try { renderToday(); } catch (e) { error = String(e); }
          return { error, html: __el("page-today").innerHTML, n: DATA.summary.n_top_pick };
        """)
        self.assertIsNone(r["error"])
        self.assertIn("Best Bets", r["html"])
        self.assertNotIn('data-open="old"', r["html"])
        self.assertEqual(r["n"], 0)

    def test_central_date_is_iso_regardless_of_locale_format(self):
        r = run_node(SETUP + r"""return { d: centralDateNow() };""")
        self.assertRegex(r["d"], r"^\d{4}-\d{2}-\d{2}$")

    def test_detail_sheet_does_not_call_a_carried_price_current(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-24", props: [], summary: {} };
          const published = { published_top_pick_at: "x", publication_artifact_id: "y",
                              published_slate_date: "2026-09-23" };
          return {
            pregame: priceFreshnessState(tp("a", Object.assign({ game_state: "pregame" }, published))).label,
            started: priceFreshnessState(tp("b", Object.assign({ game_state: "live",
                       market_fetch_state: "IN_PLAY" }, published))).label,
            current: priceFreshnessState(tp("c", { game_state: "pregame" })).label,
          };
        """)
        self.assertEqual(r["pregame"], "As published · not a current quote")
        self.assertEqual(r["started"], "Game live · price locked pregame")
        self.assertTrue(r["current"].startswith("Current"))

    def test_open_tab_rerenders_when_the_central_day_turns_over(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2020-01-02", display_date: "2020-01-01", display_timezone: "America/Chicago",
                   props: [], summary: {} };
          let renders = 0;
          renderRoute = () => { renders += 1; };
          LAST_DISPLAY_TODAY = "2020-01-01";   // the day the page was rendered on
          rerenderOnSlateDayChange();          // the browser's Central date is later now
          const afterTurnover = renders;
          rerenderOnSlateDayChange();          // same day again: no extra render
          return { afterTurnover, afterSameDay: renders };
        """)
        self.assertEqual(r["afterTurnover"], 1)
        self.assertEqual(r["afterSameDay"], 1)

    def test_carried_pick_label_wins_over_stale_line_moved_or_failed_states(self):
        r = run_node(SETUP + r"""
          DATA = { date: "2026-09-24", props: [], summary: {} };
          const published = { published_top_pick_at: "x", publication_artifact_id: "y",
                              published_slate_date: "2026-09-23", game_state: "pregame" };
          return {
            moved: priceFreshnessState(tp("a", Object.assign({ market_odds: null,
                     market_fetch_state: "LINE_MOVED", market_posted_line: 1.5 }, published))).label,
            failed: priceFreshnessState(tp("b", Object.assign({ market_fetch_state: "FETCH_FAILED" },
                     published))).label,
          };
        """)
        self.assertEqual(r["moved"], "As published · not a current quote")
        self.assertEqual(r["failed"], "As published · not a current quote")

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
