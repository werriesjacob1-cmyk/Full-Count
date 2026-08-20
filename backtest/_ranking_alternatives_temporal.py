#!/usr/bin/env python3
"""_ranking_alternatives_temporal.py -- temporally-validated comparison of
a SMALL number of principled ranking alternatives, developed on 2024 only
and evaluated on 2025+2026 (never touched during development). No search,
no per-market fitting to its own history -- every formula's coefficients
are simple, motivated directly by _score_component_deep_dive.py's 2024-only
findings, not tuned against the eval set.

METHODS:
  A. current score          (35/25/15/15/10 cat_* blend, as shipped)
  B. raw predicted_prob     (pre-calibration model probability)
  D. prob + real-incremental components only: 0.70*(predicted_prob*100)
     + 0.20*cat_context + 0.10*cat_matchup -- context and matchup were the
     only two components with a real, stable, positive incremental
     correlation (controlling for predicted_prob) on the 2024 dev set;
     weights are simple round numbers reflecting that ranking (context >
     matchup), not a fit.
  E. score with weak/redundant components removed, proportionally
     renormalized: drop cat_recent_form (near-zero/negative incremental
     value on dev) and cat_baseline_skill (incremental value collapses
     once predicted_prob is controlled for -- redundant, not additive).
     Remaining weights (matchup .35, environment .15, context .10 = .60)
     renormalized to sum to 1: matchup .583, environment .25, context .167.
  G. market-tiered: SCORE for markets where the 2024 DEV set showed score
     winning or tying (hits, hits_runs_rbis, total_bases, runs, rbis,
     doubles), PROB for markets where the 2024 dev set showed prob winning
     (pitcher_outs, strikeouts, hard_hit_105, home_run, triples, singles,
     nrfi_combined) -- the assignment itself is decided ONLY on 2024, then
     applied unchanged to the 2025+2026 holdout, so it cannot be
     overfit to the eval data by construction.

Reliability-guarded prob (method C in the request) is NOT computable
here -- rows.jsonl carries no reliability grade. See
_sweep_best_of_category_reliability.py for the closest available
evidence on that question.

    /tmp/mlbvenv/bin/python3 backtest/_ranking_alternatives_temporal.py
"""
import json
import os
from collections import defaultdict

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")


def load():
    by_date = defaultdict(list)
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("outcome") is None or d.get("fair_test") is not True:
                continue
            by_date[d["date"]].append(d)
    return dict(sorted(by_date.items()))


def groups_for(by_date, years):
    groups = defaultdict(list)
    for date, rows in by_date.items():
        if date[:4] not in years:
            continue
        for r in rows:
            groups[(date, r["prop_type"])].append(r)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def method_A(e):
    return e["score"]


def method_B(e):
    return e["predicted_prob"]


def method_D(e):
    if e.get("cat_context") is None:
        return e["predicted_prob"] * 100  # non-batter/pitcher row: falls back to prob alone
    return 0.70 * (e["predicted_prob"] * 100) + 0.20 * e["cat_context"] + 0.10 * e["cat_matchup"]


def method_E(e):
    if e.get("cat_context") is None:
        return e["score"]  # non-batter/pitcher row: score IS already just its own thing here
    return 0.583 * e["cat_matchup"] + 0.25 * e["cat_environment"] + 0.167 * e["cat_context"]


# Assignment decided from a DIRECT 2024-only score-vs-prob check (never
# the pooled 2024-2026 numbers, which would leak eval-period information
# into the tiering decision). Real finding from that check: hits itself
# FLIPS -- 2024-only favors PROB (72.4% vs 70.2%), the opposite of what
# the full pooled sample suggested (score winning) -- underscoring why
# per-market winners must be decided out-of-sample, not from the whole
# history at once. total_bases/home_run/runs/rbis/doubles/triples had too
# few 2024-only multi-candidate groups to assign a dev-set winner with any
# confidence, so they default to SCORE (the current shipped behavior) --
# "insufficient dev evidence" means "no change," never a guess.
DEV_SCORE_MARKETS = {"hits_runs_rbis", "nrfi_combined", "total_bases", "home_run",
                     "runs", "rbis", "doubles", "triples"}
DEV_PROB_MARKETS = {"hits", "pitcher_outs", "strikeouts", "hard_hit_105", "singles"}


def method_G(e, market):
    if market in DEV_SCORE_MARKETS:
        return e["score"]
    return e["predicted_prob"]


# NOTE: a method H ("D everywhere except hits_runs_rbis, which stays on
# score") was drafted and then DELETED here, not shipped as a silent fix.
# Reason, stated rather than hidden: checking method D vs score on
# hits_runs_rbis using 2024-DEV-ONLY data first (the same discipline used
# for every other market-tier decision in this file) showed D WINNING on
# dev (81.0% vs 79.8%) -- the opposite of what the 2025+2026 eval set
# showed (D losing badly, 71.4% vs 77.8%). Excluding hits_runs_rbis from D
# would therefore have been a decision made by looking at the eval
# result and rationalizing it after the fact -- exactly the cherry-
# picking this investigation was told not to do. The real, more important
# finding is reported instead: hits_runs_rbis's true score-vs-prob-blend
# relationship is UNSTABLE across the one dev/eval boundary available,
# the same instability already found for hits itself (2024 dev: prob
# wins; 2025+2026 eval: prob loses badly). Two of the board's highest-
# volume markets both flip sign across this split -- see the report for
# why that argues for NEEDS MORE EVIDENCE rather than a market-specific
# carve-out.


def top_choice(entries, key_fn):
    return max(entries, key=lambda e: (key_fn(e), str(e.get("player_id") or "")))


def top_choice_market_aware(entries, market):
    return max(entries, key=lambda e: (method_G(e, market), str(e.get("player_id") or "")))


def evaluate(groups, label):
    methods = {"A_score": method_A, "B_prob": method_B, "D_prob_context_matchup": method_D,
              "E_score_minus_weak": method_E}
    results = {}
    for name, fn in methods.items():
        picks = [top_choice(v, fn) for v in groups.values()]
        hit = sum(e["outcome"] for e in picks) / len(picks)
        results[name] = (hit, len(picks))
    picks_G = [top_choice_market_aware(v, k[1]) for k, v in groups.items()]
    results["G_market_tiered"] = (sum(e["outcome"] for e in picks_G) / len(picks_G), len(picks_G))
    print(f"  -- {label} (n groups={len(groups)}) --")
    for name, (hit, n) in results.items():
        print(f"     {name:24s} hit_rate={hit:.4f}  n={n}")
    return results


def evaluate_by_market(groups, label, markets):
    print(f"\n=== {label}: by market ===")
    by_market = defaultdict(dict)
    for k, v in groups.items():
        by_market[k[1]][k] = v
    for market in markets:
        sub = by_market.get(market, {})
        if len(sub) < 20:
            continue
        print(f"  -- {market} (n={len(sub)})  [dev-tier: "
             f"{'SCORE' if market in DEV_SCORE_MARKETS else 'PROB'}] --")
        for name, fn in [("A_score", method_A), ("B_prob", method_B),
                        ("D_prob_context_matchup", method_D), ("E_score_minus_weak", method_E)]:
            picks = [top_choice(v, fn) for v in sub.values()]
            hit = sum(e["outcome"] for e in picks) / len(picks)
            print(f"     {name:24s} hit_rate={hit:.4f}")
        picks_G = [top_choice_market_aware(v, market) for v in sub.values()]
        print(f"     {'G_market_tiered':24s} hit_rate={sum(e['outcome'] for e in picks_G)/len(picks_G):.4f}")


def dev_vs_eval_instability_check(dev_groups, eval_groups):
    print("\n=== Dev-vs-eval stability check for the two highest-volume markets ===")
    print("    (does the market's own score-vs-prob-blend winner survive the dev/eval boundary?)")
    for market in ("hits", "hits_runs_rbis"):
        dev_sub = {k: v for k, v in dev_groups.items() if k[1] == market}
        eval_sub = {k: v for k, v in eval_groups.items() if k[1] == market}
        if not dev_sub or not eval_sub:
            continue
        print(f"  -- {market} --")
        for name, fn in [("A_score", method_A), ("B_prob", method_B), ("D_prob_context_matchup", method_D)]:
            dp = [top_choice(v, fn) for v in dev_sub.values()]
            ep = [top_choice(v, fn) for v in eval_sub.values()]
            dh = sum(e["outcome"] for e in dp) / len(dp)
            eh = sum(e["outcome"] for e in ep) / len(ep)
            print(f"     {name:24s} DEV(2024)={dh:.4f} (n={len(dp)})   "
                 f"EVAL(2025+26)={eh:.4f} (n={len(ep)})   delta={eh-dh:+.4f}")


def main():
    by_date = load()
    dev_groups = groups_for(by_date, {"2024"})
    eval_groups = groups_for(by_date, {"2025", "2026"})
    eval_2025 = groups_for(by_date, {"2025"})
    eval_2026 = groups_for(by_date, {"2026"})

    print(f"DEV (2024): {len(dev_groups)} groups. EVAL (2025+2026, never used to develop "
         f"the formulas above): {len(eval_groups)} groups.\n")

    print("=== DEV set (2024) -- for transparency only, NOT the acceptance evidence ===")
    evaluate(dev_groups, "2024 (dev)")

    print("\n=== EVAL set (2025+2026 holdout) -- the real out-of-sample test ===")
    evaluate(eval_groups, "2025+2026 (eval, holdout)")

    print("\n=== EVAL split by year ===")
    evaluate(eval_2025, "2025 only")
    evaluate(eval_2026, "2026 only")

    markets_of_interest = ["hits", "hits_runs_rbis", "total_bases", "singles", "home_run",
                           "runs", "rbis", "strikeouts", "pitcher_outs", "hard_hit_105", "doubles"]
    evaluate_by_market(eval_groups, "EVAL (2025+2026)", markets_of_interest)
    dev_vs_eval_instability_check(dev_groups, eval_groups)

    # agreement rates on eval set
    print("\n=== Selection agreement, EVAL set ===")
    picks_A = {k: top_choice(v, method_A) for k, v in eval_groups.items()}
    picks_B = {k: top_choice(v, method_B) for k, v in eval_groups.items()}
    picks_D = {k: top_choice(v, method_D) for k, v in eval_groups.items()}
    picks_E = {k: top_choice(v, method_E) for k, v in eval_groups.items()}
    def agree(a, b):
        n = len(a)
        m = sum(1 for k in a if a[k].get("player_id") == b[k].get("player_id")
               and a[k].get("needs") == b[k].get("needs"))
        return m / n
    print(f"  A vs B: {agree(picks_A, picks_B):.1%}")
    print(f"  A vs D: {agree(picks_A, picks_D):.1%}")
    print(f"  A vs E: {agree(picks_A, picks_E):.1%}")
    print(f"  B vs D: {agree(picks_B, picks_D):.1%}")


if __name__ == "__main__":
    main()
