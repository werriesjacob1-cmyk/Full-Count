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
import a3_attacks as A3ATT  # noqa: E402
import a4_attacks as A4ATT  # noqa: E402
import anchor as AN  # noqa: E402
import cutoff as CT  # noqa: E402
import fanduel as FDC  # noqa: E402
import features as F  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import post_repair_attacks as ATT  # noqa: E402
import timing_attacks as TA  # noqa: E402
import raw_archive as RA  # noqa: E402
import schedule as SCH  # noqa: E402
import workload as W  # noqa: E402

LATE = f"{X.DATE}T21:35:00Z"
COMMIT_AT = f"{X.DATE}T18:30:00Z"                 # before the 19:00Z (15:00 EDT) NIGHT commitment deadline


def record(led, arch, commitment=None):
    """A4: the commitment is chained into a PROSPECTIVE ledger, extending its (simulated) anchored head."""
    return L.record_cutoff(led, commitment or X.commitment(arch), arch, ledger_kind="PROSPECTIVE",
                           anchored_head=L.head(led), now=COMMIT_AT)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.arch = RA.RawArchive(os.path.join(self.tmp.name, "a"))

    def tearDown(self):
        self.tmp.cleanup()

    def cap(self, **kw):
        return X.capture(self.arch, **kw)

    def build(self, cap=None, recs=None, snaps=None, mode="DRY_RUN_NONCONFIRMATORY", unit=None, sched=None,
              commitment="ORIGINAL", **kw):
        """Default: the A3 cutoff commitment from the ORIGINAL (unmodified) schedule observed 14:00 EDT;
        schedule overrides in **kw change only the later decision-time schedule."""
        if commitment == "ORIGINAL":
            commitment = X.commitment(self.arch)
        return M.build(unit=unit or X.UNIT, board=kw.pop("board", None) or X.board(recs),
                       schedule_source=sched or X.schedule_source(self.arch, **kw), capture=cap or self.cap(),
                       archive=self.arch, feature_snapshots=snaps or {}, mode=mode, cutoff_commitment=commitment)

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
        late = FDC.sweep(self.arch, X.make_fetch(), clock=X.make_clock(21, 40))      # completes 21:40 > cutoff
        with self.assertRaisesRegex(M.ManifestError, "capture completed after"):
            self.build(cap=late)
        with self.assertRaises(M.ManifestError):
            self.build(unit=dict(X.UNIT, decision_at=f"{X.DATE}T21:30:00"))

    def test_non_pregame_game_stays_in_unit_and_volume(self):
        m = self.build(state={"900001": "Live"})
        self.assertEqual(m["game_issues"], {"900001": "GAME_NOT_PREGAME_IN_RAW"})
        self.assertEqual(self.row(m, "c1")["offer_status"], "GAME_NOT_PREGAME_IN_RAW")
        champ = m["arms"][ARMS0]
        self.assertEqual((champ["n_target"], champ["n_selected"], champ["status"]), (3, 0, "SHORTFALL"))
        self.assertEqual(m["unit_status"], "NON_COMPARABLE")
        self.assertNotIn("excluded_games", m)

    def test_lineup_must_be_confirmed_in_raw(self):
        m = self.build(lineups={"900001": ([], []), "900002": X.LINEUPS["900002"]})
        self.assertEqual(self.row(m, "c2")["offer_status"], "LINEUP_NOT_CONFIRMED_IN_RAW")
        self.assertEqual(self.row(m, "c5")["offer_status"], "AUTHENTICATED")          # pitcher: probable starter

    def test_starter_must_be_probable_in_raw(self):
        m = self.build(starters={"900001": ("19", "77")})
        self.assertEqual(self.row(m, "c5")["offer_status"], "STARTER_NOT_PROBABLE_IN_RAW")

    def test_board_must_match_schedule_and_be_sealed_pregame(self):
        m = self.build(first_pitch={"900002": f"{X.DATE}T22:50:00Z"})          # earliest (cutoff) unchanged
        self.assertEqual(m["game_issues"], {"900002": "BOARD_SCHEDULE_MISMATCH"})
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


class UnitTiming(Base):
    """AMENDMENT_A2: New York DAY/NIGHT boundary and one common unit cutoff."""

    def w(self, t, d):
        return SCH.window_of(t, d)

    def test_daylight_saving_boundary(self):
        self.assertEqual(self.w("2026-07-15T20:59:59Z", "2026-07-15"), "DAY")      # 16:59:59 EDT
        self.assertEqual(self.w("2026-07-15T21:00:00Z", "2026-07-15"), "NIGHT")    # 17:00:00 EDT
        self.assertEqual(self.w("2026-10-15T21:30:00Z", "2026-10-15"), "NIGHT")    # Codex probe: 17:30 EDT
        self.assertEqual(self.w("2026-07-15T16:59:59-04:00", "2026-07-15"), "DAY")  # explicit offset accepted

    def test_standard_time_boundary(self):
        self.assertEqual(self.w("2026-04-01T20:59:59Z", "2026-04-01"), "DAY")      # EDT already (sanity)
        self.assertEqual(self.w("2027-03-03T21:59:59Z", "2027-03-03"), "DAY")      # 16:59:59 EST
        self.assertEqual(self.w("2027-03-03T22:00:00Z", "2027-03-03"), "NIGHT")    # 17:00:00 EST
        self.assertEqual(self.w("2027-03-03T21:30:00Z", "2027-03-03"), "DAY")      # 16:30 EST

    def test_transition_days(self):
        self.assertEqual(self.w("2026-03-08T20:59:59Z", "2026-03-08"), "DAY")      # spring forward: EDT from 02:00
        self.assertEqual(self.w("2026-03-08T21:00:00Z", "2026-03-08"), "NIGHT")
        self.assertEqual(self.w("2026-11-01T21:59:59Z", "2026-11-01"), "DAY")      # fall back: EST from 02:00
        self.assertEqual(self.w("2026-11-01T22:00:00Z", "2026-11-01"), "NIGHT")

    def test_utc_date_transitions(self):
        self.assertEqual(self.w("2026-07-16T00:10:00Z", "2026-07-15"), "NIGHT")    # 20:10 EDT on the 15th
        self.assertEqual(self.w("2026-07-16T04:30:00Z", "2026-07-15"), "NIGHT")    # 00:30 EDT next local day
        self.assertEqual(self.w("2026-07-15T13:05:00Z", "2026-07-15"), "DAY")      # 09:05 EDT
        with self.assertRaises(SCH.ScheduleError):                                 # 23:00 EDT on the 14th
            self.w("2026-07-15T03:00:00Z", "2026-07-15")

    def test_invalid_and_ambiguous_timestamps_rejected(self):
        for bad in ("2026-11-01T01:30:00", "2026-07-15T21:00:00", "", None, "not-a-time", "2026-13-01T00:00:00Z"):
            with self.assertRaises(SCH.ScheduleError):
                self.w(bad, "2026-11-01")
        with self.assertRaises(SCH.ScheduleError):
            self.w("2026-07-15T21:00:00Z", "2026-7-15x")

    def test_unit_membership_follows_new_york_time(self):
        # 900001 later moved to 13:05 EDT -> DAY: it leaves the NIGHT unit's decision schedule, but the committed
        # cutoff (21:30) does not move and the committed game is not dropped: the unit is NON_COMPARABLE
        m = self.build(first_pitch={"900001": f"{X.DATE}T17:05:00Z"})
        self.assertEqual(m["game_issues"]["900001"], "MISSING_FROM_DECISION_SCHEDULE")
        self.assertIn("UNIT_GAMES_CHANGED_SINCE_COMMITMENT", m["non_comparable_reasons"])
        self.assertEqual((m["unit_status"], m["arms"][ARMS0]["n_target"]), ("NON_COMPARABLE", 3))
        with self.assertRaisesRegex(M.ManifestError, "unit cutoff"):                  # A2 fallback: cutoff moves
            self.build(first_pitch={"900001": f"{X.DATE}T17:05:00Z"}, commitment=None)

    def test_decision_must_be_the_single_unit_cutoff(self):
        for t in ("21:29:00", "21:31:00", "22:10:00", "22:45:00"):        # off-rule, or after the earliest pitch
            with self.assertRaisesRegex(M.ManifestError, "unit cutoff"):
                self.build(unit=dict(X.UNIT, decision_at=f"{X.DATE}T{t}Z"))
        m = self.build()
        self.assertEqual(m["decision_rule"]["unit_earliest_first_pitch_utc"], X.FIRST_PITCH["900001"])
        self.assertEqual(m["first_pitch_utc_min"], X.FIRST_PITCH["900001"])
        self.assertEqual(sorted(m["unit_games"]), ["900001", "900002"])

    def test_no_later_per_game_information_enters_the_unit(self):
        # a later production run (after 900001 started) only covers 900002; it can never enter this unit
        later_run = X.board([r for r in X.records() if r["game_pk"] == "900002"],
                            generated_at=f"{X.DATE}T22:20:00Z", sealed_at=f"{X.DATE}T22:21:00Z",
                            starts={"900002": X.FIRST_PITCH["900002"]})
        with self.assertRaisesRegex(M.ManifestError, "after the decision cutoff"):
            self.build(board=later_run)
        with self.assertRaises(M.ManifestError):                                     # schedule seen after cutoff
            self.build(sched=X.S.fetch(self.arch, lambda r: ("statsapi", X.schedule_bytes()), X.make_clock(22, 5),
                                       source=SCH.SOURCE, request=SCH.request_for(X.DATE)))
        led = os.path.join(self.tmp.name, "led")
        record(led, self.arch)
        L.seal(led, self.build(mode="PROSPECTIVE"), self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led),
               now=LATE)
        later = self.build(cap=self.cap(overrides={"slugger_hit": -260}), mode="PROSPECTIVE")
        with self.assertRaises(L.LedgerError):                                       # unit already sealed
            L.seal(led, later, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led), now=LATE)

    def test_no_seal_at_or_after_unit_first_pitch(self):
        pm = self.build(mode="PROSPECTIVE")
        for when in (X.FIRST_PITCH["900001"], f"{X.DATE}T22:20:00Z"):
            with self.assertRaises(L.LedgerError):
                L.seal(os.path.join(self.tmp.name, "led"), pm, self.arch, ledger_kind="PROSPECTIVE",
                       anchored_head=L.GENESIS, now=when)

    def test_timing_attacks_rejected(self):
        res = TA.run()
        self.assertEqual(len(res), 5)
        self.assertEqual({k: v for k, v in res.items() if not v.startswith("REJECTED")}, {})


D = X.DATE
DELAYED = {"900001": f"{D}T22:20:00Z", "900002": X.FIRST_PITCH["900002"]}


class CutoffCommitment(Base):
    """AMENDMENT_A3: a committed cutoff never moves later; missing evidence is never certified."""

    def test_a3_attacks_rejected(self):
        res = A3ATT.run()
        self.assertEqual(len(res), 5)
        self.assertEqual({k: v for k, v in res.items() if not v.startswith("REJECTED")}, {})

    def test_commitment_deadlines_follow_new_york_time(self):
        self.assertEqual(CT.deadline_utc("2026-07-15", "DAY").isoformat(), "2026-07-15T13:00:00+00:00")    # 09:00 EDT
        self.assertEqual(CT.deadline_utc("2026-07-15", "NIGHT").isoformat(), "2026-07-15T19:00:00+00:00")  # 15:00 EDT
        self.assertEqual(CT.deadline_utc("2027-01-15", "NIGHT").isoformat(), "2027-01-15T20:00:00+00:00")  # 15:00 EST

    def test_delay_never_moves_the_cutoff_later(self):
        board = X.board(starts=DELAYED)
        m = self.build(board=board, first_pitch=DELAYED)                       # decision schedule shows the delay
        self.assertEqual((m["unit"]["decision_at"], m["unit_status"]), (X.DECISION, "COMPARABLE"))
        self.assertEqual(m["decision_rule"]["decision_cutoff_utc"], X.DECISION)
        with self.assertRaisesRegex(M.ManifestError, "committed unit cutoff"):
            self.build(board=board, first_pitch=DELAYED, unit=dict(X.UNIT, decision_at=f"{D}T21:50:00Z"))

    def test_game_moved_earlier_shortens_or_invalidates(self):
        earlier = {"900001": f"{D}T21:50:00Z", "900002": X.FIRST_PITCH["900002"]}
        m = self.build(board=X.board(starts=earlier), first_pitch=earlier)
        self.assertEqual(m["unit_status"], "NON_COMPARABLE")
        self.assertIn("CUTOFF_LEAD_SHORTENED", m["non_comparable_reasons"])
        before = {"900001": f"{D}T21:20:00Z", "900002": X.FIRST_PITCH["900002"]}
        with self.assertRaisesRegex(M.ManifestError, "at or before the decision cutoff"):
            self.build(board=X.board(starts=before, sealed_at=f"{D}T20:56:00Z"), first_pitch=before)

    def test_missing_earlier_schedule_is_missing_cutoff_evidence(self):
        m = self.build(commitment=None)
        self.assertEqual((m["unit_status"], m["non_comparable_reasons"]), ("NON_COMPARABLE", ["MISSING_CUTOFF_EVIDENCE"]))
        self.assertIn("MISSING_CUTOFF_EVIDENCE", m["certification"]["blockers"])
        self.assertTrue(all(a["status"] != "MATCHED" for a in m["arms"].values()))

    def test_commitment_must_be_timely_authentic_and_for_this_unit(self):
        with self.assertRaisesRegex(CT.CutoffError, "deadline"):
            X.commitment(self.arch, hour=19, minute=5)                          # 15:05 EDT: after the deadline
        c = X.commitment(self.arch)
        forged = dict(c, cutoff_utc=f"{D}T21:50:00Z")
        forged["commitment_sha256"] = M.FDC.canonical_sha256({k: v for k, v in forged.items()
                                                              if k != "commitment_sha256"})
        with self.assertRaisesRegex(M.ManifestError, "cutoff commitment rejected"):
            self.build(commitment=forged)
        with self.assertRaises(CT.CutoffError):
            CT.verify(c, self.arch, {"date": D, "window": "DAY"})

    def test_commitment_log_is_write_once_and_binds_the_seal(self):
        led = os.path.join(self.tmp.name, "led")
        pm = self.build(mode="PROSPECTIVE")
        with self.assertRaisesRegex(L.LedgerError, "no cutoff commitment was chained"):
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led), now=LATE)
        with self.assertRaisesRegex(L.LedgerError, "after its commitment deadline"):
            L.record_cutoff(led, X.commitment(self.arch), self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS,
                            now=f"{D}T19:30:00Z")
        self.assertEqual(record(led, self.arch), "RECORDED")
        self.assertEqual(record(led, self.arch), "IDEMPOTENT_NOOP")
        other = X.commitment(self.arch, first_pitch=DELAYED)                     # same unit, different evidence
        with self.assertRaisesRegex(L.LedgerError, "cutoffs never move"):
            record(led, self.arch, other)
        with self.assertRaisesRegex(L.LedgerError, "manifest carries no cutoff commitment"):
            L.seal(led, self.build(mode="PROSPECTIVE", commitment=None), self.arch, ledger_kind="PROSPECTIVE",
                   anchored_head=L.head(led), now=LATE)
        self.assertEqual(L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led),
                                now=LATE)[1], "SEALED")

    def test_commitment_anchor_verification(self):
        c = X.commitment(self.arch)
        st = AN.commitment_statement(c)
        self.assertEqual(AN.verify_commitment_anchor(c, []), "UNANCHORED")
        self.assertEqual(AN.verify_commitment_anchor(c, [{"statement": st, "external_created_at": f"{D}T18:40:00Z"}]),
                         "ANCHORED_BEFORE_DEADLINE")
        self.assertEqual(AN.verify_commitment_anchor(c, [{"statement": st, "external_created_at": f"{D}T19:40:00Z"}]),
                         "ANCHORED_LATE")
        self.assertIn("CUTOFF_COMMITMENT_NOT_EXTERNALLY_ANCHORED", self.build()["certification"]["blockers"])


class ZeroChampion(Base):
    """AMENDMENT_A3: ZERO_CHAMPION only with verified complete coverage."""

    def lean(self, recs):
        for r in recs:
            r["selector"]["recommendation_status"] = "lean"
        return recs

    def test_complete_zero_pick_slate_is_zero_champion(self):
        m = self.build(recs=self.lean(X.records()))
        self.assertEqual((m["unit_status"], m["non_comparable_reasons"]), ("ZERO_CHAMPION", []))
        self.assertTrue(all(a["status"] == "ZERO_CHAMPION" for a in m["arms"].values()))

    def test_incomplete_zero_pick_slates_are_non_comparable(self):
        cases = {
            "missing game": dict(board=X.board(self.lean([r for r in X.records() if r["game_pk"] == "900002"]),
                                               starts={"900002": X.FIRST_PITCH["900002"]})),
            "failed capture": dict(recs=self.lean(X.records()), cap=FDC.sweep(
                self.arch, X.make_fetch(fail_paths=("eventId=E1&tab=moonshots",)), clock=X.make_clock())),
            "no cutoff evidence": dict(recs=self.lean(X.records()), commitment=None),
            "non-pregame game": dict(recs=self.lean(X.records()), state={"900002": "Live"}),
        }
        for label, kw in cases.items():
            m = self.build(**kw)
            self.assertEqual(m["unit_status"], "NON_COMPARABLE", label)
            self.assertTrue(m["non_comparable_reasons"], label)
            self.assertTrue(all(a["status"] == "NON_COMPARABLE" for a in m["arms"].values()), label)

    def test_downstream_tally_cannot_override_unit_state(self):
        good = self.build(recs=self.lean(X.records()))
        bad = self.build(recs=self.lean(X.records()), commitment=None)
        t = M.unit_tally([good, bad], self.arch)
        self.assertEqual((t["ZERO_CHAMPION"], t["NON_COMPARABLE"]), (1, 1))
        forged = copy.deepcopy(bad)
        for a in forged["arms"].values():
            a["status"] = "ZERO_CHAMPION"
        forged["unit_status"] = "ZERO_CHAMPION"
        forged["manifest_sha256"] = FDC.canonical_sha256({k: v for k, v in forged.items() if k != "manifest_sha256"})
        with self.assertRaises(M.ManifestError):
            M.unit_tally([forged], self.arch)

    def test_deterministic_replay_with_commitment(self):
        cap, sched, c = self.cap(), X.schedule_source(self.arch), X.commitment(self.arch)
        a = self.build(cap=cap, sched=sched, commitment=c)
        b = self.build(cap=cap, sched=sched, commitment=c)
        self.assertEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertEqual(M.verify_manifest(a, self.arch), a["manifest_sha256"])
        self.assertEqual(a["inputs"]["cutoff_commitment"]["commitment_sha256"], c["commitment_sha256"])


class EvidencePreservation(unittest.TestCase):
    FROZEN = {"research/fc_acc_001/S1_PROTOCOL.md": "736bc944ada1bdb88cd6d0fb77780206dd75a47d566b66d45a233b57d1d8b42b",
              "research/fc_acc_001/PREREG.md": "e6466a71cdbacb86ce0f10af1cd89e353c20aa8f99ac8c3df59a616d5905dbbb",
              "research/fc_acc_001/AMENDMENT_A1.md": "36b6f32a5d44139c0ee6bb8e2c892326cacb66fe7f1994ddd214664ef80e180c",
              "research/fc_acc_001/out/amendment_a1.json":
                  "d09e7e343720d27fb5558e5e02e1478ea3bc6fd46632ff2adef74f4e81f60185",
              "research/fc_acc_001/out/s1_codex_attacks_before_after.json":
                  "9814b6ec5f7053fcad9531eb217b20ac37f6e4c695670316c8be4e9dc638d7c9",
              "research/fc_acc_001/AMENDMENT_A2.md": "87df0289be2cac0e66abb062369a57f58988fba9ccd640ac6c4e9876c5cbd9d0",
              "research/fc_acc_001/out/s1_a2_timing_attacks_before_after.json": "06e66983fbeb4a71f187c2f4de2b52003c6559a90b89ce9292e47766ea502da4",
              "research/fc_acc_001/AMENDMENT_A3.md": "e9640b2281637a2255a7097a0f551fccff0d242e9df636dc786fa4e0afbb1304",                 # A4 pins A3
              "research/fc_acc_001/out/s1_a3_attacks_before_after.json": "6473bf165f2467ae5bd10298cdfcc60645745ecd4adbed3b8eb74d9d9fe4d9f6"}

    def test_frozen_protocol_and_prior_evidence_bytes_unchanged(self):
        import hashlib
        for rel, sha in self.FROZEN.items():
            with open(os.path.join(HERE, rel), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), sha, rel)


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
                mode="DRY_RUN_NONCONFIRMATORY", cutoff_commitment=X.commitment(a))
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
        record(led, self.arch)
        with self.assertRaises(L.LedgerError):
            L.seal(led, self.build(), self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led), now=LATE)
        pm = self.build(mode="PROSPECTIVE")
        with self.assertRaises(L.LedgerError):
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", now=LATE)                 # no anchored head
        for when in (f"{X.DATE}T23:15:00Z", f"{X.DATE}T21:00:00Z"):                       # after pitch / before decision
            with self.assertRaises(L.LedgerError):
                L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led), now=when)
        self.assertEqual(L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led),
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
        record(led, self.arch)
        cut_st = AN.head_statement(led)                                   # head = the chained cutoff commitment
        L.seal(led, self.build(mode="PROSPECTIVE"), self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led),
               now=LATE)
        st = AN.head_statement(led)
        key, ck = f"{X.DATE}_NIGHT", f"cutoff:{X.DATE}_NIGHT"
        self.assertEqual(AN.verify_anchors(led, []), {key: "UNANCHORED", ck: "UNANCHORED"})
        self.assertEqual(AN.verify_anchors(led, [{"statement": st, "external_created_at": f"{X.DATE}T21:40:00Z"}]),
                         {key: "ANCHORED_PREGAME", ck: "ANCHORED_LATE"})          # head anchor after the deadline
        self.assertEqual(AN.verify_anchors(led, [{"statement": st, "external_created_at": f"{X.DATE}T23:30:00Z"}]),
                         {key: "ANCHORED_LATE", ck: "ANCHORED_LATE"})
        pre = {"statement": cut_st, "external_created_at": f"{X.DATE}T18:45:00Z"}      # before the 19:00Z deadline
        post = {"statement": st, "external_created_at": f"{X.DATE}T21:40:00Z"}
        self.assertEqual(AN.verify_anchors(led, [pre, post]), {key: "ANCHORED_PREGAME", ck: "ANCHORED_BEFORE_DEADLINE"})
        # A4: local validity is never prospective evidence; both anchors are required
        self.assertEqual(AN.unit_evidence(led, [], self.arch), {key: "LOCAL_ONLY"})
        self.assertEqual(AN.unit_evidence(led, [post], self.arch), {key: "LOCAL_ONLY"})
        self.assertEqual(AN.unit_evidence(led, [pre], self.arch), {key: "LOCAL_ONLY"})
        self.assertEqual(AN.unit_evidence(led, [pre, post], self.arch), {key: "EXTERNALLY_ANCHORED_PROSPECTIVE"})
        forged = st.replace(st.split("entry=")[1][:64], "0" * 64)
        with self.assertRaises(AN.AnchorError):
            AN.verify_anchors(led, [{"statement": forged, "external_created_at": f"{X.DATE}T21:40:00Z"}])


def _race_record(ledger_dir, archive_root, delayed, q):
    sys.path.insert(0, S1)
    a = RA.RawArchive(archive_root)
    c = X.commitment(a, first_pitch=A4ATT.DELAYED) if delayed else X.commitment(a)
    try:
        q.put(L.record_cutoff(ledger_dir, c, a, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=COMMIT_AT))
    except L.LedgerError:
        q.put("REFUSED")


def _race_seal_prospective(ledger_dir, archive_root, q):
    sys.path.insert(0, S1)
    a = RA.RawArchive(archive_root)
    m = M.build(unit=X.UNIT, board=X.board(), schedule_source=X.schedule_source(a), capture=X.capture(a), archive=a,
                feature_snapshots={}, mode="PROSPECTIVE", cutoff_commitment=X.commitment(a))
    try:
        q.put(L.seal(ledger_dir, m, a, ledger_kind="PROSPECTIVE", anchored_head=L.head(ledger_dir), now=LATE)[1])
    except L.LedgerError as e:
        q.put("REFUSED: " + str(e)[:40])


class ChainedCutoff(Base):
    """Amendment A4 (Codex A3 HOLD 6197211757): cutoff commitments are hash-chained ledger entries."""

    KEY = f"{X.DATE}_NIGHT"

    def led(self):
        return os.path.join(self.tmp.name, "led")

    def cutoff_file(self):
        return os.path.join(self.led(), "cutoffs", self.KEY + ".json")

    def sealed(self):
        led = self.led()
        self.assertEqual(record(led, self.arch), "RECORDED")
        self.assertEqual(L.seal(led, self.build(mode="PROSPECTIVE"), self.arch, ledger_kind="PROSPECTIVE",
                                anchored_head=L.head(led), now=LATE)[1], "SEALED")
        return led

    def test_a4_attacks_rejected(self):
        res = A4ATT.run()
        self.assertEqual(len(res), 8)
        self.assertEqual({k: v for k, v in res.items() if not v.startswith("REJECTED")}, {})

    def test_commitment_is_a_chain_entry_and_the_seal_extends_it(self):
        led = self.sealed()
        cut, seal_e = L.read_chain(led, self.arch)
        self.assertEqual((cut["entry_type"], cut["seq"], seal_e["seq"]), (L.CUTOFF, 1, 2))
        self.assertEqual(seal_e["prev_entry_sha256"], cut["entry_sha256"])
        self.assertEqual(cut["commitment_sha256"], X.commitment(self.arch)["commitment_sha256"])
        self.assertEqual(L.logged_cutoff(led, self.KEY, self.arch)["commitment"], X.commitment(self.arch))
        self.assertEqual(L.verify_ledger(led, self.arch), [self.KEY])
        self.assertEqual(cut["evidence_status"], "LOCAL_COMMITMENT_UNANCHORED")

    def test_deletion_fails_closed_and_recovery_restores_the_same_first_commitment(self):
        led = self.sealed()
        with open(self.cutoff_file(), "rb") as fh:
            original = fh.read()
        os.chmod(self.cutoff_file(), 0o644)
        os.remove(self.cutoff_file())
        for check in (lambda: L.verify_ledger(led, self.arch), lambda: L.head(led),
                      lambda: L.logged_cutoff(led, self.KEY),
                      lambda: record(led, self.arch, X.commitment(self.arch, first_pitch=A4ATT.DELAYED))):
            with self.assertRaisesRegex(L.LedgerError, "MISSING_LINK"):
                check()
        self.assertEqual(L.restore_index(led), [f"cutoff:{self.KEY}"])
        with open(self.cutoff_file(), "rb") as fh:
            self.assertEqual(fh.read(), original)                                    # byte-identical, from the chain
        self.assertEqual(L.verify_ledger(led, self.arch), [self.KEY])
        with self.assertRaisesRegex(L.LedgerError, "cutoffs never move"):           # still the first commitment
            record(led, self.arch, X.commitment(self.arch, first_pitch=A4ATT.DELAYED))

    def test_replacement_and_tampering_fail_closed(self):
        led = self.sealed()
        rec = json.load(open(self.cutoff_file()))
        for field, value in (("commitment_sha256", "0" * 64), ("cutoff_utc", f"{X.DATE}T21:50:00Z"),
                             ("recorded_at", f"{X.DATE}T18:59:00Z")):
            bad = dict(rec, **{field: value})
            os.chmod(self.cutoff_file(), 0o644)
            with open(self.cutoff_file(), "wb") as fh:
                fh.write(RA.canonical_bytes(bad))
            with self.subTest(field), self.assertRaisesRegex(L.LedgerError, "does not match its chain entry"):
                L.verify_ledger(led, self.arch)
        os.chmod(self.cutoff_file(), 0o644)
        with open(self.cutoff_file(), "wb") as fh:
            fh.write(RA.canonical_bytes(rec))
        lines = open(os.path.join(led, "chain.jsonl")).read().splitlines()
        e = json.loads(lines[0])
        e["cutoff_utc"] = f"{X.DATE}T21:50:00Z"                                    # rewrite the chain entry itself
        with open(os.path.join(led, "chain.jsonl"), "w") as fh:
            fh.write("\n".join([json.dumps(e, sort_keys=True, separators=(",", ":"))] + lines[1:]) + "\n")
        with self.assertRaisesRegex(L.LedgerError, "chain broken at seq 1"):
            L.verify_ledger(led, self.arch)

    def test_unverifiable_commitment_object_fails_closed(self):
        led = self.sealed()
        cut = L.read_chain(led)[0]
        p = self.arch._path(cut["commitment_object_sha256"])
        os.chmod(p, 0o644)
        os.remove(p)
        with self.assertRaises(Exception):
            L.verify_ledger(led, self.arch)

    def test_missing_links_and_orphans(self):
        led = self.sealed()
        lines = open(os.path.join(led, "chain.jsonl")).read().splitlines()
        with open(os.path.join(led, "chain.jsonl"), "w") as fh:                   # drop the seal: orphan unit index
            fh.write(lines[0] + "\n")
        with self.assertRaisesRegex(L.LedgerError, "ORPHAN unit index"):
            L.read_chain(led)
        with open(os.path.join(led, "chain.jsonl"), "w") as fh:                   # drop everything: orphan cutoff too
            fh.write("")
        with self.assertRaisesRegex(L.LedgerError, "ORPHAN"):
            L.read_chain(led)
        with self.assertRaises(L.LedgerError):                                     # not locally recoverable
            L.restore_index(led)

    def test_prospective_seal_requires_the_chained_commitment(self):
        led = self.led()
        pm = self.build(mode="PROSPECTIVE")
        with self.assertRaisesRegex(L.LedgerError, "no cutoff commitment was chained"):          # missing
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led), now=LATE)
        with self.assertRaisesRegex(L.LedgerError, "after its commitment deadline"):             # late
            L.record_cutoff(led, X.commitment(self.arch), self.arch, ledger_kind="PROSPECTIVE",
                            anchored_head=L.GENESIS, now=f"{X.DATE}T19:00:01Z")
        with self.assertRaisesRegex(L.LedgerError, "anchored chain head"):                       # no anchor named
            L.record_cutoff(led, X.commitment(self.arch), self.arch, ledger_kind="PROSPECTIVE", now=COMMIT_AT)
        record(led, self.arch)
        with self.assertRaisesRegex(L.LedgerError, "manifest carries no cutoff commitment"):     # manifest lacks it
            L.seal(led, self.build(mode="PROSPECTIVE", commitment=None), self.arch, ledger_kind="PROSPECTIVE",
                   anchored_head=L.head(led), now=LATE)
        other = X.commitment(self.arch, first_pitch=A4ATT.DELAYED)                               # different
        with self.assertRaises((L.LedgerError, M.ManifestError)):
            L.seal(led, A4ATT._build(self.arch, commitment=other, delayed=True), self.arch,
                   ledger_kind="PROSPECTIVE", anchored_head=L.head(led), now=f"{X.DATE}T21:55:00Z")
        with self.assertRaisesRegex(L.LedgerError, "externally anchored head"):                  # stale anchor
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=LATE)
        self.assertEqual(L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led),
                                now=LATE)[1], "SEALED")

    def test_idempotent_reseal_validates_the_commitment_first(self):
        led = self.sealed()
        pm = self.build(mode="PROSPECTIVE")
        self.assertEqual(L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.head(led),
                                now=LATE)[1], "IDEMPOTENT_NOOP")
        # DRY_RUN: a unit sealed without a commitment can never acquire one, and its re-seal still validates
        dled = os.path.join(self.tmp.name, "dry")
        dm = self.build(commitment=None)
        self.assertEqual(L.seal(dled, dm, self.arch, ledger_kind="DRY_RUN", now=X.DECISION)[1], "SEALED")
        with self.assertRaisesRegex(L.LedgerError, "already sealed"):
            L.record_cutoff(dled, X.commitment(self.arch), self.arch, ledger_kind="DRY_RUN", now=COMMIT_AT)
        self.assertEqual(L.seal(dled, dm, self.arch, ledger_kind="DRY_RUN", now=X.DECISION)[1], "IDEMPOTENT_NOOP")
        # tampered commitment index: the re-seal is refused, not NOOP
        rec = json.load(open(self.cutoff_file()))
        os.chmod(self.cutoff_file(), 0o644)
        with open(self.cutoff_file(), "wb") as fh:
            fh.write(RA.canonical_bytes(dict(rec, commitment_sha256="1" * 64)))
        with self.assertRaises(L.LedgerError):
            L.seal(led, pm, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=LATE)

    def test_wholesale_deletion_cannot_silently_restart_with_a_different_first_commitment(self):
        led = self.led()
        record(led, self.arch)
        anchored = L.head(led)
        published = [{"statement": AN.head_statement(led), "external_created_at": f"{X.DATE}T18:45:00Z",
                      "external_ref": "synthetic"}]
        import shutil
        shutil.rmtree(led)
        other = X.commitment(self.arch, first_pitch=A4ATT.DELAYED)
        with self.assertRaisesRegex(L.LedgerError, "externally anchored head"):          # honest anchored head
            L.record_cutoff(led, other, self.arch, ledger_kind="PROSPECTIVE", anchored_head=anchored, now=COMMIT_AT)
        L.record_cutoff(led, other, self.arch, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=COMMIT_AT)
        with self.assertRaisesRegex(AN.AnchorError, "CONFLICT"):                            # the verifier detects it
            AN.verify_anchors(led, published, self.arch)

    def test_concurrent_conflicting_commitments_record_exactly_one(self):
        ctx = multiprocessing.get_context("fork")
        for i in range(3):
            led = os.path.join(self.tmp.name, f"race{i}")
            q = ctx.Queue()
            procs = [ctx.Process(target=_race_record, args=(led, self.arch.root, d, q)) for d in (False, True, False, True)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(60)
            got = sorted(q.get(timeout=5) for p in procs)
            # one writer wins; its twin is a NOOP; both writers of the other commitment are refused
            self.assertEqual(got, ["IDEMPOTENT_NOOP", "RECORDED", "REFUSED", "REFUSED"])
            cuts = [e for e in L.read_chain(led, self.arch) if L.is_cutoff(e)]
            self.assertEqual(len(cuts), 1)

    def test_concurrent_record_and_seal_keep_the_chain_consistent(self):
        ctx = multiprocessing.get_context("fork")
        led = self.led()
        record(led, self.arch)
        q = ctx.Queue()
        procs = [ctx.Process(target=_race_seal_prospective, args=(led, self.arch.root, q)) for _ in range(3)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        got = [q.get(timeout=5) for _ in procs]
        self.assertEqual(got.count("SEALED"), 1, got)
        entries = L.read_chain(led, self.arch)
        self.assertEqual([e.get("entry_type", "SEAL") for e in entries], [L.CUTOFF, "SEAL"])


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
