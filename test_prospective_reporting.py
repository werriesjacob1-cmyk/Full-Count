#!/usr/bin/env python3
"""test_prospective_reporting.py -- coverage for
backtest/prospective_reporting.py, Priority 7 of the
restart-safety-mission directive (2026-08-25). Synthetic fixtures only --
this session's own real live-logged funnel data was lost to the same
container restarts that wiped the canonical backfill, so there is no real
data to test against yet.

    /tmp/mlbvenv/bin/python3 test_prospective_reporting.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import prospective_reporting as pr


def record(candidate_id="2026-08-25:1:100:hits:1", qc_status="confirmed_lineup",
           hit_probability=0.65, n_alt_lines=0, alt_lines=None, blocking_gate=None,
           gates=None, qc_reason=None, recommendation_status=None,
           market_odds=-120, market_fetch_state="MATCHED", edge_vs_fair=0.05,
           score=70.0, game_pk=1, player_id=100, stat="hits",
           game_start="2026-08-25T23:00:00Z"):
    return {
        "identity": {
            "candidate_id": candidate_id, "game_pk": game_pk,
            "player_id": player_id, "stat": stat, "game_start": game_start,
        },
        "prediction": {"hit_probability": hit_probability},
        "market": {
            "market_odds": market_odds,
            "market_fetch_state": market_fetch_state,
            "edge_vs_fair": edge_vs_fair,
        },
        "evidence": {"score": score},
        "decision": {
            "quality_control_status": qc_status, "quality_control_reason": qc_reason,
            "n_alt_lines": n_alt_lines, "alt_lines": alt_lines or [],
            "blocking_gate": blocking_gate, "gates": gates or {},
            "recommendation_status": recommendation_status,
        },
    }


def outcome(candidate_id, grade="hit"):
    return {"candidate_id": candidate_id, "grade": grade}


class SnapshotResolutionTests(unittest.TestCase):
    def test_resolves_exact_candidate_states_from_manifest_hashes(self):
        records = [
            record(candidate_id="a", hit_probability=0.61),
            record(candidate_id="b", hit_probability=0.72),
        ]
        manifest = pr.cfl.build_snapshot_manifest(
            records, date="2026-08-25",
            observed_at="2026-08-25T17:00:00Z", code_git_sha="abc")
        resolved = pr.resolve_snapshot(records, manifest)
        self.assertEqual(
            {r["identity"]["candidate_id"] for r in resolved}, {"a", "b"})

    def test_missing_candidate_state_fails_closed(self):
        records = [record(candidate_id="a")]
        manifest = pr.cfl.build_snapshot_manifest(
            records, date="2026-08-25",
            observed_at="2026-08-25T17:00:00Z")
        with self.assertRaises(pr.ProspectiveIntegrityError):
            pr.resolve_snapshot([], manifest)

    def test_tampered_universe_fingerprint_fails_closed(self):
        records = [record(candidate_id="a")]
        manifest = pr.cfl.build_snapshot_manifest(
            records, date="2026-08-25",
            observed_at="2026-08-25T17:00:00Z")
        manifest["candidate_universe_fingerprint"] = "0" * 64
        with self.assertRaises(pr.ProspectiveIntegrityError):
            pr.resolve_snapshot(records, manifest)


class OperationalEligibilityTests(unittest.TestCase):
    def test_requires_confirmed_qc_price_probability_and_pregame_clock(self):
        observed = "2026-08-25T17:00:00Z"
        good = record(candidate_id="good")
        bad_qc = record(candidate_id="bad-qc", qc_status="assumed_lineup")
        bad_price = record(
            candidate_id="bad-price", market_odds=None,
            market_fetch_state="NOT_MATCHED")
        bad_prob = record(candidate_id="bad-prob", hit_probability=None)
        started = record(
            candidate_id="started", game_start="2026-08-25T16:00:00Z")
        eligible = pr.operationally_eligible(
            [good, bad_qc, bad_price, bad_prob, started],
            observed_at=observed)
        self.assertEqual(
            [r["identity"]["candidate_id"] for r in eligible], ["good"])


class EqualVolumeSelectorComparisonTests(unittest.TestCase):
    def test_challenger_is_forced_to_exact_champion_volume(self):
        records = [
            record(candidate_id="a", recommendation_status="top_pick",
                   edge_vs_fair=0.02, hit_probability=0.61),
            record(candidate_id="b", recommendation_status="top_pick",
                   edge_vs_fair=0.03, hit_probability=0.62),
            record(candidate_id="c", edge_vs_fair=0.20, hit_probability=0.70),
            record(candidate_id="d", edge_vs_fair=0.10, hit_probability=0.69),
        ]
        outcomes = [
            outcome("a", "miss"), outcome("b", "hit"),
            outcome("c", "hit"), outcome("d", "hit"),
        ]
        report = pr.equal_volume_selector_comparison(
            records, outcomes, challenger_ranking="edge_vs_fair",
            observed_at="2026-08-25T17:00:00Z")
        self.assertEqual(report["selection_volume"], 2)
        self.assertEqual(
            report["challenger_candidate_ids"], ["c", "d"])
        self.assertEqual(report["champion"]["selected"], 2)
        self.assertEqual(report["challenger"]["selected"], 2)
        self.assertEqual(report["champion"]["hit_rate"], 0.5)
        self.assertEqual(report["challenger"]["hit_rate"], 1.0)
        self.assertEqual(report["realized_hit_rate_delta"], 0.5)
        self.assertEqual(report["overlap_count"], 0)

    def test_overlap_added_removed_are_explicit(self):
        records = [
            record(candidate_id="a", recommendation_status="top_pick",
                   edge_vs_fair=0.20),
            record(candidate_id="b", recommendation_status="top_pick",
                   edge_vs_fair=0.01),
            record(candidate_id="c", edge_vs_fair=0.10),
        ]
        outcomes = [
            outcome("a", "hit"), outcome("b", "miss"), outcome("c", "hit"),
        ]
        report = pr.equal_volume_selector_comparison(
            records, outcomes, challenger_ranking="edge_vs_fair")
        self.assertEqual(report["overlap_candidate_ids"], ["a"])
        self.assertEqual(report["added_candidate_ids"], ["c"])
        self.assertEqual(report["removed_candidate_ids"], ["b"])
        self.assertEqual(report["added"]["hit_rate"], 1.0)
        self.assertEqual(report["removed"]["hit_rate"], 0.0)

    def test_unsettled_selection_blocks_hit_rate_delta(self):
        records = [
            record(candidate_id="a", recommendation_status="top_pick",
                   edge_vs_fair=0.01),
            record(candidate_id="b", edge_vs_fair=0.20),
        ]
        report = pr.equal_volume_selector_comparison(
            records, [outcome("a", "hit")],
            challenger_ranking="edge_vs_fair")
        self.assertEqual(report["comparison_status"], "INCOMPLETE_SETTLEMENT")
        self.assertIsNone(report["realized_hit_rate_delta"])

    def test_champion_outside_operational_population_is_integrity_error(self):
        records = [
            record(
                candidate_id="a", recommendation_status="top_pick",
                market_odds=None, market_fetch_state="NOT_MATCHED"),
            record(candidate_id="b", edge_vs_fair=0.20),
        ]
        with self.assertRaises(pr.ProspectiveIntegrityError):
            pr.equal_volume_selector_comparison(
                records, [outcome("a"), outcome("b")],
                challenger_ranking="edge_vs_fair")

    def test_challenger_cannot_win_by_using_lower_volume(self):
        records = [
            record(candidate_id="a", recommendation_status="top_pick",
                   edge_vs_fair=0.02),
            record(candidate_id="b", recommendation_status="top_pick",
                   edge_vs_fair=0.03),
            record(candidate_id="c", edge_vs_fair=None),
        ]
        with self.assertRaises(pr.ProspectiveIntegrityError):
            pr.equal_volume_selector_comparison(
                records, [outcome("a"), outcome("b"), outcome("c")],
                challenger_ranking="edge_vs_fair")

    def test_zero_champion_volume_is_reported_not_padded(self):
        report = pr.equal_volume_selector_comparison(
            [record(candidate_id="a", edge_vs_fair=0.4)],
            [outcome("a")], challenger_ranking="edge_vs_fair")
        self.assertEqual(report["comparison_status"], "NO_CHAMPION_VOLUME")
        self.assertEqual(report["selection_volume"], 0)

    def test_market_mix_and_clustering_are_visible(self):
        records = [
            record(candidate_id="a", recommendation_status="top_pick",
                   game_pk=1, player_id=10, stat="hits", edge_vs_fair=0.10),
            record(candidate_id="b", recommendation_status="top_pick",
                   game_pk=1, player_id=11, stat="total_bases", edge_vs_fair=0.09),
            record(candidate_id="c", game_pk=2, player_id=12, stat="hits",
                   edge_vs_fair=0.20),
        ]
        outcomes = [outcome("a"), outcome("b"), outcome("c")]
        report = pr.equal_volume_selector_comparison(
            records, outcomes, challenger_ranking="edge_vs_fair")
        self.assertEqual(
            report["champion"]["market_mix"], {"hits": 1, "total_bases": 1})
        self.assertEqual(report["champion"]["unique_games"], 1)
        self.assertEqual(report["champion"]["unique_player_entities"], 2)


class SlateSummaryTests(unittest.TestCase):
    def test_counts_by_qc_status(self):
        records = [record(qc_status="confirmed_lineup"), record(qc_status="rejected"),
                   record(qc_status="rejected")]
        summary = pr.slate_summary(records)
        self.assertEqual(summary["n_total_candidates"], 3)
        self.assertEqual(summary["by_quality_control_status"]["rejected"], 2)

    def test_counts_multi_alt_line_candidates(self):
        records = [record(n_alt_lines=3), record(n_alt_lines=1), record(n_alt_lines=0)]
        summary = pr.slate_summary(records)
        self.assertEqual(summary["n_with_multiple_alt_lines"], 1)


class JoinOutcomesTests(unittest.TestCase):
    def test_joins_by_candidate_id(self):
        records = [record(candidate_id="a"), record(candidate_id="b")]
        outcomes = [outcome("a", "hit")]
        joined = pr.join_outcomes(records, outcomes)
        self.assertEqual(joined["a"][1]["grade"], "hit")
        self.assertIsNone(joined["b"][1])  # no outcome yet, not a crash


class HighestProbabilityRejectedTests(unittest.TestCase):
    def test_only_rejected_included_sorted_descending(self):
        records = [
            record(candidate_id="a", qc_status="rejected", hit_probability=0.55),
            record(candidate_id="b", qc_status="rejected", hit_probability=0.75),
            record(candidate_id="c", qc_status="confirmed_lineup", hit_probability=0.90),
        ]
        result = pr.highest_probability_rejected(records, [], n=10)
        self.assertEqual([r["candidate_id"] for r in result], ["b", "a"])  # c excluded

    def test_n_limits_result_size(self):
        records = [record(candidate_id=str(i), qc_status="rejected", hit_probability=0.5 + i * 0.01)
                   for i in range(20)]
        result = pr.highest_probability_rejected(records, [], n=5)
        self.assertEqual(len(result), 5)

    def test_includes_outcome_when_graded(self):
        records = [record(candidate_id="a", qc_status="rejected", hit_probability=0.70)]
        result = pr.highest_probability_rejected(records, [outcome("a", "miss")])
        self.assertEqual(result[0]["grade"], "miss")


class AlternateLineWinnerComparisonTests(unittest.TestCase):
    def test_board_line_was_the_best_option(self):
        records = [record(hit_probability=0.70,
                           alt_lines=[{"prob": 0.70}, {"prob": 0.55}])]
        result = pr.alternate_line_winner_comparison(records)
        self.assertTrue(result[0]["board_was_highest_prob_option"])

    def test_board_line_was_not_the_best_option(self):
        records = [record(hit_probability=0.55,
                           alt_lines=[{"prob": 0.70}, {"prob": 0.55}])]
        result = pr.alternate_line_winner_comparison(records)
        self.assertFalse(result[0]["board_was_highest_prob_option"])

    def test_single_line_candidates_excluded(self):
        records = [record(alt_lines=[{"prob": 0.60}])]  # only 1 alt line
        result = pr.alternate_line_winner_comparison(records)
        self.assertEqual(result, [])


class GateFailureCountsTests(unittest.TestCase):
    def test_tally_by_blocking_gate(self):
        records = [record(blocking_gate="evidence_ok"), record(blocking_gate="evidence_ok"),
                   record(blocking_gate="lineup_ok"), record(blocking_gate=None)]
        counts = pr.gate_failure_counts(records)
        self.assertEqual(counts["evidence_ok"], 2)
        self.assertEqual(counts["lineup_ok"], 1)
        self.assertEqual(counts[None], 1)


class GateRegretTests(unittest.TestCase):
    def test_only_single_gate_failures_counted(self):
        records = [
            # blocked ONLY by evidence_ok (every other gate True)
            record(candidate_id="a", gates={"has_prob": True, "meets_prob_floor": True,
                                             "evidence_ok": False, "lineup_ok": True}),
            # blocked by TWO gates -- must be excluded from regret attribution
            record(candidate_id="b", gates={"has_prob": True, "meets_prob_floor": False,
                                             "evidence_ok": False, "lineup_ok": True}),
        ]
        outcomes = [outcome("a", "hit"), outcome("b", "miss")]
        result = pr.gate_regret(records, outcomes)
        self.assertEqual(result["evidence_ok"]["n_blocked_solely_by_this_gate"], 1)
        self.assertNotIn("meets_prob_floor", result)  # b excluded, not attributed anywhere

    def test_hit_rate_computed_only_over_graded(self):
        records = [
            record(candidate_id="a", gates={"has_prob": True, "evidence_ok": False}),
            record(candidate_id="b", gates={"has_prob": True, "evidence_ok": False}),
        ]
        outcomes = [outcome("a", "hit")]  # b never graded
        result = pr.gate_regret(records, outcomes)
        self.assertEqual(result["evidence_ok"]["n_blocked_solely_by_this_gate"], 2)
        self.assertEqual(result["evidence_ok"]["n_graded"], 1)
        self.assertEqual(result["evidence_ok"]["hit_rate"], 1.0)

    def test_no_gates_dict_excluded_not_a_crash(self):
        records = [record(candidate_id="a", gates={})]
        result = pr.gate_regret(records, [])
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
