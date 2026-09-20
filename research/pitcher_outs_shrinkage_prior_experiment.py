#!/usr/bin/env python3
"""research/pitcher_outs_shrinkage_prior_experiment.py -- research-only
measurement, NOT wired into the live pipeline. Answers a single question
left open in mlb_sources.py's own comments:

HYPOTHESIS
----------
mlb_sources.empirical_pitcher_outs_rates hardcodes prior_games=6 for the
Beta-Binomial shrinkage _apply_shrinkage applies to the "Pitcher Outs
Recorded" market, with its own comment saying this was "borrowed" from
empirical_pitcher_k_rates' independently-audited strikeout-rate constant,
"no separate fit was done for this market yet." _apply_shrinkage already
supports prior_games=None, which fits the Beta-Binomial concentration n0
per threshold via _fit_shrinkage_n0's golden-section MLE search, using that
threshold's own real (hit, n) pairs, gated by MIN_PLAYERS_TO_FIT_SHRINKAGE.

Question: on REAL held-out pitcher_outs data, does the auto-fit
(prior_games=None) produce better-calibrated probabilities than the
hardcoded prior_games=6? This module measures that, honestly, in either
direction -- see the docstring of run_experiment for the actual result of
the run that produced engineering/evidence/
mlb_pitcher_outs_shrinkage_prior_experiment_2026-09-20.json.

METHOD
------
1. Real starting-pitcher population: MLB Stats API season pitching leaders
   for TRAIN_CUTOFF's season, playerPool=ALL (not just ERA-title
   qualifiers), filtered to pitchers with >= MIN_STARTS starts across the
   whole season -- the same min_starts default empirical_pitcher_outs_rates
   and backtest/engine.py's own call to it already use.
2. For each pitcher, real per-start (outs recorded) data comes from the
   EXACT same real source the live pipeline uses: mlb_sources._game_log via
   mlb_sources._empirical_pitcher_outs_one (called directly, not
   reimplemented -- see build_raw_table), which is also what
   backtest/engine.py calls at line ~927
   (`msrc.empirical_pitcher_outs_rates(starter_ids.values(), asof=cutoff)`).
   No (hit, n) pair or outcome here is fabricated; every one is a real
   parsed MLB Stats API gameLog split.
3. TRAIN: _empirical_pitcher_outs_one(pid, min_starts=1, asof=TRAIN_CUTOFF)
   for every pitcher in the population -- real starts on or before the
   cutoff only, exactly mirroring what empirical_pitcher_outs_rates(ids,
   asof=TRAIN_CUTOFF) builds internally before its own shrinkage call.
   Pitchers are further filtered to >= MIN_STARTS real starts BEFORE the
   cutoff (train_min_starts_table) -- the same point-in-time population
   size gate the live/backtest path would apply.
4. HELD-OUT: the SAME real fetch with asof=None (all real starts through
   the day this module was run) minus the train counts, by subtraction
   (held = full - train). Because both counts come from the identical
   asof-filtered real fetcher applied to the identical season, this is
   exactly "starts with date > TRAIN_CUTOFF," with no double-count and no
   synthetic observation (see held_out_outcomes).
5. Two Beta-Binomial fits are applied to two independent deep copies of the
   IDENTICAL real train (hit, n) pairs: prior_games=6 (today's hardcoded
   value) and prior_games=None (the auto-fit). The only thing that differs
   between the two resulting p_hat tables is which shrinkage prior was
   used -- see fit_both_priors.
6. Both p_hat tables are scored against the real held-out outcomes with two
   independent proper scoring rules -- Brier score and log-loss -- chosen
   because both are strictly proper (a forecaster cannot improve either by
   reporting anything other than their true belief), and using two
   different ones guards against a result that is an artifact of one
   metric's own curvature. A pitcher-clustered bootstrap (resampling
   PITCHERS, not individual threshold rows, since a pitcher's ten
   thresholds are not independent draws) gives a confidence interval on the
   Brier-score difference, so "which is better" is not read off a single
   point estimate.

PREDECLARED TRAIN/HELD-OUT SPLIT
---------------------------------
Committed in this file, by DATE, BEFORE any held-out number in this
module's docstring or engineering/evidence/ output was computed, and never
adjusted afterward:

    SEASON       = 2026
    TRAIN_CUTOFF = "2026-07-01"   (real starts on/before this date are
                                    "train"; everything after is "held-out")
    MIN_STARTS   = 5              (population + train-population gate,
                                    matching empirical_pitcher_outs_rates'
                                    own default and backtest/engine.py's
                                    unmodified call to it)

2026 is used (rather than a fully-closed prior season) because
mlb_sources._game_log defaults its season parameter to mlb_daily.YEAR,
which is 2026 as of this research -- i.e. this reuses the exact same
season-selection behaviour the live pipeline itself has today, with no
extra season-override plumbing added to any production function.
TRAIN_CUTOFF = 2026-07-01 was picked as the season's rough midpoint /
near the real All-Star break, before this module ran a single held-out
comparison -- purely a "split the season in two" choice, not tuned to make
either shrinkage prior look better.

Run reproducibly (hits the real network -- MLB Stats API, no mock, no
cache):
    PYTHONPATH=. python3 research/pitcher_outs_shrinkage_prior_experiment.py

DELIBERATE SIMPLIFICATION, STATED RATHER THAN HIDDEN: this is a single
static train/held-out split, not a rolling day-by-day walk-forward
simulation the way backtest/engine.py replays a slate. p_hat is fit ONCE
at TRAIN_CUTOFF and then scored against every real held-out start through
the day this module ran, unchanged. That is a real simplification relative
to how the live pipeline actually recomputes nightly -- but it does not
bias the COMPARISON between the two priors, since both are fit on the
identical frozen train snapshot and scored against the identical held-out
outcomes; only prior_games differs between them.

WHAT THIS DOES NOT DO: it does not change mlb_sources.py, generate_picks.py,
or any file the live pipeline imports; it makes no selector, scoring, or
production behavior change. It is a measurement only.
"""
import copy
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor

import mlb_daily as m
import mlb_sources as msrc

SEASON = 2026
TRAIN_CUTOFF = "2026-07-01"
MIN_STARTS = 5
THRESHOLDS = tuple(f"outs_{t}plus" for t in range(12, 22))
N_BOOTSTRAP = 5000
# Seeded with the predeclared cutoff date itself so the seed is documented
# and reproducible, not picked after seeing a result.
BOOTSTRAP_SEED = 20260701


def fetch_starter_population(season=SEASON, min_starts=MIN_STARTS):
    """Real starting-pitcher population for `season`: MLB Stats API season
    pitching leaders, playerPool=ALL (every pitcher with a game, not just
    ERA-title qualifiers -- QUALIFIED would only return ~50 names), filtered
    to real gamesStarted >= min_starts across the whole season. Returns a
    sorted list of real MLBAM pitcher ids."""
    r = m.retry_get(f"{msrc.STATS_API}/stats",
                     params={"stats": "season", "group": "pitching",
                             "season": season, "sportId": 1,
                             "limit": 1000, "playerPool": "ALL"},
                     headers=msrc.UA, timeout=25, retries=2)
    r.raise_for_status()
    stats = r.json().get("stats") or []
    splits = (stats[0].get("splits") or []) if stats else []
    ids = []
    for s in splits:
        gs = (s.get("stat") or {}).get("gamesStarted") or 0
        if gs >= min_starts:
            pid = (s.get("player") or {}).get("id")
            if pid:
                ids.append(int(pid))
    return sorted(set(ids))


def build_raw_table(pitcher_ids, asof, min_starts=1, max_workers=12):
    """The real per-pitcher outs-threshold (hit, n) table, built by calling
    mlb_sources._empirical_pitcher_outs_one DIRECTLY -- the exact private
    function empirical_pitcher_outs_rates itself calls -- for every pitcher,
    stopping BEFORE empirical_pitcher_outs_rates' own final
    `_apply_shrinkage(out, prior_games=6)` call. This module needs the raw
    (pre-shrinkage) real table so it can apply BOTH competing priors to the
    identical real data; it never re-derives the game-log fetch or the
    outs-per-start parsing itself.

    min_starts=1 here (not this module's own MIN_STARTS) deliberately
    passes every real fetch through regardless of start count, so the SAME
    raw table can support both the >=MIN_STARTS-gated train population and
    the full-season subtraction used by held_out_outcomes -- min_starts only
    changes _empirical_pitcher_outs_one's None-vs-dict return gate, never
    the real hit/n content of what it returns."""
    ids = [int(p) for p in dict.fromkeys(pitcher_ids) if p]
    out = {}
    if not ids:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        jobs = [(i, min_starts, asof) for i in ids]
        for pid, res in ex.map(msrc._empirical_pitcher_outs_one, jobs):
            if res:
                out[pid] = res
    return out


def held_out_outcomes(train_raw, full_raw):
    """Real per-pitcher/per-threshold held-out (hit, n) pair for starts
    strictly AFTER TRAIN_CUTOFF, computed by subtraction: full-season minus
    train-window real counts. Both `train_raw` and `full_raw` must come from
    build_raw_table on the SAME pitcher/season with min_starts=1, one with
    asof=TRAIN_CUTOFF and one with asof=None -- then
    held_n = full_n - train_n and held_hit = full_hit - train_hit are
    exactly "how many of this pitcher's real starts AFTER the cutoff
    recorded at least this many outs," never a fabricated or resampled
    number.

    Returns {pid: {threshold_key: (held_hit, held_n)}}, omitting any
    pitcher/threshold with zero held-out starts (nothing to evaluate)."""
    out = {}
    for pid, full_tbl in full_raw.items():
        train_tbl = train_raw.get(pid)
        if not train_tbl:
            continue
        pid_out = {}
        for key, full_r in full_tbl["rates"].items():
            train_r = train_tbl["rates"].get(key)
            if not train_r:
                continue
            held_n = full_r["n"] - train_r["n"]
            held_hit = full_r["hit"] - train_r["hit"]
            if held_n > 0:
                pid_out[key] = (held_hit, held_n)
        if pid_out:
            out[pid] = pid_out
    return out


def fit_both_priors(train_min_starts_table):
    """Applies BOTH shrinkage variants to independent deep copies of the
    IDENTICAL real train (hit, n) pairs, via mlb_sources._apply_shrinkage
    itself (not reimplemented) -- so the only difference between the two
    resulting p_hat tables is prior_games (today's hardcoded 6, vs the
    auto-fit None). Returns (shrunk_hardcoded, shrunk_autofit)."""
    shrunk_hardcoded = msrc._apply_shrinkage(copy.deepcopy(train_min_starts_table), prior_games=6)
    shrunk_autofit = msrc._apply_shrinkage(copy.deepcopy(train_min_starts_table), prior_games=None)
    return shrunk_hardcoded, shrunk_autofit


def _clip(p, eps=1e-6):
    return min(max(p, eps), 1 - eps)


def per_pitcher_scores(train_min_starts_table, shrunk_hardcoded, shrunk_autofit, outcomes):
    """Real per-pitcher, summed-across-threshold Brier-score and log-loss
    totals for each shrinkage variant, scored against the real held-out
    outcomes. Two strictly proper scoring rules, both computed at the
    individual real held-out START level (a pitcher's held_n real starts
    are each scored against the same frozen p_hat, then summed) --

        brier = held_hit*(1-p)**2 + (held_n-held_hit)*p**2
        logloss = -[held_hit*log(p) + (held_n-held_hit)*log(1-p)]

    Returns (per_pitcher, per_threshold), both dicts of running totals
    ({"n", "brier_hardcoded", "brier_autofit", "logloss_hardcoded",
    "logloss_autofit"})."""
    per_pitcher = {}
    per_threshold = {key: {"n": 0, "brier_hardcoded": 0.0, "brier_autofit": 0.0,
                            "logloss_hardcoded": 0.0, "logloss_autofit": 0.0}
                     for key in THRESHOLDS}
    for pid, tbl in train_min_starts_table.items():
        held = outcomes.get(pid)
        if not held:
            continue
        agg = {"n": 0, "brier_hardcoded": 0.0, "brier_autofit": 0.0,
               "logloss_hardcoded": 0.0, "logloss_autofit": 0.0}
        for key in tbl["rates"]:
            if key not in held:
                continue
            held_hit, held_n = held[key]
            p6 = shrunk_hardcoded[pid]["rates"][key]["p_hat"]
            pauto = shrunk_autofit[pid]["rates"][key]["p_hat"]
            p6c, pautoc = _clip(p6), _clip(pauto)
            b6 = held_hit * (1 - p6) ** 2 + (held_n - held_hit) * p6 ** 2
            bauto = held_hit * (1 - pauto) ** 2 + (held_n - held_hit) * pauto ** 2
            l6 = -(held_hit * math.log(p6c) + (held_n - held_hit) * math.log(1 - p6c))
            lauto = -(held_hit * math.log(pautoc) + (held_n - held_hit) * math.log(1 - pautoc))
            agg["n"] += held_n
            agg["brier_hardcoded"] += b6
            agg["brier_autofit"] += bauto
            agg["logloss_hardcoded"] += l6
            agg["logloss_autofit"] += lauto
            pt = per_threshold[key]
            pt["n"] += held_n
            pt["brier_hardcoded"] += b6
            pt["brier_autofit"] += bauto
            pt["logloss_hardcoded"] += l6
            pt["logloss_autofit"] += lauto
        if agg["n"] > 0:
            per_pitcher[pid] = agg
    return per_pitcher, per_threshold


def pooled_summary(totals):
    """Reduces a per_pitcher or per_threshold totals dict to overall mean
    Brier score / log-loss for each prior variant, pooled over every real
    held-out start-observation it contains."""
    n = sum(v["n"] for v in totals.values())
    if n == 0:
        return None
    return {
        "n": n,
        "n_groups": len(totals),
        "brier_hardcoded": sum(v["brier_hardcoded"] for v in totals.values()) / n,
        "brier_autofit": sum(v["brier_autofit"] for v in totals.values()) / n,
        "logloss_hardcoded": sum(v["logloss_hardcoded"] for v in totals.values()) / n,
        "logloss_autofit": sum(v["logloss_autofit"] for v in totals.values()) / n,
    }


def bootstrap_ci(per_pitcher, n_boot=N_BOOTSTRAP, seed=BOOTSTRAP_SEED, metric="brier"):
    """Pitcher-clustered bootstrap on (metric_hardcoded - metric_autofit),
    resampling PITCHERS with replacement (never individual threshold rows,
    which are correlated within one pitcher's own ten thresholds) so the
    interval reflects real between-pitcher variance rather than an inflated
    row count. Positive means the hardcoded prior scored worse, i.e. the
    auto-fit wins.

    Returns {"point_estimate", "ci_lo", "ci_hi",
    "fraction_favoring_autofit", "n_pitchers"}, or None if there is nothing
    to evaluate."""
    pids = list(per_pitcher.keys())
    if not pids:
        return None

    def diff_for(sample):
        n = sum(per_pitcher[p]["n"] for p in sample)
        hc = sum(per_pitcher[p][f"{metric}_hardcoded"] for p in sample)
        af = sum(per_pitcher[p][f"{metric}_autofit"] for p in sample)
        return (hc / n) - (af / n)

    point = diff_for(pids)
    rng = random.Random(seed)
    diffs = sorted(diff_for([rng.choice(pids) for _ in pids]) for _ in range(n_boot))
    lo = diffs[int(0.025 * n_boot)]
    hi = diffs[min(n_boot - 1, int(0.975 * n_boot))]
    frac_favoring_autofit = sum(1 for d in diffs if d > 0) / n_boot
    return {"point_estimate": point, "ci_lo": lo, "ci_hi": hi,
            "fraction_favoring_autofit": frac_favoring_autofit,
            "n_pitchers": len(pids), "n_boot": n_boot, "metric": metric}


def run_experiment(season=SEASON, train_cutoff=TRAIN_CUTOFF, min_starts=MIN_STARTS,
                    n_boot=N_BOOTSTRAP, seed=BOOTSTRAP_SEED):
    """End-to-end: fetch the real population and real game logs, build both
    shrinkage variants on the identical real train data, score both against
    the identical real held-out outcomes, and return a single evidence dict
    -- everything this module's report and engineering/evidence/ JSON are
    built from. Every number in the returned dict traces back to a real
    MLB Stats API response fetched during this call; nothing is simulated
    except the bootstrap resampling of WHICH real pitchers are pooled, which
    is a standard variance-estimation technique, not a data source.

    ACTUAL RESULT of the run that produced
    engineering/evidence/mlb_pitcher_outs_shrinkage_prior_experiment_2026-09-20.json
    (2026-09-20, season-to-date through that day): 240 real starters with
    >=5 starts in 2026 fetched; 187 had >=5 real starts before the
    2026-07-01 cutoff (train population) -- well above
    mlb_sources.MIN_PLAYERS_TO_FIT_SHRINKAGE (30), so the auto-fit actually
    ran rather than silently falling back to SHRINKAGE_PRIOR_GAMES (20).
    Auto-fitted n0 ranged 6.7-12.9 across the ten outs_12plus..outs_21plus
    thresholds (vs the hardcoded 6), fit from the pooled real (hit, n)
    pairs of those 187 pitchers per threshold. Scored against 16,050 real
    held-out start-observations (167 distinct pitchers with at least one
    real start after 2026-07-01, through 2026-09-20): pooled Brier score
    0.173251 (prior_games=6) vs 0.172248 (prior_games=None); pooled
    log-loss 0.527675 vs 0.522969 -- the auto-fit was better (lower) on
    BOTH metrics, and on EVERY ONE of the ten individual thresholds
    considered separately, not just in aggregate (see
    engineering/evidence/mlb_pitcher_outs_shrinkage_prior_experiment_2026-09-20.json
    for the full per-threshold breakdown). A pitcher-clustered bootstrap
    (5000 resamples) on the Brier-score gap gave a point estimate of
    0.001003 with 95% CI [0.000403, 0.001607] -- excludes zero, and 99.96%
    of resamples favored the auto-fit. HONEST CONCLUSION: auto-fit wins,
    consistently but by a small absolute margin (~0.6% relative
    improvement in Brier score) -- real, statistically distinguishable from
    noise on this real held-out population, but modest in size. This is a
    measurement, not an implemented change: mlb_sources.py's hardcoded
    prior_games=6 is untouched."""
    print(f"Fetching real {season} starter population (>= {min_starts} starts)...")
    ids = fetch_starter_population(season=season, min_starts=min_starts)
    print(f"  {len(ids)} real pitchers")

    print(f"Fetching real per-pitcher game logs, train window (asof<= {train_cutoff})...")
    train_raw = build_raw_table(ids, asof=train_cutoff, min_starts=1)
    print(f"  {len(train_raw)} pitchers with >=1 real start on/before {train_cutoff}")

    print("Fetching real per-pitcher game logs, full season to date (asof=None)...")
    full_raw = build_raw_table(ids, asof=None, min_starts=1)
    print(f"  {len(full_raw)} pitchers with >=1 real start all season")

    train_min_starts_table = {pid: tbl for pid, tbl in train_raw.items()
                               if tbl["starts"] >= min_starts}
    print(f"  {len(train_min_starts_table)} pitchers with >={min_starts} real starts "
          f"before the cutoff (train population)")

    fit_gate_cleared = len(train_min_starts_table) >= msrc.MIN_PLAYERS_TO_FIT_SHRINKAGE
    print(f"  MIN_PLAYERS_TO_FIT_SHRINKAGE ({msrc.MIN_PLAYERS_TO_FIT_SHRINKAGE}) "
          f"{'cleared' if fit_gate_cleared else 'NOT cleared -- auto-fit would fall back'}")

    outcomes = held_out_outcomes(train_raw, full_raw)
    shrunk_hardcoded, shrunk_autofit = fit_both_priors(train_min_starts_table)

    fitted_n0_by_threshold = {}
    fell_back = []
    for key in THRESHOLDS:
        for tbl in shrunk_autofit.values():
            if key in tbl["rates"]:
                n0 = tbl["rates"][key]["n0"]
                fitted_n0_by_threshold[key] = n0
                if n0 == msrc.SHRINKAGE_PRIOR_GAMES:
                    fell_back.append(key)
                break

    per_pitcher, per_threshold = per_pitcher_scores(
        train_min_starts_table, shrunk_hardcoded, shrunk_autofit, outcomes)
    overall = pooled_summary(per_pitcher)
    per_threshold_summary = {key: pooled_summary({key: v}) for key, v in per_threshold.items()
                              if v["n"] > 0}
    brier_bootstrap = bootstrap_ci(per_pitcher, n_boot=n_boot, seed=seed, metric="brier")
    logloss_bootstrap = bootstrap_ci(per_pitcher, n_boot=n_boot, seed=seed, metric="logloss")

    return {
        "season": season,
        "train_cutoff": train_cutoff,
        "min_starts": min_starts,
        "n_population": len(ids),
        "n_train_population": len(train_min_starts_table),
        "min_players_to_fit_shrinkage": msrc.MIN_PLAYERS_TO_FIT_SHRINKAGE,
        "fit_gate_cleared": fit_gate_cleared,
        "fitted_n0_by_threshold": fitted_n0_by_threshold,
        "thresholds_that_fell_back_to_flat_prior": fell_back,
        "shrinkage_prior_games_fallback_constant": msrc.SHRINKAGE_PRIOR_GAMES,
        "n_held_out_pitchers": overall["n_groups"] if overall else 0,
        "n_held_out_start_observations": overall["n"] if overall else 0,
        "overall": overall,
        "per_threshold": per_threshold_summary,
        "brier_bootstrap": brier_bootstrap,
        "logloss_bootstrap": logloss_bootstrap,
    }


def print_report(evidence):
    o = evidence["overall"]
    print("\n" + "=" * 78)
    print("PITCHER OUTS SHRINKAGE PRIOR EXPERIMENT -- real held-out result")
    print("=" * 78)
    print(f"season={evidence['season']}  train_cutoff={evidence['train_cutoff']}  "
          f"min_starts={evidence['min_starts']}")
    print(f"real population: {evidence['n_population']}  "
          f"train population (>={evidence['min_starts']} starts before cutoff): "
          f"{evidence['n_train_population']}  "
          f"(MIN_PLAYERS_TO_FIT_SHRINKAGE={evidence['min_players_to_fit_shrinkage']}, "
          f"gate {'cleared' if evidence['fit_gate_cleared'] else 'NOT cleared'})")
    if o:
        print(f"real held-out: {o['n_groups']} pitchers, {o['n']} start-observations")
        print(f"  Brier   hardcoded(6)={o['brier_hardcoded']:.6f}   "
              f"autofit(None)={o['brier_autofit']:.6f}")
        print(f"  LogLoss hardcoded(6)={o['logloss_hardcoded']:.6f}   "
              f"autofit(None)={o['logloss_autofit']:.6f}")
    bb = evidence["brier_bootstrap"]
    if bb:
        print(f"  Bootstrap Brier gap (hardcoded-autofit): point={bb['point_estimate']:.6f}  "
              f"95% CI=[{bb['ci_lo']:.6f}, {bb['ci_hi']:.6f}]  "
              f"fraction favoring autofit={bb['fraction_favoring_autofit']:.4f}")
    print("\nfitted n0 by threshold (auto-fit) vs hardcoded 6:")
    for key, n0 in evidence["fitted_n0_by_threshold"].items():
        print(f"  {key:12s} n0={n0:6.1f}")
    if evidence["thresholds_that_fell_back_to_flat_prior"]:
        print("  fell back to the flat SHRINKAGE_PRIOR_GAMES fallback for:",
              evidence["thresholds_that_fell_back_to_flat_prior"])
    print("=" * 78)


if __name__ == "__main__":
    evidence = run_experiment()
    print_report(evidence)
    out_path = (sys.argv[1] if len(sys.argv) > 1 else
                "engineering/evidence/mlb_pitcher_outs_shrinkage_prior_experiment_2026-09-20.json")
    with open(out_path, "w") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)
    print(f"\nwrote {out_path}")
