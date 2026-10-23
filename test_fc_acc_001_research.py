#!/usr/bin/env python3
"""FC-ACC-001 research regression tests (Amendment A1): grade reconciliation,
population boundaries, price semantics, offer-provenance requirements and the
hardened board loader. Research-only code; no production behaviour."""
import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "research", "fc_acc_001"))
import common  # noqa: E402
import offer_provenance as OP  # noqa: E402

PUB, PROV, RECON = common.load_public_reconciled()


class GradeReconciliation(unittest.TestCase):
    def test_public_counts_in_audited_window(self):
        # Window <= 2026-10-11 (final grades): the audited 321/609 + 9 void.
        g = [r["grade"] for r in PUB]
        self.assertEqual(len(PUB), 618)
        self.assertEqual((g.count("hit"), g.count("miss"), g.count("void")), (321, 288, 9))

    def test_history_gap_is_filled_from_results_files_without_conflict(self):
        # Rolling history ages out, so only the invariants are pinned.
        self.assertEqual((RECON["conflict"], RECON["none"]), (0, 0))
        self.assertEqual(sum(RECON.values()), len(PUB))
        self.assertGreaterEqual(RECON["results_only"], 57)
        for r in PUB:
            if r["grade_history"] is not None and r["grade_results"] is not None:
                self.assertEqual(r["grade_history"], r["grade_results"])

    def test_hit_rate_and_roi_at_published_odds(self):
        s = [r for r in PUB if r["grade"] in ("hit", "miss")]
        pnl = sum(common.american_profit(r["odds"]) if r["grade"] == "hit" else -1 for r in s)
        self.assertAlmostEqual(sum(r["grade"] == "hit" for r in s) / len(s), 0.5271, places=4)
        self.assertAlmostEqual(pnl / len(s), -0.1117, places=4)

    def test_conflicting_sources_fail_closed(self):
        real = json.load
        def fake(fh):
            d = real(fh)
            if isinstance(d, dict) and d.get("public_top_picks") and d.get("date") == "2026-09-20":
                d = copy.deepcopy(d)
                p = d["public_top_picks"][0]
                p["grade"] = "miss" if p.get("grade") == "hit" else "hit"
            return d
        common.json.load = fake
        try:
            with self.assertRaises(ValueError):
                common.load_public_reconciled()
        finally:
            common.json.load = real


class PriceSemantics(unittest.TestCase):
    def test_break_even_is_uniform_from_odds(self):
        self.assertAlmostEqual(common.break_even_raw(-150), 0.6)
        self.assertAlmostEqual(common.break_even_raw(150), 0.4)
        self.assertIsNone(common.break_even_raw(None))

    def test_fair_novig_only_for_two_sided(self):
        self.assertEqual(common.fair_novig({"market_implied": 0.52, "market_hold": 0.05}), 0.52)
        self.assertIsNone(common.fair_novig({"market_implied": 0.63, "market_hold": None}))

    def test_stored_market_implied_is_mixed_exactly_as_documented(self):
        boards, _ = common.load_freezes()
        for b, _ in boards:
            for r in b["records"]:
                m = r["market"]
                if m["market_odds"] is None or m["market_implied"] is None:
                    continue
                be = common.break_even_raw(m["market_odds"])
                if m["market_hold"] is None:
                    self.assertAlmostEqual(m["market_implied"], be, delta=0.002)
                else:
                    self.assertLess(m["market_implied"], be + 1e-9)   # de-vigged: below raw break-even


class PopulationBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = json.load(open(os.path.join(HERE, "research/fc_acc_001/out/amendment_a1.json")))
        cls.pop = out["populations"]

    def test_four_populations_are_distinct_and_labelled(self):
        self.assertEqual(self.pop["P1_published_customer_top_picks"]["count"], 618)  # window <= 2026-10-11
        p2 = self.pop["P2_publication_eligible_candidate_top_picks_in_freezes"]
        self.assertEqual((p2["count"], p2["actually_published"], p2["not_published"]), (91, 60, 31))
        self.assertEqual(self.pop["P3_research_board_selections"]["count"], 80)

    def test_shadow_arms_are_not_existing_policy_or_published(self):
        ch1 = self.pop["P4_alternative_policy_shadow_CH1_BLEND"]
        self.assertEqual(ch1["existing_policy_status"].get("lean"), 79)
        self.assertEqual(ch1["price_clears_false"], 62)
        self.assertLessEqual(ch1["published"], 1)

    def test_published_set_is_registry_only(self):
        reg = json.load(open(os.path.join(HERE, "data/public_top_picks/registry.json")))["entries"]
        self.assertEqual({r["id"] for r in PUB},
                         {k for k, e in reg.items() if e["slate_date"] <= common.EVIDENCE_CUTOFF})
        self.assertTrue(all(r["date"] <= common.EVIDENCE_CUTOFF for r in PUB))


class OfferRequirements(unittest.TestCase):
    GOOD = {"sportsbook": "fanduel", "sportsbook_event_id": "36164651", "game_pk": "849820",
            "player_id_or_name": "691406", "market": "hits", "side": "over", "line": 0.5,
            "american_odds": -170, "observation_completed_at": "2026-10-12T19:29:00Z",
            "raw_response_sha256": "a" * 64}

    def test_complete_offer_passes(self):
        self.assertTrue(OP.validate_offer(self.GOOD, decision_cutoff="2026-10-12T20:00:00Z",
                                          selection={"market": "hits", "side": "over", "line": 0.5, "american_odds": -170}))

    def test_each_missing_field_is_rejected(self):
        for f in OP.REQUIRED_OFFER_FIELDS:
            bad = dict(self.GOOD, **{f: None})
            with self.subTest(field=f), self.assertRaises(OP.OfferProvenanceError):
                OP.validate_offer(bad, decision_cutoff="2026-10-12T20:00:00Z")

    def test_offer_after_cutoff_rejected(self):
        with self.assertRaises(OP.OfferProvenanceError):
            OP.validate_offer(self.GOOD, decision_cutoff="2026-10-12T19:00:00Z")

    def test_bad_raw_hash_and_mismatched_selection_rejected(self):
        with self.assertRaises(OP.OfferProvenanceError):
            OP.validate_offer(dict(self.GOOD, raw_response_sha256="xyz"), decision_cutoff="2026-10-12T20:00:00Z")
        with self.assertRaises(OP.OfferProvenanceError):
            OP.validate_offer(self.GOOD, decision_cutoff="2026-10-12T20:00:00Z",
                              selection={"market": "hits", "side": "over", "line": 0.5, "american_odds": -160})

    def test_existing_evidence_yields_no_fully_valid_offer(self):
        out = json.load(open(os.path.join(HERE, "research/fc_acc_001/out/amendment_a1.json")))
        for arm, v in out["offer_feasibility"].items():
            self.assertEqual(v["fully_valid_offers"], 0, arm)   # blocker stays visible until capture exists


class HardenedLoader(unittest.TestCase):
    def test_missing_grade_binding_fails_closed(self):
        import glob
        src = sorted(glob.glob(os.path.join(HERE, "output/board_freeze_graded_*.json")))[0]
        g = json.load(open(src))
        g.pop("source_board_sha256", None)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "output"))
            board = src.replace("board_freeze_graded_", "board_freeze_")
            with open(os.path.join(d, "output", os.path.basename(board)), "w") as fh:
                fh.write(open(board).read())
            with open(os.path.join(d, "output", os.path.basename(src)), "w") as fh:
                json.dump(g, fh)
            old = common.ROOT
            common.ROOT = d
            sys.path.insert(0, HERE)
            try:
                with self.assertRaises(ValueError):
                    common.load_freezes()
            finally:
                common.ROOT = old

    def test_tampered_board_fails_seal(self):
        sys.path.insert(0, HERE)
        from board_freeze import BoardFreezeError, verify_board_seal
        import glob
        b = json.load(open(sorted(glob.glob(os.path.join(HERE, "output/board_freeze_2026-*.json")))[0]))
        b["records"][0]["market"]["market_odds"] = 999
        with self.assertRaises(BoardFreezeError):
            verify_board_seal(b)


class OriginalResultsReproduce(unittest.TestCase):
    def test_preregistered_arm_results_reproduce_in_pinned_window(self):
        import competition as C
        orig = json.load(open(os.path.join(HERE, "research/fc_acc_001/out/competition.json")))
        slates, _, _ = C.build()
        for arm in ("CHAMPION", "CH1_BLEND", "CH2_MARKET", "CH3_CAPPED_MODEL", "CH4_DEPTH"):
            got = C.agg(slates, arm)
            for k in ("selected", "settled", "hits", "units"):
                self.assertEqual(got[k], orig["arms"][arm]["all_slates"][k], (arm, k))


class PreregImmutable(unittest.TestCase):
    def test_prereg_bytes_unchanged(self):
        self.assertEqual(common.sha256_file(os.path.join(HERE, "research/fc_acc_001/PREREG.md")),
                         "e6466a71cdbacb86ce0f10af1cd89e353c20aa8f99ac8c3df59a616d5905dbbb")


if __name__ == "__main__":
    unittest.main()
