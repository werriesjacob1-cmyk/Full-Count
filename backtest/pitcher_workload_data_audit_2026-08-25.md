# Pitcher workload data-availability audit (Priority 11 prep -- audit only, no model)

Checked directly against real partial canonical data (the in-progress
rebuild, `backtest/rows_canonical_rebuild.jsonl`, 574 pitcher-market rows
as of this check) -- not assumed from the schema docs alone.

## The opportunity/target variable exists and varies

`actual_ip` (innings pitched) is present on **100% of `strikeouts`/
`pitcher_outs` rows**, ranging 0.1 to 9.0 in the real sample checked. This
is the direct pitcher-market analogue of `actual_pa` -- the same kind of
"how much opportunity did this player actually get" target that powered
the (ultimately closed) hitter opportunity research. **The mechanism
question ("do low-actual-IP starts explain a large share of same-
probability Ks/outs misses?") is directly answerable once canonical
history is rebuilt** -- nothing new needs to be instrumented for the
target side.

## `cat_context` is constant for pitchers -- confirmed again, not new

Consistent with `backtest/disagreement_priority1_2_3_2026-08-25.md`'s
finding: `cat_context` is exactly 50 for every pitcher-market row checked
(572/574; 2 rows had it `None` entirely). `score_pitcher` does not
populate a CONTEXT category the way `score_batter` does. Restated here
because it also rules out reusing the disagreement `baseline_context_conflict`
metric for pitchers -- any pitcher-side disagreement work needs a
different pair of components, not attempted here (out of scope for this
audit).

## Pregame WORKLOAD-RISK predictors are much sparser than the hitter side had

The hitter opportunity thread had two real, well-populated (>80%),
independent, year-stable predictors (`days_rest`, `getaway_day`). The
pitcher `signals` dict, checked directly against the same real sample,
has no direct analogue:

| signal | presence | plausibly opportunity/workload-related? |
|---|---|---|
| `opp_team_k_pct` | 99.7% | No -- opponent skill, not this pitcher's own workload risk |
| `same_hand_ratio` | 99.7% | No -- matchup quality |
| `env_neutral` | 99.7% | No -- environment placeholder (see `cat_environment`, also constant) |
| `l14_k_pct` | 91.3% | No -- recent skill level, not workload |
| `season_k_pct` | 86.1% | No -- skill level |
| `csw_pct` | 60.1% | No -- skill/quality-of-stuff metric |
| `tto_penalty` | 19.7% | **Maybe** -- "times-through-order penalty" plausibly correlates with expected hook timing/workload risk, but only 1-in-5 rows carry it |
| `outs_rate` | 0.3% | Too sparse to use |
| `avg_outs_per_start` | 0.3% | Too sparse to use |

**No `days_rest`-equivalent exists for pitchers in the current signal set**
-- the hitter-side `days_rest`/`consecutive_games`/`getaway_day` signals
are computed inside `score_batter`'s own code path
(`generate_picks.py:1806-1899`, checked this session for the residual-
opportunity work) and are not wired into `score_pitcher` at all. This is
a real, structural gap in the SIGNAL, not the outcome data -- confirmed by
absence, not inferred.

## What this means for Priority 8/9 (pitcher workload decomposition/model)

The target/mechanism question (Priority 8 -- "does actual-IP shortfall
explain same-probability misses") can be answered directly once canonical
history returns, using exactly the same methodology already built and
tested for hitters (`opportunity_decomposition.py`'s pattern: bucket
`predicted_prob`, compare realized hit rate by `actual_ip` tier). **Not
run yet -- would be a real conclusion, and this audit deliberately does
not compute one against incomplete history.**

The harder question (Priority 9 -- "which PREGAME signal predicts IP
shortfall") is currently under-resourced on the signal side. `tto_penalty`
is the only plausible lead, and it's sparse (19.7%). If Priority 8 shows
IP shortfall is a real, large same-probability miss driver (mirroring the
hitter finding), the natural next step is NOT to immediately build a
model on `tto_penalty` alone -- it's to check whether `days_rest`-style
signals could be wired into `score_pitcher` the same way they already
exist for `score_batter` (a real, scoped code change, not attempted this
pass, and only worth doing if Priority 8 first proves the mechanism is
large enough to matter).

## What was NOT done

No mechanism conclusion computed (Priority 8 proper) -- this file is the
audit that precedes it, run against real but incomplete data (574 rows,
20 dates, no 2025/2026 holdout at all) specifically so that step doesn't
have to be repeated once canonical history exists; only the DATA
AVAILABILITY facts above are asserted, and those don't change as more
dates are added (they're about which fields exist, not their
distribution).
