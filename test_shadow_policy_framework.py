#!/usr/bin/env python3
"""test_shadow_policy_framework.py -- coverage for
backtest/shadow_policy_framework.py, Priorities 2/3 of the
restart-safety-mission directive (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_shadow_policy_framework.py
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import shadow_policy_framework as spf


def candidate(**overrides):
    c = {
        "date": "2026-08-25", "game_pk": 1, "player_id": 100, "team": "A",
        "matchup": "A @ B", "type": "batter", "projection": {"stat": "hits", "needs": 1},
        "hit_probability": 0.65, "reliability": "A", "prob_ci": [0.58, 0.72],
        "market_odds": -120, "market_side": "over", "status_reasons": [],
        "lineup_assumed": False,
    }
    c.update(overrides)
    return c


class CandidateIdTests(unittest.TestCase):
    def test_stable_and_deterministic(self):
        c = candidate()
        self.assertEqual(spf._candidate_id(c), spf._candidate_id(candidate()))

    def test_differs_by_player(self):
        self.assertNotEqual(spf._candidate_id(candidate(player_id=100)),
                             spf._candidate_id(candidate(player_id=200)))


class NonMutationTests(unittest.TestCase):
    def test_every_policy_never_mutates_input_candidates(self):
        candidates = [candidate(player_id=i, hit_probability=0.6 + i * 0.01) for i in range(10)]
        before = copy.deepcopy(candidates)
        for name, (fn, _version) in spf.POLICY_REGISTRY.items():
            fn(candidates, {})
        self.assertEqual(candidates, before)


class ProbabilityFirstPolicyTests(unittest.TestCase):
    def test_ranks_by_probability_descending(self):
        candidates = [candidate(player_id=1, hit_probability=0.70),
                      candidate(player_id=2, hit_probability=0.90),
                      candidate(player_id=3, hit_probability=0.61)]
        ids = spf.probability_first_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids[0], spf._candidate_id(candidates[1]))  # highest prob first

    def test_below_min_prob_excluded(self):
        candidates = [candidate(player_id=1, hit_probability=0.50)]
        ids = spf.probability_first_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(ids, [])

    def test_missing_probability_excluded_not_a_crash(self):
        candidates = [candidate(player_id=1, hit_probability=None)]
        ids = spf.probability_first_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(ids, [])


class ReliabilityFirstPolicyTests(unittest.TestCase):
    def test_better_reliability_ranks_first(self):
        candidates = [candidate(player_id=1, reliability="B", hit_probability=0.90),
                      candidate(player_id=2, reliability="A", hit_probability=0.61)]
        ids = spf.reliability_first_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(ids[0], spf._candidate_id(candidates[1]))  # A beats B even with lower prob

    def test_missing_reliability_sorts_last(self):
        candidates = [candidate(player_id=1, reliability=None, hit_probability=0.90),
                      candidate(player_id=2, reliability="C", hit_probability=0.61)]
        ids = spf.reliability_first_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(ids[0], spf._candidate_id(candidates[1]))


class CiLowerBoundPolicyTests(unittest.TestCase):
    def test_ranks_by_lower_bound_descending(self):
        candidates = [candidate(player_id=1, prob_ci=[0.50, 0.80]),
                      candidate(player_id=2, prob_ci=[0.60, 0.70])]
        ids = spf.ci_lower_bound_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(ids[0], spf._candidate_id(candidates[1]))  # higher lower-bound first

    def test_no_ci_market_excluded_not_fabricated(self):
        """Markets without a structural CI must not get a fake one --
        candidates with prob_ci=None are simply excluded from this
        policy's ranking, never assigned an invented bound."""
        candidates = [candidate(player_id=1, prob_ci=None),
                      candidate(player_id=2, prob_ci=[0.55, 0.75])]
        ids = spf.ci_lower_bound_policy(candidates, {"min_prob": 0.60})
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], spf._candidate_id(candidates[1]))


class ChampionPolicyTests(unittest.TestCase):
    def test_selects_only_real_top_picks(self):
        # A candidate that clearly fails the Top Pick gates (no odds at all).
        weak = candidate(player_id=1, hit_probability=0.40, market_odds=None)
        strong = candidate(player_id=2, hit_probability=0.90, reliability="A",
                            lineup_assumed=False, market_odds=-150)
        ids = spf.champion_policy([weak, strong])
        # weak should not appear (fails meets_prob_floor and has_odds);
        # strong may or may not clear value depending on real pricing math,
        # but the policy must not crash and must not include obviously
        # disqualified candidates.
        self.assertNotIn(spf._candidate_id(weak), ids)


class RunPoliciesTests(unittest.TestCase):
    def test_every_policy_sees_identical_input(self):
        candidates = [candidate(player_id=i, hit_probability=0.6 + i * 0.02) for i in range(5)]
        before = copy.deepcopy(candidates)
        results = spf.run_policies(candidates, ["probability_first", "reliability_first"],
                                    config={"min_prob": 0.60}, snapshot_id="snap-1")
        self.assertEqual(candidates, before)
        self.assertEqual(set(results.keys()), {"probability_first", "reliability_first"})

    def test_selections_carry_no_outcome_field(self):
        """Frozen pregame / no postgame leakage -- a fresh PolicySelection
        must never contain an 'outcome' or 'grade' key anywhere."""
        candidates = [candidate(player_id=i, hit_probability=0.6 + i * 0.02) for i in range(3)]
        results = spf.run_policies(candidates, ["probability_first"], config={"min_prob": 0.60})
        selection = results["probability_first"]
        self.assertNotIn("outcome", selection)
        self.assertNotIn("grade", selection)
        self.assertNotIn("outcome", json_roundtrip_keys(selection))

    def test_snapshot_id_and_created_at_recorded(self):
        candidates = [candidate()]
        results = spf.run_policies(candidates, ["probability_first"], snapshot_id="snap-42",
                                    created_at="2026-08-25T00:00:00Z")
        self.assertEqual(results["probability_first"]["snapshot_id"], "snap-42")
        self.assertEqual(results["probability_first"]["created_at"], "2026-08-25T00:00:00Z")

    def test_config_hash_differs_for_different_config(self):
        candidates = [candidate()]
        a = spf.run_policies(candidates, ["probability_first"], config={"min_prob": 0.60})
        b = spf.run_policies(candidates, ["probability_first"], config={"min_prob": 0.70})
        self.assertNotEqual(a["probability_first"]["config_hash"], b["probability_first"]["config_hash"])


def json_roundtrip_keys(d):
    import json
    return set(json.loads(json.dumps(d, default=str)).keys())


class GradePolicySelectionTests(unittest.TestCase):
    def test_hit_miss_and_missing_counted_separately(self):
        selection = {"policy_name": "x", "selected_candidate_ids": ["a", "b", "c"]}
        outcomes = {"a": {"grade": "hit"}, "b": {"grade": "miss"}}  # "c" never graded
        result = spf.grade_policy_selection(selection, outcomes)
        self.assertEqual(result["n_hit"], 1)
        self.assertEqual(result["n_miss"], 1)
        self.assertEqual(result["n_ungraded_or_missing"], 1)
        self.assertEqual(result["hit_rate"], 0.5)  # computed only over decided rows

    def test_all_ungraded_gives_none_hit_rate_not_zero(self):
        selection = {"policy_name": "x", "selected_candidate_ids": ["a"]}
        result = spf.grade_policy_selection(selection, {})
        self.assertIsNone(result["hit_rate"])


class ComparePoliciesTests(unittest.TestCase):
    def test_overlap_added_removed_computed_correctly(self):
        champion = {"selected_candidate_ids": ["a", "b", "c"]}
        challenger = {"selected_candidate_ids": ["b", "c", "d"]}
        outcomes = {"a": {"grade": "miss"}, "b": {"grade": "hit"}, "c": {"grade": "hit"},
                    "d": {"grade": "hit"}}
        result = spf.compare_policies(champion, challenger, outcomes, allow_missing_snapshot=True)
        self.assertEqual(result["n_overlap"], 2)  # b, c
        self.assertEqual(result["n_champion_only_removed_by_challenger"], 1)  # a
        self.assertEqual(result["n_challenger_only_added"], 1)  # d
        self.assertEqual(result["removed_hit_rate"], 0.0)  # a was a miss
        self.assertEqual(result["added_hit_rate"], 1.0)  # d was a hit
        self.assertEqual(result["overlap_hit_rate"], 1.0)  # b, c both hits

    def test_ungraded_ids_excluded_from_rate_but_counted_in_selection_size(self):
        champion = {"selected_candidate_ids": ["a"]}
        challenger = {"selected_candidate_ids": ["a"]}
        result = spf.compare_policies(champion, challenger, {}, allow_missing_snapshot=True)
        self.assertEqual(result["n_overlap"], 1)
        self.assertIsNone(result["overlap_hit_rate"])
        self.assertEqual(result["overlap_n_graded"], 0)

    def test_mismatched_snapshot_ids_raise_instead_of_silently_comparing(self):
        """Real gap found 2026-08-25 during a leakage/candidate-universe-
        mismatch review of the shadow-policy framework: comparing two
        selections built from different snapshots (different dates, or the
        same date re-fetched after the live board moved) used to silently
        produce a plausible-looking but meaningless overlap/added/removed
        result. Both real run_policies() selections always carry a real
        snapshot_id, so this guard is exactly effective on the path that
        matters. Same policy on both sides, different snapshot -- still
        must raise; the guard cares about candidate-universe identity, not
        which policy produced the selection."""
        champion = {"policy_name": "champion", "selected_candidate_ids": ["a"], "snapshot_id": "snap-1"}
        challenger = {"policy_name": "champion", "selected_candidate_ids": ["a"], "snapshot_id": "snap-2"}
        with self.assertRaises(ValueError):
            spf.compare_policies(champion, challenger, {})

    def test_missing_snapshot_metadata_fails_closed_by_default(self):
        """Hardened 2026-08-25 per an explicit "fail closed on missing
        metadata" directive: a selection missing snapshot_id entirely used
        to be silently treated as "untagged, not a mismatch." That was too
        permissive -- a real run_policies() selection should always carry a
        snapshot_id, so a missing one is itself a signal something upstream
        is wrong, not a benign default. Now raises unless the caller
        explicitly opts into the legacy path."""
        champion = {"selected_candidate_ids": ["a"]}  # no snapshot_id at all
        challenger = {"selected_candidate_ids": ["a"], "snapshot_id": "snap-1"}
        with self.assertRaises(ValueError):
            spf.compare_policies(champion, challenger, {})
        # Neither side tagged -- still fails closed by default.
        challenger_untagged = {"selected_candidate_ids": ["a"]}
        with self.assertRaises(ValueError):
            spf.compare_policies(champion, challenger_untagged, {})

    def test_missing_snapshot_metadata_allowed_via_explicit_legacy_opt_in(self):
        champion = {"selected_candidate_ids": ["a"]}
        challenger = {"selected_candidate_ids": ["a"]}
        result = spf.compare_policies(champion, challenger, {}, allow_missing_snapshot=True)
        self.assertEqual(result["n_overlap"], 1)

    def test_matching_snapshot_ids_do_not_raise(self):
        champion = {"selected_candidate_ids": ["a"], "snapshot_id": "snap-1"}
        challenger = {"selected_candidate_ids": ["a"], "snapshot_id": "snap-1"}
        result = spf.compare_policies(champion, challenger, {})
        self.assertEqual(result["n_overlap"], 1)

    def test_different_policy_same_snapshot_is_valid(self):
        # The real, intended use case: two DIFFERENT policies' selections
        # from the SAME candidate universe -- must compare cleanly.
        champion = {"policy_name": "champion", "selected_candidate_ids": ["a", "b"], "snapshot_id": "snap-1"}
        challenger = {"policy_name": "probability_first", "selected_candidate_ids": ["b", "c"], "snapshot_id": "snap-1"}
        result = spf.compare_policies(champion, challenger, {})
        self.assertEqual(result["n_overlap"], 1)  # b
        self.assertEqual(result["n_challenger_only_added"], 1)  # c
        self.assertEqual(result["n_champion_only_removed_by_challenger"], 1)  # a

    def test_copied_candidate_objects_same_snapshot_compare_correctly_by_id_string(self):
        # candidate_ids in a PolicySelection are semantic strings (built by
        # _candidate_id() from real fields), not Python object identity --
        # so two selections built from SEPARATELY reconstructed candidate
        # dicts (e.g. re-run_policies() on a re-fetched-but-logically-
        # identical snapshot) still compare correctly as long as the
        # candidate_id strings themselves match.
        candidates_a = [dict(date="2024-05-14", game_pk=1, player_id=100,
                              projection={"stat": "hits"}, hit_probability=0.65)]
        candidates_b = [dict(date="2024-05-14", game_pk=1, player_id=100,
                              projection={"stat": "hits"}, hit_probability=0.65)]  # separate objects
        self.assertIsNot(candidates_a[0], candidates_b[0])
        sel_a = spf.run_policies(candidates_a, ["probability_first"], snapshot_id="snap-1")["probability_first"]
        sel_b = spf.run_policies(candidates_b, ["probability_first"], snapshot_id="snap-1")["probability_first"]
        result = spf.compare_policies(sel_a, sel_b, {})
        self.assertEqual(result["n_overlap"], 1)
        self.assertEqual(result["n_champion_only_removed_by_challenger"], 0)
        self.assertEqual(result["n_challenger_only_added"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
