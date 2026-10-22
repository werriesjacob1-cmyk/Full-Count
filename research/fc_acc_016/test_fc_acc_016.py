#!/usr/bin/env python3
"""FC-ACC-016 protocol checker tests (synthetic only, no network, no outcomes of any real game)."""
import copy
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import checker as C  # noqa: E402
import synthetic as S  # noqa: E402


def _shift_unit(u, i):
    d = f"2027-06-{i + 1:02d}"
    u = copy.deepcopy(u)
    u["unit_id"], u["slate_date"] = f"U{i}", d
    for k in ("prepared_at", "decision_cutoff_at", "sealed_at"):
        u[k] = d + u[k][10:]
    for g in u["games"]:
        g["first_pitch_at"] = d + g["first_pitch_at"][10:]
    for r in u["rows"]:
        if r.get("offer"):
            r["offer"]["observed_at"] = d + r["offer"]["observed_at"][10:]
        if r.get("challenger"):
            r["challenger"]["features_built_at"] = d + r["challenger"]["features_built_at"][10:]
            r["challenger"]["features_data_through"] = "2027-05-31" if i == 0 else f"2027-06-{i:02d}"
    return u


class Protocol(unittest.TestCase):
    def test_labeled_synthetic_suite(self):
        rep = S.run_suite()
        failed = [k for k, v in rep["cases"].items() if not v["pass"]]
        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(rep["cases"]), 19)

    def test_model_lock_detects_tampering(self):
        saved = copy.deepcopy(C.LOCK)
        try:
            C.LOCK["entries"]["total_bases"]["coef_sha256"] = "0" * 64
            with self.assertRaises(RuntimeError):
                C.load_frozen_models()
        finally:
            C.LOCK.clear()
            C.LOCK.update(saved)
        self.assertIn("total_bases", C.load_frozen_models())

    def test_gate_code_and_constants_pinned(self):
        self.assertTrue(C.verify_gate_code())
        import recommendation as rec
        import prop_probability as pp
        tp = C.PROTOCOL["lanes"]["TP"]
        self.assertEqual(tp["probability_floor_min"], rec.TOP_PICK_MIN_PROB)
        self.assertEqual(tuple(tp["min_reliability"]), tuple(rec.TOP_PICK_MIN_RELIABILITY))
        self.assertEqual(C.G["min_roi"], rec.TOP_PICK_MIN_ROI)
        self.assertEqual(C.G["min_roi"], pp.MIN_ROI)
        self.assertEqual(C.G["max_offer_age_seconds"], rec.MAX_PRICE_AGE_SECONDS)
        self.assertEqual(C.G["max_board_age_seconds"], rec.MAX_BOARD_AGE_SECONDS)

    def test_frozen_transform_matches_artifact_algebra(self):
        m = C.load_frozen_models()
        for mk in ("total_bases", "hits"):
            for target in (0.2, 0.45, 0.62):
                p, st = C.challenger_probability(m[mk], S.features_for(m[mk], target))
                self.assertEqual(st, "OK")
                self.assertAlmostEqual(p, target, places=9)

    def test_insufficient_discordance_is_not_a_negative_result(self):
        u = S.cases()["qualifying_tb_displaces_weakest_champion"][0]
        rng = np.random.default_rng(9)
        bundles = []
        for i in range(20):
            uu = _shift_unit(u, i)
            bundles.append({"synthetic": True, "units": [uu],
                            "outcomes": {r["canonical_id"]: {"status": "WIN" if rng.random() < 0.6 else "LOSS"}
                                         for r in uu["rows"]}})
        res = C.evaluate(bundles, "TP", "total_bases")
        self.assertEqual(res["noncomparable_units"], 0)
        self.assertEqual(res["requested_N"], res["realized_B"])
        self.assertEqual(res["discordant"]["D_settled"], 40)
        self.assertEqual(res["verdict_label"], "NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE")

    def test_unresolved_outcomes_hold_the_unit_pending(self):
        u = _shift_unit(S.cases()["qualifying_tb_displaces_weakest_champion"][0], 0)
        res = C.evaluate([{"synthetic": True, "units": [u], "outcomes": {"h1": {"status": "WIN"}}}], "TP", "total_bases")
        self.assertEqual(res["pending_units"], 1)
        self.assertEqual(res["slates_settled"], 0)

    def test_serving_skew_flags_missingness_shift(self):
        m = C.load_frozen_models()["total_bases"]
        f = S.features_for(m, 0.4)
        rows = [dict(f) for _ in range(40)]
        for r in rows:
            r["skill_prev_tb"] = None          # forward missing rate 100% vs a low training rate
        flagged = {x["feature"] for x in C.serving_skew(m, rows)["flags"] if x["check"] == "missing_rate"}
        self.assertIn("skill_prev_tb", flagged)


if __name__ == "__main__":
    unittest.main()
