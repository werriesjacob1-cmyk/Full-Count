#!/usr/bin/env python3
"""Scorecard + final-result persistence tests. SYNTHETIC FIXTURES ONLY (no real unit, no real outcome)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import evaluate_v3 as EV  # noqa: E402
import harness as H  # noqa: E402
import scorecard_v3 as SC  # noqa: E402
import test_v3 as T3  # noqa: E402

FINAL = {"codedGameState": "F", "detailedState": "Final"}


def hit_rule(cid):
    """Synthetic outcomes: even index -> hit, odd -> miss; index 7 is unresolved (absent from the box score)."""
    i = int(cid.rsplit("-", 1)[1])
    return "ungraded" if i == 7 else ("hit" if i % 2 == 0 else "miss")


class Scorecard(unittest.TestCase):
    def run_eval(self, dates, regime, now, gtype="R"):
        root = T3.new_root()
        self.addCleanup(shutil.rmtree, root, True)
        seals = [T3.make_unit(root, date=d, gtype=gtype)[0] for d in dates]
        ext = T3.Externals()
        graded_after_lock = []

        def grade(board):
            graded_after_lock.append(os.path.exists(os.path.join(root, f"FINAL_ANALYSIS_{regime}.json")))
            return {"source_board_sha256": board["board_sha256"],
                    "records": [{"candidate_id": r["candidate_id"], "grade": hit_rule(r["candidate_id"])}
                                for r in board["records"]]}

        def lock(r, rec):
            json.dump(rec, open(os.path.join(r, f"FINAL_ANALYSIS_{rec['regime']}.json"), "w"))

        outs = []
        orig_verify = EV.VE.verify_unit

        def verify_unit(r, s):
            ext.current_seal = s
            return orig_verify(r, s)
        p1, p2 = ext.patch(seals[0])
        with p1, p2, mock.patch.multiple(EV, utc_now=lambda: now, publish_lock=lock, grade_shadow_board=grade,
                                         fetch_game_states=lambda gps: {str(g): FINAL for g in gps}), \
                mock.patch.object(EV.VE, "verify_unit", verify_unit):
            outs.append(EV.evaluate_from_evidence(root, regime))
        self.assertTrue(graded_after_lock and all(graded_after_lock), "outcomes opened before the one-look lock")
        return outs[0], root

    def test_confirmatory_scorecard_numbers_are_exact(self):
        out, _ = self.run_eval(["2027-09-20", "2027-09-21"], "2027_REGULAR_CONFIRMATORY",
                               datetime(2027, 10, 20, tzinfo=timezone.utc))
        sc = out["scorecard"]
        self.assertEqual((sc["evidence_label"], sc["confirmatory"]), ("CONFIRMATORY", True))
        self.assertTrue(sc["promotion_statistic"].startswith("ELIGIBLE"))
        champ_ids = [i for a in sc["equal_volume_audit"] for i in a["champion_ids"]]
        v3_ids = [i for a in sc["equal_volume_audit"] for i in a["v3_ids"]]
        for a in sc["equal_volume_audit"]:                         # equal volume N_d per slate
            self.assertEqual((a["n_d"], a["champion"], a["v3"]), (2, 2, 2))
            self.assertEqual(a["champion_ids"], sorted(f"{a['unit'][:10]}-{i}" for i in (0, 1)))   # sealed top picks
        g = {c: hit_rule(c) for c in champ_ids + v3_ids}
        hits = lambda ids: sum(g[i] == "hit" for i in ids)
        settled = lambda ids: sum(g[i] in ("hit", "miss") for i in ids)
        o = sc["overall"]
        self.assertEqual((o["champion"]["selected"], o["champion"]["hits"], o["champion"]["settled"]),
                         (4, hits(champ_ids), settled(champ_ids)))
        self.assertEqual((o["v3"]["selected"], o["v3"]["hits"], o["v3"]["settled"]), (4, hits(v3_ids), settled(v3_ids)))
        self.assertEqual(o["v3"]["unresolved"], 4 - settled(v3_ids))      # excluded by status, reported
        self.assertAlmostEqual(o["delta_pp"], (hits(v3_ids) / settled(v3_ids) - hits(champ_ids) / settled(champ_ids)) * 100)
        m = sc["selection_mechanics"]
        self.assertEqual(m["overlap"], len(set(champ_ids) & set(v3_ids)))
        self.assertEqual(m["champion_only"]["selected"] + m["overlap"], 4)
        self.assertEqual(m["v3_only"]["selected"] + m["overlap"], 4)
        # the scorecard's selection is exactly the evaluator's (same frozen machinery)
        self.assertEqual(o["v3"]["selected"], out["picking"][H.PRIMARY]["counts"]["selected"])
        self.assertEqual(o["champion"]["hits"], out["picking"]["SHADOW_CHAMPION"].get("wins", 0))
        cum = sc["cumulative"]
        self.assertEqual([c["through"] for c in cum], ["2027-09-20", "2027-09-21"])
        self.assertEqual((cum[0]["champion_selected"], cum[-1]["champion_selected"]), (2, 4))
        self.assertEqual(cum[-1]["delta_pp"], o["delta_pp"])
        self.assertEqual(set(sc["breakdowns"]["season_phase"]), {"LATE (Aug-Oct)"})
        self.assertEqual(set(sc["breakdowns"]["market_family"]), {"hits"})
        self.assertIn("ci95", o["paired_ci95_pp"]["game"])
        md = SC.render_markdown(sc)
        self.assertIn("**CONFIRMATORY**", md)

    def test_postseason_is_descriptive_and_quarantined_units_never_scored(self):
        out, _ = self.run_eval(["2026-10-03", "2026-10-07"], "2026_POSTSEASON_SHADOW",
                               datetime(2026, 11, 20, tzinfo=timezone.utc), gtype="D")
        self.assertTrue(out["invalid_slates"]["2026-10-03/DAY"].startswith("QUARANTINED_DESCRIPTIVE_SHADOW"))
        sc = out["scorecard"]
        self.assertEqual((sc["evidence_label"], sc["confirmatory"]), ("DESCRIPTIVE SHADOW", False))
        self.assertTrue(sc["promotion_statistic"].startswith("NOT A PROMOTION STATISTIC"))
        self.assertEqual([a["unit"] for a in sc["equal_volume_audit"]], ["2026-10-07/DAY"])
        self.assertEqual(set(sc["breakdowns"]["season_phase"]), {"DIVISION_SERIES"})
        self.assertEqual(out["primary_verdict"], "NOT_APPLICABLE_DESCRIPTIVE_REGIME")

    def test_regimes_are_never_mixed(self):
        _, m27, _ = T3.make_unit(T3.new_root(), date="2027-05-04")
        _, m26, _ = T3.make_unit(T3.new_root(), date="2026-10-07", gtype="D")
        pairs = [(m, {"source_board_sha256": m["shadow_board_sha256"], "records": []}) for m in (m27, m26)]
        with self.assertRaises(ValueError):
            SC.build_scorecard(pairs, H.load_coefficients(), "2027_REGULAR_CONFIRMATORY")
        with self.assertRaises(ValueError):
            SC.build_scorecard(pairs, H.load_coefficients(), "2026_POSTSEASON_SHADOW")

    def test_no_settled_picks_gives_no_interval_not_a_guess(self):
        _, m, _ = T3.make_unit(T3.new_root(), date="2027-05-04")
        sc = SC.build_scorecard([(m, {"source_board_sha256": m["shadow_board_sha256"], "records": []})],
                                H.load_coefficients(), "2027_REGULAR_CONFIRMATORY")
        self.assertIsNone(sc["overall"]["delta_pp"])
        self.assertIsNone(sc["overall"]["paired_ci95_pp"]["game"]["ci95"])
        self.assertEqual(sc["overall"]["champion"]["unresolved"], 2)

    def test_result_is_persisted_with_graded_hashes_and_never_rewritten(self):
        out, root = self.run_eval(["2027-09-20"], "2027_REGULAR_CONFIRMATORY", datetime(2027, 10, 20, tzinfo=timezone.utc))
        d = tempfile.mkdtemp()
        paths = EV.write_result(out, "2027_REGULAR_CONFIRMATORY", d)
        res = json.load(open(os.path.join(d, "FINAL_RESULT_2027_REGULAR_CONFIRMATORY.json")))
        self.assertNotIn("_graded", res)
        g = open(os.path.join(d, "GRADED", "2027-09-20_DAY.json"), "rb").read()
        self.assertEqual(res["graded_sha256"]["2027-09-20/DAY"], __import__("hashlib").sha256(g).hexdigest())
        self.assertIn("CONFIRMATORY", open(os.path.join(d, "SCORECARD_2027_REGULAR_CONFIRMATORY.md")).read())
        with mock.patch.object(EV.subprocess, "run") as git:
            EV.publish_result(root, paths)
            self.assertEqual([c.args[0][3] for c in git.call_args_list], ["add", "commit", "push"])
            with self.assertRaises(FileExistsError):
                EV.publish_result(root, paths)


if __name__ == "__main__":
    unittest.main()
