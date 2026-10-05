#!/usr/bin/env python3
"""Deterministic synthetic tests for preregistration v3 (no live network, no outcomes).

External effects are replaced only by monkeypatching module functions inside tests;
the production code paths have no injectable trust parameters. RFC 3161 cryptography
is exercised against the REAL stored prereg-anchor tokens (FreeTSA + DigiCert)."""
from __future__ import annotations

import copy
import gzip
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import activation as AC  # noqa: E402
import capture as CP  # noqa: E402
import evaluate_v3 as EV  # noqa: E402
import harness as H  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import regimes as RG  # noqa: E402
import runner as RN  # noqa: E402
import schedule_plan as SP  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402
import verify_evidence as VE  # noqa: E402

COEF = H.load_coefficients()
D = "2099-04-01"
CUT = f"{D}T16:00:00+00:00"
START1, START2 = f"{D}T17:05:00Z", f"{D}T23:10:00Z"     # 13:05 ET (DAY), 19:10 ET (NIGHT)
PROV = {"git_sha": SH.SHADOW_PIN[:10], **SH.SHADOW_LABELS}
ANCHOR = json.load(open(os.path.join(HERE, "PREREG_ANCHOR.json")))


# ---- fixtures ------------------------------------------------------------------------------------
def rec(cid, *, name="Al Bat", game="1", player="10", team="Away Club", stat="hits", needs="1", line=0.5,
        side="over", odds=-120, p=0.62, rel="A", n=30, status="lean", qc="kept", assumed=False, prov=None):
    return {"candidate_id": cid, "game_pk": game, "player_id": player, "player_name": name, "team": team,
            "stat": stat, "needs": needs, "line": line, "market_side": side,
            "prediction": {"hit_probability": p, "reliability": rel, "sample_n": n},
            "market": {"market_odds": odds}, "eligibility": {"qc_status": qc, "lineup_assumed": assumed},
            "selector": {"recommendation_status": status}, "provenance": dict(prov or PROV)}


def board(records, *, generated=f"{D}T15:50:00+00:00", prov=None, date=D):
    b = {"date": date, "board_generated_at": generated, "sealed_at": generated, "provenance": dict(prov or PROV),
         "records": records}
    b["board_sha256"] = CP.canonical_sha256(b)
    return b


def game(pk, *, away="Away Club", home="Home Club", start=START1, gtype="R", probables=(50,), rosters="AUTO", tbd=False):
    return {"game_pk": int(pk), "game_type": gtype, "game_date": start, "away_team": away, "home_team": home,
            "double_header": "N", "game_number": 1, "probable_pitcher_ids": list(probables),
            "start_time_tbd": tbd, "rosters": rosters}


NO_ROSTERS = {"away": None, "home": None}


def autofill_rosters(schedule, records):
    """Fixture only: games declared rosters="AUTO" get active rosters built from the board's own
    records (each player on his board team), plus one unrelated filler per side."""
    for g in schedule["games"]:
        if g.get("rosters") != "AUTO":
            continue
        ro = {"away": [{"id": 900001, "name": "Filler Away"}], "home": [{"id": 900002, "name": "Filler Home"}]}
        seen = set()
        for r in records:
            if str(r["game_pk"]) != str(g["game_pk"]) or r["player_id"] in seen:
                continue
            side = "away" if r["team"] == g["away_team"] else "home" if r["team"] == g["home_team"] else None
            if side:
                ro[side].append({"id": int(r["player_id"]), "name": r["player_name"]})
                seen.add(r["player_id"])
        g["rosters"] = ro
    return schedule


def sched(*games, date=D):
    return {"date": date, "fetched_at": f"{date}T15:55:00+00:00", "games": list(games)}


def runner_(name, odds, *, status="ACTIVE", team="away_club", handicap=0, rtype=None, sel=1):
    return {"selection_id": sel, "runner_name": name, "runner_status": status, "handicap": handicap,
            "result_type": rtype, "team_slug": team, "american": odds}


def market(mtype, runners, *, mid="m1", status="OPEN", in_play=False, **kw):
    m = {"market_id": mid, "market_type": mtype, "market_name": mtype, "market_status": status,
         "in_play": in_play, "runners": runners}
    m.update(kw)                                   # e.g. event_id=... for foreign-attachment tests
    return m


def event(eid, markets, *, name="Away Club (P A) @ Home Club (P B)", open_date=START1, tabs_ok=True,
          completed=f"{D}T15:58:00+00:00", status=None, date=D):
    tabs = {t: "OK" for t in CP.TABS}
    if not tabs_ok:
        tabs["batter-props"] = "FAILED: RuntimeError: all FanDuel hosts failed"
    for m in markets:
        m.setdefault("event_id", eid)
    return {"event_id": eid, "event_name": name, "open_date": open_date, "fetch_started_at": f"{date}T15:57:00+00:00",
            "fetch_completed_at": completed, "tabs": tabs, "markets": markets,
            "status": status or ("COMPLETE" if tabs_ok else "PARTIAL")}


def cap(*events, started=f"{D}T15:57:00+00:00", completed=f"{D}T15:58:30+00:00", book="fanduel"):
    c = {"capture_version": CP.CAPTURE_VERSION, "book": book, "capture_started_at": started,
         "capture_completed_at": completed, "discovery": {"status": "OK"}, "events": list(events),
         "status": "COMPLETE" if completed and all(e["status"] == "COMPLETE" for e in events) else "INCOMPLETE"}
    c["capture_sha256"] = CP.canonical_sha256(c)
    return c


HIT = "PLAYER_TO_RECORD_A_HIT"
FINAL = {"codedGameState": "F", "detailedState": "Final"}
IN_PROGRESS = {"codedGameState": "I", "detailedState": "In Progress"}
POSTPONED = {"codedGameState": "D", "detailedState": "Postponed"}
SUSPENDED = {"codedGameState": "T", "detailedState": "Suspended: Rain"}
CANCELLED = {"codedGameState": "C", "detailedState": "Cancelled"}
COMPLETED_EARLY = {"codedGameState": "F", "detailedState": "Completed Early: Rain"}


def build(records, capture, schedule, window="DAY", cutoff=CUT):
    return M3.build_manifest(board(records), capture, autofill_rosters(schedule, records), window=window, cutoff_utc=cutoff)


def why(m):
    return {r["candidate_id"]: r["exclusion_reason"] for r in m["rows"]}


# ---- quote identity --------------------------------------------------------------------------------
class QuoteIdentity(unittest.TestCase):
    def test_exact_offer_binds_all_identity_fields(self):
        r = build([rec("a")], cap(event(101, [market(HIT, [runner_("Al Bat", -120)])])), sched(game(1)))["rows"][0]
        self.assertTrue(r["eligible"], r["exclusion_reason"])
        q = r["quote"]
        for k in ("book", "capture_sha256", "event_id", "market_id", "market_type", "selection_id", "team_slug",
                  "side", "needs", "american"):
            self.assertIsNotNone(q[k], k)
        self.assertEqual((q["event_id"], q["american"], q["identity_proof"]),
                         (101, -120, "TEAM_SLUG+MLB_ACTIVE_ROSTER_UNIQUE_NAME_ID_TEAM"))

    def test_market_from_wrong_event_rejected(self):
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)], event_id=202)]))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "QUOTE_EVENT_MISMATCH")
        c2 = cap(event(101, [market(HIT, [runner_("Al Bat", -120)], event_id=None)]))
        self.assertEqual(why(build([rec("a")], c2, sched(game(1))))["a"], "QUOTE_EVENT_MISMATCH")

    def test_missing_event_market_selection_ids(self):
        e = event(None, [market(HIT, [runner_("Al Bat", -120)])])
        self.assertEqual(why(build([rec("a")], cap(e), sched(game(1))))["a"], "QUOTE_ID_MISSING")
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)], mid=None)]))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "QUOTE_ID_MISSING")
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120, sel=None)])]))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "QUOTE_ID_MISSING")

    def test_book_must_be_fanduel(self):
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)])]), book="otherbook")
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "QUOTE_BOOK_MISMATCH")

    def test_missing_team_slug_needs_roster_proof(self):
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120, team=None)])]))
        self.assertEqual(why(build([rec("a")], c, sched(game(1, rosters=NO_ROSTERS))))["a"], "QUOTE_IDENTITY_UNPROVEN")
        ok = {"away": [{"id": 10, "name": "Al Bat"}], "home": [{"id": 77, "name": "Other Guy"}]}
        r = build([rec("a")], c, sched(game(1, rosters=ok)))["rows"][0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["quote"]["identity_proof"], "MLB_ACTIVE_ROSTER_UNIQUE_NAME_ID_TEAM")
        twin = {"away": [{"id": 10, "name": "Al Bat"}], "home": [{"id": 78, "name": "Al Bat"}]}
        self.assertEqual(why(build([rec("a")], c, sched(game(1, rosters=twin))))["a"], "QUOTE_IDENTITY_UNPROVEN")
        other = {"away": [{"id": 99, "name": "Al Bat"}], "home": []}
        self.assertEqual(why(build([rec("a")], c, sched(game(1, rosters=other))))["a"], "QUOTE_IDENTITY_UNPROVEN")

    def test_doubleheader_collision_fails_closed(self):
        g1, g2 = game(1, start=f"{D}T17:05:00Z"), game(2, start=f"{D}T18:00:00Z")
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)])], open_date=f"{D}T17:05:00Z"),
                event(102, [market(HIT, [runner_("Al Bat", -120)], mid="m2")], open_date=f"{D}T18:00:00Z"))
        self.assertEqual(why(build([rec("a"), rec("b", game="2", player="11")], c, sched(g1, g2)))["a"],
                         "EVENT_MAPPING_AMBIGUOUS")

    def test_doubleheader_separated_in_time_maps_uniquely(self):
        g1, g2 = game(1, start=f"{D}T17:05:00Z"), game(2, start=f"{D}T21:10:00Z")
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -150)])], open_date=f"{D}T17:05:00Z"),
                event(102, [market(HIT, [runner_("Al Bat", -120)], mid="m2")], open_date=f"{D}T21:10:00Z"))
        self.assertEqual(why(build([rec("a", odds=-120)], c, sched(g1, g2)))["a"], "PRICE_MISMATCH_BOARD_VS_CAPTURE")

    def test_same_player_stat_price_in_another_game_never_matches(self):
        c = cap(event(101, [market(HIT, [runner_("Someone Else", -120)])]),
                event(102, [market(HIT, [runner_("Al Bat", -120)], mid="m2")], name="Other A (x) @ Other B (y)"))
        s = sched(game(1), game(2, away="Other A", home="Other B"))
        self.assertEqual(why(build([rec("a")], c, s))["a"], "MARKET_ABSENT")

    def test_wrong_event_mapping(self):
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)])], name="Wrong Club (x) @ Home Club (y)"))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "EVENT_NOT_MAPPED")

    def test_team_line_side_status_and_ambiguity(self):
        k = "PITCHER_A_TOTAL_STRIKEOUTS"
        recs = [rec("team", name="T M", player="20"), rec("kline", name="K P", player="50", stat="strikeouts", needs="5", line=4.5),
                rec("susp", name="S U", player="21"), rec("inplay", name="I P", player="22"),
                rec("plus", name="P L", player="23", odds="+100"), rec("amb", name="A M", player="24")]
        c = cap(event(101, [market(HIT, [runner_("T M", -120, team="home_club"), runner_("S U", -120, status="SUSPENDED", sel=3),
                                          runner_("P L", 100, sel=4), runner_("A M", -120, sel=10)]),
                            market(HIT, [runner_("A M", -125, sel=11)], mid="m10"),
                            market(HIT, [runner_("I P", -120, sel=5)], mid="m9", in_play=True),
                            market(k, [runner_("K P Over", -120, handicap=5.5, rtype="OVER", sel=6),
                                       runner_("K P Under", 100, handicap=5.5, rtype="UNDER", sel=7)], mid="mk")]))
        w = why(build(recs, c, sched(game(1))))
        self.assertEqual((w["team"], w["kline"], w["susp"], w["inplay"], w["amb"], w["plus"]),
                         ("QUOTE_TEAM_MISMATCH", "MARKET_ABSENT", "MARKET_SUSPENDED", "IN_PLAY", "QUOTE_AMBIGUOUS", None))

    def test_two_sided_devig_only_with_real_opposite(self):
        k = "PITCHER_A_TOTAL_STRIKEOUTS"
        c = cap(event(101, [market(k, [runner_("K P Over", -120, handicap=4.5, rtype="OVER", sel=6),
                                       runner_("K P Under", 100, handicap=4.5, rtype="UNDER", sel=7)], mid="mk"),
                            market("PITCHER_B_OUTS_RECORDED_SB", [runner_("O P Over 15.5", -110, sel=8)], mid="mo")]))
        recs = [rec("k", name="K P", player="50", stat="strikeouts", needs="5", line=4.5),
                rec("o", name="O P", player="51", stat="pitcher_outs", needs="16", line=15.5, odds=-110)]
        rows = {r["candidate_id"]: r for r in build(recs, c, sched(game(1, probables=(50, 51))))["rows"]}
        a, b = M3.implied(-120), M3.implied(100)
        self.assertAlmostEqual(rows["k"]["quote"]["q_devig"], a / (a + b))
        self.assertEqual(rows["o"]["quote"]["devig"], "ONE_SIDED_NO_DEVIG")

    def test_starter_must_be_mlb_probable_and_is_never_called_confirmed(self):
        k = "PITCHER_A_TOTAL_STRIKEOUTS"
        c = cap(event(101, [market(k, [runner_("K P Over", -120, handicap=4.5, rtype="OVER")], mid="mk")]))
        r = build([rec("k", name="K P", player="99", stat="strikeouts", needs="5", line=4.5)], c, sched(game(1)))["rows"][0]
        self.assertEqual((r["exclusion_reason"], r["starter_status"]), ("STARTER_FAIL", "NOT_MLB_PROBABLE"))


# ---- blocker 1 (Codex 5941258168): exact player identity on the TEAM-SLUG path ----------------------
class TeamSlugIdentity(unittest.TestCase):
    """The runner shows the correct team slug in every case; only the roster proof decides."""
    def q(self, rosters, player="10", name="Al Bat", runner_name="Al Bat"):
        c = cap(event(101, [market(HIT, [runner_(runner_name, -120)])]))
        return build([rec("a", player=player, name=name)], c, sched(game(1, rosters=rosters)))["rows"][0]

    def test_same_normalized_name_same_team_different_ids_rejected(self):
        r = self.q({"away": [{"id": 10, "name": "Al Bat"}, {"id": 11, "name": "Al Bat"}], "home": [{"id": 77, "name": "X Y"}]})
        self.assertEqual(r["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")

    def test_suffix_normalized_teammate_collision_rejected(self):
        r = self.q({"away": [{"id": 10, "name": "Al Bat"}, {"id": 12, "name": "Al Bat Jr."}], "home": [{"id": 77, "name": "X Y"}]})
        self.assertEqual(r["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")
        r = self.q({"away": [{"id": 10, "name": "Al Bat II"}, {"id": 12, "name": "Al Bat"}], "home": [{"id": 77, "name": "X Y"}]},
                   name="Al Bat II", runner_name="Al Bat II")
        self.assertEqual(r["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")

    def test_correct_slug_but_wrong_or_stale_player_id_rejected(self):
        r = self.q({"away": [{"id": 99, "name": "Al Bat"}], "home": [{"id": 77, "name": "X Y"}]})
        self.assertEqual(r["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")

    def test_correct_slug_and_correct_player_id_succeeds(self):
        r = self.q({"away": [{"id": 10, "name": "Al Bat"}], "home": [{"id": 77, "name": "X Y"}]})
        self.assertTrue(r["eligible"], r["exclusion_reason"])
        self.assertEqual(r["quote"]["identity_proof"], "TEAM_SLUG+MLB_ACTIVE_ROSTER_UNIQUE_NAME_ID_TEAM")

    def test_ambiguous_or_missing_or_wrong_side_roster_rejected(self):
        both = {"away": [{"id": 10, "name": "Al Bat"}], "home": [{"id": 78, "name": "Al Bat"}]}
        self.assertEqual(self.q(both)["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")           # cross-team twin
        self.assertEqual(self.q(NO_ROSTERS)["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")     # no roster
        wrong_side = {"away": [{"id": 77, "name": "X Y"}], "home": [{"id": 10, "name": "Al Bat"}]}
        self.assertEqual(self.q(wrong_side)["exclusion_reason"], "QUOTE_IDENTITY_UNPROVEN")     # id on the other team


# ---- blocker 2: TBD first pitch -----------------------------------------------------------------------
class TbdFirstPitch(unittest.TestCase):
    def unit(self, g2, *, evs2=True):
        recs = [rec("t", name="Timed Guy"), rec("x", name="Tbd Guy", game="2", player="11", team="Two Away")]
        e1 = event(101, [market(HIT, [runner_("Timed Guy", -120)])])
        e2 = event(102, [market(HIT, [runner_("Tbd Guy", -120, team="two_away")], mid="m2")],
                   name="Two Away (x) @ Two Home (y)", open_date=g2["game_date"])
        s = sched(game(1, start=f"{D}T17:05:00Z"), g2)
        return build(recs, cap(e1, e2) if evs2 else cap(e1), s), s

    def test_tbd_placeholder_earlier_than_timed_game(self):
        m, s = self.unit(game(2, away="Two Away", home="Two Home", start=f"{D}T16:33:00Z", tbd=True))
        self.assertEqual(why(m)["x"], "FIRST_PITCH_TBD_NOT_TIMED")
        self.assertTrue(m["rows"][0]["eligible"] if m["rows"][0]["candidate_id"] == "t" else m["rows"][1]["eligible"])
        self.assertEqual(m["covered_games"], ["1"])
        self.assertEqual(m["earliest_first_pitch_utc"], f"{D}T17:05:00Z")
        self.assertEqual(SP.unit_first_pitch(s, "DAY"), m["earliest_first_pitch_utc"])
        self.assertTrue(SP.seal_deadline_consistent(m, s, "DAY"))

    def test_tbd_placeholder_later_than_timed_game(self):
        m, _ = self.unit(game(2, away="Two Away", home="Two Home", start=f"{D}T19:40:00Z", tbd=True))
        self.assertEqual((why(m)["x"], m["covered_games"]), ("FIRST_PITCH_TBD_NOT_TIMED", ["1"]))

    def test_doubleheader_timed_plus_tbd(self):
        recs = [rec("g1", name="Al Bat"), rec("g2", name="Al Bat", game="2")]
        e1 = event(101, [market(HIT, [runner_("Al Bat", -120)])], open_date=f"{D}T17:05:00Z")
        s = sched(game(1, start=f"{D}T17:05:00Z"), game(2, start=f"{D}T20:05:00Z", tbd=True))   # both DAY
        m = build(recs, cap(e1), s)
        self.assertEqual(why(m), {"g1": None, "g2": "FIRST_PITCH_TBD_NOT_TIMED"})
        self.assertEqual(m["covered_games"], ["1"])
        s2 = sched(game(1, start=f"{D}T17:05:00Z"), game(2, start=f"{D}T23:10:00Z", tbd=True))  # TBD placeholder NIGHT
        night = build(recs, cap(e1), s2, window="NIGHT")
        self.assertEqual((why(night), night["covered_games"], night["earliest_first_pitch_utc"]),
                         ({"g2": "FIRST_PITCH_TBD_NOT_TIMED"}, [], None))

    def test_runner_refuses_to_seal_unit_whose_only_board_game_is_tbd(self):
        out = tempfile.mkdtemp()
        day = (datetime.now(timezone.utc) + __import__("datetime").timedelta(days=1)).date().isoformat()
        timed, tbd = f"{day}T17:05:00Z", f"{day}T16:33:00Z"          # timed game has no board records
        sch = sched(game(1, start=timed), game(2, away="Two Away", home="Two Home", start=tbd, tbd=True), date=day)
        recs = [rec("x", name="Tbd Guy", game="2", player="11", team="Two Away")]
        b = board(recs, generated=datetime.now(timezone.utc).isoformat(), date=day)
        bp = os.path.join(out, "b.json")
        json.dump(b, open(bp, "w"))

        def fake_pipeline(tree, tape, mode, **k):
            open(tape, "wb").write(b"tape")
            json.dump({"runtime_image": {"manifest_digest": "sha256:x", "rootfs_tree_sha256": "y"}},
                      open(tape + ".record.env.json", "w"))         # FC-MLB-001B record fingerprint (stub)
            return bp
        c = cap(event(102, [market(HIT, [runner_("Tbd Guy", -120, team="two_away")], mid="m2")],
                      name="Two Away (x) @ Two Home (y)", open_date=tbd, completed=datetime.now(timezone.utc).isoformat()),
                started=datetime.now(timezone.utc).isoformat(), completed=datetime.now(timezone.utc).isoformat())
        try:
            with mock.patch.object(RN, "schedule_snapshot", lambda d: autofill_rosters(sch, recs)), \
                    mock.patch.object(SH, "build_tree", lambda *a, **k: {"overlay": {}}), \
                    mock.patch.object(SH, "run_pipeline_b", fake_pipeline), \
                    mock.patch.dict(os.environ, {"V3B_TAPE_STORE": "localfs:" + os.path.join(out, "store")}), \
                    mock.patch.object(SH, "remove_tree", lambda *a: None), \
                    mock.patch.object(CP, "capture", lambda: c):
                rc = RN.main(["--mode", "drill", "--repo", "/nonexistent", "--date", day, "--window", "DAY", "--out", out])
            self.assertEqual(rc, 4)
            self.assertIn("no timed covered game", json.load(open(os.path.join(out, "MISSED_UNIT.json")))["reason"])
            self.assertFalse(os.path.exists(os.path.join(out, "manifest.json.gz")))
        finally:
            shutil.rmtree(out)

    def test_tbd_game_never_covered_and_unsafe_unit_not_sealable(self):
        m, s = self.unit(game(2, away="Two Away", home="Two Home", start=f"{D}T16:33:00Z", tbd=True))
        self.assertNotIn("2", m["covered_games"])
        only_tbd = sched(game(2, away="Two Away", home="Two Home", start=f"{D}T16:33:00Z", tbd=True))
        m2 = build([rec("x", name="Tbd Guy", game="2", player="11", team="Two Away")],
                   cap(event(102, [market(HIT, [runner_("Tbd Guy", -120, team="two_away")], mid="m2")],
                             name="Two Away (x) @ Two Home (y)", open_date=f"{D}T16:33:00Z")), only_tbd)
        self.assertEqual((m2["covered_games"], m2["earliest_first_pitch_utc"]), ([], None))
        self.assertIsNone(SP.unit_first_pitch(only_tbd, "DAY"))
        self.assertFalse(SP.seal_deadline_consistent(m2, only_tbd, "DAY"))
        bad = dict(m, earliest_first_pitch_utc=f"{D}T16:00:00Z")              # deadline earlier than planned
        self.assertFalse(SP.seal_deadline_consistent(bad, s, "DAY"))
        self.assertFalse(SP.seal_deadline_consistent(dict(m, covered_games=[]), s, "DAY"))   # inconsistent manifest

    def test_once_timed_normal_behaviour(self):
        m, s = self.unit(game(2, away="Two Away", home="Two Home", start=f"{D}T16:33:00Z", tbd=False))
        self.assertEqual(why(m)["x"], None)
        self.assertEqual(m["covered_games"], ["1", "2"])
        self.assertEqual(m["earliest_first_pitch_utc"], f"{D}T16:33:00Z")
        self.assertEqual(SP.unit_first_pitch(s, "DAY"), f"{D}T16:33:00Z")
        self.assertTrue(SP.seal_deadline_consistent(m, s, "DAY"))


class CaptureCompleteness(unittest.TestCase):
    def ev(self, **kw):
        return event(101, [market(HIT, [runner_("Al Bat", -120)])], **kw)

    def test_capture_starts_before_cutoff_ends_after(self):
        c = cap(self.ev(), started=f"{D}T15:59:00+00:00", completed=f"{D}T16:00:30+00:00")
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "CAPTURE_COMPLETED_AFTER_CUTOFF")

    def test_partial_capture_is_not_market_absence(self):
        self.assertEqual(why(build([rec("a")], cap(event(101, [], tabs_ok=False)), sched(game(1))))["a"], "EVENT_NOT_OBSERVED")
        self.assertEqual(why(build([rec("a")], cap(event(101, [])), sched(game(1))))["a"], "MARKET_ABSENT")

    def test_failed_event_fetch_and_unfinished_capture(self):
        self.assertEqual(why(build([rec("a")], cap(event(101, [], status="NOT_FETCHED_BUDGET")), sched(game(1))))["a"],
                         "EVENT_NOT_OBSERVED")
        self.assertEqual(why(build([rec("a")], cap(self.ev(), completed=None), sched(game(1))))["a"], "CAPTURE_INCOMPLETE")

    def test_quote_age_ceiling(self):
        self.assertEqual(why(build([rec("a")], cap(self.ev(), completed=f"{D}T15:14:59+00:00"), sched(game(1))))["a"],
                         "QUOTE_STALE")

    def test_tampered_capture_is_refused_by_the_manifest_builder(self):
        c = cap(self.ev())
        c["events"][0]["markets"][0]["runners"][0]["american"] = -110      # hash no longer rebuilds
        with self.assertRaises(ValueError):
            build([rec("a")], c, sched(game(1)))


class Universe(unittest.TestCase):
    def test_gates_and_window(self):
        recs = [rec("relC", name="R C", player="1", rel="C"), rec("lin", name="L A", player="2", assumed=True),
                rec("qc", name="Q C", player="3", qc="lineup_assumed_holdout"), rec("n0", name="N Z", player="4", n=0),
                rec("dup1", name="D U", player="5"), rec("dup2", name="D U", player="5"),
                rec("combo", name="C K", player="6", stat="combined_strikeouts"),
                rec("night", name="N I", player="7", game="2"), rec("band", name="B A", player="8", odds=-400)]
        c = cap(event(101, [market(HIT, [runner_(n, -400 if n == "B A" else -120, sel=i + 1)
                                          for i, n in enumerate(["R C", "L A", "Q C", "N Z", "D U", "C K", "B A"])])]),
                event(102, [market(HIT, [runner_("N I", -120)], mid="mn")], name="Night A (x) @ Night B (y)", open_date=START2))
        m = build(recs, c, sched(game(1), game(2, away="Night A", home="Night B", start=START2)))
        self.assertEqual(why(m), {"relC": "RELIABILITY_FAIL", "lin": "LINEUP_FAIL", "qc": "QC_FAIL", "n0": "SAMPLE_N_FAIL",
                                  "dup1": "DUPLICATE_IDENTITY_FAIL", "dup2": "DUPLICATE_IDENTITY_FAIL",
                                  "combo": "FAMILY_RED_FLAG_SEPARATE", "band": "PRICE_BAND_FAIL"})
        self.assertNotIn("2", m["covered_games"])

    def test_no_outcome_input_and_hash(self):
        self.assertEqual(list(inspect.signature(M3.build_manifest).parameters),
                         ["shadow_board", "capture", "schedule", "window", "cutoff_utc", "shadow_provenance"])
        m = build([rec("a")], cap(event(101, [market(HIT, [runner_("Al Bat", -120)])])), sched(game(1)))
        M3.verify_manifest(m)
        t = copy.deepcopy(m)
        t["rows"][0]["shadow_champion"] = True
        with self.assertRaises(ValueError):
            M3.verify_manifest(t)


# ---- shadow champion & sealed overlay ------------------------------------------------------------
class ShadowInputs(unittest.TestCase):
    def test_independent_of_live_production_version(self):
        SH.verify_shadow_board(board([rec("a")]))
        for prov in ({"git_sha": "abcdef1234", "model_version": "2027.01.15", "selection_policy_version": "2.0.0",
                      "calibration_version": "2.0.0", "feature_version": "2.0.0"},
                     dict(PROV, git_sha="0123456789"), dict(PROV, model_version="2027.01.15")):
            with self.assertRaises(ValueError):
                SH.verify_shadow_board(board([rec("a", prov=prov)], prov=prov))

    def overlay(self, *stamps):
        return json.dumps({"date": D, "snapshots": [{"taken_at": t, "rows": []} for t in stamps]}).encode()

    def test_post_cutoff_and_future_end_rows_are_not_consumed(self):
        cut = datetime(2099, 4, 1, 16, tzinfo=timezone.utc)
        sealed, rep = SH.filter_overlay(self.overlay(f"{D}T10:00:00+00:00", f"{D}T15:59:59+00:00",
                                                      f"{D}T16:00:01+00:00", "2099-12-31T00:00:00+00:00", "garbage"), cut)
        kept = [s["taken_at"] for s in json.loads(sealed)["snapshots"]]
        self.assertEqual(kept, [f"{D}T10:00:00+00:00", f"{D}T15:59:59+00:00"])     # last row is pre-cutoff
        self.assertEqual(rep["latest_kept_taken_at"], f"{D}T15:59:59+00:00")
        self.assertEqual(len(rep["dropped_post_cutoff_or_invalid"]), 3)

    def test_replay_uses_sealed_overlay_bytes_not_current_data_odds(self):
        tmp = tempfile.mkdtemp()
        try:
            target = os.path.join(tmp, SH.LIVE_OVERLAY_TEMPLATE.format(date=D))
            os.makedirs(os.path.dirname(target))
            open(target, "wb").write(b"CURRENT MAIN BYTES (changed after the seal)")
            SH.install_sealed_overlay(tmp, D, b"SEALED BYTES")
            self.assertEqual(open(target, "rb").read(), b"SEALED BYTES")
            SH.install_sealed_overlay(tmp, D, None)                  # none sealed -> none present (pin: {} )
            self.assertFalse(os.path.exists(target))
        finally:
            shutil.rmtree(tmp)

    def test_replay_equivalence_ignores_only_run_timestamps(self):
        a = board([rec("a"), rec("b", player="11")])
        b = copy.deepcopy(a)
        b["board_generated_at"] = b["sealed_at"] = "2099-04-01T15:51:00+00:00"
        for r in b["records"]:
            r["generation_timestamp"] = "x"
        b["board_sha256"] = "changed"
        self.assertTrue(SH.replay_equivalent(a, b))
        b["records"][1]["prediction"]["hit_probability"] = 0.63
        self.assertFalse(SH.replay_equivalent(a, b))


# ---- RFC 3161 (real tokens) -------------------------------------------------------------------------
class TimestampAuthority(unittest.TestCase):
    H_ = ANCHOR["prereg_sha256"]

    def test_real_tokens_verify_and_genTime_comes_from_token(self):
        for n, t in ANCHOR["tsa"].items():
            g = SL.tsa_check(self.H_, t["token_b64"], n)
            self.assertEqual(g, datetime(2026, 10, 1, 19, 23, 48, tzinfo=timezone.utc), n)

    def test_wrong_imprint_wrong_body_bad_signature_missing_unknown(self):
        t = ANCHOR["tsa"]["freetsa"]["token_b64"]
        import base64
        raw = bytearray(base64.b64decode(t))
        raw[-20] ^= 0xFF                                            # signature bytes corrupted
        bad_sig = base64.b64encode(bytes(raw)).decode()
        self.assertIsNone(SL.tsa_check("0" * 64, t, "freetsa"))     # wrong message imprint
        self.assertIsNone(SL.tsa_check(self.H_, bad_sig, "freetsa"))
        self.assertIsNone(SL.tsa_check(self.H_, base64.b64encode(b"not a token").decode(), "freetsa"))
        self.assertIsNone(SL.tsa_check(self.H_, None, "freetsa"))
        self.assertIsNone(SL.tsa_check(self.H_, t, "unlisted_tsa"))
        self.assertIsNone(SL.tsa_check(self.H_, t, "digicert"))      # FreeTSA token under DigiCert trust


# ---- evidence ref -----------------------------------------------------------------------------------
GH_ISSUE = SL.ISSUE_91_API_URL


def make_unit(root, *, date="2027-05-04", gtype="R", n=8, champs=2, prev=None, overlay_rows=None, mutate=None,
              challenger=VE.FROZEN_COEFFICIENTS_SHA256):
    """A complete sealed unit on a synthetic evidence ref (no network)."""
    start = f"{date}T17:05:00Z"
    recs = [rec(f"{date}-{i}", name=f"P{i} X", player=str(i), game=str(i % 4 + 1), team=f"A{i % 4 + 1}",
                p=0.55 + 0.02 * i, odds=-110 - 10 * i, status="top_pick" if i < champs else "lean") for i in range(n)]
    games = [game(g, away=f"A{g}", home=f"H{g}", start=start, gtype=gtype) for g in range(1, 5)]
    evs = [event(100 + g, [market(HIT, [runner_(f"P{i} X", -110 - 10 * i, team=f"a{g}", sel=i + 1)
                                         for i in range(n) if i % 4 + 1 == g], mid=f"m{g}")],
                 name=f"A{g} (x) @ H{g} (y)", open_date=start, completed=f"{date}T15:58:00+00:00", date=date)
           for g in range(1, 5)]
    b = board(recs, generated=f"{date}T15:50:00+00:00", date=date)
    s = autofill_rosters(sched(*games, date=date), recs)
    c = cap(*evs, started=f"{date}T15:57:00+00:00", completed=f"{date}T15:58:30+00:00")
    overlay = json.dumps({"snapshots": overlay_rows if overlay_rows is not None
                          else [{"taken_at": f"{date}T12:00:00+00:00", "rows": []}]}).encode()
    tape = b"synthetic tape bytes " + date.encode()
    prov = {"shadow_id": SH.SHADOW_ID, "overlay": {"status": "SEALED", "sealed_sha256": hashlib.sha256(overlay).hexdigest(),
                                                    "overlay_cutoff": f"{date}T15:40:00+00:00"},
            "tape_sha256": hashlib.sha256(tape).hexdigest()}
    m = M3.build_manifest(b, c, s, window="DAY", cutoff_utc=f"{date}T16:00:00+00:00", shadow_provenance=prov)
    if mutate:
        mutate(m)
    unit = f"{date}_DAY"
    d = os.path.join(root, "seals", unit)
    os.makedirs(d)
    files = {"shadow_board.json.gz": b, "capture.json.gz": c, "schedule.json": s, "manifest.json.gz": m}
    for name, obj in files.items():
        data = json.dumps(obj, sort_keys=True).encode()
        with (gzip.GzipFile(os.path.join(d, name), "wb", mtime=0) if name.endswith(".gz") else open(os.path.join(d, name), "wb")) as fh:
            fh.write(data)
    open(os.path.join(d, "overlay.json"), "wb").write(overlay)
    open(os.path.join(d, "shadow_tape.json.gz"), "wb").write(tape)
    arts = {nm: SH.sha256_file(os.path.join(d, nm)) for nm in list(files) + ["overlay.json", "shadow_tape.json.gz"]}
    chain = json.load(open(os.path.join(root, "CHAIN.json")))
    sl = SL.build_seal(m, prev_seal_sha256=chain[-1]["seal_sha256"], prereg_sha256=VE.PREREG_SHA256,
                       challenger_sha256=challenger, shadow_id=SH.SHADOW_ID, created_at=f"{date}T16:00:01+00:00",
                       artifacts_sha256=arts)
    json.dump(sl, open(os.path.join(d, "seal.json"), "w"))
    json.dump({"github_comment_id": 4242, "tsa": [{"tsa": "freetsa", "token_b64": "TOKEN:" + sl["seal_sha256"],
                                                    "verified": True, "gen_time": "2000-01-01T00:00:00Z"}]},
              open(os.path.join(d, "receipts.json"), "w"))
    chain.append({"index": len(chain), "unit": unit, "seal_sha256": sl["seal_sha256"]})
    json.dump(chain, open(os.path.join(root, "CHAIN.json"), "w"))
    return sl, m, b


def new_root():
    root = tempfile.mkdtemp(prefix="v3ev_")
    json.dump([{"index": 0, "unit": "GENESIS", "seal_sha256": VE.GENESIS_SEAL_SHA256}], open(os.path.join(root, "CHAIN.json"), "w"))
    return root


class Externals:
    """Simulated GitHub + TSA + replay for synthetic evidence. TSA 'validity' here mirrors tsa_check's
    contract (token covers the digest); the real cryptography is tested in TimestampAuthority."""
    def __init__(self, gh_at="T16:01:00Z", tsa_at="T16:01:05+00:00", issue=GH_ISSUE, body_ok=True, replay_ok=True,
                 gh_raises=False, tsa_ok=True):
        self.__dict__.update(locals())

    def comment(self, cid):
        if self.gh_raises:
            raise VE.EvidenceUnverified("simulated GitHub outage")
        seal = self.current_seal
        return {"id": cid, "issue_url": self.issue, "created_at": seal["date"] + self.gh_at,
                "body": SL.receipt_comment_body(seal) if self.body_ok else "something else"}

    def tsa(self, digest, token, name):
        if self.tsa_ok and token == "TOKEN:" + digest:
            return datetime.fromisoformat(self.current_seal["date"] + self.tsa_at)
        return None

    def patch(self, seal):
        self.current_seal = seal
        return mock.patch.multiple(VE, fetch_issue_comment=self.comment,
                                   replay_shadow=lambda *a, **k: self.replay_ok), mock.patch.object(SL, "tsa_check", self.tsa)


def verify(root, seal, ext=None):
    ext = ext or Externals()
    p1, p2 = ext.patch(seal)
    with p1, p2:
        return VE.verify_unit(root, seal)


class EvidenceVerifier(unittest.TestCase):
    def setUp(self):
        self.root = new_root()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_happy_path_and_caller_flags_ignored(self):
        sl, m, _ = make_unit(self.root)
        self.assertEqual([s["seal_sha256"] for s in VE.load_chain(self.root)], [sl["seal_sha256"]])
        st, detail, man = verify(self.root, sl)
        self.assertEqual(st, "VERIFIED", detail)
        self.assertEqual(man["manifest_sha256"], m["manifest_sha256"])
        # stored "verified": True / gen_time 2000 are ignored: a token that does not cover the hash fails
        rc = json.load(open(os.path.join(self.root, "seals", "2027-05-04_DAY", "receipts.json")))
        rc["tsa"][0]["token_b64"] = "forged"
        json.dump(rc, open(os.path.join(self.root, "seals", "2027-05-04_DAY", "receipts.json"), "w"))
        self.assertEqual(verify(self.root, sl)[0], "MISSING_EXTERNAL_RECEIPT")

    def test_no_chain_no_evaluation(self):
        os.remove(os.path.join(self.root, "CHAIN.json"))
        with self.assertRaises(VE.EvidenceError):
            VE.load_chain(self.root)
        self.assertEqual(list(inspect.signature(EV.evaluate_from_evidence).parameters), ["evidence_root", "regime"])
        self.assertFalse(hasattr(EV, "evaluate"))

    def test_fake_or_wrong_or_late_github_receipt(self):
        sl, _, _ = make_unit(self.root)
        self.assertEqual(verify(self.root, sl, Externals(issue=GH_ISSUE.replace("/91", "/92")))[0], "MISSING_EXTERNAL_RECEIPT")
        self.assertEqual(verify(self.root, sl, Externals(body_ok=False))[0], "MISSING_EXTERNAL_RECEIPT")
        self.assertEqual(verify(self.root, sl, Externals(gh_at="T17:06:00Z"))[0], "LATE_SEAL")
        with self.assertRaises(VE.EvidenceUnverified):
            verify(self.root, sl, Externals(gh_raises=True))

    def test_invalid_or_late_tsa(self):
        sl, _, _ = make_unit(self.root)
        self.assertEqual(verify(self.root, sl, Externals(tsa_ok=False))[0], "MISSING_EXTERNAL_RECEIPT")
        self.assertEqual(verify(self.root, sl, Externals(tsa_at="T17:06:00+00:00"))[0], "LATE_SEAL")

    def test_one_valid_and_one_invalid_token_is_accepted_and_both_recorded(self):
        sl, _, _ = make_unit(self.root)
        p = os.path.join(self.root, "seals", "2027-05-04_DAY", "receipts.json")
        rc = json.load(open(p))
        rc["tsa"].append({"tsa": "digicert", "token_b64": "garbage"})
        json.dump(rc, open(p, "w"))
        st, detail, _ = verify(self.root, sl)
        self.assertEqual(st, "VERIFIED")
        self.assertEqual(detail["tsa_invalid"], ["digicert"])

    def test_rewritten_deleted_reordered_or_unchained(self):
        sl1, _, _ = make_unit(self.root, date="2027-05-04")
        sl2, _, _ = make_unit(self.root, date="2027-05-05")
        VE.load_chain(self.root)
        p = os.path.join(self.root, "seals", "2027-05-04_DAY", "seal.json")
        orig = open(p).read()
        t = json.loads(orig)
        t["manifest_sha256"] = "f" * 64
        open(p, "w").write(json.dumps(t))
        with self.assertRaises(VE.EvidenceError):
            VE.load_chain(self.root)
        open(p, "w").write(orig)
        chain = json.load(open(os.path.join(self.root, "CHAIN.json")))
        json.dump([chain[0], dict(chain[2], index=1), dict(chain[1], index=2)], open(os.path.join(self.root, "CHAIN.json"), "w"))
        with self.assertRaises(VE.EvidenceError):
            VE.load_chain(self.root)
        json.dump(chain[:2], open(os.path.join(self.root, "CHAIN.json"), "w"))      # unit 2 left unchained
        with self.assertRaises(VE.EvidenceError):
            VE.load_chain(self.root)
        json.dump([dict(chain[0], seal_sha256="0" * 64)] + chain[1:], open(os.path.join(self.root, "CHAIN.json"), "w"))
        with self.assertRaises(VE.EvidenceError):                                   # not the anchored genesis
            VE.load_chain(self.root)

    def test_missing_or_altered_sealed_artifacts(self):
        sl, _, _ = make_unit(self.root)
        d = os.path.join(self.root, "seals", "2027-05-04_DAY")
        shutil.copy(os.path.join(d, "capture.json.gz"), os.path.join(d, "cap.bak"))
        os.remove(os.path.join(d, "capture.json.gz"))
        self.assertEqual(verify(self.root, sl)[0], "ARTIFACT_MISSING")
        shutil.move(os.path.join(d, "cap.bak"), os.path.join(d, "capture.json.gz"))
        open(os.path.join(d, "shadow_tape.json.gz"), "ab").write(b"x")
        self.assertEqual(verify(self.root, sl)[0], "ARTIFACT_HASH_MISMATCH")

    def test_quote_must_reproduce_from_sealed_capture(self):
        def flip(m):
            m["rows"][0]["eligible"] = not m["rows"][0]["eligible"]
            m["manifest_sha256"] = CP.canonical_sha256({k: v for k, v in m.items() if k != "manifest_sha256"})
        sl, _, _ = make_unit(self.root, mutate=flip)
        self.assertEqual(verify(self.root, sl)[0], "MANIFEST_NOT_REPRODUCIBLE")

    def test_overlay_post_cutoff_row_and_missing_overlay(self):
        sl, _, _ = make_unit(self.root, overlay_rows=[{"taken_at": "2027-05-04T15:50:00+00:00", "rows": []}])
        self.assertEqual(verify(self.root, sl)[0], "OVERLAY_POST_CUTOFF_ROW")       # after its 15:40 cutoff
        root2 = new_root()
        try:
            sl2, _, _ = make_unit(root2)
            d = os.path.join(root2, "seals", "2027-05-04_DAY")
            os.remove(os.path.join(d, "overlay.json"))
            self.assertEqual(verify(root2, sl2)[0], "ARTIFACT_MISSING")
        finally:
            shutil.rmtree(root2)

    def test_shadow_must_reproduce(self):
        sl, _, _ = make_unit(self.root)
        self.assertEqual(verify(self.root, sl, Externals(replay_ok=False))[0], "SHADOW_NOT_REPRODUCIBLE")


# ---- final evaluation: one look ------------------------------------------------------------------
class OneLook(unittest.TestCase):
    def setUp(self):
        self.root = new_root()
        self.sl, _, self.board = make_unit(self.root, date="2027-09-28")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_eval(self, now, terminal=True):
        ext = Externals()
        p1, p2 = ext.patch(self.sl)
        graded = []

        def grade(board):
            self.assertTrue(os.path.exists(os.path.join(self.root, "FINAL_ANALYSIS_2027_REGULAR_CONFIRMATORY.json")),
                            "outcomes opened before the one-look lock")
            graded.append(1)
            return {"source_board_sha256": board["board_sha256"],
                    "records": [{"candidate_id": r["candidate_id"], "grade": "hit"} for r in board["records"]]}

        def lock(root, rec_):
            json.dump(rec_, open(os.path.join(root, f"FINAL_ANALYSIS_{rec_['regime']}.json"), "w"))

        st = terminal if isinstance(terminal, dict) else (FINAL if terminal else IN_PROGRESS)
        states = (lambda gps: {str(g): st for g in gps})
        with p1, p2, mock.patch.multiple(EV, utc_now=lambda: now, publish_lock=lock, grade_shadow_board=grade,
                                         fetch_game_states=states):
            out = EV.evaluate_from_evidence(self.root, "2027_REGULAR_CONFIRMATORY")
        return out, graded

    def test_midseason_and_day_before_refused(self):
        for when in (datetime(2027, 7, 1, tzinfo=timezone.utc), datetime(2027, 10, 4, 23, 59, tzinfo=timezone.utc)):
            with self.assertRaises(EV.OneLookRefused):
                self.run_eval(when)

    def test_analysis_window_and_second_look_refused(self):
        with self.assertRaises(EV.OneLookRefused):     # 10-05 but games not all terminal and grace not elapsed
            self.run_eval(datetime(2027, 10, 5, 12, tzinfo=timezone.utc), terminal=False)
        out, graded = self.run_eval(datetime(2027, 10, 5, 12, tzinfo=timezone.utc))
        self.assertEqual((out["one_look_trigger"], out["n_slates_used"], len(graded)),
                         ("ALL_COVERED_GAMES_FINAL_UNDER_PINNED_GRADER", 1, 1))
        with self.assertRaises(EV.OneLookRefused):
            self.run_eval(datetime(2027, 10, 20, tzinfo=timezone.utc))

    def test_unverified_unit_is_excluded_not_scored(self):
        late = Externals(gh_at="T17:30:00Z")
        p1, p2 = late.patch(self.sl)
        with p1, p2, mock.patch.multiple(EV, utc_now=lambda: datetime(2027, 10, 20, tzinfo=timezone.utc),
                                         publish_lock=lambda r, x: None,
                                         grade_shadow_board=lambda b: self.fail("graded an invalid unit"),
                                         fetch_game_states=lambda g: {}):
            out = EV.evaluate_from_evidence(self.root, "2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["n_slates_used"], 0)
        self.assertTrue(out["invalid_slates"]["2027-09-28/DAY"].startswith("SLATE_INVALID_NO_CONFIRMATORY_USE:LATE_SEAL"))

    def test_pre_boundary_unit_is_drill_only(self):
        root = new_root()
        try:
            sl, _, _ = make_unit(root, date="2026-10-01", gtype="F")
            ext = Externals()
            p1, p2 = ext.patch(sl)
            with p1, p2, mock.patch.multiple(EV, utc_now=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc),
                                             publish_lock=lambda r, x: None, fetch_game_states=lambda g: {},
                                             grade_shadow_board=lambda b: self.fail("graded a drill unit")):
                out = EV.evaluate_from_evidence(root, "2026_POSTSEASON_SHADOW")
            self.assertEqual(out["invalid_slates"]["2026-10-01/DAY"], "PRE_V3_BOUNDARY_DRILL_ONLY")
            self.assertEqual(out["primary_verdict"], "NOT_APPLICABLE_DESCRIPTIVE_REGIME")
        finally:
            shutil.rmtree(root)

    def test_postseason_game_in_2027_window_rejected_by_final_evaluation(self):
        root = new_root()
        try:
            sl, _, _ = make_unit(root, date="2027-09-30", gtype="F")
            ext = Externals()
            p1, p2 = ext.patch(sl)
            with p1, p2, mock.patch.multiple(EV, utc_now=lambda: datetime(2027, 10, 20, tzinfo=timezone.utc),
                                             publish_lock=lambda r, x: None, fetch_game_states=lambda g: {},
                                             grade_shadow_board=lambda b: self.fail("graded postseason in 2027")):
                with self.assertRaises(ValueError):
                    EV.evaluate_from_evidence(root, "2027_REGULAR_CONFIRMATORY")
        finally:
            shutil.rmtree(root)

    def test_grace_period_allows_after_seven_days(self):
        out, _ = self.run_eval(datetime(2027, 10, 12, tzinfo=timezone.utc), terminal=False)
        self.assertEqual(out["one_look_trigger"], "SEVEN_DAY_GRACE_ELAPSED")


class OneLookGameStates(unittest.TestCase):
    """Blocker 3: only the pinned grader's notion of final may unlock the early path."""
    def setUp(self):
        self.root = new_root()
        self.sl, _, _ = make_unit(self.root, date="2027-09-28")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def attempt(self, when, state):
        ext = Externals()
        p1, p2 = ext.patch(self.sl)
        lock = lambda root, r: json.dump(r, open(os.path.join(root, f"FINAL_ANALYSIS_{r['regime']}.json"), "w"))
        grade = lambda b: {"source_board_sha256": b["board_sha256"],
                           "records": [{"candidate_id": r["candidate_id"], "grade": "hit"} for r in b["records"]]}
        with p1, p2, mock.patch.multiple(EV, utc_now=lambda: when, publish_lock=lock, grade_shadow_board=grade,
                                         fetch_game_states=lambda gps: {str(g): state for g in gps}):
            return EV.evaluate_from_evidence(self.root, "2027_REGULAR_CONFIRMATORY")

    def locked(self):
        return os.path.exists(os.path.join(self.root, "FINAL_ANALYSIS_2027_REGULAR_CONFIRMATORY.json"))

    def test_postponed_suspended_cancelled_do_not_unlock(self):
        for st in (POSTPONED, SUSPENDED, CANCELLED):
            with self.assertRaises(EV.OneLookRefused):
                self.attempt(datetime(2027, 10, 5, 12, tzinfo=timezone.utc), st)
            self.assertFalse(self.locked(), st)
        self.assertFalse(EV.pinned_is_final(None))

    def test_final_unlocks_only_when_other_conditions_hold(self):
        with self.assertRaises(EV.OneLookRefused):
            self.attempt(datetime(2027, 10, 4, 23, 59, tzinfo=timezone.utc), FINAL)        # before the date
        self.assertFalse(self.locked())
        out = self.attempt(datetime(2027, 10, 5, 12, tzinfo=timezone.utc), FINAL)
        self.assertEqual(out["one_look_trigger"], "ALL_COVERED_GAMES_FINAL_UNDER_PINNED_GRADER")

    def test_postponed_then_final(self):
        with self.assertRaises(EV.OneLookRefused):
            self.attempt(datetime(2027, 10, 5, 12, tzinfo=timezone.utc), POSTPONED)
        self.assertFalse(self.locked())
        self.assertEqual(self.attempt(datetime(2027, 10, 6, 12, tzinfo=timezone.utc), FINAL)["n_slates_used"], 1)

    def test_suspended_then_completed(self):
        with self.assertRaises(EV.OneLookRefused):
            self.attempt(datetime(2027, 10, 5, 12, tzinfo=timezone.utc), SUSPENDED)
        out = self.attempt(datetime(2027, 10, 6, 12, tzinfo=timezone.utc), COMPLETED_EARLY)
        self.assertEqual(out["one_look_trigger"], "ALL_COVERED_GAMES_FINAL_UNDER_PINNED_GRADER")

    def test_grace_path_and_second_look(self):
        out = self.attempt(datetime(2027, 10, 12, tzinfo=timezone.utc), POSTPONED)
        self.assertEqual(out["one_look_trigger"], "SEVEN_DAY_GRACE_ELAPSED")
        with self.assertRaises(EV.OneLookRefused):
            self.attempt(datetime(2027, 10, 30, tzinfo=timezone.utc), FINAL)

    def test_pinned_rule_matches_pinned_grader_source(self):
        src = subprocess.check_output(["git", "-C", HERE, "show", f"{SH.SHADOW_PIN}:grade_results.py"]).decode()
        self.assertIn('return coded in ("F", "O") or "final" in detailed.lower() or "completed" in detailed.lower()', src)


# ---- blocker 4: frozen coefficient identity ----------------------------------------------------------
class FrozenCoefficients(unittest.TestCase):
    def setUp(self):
        self.root = new_root()
        self.sl, _, _ = make_unit(self.root, date="2027-09-28")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def variant(self, mutate_bytes=None, mutate_obj=None):
        raw = open(VE.FROZEN_COEFFICIENTS_PATH, "rb").read()
        if mutate_obj:
            obj = json.loads(raw)
            mutate_obj(obj)
            raw = json.dumps(obj, indent=2, sort_keys=True).encode()
        if mutate_bytes:
            raw = mutate_bytes(bytearray(raw))
        p = os.path.join(self.tmp, "coef.json")
        open(p, "wb").write(bytes(raw))
        return p

    def evaluate_with(self, path):
        ext = Externals()
        p1, p2 = ext.patch(self.sl)
        with p1, p2, mock.patch.object(VE, "FROZEN_COEFFICIENTS_PATH", path), \
                mock.patch.multiple(EV, utc_now=lambda: datetime(2027, 10, 20, tzinfo=timezone.utc),
                                    publish_lock=lambda *a: self.fail("one-look lock published"),
                                    grade_shadow_board=lambda b: self.fail("outcomes opened"),
                                    fetch_game_states=lambda g: {}):
            return EV.evaluate_from_evidence(self.root, "2027_REGULAR_CONFIRMATORY")

    def test_valid_frozen_file_loads_and_matches_prereg_hash(self):
        self.assertEqual(VE.FROZEN_COEFFICIENTS_SHA256, "3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009")
        self.assertEqual(VE.load_frozen_coefficients(), H.load_coefficients())

    def test_one_byte_mutation_rejected_before_lock_and_outcomes(self):
        def flip(b):
            b[len(b) // 2] ^= 0x01
            return b
        with self.assertRaises(VE.CoefficientIntegrityError):
            self.evaluate_with(self.variant(mutate_bytes=flip))

    def test_same_schema_changed_coefficient_rejected(self):
        def bump(o):
            o["coefficients"]["p2"]["_pooled"]["beta"][2] += 0.01
        with self.assertRaises(VE.CoefficientIntegrityError):
            self.evaluate_with(self.variant(mutate_obj=bump))

    def test_missing_artifact_rejected(self):
        with self.assertRaises(VE.CoefficientIntegrityError):
            self.evaluate_with(os.path.join(self.tmp, "absent.json"))

    def test_caller_cannot_inject_coefficients(self):
        with self.assertRaises(TypeError):
            EV.evaluate_from_evidence(self.root, "2027_REGULAR_CONFIRMATORY", coef=H.load_coefficients())
        with self.assertRaises(TypeError):
            EV.evaluate_from_evidence(self.root, "2027_REGULAR_CONFIRMATORY", H.load_coefficients())

    def test_wrong_challenger_version_in_seal_rejected_before_lock(self):
        root = new_root()
        try:
            sl, _, _ = make_unit(root, date="2027-09-28", challenger="c" * 64)
            ext = Externals()
            p1, p2 = ext.patch(sl)
            with p1, p2, mock.patch.multiple(EV, utc_now=lambda: datetime(2027, 10, 20, tzinfo=timezone.utc),
                                             publish_lock=lambda r, x: None, fetch_game_states=lambda g: {},
                                             grade_shadow_board=lambda b: self.fail("graded a wrong-version unit")):
                out = EV.evaluate_from_evidence(root, "2027_REGULAR_CONFIRMATORY")
            self.assertEqual(out["invalid_slates"]["2027-09-28/DAY"],
                             "SLATE_INVALID_NO_CONFIRMATORY_USE:CHALLENGER_VERSION_MISMATCH")
            self.assertEqual(out["n_slates_used"], 0)
        finally:
            shutil.rmtree(root)

    def test_runner_seals_only_the_frozen_version(self):
        self.assertEqual(RN._frozen_challenger_version(), VE.FROZEN_COEFFICIENTS_SHA256)
        with mock.patch.object(VE, "FROZEN_COEFFICIENTS_PATH", self.variant(mutate_obj=lambda o: o.update(l2=2.0))):
            with self.assertRaises(VE.CoefficientIntegrityError):
                RN._frozen_challenger_version()


# ---- statistics ----------------------------------------------------------------------------------------
def pair(date="2027-05-04", champs=2, flip=False, n=8):
    root = new_root()
    try:
        sl, m, b = make_unit(root, date=date, champs=champs, n=n)
    finally:
        shutil.rmtree(root)
    grades = {r["candidate_id"]: ("hit" if (i % 2 == 0) != flip else "miss") for i, r in enumerate(b["records"])}
    return m, {"source_board_sha256": b["board_sha256"], "records": [{"candidate_id": k, "grade": v} for k, v in grades.items()]}


SPEC = RG.REGIMES["2027_REGULAR_CONFIRMATORY"]


class Statistics(unittest.TestCase):
    def test_equal_volume_same_sealed_ids(self):
        out = EV._compute([pair()], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")
        for arm in ("SHADOW_CHAMPION", *H.SELECTORS):
            self.assertEqual(out["picking"][arm]["counts"]["selected"], 2)

    def test_zero_pick_slate(self):
        out = EV._compute([pair(champs=0)], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["zero_champion_slates"], 1)
        self.assertTrue(all(v["counts"]["selected"] == 0 for v in out["picking"].values()))
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")

    def test_void_push_unresolved_denominators(self):
        m, g = pair(champs=3)
        g["records"][0]["grade"], g["records"][1]["grade"], g["records"][2]["grade"] = "void", "push", "ungraded"
        out = EV._compute([(m, g)], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["picking"]["SHADOW_CHAMPION"]["counts"],
                         {"selected": 3, "settled": 0, "void": 1, "push": 1, "unresolved": 1})

    def test_selection_ignores_outcomes(self):
        a = EV._compute([pair()], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")
        b = EV._compute([pair(flip=True)], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")
        for arm in a["picking"]:
            self.assertEqual(a["picking"][arm]["market_mix"], b["picking"][arm]["market_mix"])
        self.assertEqual(a["picking"]["C2_RESIDUAL"]["overlap"]["overlap"], b["picking"]["C2_RESIDUAL"]["overlap"]["overlap"])

    def test_graded_file_must_link_to_shadow_board(self):
        m, g = pair()
        g["source_board_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            EV._compute([(m, g)], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")

    def test_verdict_table_practical_threshold_cannot_be_bypassed(self):
        c = lambda n, h, q: {"n_scored": n, "hit_rate": h, "mean_q": q}
        strong = {"one_sided_lower95": 0.04}
        self.assertEqual(EV.verdict_v3(c(200, .5, .55), c(200, .6, .55), strong, 80), "INSUFFICIENT_N")
        self.assertEqual(EV.verdict_v3(c(300, .55, .55), c(300, .55, .55), strong, 80), "REJECTED")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .56, .55), {"one_sided_lower95": -0.01}, 80), "INCONCLUSIVE")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .56, .60), strong, 80), "INCONCLUSIVE_CHALK_GUARD")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .549, .55), strong, 80), "POSITIVE_BELOW_PRACTICAL_THRESHOLD")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .55, .55), strong, 80), "SUPPORTED_ADOPTABLE")

    def test_c3_cannot_promote(self):
        with mock.patch.object(H, "c3_pitcher_outs", lambda rows: {"verdict": "SUPPORTED"}):
            out = EV._compute([pair()], COEF, SPEC, "2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")
        self.assertEqual(out["c3_pitcher_outs_secondary"]["role"], "SECONDARY_NO_PROMOTION_ON_ITS_OWN")


class Regimes(unittest.TestCase):
    def test_postseason_and_wrong_year_and_pooling_rejected(self):
        with self.assertRaises(ValueError):
            RG.check_regime("2027_REGULAR_CONFIRMATORY", [pair(date="2026-10-05")[0]])
        with self.assertRaises(ValueError):
            RG.check_regime("2027_REGULAR_CONFIRMATORY", [pair(date="2026-08-15")[0]])
        m_post = pair(date="2027-09-30")[0]
        for r in m_post["rows"]:
            r["game_type"] = "F"
        with self.assertRaises(ValueError):
            RG.check_regime("2027_REGULAR_CONFIRMATORY", [pair()[0], m_post])
        with self.assertRaises(ValueError):
            RG.check_regime("2027_REGULAR_CONFIRMATORY_FROM_2026", [])

    def test_smoke_regime_not_accepted_by_final_evaluation(self):
        with self.assertRaises(ValueError):
            EV.evaluate_from_evidence("/nonexistent", "SMOKE_TEST_SYNTHETIC")


# ---- activation ------------------------------------------------------------------------------------
def git(repo, *a):
    return subprocess.check_output(["git", "-C", repo, *a], stderr=subprocess.DEVNULL).decode().strip()


class Activation(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="v3act_")
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        os.makedirs(os.path.join(self.repo, AC.V3_DIR))
        shutil.copy(os.path.join(os.path.dirname(os.path.dirname(HERE)), "mlb_accuracy_challenger_prereg_v3_20261001.md"),
                    os.path.join(self.repo, AC.PREREG_PATH))
        open(os.path.join(self.repo, AC.V3_DIR, "x.py"), "w").write("x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "impl")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.rec = {"jacob_authorization_comment_id": 777, "repository": AC.REPOSITORY, "pr_number": 220,
                    "protocol_version": "V3", "prereg_commit": VE.PREREG_COMMIT, "prereg_sha256": VE.PREREG_SHA256,
                    "implementation_commit": self.head, "implementation_tree": git(self.repo, "rev-parse", f"HEAD:{AC.V3_DIR}"),
                    "activation_timestamp": "2027-03-01T00:00:00Z"}
        self.env = {"MLB_V3_ACTIVATION": "JACOB_AUTHORIZED"}

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def comment(self, body=None, cid=777, issue=GH_ISSUE):
        body = body if body is not None else (
            f"JACOB AUTHORIZATION: ALLOW\n\nV3 prospective activation for prereg {VE.PREREG_COMMIT} "
            f"(sha256 {VE.PREREG_SHA256}) and implementation {self.head}.\n\nAlligator")
        return lambda i: {"id": cid, "issue_url": issue, "created_at": "2027-02-28T12:00:00Z", "body": body}

    def check(self, rec=None, fetch=None, env=None):
        return AC.verify_activation(self.repo, record=rec or self.rec, fetch=fetch or self.comment(), env=env or self.env)

    def test_valid_activation(self):
        self.assertEqual(self.check(), (True, []))

    def test_env_or_file_alone_insufficient(self):
        self.assertFalse(self.check(env={"MLB_V3_ACTIVATION": "no"})[0])
        self.assertFalse(AC.verify_activation(self.repo, env=self.env)[0] if not os.path.exists(AC.ACTIVATION_FILE) else False)

    def test_old_activation_after_code_change(self):
        open(os.path.join(self.repo, AC.V3_DIR, "x.py"), "w").write("x = 2\n")
        git(self.repo, "commit", "-qam", "changed")
        ok, why_ = self.check()
        self.assertFalse(ok)
        self.assertIn("CODE_COMMIT_NOT_AUTHORIZED", why_)

    def test_uncommitted_change_and_wrong_prereg(self):
        open(os.path.join(self.repo, AC.V3_DIR, "x.py"), "w").write("x = 3\n")
        self.assertIn("LOCAL_MODIFICATIONS", self.check()[1])
        git(self.repo, "checkout", "--", ".")
        self.assertIn("WRONG_PREREG_COMMIT", self.check(rec=dict(self.rec, prereg_commit="1" * 40))[1])
        self.assertIn("WRONG_PROTOCOL_VERSION", self.check(rec=dict(self.rec, protocol_version="V4"))[1])
        open(os.path.join(self.repo, AC.PREREG_PATH), "a").write("edit")
        self.assertIn("PREREG_FILE_CHANGED", self.check()[1])

    def test_wrong_code_sha(self):
        self.assertIn("CODE_COMMIT_NOT_AUTHORIZED", self.check(rec=dict(self.rec, implementation_commit="2" * 40))[1])

    def test_fabricated_or_unverifiable_comment(self):
        def gone(i):
            raise VE.EvidenceUnverified("404")
        self.assertFalse(self.check(fetch=gone)[0])
        self.assertIn("AUTHORIZATION_COMMENT_ID_MATCHES", self.check(fetch=self.comment(cid=999))[1])
        self.assertIn("AUTHORIZATION_COMMENT_ON_ISSUE_91", self.check(fetch=self.comment(issue=GH_ISSUE + "0"))[1])

    def test_missing_jacob_authorization(self):
        for body in ("JACOB AUTHORIZATION: ALLOW\nsomething unrelated",
                     f"CLAUDE STATUS\nV3 prospective activation {VE.PREREG_COMMIT} {VE.PREREG_SHA256} {self.head}",
                     f"JACOB AUTHORIZATION: ALLOW\nV3 prospective activation {VE.PREREG_COMMIT} {VE.PREREG_SHA256} {self.head}\n"
                     "_Generated by [Claude Code](https://claude.ai/code)_"):
            self.assertFalse(self.check(fetch=self.comment(body=body))[0], body[:40])

    def test_header_and_action_phrase_both_required(self):
        full = f"V3 prospective activation {VE.PREREG_COMMIT} {VE.PREREG_SHA256} {self.head}"
        self.assertIn("AUTHORIZATION_COMMENT_JACOB_FORMAT", self.check(fetch=self.comment(body="Please proceed.\n" + full))[1])
        no_action = f"JACOB AUTHORIZATION: ALLOW\nGo ahead {VE.PREREG_COMMIT} {VE.PREREG_SHA256} {self.head}"
        self.assertIn("AUTHORIZATION_COMMENT_NAMES_ACTION", self.check(fetch=self.comment(body=no_action))[1])

    def test_runner_refuses_without_activation(self):
        os.environ.pop("MLB_V3_ACTIVATION", None)
        self.assertFalse(os.path.exists(AC.ACTIVATION_FILE))
        self.assertEqual(RN.main(["--mode", "prospective", "--repo", "/nonexistent", "--date", D, "--window", "DAY",
                                  "--out", "/nonexistent/out"]), 2)


# ---- scheduling ---------------------------------------------------------------------------------------
class Scheduling(unittest.TestCase):
    def s(self, *g):
        return sched(*g, date="2027-05-04")

    def test_early_game_and_night_and_split(self):
        p = SP.plan(self.s(game(1, start="2027-05-04T16:05:00Z"), game(2, start="2027-05-04T23:05:00Z")),
                    "2027-05-04T14:00:00+00:00")
        self.assertEqual([(u["window"], u["status"]) for u in p], [("DAY", "PLANNED"), ("NIGHT", "PLANNED")])
        lead = sum(SP.BUDGET_S.values()) + SP.SAFETY_S
        self.assertEqual(M3.utc(p[0]["first_pitch_utc"]) - M3.utc(p[0]["latest_start_utc"]),
                         __import__("datetime").timedelta(seconds=lead))

    def test_doubleheader_tbd_second_game(self):
        p = SP.plan(self.s(game(1, start="2027-05-04T17:05:00Z"), game(2, start="2027-05-04T16:33:00Z", tbd=True)),
                    "2027-05-04T14:00:00+00:00")
        self.assertEqual(p[0]["first_pitch_utc"], "2027-05-04T17:05:00Z")

    def test_late_start_missed(self):
        p = SP.plan(self.s(game(1, start="2027-05-04T16:05:00Z")), "2027-05-04T15:20:00+00:00")
        self.assertEqual(p[0]["status"], "MISSED_NO_CONFIRMATORY_USE")

    def test_guards_network_shadow_tsa_overruns(self):
        fp = "2027-05-04T17:00:00+00:00"
        need = lambda step: sum(SP.BUDGET_S[s] for s in SP.STEPS[SP.STEPS.index(step):]) + SP.SAFETY_S
        from datetime import timedelta
        for step in SP.STEPS:
            at = (M3.utc(fp) - timedelta(seconds=need(step))).isoformat()
            SP.guard(at, fp, step)                                  # exactly enough: proceeds
            with self.assertRaises(SP.MissUnit):
                SP.guard((M3.utc(at) + timedelta(seconds=1)).isoformat(), fp, step)

    def test_runner_misses_unit_without_sealing(self):
        out = tempfile.mkdtemp()
        soon = (datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=5)).isoformat()
        try:
            with mock.patch.object(RN, "schedule_snapshot", lambda d: sched(game(1, start=soon), date=d)), \
                    mock.patch.object(SH, "build_tree", side_effect=AssertionError("must not start")):
                rc = RN.main(["--mode", "drill", "--repo", "/nonexistent", "--date", soon[:10],
                              "--window", M3.window_of(soon), "--out", out])
            self.assertEqual(rc, 4)
            self.assertEqual(json.load(open(os.path.join(out, "MISSED_UNIT.json")))["status"],
                             "MISS_UNIT_NO_CONFIRMATORY_USE_NO_BACKFILL")
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
