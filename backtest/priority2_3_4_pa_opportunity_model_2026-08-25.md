# Priority 2/3/4 -- PA/opportunity distribution model, challenger probability, and the equal-volume test

Script: `backtest/pa_opportunity_model.py` (19 tests), market=`hits`, run
against real canonical history with a strict train/holdout split (fit on
2024+2025, evaluate on 2026 only -- the tables never saw holdout data).
Reproducible:

```
/tmp/mlbvenv/bin/python3 backtest/pa_opportunity_model.py backtest/rows_canonical.jsonl hits
```

## Priority 2: the PA distribution model itself

Simplest defensible approach per the standing instruction ("prefer simple
and reproducible first"): an empirical conditional distribution,
`P(actual_pa = k | batting_order)`, fit on 90,468 deduplicated player-games
from 2024-2025 (`actual_pa` is identical across every market row for the
same player-game -- verified zero mismatches across 115,521 real
player-games before deduplicating; fitting per-row instead of per-player-
game would silently over-weight players who appear in more markets).

**Point-in-time safety**: every input traces back to `signals.lineup_slot`,
which Priority 1 already established is invertible to real batting order
and pregame-knowable by construction (it is a value `generate_picks.py`'s
own scoring pass computes before first pitch, under the same
`verify_no_lookahead()` guarantee the whole backtest engine enforces).
Nothing here uses `actual_pa`, `outcome`, or any postgame field as a model
INPUT -- only as the target being predicted (PA) or the ground truth being
graded against (outcome).

**Out-of-sample calibration** (2024-2025-fitted distribution vs 2026
holdout actual): tracks closely at every order, no material drift --

| order | fitted mean PA (train) | actual mean PA (2026 holdout) |
|---|---|---|
| 1 | 4.377 | 4.330 |
| 5 | 3.960 | 3.895 |
| 9 | 3.338 | 3.320 |

The model generalizes to a year it never saw.

## Priority 3: challenger probability

`P(prop hits) = sum_k P(PA=k|order) * P(prop hits|PA=k)`, both terms fit
on 2024-2025, applied to every 2026 `hits`-market row with a known order
(25,055 of 25,056 holdout rows had one).

## Priority 4a: within-bucket discrimination (real, but not yet the deployment-relevant test)

Splitting 2026 holdout rows by the CHALLENGER probability's own median,
*within* each CURRENT-probability bucket, produces real, consistent
separation -- e.g. bucket 0.60-0.65 (n=7,019): challenger-low-half hits
61.5%, challenger-high-half hits 66.0% (+4.4pp); bucket 0.40-0.45
(n=3,540): 52.4% vs 59.8% (+7.3pp). Every populated bucket from 0.40 to
0.70 shows the same direction. This is genuine evidence the challenger's
ranking correlates with real outcomes the model never trained on.

## Priority 4b: the equal-volume test -- the one that actually matters, and it tempers the result above

Per the standing instruction ("Do not claim success if the challenger only
selects fewer picks" / equal-volume refill), the real promotion-relevant
question holds total selected volume FIXED at what the CURRENT policy
already selects (`predicted_prob >= 0.60`, `generate_picks.py`'s own
`MIN_LINE_PROB`), then asks what the CHALLENGER's own top-N (same N) would
have selected instead. Result, on the 2026 holdout, `hits` market:

| | n | hit rate |
|---|---|---|
| Current selection | 8,599 | 64.17% |
| Challenger selection (same N) | 8,599 | 64.39% |
| Overlap (both select) | 4,484 | 66.12% |
| Removed by challenger (current-only) | 4,115 | 62.04% |
| Added by challenger (challenger-only) | 4,115 | 62.50% |

**Net gain: +0.22 percentage points at matched volume** -- and critically,
**added-pick hit rate (62.50%) is essentially indistinguishable from
removed-pick hit rate (62.04%)**, a 0.46pp gap on ~4,100 picks each, well
within noise. Per Priority 12's own promotion bar ("added picks outperform
removed picks convincingly"), **this is not met.**

## Why the equal-volume result is so much smaller than the within-bucket result -- a real mechanistic answer, not a shrug

`generate_picks.py:1379` already computes `lineup_context =
scale(10 - order, 1, 9)` and feeds it directly into `score_batter`'s
CONTEXT category component (line 1386/1390), which flows into `score` and
then `predicted_prob` via the existing 35/25/15/15/10 weighted formula.
**Batting order is not new information to the current model -- it is
already partially priced in.** The within-bucket discrimination test
(Priority 4a) shows a real residual (order predicts outcome even after
conditioning on the bucket the CURRENT policy already assigned), but a
challenger built from order ALONE mostly rediscovers information the
current score already has, so swapping the actual selected population at
fixed volume barely moves the needle. The within-bucket test and the
equal-volume test are not contradictory -- they're answering different
questions ("does order add residual signal within a probability slice"
vs. "does a pure-order challenger change WHO gets selected in a way that
wins"), and the honest answer is: yes to the first, not yet to the second.

## Verdict

**This does not earn shadow testing yet.** Per the standing discipline,
this is reported as a real, well-characterized, currently-marginal result,
not spun as a win. The mechanistic finding (order info is already
partially captured via CONTEXT) points to the actual next step: a
challenger that captures the RESIDUAL opportunity information beyond what
CONTEXT's linear order treatment already extracts -- not a from-scratch
order-only model. Candidates for that residual (not yet built): a richer
distribution conditioned on order + another pregame signal jointly (e.g.
`days_rest`, `bullpen_fatigue`, or `getaway_day`/`series_game`, all
present pregame per Priority 1's signal survey), or examining whether the
CONTEXT category's linear treatment of order under- or over-weights the
tails (order 1 vs 9) relative to what the empirical PA relationship
actually implies.

## What this does NOT do

Nothing here changes scoring, thresholds, or the board -- this stays
research per the standing instruction. Scope not yet covered: other
hitter markets beyond `hits` (total_bases/hits_runs_rbis/home_run etc. --
Priority 1 showed the order effect holds directionally on total_bases too,
but the full challenger+equal-volume pipeline above has only been run for
`hits`), fragility (Priority 5), and any richer multi-signal opportunity
model.
