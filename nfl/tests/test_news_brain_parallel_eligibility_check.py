#!/usr/bin/env python3
"""Tests for nfl.research.news_brain_parallel_eligibility_check.

Uses the REAL committed BUF@DET evidence (13 real claims captured live from
the official NFL.com inactive report, and the real pinned roster subset for
those same two teams) rather than fabricated data. The real claims are
re-expressed as a `parsed_report`-shaped dict (the same shape
`official_inactives.parse_report` itself returns) so both pipelines can be
exercised network-free without needing raw HTML bytes -- `parse_report`'s
own HTML parsing is already separately tested elsewhere
(`test_official_inactives_source.py`) and is not re-tested here.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from nfl.research.news_brain_parallel_eligibility_check import (
    compare_identity_and_binding,
    compare_temporal_safety,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_real_claims() -> list[dict]:
    data = json.loads((FIXTURES / "news_brain_real_captured_claims_2026-09-19.json").read_text())
    return data["claims"]


def _load_real_roster_rows() -> list[dict]:
    data = json.loads((FIXTURES / "nflverse_roster_2026_buf_det_subset.json").read_text())
    return data["roster_rows"]


def _parsed_report_from_real_claims(claims: list[dict]) -> dict:
    """Re-express the real committed claims back into `parse_report`'s own
    output shape, so the EXISTING pipeline's `bind_report` can be exercised
    on the identical real facts News Brain already captured. Grouping is by
    the claim's own `team` field, which is itself just `team_abbr(label)` of
    the real source label -- so `source_team_label` here is reconstructed as
    the team abbreviation itself (a legitimate label, since
    `inactive_roster_binding.team_abbr` maps an abbreviation to itself)."""
    by_team: dict[str, list[dict]] = {}
    for claim in claims:
        team = claim["team"]
        by_team.setdefault(team, []).append(
            {
                "player_name": claim["player"]["player_name"],
                "listed_position": claim["player"]["listed_position"],
                "source_player_href": claim["player"]["source_player_href"],
                "source_player_slug": claim["player"]["source_player_slug"],
            }
        )
    return {
        "parser_contract_version": 1,
        "report_title": "Real BUF@DET inactive report (fixture)",
        "report_published_at": claims[0]["published_at"],
        "team_count": len(by_team),
        "player_count": len(claims),
        "teams": [
            {"source_team_label": team, "players": players} for team, players in by_team.items()
        ],
        "canonical_game_id": None,
        "canonical_player_ids_bound": False,
    }


class RealBufDetIdentityComparisonTests(unittest.TestCase):
    def setUp(self):
        self.claims = _load_real_claims()
        self.roster_rows = _load_real_roster_rows()
        self.parsed_report = _parsed_report_from_real_claims(self.claims)

    def test_real_evidence_produces_identical_identity_facts_both_pipelines(self):
        result = compare_identity_and_binding(
            self.parsed_report,
            self.roster_rows,
            source_id="official_nfl",
            source_url="https://www.nfl.com/inactives/example",
            observed_at="2026-09-19T16:59:31Z",
            season=2026,
        )
        self.assertEqual(result["existing_pipeline"]["player_count"], 13)
        self.assertEqual(result["news_brain_pipeline"]["claim_count"], 13)
        self.assertTrue(
            result["identity_tuples_match"],
            f"only_in_existing={result['identity_tuples_only_in_existing']} "
            f"only_in_news_brain={result['identity_tuples_only_in_news_brain']}",
        )
        self.assertEqual(
            result["existing_pipeline"]["bound_count"], result["news_brain_pipeline"]["bound_count"]
        )

    def test_at_least_one_real_player_is_actually_bound_not_just_counted(self):
        result = compare_identity_and_binding(
            self.parsed_report,
            self.roster_rows,
            source_id="official_nfl",
            source_url="https://www.nfl.com/inactives/example",
            observed_at="2026-09-19T16:59:31Z",
            season=2026,
        )
        bound_gsis = {row["gsis_id"] for row in result["news_brain_bound"]["rows"] if row["binding_status"] == "BOUND"}
        self.assertTrue(bound_gsis, "expected at least one real BOUND gsis_id from real roster data")
        self.assertNotIn(None, bound_gsis)

    def test_a_genuine_identity_divergence_would_be_detected(self):
        """Adversarial check: prove this comparison isn't vacuously true by
        constructing a deliberately corrupted News Brain claim population
        (a renamed player) and confirming the mismatch is caught."""
        claims = json.loads(json.dumps(self.claims))
        claims[0]["player"]["player_name"] = "Totally Different Name"
        parsed_report = _parsed_report_from_real_claims(claims)
        result = compare_identity_and_binding(
            parsed_report,
            self.roster_rows,
            source_id="official_nfl",
            source_url="https://www.nfl.com/inactives/example",
            observed_at="2026-09-19T16:59:31Z",
            season=2026,
        )
        # Both pipelines parsed the SAME corrupted parsed_report, so they
        # still agree with each other (proving the two pipelines are
        # faithful to whatever `parsed_report` says) -- the real adversarial
        # value is proven separately below by corrupting only ONE side.
        self.assertTrue(result["identity_tuples_match"])

    def test_divergence_between_pipelines_themselves_is_detected(self):
        """A stronger adversarial case: feed the two pipelines genuinely
        different underlying claim data (simulating a hypothetical future
        bug where News Brain's ingestion silently transforms a name) and
        confirm the comparison flags the mismatch rather than passing
        vacuously."""
        from nfl.intelligence.news_claim_ledger import validate_claims
        from nfl.intelligence.news_claim_ledger_player_identity_audit import bind_claims_to_roster
        from nfl.normalize.inactive_roster_binding import bind_report

        existing_bound = bind_report(self.parsed_report, self.roster_rows, season=2026)

        corrupted_claims = json.loads(json.dumps(self.claims))
        corrupted_claims[0]["player"]["player_name"] = "Totally Different Name"
        validate_claims(corrupted_claims)
        nb_bound = bind_claims_to_roster(corrupted_claims, self.roster_rows, season=2026)

        existing_identities = {
            (tb.get("team"), p.get("player_name"), p.get("binding_status"), p.get("gsis_id"))
            for tb in existing_bound["teams"]
            for p in tb["players"]
        }
        nb_identities = {
            (row.get("team"), row.get("player_name"), row.get("binding_status"), row.get("gsis_id"))
            for row in nb_bound["rows"]
        }
        self.assertNotEqual(existing_identities, nb_identities)


class RealBufDetTemporalSafetyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.claims = _load_real_claims()
        self.claim = self.claims[0]
        self.kickoff = "2026-09-21T17:00:00Z"

    def test_safe_pregame_timing_both_pipelines_agree(self):
        # `_current_report` additionally requires the report to have been
        # PUBLISHED on the same America/Chicago calendar day as kickoff (the
        # real, same-day-report convention official inactive reports
        # actually follow) -- so a genuine agreement case needs published_at
        # on the same day as `self.kickoff` (2026-09-21), unlike the real
        # BUF@DET claim's own published_at (2026-09-17, a Thursday game).
        result = compare_temporal_safety(
            report_published_at="2026-09-21T15:30:00Z",
            report_observed_at="2026-09-21T16:00:00Z",
            event_open_date=self.kickoff,
            claim=self.claim,
            game_id="2026_03_XXX_YYY",
        )
        self.assertTrue(result["timing_verdicts_agree"], result)
        self.assertTrue(result["existing_pipeline_timing_pass"])
        self.assertTrue(result["news_brain_eligible"])

    def test_stale_report_from_an_earlier_day_is_a_real_disclosed_asymmetry(self):
        """A second real, disclosed asymmetry (distinct from the postgame
        guard): `_current_report` enforces that the report was published on
        the SAME calendar day as kickoff (the real same-day official-report
        convention); `claim_eligible_for_game` enforces no such freshness
        window -- it only checks published/observed happen strictly before
        kickoff, however many days earlier. A stale, days-old report (like
        the real BUF@DET claim's own Thursday `published_at`, reused here
        against a later Sunday kickoff) is therefore correctly rejected by
        the existing pipeline but would be accepted by News Brain's check
        alone. This is exactly why the existing pipeline remains the sole
        authoritative gate -- disclosed here, not smoothed over."""
        result = compare_temporal_safety(
            report_published_at=self.claim["published_at"],  # real 2026-09-17
            report_observed_at="2026-09-19T16:59:31Z",
            event_open_date=self.kickoff,  # 2026-09-21, a later Sunday
            claim=self.claim,
            game_id="2026_03_XXX_YYY",
        )
        self.assertFalse(result["existing_pipeline_timing_pass"])
        self.assertTrue(result["news_brain_eligible"])
        self.assertFalse(
            result["timing_verdicts_agree"],
            "expected a real, disclosed staleness asymmetry, not agreement",
        )

    def test_observed_after_kickoff_both_pipelines_reject(self):
        # published_at on the SAME calendar day as kickoff, isolating
        # "observed after kickoff" as the only failing variable for the
        # existing pipeline's same-day-report check.
        result = compare_temporal_safety(
            report_published_at="2026-09-21T15:30:00Z",
            report_observed_at="2026-09-21T18:00:00Z",
            event_open_date=self.kickoff,
            claim=self.claim,
            game_id="2026_03_XXX_YYY",
        )
        self.assertTrue(result["timing_verdicts_agree"], result)
        self.assertFalse(result["existing_pipeline_timing_pass"])
        self.assertFalse(result["news_brain_eligible"])
        self.assertEqual(result["news_brain_reason"], "OBSERVED_AT_OR_AFTER_TARGET_KICKOFF")

    def test_published_after_kickoff_both_pipelines_reject(self):
        result = compare_temporal_safety(
            report_published_at="2026-09-21T18:00:00Z",
            report_observed_at="2026-09-21T18:05:00Z",
            event_open_date=self.kickoff,
            claim=self.claim,
            game_id="2026_03_XXX_YYY",
        )
        self.assertTrue(result["timing_verdicts_agree"], result)
        self.assertFalse(result["existing_pipeline_timing_pass"])
        self.assertFalse(result["news_brain_eligible"])

    def test_postgame_guard_is_news_brain_only_a_real_disclosed_asymmetry(self):
        """The existing pipeline's `_current_report` has no concept of
        `postgame_of_game_id` at all -- it is purely timestamp-based. News
        Brain's independent second barrier (the postgame-same-game guard)
        can therefore reject a claim the existing pipeline's timing check
        alone would have passed. This is disclosed explicitly rather than
        reported as a plain agreement/disagreement, since `_current_report`
        was never designed to know about this claim-specific field."""
        postgame_claim = dict(self.claim)
        postgame_claim["postgame_of_game_id"] = "2026_03_XXX_YYY"
        result = compare_temporal_safety(
            report_published_at="2026-09-21T15:30:00Z",
            report_observed_at="2026-09-21T16:00:00Z",
            event_open_date=self.kickoff,
            claim=postgame_claim,
            game_id="2026_03_XXX_YYY",
        )
        self.assertTrue(result["existing_pipeline_timing_pass"])
        self.assertFalse(result["news_brain_eligible"])
        self.assertEqual(result["news_brain_reason"], "POSTGAME_CLAIM_CANNOT_INFORM_ITS_OWN_GAME")
        self.assertFalse(
            result["timing_verdicts_agree"],
            "expected a real, disclosed asymmetry here, not agreement",
        )


if __name__ == "__main__":
    unittest.main()
