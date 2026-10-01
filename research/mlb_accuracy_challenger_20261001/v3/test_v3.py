#!/usr/bin/env python3
"""Deterministic synthetic tests for preregistration v3 (no network, no outcomes)."""
from __future__ import annotations

import copy
import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import capture as CP  # noqa: E402
import evaluate_v3 as EV  # noqa: E402
import harness as H  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import regimes as RG  # noqa: E402
import runner as RN  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402

COEF = H.load_coefficients()
D = "2099-04-01"
CUT = f"{D}T16:00:00+00:00"
START1, START2 = f"{D}T17:05:00Z", f"{D}T23:10:00Z"     # 13:05 ET (DAY), 19:10 ET (NIGHT)
PROV = {"git_sha": SH.SHADOW_PIN[:10], **SH.SHADOW_LABELS}


def rec(cid, *, name="Al Bat", game="1", player="10", team="Away Club", stat="hits", needs="1", line=0.5,
        side="over", odds=-120, p=0.62, rel="A", n=30, status="lean", qc="kept", assumed=False, prov=None):
    return {"candidate_id": cid, "game_pk": game, "player_id": player, "player_name": name, "team": team,
            "stat": stat, "needs": needs, "line": line, "market_side": side,
            "prediction": {"hit_probability": p, "reliability": rel, "sample_n": n},
            "market": {"market_odds": odds}, "eligibility": {"qc_status": qc, "lineup_assumed": assumed},
            "selector": {"recommendation_status": status}, "provenance": dict(prov or PROV)}


def board(records, *, generated=f"{D}T15:50:00+00:00", prov=None):
    b = {"date": D, "board_generated_at": generated, "sealed_at": generated, "provenance": dict(prov or PROV),
         "records": records}
    b["board_sha256"] = CP.canonical_sha256(b)
    return b


def game(pk, *, away="Away Club", home="Home Club", start=START1, gtype="R", probables=(50,)):
    return {"game_pk": int(pk), "game_type": gtype, "game_date": start, "away_team": away, "home_team": home,
            "double_header": "N", "game_number": 1, "probable_pitcher_ids": list(probables)}


def sched(*games):
    return {"date": D, "fetched_at": f"{D}T15:55:00+00:00", "games": list(games)}


def runner_(name, odds, *, status="ACTIVE", team="away_club", handicap=0, rtype=None, sel=1):
    return {"selection_id": sel, "runner_name": name, "runner_status": status, "handicap": handicap,
            "result_type": rtype, "team_slug": team, "american": odds}


def market(mtype, runners, *, mid="m1", status="OPEN", in_play=False):
    return {"market_id": mid, "market_type": mtype, "market_name": mtype, "market_status": status,
            "in_play": in_play, "runners": runners}


def event(eid, markets, *, name="Away Club (P A) @ Home Club (P B)", open_date=START1, tabs_ok=True,
          completed=f"{D}T15:58:00+00:00", status=None):
    tabs = {t: "OK" for t in CP.TABS}
    if not tabs_ok:
        tabs["batter-props"] = "FAILED: RuntimeError: all FanDuel hosts failed"
    return {"event_id": eid, "event_name": name, "open_date": open_date, "fetch_started_at": f"{D}T15:57:00+00:00",
            "fetch_completed_at": completed, "tabs": tabs, "markets": markets,
            "status": status or ("COMPLETE" if tabs_ok else "PARTIAL")}


def cap(*events, started=f"{D}T15:57:00+00:00", completed=f"{D}T15:58:30+00:00"):
    c = {"capture_version": CP.CAPTURE_VERSION, "book": "fanduel", "capture_started_at": started,
         "capture_completed_at": completed, "discovery": {"status": "OK"}, "events": list(events),
         "status": "COMPLETE" if completed and all(e["status"] == "COMPLETE" for e in events) else "INCOMPLETE"}
    c["capture_sha256"] = CP.canonical_sha256(c)
    return c


HIT = "PLAYER_TO_RECORD_A_HIT"


def build(records, capture, schedule, window="DAY", cutoff=CUT):
    return M3.build_manifest(board(records), capture, schedule, window=window, cutoff_utc=cutoff)


def why(m):
    return {r["candidate_id"]: r["exclusion_reason"] for r in m["rows"]}


class QuoteIdentity(unittest.TestCase):
    def test_exact_offer_binds_all_identity_fields(self):
        m = build([rec("a")], cap(event(101, [market(HIT, [runner_("Al Bat", -120)])])), sched(game(1)))
        r = m["rows"][0]
        self.assertTrue(r["eligible"], r["exclusion_reason"])
        q = r["quote"]
        for k in ("book", "capture_sha256", "event_id", "market_id", "market_type", "selection_id", "team_slug",
                  "side", "needs", "american"):
            self.assertIsNotNone(q[k], k)
        self.assertEqual((q["event_id"], q["market_type"], q["american"]), (101, HIT, -120))

    def test_doubleheader_collision_fails_closed(self):
        # both games same teams; both FanDuel events open within tolerance of both games -> ambiguous
        g1, g2 = game(1, start=f"{D}T17:05:00Z"), game(2, start=f"{D}T18:00:00Z")
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)])], open_date=f"{D}T17:05:00Z"),
                event(102, [market(HIT, [runner_("Al Bat", -120)], mid="m2")], open_date=f"{D}T18:00:00Z"))
        self.assertEqual(why(build([rec("a"), rec("b", game="2", player="11")], c, sched(g1, g2)))["a"],
                         "EVENT_MAPPING_AMBIGUOUS")

    def test_doubleheader_separated_in_time_maps_uniquely(self):
        g1, g2 = game(1, start=f"{D}T17:05:00Z"), game(2, start=f"{D}T21:10:00Z")
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -150)])], open_date=f"{D}T17:05:00Z"),
                event(102, [market(HIT, [runner_("Al Bat", -120)], mid="m2")], open_date=f"{D}T21:10:00Z"))
        m = build([rec("a", odds=-120)], c, sched(g1, g2))
        self.assertEqual(why(m)["a"], "PRICE_MISMATCH_BOARD_VS_CAPTURE")   # game-1 price -150, not game-2's -120

    def test_same_player_stat_price_in_another_game_never_matches(self):
        c = cap(event(101, [market(HIT, [runner_("Someone Else", -120)])]),
                event(102, [market(HIT, [runner_("Al Bat", -120)], mid="m2")], name="Other A (x) @ Other B (y)"))
        s = sched(game(1), game(2, away="Other A", home="Other B"))
        self.assertEqual(why(build([rec("a")], c, s))["a"], "MARKET_ABSENT")

    def test_wrong_event_mapping(self):
        c = cap(event(101, [market(HIT, [runner_("Al Bat", -120)])], name="Wrong Club (x) @ Home Club (y)"))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "EVENT_NOT_MAPPED")

    def test_team_line_side_and_status_bindings(self):
        k = "PITCHER_A_TOTAL_STRIKEOUTS"
        recs = [rec("team", name="T M", player="20", team="Away Club"),
                rec("kline", name="K P", player="50", stat="strikeouts", needs="5", line=4.5),
                rec("susp", name="S U", player="21"), rec("inplay", name="I P", player="22"),
                rec("plus", name="P L", player="23", odds="+100"), rec("amb", name="A M", player="24")]
        c = cap(event(101, [market(HIT, [runner_("T M", -120, team="home_club"),
                                          runner_("S U", -120, status="SUSPENDED", sel=3),
                                          runner_("P L", 100, sel=4), runner_("A M", -120, sel=10)]),
                            market(HIT, [runner_("A M", -125, sel=11)], mid="m10"),
                            market(HIT.replace("A_HIT", "A_HIT"), [runner_("I P", -120, sel=5)], mid="m9", in_play=True),
                            market(k, [runner_("K P Over", -120, handicap=5.5, rtype="OVER", sel=6),
                                       runner_("K P Under", 100, handicap=5.5, rtype="UNDER", sel=7)], mid="mk")]))
        w = why(build(recs, c, sched(game(1))))
        self.assertEqual(w["team"], "QUOTE_TEAM_MISMATCH")
        self.assertEqual(w["kline"], "MARKET_ABSENT")          # board 4.5 vs posted 5.5: different offer
        self.assertEqual(w["susp"], "MARKET_SUSPENDED")
        self.assertEqual(w["inplay"], "IN_PLAY")
        self.assertEqual(w["amb"], "QUOTE_AMBIGUOUS")          # two offers for one identity -> fail closed
        self.assertIsNone(w["plus"])                            # '+100' == 100, representation only

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
        self.assertTrue(rows["o"]["eligible"])
        self.assertEqual(rows["o"]["quote"]["devig"], "ONE_SIDED_NO_DEVIG")

    def test_starter_must_be_mlb_probable_and_is_never_called_confirmed(self):
        k = "PITCHER_A_TOTAL_STRIKEOUTS"
        c = cap(event(101, [market(k, [runner_("K P Over", -120, handicap=4.5, rtype="OVER")], mid="mk")]))
        r = build([rec("k", name="K P", player="99", stat="strikeouts", needs="5", line=4.5)], c, sched(game(1)))["rows"][0]
        self.assertEqual(r["exclusion_reason"], "STARTER_FAIL")
        self.assertEqual(r["starter_status"], "NOT_MLB_PROBABLE")
        ok = build([rec("k", name="K P", player="50", stat="strikeouts", needs="5", line=4.5)], c, sched(game(1)))["rows"][0]
        self.assertEqual(ok["starter_status"], "MLB_PROBABLE_LISTED_NOT_INDEPENDENTLY_CONFIRMED")


class CaptureCompleteness(unittest.TestCase):
    def ev(self, **kw):
        return event(101, [market(HIT, [runner_("Al Bat", -120)])], **kw)

    def test_capture_starts_before_cutoff_ends_after(self):
        c = cap(self.ev(), started=f"{D}T15:59:00+00:00", completed=f"{D}T16:00:30+00:00")
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "CAPTURE_COMPLETED_AFTER_CUTOFF")

    def test_partial_capture_is_not_market_absence(self):
        c = cap(event(101, [], tabs_ok=False))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "EVENT_NOT_OBSERVED")
        c2 = cap(event(101, []))
        self.assertEqual(why(build([rec("a")], c2, sched(game(1))))["a"], "MARKET_ABSENT")

    def test_failed_event_fetch_and_unfinished_capture(self):
        c = cap(event(101, [], status="NOT_FETCHED_BUDGET"))
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "EVENT_NOT_OBSERVED")
        c2 = cap(self.ev(), completed=None)
        self.assertEqual(why(build([rec("a")], c2, sched(game(1))))["a"], "CAPTURE_INCOMPLETE")

    def test_quote_age_ceiling(self):
        c = cap(self.ev(), completed=f"{D}T15:14:59+00:00")
        self.assertEqual(why(build([rec("a")], c, sched(game(1))))["a"], "QUOTE_STALE")

    def test_capture_hash_rebuilds(self):
        c = cap(self.ev())
        CP.verify_capture(c)
        c["events"][0]["markets"][0]["runners"][0]["american"] = -110
        with self.assertRaises(ValueError):
            CP.verify_capture(c)


class Universe(unittest.TestCase):
    def test_gates_and_window(self):
        recs = [rec("relC", name="R C", player="1", rel="C"), rec("lin", name="L A", player="2", assumed=True),
                rec("qc", name="Q C", player="3", qc="lineup_assumed_holdout"), rec("n0", name="N Z", player="4", n=0),
                rec("dup1", name="D U", player="5"), rec("dup2", name="D U", player="5"),
                rec("combo", name="C K", player="6", stat="combined_strikeouts"),
                rec("night", name="N I", player="7", game="2"), rec("band", name="B A", player="8", odds=-400)]
        c = cap(event(101, [market(HIT, [runner_(n, -400 if n == "B A" else -120, sel=i)
                                          for i, n in enumerate(["R C", "L A", "Q C", "N Z", "D U", "C K", "B A"])])]),
                event(102, [market(HIT, [runner_("N I", -120)], mid="mn")], name="Night A (x) @ Night B (y)",
                      open_date=START2))
        m = build(recs, c, sched(game(1), game(2, away="Night A", home="Night B", start=START2)))
        w = why(m)
        self.assertEqual(w, {"relC": "RELIABILITY_FAIL", "lin": "LINEUP_FAIL", "qc": "QC_FAIL", "n0": "SAMPLE_N_FAIL",
                             "dup1": "DUPLICATE_IDENTITY_FAIL", "dup2": "DUPLICATE_IDENTITY_FAIL",
                             "combo": "FAMILY_RED_FLAG_SEPARATE", "band": "PRICE_BAND_FAIL"})   # NIGHT game not in DAY unit
        self.assertNotIn("2", m["covered_games"])
        for r in m["rows"]:
            self.assertEqual(set(r["gates"]), set(M3.GATES))

    def test_no_outcome_input_and_hash(self):
        self.assertEqual(list(inspect.signature(M3.build_manifest).parameters),
                         ["shadow_board", "capture", "schedule", "window", "cutoff_utc", "shadow_provenance"])
        m = build([rec("a")], cap(event(101, [market(HIT, [runner_("Al Bat", -120)])])), sched(game(1)))
        M3.verify_manifest(m)
        t = copy.deepcopy(m)
        t["rows"][0]["shadow_champion"] = True
        with self.assertRaises(ValueError):
            M3.verify_manifest(t)


class ShadowChampion(unittest.TestCase):
    def test_independent_of_live_production_version(self):
        SH.verify_shadow_board(board([rec("a")]))
        live = {"git_sha": "abcdef1234", "model_version": "2027.01.15", "selection_policy_version": "2.0.0",
                "calibration_version": "2.0.0", "feature_version": "2.0.0"}
        with self.assertRaises(ValueError):          # a production board can never stand in for the champion
            SH.verify_shadow_board(board([rec("a", prov=live)], prov=live))
        other_code = dict(PROV, git_sha="0123456789")   # pinned labels, different code
        with self.assertRaises(ValueError):
            SH.verify_shadow_board(board([rec("a", prov=other_code)], prov=other_code))
        relabeled = dict(PROV, model_version="2027.01.15")
        with self.assertRaises(ValueError):
            SH.verify_shadow_board(board([rec("a")], prov=relabeled))
        self.assertEqual(SH.SHADOW_ID, "FROZEN_SHADOW_POLICY_2026.08.15@7d3ebacd55")


# ---- seal / chain / receipts ---------------------------------------------------------------
def mk_manifest(date=D, gtype="R", window="DAY", n=8, flip=False, champs=2, start=None):
    start = start or f"{date}T17:05:00Z"
    recs = [rec(f"{date}-{i}", name=f"P{i} X", player=str(i), game=str(i % 4 + 1), p=0.55 + 0.02 * i,
                odds=-110 - 10 * i, status="top_pick" if i < champs else "lean") for i in range(n)]
    games = [dict(game(g, away=f"A{g}", home=f"H{g}", start=start, gtype=gtype)) for g in range(1, 5)]
    evs = [event(100 + g, [market(HIT, [runner_(f"P{i} X", -110 - 10 * i, team=f"a{g}", sel=i)
                                         for i in range(n) if i % 4 + 1 == g], mid=f"m{g}")],
                 name=f"A{g} (x) @ H{g} (y)", open_date=start, completed=f"{date}T15:58:00+00:00") for g in range(1, 5)]
    for r_ in recs:
        r_["team"] = f"A{r_['game_pk']}"
    b = board(recs, generated=f"{date}T15:50:00+00:00")
    b["date"] = date
    b["board_sha256"] = CP.canonical_sha256({k: v for k, v in b.items() if k != "board_sha256"})
    s = sched(*games)
    s["date"] = date
    c = cap(*evs, started=f"{date}T15:57:00+00:00", completed=f"{date}T15:58:30+00:00")
    m = M3.build_manifest(b, c, s, window=window, cutoff_utc=f"{date}T16:00:00+00:00")
    grades = {r_["candidate_id"]: ("hit" if (i % 2 == 0) != flip else "miss") for i, r_ in enumerate(recs)}
    return m, {"source_board_sha256": b["board_sha256"],
               "records": [{"candidate_id": k, "grade": v} for k, v in grades.items()]}


def mk_seal(m, prev="g" * 64):
    return SL.build_seal(m, prev_seal_sha256=prev, prereg_sha256="p" * 64, challenger_sha256="c" * 64,
                         shadow_id=SH.SHADOW_ID, created_at=m["cutoff_utc"])


def receipts(seal, gh_at, tsa_at, verified=True):
    return {"github": {"id": 1, "created_at": gh_at, "body": SL.receipt_comment_body(seal)},
            "tsa": [{"tsa": "freetsa", "gen_time": tsa_at, "verified": verified}]}


class Seal(unittest.TestCase):
    def test_on_time_and_late(self):
        m, _ = mk_manifest()
        s = mk_seal(m)
        self.assertEqual(SL.verify_receipts(s, receipts(s, f"{D}T16:01:00Z", f"{D}T16:01:05Z"))[0], "ON_TIME")
        self.assertEqual(SL.verify_receipts(s, receipts(s, f"{D}T17:06:00Z", f"{D}T16:01:05Z"))[0], "LATE_SEAL")

    def test_local_commit_pregame_but_server_receipt_postgame(self):
        m, _ = mk_manifest()
        s = mk_seal(m)                                   # created_at_claimed is pregame (16:00Z)
        st, _ = SL.verify_receipts(s, receipts(s, f"{D}T17:30:00Z", f"{D}T17:30:01Z"))
        self.assertEqual(st, "LATE_SEAL")

    def test_missing_or_unverified_external_evidence(self):
        m, _ = mk_manifest()
        s = mk_seal(m)
        self.assertEqual(SL.verify_receipts(s, {"github": None, "tsa": []})[0], "MISSING_EXTERNAL_RECEIPT")
        self.assertEqual(SL.verify_receipts(s, receipts(s, f"{D}T16:01:00Z", f"{D}T16:01:05Z", verified=False))[0],
                         "MISSING_EXTERNAL_RECEIPT")
        wrong = receipts(s, f"{D}T16:01:00Z", f"{D}T16:01:05Z")
        wrong["github"]["body"] = "MLB V3 SEAL RECEIPT for some other hash"
        self.assertEqual(SL.verify_receipts(s, wrong)[0], "MISSING_EXTERNAL_RECEIPT")

    def test_rewritten_seal_ref_detection(self):
        ms = [mk_manifest(date=f"2099-04-0{d}")[0] for d in (1, 2, 3)]
        chain, prev = [], "g" * 64
        for m in ms:
            s = mk_seal(m, prev)
            chain.append(s)
            prev = s["seal_sha256"]
        SL.verify_chain(chain, "g" * 64)
        tampered = copy.deepcopy(chain)
        tampered[1]["manifest_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            SL.verify_chain(tampered, "g" * 64)
        with self.assertRaises(ValueError):                 # deletion
            SL.verify_chain([chain[0], chain[2]], "g" * 64)
        with self.assertRaises(ValueError):                 # reorder
            SL.verify_chain([chain[1], chain[0], chain[2]], "g" * 64)
        dup = mk_seal(ms[0], chain[-1]["seal_sha256"])
        with self.assertRaises(ValueError):                 # second seal for a slate unit
            SL.verify_chain(chain + [dup], "g" * 64)


def slate(date="2027-05-04", gtype="R", gh="T16:01:00Z", flip=False, champs=2, **kw):
    m, g = mk_manifest(date=date, gtype=gtype, flip=flip, champs=champs, **kw)
    s = mk_seal(m)
    return {"seal": s, "manifest": m, "graded": g, "receipts": receipts(s, f"{date}{gh}", f"{date}T16:01:05Z")}


class Regimes(unittest.TestCase):
    def test_postseason_rejected_from_2027_confirmatory(self):
        with self.assertRaises(ValueError):
            EV.evaluate([slate(date="2026-10-05", gtype="D")], COEF, regime="2027_REGULAR_CONFIRMATORY")

    def test_2027_postseason_cannot_pool_with_regular(self):
        with self.assertRaises(ValueError):
            EV.evaluate([slate(), slate(date="2027-10-06", gtype="D")], COEF, regime="2027_REGULAR_CONFIRMATORY")
        with self.assertRaises(ValueError):                 # inside the calendar window but a postseason game
            EV.evaluate([slate(date="2027-09-30", gtype="F")], COEF, regime="2027_REGULAR_CONFIRMATORY")

    def test_regular_season_game_from_another_year_rejected(self):
        with self.assertRaises(ValueError):                 # gameType R passes; the calendar window does not
            EV.evaluate([slate(date="2026-08-15", gtype="R")], COEF, regime="2027_REGULAR_CONFIRMATORY")

    def test_no_override_and_descriptive_postseason(self):
        with self.assertRaises(ValueError):
            EV.evaluate([], COEF, regime="2027_REGULAR_CONFIRMATORY_FROM_2026")
        out = EV.evaluate([slate(date="2026-10-05", gtype="D")], COEF, regime="2026_POSTSEASON_SHADOW")
        self.assertEqual(out["primary_verdict"], "NOT_APPLICABLE_DESCRIPTIVE_REGIME")
        with self.assertRaises(ValueError):
            EV.evaluate([slate(date="2026-10-05", gtype="R")], COEF, regime="2026_POSTSEASON_SHADOW")


class Evaluation(unittest.TestCase):
    def test_equal_volume_and_late_slate_excluded(self):
        out = EV.evaluate([slate(), slate(date="2027-05-05", gh="T17:30:00Z")], COEF, regime="2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["n_slates_used"], 1)
        self.assertTrue(out["invalid_slates"]["2027-05-05/DAY"].startswith("SLATE_INVALID_NO_CONFIRMATORY_USE"))
        for arm in ("SHADOW_CHAMPION", *H.SELECTORS):
            self.assertEqual(out["picking"][arm]["counts"]["selected"], 2)

    def test_zero_pick_slate(self):
        out = EV.evaluate([slate(champs=0)], COEF, regime="2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["zero_champion_slates"], 1)
        self.assertTrue(all(v["counts"]["selected"] == 0 for v in out["picking"].values()))
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")

    def test_void_push_unresolved_denominators(self):
        s = slate(champs=3)
        recs = s["graded"]["records"]
        recs[0]["grade"], recs[1]["grade"], recs[2]["grade"] = "void", "push", "ungraded"
        out = EV.evaluate([s], COEF, regime="2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["picking"]["SHADOW_CHAMPION"]["counts"],
                         {"selected": 3, "settled": 0, "void": 1, "push": 1, "unresolved": 1})

    def test_selection_ignores_outcomes(self):
        a = EV.evaluate([slate()], COEF, regime="2027_REGULAR_CONFIRMATORY")
        b = EV.evaluate([slate(flip=True)], COEF, regime="2027_REGULAR_CONFIRMATORY")
        for arm in a["picking"]:
            self.assertEqual(a["picking"][arm]["counts"]["selected"], b["picking"][arm]["counts"]["selected"])
            self.assertEqual(a["picking"][arm]["market_mix"], b["picking"][arm]["market_mix"])

    def test_seal_must_bind_manifest_and_chain(self):
        s = slate()
        s["seal"] = mk_seal(mk_manifest(date="2027-05-04", n=7)[0])
        out = EV.evaluate([s], COEF, regime="2027_REGULAR_CONFIRMATORY")
        self.assertEqual(out["invalid_slates"]["2027-05-04/DAY"], "SEAL_DOES_NOT_BIND_MANIFEST")
        s2 = slate()
        out = EV.evaluate([s2], COEF, regime="2027_REGULAR_CONFIRMATORY", chain=[], genesis_sha256="g" * 64)
        self.assertEqual(out["invalid_slates"]["2027-05-04/DAY"], "SEAL_NOT_IN_EVIDENCE_CHAIN")

    def test_pre_boundary_slate_is_drill_only(self):
        out = EV.evaluate([slate(date="2026-10-01", gtype="F")], COEF, regime="2026_POSTSEASON_SHADOW")
        self.assertEqual(out["invalid_slates"]["2026-10-01/DAY"], "PRE_V3_BOUNDARY_DRILL_ONLY")
        self.assertEqual(out["n_slates_used"], 0)

    def test_graded_file_must_link_to_shadow_board(self):
        s = slate()
        s["graded"]["source_board_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            EV.evaluate([s], COEF, regime="2027_REGULAR_CONFIRMATORY")

    def test_verdict_table_with_practical_threshold(self):
        c = lambda n, h, q: {"n_scored": n, "hit_rate": h, "mean_q": q}
        pos, neg = {"one_sided_lower95": 0.01}, {"one_sided_lower95": -0.01}
        self.assertEqual(EV.verdict_v3(c(200, .5, .55), c(200, .6, .55), pos, 80), "INSUFFICIENT_N")
        self.assertEqual(EV.verdict_v3(c(300, .55, .55), c(300, .55, .55), pos, 80), "REJECTED")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .56, .55), neg, 80), "INCONCLUSIVE")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .56, .60), pos, 80), "INCONCLUSIVE_CHALK_GUARD")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .53, .55), pos, 80), "POSITIVE_BELOW_PRACTICAL_THRESHOLD")
        self.assertEqual(EV.verdict_v3(c(300, .50, .55), c(300, .55, .55), pos, 80), "SUPPORTED_ADOPTABLE")


class Runner(unittest.TestCase):
    def test_prospective_refused_without_activation(self):
        os.environ.pop("MLB_V3_ACTIVATION", None)
        self.assertFalse(os.path.exists(RN.ACTIVATION_FILE))
        self.assertEqual(RN.main(["--mode", "prospective", "--repo", "/nonexistent", "--date", D, "--window", "DAY",
                                  "--out", "/nonexistent/out"]), 2)
        os.environ["MLB_V3_ACTIVATION"] = "JACOB_AUTHORIZED"
        try:
            self.assertEqual(RN.main(["--mode", "prospective", "--repo", "/nonexistent", "--date", D, "--window",
                                      "DAY", "--out", "/nonexistent/out"]), 2)
        finally:
            os.environ.pop("MLB_V3_ACTIVATION", None)


if __name__ == "__main__":
    unittest.main(verbosity=1)
