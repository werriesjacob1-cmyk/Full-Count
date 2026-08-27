#!/usr/bin/env python3
"""Tests for backtest/equal_volume.py.

Every test here is an attempt to CHEAT the comparison. That is the point:
the framework's value is not that it computes a hit rate, it is that a
selector cannot post a better one by taking fewer picks, changing who was
eligible, seeing outcomes early, or being irreproducible. Each of those is
attempted below and must raise.
"""
from __future__ import annotations

import os
import sys
import unittest
import json
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from backtest import equal_volume as ev


def row(date, game_pk, player_id, prop_type, line, outcome, prob=0.6, score=50.0):
    return {"date": date, "game_pk": game_pk, "player_id": player_id,
            "prop_type": prop_type, "line": line, "outcome": outcome,
            "predicted_prob": prob, "score": score}


def strong_dataset_identity(n):
    return {
        "manifest_schema_version": 2,
        "cutoff_date": "2024-05-15",
        "holdout_frac": 0.2,
        "rows_path": "backtest/rows_canonical.jsonl",
        "artifact_sha256": "a" * 64,
        "artifact_row_count": n,
        "artifact_n_distinct_dates": n,
        "artifact_date_range": ["2024-05-01", f"2024-05-{min(n, 28):02d}"],
        "code_git_sha_at_lock": "b" * 40,
    }


def make_population(n=20, **kw):
    rows = []
    for i in range(n):
        rows.append(row(f"2024-05-{(i % 28) + 1:02d}", 100 + (i // 2), 500 + i,
                        "hits", 0.5, i % 2, prob=0.5 + i / 100.0, score=float(i)))
    return ev.EligiblePopulation(
        rows, definition="test population", definition_version="v1",
        evidence_regime="canonical_historical_model_data",
        dataset_identity=kw.pop("dataset_identity", strong_dataset_identity(n)),
        **kw)


CHAMP = ev.SelectionPolicy(
    "champion_score", "1.0", ev.rank_by(lambda r: r["score"]),
    ranking_input_fields=("score",))
CHAL = ev.SelectionPolicy(
    "challenger_prob", "1.0", ev.rank_by(lambda r: r["predicted_prob"]),
    ranking_input_fields=("predicted_prob",))


def verified_population(n=10):
    """Build a population whose dataset identity is backed by a real
    schema-v2 Accuracy Lab manifest and artifact, not a synthetic dict."""
    import accuracy_lab as al
    base = make_population(n)
    tmp = tempfile.mkdtemp(prefix="fc_equal_volume_verified_")
    rows_path = os.path.join(tmp, "rows.jsonl")
    manifest_path = os.path.join(tmp, "holdout_manifest.json")
    with open(rows_path, "w", encoding="utf-8") as fh:
        for r in base.rows:
            fh.write(json.dumps(r) + "\n")
    al.lock_holdout(
        rows_path, holdout_frac=0.2, manifest_path=manifest_path,
        require_strong_dataset_identity=True)
    with open(manifest_path, encoding="utf-8") as fh:
        ident = json.load(fh)
    ident["manifest_path"] = manifest_path
    return ev.EligiblePopulation(
        base.rows, definition=base.definition,
        definition_version=base.definition_version,
        evidence_regime=base.evidence_regime,
        dataset_identity=ident)


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

    def test_population_fingerprint_is_order_independent(self):
        rows = [row("2024-05-01", 1, i, "hits", 0.5, i % 2) for i in range(5)]
        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(list(reversed(rows)), definition="d",
                                  definition_version="v", evidence_regime="r",
                                  dataset_identity={})
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.content_fingerprint, b.content_fingerprint)

    def test_content_fingerprint_changes_when_ranking_content_changes(self):
        rows = [row("2024-05-01", 1, i, "hits", 0.5, i % 2, score=float(i))
                for i in range(5)]
        changed = [dict(r) for r in rows]
        changed[0]["score"] = 999.0
        a = ev.EligiblePopulation(rows, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        b = ev.EligiblePopulation(changed, definition="d", definition_version="v",
                                  evidence_regime="r", dataset_identity={})
        self.assertEqual(a.fingerprint, b.fingerprint,
                         "candidate keys did not change")
        self.assertNotEqual(a.content_fingerprint, b.content_fingerprint,
                            "ranking content drift must not hide behind identity-only equality")


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
    @staticmethod
    def _quota(pop, n=3):
        dates = sorted({r["date"] for r in pop.rows})[:n]
        return {d: 1 for d in dates}

    def test_promotion_grade_requires_strong_dataset_identity(self):
        pop = make_population(10, dataset_identity={})
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP, challenger=CHAL,
                                     volume=3, promotion_grade=True,
                                     volume_by_date=self._quota(pop))
        self.assertIn("dataset_identity", str(cm.exception))

    def test_promotion_grade_rejects_checksum_only_identity(self):
        pop = make_population(10, dataset_identity={
            "manifest_schema_version": 2,
            "artifact_sha256": "a" * 64,
            "artifact_row_count": 10,
        })
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP, challenger=CHAL,
                                     volume=3, promotion_grade=True,
                                     volume_by_date=self._quota(pop))
        self.assertIn("manifest_path", str(cm.exception))

    def test_promotion_grade_rejects_manifest_metadata_if_artifact_drifted(self):
        import accuracy_lab as al
        pop = verified_population(10)
        manifest_path = pop.dataset_identity["manifest_path"]
        rows_path = os.path.join(al.ROOT, pop.dataset_identity["rows_path"])
        with open(rows_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row("2024-05-20", 999, 999, "hits", 0.5, 1)) + "\n")
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(
                population=pop, champion=CHAMP, challenger=CHAL, volume=3,
                promotion_grade=True, volume_by_date=self._quota(pop))
        self.assertIn("no longer matches", str(cm.exception))

    def test_promotion_grade_requires_declared_ranking_inputs(self):
        pop = verified_population(10)
        undeclared = ev.SelectionPolicy(
            "undeclared", "1.0", ev.rank_by(lambda r: r["score"]))
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(
                population=pop, champion=CHAMP, challenger=undeclared, volume=3,
                promotion_grade=True, volume_by_date=self._quota(pop))
        self.assertIn("ranking_input_fields", str(cm.exception))

    def test_promotion_grade_requires_per_date_operational_volume(self):
        pop = verified_population(10)
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(population=pop, champion=CHAMP, challenger=CHAL,
                                     volume=3, promotion_grade=True)
        self.assertIn("volume_by_date", str(cm.exception))

    def test_promotion_grade_accepted_with_strong_identity_and_locked_slates(self):
        pop = verified_population(10)
        quota = self._quota(pop)
        rep = ev.EqualVolumeExperiment(
            population=pop, champion=CHAMP, challenger=CHAL, volume=3,
            promotion_grade=True, volume_by_date=quota).run()
        self.assertTrue(rep["promotion_grade"])
        self.assertEqual(rep["allocation_mode"], "per_date_locked")
        self.assertEqual(rep["requested_volume_by_date"], quota)
        self.assertTrue(rep["integrity"]["same_operational_volume_by_date"])
        self.assertTrue(rep["integrity"]["ranking_input_fingerprints"]["champion"])
        self.assertTrue(rep["integrity"]["ranking_input_fingerprints"]["challenger"])

    def test_exploratory_mode_does_not_require_strong_identity(self):
        pop = make_population(10, dataset_identity={})
        rep = ev.EqualVolumeExperiment(population=pop, champion=CHAMP,
                                       challenger=CHAL, volume=3).run()
        self.assertFalse(rep["promotion_grade"])


class OperationalVolumeTests(unittest.TestCase):
    def test_per_date_quota_is_structural_for_both_policies(self):
        pop = make_population(12)
        quota = {"2024-05-01": 1, "2024-05-02": 1, "2024-05-03": 1}
        rep = ev.EqualVolumeExperiment(
            population=pop, champion=CHAMP, challenger=CHAL, volume=3,
            volume_by_date=quota).run()
        self.assertEqual(rep["requested_volume_by_date"], quota)
        # The report is sufficient to prove the locked allocation was active;
        # _select() itself raises if either policy cannot fill a quota.
        self.assertEqual(rep["allocation_mode"], "per_date_locked")

    def test_per_date_quota_sum_must_equal_requested_volume(self):
        pop = make_population(10)
        with self.assertRaises(ev.EqualVolumeViolation):
            ev.EqualVolumeExperiment(
                population=pop, champion=CHAMP, challenger=CHAL, volume=3,
                volume_by_date={"2024-05-01": 1, "2024-05-02": 1})


class PairwiseOutcomeIntegrityTests(unittest.TestCase):
    def test_asymmetric_missing_outcome_fails_closed(self):
        rows = [
            row("2024-05-01", 1, 1, "hits", 0.5, None, score=100.0, prob=0.1),
            row("2024-05-02", 2, 2, "hits", 0.5, 1, score=90.0, prob=0.9),
            row("2024-05-03", 3, 3, "hits", 0.5, 0, score=80.0, prob=0.8),
            row("2024-05-04", 4, 4, "hits", 0.5, 1, score=70.0, prob=0.7),
        ]
        pop = ev.EligiblePopulation(
            rows, definition="d", definition_version="v", evidence_regime="r",
            dataset_identity={})
        with self.assertRaises(ev.EqualVolumeViolation) as cm:
            ev.EqualVolumeExperiment(
                population=pop, champion=CHAMP, challenger=CHAL, volume=2,
                outcome_policy=ev.OutcomePolicy(ev.OUTCOME_EXCLUDE_PAIRWISE)).run()
        self.assertIn("unequal post-outcome denominators", str(cm.exception))


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
        for key in ("eligible_population_fingerprint", "evidence_regime",
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
