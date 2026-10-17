#!/usr/bin/env python3
"""Deterministic tests for preregistration v2: manifest.py + harness_v2.py (synthetic data only)."""
from __future__ import annotations

import copy
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
import harness_v2 as H2  # noqa: E402
import manifest as MF  # noqa: E402

COEF = H.load_coefficients()
CUT = "2027-04-01T17:00:00+00:00"
START = "2027-04-01T23:05:00Z"


def rec(cid, *, name="Al Bat", game="1", player="10", stat="hits", needs="1", side="over", odds=-120,
        p=0.62, rel="A", n=30, status="lean", qc="kept", assumed=False, version="2026.08.15"):
    return {"candidate_id": cid, "game_pk": game, "player_id": player, "player_name": name, "stat": stat,
            "needs": needs, "line": float(needs) - 0.5, "market_side": side,
            "prediction": {"hit_probability": p, "reliability": rel, "sample_n": n},
            "market": {"market_odds": odds}, "eligibility": {"qc_status": qc, "lineup_assumed": assumed},
            "selector": {"recommendation_status": status}, "provenance": {"model_version": version}}


def board(records, *, date="2027-04-01", sealed=CUT, generated=None, starts=None):
    b = {"date": date, "sealed_at": sealed, "board_generated_at": generated or sealed,
         "game_start_times": starts or {str(g): START for g in range(20)}, "records": records}
    b["board_sha256"] = H.canonical_board_hash(b)
    return b


def one(name, stat, needs, american, taken="2027-04-01T16:30:00+00:00", in_play=False, start=START):
    return {"taken_at": taken, "player_norm": MF.normalize_name(name), "stat": stat, "needs": needs,
            "american": american, "in_play": in_play, "start_time": start}


def two(name, market, needs, over, under, taken="2027-04-01T16:30:00+00:00"):
    return {"taken_at": taken, "market": market, "player_norm": MF.normalize_name(name), "needs": needs,
            "over_odds": over, "under_odds": under, "in_play": False}


def props(one_rows=(), two_rows=(), taken="2027-04-01T16:30:00+00:00"):
    return {"snapshots": [{"taken_at": taken, "rows": list(one_rows)}],
            "two_sided_snapshots": [{"taken_at": taken, "rows": list(two_rows)}]}


def reasons(m):
    return {r["candidate_id"]: r["exclusion_reason"] for r in m["rows"]}


class Manifest(unittest.TestCase):
    def test_eligible_with_exact_fresh_quote(self):
        m = MF.build_manifest(board([rec("a")]), [props([one("Al Bat", "hits", 1, -120)])])
        r = m["rows"][0]
        self.assertTrue(r["eligible"])
        self.assertEqual((r["quote_odds"], r["devig"], r["q_devig"]), (-120, "ONE_SIDED_NO_DEVIG", None))
        self.assertEqual(r["quote_source"], "data/props:snapshots")
        self.assertEqual(m["counts"]["eligible_primary"], 1)

    def test_every_exclusion_reason(self):
        recs = [rec("stale", name="S T", player="1"), rec("noq", name="Nobody", player="2"),
                rec("inplay", name="In Play", player="3"), rec("mismatch", name="Mis Match", player="4"),
                rec("under", name="Un Der", player="5", side="under"), rec("ambig", name="Am Big", player="6"),
                rec("band", name="Ba Nd", player="7", odds=-400), rec("relC", name="R C", player="8", rel="C"),
                rec("n0", name="N Zero", player="9", n=0), rec("lineup", name="L A", player="11", assumed=True),
                rec("qc", name="Q C", player="12", qc="rejected"),
                rec("combo", name="C K", player="13", stat="combined_strikeouts", needs="13"),
                rec("nrfi", name="N R", player="14", stat="nrfi_combined", side="nrfi"),
                rec("dup1", name="D U", player="15"), rec("dup2", name="D U", player="15"),
                rec("frac", name="F R", player="16", needs="1.5"), rec("ver", name="V E", player="17", version="2027.1"),
                rec("nostat", name="N S", player="18", status=None), rec("noprice", name="N P", player="19", odds=None),
                rec("noprob", name="N Pr", player="20", p=None),
                rec("started", name="St Arted", player="21", game="5")]
        rows = [one("In Play", "hits", 1, -120, in_play=True), one("Mis Match", "hits", 1, -125),
                one("Un Der", "hits", 1, -120), one("Am Big", "hits", 1, -120), one("Am Big", "hits", 1, -130),
                one("Ba Nd", "hits", 1, -400)] + [one(n, "hits", 1, -120) for n in ("R C", "N Zero", "L A", "D U")]
        P = props(rows)
        P["snapshots"].insert(0, {"taken_at": "2027-04-01T15:00:00+00:00", "rows": [one("S T", "hits", 1, -120)]})
        b = board(recs, starts={**{str(g): START for g in range(20)}, "5": "2027-04-01T16:59:00Z"})
        why = reasons(MF.build_manifest(b, [P]))
        self.maxDiff = None
        self.assertEqual(why, {
            "stale": "NOT_QUOTED_AT_CUTOFF", "noq": "NOT_QUOTED_AT_CUTOFF", "inplay": "IN_PLAY",
            "mismatch": "PRICE_MISMATCH_BOARD_VS_CAPTURE", "under": "SETTLEMENT_UNSUPPORTED", "ambig": "AMBIGUOUS_QUOTE",
            "band": "PRICE_OUTSIDE_BAND", "relC": "RELIABILITY_NOT_PUBLIC_ELIGIBLE", "n0": "NO_TRACK_RECORD",
            "lineup": "LINEUP_OR_STARTER_NOT_CONFIRMED", "qc": "QC_NOT_KEPT", "combo": "FAMILY_RED_FLAG_SEPARATE",
            "nrfi": "FAMILY_NOT_A_PLAYER_PROP", "dup1": "DUPLICATE_IDENTITY", "dup2": "DUPLICATE_IDENTITY",
            "frac": "SETTLEMENT_UNSUPPORTED", "ver": "MODEL_VERSION_NOT_FROZEN", "nostat": "NO_RECOMMENDATION_STATUS",
            "noprice": "NO_BOARD_PRICE", "noprob": "NO_PROBABILITY", "started": "GAME_STARTED"})

    def test_quote_age_limit_is_inclusive_45_minutes(self):
        b = board([rec("a")])
        ok = MF.build_manifest(b, [props([one("Al Bat", "hits", 1, -120)], taken="2027-04-01T16:15:00+00:00")])
        late = MF.build_manifest(b, [props([one("Al Bat", "hits", 1, -120)], taken="2027-04-01T16:14:59+00:00")])
        self.assertTrue(ok["rows"][0]["eligible"])
        self.assertEqual(late["rows"][0]["exclusion_reason"], "QUOTE_STALE")

    def test_capture_after_cutoff_is_invisible(self):
        P = props([one("Al Bat", "hits", 1, -150)], taken="2027-04-01T16:30:00+00:00")
        P["snapshots"].append({"taken_at": "2027-04-01T17:00:01+00:00", "rows": [one("Al Bat", "hits", 1, -120)]})
        r = MF.build_manifest(board([rec("a")]), [P])["rows"][0]
        self.assertEqual(r["exclusion_reason"], "PRICE_MISMATCH_BOARD_VS_CAPTURE")
        self.assertEqual(r["quote_taken_at"], "2027-04-01T16:30:00+00:00")

    def test_only_the_latest_capture_counts_absence_means_pulled(self):
        P = props([], taken="2027-04-01T16:50:00+00:00")
        P["snapshots"].insert(0, {"taken_at": "2027-04-01T16:40:00+00:00", "rows": [one("Al Bat", "hits", 1, -120)]})
        self.assertEqual(MF.build_manifest(board([rec("a")]), [P])["rows"][0]["exclusion_reason"], "NOT_QUOTED_AT_CUTOFF")

    def test_two_sided_devig_only_with_a_real_opposite_side(self):
        recs = [rec("k", name="Pi Tcher", stat="strikeouts", needs="6"),
                rec("o", name="Out Man", player="11", stat="pitcher_outs", needs="16", odds=-140),
                rec("solo", name="Solo Side", player="12", stat="strikeouts", needs="5"),
                rec("gone", name="Gone Side", player="13", stat="strikeouts", needs="5"),
                rec("amb2", name="Two Rows", player="14", stat="strikeouts", needs="5")]
        P = props(two_rows=[two("Pi Tcher", "strikeouts", 6, -120, 100), two("Out Man", "pitcher_outs", 16, -140, 110),
                            two("Solo Side", "strikeouts", 5, -120, None), two("Gone Side", "strikeouts", 5, None, -120),
                            two("Two Rows", "strikeouts", 5, -120, 100), two("Two Rows", "strikeouts", 5, -125, 105)])
        rows = {r["candidate_id"]: r for r in MF.build_manifest(board(recs), [P])["rows"]}
        a, b_ = H.implied(-120), H.implied(100)
        self.assertAlmostEqual(rows["k"]["q_devig"], a / (a + b_))
        self.assertEqual(rows["o"]["quote_odds"], -140)
        self.assertTrue(rows["solo"]["eligible"])
        self.assertEqual((rows["solo"]["devig"], rows["solo"]["q_devig"]), ("ONE_SIDED_NO_DEVIG", None))
        self.assertEqual(rows["gone"]["exclusion_reason"], "SIDE_NOT_OFFERED")
        self.assertEqual(rows["amb2"]["exclusion_reason"], "AMBIGUOUS_QUOTE")
        self.assertEqual(rows["k"]["quote_source"], "data/props:two_sided_snapshots")

    def test_board_stale_and_hash(self):
        b = board([rec("a")], generated="2027-04-01T12:59:59+00:00")
        self.assertEqual(MF.build_manifest(b, [props([one("Al Bat", "hits", 1, -120)])])["rows"][0]["exclusion_reason"],
                         "BOARD_STALE")
        bad = copy.deepcopy(board([rec("a")]))
        bad["records"][0]["market"]["market_odds"] = -110
        with self.assertRaises(ValueError):
            MF.build_manifest(bad, [props()])

    def test_manifest_hash_and_no_outcome_input(self):
        self.assertEqual(list(inspect.signature(MF.build_manifest).parameters), ["board", "props_files", "source_paths"])
        m = MF.build_manifest(board([rec("a")]), [props([one("Al Bat", "hits", 1, -120)])])
        MF.verify_manifest(m)
        t = copy.deepcopy(m)
        t["rows"][0]["champion"] = True
        with self.assertRaises(ValueError):
            MF.verify_manifest(t)

    def test_name_normalization(self):
        self.assertEqual(MF.normalize_name("Michael Harris II"), "michael harris")
        self.assertEqual(MF.normalize_name("José Ramírez Jr."), "jose ramirez")


def slate(date, recs, outcomes, *, sealed=None, one_rows=None, two_rows=None):
    sealed = sealed or f"{date}T17:00:00+00:00"
    b = board(recs, date=date, sealed=sealed, starts={str(g): f"{date}T23:05:00Z" for g in range(20)})
    P = props(one_rows or [], two_rows or [], taken=sealed.replace("17:00:00", "16:30:00"))
    for s in P["snapshots"] + P["two_sided_snapshots"]:
        for r in s["rows"]:
            r["taken_at"] = s["taken_at"]
            if "start_time" in r:
                r["start_time"] = f"{date}T23:05:00Z"
    m = MF.build_manifest(b, [P])
    g = {"source_board_sha256": b["board_sha256"], "records": [{"candidate_id": k, "grade": v} for k, v in outcomes.items()]}
    return m, g


def std_slate(date, flip=False):
    names = [f"P{i} X" for i in range(8)]
    recs = [rec(f"{date}-{i}", name=names[i], player=str(i), game=str(i % 4), p=0.55 + 0.02 * i, odds=-110 - 10 * i,
                status="top_pick" if i < 2 else "lean") for i in range(8)]
    recs.append(rec(f"{date}-ck", name="Combo K", player="99", stat="combined_strikeouts", needs="13",
                    status="top_pick"))
    recs.append(rec(f"{date}-tb", name="Total Bases", player="98", stat="total_bases", needs="2", status="top_pick"))
    rows = [one(names[i], "hits", 1, -110 - 10 * i) for i in range(8)] + [one("Total Bases", "total_bases", 2, -120)]
    out = {r["candidate_id"]: ("hit" if (i % 2 == 0) != flip else "miss") for i, r in enumerate(recs)}
    return slate(date, recs, out, one_rows=rows)


class Evaluate(unittest.TestCase):
    def test_equal_volume_primary_only_and_red_flag_reported(self):
        out = H2.evaluate([std_slate("2027-04-01")], COEF, regime="SMOKE_TEST_SYNTHETIC")
        self.assertEqual(out["slate_audit"]["2027-04-01"]["champion_n_d"], 2)
        for arm in ("CHAMPION", *H.SELECTORS):
            self.assertEqual(out["picking"][arm]["n_selected"], 2)
        self.assertEqual(out["combined_starter_strikeouts"]["champion_picks"], 1)
        self.assertEqual(out["combined_starter_strikeouts"]["status"], "RED_FLAG_SEPARATE_NOT_IN_PRIMARY_UNIVERSE")
        self.assertEqual(out["exploratory_families"]["champion_picks_by_stat"], {"total_bases": 1})
        self.assertNotIn("total_bases", out["picking"]["CHAMPION"]["market_mix"])

    def test_zero_champion_slate_means_no_picks_and_is_counted(self):
        m, g = std_slate("2027-04-01")
        for r in m["rows"]:
            r["champion"] = False
        m["manifest_sha256"] = MF.canonical_hash(m, "manifest_sha256")
        out = H2.evaluate([(m, g)], COEF, regime="SMOKE_TEST_SYNTHETIC")
        self.assertEqual(out["zero_champion_slates"], 1)
        self.assertTrue(all(out["picking"][a]["n_selected"] == 0 for a in out["picking"]))
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")

    def test_selection_ignores_outcomes(self):
        a = H2.evaluate([std_slate("2027-04-01")], COEF, regime="SMOKE_TEST_SYNTHETIC")
        b = H2.evaluate([std_slate("2027-04-01", flip=True)], COEF, regime="SMOKE_TEST_SYNTHETIC")
        for arm in a["picking"]:
            self.assertEqual(a["picking"][arm]["n_selected"], b["picking"][arm]["n_selected"])
            self.assertEqual(a["picking"][arm]["market_mix"], b["picking"][arm]["market_mix"])
        self.assertEqual(a["picking"]["C2_RESIDUAL"]["overlap"]["overlap"], b["picking"]["C2_RESIDUAL"]["overlap"]["overlap"])

    def test_guards(self):
        with self.assertRaises(ValueError):
            H2.evaluate([std_slate("2026-09-15")], COEF, regime="SMOKE_TEST_SYNTHETIC")
        early = std_slate("2026-10-02")   # sealed 2026-10-02T17:00Z is after the boundary: allowed
        self.assertEqual(H2.evaluate([early], COEF, regime="POSTSEASON_2026_SHADOW")["primary_verdict"],
                         "NOT_APPLICABLE_POSTSEASON_DESCRIPTIVE")
        m, g = slate("2026-10-02", [rec("x", status="top_pick")], {"x": "hit"}, sealed="2026-10-02T04:59:59+00:00",
                     one_rows=[one("Al Bat", "hits", 1, -120)])
        with self.assertRaises(ValueError):
            H2.evaluate([(m, g)], COEF, regime="POSTSEASON_2026_SHADOW")
        with self.assertRaises(ValueError):
            H2.evaluate([], COEF, regime="WHATEVER")

    def test_graded_must_link_and_manifest_must_rebuild(self):
        m, g = std_slate("2027-04-01")
        g2 = dict(g, source_board_sha256="0" * 64)
        with self.assertRaises(ValueError):
            H2.evaluate([(m, g2)], COEF, regime="SMOKE_TEST_SYNTHETIC")
        m2 = copy.deepcopy(m)
        m2["rows"][2]["eligible"] = False
        with self.assertRaises(ValueError):
            H2.evaluate([(m2, g)], COEF, regime="SMOKE_TEST_SYNTHETIC")

    def test_empty_window_and_confirmatory_minimum(self):
        out = H2.evaluate([], COEF, regime="CONFIRMATORY_2027_REGULAR")
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")
        self.assertEqual(out["probability_quality"]["primary_universe"], {"n": 0})
        out = H2.evaluate([std_slate(f"2027-04-0{d}") for d in range(1, 4)], COEF, regime="CONFIRMATORY_2027_REGULAR")
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")
        self.assertEqual(set(out["picking"]["C2_RESIDUAL"]["vs_champion"]), {"game", "player", "week"})
        self.assertEqual(out["picking"]["C2_RESIDUAL"]["roi_basis"][:24], "captured FanDuel quote a")

    def test_week_bootstrap_deterministic(self):
        rows = [{"date": f"2027-04-{1 + i % 20:02d}", "week": H2._week(f"2027-04-{1 + i % 20:02d}"), "game_pk": str(i % 5),
                 "player_id": str(i), "y": i % 2, "id": str(i)} for i in range(40)]
        self.assertEqual(H2.clustered_diff(rows[:20], rows[20:], "week"), H2.clustered_diff(rows[:20], rows[20:], "week"))
        self.assertEqual(H2._week("2027-04-01"), "2027-W13")


if __name__ == "__main__":
    unittest.main(verbosity=1)
