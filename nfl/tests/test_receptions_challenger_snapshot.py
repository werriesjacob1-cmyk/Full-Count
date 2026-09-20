#!/usr/bin/env python3
"""Tests for nfl.prospective.receptions_challenger_snapshot."""
from __future__ import annotations

import unittest

from nfl.prospective.receptions_challenger_snapshot import (
    CHALLENGER_PREDICTION_SOURCE,
    ChallengerSnapshotError,
    build_challenger_snapshot_record,
    seal_challenger_snapshot,
)
from nfl.research.receptions_frozen_challenger import compare_b0_vs_frozen_challenger
from nfl.research.receptions_shadow import score_shadow_candidate

REAL_SHAPE_RESIDUALS = [0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5


def _real_b0_score():
    return score_shadow_candidate(
        projection=4.2, line=3.5, over_odds=-115, under_odds=-105,
        residuals=REAL_SHAPE_RESIDUALS,
    )


def _real_challenger_comparison(b0_score):
    return compare_b0_vs_frozen_challenger(
        projection=4.2, line=3.5,
        b0_over=b0_score["model_over_probability"],
        b0_under=b0_score["model_under_probability"],
        over_price=-115, under_price=-105,
    )


class BuildChallengerSnapshotRecordTests(unittest.TestCase):
    def test_builds_a_valid_sealable_record(self):
        b0_score = _real_b0_score()
        comparison = _real_challenger_comparison(b0_score)
        record = build_challenger_snapshot_record(
            event_id="36048239", market_id="734.186651175", gsis_id="00-0039918",
            player_name="Tetairoa McMillan", team="CAR",
            event_open_date="2026-09-20T17:01:00.000Z", line=3.5,
            over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
            availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY",
            b0_score=b0_score, challenger_comparison=comparison,
            challenger_model_version="NEGATIVE_BINOMIAL_POOLED_V1_alpha_0.09323867966867905",
            source_vintage="nflverse_2023_2024_2025_weekly_stats", feature_cutoff="2025_REG_17",
        )
        sealed = seal_challenger_snapshot(
            [record], slate_date="2026-09-20", code_sha="deadbeef",
            source_vintage="nflverse_2023_2024_2025_weekly_stats", sealed_at="2026-09-20T01:00:00Z",
        )
        self.assertEqual(sealed["record_count"], 1)
        self.assertIn("snapshot_sha256", sealed)
        self.assertEqual(sealed["records"][0]["prediction_source"], CHALLENGER_PREDICTION_SOURCE)

    def test_rejects_missing_required_field(self):
        b0_score = _real_b0_score()
        comparison = _real_challenger_comparison(b0_score)
        with self.assertRaises(ChallengerSnapshotError):
            build_challenger_snapshot_record(
                event_id="", market_id="m1", gsis_id="00-0039918",
                player_name="Player", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
                line=3.5, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
                availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY",
                b0_score=b0_score, challenger_comparison=comparison,
                challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
            )

    def test_rejects_fake_b0_score_missing_real_shape(self):
        b0_score = _real_b0_score()
        comparison = _real_challenger_comparison(b0_score)
        with self.assertRaises(ChallengerSnapshotError):
            build_challenger_snapshot_record(
                event_id="e1", market_id="m1", gsis_id="00-0039918",
                player_name="Player", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
                line=3.5, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
                availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY",
                b0_score={"not": "a real score"}, challenger_comparison=comparison,
                challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
            )

    def test_rejects_fake_challenger_comparison(self):
        b0_score = _real_b0_score()
        with self.assertRaises(ChallengerSnapshotError):
            build_challenger_snapshot_record(
                event_id="e1", market_id="m1", gsis_id="00-0039918",
                player_name="Player", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
                line=3.5, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
                availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY",
                b0_score=b0_score, challenger_comparison={"not": "real"},
                challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
            )

    def test_quarantined_decision_status_is_sealable_too(self):
        b0_score = _real_b0_score()
        comparison = _real_challenger_comparison(b0_score)
        record = build_challenger_snapshot_record(
            event_id="e1", market_id="m1", gsis_id="00-0039918",
            player_name="Player", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
            line=3.5, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
            availability_status="UNKNOWN_GAME_COVERAGE", decision_status="QUARANTINED",
            b0_score=b0_score, challenger_comparison=comparison,
            challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
        )
        sealed = seal_challenger_snapshot(
            [record], slate_date="2026-09-20", code_sha="deadbeef",
            source_vintage="v", sealed_at="2026-09-20T01:00:00Z",
        )
        self.assertEqual(sealed["records"][0]["decision_status"], "QUARANTINED")

    def test_rejects_invalid_decision_status_via_the_real_shadow_snapshot_validator(self):
        b0_score = _real_b0_score()
        comparison = _real_challenger_comparison(b0_score)
        record = build_challenger_snapshot_record(
            event_id="e1", market_id="m1", gsis_id="00-0039918",
            player_name="Player", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
            line=3.5, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
            availability_status="NOT_LISTED_INACTIVE", decision_status="PLAY",
            b0_score=b0_score, challenger_comparison=comparison,
            challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
        )
        with self.assertRaises(ValueError):
            seal_challenger_snapshot(
                [record], slate_date="2026-09-20", code_sha="deadbeef",
                source_vintage="v", sealed_at="2026-09-20T01:00:00Z",
            )

    def test_two_records_seal_deterministically_and_are_distinguishable(self):
        b0_score = _real_b0_score()
        comparison = _real_challenger_comparison(b0_score)
        record_a = build_challenger_snapshot_record(
            event_id="e1", market_id="m1", gsis_id="00-0039918",
            player_name="Player A", team="CAR", event_open_date="2026-09-20T17:01:00.000Z",
            line=3.5, over_odds=-115, under_odds=-105, captured_at="2026-09-20T00:30:00Z",
            availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY",
            b0_score=b0_score, challenger_comparison=comparison,
            challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
        )
        record_b = build_challenger_snapshot_record(
            event_id="e1", market_id="m2", gsis_id="00-0039919",
            player_name="Player B", team="ATL", event_open_date="2026-09-20T17:01:00.000Z",
            line=2.5, over_odds=-110, under_odds=-110, captured_at="2026-09-20T00:30:00Z",
            availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY",
            b0_score=b0_score, challenger_comparison=comparison,
            challenger_model_version="v1", source_vintage="v", feature_cutoff="c",
        )
        sealed_1 = seal_challenger_snapshot(
            [record_a, record_b], slate_date="2026-09-20", code_sha="deadbeef",
            source_vintage="v", sealed_at="2026-09-20T01:00:00Z",
        )
        sealed_2 = seal_challenger_snapshot(
            [record_b, record_a], slate_date="2026-09-20", code_sha="deadbeef",
            source_vintage="v", sealed_at="2026-09-20T01:00:00Z",
        )
        self.assertEqual(sealed_1["snapshot_sha256"], sealed_2["snapshot_sha256"])
        self.assertEqual(sealed_1["record_count"], 2)


if __name__ == "__main__":
    unittest.main()
