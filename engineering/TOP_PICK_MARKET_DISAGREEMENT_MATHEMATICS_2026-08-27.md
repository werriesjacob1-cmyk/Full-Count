# Top Pick / MARKET DISAGREES Mathematics — Phase 9

**Status: research analysis only. No production code changed by this document.**

## The question

42/42 immutable public Top Picks inspected carried `MARKET DISAGREES`
(SUSPECT). Is that structurally forced by the current formulas, or bad
luck? Derived and numerically verified directly against the real
production functions (`prop_probability.value_verdict`,
`prop_probability.market_agreement`, `recommendation.py`'s Top Pick gate)
— not re-implemented or approximated.

## The relevant production constants (read directly from source)

- `TOP_PICK_MIN_PROB = 0.60` (recommendation.py) — a Top Pick's model
  probability must be ≥ 60%.
- `MIN_ROI = 0.05` (prop_probability.py) — a bet must return ≥5% at the
  model's own probability, reused by `TOP_PICK_MIN_ROI`.
- `ASSUMED_PROP_HOLD = 0.08` (prop_probability.py) — the assumed two-way
  hold used to de-vig a one-sided posted price.
- SUSPECT band, from `market_agreement()`: a prop is **not** SUSPECT
  (i.e. AGREE or LEAN) iff both `|p − fair| ≤ 0.07` and
  `0.65 ≤ p/fair ≤ 1.5`, where `fair = implied(american) / 1.08` and `p`
  is the model's probability. (AGREE's tighter band, `≤0.03` /
  `[0.8,1.25]`, is a strict subset of LEAN's, so "not SUSPECT" reduces
  exactly to LEAN's condition.)
- `implied_probability(american)` is proven algebraically identical to
  `1/decimal_odds(american)` (`breakeven`) for both odds signs — verified
  by direct substitution, not assumed.

## The derivation

Let `x` = the price's implied probability (`x = 1/d`), and `p` = the
model's probability (the Top Pick candidate).

**ROI floor** (`expected_roi(p, american) ≥ MIN_ROI`):
`p·d − 1 ≥ 0.05  ⟺  p ≥ 1.05·x  ⟺  x ≤ p / 1.05`

**Not-SUSPECT** (`fair = x/1.08`):
- absolute-gap arm: `|p − x/1.08| ≤ 0.07  ⟺  x ∈ [1.08(p−0.07), 1.08(p+0.07)]`
- ratio arm: `0.65 ≤ p/(x/1.08) ≤ 1.5  ⟺  x ∈ [0.72p, 1.6615p]`

For any `p ≥ 0.21` (true for every Top-Pick-relevant probability),
`1.08(p−0.07) > 0.72p`, so the **absolute-gap arm is the binding lower
bound** on `x` for not-SUSPECT: `x ≥ 1.08(p − 0.07)`.

For a price to satisfy **both** requirements simultaneously, the
not-SUSPECT lower bound must not exceed the ROI-floor upper bound:

```
1.08(p − 0.07) ≤ p / 1.05
1.08p − 0.0756 ≤ 0.952381p
0.127619p ≤ 0.0756
p ≤ 0.075600 / 0.127619 = 0.59237...
```

**So for any model probability `p > 0.5924` (59.24%), there is
mathematically no price at which a bet can simultaneously clear the 5%
ROI floor and avoid a SUSPECT verdict.**

`TOP_PICK_MIN_PROB = 0.60 > 0.5924`. Every Top Pick, by definition, has
`p ≥ 0.60`. Therefore **SUSPECT is mathematically forced for every Top
Pick that also clears its own required ROI floor** — which every actual
Top Pick must, since `require_robust=True` `value_verdict` calls are the
other half of the Top Pick gate. The two requirements the current
formulas impose on a Top Pick (probability ≥ 60%, ROI ≥ 5%) are not
jointly satisfiable with "the market agrees" under the current SUSPECT
band and 8% hold assumption. This is not the model being aggressive on
any one favorite; it is baked into the geometry every single time.

## Numerical confirmation (not just algebra)

Brute-force grid search over every integer American price from −2000 to
+2000, calling the real `prop_probability.expected_roi` and
`prop_probability.market_agreement` directly:

| model p | # prices clearing BOTH ROI≥5% AND not-SUSPECT |
|---|---|
| 0.55 | 3 (best: −110, LEAN) |
| 0.58 | 1 (only: −123, LEAN) |
| 0.59 | **0** |
| 0.5924 (derived crossover) | 0 |
| 0.60 (`TOP_PICK_MIN_PROB`) | **0** |
| 0.62, 0.65, 0.70, 0.80, 0.90 | **0** |

The tiny discrepancy between the continuous crossover (0.5924) and the
integer-grid crossover (between 0.58 and 0.59) is exactly what quantized
real American odds would produce — it does not change the conclusion,
since 0.60 sits comfortably above both.

## Why the vig is the actual culprit, not the model

The 8% hold assumption pushes `fair` well below the model's read even
when the model and the TRUE market number agree closely, because
`fair = implied/1.08` while `expected_roi` is computed against the
*posted* (vig-included) price directly, not the de-vigged one. A model
that is essentially correct still needs `p` meaningfully above the
posted implied probability to clear ROI — and that same gap is what
`market_agreement` reads as disagreement. The ROI floor and the SUSPECT
band are measuring almost the same gap from two different reference
points (posted implied vs. de-vigged fair), separated by exactly the
hold. At `MIN_ROI=0.05` and `hold=0.08`, that separation is large enough
relative to the SUSPECT band's own width (`0.07` absolute / `1.5` ratio)
that the two requirements structurally collide once `p` clears roughly
0.59.

## Classification

This is **primarily a market-agreement warning specification problem**,
not evidence the underlying probability model is wrong. The SUSPECT
label was built to catch a real failure mode (the CJ Abrams longshot
overstatement — a 2x disagreement on a *low*-probability read). Its
band, tuned for that regime, was never checked against the *high*-
probability regime Top Pick actually operates in, where the ROI floor
and the hold assumption combine to make "the market agrees" essentially
unreachable by construction. Whether the Top Pick policy itself (the
`p ≥ 0.60` floor, or requiring ROI at all for something already this
probable) is *separately* miscalibrated is a distinct, unanswered
question — this analysis does not attempt to resolve it, since that would
require realized accuracy evidence (the canonical backfill, still
running) to know whether Top Pick's ~65% average predicted probability
is itself trustworthy.

## Explicitly NOT done here

Per the governing mission's Phase 9 instructions: no threshold changed,
no SUSPECT hard-block added, no warning removed, no new number invented.
This is geometry, not a fix. Any production change from this analysis
requires separate research evidence and explicit authorization.
