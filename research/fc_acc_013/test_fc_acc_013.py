#!/usr/bin/env python3
"""FC-ACC-013 integrity tests (synthetic starts, no network):
  * no leakage: changing a LATER start's outcome never changes an earlier row's A/B probabilities;
  * isolation: with zero BF dispersion everywhere, challenger B equals baseline A exactly;
  * mean preservation: the challenger's BF mixture keeps E[BF] (no clamp in range);
  * eligibility: fewer than three strictly-prior same-season starts is excluded."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import experiment as E  # noqa: E402


def synth(bf_fn, n_pitchers=12, n_starts=14, season=2024):
    out, gp = [], 1000
    for p in range(n_pitchers):
        for s in range(n_starts):
            gp += 1
            day = f"{season}-05-{1 + s * 2:02d}"
            bf = bf_fn(p, s)
            out.append({"season": season, "date": day, "game_pk": gp + p * 100, "pitcher_id": 500 + p,
                        "pitches": 90, "bf": bf, "outs": 15, "ks": (p + s) % 9})
    return out


class Integrity(unittest.TestCase):
    def test_no_leakage_from_later_outcomes(self):
        starts = synth(lambda p, s: 18 + (p * 7 + s * 3) % 11)
        rows, _ = E.build(starts)
        last_date = max(s["date"] for s in starts)
        mutated = copy.deepcopy(starts)
        for s in mutated:
            if s["date"] == last_date:
                s["ks"], s["bf"] = 0, 40
        rows2, _ = E.build(mutated)
        key = lambda r: (r["pitcher_id"], r["game_pk"])  # noqa: E731
        b = {key(r): r for r in rows2}
        for r in rows:
            if r["date"] < last_date:
                for t in E.THRESH:
                    self.assertEqual(r[f"A{t}"], b[key(r)][f"A{t}"])
                    self.assertEqual(r[f"B{t}"], b[key(r)][f"B{t}"])

    def test_zero_dispersion_challenger_equals_baseline(self):
        rows, _ = E.build(synth(lambda p, s: 22))
        self.assertTrue(rows)
        for r in rows:
            for t in E.THRESH:
                self.assertAlmostEqual(r[f"A{t}"], r[f"B{t}"], places=12)

    def test_mixture_preserves_expected_bf(self):
        rows, _ = E.build(synth(lambda p, s: 18 + (p * 7 + s * 3) % 11))
        for r in rows:
            if r["clamp_hits"] == 0:
                self.assertAlmostEqual(r["mix_mean"], r["ebf"], places=9)

    def test_eligibility_requires_three_prior_starts(self):
        rows, excluded = E.build(synth(lambda p, s: 22, n_pitchers=2, n_starts=5))
        self.assertEqual(excluded.get("fewer_than_three_prior_season_starts"), 6)
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()
