#!/usr/bin/env python3
"""FC-ACC-001-S1 research-only capture: regression tests (synthetic fixtures,
no network). Covers the five Codex attacks (PR #226 comment 6154204021) and
their variants, exact source lineage, two-sided integrity, point-in-time
provenance, equal-volume integrity, ledger durability/concurrency/recovery,
external-anchor verification, determinism and bounded growth."""
import copy
import json
import multiprocessing
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
S1 = os.path.join(HERE, "research", "fc_acc_001", "s1")
sys.path.insert(0, S1)
sys.path.insert(0, os.path.join(S1, "attacks"))
import anchor as AN  # noqa: E402
import fanduel as FDC  # noqa: E402
import features as F  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import post_repair_attacks as ATT  # noqa: E402
import raw_archive as RA  # noqa: E402
import workload as W  # noqa: E402

LATE = f"{X.DATE}T21:35:00Z"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.arch = RA.RawArchive(os.path.join(self.tmp.name, "a"))

    def tearDown(self):
        self.tmp.cleanup()

    def cap(self, **kw):
        return X.capture(self.arch, **kw)

    def build(self, cap=None, recs=None, snaps=None, mode="DRY_RUN_NONCONFIRMATORY", unit=None, sched=None, **kw):
        return M.build(unit=unit or X.UNIT, board=kw.pop("board", None) or X.board(recs),
                       schedule_source=sched or X.schedule_source(self.arch, **kw), capture=cap or self.cap(),
                       archive=self.arch, feature_snapshots=snaps or {}, mode=mode)

    def row(self, m, cid):
        return next(c for c in m["candidates"] if c["candidate_id"] == cid)


class CodexAttacks(unittest.TestCase):
    def test_every_attack_is_rejected(self):
        res = ATT.run()
        self.assertGreaterEqual(len(res), 19)
        bad = {k: v for k, v in res.items() if not v.startswith("REJECTED")}
        self.assertEqual(bad, {})


class SourceLineage(Base):
    def test_offers_rederive_from_archived_bytes_and_bind_lineage(self):
        cap = self.cap()
        offers = FDC.parse_offers(cap, self.arch)
        self.assertEqual(len(offers), 10)                                   # in-play + futures excluded
        for o in offers:
            self.assertEqual(RA.sha256_bytes(self.arch.get(o["raw_response_sha256"])), o["raw_response_sha256"])
            self.assertIn(f"eventId={o['sportsbook_event_id']}&", o["request_path"])
            self.assertTrue(o["availability_verified"])
        self.assertEqual(FDC.parse_offers(cap, self.arch), offers)

    def test_tampered_archive_object_and_record_fail(self):
        import gzip
        cap = self.cap()
        with gzip.open(self.arch._path(cap["root"]["raw_sha256"]), "wb") as fh:
            fh.write(b'{"attachments":{"events":{}}}')
        with self.assertRaises(RA.ArchiveError):
            FDC.parse_offers(cap, self.arch)
        cap2 = X.capture(RA.RawArchive(os.path.join(self.tmp.name, "b")))
        cap2["events"][0]["requests"][0]["completed_at"] = "2031-05-01T21:00:00Z"
        with self.assertRaises(FDC.CaptureError):
            FDC.verify_capture(cap2, self.arch)

    def test_wrong_sportsbook_and_status_relabel_rejected(self):
        for mutate in (lambda c: c.update(sportsbook="other"), lambda c: c.update(status="COMPLETE") or
                       c["events"][1].update(status="COMPLETE")):
            cap = FDC.sweep(self.arch, X.make_fetch(fail_paths=("eventId=E2&tab=lasers",)), clock=X.make_clock())
            mutate(cap)
            with self.assertRaises(FDC.CaptureError):
                FDC.verify_capture(ATT._rehash(cap), self.arch)

    def test_root_failure_fails_closed(self):
        cap = FDC.sweep(self.arch, X.make_fetch(fail_paths=("content-managed-page",)), clock=X.make_clock())
        with self.assertRaises(M.ManifestError):
            self.build(cap=cap)

    def test_absent_status_fields_are_unverified_not_open(self):
        m = self.build(cap=self.cap(overrides={"no_status": True}))
        self.assertFalse(self.row(m, "c2")["offer"]["availability_verified"])
        self.assertIn("SPORTSBOOK_AVAILABILITY_SCHEMA_UNMEASURED", m["certification"]["blockers"])

    def test_conflicting_prices_excluded(self):
        m = self.build(cap=self.cap(overrides={"conflict": True}))
        self.assertEqual(self.row(m, "c7")["offer_status"], "CONFLICTING_OFFERS")

    def test_budget_is_checked_between_tabs(self):
        ticks = iter([0, 0, 0, 0, 0, 1000] + [1000] * 50)
        cap = FDC.sweep(self.arch, X.make_fetch(), clock=X.make_clock(), monotonic=lambda: next(ticks), budget_s=10)
        statuses = [r["status"] for r in cap["events"][0]["requests"]]
        self.assertEqual(statuses[:3], ["OK"] * 3)
        self.assertIn(FDC.NOT_FETCHED, statuses)
        self.assertEqual((cap["events"][0]["status"], cap["status"]), ("PARTIAL", "INCOMPLETE"))
        FDC.verify_capture(cap, self.arch)


class PointInTime(Base):
    def test_capture_after_decision_and_naive_time_fail_closed(self):
        with self.assertRaises(M.ManifestError):
            self.build(unit=dict(X.UNIT, decision_at=f"{X.DATE}T21:00:00Z"))
        with self.assertRaises(M.ManifestError):
            self.build(unit=dict(X.UNIT, decision_at=f"{X.DATE}T21:30:00"))

    def test_started_or_non_pregame_games_excluded(self):
        m = self.build(state={"900001": "Live"})
        self.assertEqual(m["excluded_games"], {"900001": "GAME_NOT_PREGAME_IN_RAW"})
        self.assertEqual(self.row(m, "c1")["offer_status"], "GAME_NOT_PREGAME_IN_RAW")
        with self.assertRaises(M.ManifestError):
            self.build(state={"900001": "Live", "900002": "Final"})

    def test_lineup_must_be_confirmed_in_raw(self):
        m = self.build(lineups={"900001": ([], []), "900002": X.LINEUPS["900002"]})
        self.assertEqual(self.row(m, "c2")["offer_status"], "LINEUP_NOT_CONFIRMED_IN_RAW")
        self.assertEqual(self.row(m, "c5")["offer_status"], "AUTHENTICATED")          # pitcher: probable starter

    def test_starter_must_be_probable_in_raw(self):
        m = self.build(starters={"900001": ("19", "77")})
        self.assertEqual(self.row(m, "c5")["offer_status"], "STARTER_NOT_PROBABLE_IN_RAW")

    def test_board_must_match_schedule_and_be_sealed_pregame(self):
        m = self.build(first_pitch={"900001": f"{X.DATE}T23:20:00Z"})
        self.assertEqual(m["game_issues"], {"900001": "BOARD_SCHEDULE_MISMATCH"})
        self.assertEqual(m["unit_status"], "NON_COMPARABLE")
        with self.assertRaises(M.ManifestError):                       # board sealed after the decision
            self.build(board=X.board(sealed_at=f"{X.DATE}T21:40:00Z"))
        b = X.board(); b["records"][0]["prediction"]["hit_probability"] = 0.99
        with self.assertRaises(M.ManifestError):                       # edited probability: seal breaks
            self.build(board=b)

    def test_workload_prior_starts_only_and_bound_to_observation(self):
        wl = W.pregame_workload(self.arch, X.gamelog_source(self.arch), "9", X.DATE, decision_at=X.DECISION)
        self.assertEqual([s["date"] for s in wl["last_starts"]], ["2031-04-26", "2031-04-20", "2031-04-14"])
        self.assertEqual((wl["last_starts"][0]["outs"], wl["days_rest"], wl["status"]), (17, 5, "COMPLETE"))
        self.assertEqual(wl["documented_pitch_limit"], "UNKNOWN_NO_VERIFIED_SOURCE")
        with self.assertRaises(W.WorkloadError):                       # wrong player for the archived request
            W.pregame_workload(self.arch, X.gamelog_source(self.arch), "10", X.DATE, decision_at=X.DECISION)
        s = {"c5": F.snapshot("c5", "strikeouts", {"signals": {}}, wl, captured_at=X.DECISION,
                              model_versions=X.VERSIONS)}
        m = self.build(snaps=s)
        self.assertEqual(m["accounting"]["feature_snapshots_admitted"], {"c5": "COMPLETE"})

    def test_missing_features_recorded_not_imputed(self):
        s = F.snapshot("c3", "hits", {"lineup_assumed": False, "signals": {}}, None, captured_at=X.DECISION,
                       model_versions=X.VERSIONS)
        self.assertEqual((s["status"], s["features"]["projected_pa"]), ("MISSING_REQUIRED", F.UNKNOWN))
        self.assertEqual(s["model_values_provenance"], "UNVERIFIED_PRODUCTION_VALUES")
        with self.assertRaises(Exception):
            F.snapshot("x", "hits", None, None, captured_at=None, model_versions=X.VERSIONS)


class EqualVolume(Base):
    def test_clean_unit_is_comparable_and_matched(self):
        m = self.build()
        self.assertEqual(m["unit_status"], "COMPARABLE")
        self.assertEqual([s["candidate_id"] for s in m["arms"]["CHAMPION_EXISTING_POLICY"]["selections"]],
                         ["c2", "c3", "c5"])
        self.assertTrue(all(a["status"] == "MATCHED" and a["n_target"] == 3 for a in m["arms"].values()))
        self.assertTrue(all(a["publication_eligible"] is False for a in m["arms"].values()))
        self.assertFalse(m["certification"]["certified_prospective"])

    def test_out_of_scope_market_counts_against_champion_volume(self):
        recs = X.records()
        game = copy.deepcopy(recs[1]); game.update(candidate_id="c11", stat="nrfi_combined", player_id="nrfi_900001",
                                                   needs="0.5", line=50.1, player_name="NRFI")
        recs.append(game)
        m = self.build(recs=recs)
        self.assertEqual(self.row(m, "c11")["offer_status"], "MARKET_NOT_IN_S1_SCOPE")
        self.assertEqual((m["arms"][ARMS0]["n_target"], m["unit_status"]), (4, "NON_COMPARABLE"))

    def test_eligibility_populations_are_separate(self):
        m = self.build()
        alt = {c["candidate_id"] for c in m["candidates"] if c["alternative_policy_eligible"]}
        self.assertNotIn("c10", alt)                                    # lineup assumed holdout
        self.assertNotIn("c9", alt)                                     # no authenticated quote
        self.assertTrue(all(a["policy"] == "ALTERNATIVE" for k, a in m["arms"].items() if k != ARMS0))

    def test_zero_champion_slate_recorded(self):
        recs = X.records()
        for r in recs:
            r["selector"]["recommendation_status"] = "lean"
        m = self.build(recs=recs)
        self.assertEqual(m["unit_status"], "ZERO_CHAMPION")
        self.assertTrue(all(a["status"] == "ZERO_CHAMPION" and a["n_selected"] == 0 for a in m["arms"].values()))

    def test_alternative_shortfall_is_not_matched(self):
        recs = X.records()
        for r in recs:
            if r["candidate_id"] != "c2":
                r["prediction"]["reliability"] = "D"          # alternative pool shrinks to one candidate
        m = self.build(recs=recs)
        self.assertEqual(m["unit_status"], "NON_COMPARABLE")
        self.assertIn("ALTERNATIVE_SHORTFALL", m["non_comparable_reasons"])

    def test_deterministic_and_rederivable(self):
        cap = self.cap()
        sched = X.schedule_source(self.arch)
        a, b = self.build(cap=cap, sched=sched), self.build(cap=cap, sched=sched)
        self.assertEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertEqual(M.verify_manifest(a, self.arch), a["manifest_sha256"])
        t = copy.deepcopy(a)
        t["arms"]["BLEND_50_50"]["selections"][0]["american_odds"] = -999
        t["manifest_sha256"] = FDC.canonical_sha256({k: v for k, v in t.items() if k != "manifest_sha256"})
        with self.assertRaises(M.ManifestError):
            M.verify_manifest(t, self.arch)


ARMS0 = "CHAMPION_EXISTING_POLICY"


def _race_seal(ledger_dir, archive_root, slugger, q):
    sys.path.insert(0, S1)
    a = RA.RawArchive(archive_root)
    m = M.build(unit=X.UNIT, board=X.board(), schedule_source=X.schedule_source(a),
                capture=X.capture(a, overrides={"slugger_hit": slugger}), archive=a, feature_snapshots={},
                mode="DRY_RUN_NONCONFIRMATORY")
    try:
        q.put(L.seal(ledger_dir, m, a, ledger_kind="DRY_RUN", now=X.DECISION)[1])
    except L.LedgerError:
        q.put("REFUSED")


class Ledger(Base):
    def led(self):
        return os.path.join(self.tmp.name, "led")

    def test_idempotent_replay_and_immutability(self):
        m = self.build()
        self.assertEqual(L.seal(self.led(), m, self.arch, ledger_kind="DRY_RUN", now=X.DECISION)[1], "SEALED")
        self.assertEqual(L.seal(self.led(), m, self.arch, ledger_kind="DRY_RUN", now=X.DECISION)[1], "IDEMPOTENT_NOOP")
        with self.assertRaises(L.LedgerError):
            L.seal(self.led(), self.build(cap=self.cap(overrides={"slugger_hit": -260})), self.arch,
                   ledger_kind="DRY_RUN", now=X.DECISION)
        self.assertEqual(L.verify_ledger(self.led(), self.arch), [f"{X.DATE}_NIGHT"])

    def test_no_backfill_dry_run_barred_and_anchor_head_required(self):
        led = self.led()
        with self.assertRaises(L.LedgerError):
            L.seal(led, self.build(), self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=LATE)
        pm = self.build(mode="PROSPECTIVE")
        with self.assertRaises(L.LedgerError):
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", now=LATE)                 # no anchored head
        for when in (f"{X.DATE}T23:15:00Z", f"{X.DATE}T21:00:00Z"):                       # after pitch / before decision
            with self.assertRaises(L.LedgerError):
                L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=when)
        self.assertEqual(L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS,
                                now=LATE)[1], "SEALED")
        with self.assertRaises(L.LedgerError):                                            # kinds never mix
            L.seal(led, self.build(), self.arch, ledger_kind="DRY_RUN", now=X.DECISION)

    def test_missing_link_blocks_and_restores_byte_identically(self):
        led = self.led()
        p, _ = L.seal(led, self.build(), self.arch, ledger_kind="DRY_RUN", now=X.DECISION)
        with open(p, "rb") as fh:
            original = fh.read()
        os.chmod(p, 0o644)
        os.remove(p)
        with self.assertRaises(L.LedgerError):
            L.read_chain(led)
        self.assertEqual(L.restore_index(led), [f"{X.DATE}_NIGHT"])
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), original)
        with open(os.path.join(led, "chain.jsonl"), "w") as fh:                           # truncated chain
            fh.write("")
        with self.assertRaises(L.LedgerError):
            L.read_chain(led)                                                             # orphan index

    def test_concurrent_sealers_cannot_both_seal_one_unit(self):
        ctx = multiprocessing.get_context("fork")
        q = ctx.Queue()
        procs = [ctx.Process(target=_race_seal, args=(self.led(), self.arch.root, s, q)) for s in (-250, -260)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertEqual(sorted(q.get(timeout=5) for _ in procs), ["REFUSED", "SEALED"])
        self.assertEqual(len(L.read_chain(self.led())), 1)

    def test_external_anchor_verification(self):
        led = self.led()
        L.seal(led, self.build(mode="PROSPECTIVE"), self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS,
               now=LATE)
        st = AN.head_statement(led)
        key = f"{X.DATE}_NIGHT"
        self.assertEqual(AN.verify_anchors(led, []), {key: "UNANCHORED"})
        self.assertEqual(AN.verify_anchors(led, [{"statement": st, "external_created_at": f"{X.DATE}T21:40:00Z"}]),
                         {key: "ANCHORED_PREGAME"})
        self.assertEqual(AN.verify_anchors(led, [{"statement": st, "external_created_at": f"{X.DATE}T23:30:00Z"}]),
                         {key: "ANCHORED_LATE"})
        forged = st.replace(st.split("entry=")[1][:64], "0" * 64)
        with self.assertRaises(AN.AnchorError):
            AN.verify_anchors(led, [{"statement": forged, "external_created_at": f"{X.DATE}T21:40:00Z"}])


class RealBoardShape(unittest.TestCase):
    def test_real_sealed_board_verifies_and_every_record_parses(self):
        """A fixed, already-committed production freeze (no fabrication): its seal
        re-verifies and every record yields the identity fields the join needs."""
        path = os.path.join(HERE, "output", "board_freeze_2026-09-22.json")
        with open(path, encoding="utf-8") as fh:
            b = json.load(fh)
        M.BF.verify_board_seal(b)
        rows = [M._cand_min(r) for r in b["records"]]
        self.assertEqual(len(rows), 985)
        in_scope = [c for c in rows if not c["combo"] and M.STAT_ALIAS.get(c["stat"], c["stat"]) in M.S1_STATS]
        self.assertTrue(all(c["line"] is not None and c["needs"] is not None and c["player_id"] for c in in_scope))
        # game-level markets (nrfi_combined: synthetic "player" id, needs 0.5) are out of S1 scope, never joined
        out = sorted({c["stat"] for c in rows if c not in in_scope})
        self.assertEqual(out, ["combined_strikeouts", "nrfi_combined"])
        self.assertEqual(len(in_scope), 965)


class BoundedGrowth(Base):
    def test_identical_raw_responses_stored_once_and_reseal_adds_nothing(self):
        self.cap()
        size, files = self.arch.size_bytes(), sum(len(f) for _, _, f in os.walk(self.arch.root))
        self.cap()
        self.assertEqual((self.arch.size_bytes(), sum(len(f) for _, _, f in os.walk(self.arch.root))), (size, files))

    def test_archive_refuses_repository_root_and_enforces_bound(self):
        with self.assertRaises(RA.ArchiveError):
            RA.RawArchive(os.path.join(HERE, "tmp_raw_archive_must_not_exist"))
        small = RA.RawArchive(os.path.join(self.tmp.name, "small"), max_bytes=200)
        with self.assertRaises(RA.ArchiveError):
            small.put(os.urandom(5000))
        cap = X.capture(small)                         # a bound hit is a failed observation, never a silent drop
        self.assertNotEqual(cap["status"], "COMPLETE")
        self.assertLessEqual(small.size_bytes(), 200)

    def test_manifest_and_ledger_do_not_nest_artifacts(self):
        m = self.build()
        blob = json.dumps(m)
        self.assertNotIn("winRunnerOdds", blob)                            # raw payloads referenced by hash only
        self.assertNotIn('"records"', blob)                                # board referenced by hash only
        led = os.path.join(self.tmp.name, "led")
        L.seal(led, m, self.arch, ledger_kind="DRY_RUN", now=X.DECISION)
        with open(os.path.join(led, "chain.jsonl"), "rb") as fh:
            self.assertLess(len(fh.read()), 1024)                          # entries are hashes, not copies


if __name__ == "__main__":
    unittest.main()
