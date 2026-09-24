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

    def test_current_slate_withdrawn_pregame_pick_is_carried_as_withdrawn(self):
        # 2026-09-24 published-downgrade-display (Mission 12 Workstream C):
        # registered on the payload's own slate, still pregame, absent from
        # the current scoring pass. Before this change the row vanished
        # entirely (silently hiding a withdrawn published recommendation --
        # exactly what Jacob's requirement forbids). It is now carried,
        # clearly labelled, never actionable, and never re-priced.
        row = late_pick()
        out = reconcile([], published_registry(row), date="2026-08-17", now="2026-08-17T20:00:00Z",
                        schedule={1: {"status": PREVIEW}})
        self.assertEqual(len(out["props"]), 1)
        carried = out["props"][0]
        self.assertEqual(carried["id"], row["id"])
        # Not an actionable current Top Pick: no valid RECOMMENDATION_STATES
        # member other than "neutral" honestly describes "no longer
        # represented in today's scoring pass."
        self.assertEqual(carried["recommendation_status"], "neutral")
        self.assertTrue(carried["withdrawn_since_publication"])
        self.assertIn("not a current recommendation", carried["status_reasons"][0])
        self.assertTrue(carried["demoted_before_start"]["withdrawn"])
        # The immutable publication record is untouched: it still proves
        # this was originally a Top Pick, at its original price/probability.
        snapshot = carried["publication_snapshot"]
        self.assertEqual(snapshot["recommendation_status"], "top_pick")
        self.assertEqual(snapshot["market_odds"], row["market_odds"])
        self.assertEqual(snapshot["hit_probability"], row["hit_probability"])
        # Never counted as a current Top Pick, but visible as a distinct,
        # derivable "published, now downgraded/withdrawn" population.
        self.assertEqual(out["summary"]["n_top_pick"], 0)
        self.assertEqual(out["summary"]["n_published_downgraded"], 1)

    def test_withdrawn_pregame_pick_is_never_a_publication_candidate(self):
        # Guards against a regression that would re-register an already-
        # published id: build_publication_manifest must not produce a
        # candidate for it (it is already in the registry, and its
        # recommendation_status is "neutral" here besides).
        from dashboard.publication_registry import build_publication_manifest
        row = late_pick()
        registry = published_registry(row)
        out = reconcile([], registry, date="2026-08-17", now="2026-08-17T20:00:00Z",
                        schedule={1: {"status": PREVIEW}})
        manifest = build_publication_manifest(
            out, default_live_state(), registry, "sha", "2026-08-17T20:05:00Z",
        )
        self.assertEqual(manifest["candidates"], [])

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

    def test_withdrawn_pregame_pick_is_never_repriced_even_on_its_own_slate(self):
        # The other-build-slate skip above is guarded by a DIFFERENT slate
        # date; a withdrawn pick is carried on its OWN, same slate date, so
        # refresh_prices needs its own explicit signal
        # (withdrawn_since_publication) to still refuse to reprice it.
        import json
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

        withdrawn = dict(late_pick(), published_slate_date="2026-08-17",
                         withdrawn_since_publication=True, recommendation_status="neutral")
        with tempfile.TemporaryDirectory() as tmp:
            data, live_path, reg = (os.path.join(tmp, n) for n in ("data.json", "live.json", "reg.json"))
            atomic_write_json(data, payload([withdrawn], date="2026-08-17"))
            atomic_write_json(live_path, default_live_state())
            write_registry(reg, default_registry())
            fetchers = ("fetch_prop_prices", "fetch_pitcher_strikeouts", "fetch_pitcher_outs",
                        "fetch_first_inning_totals", "fetch_combined_pitcher_strikeouts")
            families = ("general_batter", "strikeouts", "pitcher_outs", "first_inning",
                        "combined_strikeouts")
            patches = [mock.patch.object(fd, f, return_value=observed_family(fam))
                       for f, fam in zip(fetchers, families)]
            patches += [mock.patch.object(gr, "fetch_game_contexts",
                                          return_value={1: {"status": PREVIEW, "feed": {}}}),
                        mock.patch.object(recommendation, "attach_recommendations"),
                        mock.patch.object(rp, "utc_now", return_value="2026-08-17T20:20:00Z")]
            for p in patches:
                p.start()
            try:
                rp.refresh(data, live_path, reg)
            finally:
                for p in patches:
                    p.stop()
            with open(live_path, encoding="utf-8") as fh:
                live = json.load(fh)["props"]
        self.assertNotIn(withdrawn["id"], live)



def newer_top_pick_delta(row, at="2026-08-17T19:30:00Z"):
    """A live.json price/status delta stamped AFTER the board's odds fetch --
    exactly what the 5-minute price refresh writes."""
    live = default_live_state()
    merge_prop_fields(live, row["id"], {
        "recommendation_status": "top_pick", "status_reasons": [],
        "market_odds": -150, "market_implied": .6, "market_edge": .1,
    }, at, channel="prices")
    return live


def lean_row(row):
    lean = dict(row)
    lean.update(recommendation_status="lean", status_reasons=["price moved past the value line"],
                market_odds=-190)
    return lean


class PregameDemotionPersistsTests(unittest.TestCase):
    """Review of the Workstream C candidate (findings 1-3): a published Top
    Pick that stopped being one before first pitch must not flip back to an
    actionable Top Pick on a later reconcile pass, at the UTC rollover, or
    silently at first pitch."""

    NOW = "2026-08-17T20:00:00Z"

    def test_second_pass_with_newer_live_delta_keeps_withdrawn_pick_withdrawn(self):
        row = late_pick()
        registry = published_registry(row)
        first = reconcile([], registry, date="2026-08-17", now=self.NOW,
                          schedule={1: {"status": PREVIEW}})
        second = reconcile([dict(r) for r in first["props"]], registry, date="2026-08-17",
                           now=self.NOW, live=newer_top_pick_delta(row),
                           schedule={1: {"status": PREVIEW}})
        kept = second["props"][0]
        self.assertEqual(kept["recommendation_status"], "neutral")
        self.assertTrue(kept["withdrawn_since_publication"])
        self.assertEqual(kept["market_odds"], row["market_odds"])  # published price, not -150
        self.assertEqual(second["summary"]["n_top_pick"], 0)
        self.assertEqual(second["summary"]["n_published_downgraded"], 1)

    def test_downgrade_recorded_while_pregame_and_cleared_if_top_pick_again(self):
        row = late_pick()
        registry = published_registry(row)
        out = reconcile([lean_row(row)], registry, date="2026-08-17", now=self.NOW,
                        schedule={1: {"status": PREVIEW}})
        marker = out["props"][0]["demoted_before_start"]
        self.assertEqual(marker["status"], "lean")
        self.assertFalse(marker["withdrawn"])
        self.assertEqual(out["summary"]["n_published_downgraded"], 1)
        back = reconcile([dict(row)], registry, date="2026-08-17", now=self.NOW,
                         schedule={1: {"status": PREVIEW}})
        self.assertNotIn("demoted_before_start", back["props"][0])
        self.assertEqual(back["summary"]["n_top_pick"], 1)
        self.assertEqual(back["summary"]["n_published_downgraded"], 0)

    def test_utc_rollover_keeps_the_pregame_demotion(self):
        row = late_pick()
        registry = published_registry(row)
        for label, before in (("downgraded", [lean_row(row)]), ("withdrawn", [])):
            with self.subTest(label):
                prior = reconcile(before, registry, date="2026-08-17", now=self.NOW,
                                  schedule={1: {"status": PREVIEW}})
                # Full rebuild after the build date rolled: the pick is absent
                # from the new slate's scoring pass; prior_payload is the
                # deployed data.json.
                rolled = bd.reconcile_public_lifecycle(
                    payload([], date="2026-08-18"), prior_payload=prior,
                    live=default_live_state(), registry=registry,
                    schedule={1: {"status": PREVIEW}}, now=ROLLOVER)
                # ...and the finalize/prepare pass over that built payload.
                again = reconcile([dict(r) for r in rolled["props"]], registry,
                                  date="2026-08-18", now=ROLLOVER,
                                  schedule={1: {"status": PREVIEW}})
                for out in (rolled, again):
                    kept = out["props"][0]
                    self.assertNotEqual(kept["recommendation_status"], "top_pick")
                    self.assertEqual(kept["market_odds"], row["market_odds"])
                    self.assertEqual(kept["publication_snapshot"]["recommendation_status"], "top_pick")
                    self.assertEqual(out["summary"]["n_top_pick"], 0)
                    self.assertEqual(out["summary"]["n_published_downgraded"], 1)
                self.assertEqual(again["props"][0].get("withdrawn_since_publication") is True,
                                 label == "withdrawn")

    def test_after_first_pitch_pick_shows_as_published_but_keeps_the_note(self):
        row = late_pick()
        registry = published_registry(row)
        prior = reconcile([], registry, date="2026-08-17", now=self.NOW,
                          schedule={1: {"status": PREVIEW}})
        started = bd.reconcile_public_lifecycle(
            payload([], date="2026-08-18"), prior_payload=prior, live=default_live_state(),
            registry=registry, schedule={1: {"status": LIVE}}, now="2026-08-18T02:30:00Z")
        kept = started["props"][0]
        # Graded and shown as published (the registry is the grading truth)...
        self.assertEqual(kept["recommendation_status"], "top_pick")
        self.assertNotIn("withdrawn_since_publication", kept)
        # ...but the page can still say it was withdrawn before first pitch.
        self.assertTrue(kept["demoted_before_start"]["withdrawn"])

    def test_prior_payload_marker_can_never_touch_an_unregistered_row(self):
        row = prop(player_id=303)
        forged = dict(row)
        forged["demoted_before_start"] = {"status": "neutral", "status_reasons": [], "withdrawn": True}
        out = bd.reconcile_public_lifecycle(
            payload([dict(row)]), prior_payload=payload([forged]), live=default_live_state(),
            registry=published_registry(late_pick()), schedule={1: {"status": PREVIEW}},
            now="2026-08-17T17:00:00Z")
        fresh = next(p for p in out["props"] if p["id"] == row["id"])
        self.assertEqual(fresh["recommendation_status"], "top_pick")
        self.assertNotIn("demoted_before_start", fresh)

    def test_prior_marker_needs_a_published_prior_row(self):
        # A marker on a prior_payload row that carries no publication
        # snapshot (never written by a reconcile pass) is ignored.
        row = late_pick()
        registry = published_registry(row)
        forged = dict(row)
        forged["demoted_before_start"] = {"status": "neutral", "status_reasons": [], "withdrawn": True}
        out = bd.reconcile_public_lifecycle(
            payload([], date="2026-08-18"), prior_payload=payload([forged]),
            live=default_live_state(), registry=registry,
            schedule={1: {"status": PREVIEW}}, now=ROLLOVER)
        self.assertEqual(out["props"][0]["recommendation_status"], "top_pick")
        self.assertNotIn("demoted_before_start", out["props"][0])

    def test_unpublished_rows_never_carry_a_marker(self):
        lean = dict(prop(player_id=404, status="lean"))
        lean["demoted_before_start"] = {"status": "lean", "status_reasons": [], "withdrawn": False}
        out = reconcile([lean], published_registry(late_pick()), date="2026-08-17",
                        now="2026-08-17T17:00:00Z", schedule={1: {"status": PREVIEW}})
        row = next(p for p in out["props"] if p["id"] == lean["id"])
        self.assertNotIn("demoted_before_start", row)

    def test_prior_slate_row_marked_withdrawn_is_not_rewithdrawn_as_same_slate(self):
        # A withdrawn marker from the Aug-17 slate, seen again by an Aug-18
        # build while still pregame: it follows the other-build-slate path
        # (carried marker), not the same-slate re-withdrawal.
        row = late_pick()
        registry = published_registry(row)
        first = reconcile([], registry, date="2026-08-17", now=self.NOW,
                          schedule={1: {"status": PREVIEW}})
        baked = dict(first["props"][0])
        baked.pop("demoted_before_start")
        out = reconcile([baked], registry, date="2026-08-18", now=ROLLOVER,
                        schedule={1: {"status": PREVIEW}})
        self.assertEqual(out["props"][0]["recommendation_status"], "top_pick")
        self.assertNotIn("withdrawn_since_publication", out["props"][0])


if __name__ == "__main__":
    unittest.main()

