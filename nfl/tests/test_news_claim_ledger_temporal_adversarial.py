#!/usr/bin/env python3
"""Audit item 5: independent adversarial temporal-safety fixtures for
`nfl.intelligence.news_claim_ledger.claim_eligible_for_game`.

PR #146 wrote its own test proving a postgame claim cannot attach back to
its own game. This file does NOT reuse that test. It builds fresh
adversarial fixtures, including two the original PR did not cover: (a) a
same-team-pair REMATCH (using real 2026 schedule game_ids, not merely a
different arbitrary game_id) to prove eligibility is keyed on exact
game_id, not team-pair similarity; (b) a real nflverse `game_id`-format
representation drift (`game_id` vs `old_game_id`, both real values for the
identical game) that defeats the `postgame_of_game_id` string-equality
barrier on purpose, to test whether the INDEPENDENT timestamp barrier still
saves the day, and to disclose the residual risk when it does not; and (c)
the exact `observed_at == kickoff` boundary instant.
"""
import unittest

from nfl.intelligence.news_claim_ledger import (
    LEDGER_SCHEMA_VERSION,
    claim_eligible_for_game,
    make_claim_id,
)


def _claim(**overrides) -> dict:
    claim = {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "claim_id": make_claim_id("audit", "fixture", overrides.get("claim_id", "1")),
        "source_id": "official_nfl",
        "source_tier": "A",
        "evidence_class": "DIRECT_QUOTE",
        "claim_type": "POSTGAME_ROLE_EXPLANATION",
        "direct_observation": True,
        "reporter": {"name": "Test Reporter", "outlet": "Test Outlet", "role": "beat_writer"},
        "team": "GB",
        "player": {
            "player_name": "Example Player",
            "gsis_id": None,
            "source_player_href": None,
            "source_player_slug": None,
            "listed_position": "WR",
        },
        "concerns_game_id": None,
        "concerns_teams": ["GB", "MIN"],
        "postgame_of_game_id": None,
        "published_at": None,
        "observed_at": "2026-09-13T21:00:00Z",
        "effective_from": None,
        "effective_until": None,
        "corrected_at": None,
        "content_summary": "Adversarial-fixture claim for temporal-safety audit.",
        "corroborations": [],
        "contradictions": [],
        "correction_of": None,
        "resolution": None,
    }
    claim.update({k: v for k, v in overrides.items() if k != "claim_id"})
    return claim


class ObservedAtKickoffBoundaryTests(unittest.TestCase):
    """Item 5b: observed_at exactly equal to kickoff_at."""

    def test_observed_at_exactly_equal_to_kickoff_fails_closed(self):
        claim = _claim(observed_at="2026-09-13T17:00:00Z")
        result = claim_eligible_for_game(claim, "2026_01_GB_MIN", "2026-09-13T17:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "OBSERVED_AT_OR_AFTER_TARGET_KICKOFF")

    def test_observed_at_one_second_before_kickoff_is_eligible(self):
        claim = _claim(observed_at="2026-09-13T16:59:59Z")
        result = claim_eligible_for_game(claim, "2026_01_GB_MIN", "2026-09-13T17:00:00Z")
        self.assertTrue(result["eligible"])

    def test_observed_at_one_second_after_kickoff_fails_closed(self):
        claim = _claim(observed_at="2026-09-13T17:00:01Z")
        result = claim_eligible_for_game(claim, "2026_01_GB_MIN", "2026-09-13T17:00:00Z")
        self.assertFalse(result["eligible"])

    def test_published_at_exactly_equal_to_kickoff_also_fails_closed(self):
        claim = _claim(
            observed_at="2026-09-13T16:00:00Z",
            published_at="2026-09-13T17:00:00Z",
        )
        result = claim_eligible_for_game(claim, "2026_01_GB_MIN", "2026-09-13T17:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "PUBLISHED_AT_OR_AFTER_TARGET_KICKOFF")


class RealRematchCrossGameTests(unittest.TestCase):
    """Item 5a: a same-team-pair rematch using real 2026 schedule game_ids
    (GB @ MIN week 1, 2026-09-13; MIN @ GB week 10, 2026-11-15 -- both real
    rows in the pinned nflverse/nfldata games.csv commit, see
    test_news_claim_ledger_game_binding_audit.py)."""

    def test_week1_postgame_explanation_cannot_attach_to_week1_itself(self):
        claim = _claim(
            postgame_of_game_id="2026_01_GB_MIN",
            observed_at="2026-09-14T02:00:00Z",  # after week1 kickoff
        )
        result = claim_eligible_for_game(claim, "2026_01_GB_MIN", "2026-11-15T18:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "POSTGAME_CLAIM_CANNOT_INFORM_ITS_OWN_GAME")

    def test_week1_postgame_explanation_can_legitimately_inform_the_week10_rematch(self):
        # Same two teams, a real different game_id -- must be treated as a
        # genuinely different game, not blocked by team-pair similarity.
        claim = _claim(
            postgame_of_game_id="2026_01_GB_MIN",
            observed_at="2026-09-14T02:00:00Z",
        )
        result = claim_eligible_for_game(claim, "2026_10_MIN_GB", "2026-11-15T18:00:00Z")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "ELIGIBLE_PREGAME_FEATURE")


class GameIdRepresentationDriftTests(unittest.TestCase):
    """Item 5, going beyond PR #146's own test: real nflverse `games.csv`
    carries TWO id formats for the identical game (`game_id` and
    `old_game_id` -- verified in the audit's game-binding fixture that this
    is true for ALL 7,548 real rows, not an edge case). If a claim's
    `postgame_of_game_id` were ever recorded in one format while a caller
    queries eligibility using the other format for the SAME real game, the
    exact-string-equality barrier is defeated by representation drift
    alone -- this test proves that, and proves the timestamp barrier is
    what actually saves correctness in that scenario, and also discloses
    the residual risk when BOTH barriers are simultaneously defeated.
    """

    # Real row: game_id="1999_01_KC_CHI", old_game_id="1999091206",
    # gameday=1999-09-12 (from games_csv_audit_subset.json).
    REAL_GAME_ID = "1999_01_KC_CHI"
    REAL_OLD_GAME_ID = "1999091206"
    REAL_KICKOFF = "1999-09-12T17:00:00Z"

    def test_id_format_drift_defeats_the_string_equality_barrier_alone(self):
        # The postgame tag is real and correct in content (it IS a postgame
        # explanation of this exact game) but was recorded using the OLD id
        # format, while eligibility is (realistically) queried with the
        # CURRENT game_id -- the two strings never match.
        claim = _claim(
            postgame_of_game_id=self.REAL_OLD_GAME_ID,
            observed_at="1999-09-12T21:00:00Z",  # correctly after kickoff
        )
        # If only the id-equality barrier existed, this would incorrectly
        # report eligible (the string comparison plainly fails to match).
        self.assertNotEqual(claim["postgame_of_game_id"], self.REAL_GAME_ID)

        result = claim_eligible_for_game(claim, self.REAL_GAME_ID, self.REAL_KICKOFF)
        # The INDEPENDENT timestamp barrier still fires and saves
        # correctness, because observed_at is genuinely after kickoff.
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "OBSERVED_AT_OR_AFTER_TARGET_KICKOFF")

    def test_disclosed_residual_risk_when_both_barriers_are_defeated_together(self):
        # Real, disclosed residual risk (not a claim that this happens in
        # practice today): if the id-format barrier is ALREADY defeated by
        # representation drift, AND a separate, independent timestamp
        # bookkeeping bug also logs observed_at as before kickoff for a
        # claim that is genuinely postgame, `claim_eligible_for_game`
        # currently has no third check and returns eligible=True. Two
        # independent errors must coincide for this to matter -- PR #146's
        # real ingestion path never sets postgame_of_game_id in the old
        # format (it always leaves it None), so this is not observed in any
        # real captured claim today, but it is a real property of the
        # current two-barrier design worth carrying forward, not silently
        # smoothed over as impossible.
        claim = _claim(
            postgame_of_game_id=self.REAL_OLD_GAME_ID,
            observed_at="1999-09-12T16:00:00Z",  # bug: before kickoff, though truly postgame
        )
        result = claim_eligible_for_game(claim, self.REAL_GAME_ID, self.REAL_KICKOFF)
        self.assertTrue(result["eligible"])  # the real, disclosed residual gap


class PostgameBarrierAloneAdversarialTests(unittest.TestCase):
    """Independently re-verifies PR #146's own claim that the
    postgame_of_game_id barrier is a SEPARATE mechanism from the timestamp
    check, using a fixture PR #146 did not write: a timestamp bookkeeping
    bug that makes the claim LOOK pregame-eligible by the clock alone, with
    the id barrier as the only thing standing in the way."""

    def test_postgame_barrier_alone_blocks_even_when_the_clock_would_allow_it(self):
        claim = _claim(
            postgame_of_game_id="2026_01_GB_MIN",
            observed_at="2026-09-10T00:00:00Z",  # before kickoff -- a clock bug
        )
        result = claim_eligible_for_game(claim, "2026_01_GB_MIN", "2026-09-13T17:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "POSTGAME_CLAIM_CANNOT_INFORM_ITS_OWN_GAME")


if __name__ == "__main__":
    unittest.main()
