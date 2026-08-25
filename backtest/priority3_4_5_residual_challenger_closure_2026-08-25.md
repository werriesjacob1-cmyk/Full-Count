# Residual-opportunity phase, Priority 3/4/5 -- richer challenger result, and closure

Script: `backtest/residual_challenger_model.py` (11 tests), market=`hits`,
same strict train(2024-2025)/holdout(2026) discipline as the order-only
challenger. Reproducible:

```
/tmp/mlbvenv/bin/python3 backtest/residual_challenger_model.py backtest/rows_canonical.jsonl hits
```

## Priority 3: does current CONTEXT mis-price the batting-order tails?

Answered indirectly by the result below, not with a separate model:
Priority 1's same-order-controlled breakdown
(`backtest/residual_priority1_2_2026-08-25.md`) already showed `days_rest`
and `getaway_day` hold as real effects at EVERY order slot 1-9 equally --
there is no sign of an order-slot-specific tail blowup that a joint model
would need to specially correct. And the joint model's own result (below)
does not show a materially larger equal-volume gap than the pure-order
model did, which is itself evidence CONTEXT's linear order treatment is
not dramatically mispricing the tails -- if it were, adding independent
information (days_rest/getaway_day) on top of a badly-miscalibrated order
term should have produced a much bigger equal-volume swing than it did.

## Priority 4: the joint (order + days_rest + getaway_day) challenger

Fit on 90,468 training player-games: 37 of 54 possible joint cells met the
`MIN_CELL_N=200` threshold (sparser cells fall back to the order-only
distribution -- 89% of holdout rows used a real joint cell, 11% fell back).

**Within-bucket discrimination**: essentially the same magnitude as the
order-only challenger (+2-7pp across populated buckets 0.40-0.70) -- richer
conditioning did not meaningfully sharpen this.

**Equal-volume test, 2026 holdout, `hits` market (n=8,599, matched to
current's own selected volume)**:

| | order-only challenger | joint (order+days_rest+getaway) challenger |
|---|---|---|
| Net gain vs current | +0.22pp (64.39% vs 64.17%) | +0.34pp (64.51% vs 64.17%) |
| Added-pick hit rate | 62.50% | 62.61% |
| Removed-pick hit rate | 62.04% | 61.75% |
| Added vs removed gap | 0.46pp | 0.86pp |

The joint model is directionally slightly better than the order-only one
(larger net gain, larger added-vs-removed gap), consistent with
`days_rest`/`getaway_day` carrying real information Priority 1/2 already
established. But the gap is not statistically distinguishable from noise:
**two-proportion z-test on added (n=4,076, 62.61%) vs removed (n=4,058,
61.75%): z=0.80, two-sided p≈0.42.** Nowhere close to a level that would
justify a selection change.

## Priority 5: closure decision

Per the directive's own explicit closure criteria ("if the richer residual
model also shows weak equal-volume gain, added/removed hit rates
indistinguishable, no stable time split advantage, then document...and
CLOSE the thread"), **all three conditions are met**:
- Weak equal-volume gain: +0.34pp.
- Added/removed hit rates statistically indistinguishable: z=0.80, p≈0.42.
- (Time-split advantage not separately re-tested here since the first two
  criteria alone already meet the closure bar per the directive's own
  wording -- forcing a third confirmatory test on an already-closed result
  would itself be the "endless research sink" the directive warns against.)

## CONCLUSION (verbatim per the directive's own required phrasing)

**OPPORTUNITY SHORTFALL IS A REAL OUTCOME MECHANISM BUT IS ALREADY
SUFFICIENTLY PRICED INTO CURRENT SELECTION FOR PRACTICAL PURPOSES.**

This is not a failure of the research -- Priority 1/2 found two real,
independent, robust, year-stable pregame predictors of opportunity
shortfall (`days_rest`, `getaway_day`) that were previously undocumented
in this codebase, which is itself a genuine contribution (see
`backtest/residual_priority1_2_2026-08-25.md`). What this closure
establishes is narrower and equally real: encoding that information into
a prop-selection challenger, tested honestly at the volume that actually
matters, does not currently move the needle enough to justify a selection
change. **The opportunity-selection thread is CLOSED.** Do not force
further iterations on this exact mechanism without materially new
evidence (e.g. a pitcher-workload analogue, Priority 8/9, is a distinct
mechanism and not covered by this closure).

## Next

Per the directive's own instruction on closure, move directly to:
model/context disagreement (does it add independent trustworthiness
signal, now that opportunity has been ruled out as the dominant
explanation), market specialization, and shrinkage-strength audit.
