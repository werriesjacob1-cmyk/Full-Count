#!/usr/bin/env python3
"""Contracts for grading sealed NFL shadow observations without population drift."""
import unittest

from nfl.prospective import shadow_grader, shadow_snapshot


def record(
    *,
    player="Jalen Hurts",
    gsis="00-1",
    team="PHI",
    away="PHI",
    home="KC",
    line=214.5,
    direction="OVER",
    decision="SHADOW_ONLY",
):
    return {
        "event_id": "999",
        "market_id": "734.1",
        "market": "passing_yards",
        "player_name": player,
        "gsis_id": gsis,
        "team": team,
        "event_away_team": away,
        "event_home_team": home,
        "line": line,
        "over_odds": -110,
        "under_odds": -110,
        "research_direction": direction,
        "decision_status": decision,
        "captured_at": "2026-09-14T22:00:00Z",
        "event_open_date": "2026-09-15T00:15:00Z",
        "availability_status": "NOT_LISTED_INACTIVE",
    }


def snapshot(*records):
    return shadow_snapshot.seal_snapshot(
        list(records),
        slate_date="2026-09-14",
        code_sha="test-sha",
        source_vintage="test-source",
        sealed_at="2026-09-14T22:05:00Z",
    )


def outcome(
    *,
    gsis="00-1",
    team="PHI",
    opponent="KC",
    yards=250,
    season=2026,
    season_type="REG",
    week=1,
):
    return {
        "player_id": gsis,
        "team": team,
        "opponent_team": opponent,
        "passing_yards": yards,
        "season": season,
        "season_type": season_type,
        "week": week,
    }


class ShadowGraderTests(unittest.TestCase):
    def test_exact_bound_over_hit(self):
        snap = snapshot(record())
        out = shadow_grader.grade_snapshot(snap, [outcome(yards=250)], season=2026)
        row = out["records"][0]
        self.assertEqual(row["outcome_status"], "SETTLED")
        self.assertEqual(row["actual_passing_yards"], 250.0)
        self.assertEqual(row["outcome_side"], "OVER")
        self.assertEqual(row["selection_result"], "HIT")
        self.assertTrue(row["eligible_shadow"])
        self.assertEqual(out["summary"]["hits"], 1)
        self.assertEqual(out["summary"]["hit_rate"], 1.0)
        self.assertEqual(out["source_snapshot_sha256"], snap["snapshot_sha256"])

    def test_under_miss(self):
        snap = snapshot(record(direction="UNDER"))
        out = shadow_grader.grade_snapshot(snap, [outcome(yards=250)], season=2026)
        self.assertEqual(out["records"][0]["selection_result"], "MISS")
        self.assertEqual(out["summary"]["misses"], 1)

    def test_quarantined_row_never_enters_hit_rate_denominator(self):
        snap = snapshot(
            record(gsis="00-1", decision="QUARANTINED"),
            record(gsis="00-2", direction="UNDER"),
        )
        rows = [
            outcome(gsis="00-1", yards=250),
            outcome(gsis="00-2", yards=150),
        ]
        out = shadow_grader.grade_snapshot(snap, rows, season=2026)
        self.assertFalse(out["records"][0]["eligible_shadow"])
        self.assertEqual(out["records"][0]["selection_result"], "NOT_ELIGIBLE")
        self.assertEqual(out["summary"]["eligible_settled"], 1)
        self.assertEqual(out["summary"]["hits"], 1)
        self.assertEqual(out["summary"]["hit_rate"], 1.0)

    def test_exact_opponent_binding_required(self):
        out = shadow_grader.grade_snapshot(
            snapshot(record()), [outcome(opponent="DAL")], season=2026
        )
        self.assertEqual(out["records"][0]["outcome_status"], "UNRESOLVED_OUTCOME")
        self.assertEqual(out["summary"]["eligible_settled"], 0)

    def test_duplicate_exact_outcomes_fail_closed(self):
        row = outcome()
        out = shadow_grader.grade_snapshot(
            snapshot(record()), [row, dict(row)], season=2026
        )
        self.assertEqual(out["records"][0]["outcome_status"], "AMBIGUOUS_OUTCOME")
        self.assertEqual(out["records"][0]["selection_result"], "UNSETTLED")

    def test_push_is_not_hit_or_miss(self):
        out = shadow_grader.grade_snapshot(
            snapshot(record(line=215.0)), [outcome(yards=215)], season=2026
        )
        self.assertEqual(out["records"][0]["selection_result"], "PUSH")
        self.assertEqual(out["summary"]["pushes"], 1)
        self.assertIsNone(out["summary"]["hit_rate"])

    def test_neutral_direction_is_not_eligible(self):
        out = shadow_grader.grade_snapshot(
            snapshot(record(direction="NEUTRAL")), [outcome(yards=250)], season=2026
        )
        self.assertFalse(out["records"][0]["eligible_shadow"])
        self.assertEqual(out["records"][0]["selection_result"], "NOT_ELIGIBLE")

    def test_tampered_snapshot_fails_closed(self):
        snap = snapshot(record())
        snap["records"][0]["line"] = 1.5
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            shadow_grader.grade_snapshot(snap, [outcome()], season=2026)

    def test_invalid_snapshot_digest_fails_closed(self):
        snap = snapshot(record())
        snap["snapshot_sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "64-character"):
            shadow_grader.grade_snapshot(snap, [outcome()], season=2026)

    def test_post_kickoff_seal_fails_closed_even_with_matching_hash(self):
        row = record()
        bad = shadow_snapshot.seal_snapshot(
            [row],
            slate_date="2026-09-14",
            code_sha="test-sha",
            source_vintage="test-source",
            sealed_at="2026-09-15T00:16:00Z",
        )
        with self.assertRaisesRegex(ValueError, "precede kickoff"):
            shadow_grader.grade_snapshot(bad, [outcome()], season=2026)

    def test_non_shadow_evidence_rejected(self):
        snap = snapshot(record())
        snap["evidence_class"] = "HISTORICAL"
        with self.assertRaisesRegex(ValueError, "PROSPECTIVE_SHADOW"):
            shadow_grader.grade_snapshot(snap, [], season=2026)


if __name__ == "__main__":
    unittest.main(verbosity=2)
