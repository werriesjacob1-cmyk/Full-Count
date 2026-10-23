#!/usr/bin/env python3
"""FC-ACC-016 Amendment A1 validation tests (synthetic + the committed outcome-free feasibility record)."""
import copy
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, V1)
sys.path.insert(0, os.path.dirname(os.path.dirname(V1)))
import a1  # noqa: E402  (verifies frozen v1 files on import)
import checker as C  # noqa: E402
import synthetic as S  # noqa: E402

import numpy as np  # noqa: E402


def _bundles(case, n=20, seed=3):
    u = S.cases()[case][0]
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        d = f"2027-07-{i + 1:02d}"
        uu = copy.deepcopy(u)
        uu["unit_id"], uu["slate_date"] = f"U{i}", d
        for k in ("prepared_at", "decision_cutoff_at", "sealed_at"):
            uu[k] = d + uu[k][10:]
        for g in uu["games"]:
            g["first_pitch_at"] = d + g["first_pitch_at"][10:]
        for r in uu["rows"]:
            if r.get("offer"):
                r["offer"]["observed_at"] = d + r["offer"]["observed_at"][10:]
            if r.get("challenger"):
                r["challenger"]["features_built_at"] = d + r["challenger"]["features_built_at"][10:]
                r["challenger"]["features_data_through"] = "2027-06-30" if i == 0 else f"2027-07-{i:02d}"
        out.append({"synthetic": True, "units": [uu],
                    "outcomes": {r["canonical_id"]: {"status": "WIN" if rng.random() < 0.5 else "LOSS"} for r in uu["rows"]}})
    return out


class AmendmentA1(unittest.TestCase):
    def test_frozen_v1_files_unchanged(self):
        for name, sha in a1.A1["frozen_v1_files_sha256"].items():
            with open(os.path.join(V1, name), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), sha, name)

    def test_v1_suite_still_passes(self):
        self.assertTrue(S.run_suite()["all_pass"])

    def test_experiment_bindings_cannot_be_mixed(self):
        self.assertEqual(a1.EXPERIMENTS, {"E1": ("TP", "hits"), "E2": ("V", "total_bases")})
        with self.assertRaises(ValueError):
            a1.evaluate_experiment("E3", [])
        with self.assertRaises(ValueError):
            a1.evaluate_experiment("strikeouts", [])

    def test_top_pick_floor_unchanged_and_not_relaxed_for_tb(self):
        self.assertEqual(C.PROTOCOL["lanes"]["TP"]["probability_floor_min"], 0.60)
        models = C.load_frozen_models()
        u, lane, mk, _ = S.cases()["tb_below_top_pick_floor_cannot_enter_tp"]
        self.assertEqual(C.select_unit(u, lane, mk, models)["added"], [])

    def test_e2_tb_without_production_interval_fails_closed(self):
        models = C.load_frozen_models()
        u = copy.deepcopy(S.cases()["lane_v_tb_enters_on_pessimistic_roi"][0])
        for r in u["rows"]:
            if r["market"] == "total_bases":
                r["prob_ci"] = None                     # what production actually emits for TB 2+
        s = C.select_unit(u, "V", "total_bases", models)
        self.assertEqual(s["added"], [])
        self.assertEqual(s["B"], s["A"])
        self.assertIn("FAILS_PROBABILITY_OR_PRICE_GATE", s["challenger_exclusions"])

    def test_hit_rate_denominator_refuses_value_lane(self):
        e1 = a1.evaluate_experiment("E1", _bundles("hits_secondary_regoverns_hits_rows"))
        self.assertEqual((e1["lane"], e1["market"]), ("TP", "hits"))
        self.assertIsNotNone(a1.top_pick_hit_rate(e1)["A"])
        e2 = a1.evaluate_experiment("E2", _bundles("lane_v_tb_enters_on_pessimistic_roi"))
        self.assertEqual((e2["lane"], e2["market"]), ("V", "total_bases"))
        with self.assertRaises(ValueError):
            a1.top_pick_hit_rate(e2)

    def test_e2_roi_verdict_rule(self):
        base = {"noncomparable_share": 0.0, "volume_share": 1.0, "discordant": {"D_settled": 400},
                "challenger_calibration": {"citl": 0.0, "slope": 1.0}, "ci_delta_roi_slate": [0.01, 0.2]}
        self.assertEqual(a1.roi_verdict_label(base), "SUPPORTED_PENDING_AUTHORIZATION")
        self.assertEqual(a1.roi_verdict_label({**base, "ci_delta_roi_slate": [-0.01, 0.2]}), "NOT_SUPPORTED")
        self.assertEqual(a1.roi_verdict_label({**base, "discordant": {"D_settled": 150}}),
                         "NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE")
        self.assertEqual(a1.roi_verdict_label({**base, "volume_share": 0.9}), "NOT_EVALUABLE_VOLUME_SHORTFALL")

    def test_e2_structural_gate_on_committed_feasibility(self):
        with open(os.path.join(HERE, "out", "feasibility.json")) as fh:
            feas = json.load(fh)
        self.assertFalse(feas["outcomes_read"])
        g = a1.e2_structural_gate(feas)
        self.assertEqual(g["status"], "NOT_EVALUABLE_STRUCTURAL")
        self.assertIn("TB2_PRODUCTION_INTERVAL_ABSENT", g["reasons"])
        self.assertEqual(g["tb2_rows_with_production_interval"], 0)
        # a hypothetical feasibility record where production supplied intervals and eligibility existed would pass
        ok = copy.deepcopy(feas)
        for s in ok["snapshots"].values():
            e = s["E2_tb2_value_lane"]
            e["champion_interval_present"] = e["rows"]
            e["challenger_scored_priced"] = max(1, e.get("challenger_scored_priced", 0))
            e["challenger_passes_value_gate_actual_production_interval"] = 3
        self.assertEqual(a1.e2_structural_gate(ok)["status"], "EVALUABLE")


if __name__ == "__main__":
    unittest.main()
