#!/usr/bin/env python3
"""A published Top Pick stays on its Central-time day's Today page.

Real incident, 2026-09-23 (Issue #91 comment 5808992903): the build slate
date (mlb_daily.TODAY) rolled at UTC midnight -- 7:13 pm Central -- and the
rebuild dropped 7 of 27 published Sept-23 Top Picks: 5 whose games had not
started (first pitches 00:40-02:10Z) and 2 already final. After that, every
carried pick disappeared as soon as its game left "live". Jacob's contract:
a published pick stays on that day's page through 11:59:59 pm Central,
whatever its game state; after Central midnight only still-live picks remain
("in progress from yesterday"), and settled picks live in History only.
"""
from __future__ import annotations

import unittest

from dashboard import build_dashboard as bd
from dashboard.live_state import default_live_state, merge_prop_fields
from test_live_lifecycle import FINAL, LIVE, PREVIEW, payload, prop, published_registry

LATE_START = "2026-08-18T02:10:00Z"   # 9:10 pm Central on Aug 17
ROLLOVER = "2026-08-18T00:13:00Z"     # 7:13 pm Central on Aug 17 (UTC date is Aug 18)
LAST_SECOND = "2026-08-18T04:59:59Z"  # 11:59:59 pm Central on Aug 17
AFTER_MIDNIGHT = "2026-08-18T05:01:00Z"  # 12:01 am Central on Aug 18


def late_pick(player_id=101):
    row = prop(player_id=player_id)
    row["game_start"] = LATE_START
    row["id"] = bd.canonical_prop_id(row)
    return row


def final_hit_live(row, at="2026-08-17T21:00:00Z"):
    live = default_live_state()
    merge_prop_fields(live, row["id"], {
        "settlement_state": "hit", "settlement_authority": "official_final",
        "settlement_observed_at": at,
        "settlement_source": "mlb_official_final_with_fanduel_eligibility",
        "result_actual": 1, "result_reason": "official final",
    }, at, channel="grades")
    merge_prop_fields(live, row["id"], {
        "game_state": "final", "game_state_source": "mlb_game_feed_by_game_pk",
        "game_state_observed_at": at,
    }, at, channel="grades")
    return live


def reconcile(rows, registry, *, date, now, live=None, schedule=None):
    return bd.reconcile_public_lifecycle(
        payload(rows, date=date), live=live or default_live_state(), registry=registry,
        schedule=schedule or {}, now=now,
    )


class DisplaySlateDateTests(unittest.TestCase):
    def test_central_date_across_utc_midnight_and_dst(self):
        self.assertEqual(bd.display_slate_date("2026-09-24T00:13:00Z"), "2026-09-23")
        self.assertEqual(bd.display_slate_date("2026-09-24T04:59:59Z"), "2026-09-23")
        self.assertEqual(bd.display_slate_date("2026-09-24T05:00:00Z"), "2026-09-24")
        # Central Standard Time (UTC-6) after DST ends.
        self.assertEqual(bd.display_slate_date("2026-11-02T05:30:00Z"), "2026-11-01")
        self.assertEqual(bd.display_slate_date("2026-11-02T06:00:00Z"), "2026-11-02")
        self.assertEqual(bd.display_slate_date("2026-09-24T00:13:00+00:00"), "2026-09-23")


class PublishedTodayCentralTests(unittest.TestCase):
    def test_utc_rollover_keeps_a_published_pick_whose_game_has_not_started(self):
        row = late_pick()
        out = reconcile([], published_registry(row), date="2026-08-18", now=ROLLOVER)
        self.assertEqual([p["id"] for p in out["props"]], [row["id"]])
        kept = out["props"][0]
        self.assertEqual(kept["recommendation_status"], "top_pick")
        self.assertEqual(kept["market_odds"], -120)          # the published price
        self.assertEqual(kept["published_slate_date"], "2026-08-17")
        self.assertEqual(out["display_date"], "2026-08-17")
        self.assertEqual(out["display_timezone"], "America/Chicago")

    def test_utc_rollover_keeps_an_already_final_published_pick_until_central_midnight(self):
        row = prop()  # first pitch 18:00Z Aug 17, final by 21:00Z
        registry = published_registry(row)
        live = final_hit_live(row)
        for now in (ROLLOVER, LAST_SECOND):
            with self.subTest(now=now):
                out = reconcile([], registry, date="2026-08-18", now=now, live=live)
                self.assertEqual([p["id"] for p in out["props"]], [row["id"]])
                self.assertEqual(out["props"][0]["settlement_state"], "hit")
                self.assertEqual(out["props"][0]["game_state"], "final")

    def test_after_central_midnight_settled_picks_leave_and_live_ones_stay(self):
        settled = prop(player_id=101)
        still_live = prop(player_id=202, game_pk=3)
        still_live["game_start"] = LATE_START
        still_live["id"] = bd.canonical_prop_id(still_live)
        registry = published_registry(settled)
        registry_live = published_registry(still_live)
        registry["entries"].update(registry_live["entries"])
        live = final_hit_live(settled)
        out = reconcile([], registry, date="2026-08-18", now=AFTER_MIDNIGHT, live=live,
                        schedule={1: {"status": FINAL}, 3: {"status": LIVE}})
        ids = [p["id"] for p in out["props"]]
        self.assertNotIn(settled["id"], ids)
        self.assertIn(still_live["id"], ids)  # in progress from yesterday
        self.assertEqual(out["display_date"], "2026-08-18")

    def test_baked_prior_row_is_kept_once_not_duplicated(self):
        row = prop()
        registry = published_registry(row)
        out = reconcile([dict(row)], registry, date="2026-08-18", now=ROLLOVER,
                        live=final_hit_live(row), schedule={1: {"status": FINAL}})
        self.assertEqual([p["id"] for p in out["props"]], [row["id"]])

    def test_days_old_settled_pick_still_never_sticks(self):
        row = prop()
        registry = published_registry(row)
        for rows in ([], [dict(row)]):
            with self.subTest(baked=bool(rows)):
                out = reconcile(rows, registry, date="2026-08-19", now="2026-08-19T20:11:00Z")
                self.assertEqual(out["props"], [])

    def test_current_slate_withdrawn_pregame_pick_rule_unchanged(self):
        # Registered on the payload's own slate, still pregame, absent from
        # the current scoring pass: the pre-existing rule does not carry it.
        row = late_pick()
        out = reconcile([], published_registry(row), date="2026-08-17", now="2026-08-17T20:00:00Z",
                        schedule={1: {"status": PREVIEW}})
        self.assertEqual(out["props"], [])

    def test_new_slate_pick_and_prior_central_day_pick_coexist(self):
        prior = late_pick(player_id=101)
        registry = published_registry(prior)
        tomorrow = prop(player_id=303, game_pk=2)
        tomorrow["game_start"] = "2026-08-18T23:05:00Z"
        tomorrow["id"] = bd.canonical_prop_id(tomorrow)
        out = reconcile([tomorrow], registry, date="2026-08-18", now=ROLLOVER,
                        schedule={1: {"status": PREVIEW}, 2: {"status": PREVIEW}})
        ids = {p["id"] for p in out["props"]}
        self.assertEqual(ids, {prior["id"], tomorrow["id"]})
        by_id = {p["id"]: p for p in out["props"]}
        self.assertEqual(by_id[prior["id"]]["published_slate_date"], "2026-08-17")
        self.assertNotIn("published_slate_date", by_id[tomorrow["id"]])  # never published


class CarriedPickIsARecordNotAnOfferTests(unittest.TestCase):
    """Review of PR #195: a carried pregame pick must be frozen on every path."""

    def test_baked_pregame_other_slate_row_is_frozen_but_keeps_current_presentation(self):
        published = late_pick()
        published["why"] = ["why at publication"]
        registry = published_registry(published)
        baked = dict(published)
        baked.update({"market_odds": -999, "recommendation_status": "lean",
                      "why": ["current generator why"]})
        out = reconcile([baked], registry, date="2026-08-18", now=ROLLOVER,
                        schedule={1: {"status": PREVIEW}})
        self.assertEqual(len(out["props"]), 1)
        row = out["props"][0]
        self.assertEqual(row["market_odds"], -120)                 # published price, not -999
        self.assertEqual(row["recommendation_status"], "top_pick")  # not reclassified
        self.assertEqual(row["why"], ["current generator why"])    # first loop kept the payload row
        self.assertEqual(row["published_slate_date"], "2026-08-17")

    def test_line_moved_on_a_carried_pick_opens_no_reconciliation(self):
        from dashboard import reconcile as rc
        carried = dict(late_pick(), published_slate_date="2026-08-17",
                       market_fetch_state="LINE_MOVED", market_posted_line=1.5)
        current = dict(prop(player_id=303, game_pk=2), market_fetch_state="LINE_MOVED",
                       market_posted_line=1.5)
        mismatches = rc.line_moved_mismatches({"date": "2026-08-18", "props": [carried, current]})
        self.assertEqual([m["prop_id"] for m in mismatches], [current["id"]])


class RefreshPricesSkipsCarriedPicksTests(unittest.TestCase):
    def test_carried_pick_is_never_repriced(self):
        import os
        import tempfile
        from unittest import mock
        import grade_results as gr
        import odds_fanduel as fd
        import recommendation
        from dashboard import refresh_prices as rp
        from dashboard.live_state import atomic_write_json
        from dashboard.publication_registry import default_registry, write_registry
        from test_refresh_prices import observed_family

        carried = dict(late_pick(), published_slate_date="2026-08-17")
        current = prop(player_id=303, game_pk=2)
        current["game_start"] = "2026-08-18T23:05:00Z"
        current["id"] = bd.canonical_prop_id(current)
        with tempfile.TemporaryDirectory() as tmp:
            data, live_path, reg = (os.path.join(tmp, n) for n in ("data.json", "live.json", "reg.json"))
            atomic_write_json(data, payload([carried, current], date="2026-08-18"))
            atomic_write_json(live_path, default_live_state())
            write_registry(reg, default_registry())
            seen = []

            def attach(rows, **_feeds):
                seen.extend(r["id"] for r in rows)
                rows[0].update({"market_odds": -300, "market_implied": .75,
                                "market_edge": -.05, "price_clears": False})
                return rows, 1

            def classify(rows, **_kwargs):
                for value in rows:
                    value["status"] = "lean"
                    value["status_reasons"] = []

            fetchers = ("fetch_prop_prices", "fetch_pitcher_strikeouts", "fetch_pitcher_outs",
                        "fetch_first_inning_totals", "fetch_combined_pitcher_strikeouts")
            families = ("general_batter", "strikeouts", "pitcher_outs", "first_inning",
                        "combined_strikeouts")
            contexts = {1: {"status": PREVIEW, "feed": {}}, 2: {"status": PREVIEW, "feed": {}}}
            patches = [mock.patch.object(fd, f, return_value=observed_family(fam))
                       for f, fam in zip(fetchers, families)]
            patches += [mock.patch.object(gr, "fetch_game_contexts", return_value=contexts),
                        mock.patch.object(fd, "attach_market_prices", side_effect=attach),
                        mock.patch.object(recommendation, "attach_recommendations", side_effect=classify),
                        mock.patch.object(rp, "utc_now", return_value="2026-08-18T00:20:00Z")]
            for p in patches:
                p.start()
            try:
                rp.refresh(data, live_path, reg)
            finally:
                for p in patches:
                    p.stop()
            import json
            with open(live_path, encoding="utf-8") as fh:
                live = json.load(fh)["props"]
        self.assertNotIn(carried["id"], seen)
        self.assertNotIn(carried["id"], live)
        self.assertIn(current["id"], seen)

    def test_started_carried_pick_still_gets_its_game_fact_and_in_play(self):
        # Round-2 review: the skip must not hide a started carried pick from
        # the game-state branch, or the detail sheet loses "price locked".
        import json
        import os
        import tempfile
        from unittest import mock
        import grade_results as gr
        from dashboard import refresh_prices as rp
        from dashboard.live_state import atomic_write_json
        from dashboard.publication_registry import default_registry, write_registry

        carried = dict(late_pick(), published_slate_date="2026-08-17")
        with tempfile.TemporaryDirectory() as tmp:
            data, live_path, reg = (os.path.join(tmp, n) for n in ("data.json", "live.json", "reg.json"))
            atomic_write_json(data, payload([carried], date="2026-08-18"))
            atomic_write_json(live_path, default_live_state())
            write_registry(reg, default_registry())
            with mock.patch.object(gr, "fetch_game_contexts",
                                   return_value={1: {"status": LIVE, "feed": {}}}), \
                 mock.patch.object(rp, "utc_now", return_value="2026-08-18T02:30:00Z"):
                rp.refresh(data, live_path, reg)
            with open(live_path, encoding="utf-8") as fh:
                live = json.load(fh)["props"]
        self.assertEqual(live[carried["id"]]["market_fetch_state"], "IN_PLAY")
        self.assertEqual(live[carried["id"]]["game_state"], "live")


if __name__ == "__main__":
    unittest.main(verbosity=2)
