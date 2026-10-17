"""FC-NFL-004 Codex P1/P2 repairs: D1-consistent fit, slate provenance, lifecycle."""
import hashlib
import json
import random
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from nfl.prospective.game_market_research_freeze import (
    GameMarketFreezeError,
    canonical_sha256,
    load_research_freeze,
    write_research_freeze,
)
from nfl.prospective.game_market_research_grade import GameMarketResearchGradeError, grade_research_freeze
from nfl.prospective.game_market_research_lifecycle import (
    LedgerLifecycleError,
    grade_ledger,
    merge_ledgers,
)
from nfl.prospective.game_market_slate import (
    COMPLETE,
    DISCOVERY_FAILURE,
    NO_EVENTS,
    PARTIAL_COVERAGE,
    GameMarketSlateError,
    artifact_sha,
    build_discovery,
    reconcile_expected,
    slate_status,
    verify_slate_artifact,
)
from nfl.research.game_market_models import (
    DEFAULT_PARAMS_PATH,
    FIT_PROCEDURE_D1,
    FIT_PROCEDURE_ORIGINAL,
    ORIGINAL_PARAMS_PATH,
    f1_centers,
    fit_params,
    load_params,
    market_center,
    params_sha256,
)
from nfl.tests.test_game_market_research_pathway import (
    ALIASES,
    SCHEDULE,
    _binding,
    _espn,
    _freeze,
    _record,
    _snapshot,
)

REPO = Path(__file__).resolve().parents[2]
REAL_LEDGER = REPO / "data/nfl_game_market_research/frozen"
ORIGINAL_SHA = "c1dbd52ee5232a882b9d91278261af66b54f5902b0ad1eeead28888eefb34fb5"
AMENDED_SHA = "acc53f4dee9a1af6da90924b69d13fb288d91793694187c497f5c14ed24d43c5"


def _synthetic_dev(n=300, seed=11):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        truth = rng.gauss(0, 6)
        tt = rng.gauss(44, 5)
        rows.append({"b0_margin": truth - 2.5 + rng.gauss(0, 4), "b0_total": tt + rng.gauss(0, 5),
                     "line_margin": round(truth * 2) / 2, "line_total": round(tt * 2) / 2,
                     "actual_margin": round(truth + rng.gauss(0, 13)),
                     "actual_total": max(0, round(tt + rng.gauss(0, 13)))})
    return rows


class FitConsistencyTests(unittest.TestCase):
    def test_committed_params_identities(self):
        # The original artifact bound by the 2026-10-12T17:01Z freezes is unchanged.
        self.assertEqual(params_sha256(load_params(ORIGINAL_PARAMS_PATH)), ORIGINAL_SHA)
        amended = load_params(DEFAULT_PARAMS_PATH)
        self.assertEqual(params_sha256(amended), AMENDED_SHA)
        self.assertEqual(amended.provenance["fit_procedure"], FIT_PROCEDURE_D1)
        self.assertEqual(amended.provenance["fit_partition"], "development_2000_2019")

    def test_d1_fit_uses_the_evaluation_m0_reference(self):
        rows = _synthetic_dev()
        p = fit_params(rows, {"fit_partition": "synthetic"}, procedure=FIT_PROCEDURE_D1)
        # k is the origin-OLS slope of (actual - solved M0) on (F1 - line) using
        # exactly the centers evaluation will solve: normal equation holds.
        resid = 0.0
        for r in rows:
            fm, _ = f1_centers(p, r["b0_margin"], r["b0_total"])
            m0 = market_center(p, "margin", r["line_margin"])
            x = fm - r["line_margin"]
            resid += (r["actual_margin"] - m0 - p.c1_k_margin * x) * x
        self.assertAlmostEqual(resid, 0.0, places=6)
        self.assertEqual(len(p.provenance["d1_trace"]), 3)

    def test_original_procedure_is_line_centered(self):
        rows = _synthetic_dev()
        p = fit_params(rows, {"fit_partition": "synthetic"}, procedure=FIT_PROCEDURE_ORIGINAL)
        self.assertNotIn("fit_procedure", p.provenance)
        sig = (sum((r["actual_margin"] - r["line_margin"]) ** 2 for r in rows) / len(rows)) ** 0.5
        self.assertAlmostEqual(p.sigma["M0"]["margin"], sig, places=9)
        with self.assertRaises(ValueError):
            fit_params(rows, {}, procedure="tuned")


def _discover(body):
    return json.loads(body)["events"]


ROOT_OBS = "2026-09-20T12:00:00Z"


def _root(events):
    return json.dumps({"events": events}).encode()


EV_MIA_BUF = {"event_id": 35601246, "name": "Miami Dolphins @ Buffalo Bills", "open_date": "2026-09-20T17:01:00Z"}


class SlateProvenanceTests(unittest.TestCase):
    def _cov(self, events, schedule=SCHEDULE, window=1.0):
        d = build_discovery(root_body=_root(events), root_observed_at=ROOT_OBS, root_url="https://fd.test/root",
                            window_days=window, discover=_discover)
        return d, reconcile_expected(d, schedule, ALIASES, tolerance_minutes=2)

    def test_complete_partial_no_events_failure(self):
        d, cov = self._cov([EV_MIA_BUF])
        self.assertEqual(cov["process_event_ids"], ["35601246"])
        self.assertEqual(slate_status(len(cov["expected_games"]), 1, len(cov["expected_not_discovered"])), COMPLETE)
        d, cov = self._cov([])
        self.assertEqual(cov["expected_not_discovered"][0]["reason"], "NOT_DISCOVERED")
        self.assertEqual(slate_status(len(cov["expected_games"]), 0, 1), DISCOVERY_FAILURE)
        d, cov = self._cov([], schedule=[])
        self.assertEqual(slate_status(0, 0, 0), NO_EVENTS)
        other = deepcopy(SCHEDULE) + [{**SCHEDULE[2], "game_id": "2026_03_NYJ_NE", "away_team": "NYJ",
                                       "home_team": "NE"}]
        d, cov = self._cov([EV_MIA_BUF], schedule=other)
        self.assertEqual([m["game_id"] for m in cov["expected_not_discovered"]], ["2026_03_NYJ_NE"])
        self.assertEqual(slate_status(2, 1, 1), PARTIAL_COVERAGE)

    def test_boundary_plus_one_minute_event_is_processed(self):
        # Window ends exactly at the schedule kickoff; FanDuel's +1 min is outside it.
        d = build_discovery(root_body=_root([EV_MIA_BUF]), root_observed_at="2026-09-20T16:00:00Z",
                            root_url="u", window_days=1 / 24, discover=_discover)
        self.assertEqual(d["window_event_ids"], [])
        cov = reconcile_expected(d, SCHEDULE, ALIASES, tolerance_minutes=2)
        self.assertEqual(cov["process_event_ids"], ["35601246"])

    def _artifact(self, root, status=None, drop=False, schedule=SCHEDULE):
        d, cov = self._cov([EV_MIA_BUF], schedule=schedule)
        events = [] if drop else [{"event_id": "35601246", "status": "FROZEN",
                                   "discovery_sha256": d["discovery_sha256"]}]
        sched = _schedule_csv(schedule)
        body = {"discovery": d, "coverage": cov, "events": events,
                "schedule_source": {"sha256": hashlib.sha256(sched).hexdigest(), "bytes": len(sched)},
                "slate_status": status or slate_status(len(cov["expected_games"]), 1,
                                                       len(cov["expected_not_discovered"]))}
        return {**body, "artifact_sha256": artifact_sha(body)}

    def test_verify_slate_rederives_from_archived_root(self):
        root, sched = _root([EV_MIA_BUF]), _schedule_csv(SCHEDULE)
        art = self._artifact(root)
        self.assertEqual(verify_slate_artifact(art, root, sched, _discover), art["artifact_sha256"])
        with self.assertRaises(GameMarketSlateError):  # root bytes swapped
            verify_slate_artifact(art, _root([EV_MIA_BUF, {**EV_MIA_BUF, "event_id": 9}]), sched, _discover)
        with self.assertRaises(GameMarketSlateError):  # an event silently dropped
            verify_slate_artifact(self._artifact(root, drop=True), root, sched, _discover)
        with self.assertRaises(GameMarketSlateError):  # status lies
            verify_slate_artifact(self._artifact(root, status=NO_EVENTS), root, sched, _discover)
        tampered = deepcopy(art)
        tampered["events"][0]["status"] = "FROZEN_LATER"
        with self.assertRaises(GameMarketSlateError):
            verify_slate_artifact(tampered, root, sched, _discover)
        with self.assertRaises(GameMarketSlateError):  # schedule bytes swapped
            verify_slate_artifact(art, root, sched + b"\n", _discover)

    def test_codex_forged_coverage_with_recomputed_self_hash_is_rejected(self):
        # Codex P1 (review 6129802534), exact manipulation: empty expected_games and
        # expected_not_discovered, recompute the artifact self-hash. With one more
        # scheduled game than discovered, the honest status is PARTIAL_COVERAGE.
        extra = deepcopy(SCHEDULE) + [{**SCHEDULE[2], "game_id": "2026_03_NYJ_NE", "away_team": "NYJ",
                                       "home_team": "NE", "espn": "402"}]
        root, sched = _root([EV_MIA_BUF]), _schedule_csv(extra)
        honest = self._artifact(root, schedule=extra)
        self.assertEqual(honest["slate_status"], PARTIAL_COVERAGE)
        self.assertEqual(verify_slate_artifact(honest, root, sched, _discover), honest["artifact_sha256"])
        for mutate in (
            lambda a: (a["coverage"].__setitem__("expected_games", []),
                       a["coverage"].__setitem__("expected_not_discovered", [])),
            lambda a: (a["coverage"].__setitem__("expected_not_discovered", []),
                       a.__setitem__("slate_status", COMPLETE)),
            lambda a: a["coverage"]["expected_games"].pop(),
        ):
            forged = deepcopy(honest)
            mutate(forged)
            body = {k: v for k, v in forged.items() if k != "artifact_sha256"}
            forged["artifact_sha256"] = artifact_sha(body)
            with self.assertRaises(GameMarketSlateError):
                verify_slate_artifact(forged, root, sched, _discover)

    def test_freeze_binds_discovery(self):
        d, cov = self._cov([EV_MIA_BUF])
        ids = cov["process_event_ids"]
        binding = {**{k: d[k] for k in ("root_sha256", "root_bytes", "root_observed_at", "root_url",
                                        "discovery_sha256")},
                   "process_event_ids": ids, "process_event_ids_sha256": canonical_sha256(sorted(ids))}
        rec = _freeze_with(binding)
        self.assertEqual(rec["schema"], "fc-nfl-004-game-market-research-freeze-v2")
        self.assertEqual(rec["discovery"]["root_sha256"], hashlib.sha256(_root([EV_MIA_BUF])).hexdigest())
        with self.assertRaises(GameMarketFreezeError):
            _freeze_with({**binding, "process_event_ids": ["1"],
                          "process_event_ids_sha256": canonical_sha256(["1"])})
        with self.assertRaises(GameMarketFreezeError):
            _freeze_with({**binding, "process_event_ids_sha256": "0" * 64})
        with self.assertRaises(GameMarketFreezeError):
            _freeze_with({**binding, "root_observed_at": "2026-09-20T16:30:00Z"})  # after capture


def _freeze_with(discovery, **kw):
    from nfl.prospective.game_market_research_freeze import build_research_freeze, qb_state_from_schedule
    from nfl.tests.test_game_market_research_pathway import FROZEN, SCHED_SHA, _b0
    snap = _snapshot()
    return build_research_freeze(
        market_snapshot=snap, identity_binding=_binding(snap), b0_prediction=_b0(),
        qb_state=qb_state_from_schedule(SCHEDULE, "2026_03_MIA_BUF"), params=load_params(),
        code_sha="deadbeef", schedule_source={"url": "u", "sha256": SCHED_SHA},
        frozen_at=FROZEN, discovery=discovery, **kw)


def _scoreboard(**kw):
    body = json.dumps({"events": [_espn(**kw)]}).encode()
    return lambda day: (body, f"https://espn.test/{day}", "2026-09-20T21:00:00Z")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        snap1 = _snapshot()
        self.rec1 = _freeze(snapshot=snap1, binding=_binding(snap1))
        later_rows = [_record(m, captured_at="2026-09-20T16:40:00Z", **extra) for m, extra in (
            ("moneyline", {}), ("spread", {"home_line": -3.5, "away_line": 3.5}), ("game_total", {}))]
        snap2 = _snapshot(later_rows, sealed_at="2026-09-20T16:41:00Z")
        self.rec2 = _freeze(snapshot=snap2, binding=_binding(snap2), frozen_at="2026-09-20T16:42:00Z")
        # Two separate "runs" each hold one vintage.
        self.run_a, self.run_b = self.root / "a", self.root / "b"
        write_research_freeze(self.run_a, self.rec1)
        write_research_freeze(self.run_b, self.rec2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_then_grade_across_runs_with_later_vintage(self):
        ledger = self.root / "ledger"
        counts = merge_ledgers([self.run_a, self.run_b], ledger)
        self.assertEqual(counts["freeze"], 2)
        before = {p: p.read_bytes() for p in ledger.rglob("*.json")}
        after_game = datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc)
        reports = grade_ledger(ledger=ledger, fetch_scoreboard=_scoreboard(home=27, away=24), now=after_game)
        self.assertEqual(sorted(r["status"] for r in reports), ["GRADED", "GRADED"])
        for p, b in before.items():  # frozen records untouched
            self.assertEqual(p.read_bytes(), b)
        grades = sorted(ledger.rglob("*.grade.json"))
        first = json.loads(grades[0].read_text())
        self.assertEqual(first["freeze_record_sha256"], self.rec1["record_sha256"])
        self.assertEqual(first["later_snapshot_sha256"], self.rec2["market_snapshot_sha256"])
        home = next(s for s in first["selections"] if s["market"] == "spread" and s["side"] == "HOME")
        self.assertEqual(home["settlement"], "PUSH")
        self.assertEqual(home["later_vintage_comparison"]["line_movement_points"], 0.5)
        second = json.loads(grades[1].read_text())
        self.assertIsNone(second["later_snapshot_sha256"])  # latest vintage has no later reference
        # Idempotent: a second pass grades nothing new.
        self.assertEqual(grade_ledger(ledger=ledger, fetch_scoreboard=_scoreboard(), now=after_game), [])
        # Re-replay of the graded ledger into a fresh run dedupes byte-identically.
        nxt = self.root / "next"
        merge_ledgers([ledger], nxt)
        self.assertEqual(merge_ledgers([ledger], nxt)["identical_skipped"], 4)

    def test_pending_unavailable_and_terminal_dispositions(self):
        ledger = self.root / "ledger"
        merge_ledgers([self.run_a], ledger)
        early = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(grade_ledger(ledger=ledger, fetch_scoreboard=_scoreboard(), now=early)[0]["reason"],
                         "GAME_NOT_YET_COMPLETE")

        def down(day):
            raise RuntimeError("503")
        later = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(grade_ledger(ledger=ledger, fetch_scoreboard=down, now=later)[0]["status"], "PENDING")
        live = _scoreboard(status="STATUS_IN_PROGRESS", completed=False)
        self.assertEqual(grade_ledger(ledger=ledger, fetch_scoreboard=live, now=later)[0]["status"], "PENDING")
        week = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
        r = grade_ledger(ledger=ledger, fetch_scoreboard=live, now=week)[0]
        self.assertEqual((r["status"], r["reason"]), ("DISPOSED", "NOT_FINAL"))
        self.assertEqual(grade_ledger(ledger=ledger, fetch_scoreboard=live, now=week), [])

    def test_postponed_game_gets_explicit_disposition(self):
        ledger = self.root / "ledger"
        merge_ledgers([self.run_a], ledger)
        moved = _scoreboard(date="2026-09-22T00:15Z")
        r = grade_ledger(ledger=ledger, fetch_scoreboard=moved,
                         now=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc))[0]
        self.assertEqual(r["reason"], "KICKOFF_CHANGED")
        doc = json.loads(next(ledger.rglob("*.disposition.json")).read_text())
        self.assertEqual(doc["disposition"], "KICKOFF_CHANGED_NOT_GRADED")

    def test_replay_conflict_and_tamper_fail_closed(self):
        path = next(self.run_a.rglob("*.json"))
        conflict = self.root / "conflict"
        target = conflict / path.relative_to(self.run_a)
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(self.rec2, indent=2, sort_keys=True) + "\n")
        with self.assertRaises(LedgerLifecycleError):
            merge_ledgers([self.run_a, conflict], self.root / "ledger")
        tampered = self.root / "tampered"
        t = tampered / path.relative_to(self.run_a)
        t.parent.mkdir(parents=True)
        bad = deepcopy(self.rec1)
        bad["selections"][0]["decision"] = "RESEARCH_LEAN"
        t.write_text(json.dumps(bad))
        with self.assertRaises(GameMarketFreezeError):
            merge_ledgers([tampered], self.root / "ledger2")

    def test_grading_identity_recheck(self):
        bad = deepcopy(self.rec1)
        sel = next(s for s in bad["selections"] if s["market"] == "spread")
        sel["grading_identity"]["american_odds"] = 150
        body = {k: v for k, v in bad.items() if k != "record_sha256"}
        bad["record_sha256"] = canonical_sha256(body)  # internally consistent forgery
        from nfl.tests.test_game_market_research_pathway import _outcome
        with self.assertRaises(GameMarketResearchGradeError):
            grade_research_freeze(bad, _outcome(_binding(_snapshot())), graded_at="2026-09-20T21:05:00Z")


def _schedule_csv(rows):
    cols = ["game_id", "season", "game_type", "week", "gameday", "gametime", "away_team", "away_score",
            "home_team", "home_score", "result", "total", "espn", "away_qb_id", "home_qb_id"]
    lines = [",".join(cols)] + [",".join(str(r.get(c, "")) for c in cols) for r in rows]
    return ("\n".join(lines) + "\n").encode()


class RealEvidenceReplayTests(unittest.TestCase):
    """The committed 2026-10-12 freezes replay byte-for-byte under current code."""

    def test_committed_freezes_replay_and_remain_gradable(self):
        if not REAL_LEDGER.exists():
            self.skipTest("no committed research ledger")
        with tempfile.TemporaryDirectory() as tmp:
            counts = merge_ledgers([REAL_LEDGER], Path(tmp))
            self.assertGreaterEqual(counts["freeze"], 15)
            for src in REAL_LEDGER.rglob("*.json"):
                if src.parent.name != "grades":
                    self.assertEqual((Path(tmp) / src.relative_to(REAL_LEDGER)).read_bytes(), src.read_bytes())
            # v1 records (no discovery binding) still load and expose grading identity.
            v1 = [load_research_freeze(p) for p in REAL_LEDGER.rglob("*.json") if p.parent.name != "grades"]
            self.assertTrue(any(r["schema"].endswith("-v1") for r in v1))
            for r in v1:
                for s in r["selections"]:
                    if "grading_identity" in s:
                        self.assertEqual(s["grading_identity"]["event_id"], r["event_id"])


if __name__ == "__main__":
    unittest.main()
