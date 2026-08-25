# Model/context disagreement phase, Priority 1/2/3 -- component audit and the Weston archetype

Script: `backtest/disagreement_decomposition.py` (13 tests), run against
real canonical history. Reproducible:

```
/tmp/mlbvenv/bin/python3 backtest/disagreement_decomposition.py backtest/rows_canonical.jsonl
```

## Priority 1: component data audit

`cat_matchup`/`cat_recent_form`/`cat_environment`/`cat_baseline_skill`/
`cat_context` exist only on `hits` (n=80,101) and `hits_runs_rbis`
(n=35,411) -- confirmed by direct query, not assumed. Two real structural
facts found before building any metric, both checked against real data:

- **`cat_environment` is a CONSTANT 50** across every canonical row --
  the main backfill ran with `--no-weather`, so environment inputs never
  populated. Not a bug, but it means "environment disagreement" cannot be
  tested from this dataset. Excluded entirely.
- **`cat_context` is ALSO a constant 50 for `strikeouts`** (the only
  pitcher market with `cat_*` data) -- `score_pitcher`'s category
  framework does not populate a CONTEXT component the way `score_batter`
  does. This confines the whole disagreement analysis below to the two
  batter markets where `cat_*` genuinely varies.

Pairwise Pearson correlations (real, both markets):

| pair | hits | hits_runs_rbis |
|---|---|---|
| cat_context x score | **0.974** | **0.966** |
| cat_baseline_skill x cat_context | 0.208 | 0.418 |
| cat_recent_form x cat_baseline_skill | 0.359 | 0.360 |
| cat_matchup x everything else | <0.19 | <0.18 |

**`cat_context` is near-collinear with `score` (r≈0.97) -- NOT because
CONTEXT dominates the documented 35/25/15/15/10 weighting (it's only 10%
nominal weight), but because `lineup_context` (batting order, full 0-100
range) gives `cat_context` far higher VARIANCE than the other components,
so it dominates the weighted sum's variance despite its small nominal
weight.** This means "score vs cat_context disagreement" is not a
meaningful metric -- they are near-collinear by construction. `cat_matchup`
is the most genuinely independent component. `cat_baseline_skill` vs
`cat_context` is only moderately correlated (0.21-0.42) -- not redundant,
and structurally exactly the Weston archetype's shape (empirical strength
vs. situational weakness).

## Priority 2: the metric tested

`baseline_context_conflict = cat_baseline_skill - cat_context`. Three
tiers: `high_empirical_low_context` (conflict >= +20, the Weston-like
signature), `high_context_low_empirical` (conflict <= -20, the opposite),
`balanced` (in between).

## Priority 3: the Weston-like archetype -- measured, not assumed

**Pooled (uncontrolled)**: `high_empirical_low_context` has the LOWEST hit
rate of the three tiers in both markets (hits_runs_rbis: 62.2% vs 65.0%
balanced vs 71.0% opposite; hits: 53.5% vs 59.9% vs 65.2%).

**Same-probability-bucket controlled (the real test)**: the ordering
`high_empirical_low_context < balanced < high_context_low_empirical` holds
in EVERY populated bucket in BOTH markets, with large samples:

| market | bucket | Weston-like | balanced | opposite |
|---|---|---|---|---|
| hits_runs_rbis | 0.60-0.65 | 60.8% (n=1,324) | 63.4% (n=8,817) | 68.3% (n=1,788) |
| hits_runs_rbis | 0.65-0.70 | 64.0% (n=1,183) | 66.2% (n=8,738) | 70.9% (n=2,691) |
| hits | 0.55-0.60 | 50.5% (n=1,875) | 56.6% (n=6,834) | 62.0% (n=2,826) |
| hits | 0.60-0.65 | 56.5% (n=1,457) | 62.6% (n=20,033) | 66.0% (n=17,445) |

**Year-stable**: the ordering and roughly the same magnitude (hits ~10-11pp
gap, hits_runs_rbis ~9-10pp gap between the two extreme tiers) holds
identically in every one of 2024, 2025, and 2026.

**This is a substantially stronger, more consistent within-bucket signal
than anything found in the opportunity-shortfall thread** (which showed
4-7pp gaps that did not survive the equal-volume test). The Weston
hypothesis is measured, not assumed, and confirmed: candidates whose
empirical history outruns their situational context really do realize
worse than nominal probability implies -- and the reverse (context
outrunning empirical support) really does realize better.

## Next

This earns Priority 4/5: the equal-volume selection test -- does using
this conflict tier as a tiebreak/penalty actually improve real selection
at fixed volume? See `backtest/disagreement_priority4_5_equal_volume_2026-08-25.md`.
