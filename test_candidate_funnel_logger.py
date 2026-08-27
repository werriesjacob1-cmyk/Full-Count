#!/usr/bin/env python3
"""test_candidate_funnel_logger.py -- coverage for
backtest/candidate_funnel_logger.py, the prospective full-candidate research
log built 2026-08-25. Enforces every safety-contract claim in that module's
own docstring: read-only over candidates, alt-line preservation, outcome
kept strictly separate from pregame features, deterministic dedup.

    /tmp/mlbvenv/bin/python3 test_candidate_funnel_logger.py
"""
import copy
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import candidate_funnel_logger as cfl


def candidate(**overrides):
    c = {
        "game_pk": 823260, "game_start": "2026-08-25T23:05:00+00:00",
        "player_id": 670541, "name": "Yordan Alvarez", "team": "HOU",
        "matchup": "HOU@SEA", "bet_side": "over",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.71, "raw_hit_probability": 0.68,
        "probability_basis": "empirical_blend", "prob_ci": [0.62, 0.79],
        "sample_n": 412, "base_rate": 0.63, "lift": 0.08,
        "market_odds": -130, "market_implied": 0.565, "market_edge": 0.021,
        "reliability": "A", "score": 74.2, "cat_matchup": 71.0,
        "signals": {"platoon": 80.0},
        "status": "top_pick", "status_reasons": [],
        "line_options": [
            {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.71,
             "base_rate": 0.63, "lift": 0.08, "basis": "empirical_blend", "ci": [0.62, 0.79]},
            {"stat": "total_bases", "needs": 1, "line": 1.5, "prob": 0.58,
             "base_rate": 0.50, "lift": 0.08, "basis": "modelled", "ci": [0.48, 0.68]},
            {"stat": "hits_runs_rbis", "needs": 1, "line": 0.5, "prob": 0.66,
             "base_rate": 0.60, "lift": 0.06, "basis": "empirical_blend", "ci": [0.57, 0.75]},
        ],
    }
    c.update(overrides)
    return c


class CandidateIdentityTests(unittest.TestCase):
    def test_stable_across_repeated_calls(self):
        c = candidate()
        self.assertEqual(cfl.candidate_identity(c, date="2026-08-25"),
                         cfl.candidate_identity(c, date="2026-08-25"))

    def test_differs_for_different_players(self):
        c1 = candidate(player_id=1)
        c2 = candidate(player_id=2)
        self.assertNotEqual(cfl.candidate_identity(c1, date="2026-08-25"),
                            cfl.candidate_identity(c2, date="2026-08-25"))

    def test_differs_for_different_stats(self):
        c1 = candidate(projection={"stat": "hits", "value": 0.5, "needs": 1})
        c2 = candidate(projection={"stat": "total_bases", "value": 1.5, "needs": 1})
        self.assertNotEqual(cfl.candidate_identity(c1, date="2026-08-25"),
                            cfl.candidate_identity(c2, date="2026-08-25"))

    def test_uses_combo_player_ids_when_present(self):
        c = candidate(combo_player_ids=[1, 2], player_id=None)
        cid = cfl.candidate_identity(c, date="2026-08-25")
        self.assertIn("[1,2]", cid)

    def test_line_movement_is_new_wager_but_same_market_series(self):
        a = candidate(
            projection={"stat": "hits", "value": 0.5, "needs": 1},
            bet_side="over")
        b = candidate(
            projection={"stat": "hits", "value": 1.5, "needs": 2},
            bet_side="over")
        self.assertNotEqual(
            cfl.candidate_identity(a, date="2026-08-25"),
            cfl.candidate_identity(b, date="2026-08-25"))
        self.assertEqual(
            cfl.candidate_series_identity(a, date="2026-08-25"),
            cfl.candidate_series_identity(b, date="2026-08-25"))

    def test_same_threshold_opposite_side_is_different_wager(self):
        over = candidate(bet_side="over")
        under = candidate(bet_side="under")
        self.assertNotEqual(
            cfl.candidate_identity(over, date="2026-08-25"),
            cfl.candidate_identity(under, date="2026-08-25"))

    def test_record_exposes_exact_and_series_identity(self):
        c = candidate()
        record = cfl.build_funnel_records(
            [c], date="2026-08-25")[0]
        self.assertEqual(
            record["identity"]["candidate_id"],
            cfl.candidate_identity(c, date="2026-08-25"))
        self.assertEqual(
            record["identity"]["candidate_series_id"],
            cfl.candidate_series_identity(c, date="2026-08-25"))


class BuildFunnelRecordsTests(unittest.TestCase):
    def test_never_mutates_input_candidates(self):
        # The core safety contract: this module is read-only over
        # candidates. Deep-copy before, deep-copy after, must be identical.
        candidates = [candidate(), candidate(player_id=999, name="Other Batter")]
        before = copy.deepcopy(candidates)
        cfl.build_funnel_records(candidates, date="2026-08-25")
        self.assertEqual(candidates, before)

    def test_multiple_alt_lines_for_the_same_batter_all_survive(self):
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        self.assertEqual(len(records), 1)
        alt_lines = records[0]["decision"]["alt_lines"]
        self.assertEqual(len(alt_lines), 3)
        stats = {a["stat"] for a in alt_lines}
        self.assertEqual(stats, {"hits", "total_bases", "hits_runs_rbis"})
        self.assertEqual(records[0]["decision"]["n_alt_lines"], 3)

    def test_outcome_is_never_a_field_on_a_fresh_record(self):
        # Pregame features and postgame outcome must stay structurally
        # separate -- grading is a later, distinct step.
        record = cfl.build_funnel_records([candidate()], date="2026-08-25")[0]
        self.assertNotIn("outcome", record)

    def test_provenance_captured_when_given(self):
        record = cfl.build_funnel_records(
            [candidate()], date="2026-08-25", code_git_sha="abc1234",
            generated_at="2026-08-25T12:00:00+00:00")[0]
        self.assertEqual(record["provenance"]["code_git_sha"], "abc1234")
        self.assertEqual(record["provenance"]["generated_at"], "2026-08-25T12:00:00+00:00")

    def test_gate_trace_and_quality_control_overlaid_by_identity(self):
        c = candidate()
        cid = cfl.candidate_identity(c, date="2026-08-25")
        gate_traces = {cid: {"gates": {"has_prob": True}, "blocking_gate": "has_odds"}}
        qc_index = {cid: ("assumed_lineup", "lineup not confirmed")}
        record = cfl.build_funnel_records(
            [c], date="2026-08-25", gate_traces=gate_traces,
            quality_control_index=qc_index)[0]
        self.assertEqual(record["decision"]["blocking_gate"], "has_odds")
        self.assertEqual(record["decision"]["quality_control_status"], "assumed_lineup")

    def test_identity_prediction_market_evidence_all_mapped(self):
        record = cfl.build_funnel_records([candidate()], date="2026-08-25")[0]
        self.assertEqual(record["identity"]["player_name"], "Yordan Alvarez")
        self.assertEqual(record["prediction"]["hit_probability"], 0.71)
        self.assertEqual(record["market"]["market_odds"], -130)
        self.assertEqual(record["market"]["feed_family"], "batter_props")
        self.assertEqual(record["evidence"]["reliability"], "A")

    def test_market_context_distinguishes_matched_failed_and_unmatched(self):
        ctx = {
            "book": "fanduel",
            "observed_at": "2026-08-25T17:00:00Z",
            "family_states": {
                "batter_props": "AVAILABLE",
                "pitcher_strikeouts": "FETCH_FAILED",
            },
        }
        matched = cfl.build_funnel_records(
            [candidate()], date="2026-08-25", market_context=ctx)[0]
        self.assertEqual(matched["market"]["market_fetch_state"], "MATCHED")
        self.assertEqual(matched["market"]["book"], "fanduel")
        self.assertEqual(
            matched["market"]["market_observed_at"], "2026-08-25T17:00:00Z")

        unmatched = cfl.build_funnel_records(
            [candidate(market_odds=None)], date="2026-08-25",
            market_context=ctx)[0]
        self.assertEqual(unmatched["market"]["market_fetch_state"], "NOT_MATCHED")

        k = candidate(
            market_odds=None,
            projection={"stat": "strikeouts", "value": 5.5, "needs": 6})
        failed = cfl.build_funnel_records(
            [k], date="2026-08-25", market_context=ctx)[0]
        self.assertEqual(failed["market"]["feed_family"], "pitcher_strikeouts")
        self.assertEqual(failed["market"]["market_fetch_state"], "FETCH_FAILED")

    def test_market_fair_semantics_and_model_versions_are_preserved(self):
        c = candidate(
            posted_implied=0.60, market_fair=0.56,
            market_fair_method="assumed_hold", edge_vs_fair=0.15,
            signal_weight_adjustment=2.5)
        meta = {
            "model_version": "m1", "selection_policy_version": "s1",
            "calibration_version": "c1", "feature_version": "f1",
            "prediction_timestamp": "2026-08-25T17:00:01Z",
            "odds_fetched_at": "2026-08-25T17:00:00Z",
            "board_generated_at": "2026-08-25T17:00:02Z",
        }
        record = cfl.build_funnel_records(
            [c], date="2026-08-25", run_metadata=meta)[0]
        self.assertEqual(record["market"]["market_fair_method"], "assumed_hold")
        self.assertEqual(record["market"]["edge_vs_fair"], 0.15)
        self.assertEqual(record["evidence"]["signal_weight_adjustment"], 2.5)
        self.assertEqual(record["provenance"]["model_version"], "m1")
        self.assertEqual(record["provenance"]["selection_policy_version"], "s1")

    def test_gate_trace_status_fills_decision_when_candidate_has_no_status(self):
        c = candidate(status=None, status_reasons=None)
        cid = cfl.candidate_identity(c, date="2026-08-25")
        trace = {
            cid: {
                "status": "value",
                "status_reasons": ["real price edge"],
                "gates": {},
                "blocking_gate": "meets_prob_floor",
            }
        }
        record = cfl.build_funnel_records(
            [c], date="2026-08-25", gate_traces=trace)[0]
        self.assertEqual(record["decision"]["recommendation_status"], "value")
        self.assertEqual(record["decision"]["status_reasons"], ["real price edge"])

    def test_qc_rejected_trace_is_counterfactual_not_champion_status(self):
        c = candidate(status=None, status_reasons=None)
        cid = cfl.candidate_identity(c, date="2026-08-25")
        trace = {
            cid: {
                "status": "top_pick",
                "status_reasons": [],
                "gates": {"has_prob": True},
                "blocking_gate": None,
            }
        }
        record = cfl.build_funnel_records(
            [c], date="2026-08-25", gate_traces=trace,
            quality_control_index={cid: ("rejected", "rain")})[0]
        self.assertIsNone(record["decision"]["recommendation_status"])
        self.assertEqual(
            record["decision"]["recommendation_stage"],
            "not_reached_qc_reject")
        self.assertEqual(
            record["decision"]["counterfactual_recommendation_status"],
            "top_pick")
        self.assertEqual(
            record["decision"]["gate_trace_scope"],
            "counterfactual_after_qc_rejection")


class ProspectivePreparationBoundaryTests(unittest.TestCase):
    def test_mutating_live_helpers_only_touch_research_copy(self):
        source = [candidate()]
        before = copy.deepcopy(source)
        calls = {"signal": 0, "attach": 0}

        def quality_control(candidates, game_meta, park_wx, emp_pitchers):
            candidates[0]["qc_marker"] = "mutated-copy"
            return candidates, [], []

        def apply_signal_weights(candidates, trust=None):
            calls["signal"] += 1
            candidates[0]["score"] = 88.0
            candidates[0]["signal_weight_adjustment"] = 3.0

        gp = types.SimpleNamespace(
            quality_control=quality_control,
            load_signal_trust=lambda: {"platoon": 0.1},
            apply_signal_weights=apply_signal_weights,
        )

        def attach_market_prices(candidates, **feeds):
            calls["attach"] += 1
            candidates[0]["market_odds"] = -125
            candidates[0]["market_fair"] = 0.54
            return candidates, 1

        fd = types.SimpleNamespace(
            fetch_prop_prices=lambda: {"Yordan Alvarez": {("hits", 1): -125}},
            fetch_first_inning_totals=lambda: {},
            attach_market_prices=attach_market_prices,
        )
        ctx = {
            "game_meta": [],
            "park_wx": {},
            "emp_pitchers": {},
            "k_prices": {"k": 1},
            "po_prices": {"po": 1},
            "combined_k_prices": {"combo": 1},
        }

        research, qc_index, market_context, feeds = cfl.prepare_research_candidates(
            source, ctx, gp=gp, fd=fd, date="2026-08-25",
            observed_at="2026-08-25T17:00:00Z")

        self.assertEqual(source, before)
        self.assertIsNot(research[0], source[0])
        self.assertEqual(research[0]["qc_marker"], "mutated-copy")
        self.assertEqual(research[0]["score"], 88.0)
        self.assertEqual(research[0]["market_odds"], -125)
        self.assertEqual(calls, {"signal": 1, "attach": 1})
        cid = cfl.candidate_identity(research[0], date="2026-08-25")
        self.assertEqual(qc_index[cid], ("confirmed_lineup", None))
        self.assertEqual(market_context["book"], "fanduel")
        self.assertEqual(
            market_context["family_states"]["batter_props"], "AVAILABLE")
        self.assertIn("prices", feeds)

    def test_market_fetch_failures_are_explicit_and_empty_is_not_called_not_posted(self):
        class FD:
            @staticmethod
            def fetch_prop_prices():
                raise RuntimeError("book unavailable")

            @staticmethod
            def fetch_first_inning_totals():
                return {}

        feeds, ctx = cfl.fetch_live_market_snapshot(
            {"k_prices": {}, "po_prices": {}, "combined_k_prices": {}},
            fd=FD, observed_at="2026-08-25T17:00:00Z")
        self.assertEqual(feeds["prices"], {})
        self.assertEqual(
            ctx["family_states"]["batter_props"], "FETCH_FAILED")
        self.assertEqual(
            ctx["family_states"]["first_inning"], "UNKNOWN_EMPTY")
        self.assertEqual(
            ctx["family_states"]["pitcher_strikeouts"], "UNKNOWN_EMPTY")

    def test_qc_labels_survive_after_pricing_mutations(self):
        c1 = candidate(player_id=1, name="Kept")
        c2 = candidate(player_id=2, name="Rejected")
        c3 = candidate(player_id=3, name="Assumed")

        def quality_control(candidates, *_args):
            candidates[1]["qc_reason"] = "rain"
            candidates[2]["lineup_assumed"] = True
            return [candidates[0]], [candidates[1]], [candidates[2]]

        gp = types.SimpleNamespace(
            quality_control=quality_control,
            load_signal_trust=lambda: {},
            apply_signal_weights=lambda candidates, trust=None: candidates,
        )
        fd = types.SimpleNamespace(
            fetch_prop_prices=lambda: {},
            fetch_first_inning_totals=lambda: {},
            attach_market_prices=lambda candidates, **kwargs: (candidates, 0),
        )
        ctx = {
            "game_meta": [], "park_wx": {}, "emp_pitchers": {},
            "k_prices": {}, "po_prices": {}, "combined_k_prices": {},
        }
        research, qc, _, _ = cfl.prepare_research_candidates(
            [c1, c2, c3], ctx, gp=gp, fd=fd, date="2026-08-25")

        ids = {
            r["name"]: cfl.candidate_identity(r, date="2026-08-25")
            for r in research
        }
        self.assertEqual(qc[ids["Kept"]], ("confirmed_lineup", None))
        self.assertEqual(qc[ids["Rejected"]], ("rejected", "rain"))
        self.assertEqual(
            qc[ids["Assumed"]], ("assumed_lineup", "lineup not confirmed"))


class OperationalOpportunityExpansionTests(unittest.TestCase):
    def _gp(self, expanded):
        def select_best_by_category(pool, prices, fd, n_per_category=1,
                                    k_prices=None, min_score=None):
            # Tests own the exact compact rows returned by the production
            # expansion seam; function still receives the real pool/feeds.
            return expanded(pool)
        return types.SimpleNamespace(
            select_best_by_category=select_best_by_category)

    def test_expands_multiple_market_families_for_one_batter(self):
        raw = candidate(
            player_id=10, name="Batter", game_pk=99,
            game_start="2026-08-25T23:00:00Z",
            bet_side="over", signal_weight_adjustment=2.0)
        raw["line_options"] = [
            {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.70},
            {"stat": "total_bases", "needs": 2, "line": 1.5, "prob": 0.55},
        ]
        raw_qc = {
            cfl.candidate_identity(raw, date="2026-08-25"):
                ("confirmed_lineup", None)
        }

        def expanded(pool):
            self.assertEqual(len(pool), 1)
            return {
                "hits": [{
                    "type": "batter", "name": "Batter", "player_id": 10,
                    "game_pk": 99, "projection": {
                        "stat": "hits", "value": 0.5, "needs": 1},
                    "hit_probability": 0.70, "score": 80.0,
                    "market_odds": -150,
                }],
                "total_bases": [{
                    "type": "batter", "name": "Batter", "player_id": 10,
                    "game_pk": 99, "projection": {
                        "stat": "total_bases", "value": 1.5, "needs": 2},
                    "hit_probability": 0.55, "score": 80.0,
                    "market_odds": 110,
                }],
            }

        rows, qc, diag = cfl.build_operational_opportunities(
            [raw], raw_qc,
            {"prices": {}, "k_prices": {}},
            gp=self._gp(expanded), fd=types.SimpleNamespace(),
            date="2026-08-25")

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {(r["projection"]["stat"], r["projection"]["needs"]) for r in rows},
            {("hits", 1), ("total_bases", 2)})
        self.assertEqual(diag["raw_candidates"], 1)
        self.assertEqual(diag["expanded_opportunities"], 2)
        self.assertEqual(diag["operational_opportunities"], 2)
        self.assertEqual(len(qc), 2)

    def test_expanded_rows_restore_settlement_and_ranking_provenance(self):
        raw = candidate(
            player_id=10, name="Batter", game_pk=99,
            game_start="2026-08-25T23:00:00Z",
            bet_side="over", cat_matchup=10.0,
            signal_weight_adjustment=3.5,
            reliability_note="real sample")
        raw_qc = {
            cfl.candidate_identity(raw, date="2026-08-25"):
                ("confirmed_lineup", None)
        }

        def expanded(pool):
            return {"hits": [{
                "type": "batter", "name": "Batter", "player_id": 10,
                "game_pk": 99,
                "projection": {"stat": "hits", "value": 0.5, "needs": 1},
                "hit_probability": 0.70, "score": 80.0,
            }]}

        rows, _, _ = cfl.build_operational_opportunities(
            [raw], raw_qc, {"prices": {}, "k_prices": {}},
            gp=self._gp(expanded), fd=types.SimpleNamespace(),
            date="2026-08-25")
        row = rows[0]
        self.assertEqual(row["game_start"], raw["game_start"])
        self.assertEqual(row["bet_side"], "over")
        self.assertEqual(row["cat_matchup"], 10.0)
        self.assertEqual(row["signal_weight_adjustment"], 3.5)
        self.assertEqual(row["reliability_note"], "real sample")

        rec = cfl.build_funnel_records(
            rows, date="2026-08-25",
            quality_control_index={
                cfl.candidate_identity(row, date="2026-08-25"):
                    ("confirmed_lineup", None)
            })[0]
        self.assertEqual(rec["identity"]["side"], "over")
        self.assertEqual(rec["identity"]["game_start"], raw["game_start"])
        self.assertEqual(rec["evidence"]["signal_weight_adjustment"], 3.5)

    def test_rejected_expansion_is_kept_counterfactual_and_separate(self):
        kept = candidate(player_id=1, name="Kept", game_pk=99)
        rejected = candidate(player_id=2, name="Rejected", game_pk=100)
        raw_qc = {
            cfl.candidate_identity(kept, date="2026-08-25"):
                ("confirmed_lineup", None),
            cfl.candidate_identity(rejected, date="2026-08-25"):
                ("rejected", "rain"),
        }

        def expanded(pool):
            out = {}
            for src in pool:
                out.setdefault("hits", []).append({
                    "type": "batter", "name": src["name"],
                    "player_id": src["player_id"], "game_pk": src["game_pk"],
                    "projection": {"stat": "hits", "value": 0.5, "needs": 1},
                    "hit_probability": 0.65, "score": 70.0,
                })
            return out

        rows, qc, diag = cfl.build_operational_opportunities(
            [kept, rejected], raw_qc, {"prices": {}, "k_prices": {}},
            gp=self._gp(expanded), fd=types.SimpleNamespace(),
            date="2026-08-25")
        self.assertEqual(diag["operational_opportunities"], 1)
        self.assertEqual(diag["rejected_counterfactual_opportunities"], 1)
        by_name = {
            r["name"]: qc[cfl.candidate_identity(r, date="2026-08-25")]
            for r in rows
        }
        self.assertEqual(by_name["Kept"], ("confirmed_lineup", None))
        self.assertEqual(by_name["Rejected"], ("rejected", "rain"))

    def test_duplicate_expanded_candidate_identity_fails_closed(self):
        raw = candidate(player_id=10, game_pk=99)
        raw_qc = {
            cfl.candidate_identity(raw, date="2026-08-25"):
                ("confirmed_lineup", None)
        }

        def expanded(pool):
            row = {
                "type": "batter", "name": "X", "player_id": 10,
                "game_pk": 99,
                "projection": {"stat": "hits", "value": 0.5, "needs": 1},
                "hit_probability": 0.65, "score": 70.0,
            }
            return {"hits": [dict(row), dict(row)]}

        with self.assertRaises(ValueError):
            cfl.build_operational_opportunities(
                [raw], raw_qc, {"prices": {}, "k_prices": {}},
                gp=self._gp(expanded), fd=types.SimpleNamespace(),
                date="2026-08-25")


class ContentHashTests(unittest.TestCase):
    def test_identical_records_hash_identically(self):
        r1 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T12:00:00Z")[0]
        r2 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T12:00:00Z")[0]
        self.assertEqual(cfl.content_hash(r1), cfl.content_hash(r2))

    def test_generated_at_alone_does_not_change_the_hash(self):
        r1 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T12:00:00Z")[0]
        r2 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                      generated_at="2026-08-25T18:30:00Z")[0]
        self.assertEqual(cfl.content_hash(r1), cfl.content_hash(r2))

    def test_a_real_probability_change_does_change_the_hash(self):
        r1 = cfl.build_funnel_records([candidate(hit_probability=0.71)], date="2026-08-25")[0]
        r2 = cfl.build_funnel_records([candidate(hit_probability=0.66)], date="2026-08-25")[0]
        self.assertNotEqual(cfl.content_hash(r1), cfl.content_hash(r2))

    def test_observation_timestamps_do_not_create_fake_candidate_changes(self):
        ctx1 = {"observed_at": "2026-08-25T12:00:00Z"}
        ctx2 = {"observed_at": "2026-08-25T13:00:00Z"}
        meta1 = {
            "prediction_timestamp": "2026-08-25T12:00:01Z",
            "odds_fetched_at": "2026-08-25T12:00:00Z",
            "board_generated_at": "2026-08-25T12:00:02Z",
        }
        meta2 = {
            "prediction_timestamp": "2026-08-25T13:00:01Z",
            "odds_fetched_at": "2026-08-25T13:00:00Z",
            "board_generated_at": "2026-08-25T13:00:02Z",
        }
        r1 = cfl.build_funnel_records(
            [candidate()], date="2026-08-25",
            market_context=ctx1, run_metadata=meta1)[0]
        r2 = cfl.build_funnel_records(
            [candidate()], date="2026-08-25",
            market_context=ctx2, run_metadata=meta2)[0]
        self.assertEqual(cfl.content_hash(r1), cfl.content_hash(r2))


class AppendNewSnapshotsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "candidate_funnel_2026-08-25.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_write_writes_everything(self):
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        n_written, n_skipped = cfl.append_new_snapshots(records, self.path)
        self.assertEqual(n_written, 1)
        self.assertEqual(n_skipped, 0)
        with open(self.path) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_identical_rerun_writes_nothing_new(self):
        # Deterministic dedup: unchanged candidate -> second run is a no-op.
        records1 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                            generated_at="2026-08-25T12:00:00Z")
        cfl.append_new_snapshots(records1, self.path)
        records2 = cfl.build_funnel_records([candidate()], date="2026-08-25",
                                            generated_at="2026-08-25T13:00:00Z")
        n_written, n_skipped = cfl.append_new_snapshots(records2, self.path)
        self.assertEqual(n_written, 0)
        self.assertEqual(n_skipped, 1)
        with open(self.path) as f:
            self.assertEqual(len(f.readlines()), 1)  # still just the one row

    def test_a_real_change_appends_a_new_row_not_a_rewrite(self):
        records1 = cfl.build_funnel_records([candidate(hit_probability=0.71)], date="2026-08-25")
        cfl.append_new_snapshots(records1, self.path)
        records2 = cfl.build_funnel_records([candidate(hit_probability=0.74)], date="2026-08-25")
        n_written, n_skipped = cfl.append_new_snapshots(records2, self.path)
        self.assertEqual(n_written, 1)
        with open(self.path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)  # append-only changelog, not overwritten

    def test_two_different_candidates_both_survive_independently(self):
        c1, c2 = candidate(player_id=1), candidate(player_id=2, name="Other")
        records = cfl.build_funnel_records([c1, c2], date="2026-08-25")
        n_written, _ = cfl.append_new_snapshots(records, self.path)
        self.assertEqual(n_written, 2)
        # Changing only c1 must not re-write c2's already-logged, unchanged row.
        records2 = cfl.build_funnel_records(
            [candidate(player_id=1, hit_probability=0.80), c2], date="2026-08-25")
        n_written2, n_skipped2 = cfl.append_new_snapshots(records2, self.path)
        self.assertEqual(n_written2, 1)
        self.assertEqual(n_skipped2, 1)

    def test_missing_file_is_a_clean_first_run_not_an_error(self):
        self.assertFalse(os.path.exists(self.path))
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        n_written, n_skipped = cfl.append_new_snapshots(records, self.path)
        self.assertEqual(n_written, 1)


class SnapshotManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(
            self.tmp.name, "candidate_funnel_snapshots_2026-08-25.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_candidate_can_be_observed_twice_without_duplicate_candidate_rows(self):
        records = cfl.build_funnel_records(
            [candidate()], date="2026-08-25",
            generated_at="2026-08-25T12:00:00Z")
        candidate_path = os.path.join(
            self.tmp.name, "candidate_funnel_2026-08-25.jsonl")
        cfl.append_new_snapshots(records, candidate_path)

        records_later = cfl.build_funnel_records(
            [candidate()], date="2026-08-25",
            generated_at="2026-08-25T13:00:00Z")
        written, skipped = cfl.append_new_snapshots(records_later, candidate_path)
        self.assertEqual((written, skipped), (0, 1))

        m1 = cfl.build_snapshot_manifest(
            records, date="2026-08-25",
            observed_at="2026-08-25T12:00:00Z", code_git_sha="abc")
        m2 = cfl.build_snapshot_manifest(
            records_later, date="2026-08-25",
            observed_at="2026-08-25T13:00:00Z", code_git_sha="abc")
        self.assertNotEqual(m1["snapshot_id"], m2["snapshot_id"])
        self.assertEqual(
            m1["candidate_universe_fingerprint"],
            m2["candidate_universe_fingerprint"])
        self.assertEqual(cfl.append_snapshot_manifest(m1, self.path), 1)
        self.assertEqual(cfl.append_snapshot_manifest(m2, self.path), 1)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(len([l for l in fh if l.strip()]), 2)

    def test_snapshot_manifest_binds_every_candidate_content_hash(self):
        records = cfl.build_funnel_records(
            [candidate(player_id=1), candidate(player_id=2, name="Other")],
            date="2026-08-25")
        manifest = cfl.build_snapshot_manifest(
            records, date="2026-08-25",
            observed_at="2026-08-25T12:00:00Z", code_git_sha="abc")
        self.assertEqual(manifest["n_candidates"], 2)
        self.assertEqual(len(manifest["candidate_hashes"]), 2)
        expected = {
            r["identity"]["candidate_id"]: cfl.content_hash(r) for r in records
        }
        self.assertEqual(
            {x["candidate_id"]: x["content_hash"]
             for x in manifest["candidate_hashes"]},
            expected)

    def test_duplicate_candidate_ids_fail_closed(self):
        records = cfl.build_funnel_records(
            [candidate(), candidate()], date="2026-08-25")
        with self.assertRaises(ValueError):
            cfl.build_snapshot_manifest(
                records, date="2026-08-25",
                observed_at="2026-08-25T12:00:00Z")

    def test_same_snapshot_id_is_idempotent(self):
        records = cfl.build_funnel_records([candidate()], date="2026-08-25")
        manifest = cfl.build_snapshot_manifest(
            records, date="2026-08-25",
            observed_at="2026-08-25T12:00:00Z")
        self.assertEqual(cfl.append_snapshot_manifest(manifest, self.path), 1)
        self.assertEqual(cfl.append_snapshot_manifest(manifest, self.path), 0)


class DefaultPathTests(unittest.TestCase):
    def test_path_is_per_date_and_matches_the_gitignored_backtest_glob(self):
        path = cfl.default_path_for_date("2026-08-25", out_dir="/tmp/x")
        self.assertTrue(path.endswith("candidate_funnel_2026-08-25.jsonl"))
        snapshot_path = cfl.default_snapshot_path_for_date(
            "2026-08-25", out_dir="/tmp/x")
        self.assertTrue(
            snapshot_path.endswith("candidate_funnel_snapshots_2026-08-25.jsonl"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
