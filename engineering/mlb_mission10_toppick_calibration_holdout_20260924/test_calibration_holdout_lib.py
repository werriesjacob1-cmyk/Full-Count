import math
import unittest

from calibration_holdout_lib import (
    Row,
    assert_pregame_integrity,
    cluster_bootstrap_gap_ci,
    exact_binomial_two_sided_pvalue,
    load_public_top_picks,
    summarize,
)


def make_row(cid, date, game_pk, player_id, stat, prob, grade, pub_at, start_at):
    return Row(
        candidate_id=cid,
        slate_date=date,
        game_pk=game_pk,
        player_id=player_id,
        stat=stat,
        hit_probability=prob,
        grade=grade,
        published_top_pick_at=pub_at,
        game_start=start_at,
    )


class ExactBinomialTest(unittest.TestCase):
    def test_matches_known_scipy_style_value_symmetric_case(self):
        # n=10, p=0.5, observed=5 (the mode) must give p-value 1.0 exactly.
        p = exact_binomial_two_sided_pvalue(5, 10, 0.5)
        self.assertAlmostEqual(p, 1.0, places=9)

    def test_extreme_observation_gives_small_pvalue(self):
        # n=371, p=0.646, observed=199 reproduces PR #128's real finding
        # (p = 0.000009, per the merged PR #128 documentation).
        p = exact_binomial_two_sided_pvalue(199, 371, 0.646)
        self.assertLess(p, 0.0001)
        self.assertGreater(p, 0.0)

    def test_all_hits_p_equal_one_edge_case(self):
        p = exact_binomial_two_sided_pvalue(10, 10, 1.0)
        self.assertAlmostEqual(p, 1.0, places=9)

    def test_zero_hits_with_zero_probability_is_certain(self):
        p = exact_binomial_two_sided_pvalue(0, 10, 0.0)
        self.assertAlmostEqual(p, 1.0, places=9)

    def test_rejects_invalid_probability(self):
        with self.assertRaises(ValueError):
            exact_binomial_two_sided_pvalue(1, 10, 1.5)

    def test_rejects_observed_out_of_range(self):
        with self.assertRaises(ValueError):
            exact_binomial_two_sided_pvalue(11, 10, 0.5)


class SummarizeTest(unittest.TestCase):
    def test_basic_gap_direction(self):
        rows = [
            make_row("a", "2026-09-19", "1", "p1", "hits", 0.70, "hit", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
            make_row("b", "2026-09-19", "1", "p2", "hits", 0.70, "miss", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
        ]
        s = summarize(rows)
        self.assertEqual(s.n, 2)
        self.assertEqual(s.hits, 1)
        self.assertAlmostEqual(s.mean_predicted, 0.70)
        self.assertAlmostEqual(s.realized_rate, 0.50)
        self.assertAlmostEqual(s.gap, 0.20)

    def test_empty_rows_returns_nan(self):
        s = summarize([])
        self.assertEqual(s.n, 0)
        self.assertTrue(math.isnan(s.gap))


class PregameIntegrityTest(unittest.TestCase):
    def test_passes_when_all_published_before_start(self):
        rows = [
            make_row("a", "2026-09-19", "1", "p1", "hits", 0.6, "hit", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
        ]
        assert_pregame_integrity(rows)  # should not raise

    def test_raises_when_published_after_start(self):
        rows = [
            make_row("a", "2026-09-19", "1", "p1", "hits", 0.6, "hit", "2026-09-19T19:00:00Z", "2026-09-19T18:00:00Z"),
        ]
        with self.assertRaises(AssertionError):
            assert_pregame_integrity(rows)

    def test_raises_when_published_equals_start(self):
        rows = [
            make_row("a", "2026-09-19", "1", "p1", "hits", 0.6, "hit", "2026-09-19T18:00:00Z", "2026-09-19T18:00:00Z"),
        ]
        with self.assertRaises(AssertionError):
            assert_pregame_integrity(rows)


class ClusterBootstrapTest(unittest.TestCase):
    def test_ci_contains_point_estimate_and_is_deterministic(self):
        rows = [
            make_row("a", "2026-09-19", "1", "p1", "hits", 0.65, "hit", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
            make_row("b", "2026-09-19", "1", "p2", "hits", 0.65, "miss", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
            make_row("c", "2026-09-20", "2", "p3", "hits", 0.65, "miss", "2026-09-20T10:00:00Z", "2026-09-20T18:00:00Z"),
            make_row("d", "2026-09-21", "3", "p4", "hits", 0.65, "hit", "2026-09-21T10:00:00Z", "2026-09-21T18:00:00Z"),
        ]
        point = summarize(rows).gap
        lo1, hi1 = cluster_bootstrap_gap_ci(rows, n_resamples=2000, seed=1)
        lo2, hi2 = cluster_bootstrap_gap_ci(rows, n_resamples=2000, seed=1)
        self.assertEqual((lo1, hi1), (lo2, hi2))  # deterministic given a seed
        self.assertLessEqual(lo1, point + 1e-9)
        self.assertGreaterEqual(hi1, point - 1e-9)

    def test_single_cluster_has_zero_width_ci(self):
        # All rows share one (date, game_pk) cluster -> every bootstrap draw
        # is the same set of rows -> zero-width CI at the point estimate.
        rows = [
            make_row("a", "2026-09-19", "1", "p1", "hits", 0.60, "hit", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
            make_row("b", "2026-09-19", "1", "p2", "hits", 0.60, "miss", "2026-09-19T10:00:00Z", "2026-09-19T18:00:00Z"),
        ]
        lo, hi = cluster_bootstrap_gap_ci(rows, n_resamples=500, seed=2)
        point = summarize(rows).gap
        self.assertAlmostEqual(lo, point)
        self.assertAlmostEqual(hi, point)

    def test_raises_on_empty_rows(self):
        with self.assertRaises(ValueError):
            cluster_bootstrap_gap_ci([], n_resamples=100)


class LoadPublicTopPicksTest(unittest.TestCase):
    def test_filters_grade_and_excludes_named_id(self):
        docs = {
            "f1.json": {
                "date": "2026-09-19",
                "public_top_picks": [
                    {
                        "id": "keep-1",
                        "grade": "hit",
                        "hit_probability": 0.6,
                        "game_pk": 100,
                        "player_id": 1,
                        "stat": "hits",
                        "slate_date": "2026-09-19",
                        "published_top_pick_at": "2026-09-19T10:00:00Z",
                        "game_start": "2026-09-19T18:00:00Z",
                    },
                    {
                        "id": "exclude-me",
                        "grade": "miss",
                        "hit_probability": 0.7,
                        "game_pk": 101,
                        "player_id": 2,
                        "stat": "hits",
                        "slate_date": "2026-09-19",
                        "published_top_pick_at": "2026-09-19T10:00:00Z",
                        "game_start": "2026-09-19T18:00:00Z",
                    },
                    {
                        "id": "void-row",
                        "grade": "void",
                        "hit_probability": 0.5,
                        "game_pk": 102,
                        "player_id": 3,
                        "stat": "hits",
                        "slate_date": "2026-09-19",
                        "published_top_pick_at": "2026-09-19T10:00:00Z",
                        "game_start": "2026-09-19T18:00:00Z",
                    },
                    {
                        "id": "no-prob",
                        "grade": "hit",
                        "hit_probability": None,
                        "game_pk": 103,
                        "player_id": 4,
                        "stat": "hits",
                        "slate_date": "2026-09-19",
                        "published_top_pick_at": "2026-09-19T10:00:00Z",
                        "game_start": "2026-09-19T18:00:00Z",
                    },
                ],
            }
        }
        rows = load_public_top_picks(docs, excluded_ids={"exclude-me"})
        ids = {r.candidate_id for r in rows}
        self.assertEqual(ids, {"keep-1"})


if __name__ == "__main__":
    unittest.main()
