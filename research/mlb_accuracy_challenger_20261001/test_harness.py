#!/usr/bin/env python3
"""Deterministic tests for the MLB accuracy challenger harness (synthetic boards only)."""
from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

COEF = H.load_coefficients()
BOUNDARY = "2026-10-01T18:00:00Z"


def rec(cid, *, game="1", player="10", stat="hits", p=0.62, odds=-120, status="lean",
        qc="kept", assumed=False, version="2026.08.15"):
    return {"candidate_id": cid, "game_pk": game, "player_id": player, "stat": stat,
            "prediction": {"hit_probability": p}, "market": {"market_odds": odds},
            "eligibility": {"qc_status": qc, "lineup_assumed": assumed},
            "selector": {"recommendation_status": status}, "provenance": {"model_version": version}}


def board(date, records, sealed="2027-04-01T17:00:00Z"):
    b = {"date": date, "sealed_at": sealed, "records": records}
    b["board_sha256"] = H.canonical_board_hash(b)
    return b


def graded(b, outcomes):
    return {"source_board_sha256": b["board_sha256"],
            "records": [{"candidate_id": cid, "grade": g} for cid, g in outcomes.items()]}


class Universe(unittest.TestCase):
    def test_eligibility_rules(self):
        b = board("2027-04-01", [
            rec("ok"), rec("qc", qc="rejected"), rec("lineup", assumed=True), rec("noprice", odds=None),
            rec("nostatus", status=None), rec("oldver", version="2027.01.01"),
            rec("chalk", odds=-400), rec("longshot", odds=+300), rec("champ_out", odds=-400, status="top_pick")])
        rows, why = H.universe(b, graded(b, {}), COEF)
        self.assertEqual([r["id"] for r in rows], ["ok"])
        self.assertEqual(why, {"qc_not_kept": 1, "lineup_assumed": 1, "no_posted_price_or_probability": 1,
                               "no_recommendation_status": 1, "model_version_not_frozen": 1,
                               "price_outside_band": 2, "price_outside_band_champion": 1})

    def test_band_edges_inclusive(self):
        # -233.33 -> 0.70, +150 -> 0.40
        b = board("2027-04-01", [rec("lo", odds=150), rec("hi", odds=-700 / 3)])
        rows, _ = H.universe(b, graded(b, {}), COEF)
        self.assertEqual(sorted(r["id"] for r in rows), ["hi", "lo"])

    def test_hash_and_link_are_verified(self):
        b = board("2027-04-01", [rec("a")])
        bad = copy.deepcopy(b)
        bad["records"][0]["market"]["market_odds"] = -110
        with self.assertRaises(ValueError):
            H.universe(bad, graded(b, {}), COEF)
        g = graded(b, {})
        g["source_board_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            H.universe(b, g, COEF)

    def test_family_coefficients_and_pooled_fallback(self):
        r = H.score({"p0": 0.6, "q": 0.55, "family": "pitcher_outs"}, COEF)
        beta = COEF["coefficients"]["p2"]["pitcher_outs"]["beta"]
        self.assertAlmostEqual(r["p2"], H.logistic(beta[0] + beta[1] * H.logit(0.6) + beta[2] * H.logit(0.55)))
        u = H.score({"p0": 0.6, "q": 0.55, "family": "not_a_family"}, COEF)
        pb = COEF["coefficients"]["p2"]["_pooled"]["beta"]
        self.assertAlmostEqual(u["p2"], H.logistic(pb[0] + pb[1] * H.logit(0.6) + pb[2] * H.logit(0.55)))


class Selection(unittest.TestCase):
    def rows(self):
        b = board("2027-04-01", [rec(f"c{i}", game=str(i), p=0.55 + i * 0.02, odds=-110 - 10 * i,
                                     status="top_pick" if i < 2 else "lean") for i in range(8)])
        return H.universe(b, graded(b, {f"c{i}": "hit" for i in range(8)}), COEF)[0]

    def test_equal_volume_every_arm(self):
        n, sel = H.select(self.rows())
        self.assertEqual(n, 2)
        self.assertTrue(all(len(v) == 2 for v in sel.values()))

    def test_no_champion_means_no_picks(self):
        rows = [dict(r, champion=False) for r in self.rows()]
        n, sel = H.select(rows)
        self.assertEqual(n, 0)
        self.assertTrue(all(len(v) == 0 for v in sel.values()))

    def test_selection_ignores_outcomes(self):
        a = self.rows()
        b = [dict(r, y=1 - r["y"]) for r in a]
        sa, sb = H.select(a)[1], H.select(b)[1]
        self.assertEqual({k: [r["id"] for r in v] for k, v in sa.items()},
                         {k: [r["id"] for r in v] for k, v in sb.items()})

    def test_ties_break_by_id(self):
        rows = [dict(r, resid=0.0) for r in self.rows()]
        self.assertEqual([r["id"] for r in H.select(rows)[1]["C2_RESIDUAL"]], ["c0", "c1"])


class Guards(unittest.TestCase):
    def test_refuses_dev_window(self):
        b = board("2026-09-15", [rec("a", status="top_pick")])
        with self.assertRaises(ValueError):
            H.evaluate([(b, graded(b, {"a": "hit"}))], COEF, boundary_utc=BOUNDARY, regime="SMOKE_TEST_SYNTHETIC")

    def test_refuses_board_sealed_before_boundary(self):
        b = board("2026-10-01", [rec("a", status="top_pick")], sealed="2026-10-01T16:00:00Z")
        with self.assertRaises(ValueError):
            H.evaluate([(b, graded(b, {"a": "hit"}))], COEF, boundary_utc=BOUNDARY, regime="POSTSEASON_2026_SHADOW")

    def test_boundary_compares_parsed_times(self):
        # 18:00:00.5+00:00 is after an 18:00:00Z boundary (string order would say otherwise)
        b = board("2026-10-02", [rec("a", status="top_pick")], sealed="2026-10-01T18:00:00.500000+00:00")
        out = H.evaluate([(b, graded(b, {"a": "hit"}))], COEF, boundary_utc="2026-10-01T18:00:00Z",
                         regime="POSTSEASON_2026_SHADOW")
        self.assertEqual(out["arms"]["CHAMPION"]["n_scored"], 1)
        late = board("2026-10-02", [rec("a", status="top_pick")], sealed="2026-10-01T13:00:00-05:00")
        with self.assertRaises(ValueError):  # 18:00Z exactly is not strictly after
            H.evaluate([(late, graded(late, {"a": "hit"}))], COEF, boundary_utc="2026-10-01T18:00:00Z",
                       regime="POSTSEASON_2026_SHADOW")

    def test_unknown_regime_refused(self):
        with self.assertRaises(ValueError):
            H.evaluate([], COEF, boundary_utc=BOUNDARY, regime="WHATEVER")

    def test_postseason_never_gets_a_primary_verdict(self):
        b = board("2026-10-05", [rec("a", status="top_pick"), rec("b")], sealed="2026-10-05T17:00:00Z")
        out = H.evaluate([(b, graded(b, {"a": "hit", "b": "miss"}))], COEF, boundary_utc=BOUNDARY,
                         regime="POSTSEASON_2026_SHADOW")
        self.assertEqual(out["primary_verdict"], "NOT_APPLICABLE_POSTSEASON_DESCRIPTIVE")


class Verdict(unittest.TestCase):
    def s(self, n, h, q):
        return {"n_scored": n, "hit_rate": h, "mean_q": q}

    def test_rules(self):
        ci_pos, ci_neg = {"one_sided_lower95": 0.01}, {"one_sided_lower95": -0.01}
        self.assertEqual(H.verdict(self.s(100, .5, .55), self.s(100, .6, .55), ci_pos, 80), "INSUFFICIENT_N")
        self.assertEqual(H.verdict(self.s(300, .5, .55), self.s(300, .6, .55), ci_pos, 40), "INSUFFICIENT_N")
        self.assertEqual(H.verdict(self.s(300, .55, .55), self.s(300, .54, .55), ci_pos, 80), "REJECTED")
        self.assertEqual(H.verdict(self.s(300, .5, .55), self.s(300, .6, .55), ci_pos, 80), "SUPPORTED")
        self.assertEqual(H.verdict(self.s(300, .5, .55), self.s(300, .6, .55), ci_neg, 80), "INCONCLUSIVE")
        self.assertEqual(H.verdict(self.s(300, .5, .55), self.s(300, .6, .60), ci_pos, 80),
                         "INCONCLUSIVE_CHALK_GUARD")

    def test_bootstrap_is_deterministic(self):
        rows = [{"date": "d", "game_pk": str(i % 5), "player_id": str(i), "y": i % 2, "id": str(i)} for i in range(40)]
        a = H.clustered_diff(rows[:20], rows[20:], "game")
        b = H.clustered_diff(rows[:20], rows[20:], "game")
        self.assertEqual(a, b)


class SmokeTest(unittest.TestCase):
    def test_end_to_end_synthetic(self):
        slates = []
        for d in range(3):
            date = f"2027-04-0{d + 1}"
            recs = [rec(f"{date}-{i}", game=str(i % 4), player=str(i), p=0.6 + 0.01 * i, odds=-115 - 5 * i,
                        stat="pitcher_outs" if i % 3 == 0 else "hits", status="top_pick" if i < 2 else "lean")
                    for i in range(10)]
            b = board(date, recs)
            slates.append((b, graded(b, {r["candidate_id"]: ("hit" if i % 2 else "miss")
                                          for i, r in enumerate(recs)})))
        out = H.evaluate(slates, COEF, boundary_utc=BOUNDARY, regime="SMOKE_TEST_SYNTHETIC")
        self.assertEqual(out["arms"]["CHAMPION"]["n_scored"], 6)
        self.assertEqual(out["arms"]["C2_RESIDUAL"]["n_scored"], 6)
        self.assertEqual(out["primary_verdict"], "INSUFFICIENT_N")
        self.assertIn("c3_pitcher_outs", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
