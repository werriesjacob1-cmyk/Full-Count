# Priority 1 -- opportunity-shortfall decomposition

Full characterization of the Priority-6 finding ("opportunity shortfall is
the dominant source of within-probability-bucket variance"), run against
canonical history before building any model. Script:
`backtest/opportunity_decomposition.py` (21 tests), run for real against
`backtest/rows_canonical.jsonl` (999,735 of 1,027,462 rows are hitter
markets). Reproducible:

```
/tmp/mlbvenv/bin/python3 backtest/opportunity_decomposition.py backtest/rows_canonical.jsonl
```

## Key discovery this analysis depends on

`signals.lineup_slot` (present on ~89% of canonical rows) is
`scale(10 - order, 1, 9)` from `generate_picks.py:1379` -- a deterministic,
**invertible, PREGAME-KNOWABLE** encoding of a batter's real batting-order
slot (1-9). This is the first genuinely pregame opportunity proxy confirmed
in canonical history. Verified: reconstructed order buckets are almost
perfectly balanced (~12,830 rows each on the `hits` market alone, ~105-106K
each pooled across all hitter markets) and average `actual_pa` declines
monotonically from 4.38 (order 1) to 3.34 (order 9).

## Q1/Q2: is low PA universal across hitter markets, and where does hit rate collapse?

Universal, and smooth rather than a single cliff -- every hitter market
tested (hits, total_bases, hits_runs_rbis, home_run) rises steadily and
monotonically with `actual_pa`, roughly doubling from `pa=3` to `pa=5`:

| actual_pa | hits | total_bases | hits_runs_rbis | home_run |
|---|---|---|---|---|
| 1 | 21.8% (n=615) | 8.6% | 35.4% | 2.4% |
| 2 | 29.9% (n=5,726) | 11.6% | 37.2% | 3.6% |
| 3 | 44.8% (n=23,336) | 20.3% | 53.5% | 6.6% |
| 4 | 62.8% (n=61,430) | 35.2% | 68.7% | 11.3% |
| 5 | 79.0% (n=22,656) | 54.5% | 85.3% | 18.5% |
| 6+ | 87.1% (n=1,627) | 69.0% | 93.4% | 21.7% |

`pa<=2` is where every market is worst; there is no single sharp
"collapse" threshold, just a large, consistent increment per additional PA.

## The load-bearing result: batting order predicts realized hit rate EVEN AFTER controlling for the model's own nominal probability

This is the central finding of Priority 1. Within essentially every 0.05
probability bucket from 0.05 to 0.80, pooled across all 10 hitter markets,
`top_1_3` (batting order 1-3) beats `mid_4_6` beats `bottom_7_9`, with tens
of thousands of rows per cell in the busy buckets:

| bucket | top_1_3 | mid_4_6 | bottom_7_9 |
|---|---|---|---|
| 0.30-0.35 | 36.2% (n=27,502) | 34.8% (n=36,542) | 31.7% (n=37,671) |
| 0.45-0.50 | 49.9% (n=21,254) | 49.3% (n=14,483) | 47.6% (n=11,650) |
| 0.55-0.60 | 63.8% (n=6,617) | 60.1% (n=13,655) | 55.9% (n=20,864) |
| 0.60-0.65 | 66.9% (n=22,974) | 64.0% (n=22,471) | 60.4% (n=14,397) |
| 0.65-0.70 | 70.7% (n=17,838) | 68.5% (n=15,238) | 64.1% (n=8,825) |
| 0.70-0.75 | 74.0% (n=13,365) | 70.5% (n=6,685) | 67.8% (n=1,780) |

**This means `predicted_prob` does not fully absorb the information batting
order carries.** Two candidates the model calls "65%" are not equally
trustworthy -- one batting leadoff realizes meaningfully higher than one
batting 9th, at the exact same nominal probability.

**Robustness checks, both real, both hold the same direction**:
- Per-market (not just pooled): `hits`-only shows the identical ordering
  at every populated bucket (e.g. 0.60-0.65: top_1_3 66.5% n=20,365 vs
  bottom_7_9 58.5% n=6,157); `total_bases`-only shows the same (e.g.
  0.30-0.35: top_1_3 38.5% n=6,455 vs bottom_7_9 30.2% n=18,299).
- Year stability: the top_1_3-vs-bottom_7_9 gap is ~8 percentage points in
  **every one of 2024, 2025, and 2026** (2024: 36.9% vs 28.6%; 2025: 36.4%
  vs 28.4%; 2026: 35.0% vs 27.3%) -- not a one-season artifact.

## Explicitly UNAVAILABLE from canonical rows (not attempted, not guessed)

- Confirmed-vs-assumed lineup status (`lineup_assumed` is LIVE/registry-only,
  never present on a backtest row per `SCHEMA.md`).
- Home/away.
- Team-level run environment.

None of these exist in `backtest/rows_canonical.jsonl`'s schema. A future
model wanting them needs the prospective candidate-funnel track (Track B),
not this canonical file.

## What this means for the roadmap

The mechanism is now characterized, not just observed: batting order is a
real, pregame-knowable, statistically robust proxy for opportunity that
today's `predicted_prob` under-uses. This directly earns Priority 2 (build
a point-in-time PA/opportunity distribution model) -- the simplest
version of which can start from exactly this relationship (order ->
empirical actual_pa distribution) before reaching for anything more complex.
