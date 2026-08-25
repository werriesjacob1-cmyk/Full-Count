# Residual-opportunity phase, Priority 1+2 -- target definition and decomposition

Script: `backtest/residual_opportunity_decomposition.py` (17 tests), run
against all 115,524 deduplicated hitter player-games in canonical history.
Reproducible:

```
/tmp/mlbvenv/bin/python3 backtest/residual_opportunity_decomposition.py backtest/rows_canonical.jsonl
```

## Priority 1: target definition

Two targets defined, per the directive's own menu:
- **residual_pa (continuous)**: `actual_pa - E[actual_pa | batting_order]`,
  both computed on the population being described (descriptive, not an
  out-of-sample model -- that's Priority 4).
- **is_shortfall (binary)**: `residual_pa <= -1.0` -- the interpretable
  target used for every predictor breakdown below. Overall shortfall rate:
  10.1% of player-games (n=115,524).

`actual_pa` is used ONLY as the target, never as a feature. Every
candidate predictor tested is read from `signals`, a value
`generate_picks.py`'s own scoring pass computes pregame under the same
`verify_no_lookahead()` guarantee the whole backtest engine enforces --
see the script's own docstring for the full leakage argument.

## Priority 2: which predictors explain shortfall beyond order

Four candidates tested (chosen after checking their REAL meaning in
`generate_picks.py`, not assumed from the field name -- `platoon` and
`bullpen_fatigue` were explicitly ruled out this way: `platoon` is
matchup-quality, not role-instability; `bullpen_fatigue` describes the
OPPONENT's bullpen, not this player's own playing-time risk).

### Real bug found and fixed while building this: `getaway_day` encoding

`generate_picks.py:1891` stores the SCALED signal value: **-2 when it IS a
getaway day, 0 otherwise** -- not a 0/1 flag. An initial version of this
script checked `v >= 0.5`, which silently matched **zero** real rows
against an actual ~34% getaway-day rate in the raw signal. Caught before
publishing any conclusion, fixed to check `v < 0`, and a regression test
(`test_getaway_day_groups`) locks the correct encoding in. No conclusion in
this document is based on the buggy version.

### Two real, robust residual predictors found

**`days_rest`** (days since this player's last game) -- monotonic,
substantial, and holds under every control:
- Pooled: 0 days rest 8.56% shortfall (n=100,107) -> 2-3 days rest 11.52%
  (n=4,740) -> 4+ days rest 13.73% (n=5,390).
- **Same-order controlled**: holds in every one of the 9 batting-order
  slots (e.g. order 9: 13.67% -> 18.87%; order 1: 7.64% -> 12.85%) -- not
  an order-correlation artifact.
- **Same-probability-bucket controlled**: holds across nearly every
  nominal probability bucket 0.00-0.75, largest bucket (0.30-0.35,
  n=89,713 vs 4,844) showing 8.22% vs 13.29%.
- **Year-stable**: 2024 +4.6pp, 2025 +5.1pp, 2026 +8.6pp (0_days_rest vs
  4plus_days_rest) -- consistent direction, if anything strengthening.

**`getaway_day`** (last game of a home/road stand before travel) --
equally robust once the encoding bug above was fixed:
- Pooled: getaway day 12.52% shortfall (n=39,095) vs non-getaway 8.86%
  (n=76,425).
- **Same-order controlled**: holds in every order slot (e.g. order 4:
  19.36% vs 13.21%; order 9: 17.34% vs 13.61%).
- **Year-stable**: 2024 +1.4pp, 2025 +3.7pp, 2026 +6.8pp -- consistent
  direction, growing.

### A third predictor, real but in the OPPOSITE (reliability) direction

**`consecutive_games`** (only fires per `generate_picks.py:1808` when
>= 10 straight games played -- sparse, 4.6% of rows) -- players on a long
consecutive-games streak have LOWER shortfall risk (6.93% vs 10.25%
pooled), holding in every order slot. Interpreted correctly: this is a
**survivorship/role-stability signal**, not a fatigue-risk signal -- a
player who has already played 10+ straight games is, by construction,
someone the team currently treats as a true everyday player, which is
evidence of higher opportunity reliability, not lower.

### Small/inconclusive: `series_game`

Pooled effect is small (~1pp, series_game_1 10.42% vs series_game_2/3+
~9.47%) -- not pursued further as a standalone signal.

## What this earns

Two real, independent, well-populated, year-stable residual predictors
exist beyond batting order: `days_rest` and `getaway_day`. Per the
directive's own gate ("only build a richer model if Priority 2 finds real
residual predictors"), this earns Priority 3/4: check whether current
CONTEXT mispricing is systematic at the tails, then build and
holdout-evaluate a richer challenger incorporating these two signals
jointly with order. See `backtest/priority3_4_residual_challenger_2026-08-25.md`
for that result.
