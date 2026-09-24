#!/usr/bin/env python3
"""Cross-slate price contamination regressions (2026-09-24 incident).

The 2026-09-24 board was built at 2026-09-24T01:05Z, while the previous
night's Angels @ Athletics game (824951, first pitch 2026-09-24T01:40Z) was
still listed pregame on FanDuel. The flat price feed merged every listed event
and the board looked prices up by player name, so all ten of Mike Trout's
markets for the NEXT day's Angels @ Mariners game (823087) carried that
night's prices (Hits+Runs+RBIs -475, Hits -290, ...). The event-scoped live
refresh correctly reported the same markets NOT_POSTED. The same board also
carried Padres @ Dodgers prices across two days of one series, which is the
matchup-fallback exposure. These tests pin both: slate-scoped board prices,
and a matchup-only fallback that cannot cross to another day of a series.
"""
from __future__ import annotations

import os
import unittest

import odds_fanduel as fd

HERE = os.path.dirname(os.path.abspath(__file__))
MATCHUP = "Los Angeles Angels @ Seattle Mariners"
PRIOR_START = "2026-09-24T01:40:00Z"     # the game about to start that night
CURRENT_START = "2026-09-25T01:40:00Z"   # the next slate's game
TROUT = fd.normalize_name("Mike Trout")
TROUT_PRIOR = {("hits_runs_rbis", 1): -475, ("hits", 1): -290, ("runs", 1): -180}


def event(event_id, start, values, name=None, complete=True):
    return fd.MarketEventObservation(
        event_id=event_id, name=name or f"Los Angeles Angels (P One) @ Seattle Mariners (P Two)",
        start=start, complete=complete, values=values,
    )


def feed(family, *events):
    flat = {}
    for e in events:
        if family == "general_batter":
            for key, markets in e.values.items():
                flat.setdefault(key, {}).update(markets)
        else:
            flat.update(e.values)
    return fd.MarketFeedObservation(
        family=family, root_state=fd.EVENTS_DISCOVERED, values=flat, events=tuple(events),
    )


def slate(start=CURRENT_START, matchup=MATCHUP):
    return [{"game_start": start, "matchup": matchup}]


def trout_candidate():
    return {"name": "Mike Trout", "matchup": MATCHUP, "game_start": CURRENT_START,
            "projection": {"stat": "hits_runs_rbis", "needs": 1}, "hit_probability": 0.7095}


class SlateScopedValuesTests(unittest.TestCase):
    def test_incident_exact_trout_case_by_name_across_different_matchups(self):
        # The real shape: prior game Angels @ Athletics, next game Angels @ Mariners.
        observation = feed(
            "general_batter",
            event("824951", PRIOR_START, {TROUT: dict(TROUT_PRIOR)},
                  name="Los Angeles Angels (A) @ Athletics (B)"),
            event("823087", CURRENT_START, {}),
        )
        self.assertEqual(observation.values[TROUT][("hits_runs_rbis", 1)], -475)  # legacy flat leak
        self.assertEqual(fd.slate_scoped_values(observation, slate()), {})

    def test_doubleheader_both_games_on_slate_never_share_a_price(self):
        g1, g2 = "2026-09-24T17:05:00Z", "2026-09-24T23:05:00Z"
        observation = feed(
            "general_batter",
            event("g1", g1, {TROUT: {("hits", 1): -200, ("runs", 1): 150}}),
            event("g2", g2, {TROUT: {("hits", 1): -210, ("runs", 1): 150}}),
        )
        scoped = fd.slate_scoped_values(observation, slate(start=g1) + slate(start=g2))
        self.assertNotIn(("hits", 1), scoped.get(TROUT, {}))  # conflicting: dropped
        self.assertEqual(scoped[TROUT][("runs", 1)], 150)       # identical: kept

    def test_doubleheader_game_level_conflict_is_dropped(self):
        g1, g2 = "2026-09-24T17:05:00Z", "2026-09-24T23:05:00Z"
        observation = feed(
            "first_inning",
            event("g1", g1, {MATCHUP: {"over": -110, "under": -110}}),
            event("g2", g2, {MATCHUP: {"over": 105, "under": -125}}),
        )
        self.assertEqual(fd.slate_scoped_values(observation, slate(start=g1) + slate(start=g2)), {})

    def test_match_report_names_unmatched_games(self):
        observation = feed("general_batter", event("current", CURRENT_START, {}))
        other = {"game_start": "2026-09-25T00:10:00Z", "matchup": "Texas Rangers @ Houston Astros"}
        self.assertEqual(fd.slate_match_report(observation, slate() + [other]),
                         (1, 2, ["Texas Rangers @ Houston Astros"]))

    def test_incident_prior_night_prices_never_reach_the_next_slate(self):
        observation = feed(
            "general_batter",
            event("prior", PRIOR_START, {TROUT: dict(TROUT_PRIOR)}),
            event("current", CURRENT_START, {}),
        )
        # The legacy flat dict is exactly what contaminated the board.
        self.assertEqual(observation.values[TROUT][("hits_runs_rbis", 1)], -475)
        scoped = fd.slate_scoped_values(observation, slate())
        self.assertNotIn(TROUT, scoped)
        candidate = trout_candidate()
        fd.attach_market_prices([candidate], prices=scoped, k_prices={}, fi_prices={},
                                po_prices={}, combined_k_prices={})
        self.assertIsNone(candidate.get("market_odds"))

    def test_prior_event_alone_is_not_accepted_as_the_next_days_game(self):
        # The next day's event is not listed yet; the only same-matchup event
        # is the previous night's. The matchup-only fallback must not bind.
        observation = feed("general_batter", event("prior", PRIOR_START, {TROUT: dict(TROUT_PRIOR)}))
        self.assertEqual(fd.slate_scoped_values(observation, slate()), {})
        evidence = fd.market_evidence_for_row(
            observation, {"matchup": MATCHUP, "game_start": CURRENT_START})
        self.assertEqual(evidence["values"], {})
        self.assertFalse(evidence["absence_proven"])

    def test_own_event_prices_are_kept(self):
        current = {TROUT: {("hits_runs_rbis", 1): -400}}
        observation = feed(
            "general_batter",
            event("prior", PRIOR_START, {TROUT: dict(TROUT_PRIOR)}),
            event("current", CURRENT_START, current),
        )
        scoped = fd.slate_scoped_values(observation, slate())
        self.assertEqual(scoped, current)

    def test_small_listing_drift_still_matches_by_matchup(self):
        drifted = "2026-09-25T02:10:00Z"
        values = {TROUT: {("hits", 1): -250}}
        observation = feed("general_batter", event("current", drifted, values))
        self.assertEqual(fd.slate_scoped_values(observation, slate()), values)

    def test_unknown_start_keeps_legacy_unique_matchup_behaviour(self):
        values = {TROUT: {("hits", 1): -250}}
        observation = feed("general_batter", event("current", None, values))
        self.assertEqual(fd.slate_scoped_values(observation, slate()), values)
        self.assertEqual(fd.slate_scoped_values(observation, slate(start="")), values)

    def test_doubleheader_same_matchup_is_disambiguated_by_start(self):
        g1, g2 = "2026-09-24T17:05:00Z", "2026-09-24T23:05:00Z"
        observation = feed(
            "general_batter",
            event("g1", g1, {TROUT: {("hits", 1): -200}}),
            event("g2", g2, {TROUT: {("hits", 1): -210}}),
        )
        self.assertEqual(fd.slate_scoped_values(observation, slate(start=g2))[TROUT][("hits", 1)], -210)

    def test_game_level_families_replace_by_key(self):
        prior = event("prior", PRIOR_START, {MATCHUP: {"over": -110, "under": -110}})
        current = event("current", CURRENT_START, {MATCHUP: {"over": 105, "under": -125}})
        observation = feed("first_inning", prior, current)
        self.assertEqual(fd.slate_scoped_values(observation, slate()),
                         {MATCHUP: {"over": 105, "under": -125}})

    def test_non_batter_family_values_are_not_merged_across_events(self):
        other = "Texas Rangers @ Houston Astros"
        a = event("a", CURRENT_START, {"p": {"line": 5.5, "over": -120, "under": 100}})
        b = event("b", "2026-09-25T00:10:00Z", {"q": {"line": 4.5, "over": 110}},
                  name="Texas Rangers (X) @ Houston Astros (Y)")
        observation = feed("strikeouts", a, b)
        scoped = fd.slate_scoped_values(
            observation, slate() + [{"game_start": "2026-09-25T00:10:00Z", "matchup": other}])
        self.assertEqual(scoped, {"p": {"line": 5.5, "over": -120, "under": 100},
                                  "q": {"line": 4.5, "over": 110}})

    def test_indeterminate_feed_yields_nothing(self):
        bad = fd.MarketFeedObservation("general_batter", fd.ROOT_MALFORMED, {}, (), ("x",))
        self.assertEqual(fd.slate_scoped_values(bad, slate()), {})
        self.assertEqual(fd.slate_scoped_values({"not": "an observation"}, slate()), {})


class FetchSlatePricesTests(unittest.TestCase):
    def test_root_transport_failure_raises_like_the_legacy_call(self):
        failed = fd.MarketFeedObservation("general_batter", fd.ROOT_FETCH_FAILED, {}, (), ("down",))
        with self.assertRaises(RuntimeError):
            fd.fetch_slate_prices(lambda with_evidence: failed, slate())

    def test_malformed_root_is_empty_like_the_legacy_call(self):
        bad = fd.MarketFeedObservation("general_batter", fd.ROOT_MALFORMED, {}, (), ("x",))
        self.assertEqual(fd.fetch_slate_prices(lambda with_evidence: bad, slate()), {})

    def test_scopes_the_fetched_observation_to_the_slate(self):
        observation = feed(
            "general_batter",
            event("prior", PRIOR_START, {TROUT: dict(TROUT_PRIOR)}),
            event("current", CURRENT_START, {}),
        )
        self.assertEqual(fd.fetch_slate_prices(lambda with_evidence: observation, slate()), {})

    def test_requests_structured_evidence(self):
        seen = {}

        def fetcher(**kwargs):
            seen.update(kwargs)
            return feed("general_batter", event("current", CURRENT_START, {}))

        fd.fetch_slate_prices(fetcher, slate())
        self.assertEqual(seen, {"with_evidence": True})

    def test_slate_games_from_meta_uses_scheduled_utc_start_and_matchup(self):
        meta = [{"matchup": MATCHUP, "game_start_utc": CURRENT_START, "venue": "x"}]
        self.assertEqual(fd.slate_games_from_meta(meta),
                         [{"game_start": CURRENT_START, "matchup": MATCHUP}])


class BoardBuildCallSitesTests(unittest.TestCase):
    """The board builders must never go back to the unscoped flat feeds."""

    def source(self, rel):
        with open(os.path.join(HERE, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_dashboard_build_scopes_every_price_feed(self):
        text = self.source(os.path.join("dashboard", "build_dashboard.py"))
        for bare in ("fd.fetch_prop_prices()", "fd.fetch_pitcher_strikeouts()",
                     "fd.fetch_first_inning_totals()"):
            self.assertNotIn(bare, text)
        self.assertIn("fd.fetch_slate_prices(fd.fetch_prop_prices, slate_games)", text)

    def test_generate_picks_scopes_every_price_feed(self):
        text = self.source("generate_picks.py")
        for bare in ("_fd.fetch_prop_prices()", "_fd.fetch_first_inning_totals()",
                     "_fd_early.fetch_pitcher_outs()", "_fd_early.fetch_pitcher_strikeouts()",
                     "_fd_early.fetch_combined_pitcher_strikeouts()"):
            self.assertNotIn(bare, text)


if __name__ == "__main__":
    unittest.main()
