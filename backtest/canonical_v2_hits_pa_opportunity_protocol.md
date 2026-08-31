# Canonical V2 Hits PA/Opportunity Experiment — LOCKED BEFORE RESULT

**Protocol version:** canonical-v2-hits-pa-opportunity-v1  
**Locked:** before the canonical-v2 date-quarantined research view produced any accuracy result.

## Scientific regime

This experiment consumes only a research view whose independent
`canonical_v2_research_certify.py` verdict is literally
`CANONICAL CERTIFIED`. The immutable raw Run #6 parent may remain
`CERTIFICATION BLOCKED`; the research-view manifest must prove an exact
byte-preserving parent subset and whole-date quarantine from pre-outcome source
integrity evidence.

This remains **canonical historical model data**, not reconstructed historical
sportsbook eligibility. It cannot by itself prove live production pick volume,
prices, publication timing, or prospective performance.

## Primary market

**Hits only.**

No other market can rescue a failed Hits result.

## Champion

The existing row-level `predicted_prob`.

Historical safe-pool proxy: `predicted_prob >= 0.60`, the same model-probability
floor already used by the existing opportunity experiments. This is explicitly
not claimed to equal real historical production eligibility.

## Primary challenger

The existing, pre-canonical-v2 **residual opportunity challenger** in
`backtest/residual_challenger_model.py`, unchanged:

- empirical PA distribution conditioned on batting order + days rest + getaway day;
- `MIN_CELL_N = 200`;
- sparse cells fall back to the pre-existing order-only PA distribution;
- P(hit) is propagated through the empirical PA distribution;
- no outcome or postgame field is a model input.

If the challenger cannot score a candidate because batting order is unavailable,
its ranking score falls back to the champion's own `predicted_prob`. This is a
neutral operational fallback: missing opportunity evidence does not remove a
champion candidate or reduce usable volume.

## Secondary challenger

The existing order-only `backtest/pa_opportunity_model.py` challenger is
reported for context only. It **cannot rescue the primary challenger** if the
primary fails.

## Train / evaluation discipline

Two walk-forward evaluations:

1. Train on 2024 only → evaluate 2025.
2. Train on 2024 + 2025 → evaluate 2026.

2024 has no earlier canonical season in this dataset, so no in-sample 2024
"stability" result is fabricated.

## Equal operational volume

The decisive comparison is **date-matched**, not merely aggregate-N matched.

For every evaluation date:

1. champion selects every Hits row with `predicted_prob >= 0.60`;
2. let that count be N(date);
3. challenger ranks the same full candidate universe for that date;
4. challenger selects exactly N(date) candidates.

Therefore the challenger cannot improve by shifting volume from hard slates to
easy slates. The implementation must fail if per-date selected counts differ.

Ties in challenger score defer first to higher champion probability, then to a
stable semantic candidate key. This minimizes artificial churn from tied
empirical cells.

## Required reporting

For 2025 and 2026, and pooled where meaningful:

- exact candidate and selected counts;
- current and challenger realized hit rate;
- hit-rate delta;
- overlap;
- added and removed candidate counts;
- added and removed realized hit rates;
- two-proportion z statistic and two-sided p-value for added vs removed;
- 95% Wilson intervals;
- deterministic date-cluster bootstrap 95% interval for challenger-current
  hit-rate delta;
- number of dates with positive/equal/negative churn contribution;
- fallback-to-current probability count;
- season-phase added/removed breakdown;
- quarantine/eligibility identity from the certified research view.

Market mix is trivially 100% Hits and must be stated.

## Promotion interpretation

Because earlier exploratory work has already inspected these historical years,
even a pass here **does not authorize production promotion**. The strongest
historical verdict is `EARNS_PROSPECTIVE_SHADOW`.

The primary challenger earns that verdict only if ALL are true:

1. 2026 date-matched equal-volume hit-rate delta is positive.
2. 2026 added-pick hit rate exceeds removed-pick hit rate with z >= 1.96.
3. The 2026 date-cluster bootstrap 95% lower bound for hit-rate delta is > 0.
4. The 2025 walk-forward hit-rate delta is non-negative.
5. No 2026 season phase with >=200 added AND >=200 removed candidates reverses
   the direction (added hit rate < removed hit rate).
6. Exact per-date N equality is proven for every evaluated slate.
7. Certified research-view identity and locked input/code provenance verify.

If any criterion fails, verdict = `CLOSED` for this PA/opportunity selector
thread. Do not retune thresholds/cells after seeing the result. Move to the
already-locked disagreement experiment and then, if selector gains remain weak,
world-model research.

## Forbidden post-result changes

A failed result may not be rescued by:

- changing the 0.60 safe-pool floor;
- changing MIN_CELL_N;
- changing the joint context fields;
- changing train/holdout years;
- switching the secondary order-only model into the primary slot;
- replacing date-matched N with aggregate N;
- excluding additional dates based on accuracy outcomes;
- lowering the z/bootstrap/stability bars.

Any genuine methodological defect must be documented as a new dated protocol,
not silently edited into this one.
