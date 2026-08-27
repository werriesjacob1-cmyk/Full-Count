#!/usr/bin/env python3
"""Tests for backtest/equal_volume.py.

Every test here is an attempt to CHEAT the comparison. That is the point:
the framework's value is not that it computes a hit rate, it is that a
selector cannot post a better one by taking fewer picks, changing who was
eligible, seeing outcomes early, or being irreproducible. Each of those is
attempted below and must raise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from backtest import equal_volume as ev


def row(date, game_pk, player_id, prop_type, line, outcome, prob=0.6, score=50.0):
    return {"date": date, "game_pk": game_pk, "player_id": player_id,
            "prop_type": prop_type, "line": line, "outcome": outcome,
            "predicted_prob": prob, "score": score}


def make_population(n=20, **kw):
    rows = []
    for i in range(n):
        rows.append(row(f"2024-05-{(i % 28) + 1:02d}", 100 + (i // 2), 500 + i,
                        "hits", 0.5, i % 2, prob=0.5 + i / 100.0, score=float(i)))
    return ev.EligiblePopulation(
        rows, definition="test population", definition_version="v1",
        evidence_regime="canonical_historical_model_data",
        dataset_identity=kw.pop("dataset_identity",
                                {"artifact_sha256": "a" * 64, "artifact_row_count": n}),
        **kw)


CHAMP = ev.SelectionPolicy("champion_score", "1.0", ev.rank_by(lambda r: r["score"]))
CHAL = ev.SelectionPolicy("challenger_prob", "1.0", ev.rank_by(lambda r: r["predicted_prob"]))


def make_verified_population(n=20):
    """A promotion-grade fixture backed by a REAL Accuracy Lab lock/artifact.

    This is deliberately more work than passing checksum-shaped metadata:
    promotion-grade tests should exercise the same proof path production
    research is required to use.
    """
    import accuracy_lab as al

    base = make_population(n, dataset_identity={})
    tmp = tempfile.mkdtemp(prefix="fc_equal_volume_promotion_")
    rows_path = os.path.join(tmp, "rows.jsonl")
    manifest_path = os.path.join(tmp, "holdout_manifest.json")
    with open(rows_path, "w", encoding="utf-8") as f:
        for r in base.rows:
            f.write(json.dumps(r) + "\n")

    al.lock_holdout(
        rows_path,
        holdout_frac=0.2,
        manifest_path=manifest_path,
        require_strong_dataset_identity=True,
    )
    with open(manifest_path, encoding="utf-8") as f:
        ident = json.load(f)
    ident["manifest_path"] = manifest_path

    population = ev.EligiblePopulation(
        base.rows,
        definition=base.definition,
        definition_version=base.definition_version,
        evidence_regime=base.evidence_regime,
        dataset_identity=ident,
    )
    return population, rows_path, manifest_path


class PopulationIntegrityTests(unittest.TestCase):
    def test_incomplete_candidate_identity_is_rejected(self):
        rows = [row("2024-05-01", 1, 1, "hits", 0.5, 1), row("2024-05-01", None, 2, "hits", 0.5, 0)]
        with self.assertRaises(ev.PopulationIntegrityError):
            ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})

    def test_duplicate_candidate_identity_is_rejected(self):
        r = row("2024-05-01", 1, 1, "hits", 0.5, 1)
        with self.assertRaises(ev.PopulationIntegrityError):
            ev.EligiblePopulation([r, dict(r)], definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})

    def test_population_fingerprints_are_order_independent(self):
        rows = [row("2024-05-01", 1, i, "hits", 0.5, i % 2) for i in range(5)]
        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(list(reversed(rows)), definition="d",
                                  definition_version="v", evidence_regime="r",
                                  dataset_identity={})
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.content_fingerprint, b.content_fingerprint)

    def test_same_candidate_ids_with_changed_selector_content_are_not_the_same_population(self):
        rows = [row("2024-05-01", 1, i, "hits", 0.5, i % 2, score=float(i))
                for i in range(5)]
        changed = [dict(r) for r in rows]
        changed[0]["score"] = 999.0

        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(changed, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})

        self.assertEqual(a.fingerprint, b.fingerprint,
                         "candidate identity keys did not change")
        self.assertNotEqual(a.content_fingerprint, b.content_fingerprint,
                            "score/input drift must not hide behind identity-only equality")

    def test_realized_outcome_is_excluded_from_preselection_content_identity(self):
        rows = [row("2024-05-01", 1, i, "hits", 0.5, i % 2, score=float(i))
                for i in range(5)]
        regraded = [dict(r) for r in rows]
        regraded[0]["outcome"] = 1 - regraded[0]["outcome"]

        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(regraded, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})

        self.assertEqual(a.content_fingerprint, b.content_fingerprint,
                         "the answer used for grading must not enter preselection identity")


class ContentManifestBindingTests(unittest.TestCase):
    def test_experiment_manifest_changes_when_candidate_content_changes(self):
        rows = [row(f"2024-05-{i+1:02d}", 100+i, 500+i, "hits", 0.5,
                    i % 2, prob=0.6, score=float(i)) for i in range(8)]
        changed = [dict(r) for r in rows]
        changed[0]["score"] = 999.0

        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(changed, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        ra = ev.EqualVolumeExperiment(population=a, champion=CHAMP, challenger=CHAL,
                                      volume=3).run()
        rb = ev.EqualVolumeExperiment(population=b, champion=CHAMP, challenger=CHAL,
                                      volume=3).run()

        self.assertNotEqual(
            ra["experiment_manifest_id"], rb["experiment_manifest_id"],
            "an experiment ID must bind candidate content, not only candidate keys")


class ExactVolumeTests(unittest.TestCase):
    def test_both_sides_always_get_exactly_the_requested_volume(self):
        pop = make_population(20)
        rep = ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                       challenger=CHAL, volume=6).run()
        self.assertEqual(rep["champion"]["selected_n"], 6)
        self.assertEqual(rep["challenger"]["selected_n"], 6)

    def test_a_policy_CANNOT_shrink_its_own_volume(self):
        # The classic cheat: return fewer, higher-confidence picks. The
        # policy only supplies ORDER, so this is structurally impossible --
        # a truncated ranking is rejected outright.
        greedy = ev.SelectionPolicy(
            "greedy_few", "1.0",
            lambda pop: sorted(pop.identities, key=ev._identity_sort_key)[:3])
        pop = make_population(20)
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                     challenger=greedy, volume=6).run()
        self.assertIn("ranked 3 of 20", str(cm.exception))

    def test_volume_larger_than_population_is_refused(self):
        pop = make_population(5)
        with self.assertRaises(ev.EqualVolumeViolation):
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                     challenger=CHAL, volume=10)

    def test_zero_or_negative_volume_is_refused(self):
        pop = make_population(10)
        for bad in (0, -1, 2.5):
            with self.assertRaises((ev.EqualVolumeViolation, ValueError)):
                ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                         challenger=CHAL, volume=bad)


class PolicyHonestyTests(unittest.TestCase):
    def test_policy_that_invents_a_candidate_is_rejected(self):
        pop = make_population(10)
        sneaky = ev.SelectionPolicy(
            "sneaky", "1.0",
            lambda p: [("2099-01-01", 1, 1, "hits", 0.5)] + list(p.identities))
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                     challenger=sneaky, volume=3).run()
        self.assertIn("not in the eligible population", str(cm.exception))

    def test_policy_that_duplicates_a_candidate_is_rejected(self):
        pop = make_population(10)
        dupe = ev.SelectionPolicy(
            "dupe", "1.0",
            lambda p: [p.identities[0]] + list(p.identities))
        with self.assertRaises(ev.EqualVolumeViolation):
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                     challenger=dupe, volume=3).run()

    def test_nondeterministic_policy_is_rejected(self):
        import random as _r
        pop = make_population(10)
        flaky = ev.SelectionPolicy(
            "flaky", "1.0",
            lambda p: _r.sample(list(p.identities), len(p.identities)))
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                     challenger=flaky, volume=3).run()
        self.assertIn("not deterministic", str(cm.exception))

    def test_ranking_is_independent_of_incoming_row_order(self):
        rows = [row("2024-05-01", 1, i, "hits", 0.5, i % 2, score=float(i % 4))
                for i in range(12)]
        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(list(reversed(rows)), definition="d",
                                  definition_version="v", evidence_regime="r",
                                  dataset_identity={})
        self.assertEqual(CHAMP.rank(a), CHAMP.rank(b),
                         "tie-breaking must not depend on input order")


class OutcomeHandlingTests(unittest.TestCase):
    def _pop_with_missing(self):
        rows = [row(f"2024-05-{i+1:02d}", 100 + i, 500 + i, "hits", 0.5, i % 2, score=float(i))
                for i in range(10)]
        rows[0]["outcome"] = None  # highest-scoring row is ungraded
        rows[0]["score"] = 999.0
        return ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                     evidence_regime="r", dataset_identity={})

    def test_missing_outcome_raises_under_the_default_required_policy(self):
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=self._pop_with_missing(),
                                     champion=CHAMP, challenger=CHAL, volume=3).run()
        self.assertIn("no recorded outcome", str(cm.exception))

    def test_count_as_miss_is_applied_identically_to_both_sides(self):
        rep = ev.EqualVolumeExperiment(
            population=self._pop_with_missing(), champion=CHAMP, challenger=CHAL,
            volume=3, outcome_policy=ev.OutcomePolicy(ev.OUTCOME_COUNT_AS_MISS)).run()
        self.assertEqual(rep["champion"]["selected_n"], rep["challenger"]["selected_n"])
        self.assertEqual(rep["outcome_policy"]["outcome_mode"], ev.OUTCOME_COUNT_AS_MISS)


class PromotionGradeTests(unittest.TestCase):
    def test_promotion_grade_requires_dataset_identity(self):
        pop = make_population(10, dataset_identity={})
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP, challenger=CHAL,
                                     volume=3, promotion_grade=True)
        self.assertIn("dataset_identity", str(cm.exception))

    def test_checksum_shaped_metadata_without_manifest_is_rejected(self):
        # This is the exact pre-fix weakness: these two plausible-looking
        # fields used to be sufficient for promotion_grade=True.
        pop = make_population(
            10,
            dataset_identity={
                "artifact_sha256": "a" * 64,
                "artifact_row_count": 10,
            },
        )
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP, challenger=CHAL,
                                     volume=3, promotion_grade=True)
        self.assertIn("manifest_path", str(cm.exception))

    def test_promotion_grade_accepts_real_verified_manifest_and_artifact(self):
        pop, _, _ = make_verified_population(10)
        rep = ev.EqualVolumeExperiment(
            population=pop, champion=CHAMP, challenger=CHAL,
            volume=3, promotion_grade=True).run()
        self.assertTrue(rep["promotion_grade"])
        verified = rep["integrity"]["verified_dataset_identity"]
        self.assertTrue(verified["manifest_sha256"])
        self.assertEqual(
            verified["artifact_sha256"],
            pop.dataset_identity["artifact_sha256"],
        )

    def test_promotion_grade_rejects_artifact_changed_after_lock(self):
        pop, rows_path, _ = make_verified_population(10)
        with open(rows_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row(
                "2024-05-28", 9999, 9999, "hits", 0.5, 1, prob=0.9, score=999.0
            )) + "\n")
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(
                population=pop, champion=CHAMP, challenger=CHAL,
                volume=3, promotion_grade=True)
        self.assertIn("no longer matches", str(cm.exception))

    def test_exploratory_mode_does_not_require_strong_identity(self):
        pop = make_population(10, dataset_identity={})
        rep = ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                       challenger=CHAL, volume=3).run()
        self.assertFalse(rep["promotion_grade"])
        self.assertIsNone(rep["integrity"]["verified_dataset_identity"])


class ReportContentTests(unittest.TestCase):
    def setUp(self):
        self.rep = ev.EqualVolumeExperiment(population=make_population(20),
                                            champion=CHAMP, challenger=CHAL,
                                            volume=8).run()

    def test_selection_anatomy_reconciles_exactly(self):
        a, v = self.rep["selection_anatomy"], self.rep["requested_volume"]
        self.assertEqual(a["overlap_n"] + a["added"]["n"], v)
        self.assertEqual(a["overlap_n"] + a["removed"]["n"], v)

    def test_hits_reconcile_between_anatomy_and_totals(self):
        a = self.rep["selection_anatomy"]
        self.assertEqual(a["overlap"]["hits"] + a["added"]["hits"],
                         self.rep["challenger"]["hits"])
        self.assertEqual(a["overlap"]["hits"] + a["removed"]["hits"],
                         self.rep["champion"]["hits"])

    def test_additional_winners_matches_the_hit_difference(self):
        self.assertEqual(self.rep["challenger"]["additional_winners"],
                         self.rep["challenger"]["hits"] - self.rep["champion"]["hits"])

    def test_report_carries_full_population_and_provenance_identity(self):
        p, i = self.rep["population"], self.rep["integrity"]
        for key in ("eligible_population_fingerprint",
                    "eligible_population_content_fingerprint", "evidence_regime",
                    "eligibility_definition_version", "dataset_identity"):
            self.assertIn(key, {**p, **i})
        self.assertTrue(self.rep["experiment_manifest_id"])

    def test_uncertainty_is_cluster_based_not_row_based(self):
        u = self.rep["uncertainty"]
        self.assertEqual(u["cluster_field"], "game_pk")
        self.assertIn("cluster", u["method"])
        self.assertLess(u["n_clusters"], self.rep["requested_volume"] * 2 + 1)

    def test_secondary_diagnostics_are_present_but_labelled_secondary(self):
        s = self.rep["secondary_diagnostics"]
        self.assertIn("SECONDARY", s["champion"]["note"])
        self.assertIn("brier", s["champion"])
        # and realized hit rate is in the primary section, not buried
        self.assertIn("hit_rate", self.rep["champion"])

    def test_formatted_report_leads_with_realized_hit_rate(self):
        text = ev.format_report(self.rep)
        self.assertLess(text.index("REALIZED HIT RATE"), text.index("SECONDARY DIAGNOSTICS"))


class ExperimentKindTests(unittest.TestCase):
    def test_eligibility_experiments_are_labelled_separately(self):
        rep = ev.EqualVolumeExperiment(
            population=make_population(10), champion=CHAMP, challenger=CHAL,
            volume=3, kind=ev.EqualVolumeExperiment.KIND_ELIGIBILITY).run()
        self.assertEqual(rep["experiment_kind"], "eligibility")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            ev.EqualVolumeExperiment(population=make_population(10), champion=CHAMP,
                                     challenger=CHAL, volume=3, kind="whatever")


if __name__ == "__main__":
    unittest.main(verbosity=2)
