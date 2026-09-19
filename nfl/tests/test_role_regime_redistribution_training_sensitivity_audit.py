#!/usr/bin/env python3
"""Tests for `role_regime_redistribution_training_sensitivity_audit.py`.

Uses a tiny, hand-computable synthetic model-fitting/predicting/evaluation
stand-in (NOT PR #147's real `HIERARCHICAL_COMMITTEE_PROBABILITY_V1` -- that
model is out of this module's scope; see the module's own docstring) so the
expected numbers can be verified by hand rather than merely re-asserting
whatever the code happens to compute.
"""
import unittest

from nfl.research.role_regime_redistribution_training_sensitivity_audit import (
    TrainingSensitivityInputs,
    compute_training_sensitivity,
    event_population_digest,
)

TRAIN_SEASONS = frozenset({2020})
HELD_OUT_SEASONS = frozenset({2021})


def _event(event_type, season, week, team, removed_player_id, value=None, realized=None):
    e = {
        "event_type": event_type,
        "season": season,
        "week": week,
        "team": team,
        "removed_player_id": removed_player_id,
    }
    if value is not None:
        e["value"] = value
    if realized is not None:
        e["realized"] = realized
    return e


def _synthetic_train_committee_model_fn(events_with_regime, candidates, usage_rows, dimension, *, train_seasons):
    """Hand-computable stand-in: fits ONE scalar weight per bucket, the mean
    of `value` across training-season events in that bucket."""
    train_events = [e for e in events_with_regime if e["season"] in train_seasons]
    buckets: dict[str, list[float]] = {}
    for e in train_events:
        buckets.setdefault(e.get("bucket", "ONLY_BUCKET"), []).append(e["value"])
    weights = {b: [sum(vs) / len(vs)] for b, vs in buckets.items()}
    return {
        "weights": weights,
        "n_training_examples": len(train_events),
        "n_training_examples_by_bucket": {b: len(vs) for b, vs in buckets.items()},
    }


def _synthetic_build_challenger_predictor_fn(model):
    weight = model["weights"]["ONLY_BUCKET"][0]

    def predictor(event):
        return weight

    return predictor


def _synthetic_compute_paired_evaluation_fn(
    events_with_regime, candidates, role_state_rows, usage_rows, dimension, predictors, *, seasons,
):
    scoped = [e for e in events_with_regime if e["season"] in seasons]
    mae_by_predictor = {}
    for name, fn in predictors.items():
        errors = [abs(fn(e) - e["realized"]) for e in scoped]
        mae_by_predictor[name] = (sum(errors) / len(errors)) if errors else None
    return {"paired_n": len(scoped), "mae_by_predictor": mae_by_predictor}


_ALWAYS_ZERO_BASELINE = {"ALWAYS_ZERO": lambda event: 0.0}

_COMMON_KWARGS = dict(
    train_committee_model_fn=_synthetic_train_committee_model_fn,
    build_challenger_predictor_fn=_synthetic_build_challenger_predictor_fn,
    compute_paired_evaluation_fn=_synthetic_compute_paired_evaluation_fn,
    baseline_predictors=_ALWAYS_ZERO_BASELINE,
    challenger_name="TOY_MODEL",
    train_seasons=TRAIN_SEASONS,
    held_out_seasons=HELD_OUT_SEASONS,
)

# Shared held-out event: identical in every scenario below unless a test
# explicitly perturbs it.
_HELD_OUT_EVENT = _event("TOY_ABSENCE", 2021, 1, "AAA", "p1", realized=12.0)


class TrainingSensitivityKnownDifferenceTests(unittest.TestCase):
    """The core hand-computable case: variant B's training population has
    exactly one extra event (value=100.0) relative to variant A; the
    held-out population is identical. Expected numbers, worked by hand:

    variant A training values: [10.0, 20.0] -> weight = 15.0
    variant B training values: [10.0, 20.0, 100.0] -> weight = 130/3 = 43.333...

    Held-out has one row, realized=12.0, prediction = fitted weight (the
    synthetic predictor is the constant weight itself), so:
    variant A challenger MAE = |15.0 - 12.0| = 3.0
    variant B challenger MAE = |43.333... - 12.0| = 31.333...
    mae_abs_delta_challenger = |3.0 - 31.333...| = 28.333... == weight delta
    exactly, because the predictor is linear in the fitted weight and there
    is only one held-out row.
    """

    def setUp(self):
        train_a = [
            _event("TOY_ABSENCE", 2020, 1, "AAA", "x1", value=10.0),
            _event("TOY_ABSENCE", 2020, 2, "AAA", "x2", value=20.0),
        ]
        train_b = train_a + [_event("TOY_ABSENCE", 2020, 3, "AAA", "x3", value=100.0)]

        self.variant_a = TrainingSensitivityInputs(
            name="population_a", events_with_regime=train_a + [_HELD_OUT_EVENT],
            candidates=[], role_state_rows=[], usage_rows=[],
        )
        self.variant_b = TrainingSensitivityInputs(
            name="population_b", events_with_regime=train_b + [_HELD_OUT_EVENT],
            candidates=[], role_state_rows=[], usage_rows=[],
        )

    def test_training_example_counts_differ_by_exactly_one(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        self.assertEqual(result.n_training_examples["population_a"], 2)
        self.assertEqual(result.n_training_examples["population_b"], 3)
        self.assertEqual(
            result.n_training_examples_by_bucket["population_a"], {"ONLY_BUCKET": 2},
        )
        self.assertEqual(
            result.n_training_examples_by_bucket["population_b"], {"ONLY_BUCKET": 3},
        )

    def test_fitted_weight_matches_hand_computed_mean(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        self.assertAlmostEqual(result.fitted_weights["population_a"]["ONLY_BUCKET"][0], 15.0, places=12)
        self.assertAlmostEqual(result.fitted_weights["population_b"]["ONLY_BUCKET"][0], 130.0 / 3.0, places=12)

    def test_weight_delta_matches_hand_computed_value(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        expected_delta = abs(15.0 - (130.0 / 3.0))
        self.assertAlmostEqual(result.weight_delta_by_bucket["ONLY_BUCKET"][0], expected_delta, places=12)
        self.assertAlmostEqual(result.max_abs_weight_delta, expected_delta, places=12)

    def test_held_out_population_correctly_identified_as_identical(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        self.assertTrue(result.held_out_populations_identical)
        self.assertEqual(
            result.held_out_event_digest["population_a"], result.held_out_event_digest["population_b"],
        )
        self.assertEqual(result.held_out_paired_n, {"population_a": 1, "population_b": 1})

    def test_challenger_mae_and_delta_match_hand_computed_values(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        mae_a = result.mae_by_predictor["population_a"]["TOY_MODEL"]
        mae_b = result.mae_by_predictor["population_b"]["TOY_MODEL"]
        self.assertAlmostEqual(mae_a, 3.0, places=12)
        self.assertAlmostEqual(mae_b, abs(130.0 / 3.0 - 12.0), places=12)

        expected_delta = abs(mae_a - mae_b)
        self.assertAlmostEqual(result.mae_abs_delta_challenger, expected_delta, places=12)
        # The predictor is linear in the fitted weight with exactly one
        # held-out row, so the MAE delta must equal the weight delta exactly.
        self.assertAlmostEqual(
            result.mae_abs_delta_challenger, result.max_abs_weight_delta, places=12,
        )

    def test_unfitted_baseline_is_unaffected_by_the_training_change(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        self.assertEqual(result.mae_abs_delta_by_predictor["ALWAYS_ZERO"], 0.0)

    def test_a_real_difference_is_not_reported_as_bit_identical(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        self.assertFalse(result.bit_identical_predictions)
        self.assertGreater(result.max_abs_mae_delta_any_predictor, 0.0)


class TrainingSensitivityNoDifferenceControlTests(unittest.TestCase):
    """Negative control: identical training AND held-out populations must
    report exactly zero delta everywhere -- proving the function does not
    spuriously manufacture a difference."""

    def setUp(self):
        train = [
            _event("TOY_ABSENCE", 2020, 1, "AAA", "x1", value=10.0),
            _event("TOY_ABSENCE", 2020, 2, "AAA", "x2", value=20.0),
        ]
        events = train + [_HELD_OUT_EVENT]
        self.variant_a = TrainingSensitivityInputs(
            name="pop_a", events_with_regime=list(events), candidates=[], role_state_rows=[], usage_rows=[],
        )
        self.variant_b = TrainingSensitivityInputs(
            name="pop_b", events_with_regime=list(events), candidates=[], role_state_rows=[], usage_rows=[],
        )

    def test_identical_populations_yield_zero_delta_everywhere(self):
        result = compute_training_sensitivity("toy_share", self.variant_a, self.variant_b, **_COMMON_KWARGS)
        self.assertTrue(result.held_out_populations_identical)
        self.assertEqual(result.max_abs_weight_delta, 0.0)
        self.assertEqual(result.max_abs_mae_delta_any_predictor, 0.0)
        self.assertTrue(result.bit_identical_predictions)
        self.assertEqual(result.n_training_examples["pop_a"], result.n_training_examples["pop_b"])


class TrainingSensitivityHeldOutMismatchTests(unittest.TestCase):
    """If a caller accidentally hands in two variants whose held-out slices
    are NOT actually the same population, that must be surfaced explicitly
    rather than silently producing a misleading MAE delta."""

    def test_mismatched_held_out_population_is_flagged(self):
        train = [_event("TOY_ABSENCE", 2020, 1, "AAA", "x1", value=10.0)]
        variant_a = TrainingSensitivityInputs(
            name="pop_a",
            events_with_regime=train + [_HELD_OUT_EVENT],
            candidates=[], role_state_rows=[], usage_rows=[],
        )
        different_held_out = _event("TOY_ABSENCE", 2021, 1, "AAA", "p2", realized=12.0)
        variant_b = TrainingSensitivityInputs(
            name="pop_b",
            events_with_regime=train + [different_held_out],
            candidates=[], role_state_rows=[], usage_rows=[],
        )
        result = compute_training_sensitivity("toy_share", variant_a, variant_b, **_COMMON_KWARGS)
        self.assertFalse(result.held_out_populations_identical)
        self.assertNotEqual(
            result.held_out_event_digest["pop_a"], result.held_out_event_digest["pop_b"],
        )


class EventPopulationDigestTests(unittest.TestCase):
    def test_digest_is_order_independent(self):
        e1 = _event("TOY_ABSENCE", 2020, 1, "AAA", "x1")
        e2 = _event("TOY_ABSENCE", 2020, 2, "AAA", "x2")
        self.assertEqual(
            event_population_digest([e1, e2]),
            event_population_digest([e2, e1]),
        )

    def test_digest_changes_on_a_real_event_difference(self):
        e1 = _event("TOY_ABSENCE", 2020, 1, "AAA", "x1")
        e2 = _event("TOY_ABSENCE", 2020, 1, "AAA", "x2")
        self.assertNotEqual(event_population_digest([e1]), event_population_digest([e2]))


if __name__ == "__main__":
    unittest.main()
