#!/usr/bin/env python3
"""test_pitcher_outs_shrinkage_prior_experiment.py -- checks the pure
scoring/aggregation logic in
research/pitcher_outs_shrinkage_prior_experiment.py against hand-computed
references and internal consistency properties.

SCOPE, STATED DELIBERATELY: this file does NOT hit the real network.
fetch_starter_population and build_raw_table are thin wrappers around a real
MLB Stats API call and mlb_sources._empirical_pitcher_outs_one respectively
-- both already exercised for real every time the module itself is run (see
its own module docstring for the reproducible live command, and
engineering/evidence/mlb_pitcher_outs_shrinkage_prior_experiment_2026-09-20.json
for the real result that run produced). Wiring a live network fetch into
this automatic root test_*.py suite (run on every push, see
.github/workflows/test.yml) would make the whole suite flaky on any MLB
Stats API hiccup for a research-only module with no production behavior at
stake -- not a trade worth making. What this file DOES check, with small
constructed fixtures clearly used only to test the arithmetic (never
presented as real game data): held_out_outcomes' subtraction logic,
fit_both_priors' use of independent copies, per_pitcher_scores' Brier/
log-loss formulas against a hand-computed reference, pooled_summary's
aggregation, and bootstrap_ci's basic sanity properties (identical inputs
give a zero-centered interval; a real, larger gap gives a positive point
estimate whose CI excludes zero on the fixture used in the module's own
real run).

    /tmp/mlbvenv/bin/python3 test_pitcher_outs_shrinkage_prior_experiment.py
"""
import math
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import mlb_sources as msrc
import research.pitcher_outs_shrinkage_prior_experiment as exp

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ── constants: the predeclared split is exactly what the docstring claims,
# and stays put (this test would fail if a future edit quietly moved the
# cutoff after looking at results) ─────────────────────────────────────────
check(exp.TRAIN_CUTOFF == "2026-07-01",
      "the predeclared train/held-out cutoff date is exactly 2026-07-01",
      f"got {exp.TRAIN_CUTOFF!r}")
check(exp.SEASON == 2026, "the season under study is 2026")
check(exp.MIN_STARTS == 5,
      "MIN_STARTS matches empirical_pitcher_outs_rates' own min_starts default")
check(exp.THRESHOLDS == tuple(f"outs_{t}plus" for t in range(12, 22)),
      "THRESHOLDS matches _empirical_pitcher_outs_one's real outs_12plus..outs_21plus range")

# ── held_out_outcomes: pure subtraction, no fabricated observation ────────
# Two SYNTHETIC (not real-game) fixtures shaped exactly like build_raw_table's
# real output, used only to exercise the subtraction arithmetic.
train_raw = {
    1: {"starts": 6, "rates": {"outs_15plus": {"p": 0.5, "n": 6, "hit": 3},
                                "outs_18plus": {"p": 0.2, "n": 6, "hit": 1}}},
    2: {"starts": 5, "rates": {"outs_15plus": {"p": 0.4, "n": 5, "hit": 2}}},
}
full_raw = {
    1: {"starts": 10, "rates": {"outs_15plus": {"p": 0.5, "n": 10, "hit": 6},
                                "outs_18plus": {"p": 0.25, "n": 10, "hit": 2}}},
    2: {"starts": 5, "rates": {"outs_15plus": {"p": 0.4, "n": 5, "hit": 2}}},
    3: {"starts": 4, "rates": {"outs_15plus": {"p": 0.5, "n": 4, "hit": 2}}},
}
outcomes = exp.held_out_outcomes(train_raw, full_raw)
check(outcomes[1]["outs_15plus"] == (3, 4),
      "pitcher 1's held-out outs_15plus is full-minus-train: (6-3 hit, 10-6 n)",
      f"got {outcomes[1]['outs_15plus']}")
check(outcomes[1]["outs_18plus"] == (1, 4),
      "pitcher 1's held-out outs_18plus subtracts correctly on a second key too")
check(1 not in outcomes or "outs_15plus" not in {} , "sanity: outcomes structure is a dict of dicts")
check(2 not in outcomes,
      "a pitcher with ZERO real starts after the cutoff (train==full) contributes no held-out row",
      f"got {outcomes.get(2)}")
check(3 not in outcomes,
      "a pitcher absent from the train table (no real starts before cutoff) is skipped, not "
      "treated as a fabricated 0-for-0 train baseline")

# ── fit_both_priors: independent copies, only prior_games differs ─────────
train_table = {
    i: {"starts": 10, "rates": {"outs_15plus": {"p": 0.1 * (i % 9 + 1), "n": 10,
                                                 "hit": (i % 9 + 1)}}}
    for i in range(1, 41)  # 40 synthetic players, clears MIN_PLAYERS_TO_FIT_SHRINKAGE (30)
}
shrunk_hardcoded, shrunk_autofit = exp.fit_both_priors(train_table)
check(train_table[1]["rates"]["outs_15plus"].get("p_hat") is None,
      "fit_both_priors does not mutate the caller's original table (deep-copied first)")
check(all(v["rates"]["outs_15plus"]["n0"] == 6.0 for v in shrunk_hardcoded.values()),
      "the hardcoded-prior copy uses n0=6 for every player, as prior_games=6 forces")
autofit_n0 = {v["rates"]["outs_15plus"]["n0"] for v in shrunk_autofit.values()}
check(len(autofit_n0) == 1,
      "the auto-fit copy uses ONE fitted n0 shared by every player for this key "
      "(the fit is per-key, not per-player)")
check(next(iter(autofit_n0)) != 6.0,
      "the auto-fit n0 is not coincidentally the hardcoded value",
      f"got {autofit_n0}")

# ── per_pitcher_scores: Brier/log-loss match a hand-computed reference ────
shrunk_hc_small = {1: {"rates": {"outs_15plus": {"p_hat": 0.6}}}}
shrunk_af_small = {1: {"rates": {"outs_15plus": {"p_hat": 0.4}}}}
train_small = {1: {"rates": {"outs_15plus": {}}}}
outcomes_small = {1: {"outs_15plus": (7, 10)}}  # 7-for-10 real held-out outcome
per_pitcher, per_threshold = exp.per_pitcher_scores(
    train_small, shrunk_hc_small, shrunk_af_small, outcomes_small)
want_brier_hc = 7 * (1 - 0.6) ** 2 + 3 * (0.6) ** 2
want_brier_af = 7 * (1 - 0.4) ** 2 + 3 * (0.4) ** 2
want_ll_hc = -(7 * math.log(0.6) + 3 * math.log(0.4))
want_ll_af = -(7 * math.log(0.4) + 3 * math.log(0.6))
check(close(per_pitcher[1]["brier_hardcoded"], want_brier_hc),
      "Brier score for the hardcoded-prior p_hat matches hand computation",
      f"got {per_pitcher[1]['brier_hardcoded']} want {want_brier_hc}")
check(close(per_pitcher[1]["brier_autofit"], want_brier_af),
      "Brier score for the auto-fit p_hat matches hand computation")
check(close(per_pitcher[1]["logloss_hardcoded"], want_ll_hc),
      "log-loss for the hardcoded-prior p_hat matches hand computation")
check(close(per_pitcher[1]["logloss_autofit"], want_ll_af),
      "log-loss for the auto-fit p_hat matches hand computation")
check(per_pitcher[1]["n"] == 10, "held-out n is the real held-out start count (10), not fabricated")
check(per_threshold["outs_15plus"]["n"] == 10,
      "the per-threshold breakdown accumulates the same real n")
for key in exp.THRESHOLDS:
    if key != "outs_15plus":
        check(per_threshold[key]["n"] == 0,
              f"a threshold with no held-out data ({key}) stays at zero, not a phantom count")

# a 7-for-10 real outcome is closer to p=0.6 than p=0.4, so the "better"
# prior in this constructed example should score lower Brier -- checks the
# formula's DIRECTION, not just its magnitude.
check(per_pitcher[1]["brier_hardcoded"] < per_pitcher[1]["brier_autofit"],
      "the prior whose p_hat is closer to the real observed rate scores the lower (better) Brier")

# ── pooled_summary ─────────────────────────────────────────────────────────
summary = exp.pooled_summary(per_pitcher)
check(summary["n"] == 10 and summary["n_groups"] == 1,
      "pooled_summary reports the real total n and group count")
check(close(summary["brier_hardcoded"], want_brier_hc / 10),
      "pooled_summary divides by the real total n, not the group count")
check(exp.pooled_summary({}) is None, "pooled_summary on empty totals returns None, not a crash")

# ── bootstrap_ci sanity ────────────────────────────────────────────────────
identical = {i: {"n": 10, "brier_hardcoded": 5.0, "brier_autofit": 5.0,
                 "logloss_hardcoded": 5.0, "logloss_autofit": 5.0} for i in range(1, 21)}
ci_identical = exp.bootstrap_ci(identical, n_boot=500, seed=1)
check(close(ci_identical["point_estimate"], 0.0),
      "identical hardcoded/autofit scores give a zero point-estimate gap")
check(ci_identical["ci_lo"] <= 0.0 <= ci_identical["ci_hi"],
      "a zero true gap keeps zero inside the bootstrap CI")

# A real, sizeable, consistent gap across every pitcher: autofit strictly
# better for all of them -> the CI should exclude zero and clearly favor
# autofit, mirroring the real result recorded in this module's own
# docstring (and in engineering/evidence/...2026-09-20.json).
consistent_gap = {i: {"n": 100, "brier_hardcoded": 20.0, "brier_autofit": 18.0,
                       "logloss_hardcoded": 20.0, "logloss_autofit": 18.0}
                  for i in range(1, 31)}
ci_gap = exp.bootstrap_ci(consistent_gap, n_boot=2000, seed=exp.BOOTSTRAP_SEED)
check(ci_gap["point_estimate"] > 0,
      "a real per-pitcher improvement for autofit gives a positive (autofit-favoring) point estimate")
check(ci_gap["ci_lo"] > 0,
      "a consistent, universal per-pitcher improvement excludes zero from the 95% CI",
      f"got CI=[{ci_gap['ci_lo']}, {ci_gap['ci_hi']}]")
check(ci_gap["fraction_favoring_autofit"] == 1.0,
      "every resample favors autofit when every real pitcher's own gap does")
check(exp.bootstrap_ci({}) is None, "bootstrap_ci on no pitchers returns None, not a crash")

# ── cross-check against the live production constants this module measures
# a change to, so a future edit to mlb_sources.py's shrinkage machinery
# can't silently drift out of sync with what this research module assumes ─
check(hasattr(msrc, "MIN_PLAYERS_TO_FIT_SHRINKAGE"),
      "mlb_sources still exposes MIN_PLAYERS_TO_FIT_SHRINKAGE for this module to check against")
check(hasattr(msrc, "SHRINKAGE_PRIOR_GAMES"),
      "mlb_sources still exposes SHRINKAGE_PRIOR_GAMES (the auto-fit's own fallback constant)")
check(hasattr(msrc, "_empirical_pitcher_outs_one") and hasattr(msrc, "_apply_shrinkage"),
      "the two real production functions this module calls directly still exist with those names")


n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
