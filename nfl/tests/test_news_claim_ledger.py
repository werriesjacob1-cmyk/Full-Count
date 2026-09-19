#!/usr/bin/env python3
"""Schema validation, fail-closed, and temporal-safety tests for the News
Brain atomic claim ledger (`nfl/intelligence/news_claim_ledger.py`).
"""
from __future__ import annotations

import copy
import unittest

from nfl.intelligence.news_claim_ledger import (
    LEDGER_SCHEMA_VERSION,
    NewsClaimLedgerError,
    claim_eligible_for_game,
    make_claim_id,
    reporter_reliability_scoreboard,
    validate_claim,
    validate_claims,
)


def _base_claim(**overrides):
    claim = {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "claim_id": make_claim_id("official_nfl", "https://www.nfl.com/x", "AVAILABILITY", "player-1"),
        "source_id": "official_nfl",
        "source_tier": "A",
        "evidence_class": "OFFICIAL_EVENT",
        "claim_type": "AVAILABILITY",
        "direct_observation": True,
        "reporter": {"name": "NFL.com", "outlet": "NFL.com", "role": "official_league_publication"},
        "team": "KC",
        "player": {
            "player_name": "Example Player",
            "gsis_id": None,
            "source_player_href": "/players/example-player",
            "source_player_slug": "example-player",
            "listed_position": "WR",
        },
        "concerns_game_id": None,
        "concerns_teams": ["KC", "DEN"],
        "postgame_of_game_id": None,
        "published_at": "2026-09-18T20:00:00Z",
        "observed_at": "2026-09-18T20:05:00Z",
        "effective_from": None,
        "effective_until": None,
        "corrected_at": None,
        "content_summary": "Example Player (WR) listed inactive by Chiefs per official NFL.com inactive report",
        "corroborations": [],
        "contradictions": [],
        "correction_of": None,
        "resolution": None,
    }
    claim.update(overrides)
    return claim


class ValidClaimTests(unittest.TestCase):
    def test_valid_claim_passes(self):
        summary = validate_claim(_base_claim())
        self.assertEqual(summary["source_tier"], "A")
        self.assertEqual(summary["claim_type"], "AVAILABILITY")
        self.assertEqual(summary["team"], "KC")

    def test_valid_batch_summarizes(self):
        claims = [
            _base_claim(claim_id=make_claim_id("a", 1)),
            _base_claim(claim_id=make_claim_id("a", 2), team="DEN"),
        ]
        summary = validate_claims(claims)
        self.assertEqual(summary["claim_count"], 2)
        self.assertEqual(summary["unique_claim_ids"], 2)
        self.assertEqual(summary["by_tier"], {"A": 2})
        self.assertEqual(summary["by_team"], {"KC": 1, "DEN": 1})

    def test_null_team_is_recorded_as_unresolved_not_dropped(self):
        claims = [_base_claim(claim_id=make_claim_id("u", 1), team=None, concerns_teams=None)]
        summary = validate_claims(claims)
        self.assertEqual(summary["by_team"], {"UNRESOLVED": 1})


class FailClosedTests(unittest.TestCase):
    def test_missing_field_fails_closed(self):
        claim = _base_claim()
        del claim["observed_at"]
        with self.assertRaisesRegex(NewsClaimLedgerError, "missing required fields"):
            validate_claim(claim)

    def test_unknown_source_tier_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "source_tier"):
            validate_claim(_base_claim(source_tier="Z"))

    def test_unknown_evidence_class_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "evidence_class"):
            validate_claim(_base_claim(evidence_class="VIBES"))

    def test_unknown_claim_type_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "claim_type"):
            validate_claim(_base_claim(claim_type="MADE_UP_TYPE"))

    def test_non_bool_direct_observation_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "direct_observation"):
            validate_claim(_base_claim(direct_observation="yes"))

    def test_malformed_reporter_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "reporter"):
            validate_claim(_base_claim(reporter={"name": "Someone"}))

    def test_unknown_team_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "team"):
            validate_claim(_base_claim(team="ZZZ"))

    def test_bad_concerns_teams_shape_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "concerns_teams"):
            validate_claim(_base_claim(concerns_teams=["KC"]))

    def test_naive_timestamp_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "observed_at"):
            validate_claim(_base_claim(observed_at="2026-09-18T20:05:00"))

    def test_unparseable_timestamp_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "observed_at"):
            validate_claim(_base_claim(observed_at="not-a-timestamp"))

    def test_bad_corroboration_relation_fails_closed(self):
        claim = _base_claim(corroborations=[
            {"claim_id": "nc_other", "relation": "CONTRADICT", "noted_at": "2026-09-18T20:10:00Z"}
        ])
        with self.assertRaisesRegex(NewsClaimLedgerError, "corroborations"):
            validate_claim(claim)

    def test_resolved_without_valid_outcome_fails_closed(self):
        claim = _base_claim(resolution={"resolved": True, "outcome": None, "resolved_at": None})
        with self.assertRaisesRegex(NewsClaimLedgerError, "resolution.outcome"):
            validate_claim(claim)

    def test_duplicate_claim_id_in_batch_fails_closed(self):
        claim = _base_claim()
        with self.assertRaisesRegex(NewsClaimLedgerError, "duplicate claim_id"):
            validate_claims([claim, copy.deepcopy(claim)])

    def test_empty_batch_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "non-empty"):
            validate_claims([])

    def test_not_a_mapping_fails_closed(self):
        with self.assertRaisesRegex(NewsClaimLedgerError, "mapping"):
            validate_claim(["not", "a", "claim"])


class TemporalSafetyTests(unittest.TestCase):
    """Proves the design doc's core temporal-safety principle in code."""

    def test_pregame_official_claim_is_eligible_for_its_own_game(self):
        claim = _base_claim(
            published_at="2026-09-21T16:30:00Z",
            observed_at="2026-09-21T16:35:00Z",
        )
        result = claim_eligible_for_game(claim, "2026_03_DEN_KC", "2026-09-21T17:00:00Z")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "ELIGIBLE_PREGAME_FEATURE")

    def test_claim_observed_after_kickoff_is_ineligible(self):
        claim = _base_claim(
            published_at="2026-09-21T17:05:00Z",
            observed_at="2026-09-21T17:10:00Z",
        )
        result = claim_eligible_for_game(claim, "2026_03_DEN_KC", "2026-09-21T17:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "OBSERVED_AT_OR_AFTER_TARGET_KICKOFF")

    def test_claim_published_after_kickoff_is_ineligible_even_if_observed_before(self):
        # Contrived but must fail closed: a published_at that outruns kickoff
        # is itself a red flag and must not be waved through just because
        # observed_at happens to look earlier.
        claim = _base_claim(
            published_at="2026-09-21T18:00:00Z",
            observed_at="2026-09-21T16:00:00Z",
        )
        result = claim_eligible_for_game(claim, "2026_03_DEN_KC", "2026-09-21T17:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "PUBLISHED_AT_OR_AFTER_TARGET_KICKOFF")

    def test_postgame_claim_cannot_attach_back_to_its_own_game_as_pregame_feature(self):
        """The exact scenario the task requires a real proof for.

        A coach's Monday postgame explanation about game G is observed well
        before that SAME game's kickoff would ever recur (it can't -- G is
        over), but more importantly it is explicitly tagged
        `postgame_of_game_id=G`. That tag alone must block it from ever being
        attached to G as a pregame feature, independent of any timestamp
        arithmetic.
        """
        claim = _base_claim(
            claim_type="POSTGAME_ROLE_EXPLANATION",
            evidence_class="DIRECT_QUOTE",
            direct_observation=True,
            postgame_of_game_id="2026_02_KC_DEN",
            concerns_game_id="2026_02_KC_DEN",
            published_at="2026-09-15T20:00:00Z",
            observed_at="2026-09-15T20:05:00Z",
            content_summary="Coach explains why Player X's role increased after the game.",
        )
        # Even against a fabricated future "kickoff" for the same game id
        # (which cannot really happen -- games do not replay), the explicit
        # postgame tag must still win over the raw timestamp comparison.
        result = claim_eligible_for_game(claim, "2026_02_KC_DEN", "2026-09-22T17:00:00Z")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "POSTGAME_CLAIM_CANNOT_INFORM_ITS_OWN_GAME")

    def test_postgame_claim_can_inform_a_later_different_game(self):
        claim = _base_claim(
            claim_type="POSTGAME_ROLE_EXPLANATION",
            evidence_class="DIRECT_QUOTE",
            postgame_of_game_id="2026_02_KC_DEN",
            concerns_game_id="2026_02_KC_DEN",
            published_at="2026-09-15T20:00:00Z",
            observed_at="2026-09-15T20:05:00Z",
            content_summary="Coach explains why Player X's role increased after the game.",
        )
        result = claim_eligible_for_game(claim, "2026_03_KC_LAC", "2026-09-22T17:00:00Z")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "ELIGIBLE_PREGAME_FEATURE")

    def test_malformed_claim_fails_closed_in_eligibility_check_too(self):
        claim = _base_claim()
        del claim["postgame_of_game_id"]
        with self.assertRaises(NewsClaimLedgerError):
            claim_eligible_for_game(claim, "2026_03_DEN_KC", "2026-09-21T17:00:00Z")

    def test_unparseable_kickoff_fails_closed(self):
        with self.assertRaises(NewsClaimLedgerError):
            claim_eligible_for_game(_base_claim(), "2026_03_DEN_KC", "not-a-timestamp")


class ReliabilityScoreboardTests(unittest.TestCase):
    def test_no_resolved_claims_yields_honest_none_not_a_fabricated_score(self):
        claims = [_base_claim(claim_id=make_claim_id("r", 1))]
        result = reporter_reliability_scoreboard(claims)
        self.assertEqual(result["testable_claim_count"], 0)
        self.assertIsNone(result["league_baseline"]["raw_agreement_rate"])
        for scores in result["by_reporter"].values():
            self.assertIsNone(scores.get("shrunk_agreement_rate"))

    def test_resolved_claims_populate_league_and_reporter_cells(self):
        confirmed = _base_claim(
            claim_id=make_claim_id("r", 2),
            resolution={"resolved": True, "outcome": "CONFIRMED", "resolved_at": "2026-09-22T00:00:00Z", "notes": None},
        )
        refuted = _base_claim(
            claim_id=make_claim_id("r", 3),
            resolution={"resolved": True, "outcome": "REFUTED", "resolved_at": "2026-09-22T00:00:00Z", "notes": None},
        )
        untestable = _base_claim(claim_id=make_claim_id("r", 4))
        result = reporter_reliability_scoreboard([confirmed, refuted, untestable])
        self.assertEqual(result["testable_claim_count"], 2)
        self.assertAlmostEqual(result["league_baseline"]["raw_agreement_rate"], 0.5)
        nfl_reporter = result["by_reporter"]["NFL.com"]
        self.assertEqual(nfl_reporter["n"], 2)
        self.assertAlmostEqual(nfl_reporter["shrunk_agreement_rate"], 0.5)

    def test_untestable_claims_never_lower_a_reporters_score(self):
        confirmed = _base_claim(
            claim_id=make_claim_id("r", 5),
            resolution={"resolved": True, "outcome": "CONFIRMED", "resolved_at": "2026-09-22T00:00:00Z", "notes": None},
        )
        many_untestable = [
            _base_claim(claim_id=make_claim_id("r", 6 + i))
            for i in range(20)
        ]
        result = reporter_reliability_scoreboard([confirmed] + many_untestable)
        self.assertEqual(result["testable_claim_count"], 1)
        self.assertEqual(result["total_claim_count"], 21)
        self.assertAlmostEqual(result["by_reporter"]["NFL.com"]["raw_agreement_rate"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
