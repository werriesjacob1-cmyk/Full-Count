#!/usr/bin/env python3
"""test_pa_opportunity_model.py -- coverage for
backtest/pa_opportunity_model.py, Priority 2+3 of the opportunity-modeling
phase (2026-08-25). Synthetic fixtures only.

    /tmp/mlbvenv/bin/python3 test_pa_opportunity_model.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import pa_opportunity_model as pom


def row(**overrides):
    r = {
        "date": "2024-05-14", "game_pk": 1, "player_id": 100,
        "prop_type": "hits", "predicted_prob": 0.62, "outcome": 1,
        "actual_pa": 4, "signals": {"lineup_slot": 100.0},  # order 1
    }
    r.update(overrides)
    return r


class DedupePlayerGamesTests(unittest.TestCase):
    def test_multiple_markets_same_player_game_dedupe_to_one(self):
        rows = [row(prop_type="hits"), row(prop_type="total_bases"),
                row(prop_type="home_run")]
        deduped = pom.dedupe_player_games(rows)
        self.assertEqual(len(deduped), 1)

    def test_different_players_both_kept(self):
        rows = [row(player_id=100), row(player_id=200)]
        deduped = pom.dedupe_player_games(rows)
        self.assertEqual(len(deduped), 2)

    def test_different_games_same_player_both_kept(self):
        rows = [row(game_pk=1), row(game_pk=2)]
        deduped = pom.dedupe_player_games(rows)
        self.assertEqual(len(deduped), 2)


class FitPaDistributionTests(unittest.TestCase):
    def test_distribution_sums_to_one_per_order(self):
        rows = [row(actual_pa=3, signals={"lineup_slot": 100.0}),
                row(actual_pa=4, signals={"lineup_slot": 100.0}, player_id=200),
                row(actual_pa=4, signals={"lineup_slot": 100.0}, player_id=300)]
        dist = pom.fit_pa_distribution(rows)
        total = sum(v for k, v in dist[1].items() if k != "_n")
        self.assertAlmostEqual(total, 1.0, places=5)
        self.assertEqual(dist[1]["_n"], 3)
        self.assertAlmostEqual(dist[1]["4"], 2 / 3, places=5)

    def test_rows_without_order_or_pa_excluded(self):
        rows = [row(signals={}), row(actual_pa=None)]
        dist = pom.fit_pa_distribution(rows)
        self.assertEqual(dist, {})

    def test_six_plus_bucket_used_for_high_pa(self):
        rows = [row(actual_pa=7, signals={"lineup_slot": 100.0})]
        dist = pom.fit_pa_distribution(rows)
        self.assertEqual(dist[1]["6+"], 1.0)


class FitHitRateGivenPaTests(unittest.TestCase):
    def test_correct_per_pa_hit_rate(self):
        rows = [row(prop_type="hits", actual_pa=3, outcome=1),
                row(prop_type="hits", actual_pa=3, outcome=0),
                row(prop_type="hits", actual_pa=5, outcome=1)]
        rates = pom.fit_hit_rate_given_pa(rows, "hits")
        self.assertEqual(rates["3"], 0.5)
        self.assertEqual(rates["5"], 1.0)

    def test_other_markets_excluded(self):
        rows = [row(prop_type="total_bases", actual_pa=3, outcome=1)]
        rates = pom.fit_hit_rate_given_pa(rows, "hits")
        self.assertEqual(rates, {})


class ChallengerProbabilityTests(unittest.TestCase):
    def test_weighted_average_computed_correctly(self):
        pa_dist = {1: {"3": 0.5, "4": 0.5, "_n": 10}}
        hit_rate = {"3": 0.4, "4": 0.6}
        p = pom.challenger_probability(1, pa_dist, hit_rate)
        self.assertAlmostEqual(p, 0.5, places=5)

    def test_unseen_order_returns_none(self):
        pa_dist = {1: {"3": 1.0, "_n": 5}}
        p = pom.challenger_probability(9, pa_dist, {"3": 0.5})
        self.assertIsNone(p)

    def test_missing_hit_rate_for_every_pa_state_returns_none(self):
        pa_dist = {1: {"7_never_priced": 1.0, "_n": 5}}
        p = pom.challenger_probability(1, pa_dist, {})
        self.assertIsNone(p)

    def test_partial_pricing_still_produces_a_normalized_result(self):
        # order-1 distribution has mass on "3" (priced) and "6+" (unpriced) --
        # result should renormalize over only the priced mass, not silently
        # treat the unpriced state as zero probability.
        pa_dist = {1: {"3": 0.5, "6+": 0.5, "_n": 10}}
        hit_rate = {"3": 0.4}
        p = pom.challenger_probability(1, pa_dist, hit_rate)
        self.assertAlmostEqual(p, 0.4, places=5)


class EqualVolumeRankingComparisonTests(unittest.TestCase):
    def _comp(self, current_prob, challenger_prob, outcome, order=1):
        return {"current_prob": current_prob, "challenger_prob": challenger_prob,
                "outcome": outcome, "order": order}

    def test_volume_matches_current_selected_count(self):
        comparisons = [self._comp(0.65, 0.60, 1, order=i) for i in range(5)] + \
                      [self._comp(0.50, 0.70, 0, order=i + 10) for i in range(5)]
        result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 5)
        self.assertEqual(result["n_challenger_selected"], 5)

    def test_perfect_overlap_when_rankings_agree(self):
        comparisons = [self._comp(0.9 - i * 0.05, 0.9 - i * 0.05, 1, order=i) for i in range(4)]
        result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
        self.assertEqual(result["n_overlap"], result["n_current_selected"])
        self.assertEqual(result["n_added_by_challenger"], 0)
        self.assertEqual(result["n_removed_by_challenger"], 0)

    def test_added_and_removed_when_rankings_disagree(self):
        # current selects A,B (>=0.60); challenger's top-2 by its own prob is C,D
        comparisons = [
            self._comp(0.70, 0.10, 1, order=1),  # A: current-selected, challenger ranks it low
            self._comp(0.65, 0.15, 0, order=2),  # B: current-selected, challenger ranks it low
            self._comp(0.50, 0.90, 1, order=3),  # C: not current-selected, challenger top
            self._comp(0.40, 0.85, 1, order=4),  # D: not current-selected, challenger top
        ]
        result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 2)
        self.assertEqual(result["n_added_by_challenger"], 2)
        self.assertEqual(result["n_removed_by_challenger"], 2)
        self.assertEqual(result["added_hit_rate"], 1.0)
        self.assertEqual(result["removed_hit_rate"], 0.5)

    def test_empty_current_selection_returns_zero_without_crash(self):
        comparisons = [self._comp(0.10, 0.10, 0)]
        result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 0)

    def test_tied_challenger_prob_at_the_selection_boundary_is_counted_by_identity_not_value(self):
        """Real bug found and fixed 2026-08-25 during a methodological
        review ahead of the (not-yet-run) decisive disagreement test:
        identity used to be a VALUE tuple (order, current_prob,
        challenger_prob, outcome). Ties on that tuple are common by
        construction in the disagreement work -- challenger_prob is a
        shared empirical rate across an entire (bucket, tier) cell -- so
        multiple genuinely distinct candidates can carry the identical
        tuple. When such a tie straddles the top-N cutoff, the old code
        treated "does this exact tuple exist ANYWHERE in
        challenger_selected" as true for every tied candidate, silently
        undercounting `removed` and overcounting `overlap`. This reproduces
        that exact scenario: 4 candidates share one tuple and are all
        current-selected; 2 higher-challenger-prob candidates outrank them
        and are NOT current-selected. Only 2 of the 4 tied candidates
        should genuinely survive the top-4 challenger cut."""
        # order shares the SAME value across all of these too (default=1) --
        # under the old value-tuple identity this was the worst case
        # (every field tied); id()-based identity is unaffected either way.
        tied = [self._comp(0.65, 0.50, 1) for _ in range(4)]
        higher_not_current = [self._comp(0.10, 0.99, 1) for _ in range(2)]
        comparisons = tied + higher_not_current

        result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 4)
        self.assertEqual(result["n_overlap"], 2)
        self.assertEqual(result["n_removed_by_challenger"], 2)
        self.assertEqual(result["n_added_by_challenger"], 2)


class CandidateKeyTests(unittest.TestCase):
    def _row(self, **overrides):
        r = {"date": "2024-05-14", "game_pk": 1, "player_id": 100, "prop_type": "total_bases",
             "line": 0.5}
        r.update(overrides)
        return r

    def test_builds_composite_key_from_real_fields(self):
        self.assertEqual(pom.candidate_key(self._row()),
                          ("2024-05-14", 1, 100, "total_bases", 0.5, None))

    def test_missing_required_field_returns_none(self):
        self.assertIsNone(pom.candidate_key({"date": "2024-05-14", "game_pk": 1}))

    def test_missing_line_returns_none(self):
        # Real canonical backtest rows always carry `line` -- a row without
        # one (e.g. a malformed fixture) should not silently get a key.
        r = self._row()
        del r["line"]
        self.assertIsNone(pom.candidate_key(r))

    def test_alternate_lines_for_same_player_market_produce_distinct_keys(self):
        """Real gap found 2026-08-25: two genuinely different lines for the
        same player/game/market (Over 0.5 Total Bases vs Over 1.5 Total
        Bases) must never be collapsed into one candidate identity."""
        half = pom.candidate_key(self._row(line=0.5))
        one_half = pom.candidate_key(self._row(line=1.5))
        self.assertNotEqual(half, one_half)

    def test_opposite_side_same_line_produces_distinct_keys(self):
        # No real backtest row carries `side` today (engine.py never
        # writes one -- this product's backtest only grades Over bets),
        # but the key must not collapse Over/Under if that ever changes.
        over = pom.candidate_key(self._row(side="over"))
        under = pom.candidate_key(self._row(side="under"))
        self.assertNotEqual(over, under)

    def test_different_game_date_produces_distinct_key(self):
        a = pom.candidate_key(self._row(date="2024-05-14", game_pk=1))
        b = pom.candidate_key(self._row(date="2024-05-15", game_pk=2))
        self.assertNotEqual(a, b)

    def test_same_logical_candidate_reconstructed_produces_the_same_key(self):
        a = pom.candidate_key(self._row())
        b = pom.candidate_key(self._row())  # separately built, same real-world candidate
        self.assertEqual(a, b)


class EqualVolumeRankingIdentityAuditTests(unittest.TestCase):
    """Locks in the 2026-08-25 candidate-identity audit: id()-based
    selection-set membership is structurally correct (this function takes
    ONE comparisons list, never merges two independently-built lists), and
    the separate _candidate_key duplicate check catches the one thing
    id() alone cannot: the same real-world candidate entering the list
    twice as two distinct dict objects."""

    def _row(self, current_prob, challenger_prob, outcome, key=None):
        c = {"current_prob": current_prob, "challenger_prob": challenger_prob, "outcome": outcome}
        if key is not None:
            c["_candidate_key"] = key
        return c

    def test_copied_but_logically_identical_dicts_without_key_are_not_collapsed(self):
        # Two SEPARATE dict objects, identical values, no _candidate_key --
        # id() must treat them as two distinct candidates (not silently
        # dedupe by value), since without a real key there is no basis to
        # assume they're the same real-world candidate.
        a = self._row(0.65, 0.50, 1)
        b = dict(a)  # a genuine copy -- distinct object, identical values
        comparisons = [a, b]
        result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 2)

    def test_same_candidate_key_reconstructed_as_separate_object_raises(self):
        # Simulates a JSON round-trip or duplicate upstream row: two
        # DISTINCT dict objects sharing the same real-world identity.
        key = ("2024-05-14", 1, 100, "hits")
        a = self._row(0.65, 0.50, 1, key=key)
        b = self._row(0.65, 0.50, 1, key=key)  # reconstructed separately, same key
        self.assertIsNot(a, b)
        with self.assertRaises(ValueError):
            pom.equal_volume_ranking_comparison([a, b], min_line_prob=0.60)

    def test_same_player_different_games_not_treated_as_duplicate(self):
        a = self._row(0.65, 0.50, 1, key=("2024-05-14", 1, 100, "hits"))
        b = self._row(0.65, 0.55, 0, key=("2024-05-15", 2, 100, "hits"))  # same player, diff game/date
        result = pom.equal_volume_ranking_comparison([a, b], min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 2)

    def test_different_market_same_player_game_not_treated_as_duplicate(self):
        a = self._row(0.65, 0.50, 1, key=("2024-05-14", 1, 100, "hits"))
        b = self._row(0.65, 0.55, 0, key=("2024-05-14", 1, 100, "total_bases"))
        result = pom.equal_volume_ranking_comparison([a, b], min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 2)

    def test_alternate_line_same_player_market_game_not_treated_as_duplicate(self):
        # End-to-end proof (not just candidate_key() in isolation): Over 0.5
        # Total Bases vs Over 1.5 Total Bases for the same player/game must
        # both survive as distinct candidates through the full comparison.
        half = self._row(0.65, 0.50, 1, key=("2024-05-14", 1, 100, "total_bases", 0.5, None))
        one_half = self._row(0.62, 0.45, 0, key=("2024-05-14", 1, 100, "total_bases", 1.5, None))
        result = pom.equal_volume_ranking_comparison([half, one_half], min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 2)

    def test_opposite_side_same_line_not_treated_as_duplicate(self):
        over = self._row(0.65, 0.50, 1, key=("2024-05-14", 1, 100, "hits", 0.5, "over"))
        under = self._row(0.65, 0.55, 0, key=("2024-05-14", 1, 100, "hits", 0.5, "under"))
        result = pom.equal_volume_ranking_comparison([over, under], min_line_prob=0.60)
        self.assertEqual(result["n_current_selected"], 2)

    def test_reordering_the_input_list_does_not_change_the_result(self):
        rows = [self._row(0.70, 0.10, 1), self._row(0.65, 0.15, 0),
                self._row(0.50, 0.90, 1), self._row(0.40, 0.85, 1)]
        forward = pom.equal_volume_ranking_comparison(list(rows), min_line_prob=0.60)
        reversed_order = pom.equal_volume_ranking_comparison(list(reversed(rows)), min_line_prob=0.60)
        for field in ("n_current_selected", "n_challenger_selected", "n_overlap",
                      "n_removed_by_challenger", "n_added_by_challenger",
                      "added_hit_rate", "removed_hit_rate"):
            self.assertEqual(forward[field], reversed_order[field], field)

    def test_invariants_hold_across_varied_synthetic_populations(self):
        import random
        rng = random.Random(20260825)
        for trial in range(25):
            comparisons = []
            for i in range(rng.randint(5, 60)):
                comparisons.append(self._row(
                    current_prob=round(rng.uniform(0.30, 0.85), 3),
                    challenger_prob=round(rng.choice([0.40, 0.50, 0.55, 0.60, 0.65, 0.70]), 3),
                    outcome=rng.randint(0, 1),
                ))
            result = pom.equal_volume_ranking_comparison(comparisons, min_line_prob=0.60)
            if result["n_current_selected"] == 0:
                continue
            n_current = result["n_current_selected"]
            n_challenger = result["n_challenger_selected"]
            n_overlap = result["n_overlap"]
            n_removed = result["n_removed_by_challenger"]
            n_added = result["n_added_by_challenger"]
            self.assertEqual(n_overlap + n_removed, n_current, f"trial {trial}")
            self.assertEqual(n_overlap + n_added, n_challenger, f"trial {trial}")
            if n_current == n_challenger:
                self.assertEqual(n_added, n_removed, f"trial {trial}")

    def test_determinism_across_separate_invocations_with_fresh_objects(self):
        def build():
            return [self._row(0.70, 0.10, 1), self._row(0.65, 0.15, 0),
                    self._row(0.50, 0.90, 1), self._row(0.40, 0.85, 1),
                    self._row(0.65, 0.50, 1), self._row(0.65, 0.50, 0)]
        first = pom.equal_volume_ranking_comparison(build(), min_line_prob=0.60)
        second = pom.equal_volume_ranking_comparison(build(), min_line_prob=0.60)
        self.assertEqual(first, second)


class BuildReportTests(unittest.TestCase):
    def test_train_holdout_split_by_year(self):
        # One real "hits" row per player-game side -- pre-2026-08-25 this
        # test fed 50 LITERAL duplicate (date, game_pk, player_id, "hits")
        # rows per side into equal_volume_ranking_comparison(), which never
        # occurs in real canonical data (one row per player-game-market) and
        # is now correctly rejected by the duplicate-candidate_key guard.
        # dedupe_player_games()'s own collapse-to-one-player-game behavior
        # (what this test actually checks) needs only one row per side.
        rows = [row(date="2024-05-01"), row(date="2026-05-01", player_id=999)]
        report = pom.build_report(rows, market="hits")
        self.assertEqual(report["n_train_player_games"], 1)  # all same player_id=100
        self.assertEqual(report["n_holdout_player_games"], 1)

    def test_discrimination_report_present_for_populated_bucket(self):
        rows = []
        for i in range(60):
            order = 1 if i % 2 == 0 else 9
            rows.append(row(date="2024-05-01", player_id=i, order=None,
                             signals={"lineup_slot": 100.0 if order == 1 else 0.0},
                             actual_pa=5 if order == 1 else 2,
                             outcome=1 if order == 1 else 0,
                             predicted_prob=0.62))
        for i in range(60, 120):
            order = 1 if i % 2 == 0 else 9
            rows.append(row(date="2026-05-01", player_id=i,
                             signals={"lineup_slot": 100.0 if order == 1 else 0.0},
                             actual_pa=5 if order == 1 else 2,
                             outcome=1 if order == 1 else 0,
                             predicted_prob=0.62))
        report = pom.build_report(rows, market="hits")
        self.assertIn("0.60-0.65", report["discrimination_within_current_probability_bucket"])

    def test_empty_input_does_not_crash(self):
        report = pom.build_report([], market="hits")
        self.assertEqual(report["n_train_player_games"], 0)
        self.assertEqual(report["n_holdout_player_games"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
