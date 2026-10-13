#!/usr/bin/env python3
"""FC-ACC-001-S1 research-only capture: regression tests (synthetic fixtures,
no network). Covers authentic offer binding, source identity, timestamps,
candidate/offer mismatch, feature gaps, eligibility populations, sealing
determinism, idempotent replay, no backfill, and bounded growth."""
import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "research", "fc_acc_001", "s1"))
import features as F  # noqa: E402
import fanduel as FDC  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402
import workload as W  # noqa: E402

V = {"model_version": "test", "selection_policy_version": "1.0.0", "calibration_version": "1.0.0"}
DEC = f"{X.DATE}T21:30:00Z"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.arch = RA.RawArchive(os.path.join(self.tmp.name, "a"))

    def tearDown(self):
        self.tmp.cleanup()

    def cap(self, **kw):
        return FDC.sweep(self.arch, X.make_fetch(**kw), clock=X.make_clock())

    def build(self, cap=None, decision=DEC, games=None, cands=None, snaps=None, mode="DRY_RUN_NONCONFIRMATORY"):
        return M.build(unit={"date": X.DATE, "window": "NIGHT", "decision_at": decision},
                       games=games or X.GAMES, candidates=cands or X.candidates(), capture=cap or self.cap(),
                       archive=self.arch, feature_snapshots=snaps or {}, mode=mode, model_versions=V)

    def row(self, m, cid):
        return next(c for c in m["candidates"] if c["candidate_id"] == cid)


class OfferBinding(Base):
    def test_every_offer_rederives_from_archived_raw_bytes(self):
        cap = self.cap()
        offers = FDC.parse_offers(cap, self.arch)
        self.assertEqual(len(offers), 10)                              # in-play + futures excluded
        for o in offers:
            raw = self.arch.get(o["raw_response_sha256"])
            self.assertEqual(RA.sha256_bytes(raw), o["raw_response_sha256"])
            self.assertTrue(o["observation_completed_at"] >= o["observation_started_at"])
            self.assertEqual(o["sportsbook"], "fanduel")
        self.assertEqual(FDC.parse_offers(cap, self.arch), offers)     # deterministic re-parse

    def test_two_sided_offers_carry_fair_one_sided_do_not(self):
        offers = FDC.parse_offers(self.cap(), self.arch)
        k = [o for o in offers if o["market"] == "strikeouts"]
        self.assertEqual({o["side"] for o in k}, {"over", "under"})
        self.assertTrue(all(o["fair_novig"] is not None and o["fair_novig"] < o["break_even_raw"] for o in k))
        self.assertTrue(all(o["fair_novig"] is None for o in offers if not o["two_sided"]))

    def test_tampered_archive_object_fails(self):
        cap = self.cap()
        sha = cap["root"]["raw_sha256"]
        import gzip
        p = self.arch._path(sha)
        with gzip.open(p, "wb") as fh:
            fh.write(b'{"attachments":{"events":{}}}')
        with self.assertRaises(RA.ArchiveError):
            FDC.parse_offers(cap, self.arch)

    def test_tampered_capture_record_fails(self):
        cap = self.cap()
        cap["events"][0]["requests"][0]["completed_at"] = "2031-05-01T21:00:00Z"
        with self.assertRaises(FDC.CaptureError):
            FDC.verify_capture(cap, self.arch)


class SourceIdentity(Base):
    def test_root_failure_fails_closed(self):
        with self.assertRaises(M.ManifestError):
            self.build(cap=FDC.sweep(self.arch, X.make_fetch(fail_paths=("content-managed-page",)), clock=X.make_clock()))

    def test_conflicting_offers_excluded(self):
        m = self.build(cap=self.cap(overrides={"conflict": True}))
        self.assertEqual(self.row(m, "c7")["offer_status"], "CONFLICTING_OFFERS")
        self.assertIsNone(self.row(m, "c7")["offer"])

    def test_ambiguous_event_mapping_fails_closed_for_that_event(self):
        g = copy.deepcopy(X.GAMES) + [{"game_pk": "900003", "matchup": "Alpha Aces @ Beta Bears",
                                       "first_pitch_utc": f"{X.DATE}T23:20:00Z"}]
        m = self.build(games=g)
        self.assertEqual(m["event_mapping_issues"].get("E1"), "AMBIGUOUS_EVENT")
        self.assertEqual(self.row(m, "c1")["offer_status"], "MISSING_EVENT")

    def test_partial_capture_excludes_event_candidates(self):
        cap = FDC.sweep(self.arch, X.make_fetch(fail_paths=("eventId=E2&tab=lasers",)), clock=X.make_clock())
        self.assertEqual(cap["status"], "INCOMPLETE")
        m = self.build(cap=cap)
        self.assertEqual(self.row(m, "c7")["offer_status"], "INCOMPLETE_CAPTURE")
        self.assertEqual(self.row(m, "c1")["offer_status"], "AUTHENTICATED")

    def test_wrong_sportsbook_identity_rejected(self):
        cap = self.cap()
        cap["sportsbook"] = "other"
        cap["capture_sha256"] = FDC.canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"})
        with self.assertRaises(FDC.CaptureError):
            FDC.verify_capture(cap, self.arch)


class Timestamps(Base):
    def test_capture_after_decision_fails_closed(self):
        with self.assertRaises(M.ManifestError):
            self.build(decision=f"{X.DATE}T21:00:00Z")

    def test_started_games_excluded(self):
        m = self.build(decision=f"{X.DATE}T23:20:00Z")
        self.assertEqual(m["excluded_games"], {"900001": "GAME_STARTED_BEFORE_DECISION"})
        self.assertEqual(self.row(m, "c1")["offer_status"], "GAME_STARTED_BEFORE_DECISION")

    def test_stale_quote_excluded(self):
        m = self.build(decision=f"{X.DATE}T23:00:00Z")
        self.assertEqual(self.row(m, "c7")["offer_status"], "STALE_QUOTE")

    def test_all_games_started_fails_closed(self):
        with self.assertRaises(M.ManifestError):
            self.build(decision=f"{X.DATE}T23:50:00Z")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(M.ManifestError):
            self.build(decision=f"{X.DATE}T21:30:00")


class CandidateOfferMismatch(Base):
    def test_identity_mismatch_is_missing_quote_and_price_diff_flagged(self):
        c = X.candidates()
        c[1]["needs"] = "2"                       # Ted Toolsy 2+ hits not offered
        c[6]["market_side"] = "under"             # no under on one-sided hits
        m = self.build(cands=c)
        self.assertEqual(self.row(m, "c2")["offer_status"], "MISSING_QUOTE")
        self.assertEqual(self.row(m, "c7")["offer_status"], "MISSING_QUOTE")
        self.assertTrue(self.row(m, "c3")["candidate_price_differs"])     # -150 vs offered -140
        self.assertEqual(self.row(m, "c3")["offer"]["american_odds"], -140)  # decision uses the captured offer


class Features(Base):
    def test_missing_required_features_recorded_not_imputed(self):
        s = F.snapshot("c3", "hits", {"lineup_assumed": False, "signals": {}}, None, captured_at=DEC, model_versions=V)
        self.assertEqual(s["status"], "MISSING_REQUIRED")
        self.assertEqual(s["features"]["projected_pa"], F.UNKNOWN)
        self.assertEqual(s["features"]["signals_scaled_0_100"]["lineup_slot"], F.UNKNOWN)
        none = F.snapshot("x", "strikeouts", None, None, captured_at=DEC, model_versions=V)
        self.assertEqual((none["source"], none["status"]), (F.UNKNOWN, "MISSING_REQUIRED"))

    def test_workload_uses_only_prior_starts(self):
        sha, _ = self.arch.put(X.gamelog_bytes())
        w = W.pregame_workload(self.arch, sha, X.DATE)
        self.assertEqual([s["date"] for s in w["last_starts"]], ["2031-04-26", "2031-04-20", "2031-04-14"])
        self.assertEqual(w["last_starts"][0]["outs"], 17)               # 5.2 IP -> 17 outs
        self.assertEqual((w["days_rest"], w["status"]), (5, "COMPLETE"))
        self.assertEqual(w["documented_pitch_limit"], "UNKNOWN_NO_VERIFIED_SOURCE")
        early = W.pregame_workload(self.arch, sha, "2031-04-15")
        self.assertEqual((early["status"], early["starts_available"]), ("PARTIAL", 1))


class Populations(Base):
    def test_existing_and_alternative_eligibility_are_separate(self):
        m = self.build()
        champ = {s["candidate_id"] for s in m["arms"]["CHAMPION_EXISTING_POLICY"]["selections"]}
        self.assertEqual(champ, {"c2", "c3", "c5"})                     # only top_pick with an authenticated offer
        alt = {c["candidate_id"] for c in m["candidates"] if c["alternative_policy_eligible"]}
        self.assertNotIn("c10", alt)                                     # lineup assumed
        self.assertNotIn("c9", alt)                                      # no authenticated quote
        for arm in ("MARKET_ONLY", "BLEND_50_50"):
            self.assertEqual(m["arms"][arm]["policy"], "ALTERNATIVE")
            self.assertEqual(m["arms"][arm]["n_selected"], m["arms"]["CHAMPION_EXISTING_POLICY"]["n_selected"])

    def test_unauthenticated_champion_recorded_and_shortfall_zero(self):
        c = X.candidates()
        c[8]["selector"]["recommendation_status"] = "top_pick"           # c9: top_pick but no quote
        m = self.build(cands=c)
        self.assertEqual(m["accounting"]["champion_unauthenticated"], ["c9"])
        z = [r for r in X.candidates()]
        for r in z:
            r["selector"]["recommendation_status"] = "lean"
        m0 = self.build(cands=z)
        self.assertTrue(all(a["status"] == "ZERO_CHAMPION" and a["n_selected"] == 0 for a in m0["arms"].values()))
        few = [r for r in X.candidates() if r["candidate_id"] in ("c2", "c3", "c5")]
        ms = self.build(cands=few)
        self.assertEqual(ms["arms"]["MARKET_ONLY"]["status"], "MATCHED")
        tiny = [r for r in X.candidates() if r["candidate_id"] in ("c2", "c3", "c5", "c10")]
        for r in tiny:
            if r["candidate_id"] in ("c3", "c5"):
                r["eligibility"]["lineup_assumed"] = True
        mt = self.build(cands=tiny)
        self.assertEqual(mt["arms"]["MARKET_ONLY"]["status"], "SHORTFALL")


class SealingAndReplay(Base):
    def test_deterministic_and_rederivable(self):
        cap = self.cap()
        a, b = self.build(cap=cap), self.build(cap=cap)
        self.assertEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertEqual(M.verify_manifest(a, self.arch), a["manifest_sha256"])
        t = copy.deepcopy(a)
        t["arms"]["BLEND_50_50"]["selections"][0]["american_odds"] = -999
        t["manifest_sha256"] = FDC.canonical_sha256({k: v for k, v in t.items() if k != "manifest_sha256"})
        with self.assertRaises(M.ManifestError):
            M.verify_manifest(t, self.arch)

    def test_idempotent_replay_and_immutability(self):
        led = os.path.join(self.tmp.name, "led")
        m = self.build()
        self.assertEqual(L.seal(led, m, self.arch, ledger_kind="DRY_RUN", now=DEC)[1], "SEALED")
        self.assertEqual(L.seal(led, m, self.arch, ledger_kind="DRY_RUN", now=DEC)[1], "IDEMPOTENT_NOOP")
        other = self.build(cap=self.cap(overrides={"slugger_hit": -260}))
        with self.assertRaises(L.LedgerError):
            L.seal(led, other, self.arch, ledger_kind="DRY_RUN", now=DEC)
        self.assertEqual(L.verify_ledger(led, self.arch), [f"{X.DATE}_NIGHT"])

    def test_no_backfill_and_no_dry_run_in_prospective_ledger(self):
        led = os.path.join(self.tmp.name, "prospective")
        with self.assertRaises(L.LedgerError):
            L.seal(led, self.build(), self.arch, ledger_kind="PROSPECTIVE", now=DEC)
        pm = self.build(mode="PROSPECTIVE")
        with self.assertRaises(L.LedgerError):                           # after first pitch: backfill refused
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", now=f"{X.DATE}T23:15:00Z")
        with self.assertRaises(L.LedgerError):                           # before the decision: refused
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", now=f"{X.DATE}T21:00:00Z")
        self.assertEqual(L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", now=f"{X.DATE}T21:35:00Z")[1], "SEALED")


class BoundedGrowth(Base):
    def test_identical_raw_responses_are_stored_once(self):
        self.cap()
        size1 = self.arch.size_bytes()
        files1 = sum(len(f) for _, _, f in os.walk(self.arch.root))
        self.cap()
        self.assertEqual(self.arch.size_bytes(), size1)
        self.assertEqual(sum(len(f) for _, _, f in os.walk(self.arch.root)), files1)

    def test_manifest_references_raw_by_hash_not_copy(self):
        m = self.build()
        blob = json.dumps(m)
        self.assertNotIn("winRunnerOdds", blob)                          # raw payloads are not duplicated


if __name__ == "__main__":
    unittest.main()
