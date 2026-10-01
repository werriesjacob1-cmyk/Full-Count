# MLB forward-chain accuracy study (2026 regular season)

**Exploratory, not pre-registered. Research only.** It changes no model, selector, threshold, workflow or public page.

- The rules were committed in `34b1506b4b` before the first run. `forward_chain.py` ran once and produced `report.json`.
- Data were read at main `58abe9f2e5`.

## Question
Does the model's probability add accuracy beyond the posted FanDuel price? Would recalibrating it help?

PR #217 could not answer this: its pre-registered 3-slate evaluation had too few rows (n=299).

## Method
- **Rows.** The construction from PR #217's fit sample, run over every regular-season date from 2026-08-04 to 09-27: 3,984 rows. There are 1,400 frozen-board rows and 2,738 final-board rows; 154 are on both and were counted once.
- **Weekly forward chaining.** Each week is predicted only by coefficients fitted on earlier weeks. That yields **3,141 out-of-sample rows across 207 games**, in 4 test weeks starting the week of 08-31.

| Arm | Definition |
|---|---|
| `p0` | The model's `hit_probability` |
| `pq` | The raw posted implied probability (no fit) |
| `p1` | Market-only recalibration |
| `p2` | Model + market blend |
| `pcal` | Model-only recalibration |

## Results

Paired log-loss differences, pooled out of sample. Negative means the first arm is better. Intervals are 95% game-clustered bootstrap CIs (B=2000).

| Comparison | Mean | 95% CI | Reading |
|---|---|---|---|
| p1 − p0 | **−0.0082** | [−0.0152, −0.0019] | The price-only forecast is **more accurate than the model** |
| p2 − p1 | +0.0006 | [−0.0008, +0.0020] | Adding the model to the price adds **nothing** overall |
| p1 − pq | −0.0006 | [−0.0044, +0.0030] | Recalibrating the raw price barely matters |
| pcal − p0 | +0.0011 | [−0.0026, +0.0046] | Recalibrating the model alone does not fix it |
| p2 − p0 | −0.0076 | [−0.0144, −0.0007] | The blend beats the raw model, because of the price |

Log loss by family (lower is better):

| Family | n | Base rate | p0 (model) | pq (price) | p2 (blend) |
|---|---|---|---|---|---|
| hits | 355 | 0.617 | 0.6646 | 0.6610 | 0.6644 |
| hits_runs_rbis | 392 | 0.648 | 0.6390 | 0.6409 | 0.6411 |
| strikeouts | 138 | 0.565 | 0.6896 | **0.6690** | 0.6883 |
| pitcher_outs | 137 | 0.504 | 0.7124 | 0.6776 | **0.6690** |
| other | 2,119 | 0.232 | 0.4613 | 0.4538 | 0.4526 |

**Top Picks** (n=75):
- They claimed 64.2% and hit **53.3%**, which is **10.8 percentage points overconfident**.
- Return at the posted odds: −2.0 units (ROI −2.7%).
- The price alone (pq) had lower log loss on these same rows than the model did.

**Equal-volume challenger** (top 75 by blend edge, p2 ≥ 0.60): 54.7% hit rate, ROI −16%.
- This does not beat the Top Picks.
- Only 5 picks overlap.
- 75 picks is not enough to draw a conclusion about selection.

## What this means for accuracy
1. The published probabilities would be more accurate if they were anchored to the posted price.
   - Today, the model's stated probability is less accurate than the price itself, especially for strikeouts, pitcher outs and the long-shot "other" markets.
   - This is a **calibration** improvement, not a betting edge.
2. Overall, there is **no evidence the model knows something the market doesn't**. That agrees with PR #201 and PR #217.
3. **Pitcher outs is the one lead:**
   - the blend beats both the model and the price there;
   - its fit-sample model coefficient interval was entirely above 0 (#217);
   - but it rests on only n=137 out-of-sample rows.

   It is a candidate for a pre-registered prospective test in 2027, not a change now.
4. The Top Pick overconfidence of about 11pp has now been replicated on forward-chained data.
   - The stated percentages shown to users overstate the hit rate.
   - The options are to display market-anchored probabilities, or to raise the bar for a Top Pick. Either needs Jacob's decision and its own proposal.

## Limitations
- Not pre-registered: the rows before 09-24 had already been examined in aggregate.
- One season, only 4 test weeks, and the last week carries 50% of the rows.
- The final-board sample (DB) is a p-surfaced subset; the frozen-board sample (FB) is the full board.
- `market_implied` is not used. q is raw, so its intercept absorbs the hold.
- No closing line is available, so CLV cannot be computed.

## Mechanism (`diagnose.py` → `diagnose_report.json`, descriptive)

This uses final-board rows only (n≈2,990, regular season). "Model" and "price" are mean stated probabilities; "real" is the realized hit rate. ΔLL is model log loss minus price log loss, so positive means the price is more accurate.

**1. Where the model disagrees with the price, the model is wrong.** Its "edge" is mostly its own error.

| Model − price | n | Model | Price | Real | ΔLL |
|---|---|---|---|---|---|
| ≤ −0.10 | 380 | 0.422 | 0.558 | 0.516 | +0.023 |
| −0.10 to 0 | 1,612 | 0.346 | 0.391 | 0.355 | −0.004 |
| 0 to +0.05 | 618 | 0.382 | 0.362 | 0.343 | +0.001 |
| +0.05 to +0.10 | 217 | 0.512 | 0.443 | 0.373 | +0.034 |
| +0.10 to +0.20 | 126 | 0.598 | 0.463 | 0.460 | +0.048 |
| ≥ +0.20 | 39 | 0.707 | 0.424 | 0.359 | +0.290 |

- When the model rates a prop well above its price, the realized rate lands at or **below the price**.
- Top Picks are ranked partly on that positive gap. That is a built-in winner's curse, and it explains their 10–11pp overconfidence.
- Top Pick rows in this set: the model said 0.640, the price said 0.533, and the realized rate was 0.500.

**2. The specific code paths that lose most to the price:**
- **`combined_strikeouts`** (`modelled_independent_binomials`, n=122): the model said 0.612, the price 0.541, real 0.475. ΔLL is **+0.108**, the worst of any probability basis with n ≥ 100.
  - `generate_picks.score_combined_strikeouts` treats each starter's batters faced as a fixed `expected_bf`, with independent binomial strikeouts.
  - That ignores early exits and game-level correlation, so the distribution is too narrow.
  - `_pick_line` then picks the rung where model − price is largest, which amplifies the error.
- **`pitcher_outs`** (`empirical_shrunk`, n=196): the model said 0.627, the price 0.560, real 0.495 (ΔLL +0.024).
  - Rows passing through the per-family **calibration layer are worse** than raw ones: +0.054 versus +0.008. That calibration likely needs refitting, or should be removed for this family.
- **`strikeouts`** (`modelled_shrunk`): ΔLL +0.010. The calibrated rows are worse than raw (+0.013 vs +0.001).
- **Thin samples:** rows in the "other" family with `sample_n` < 20 (n=301; mostly `combined_strikeouts` and `nrfi_combined`): model 0.523, real 0.445.
- **`hits_runs_rbis`** (`empirical`): ties with the price (ΔLL +0.000). **`other|combined_shrunk`** is the only basis that beats the price (−0.004).

## Proposed accuracy fixes (each needs Jacob's approval, then its own test before any production change)
1. **Anchor displayed probabilities to the price.** Use the per-family market-anchored form (p1/p2).
   - Out of sample, this is the most accurate forecast tested here.
   - It fixes the stated-versus-realized gap users see, without claiming an edge.
2. **Stop treating model − price as edge when choosing Top Picks.**
   - At minimum, require positive edge only where a family has shown out-of-sample value beyond the price; today that is no family with confidence, and only `pitcher_outs` with weak evidence.
3. **`combined_strikeouts`.** Either pause it, or replace the fixed-BF binomial with a mixture over batters faced (an overdispersed count model). Then validate the replacement against graded rungs before it is shown again.
4. **Calibration layer.** Refit or remove the per-family calibration for `pitcher_outs` and `strikeouts`, where it makes accuracy worse.

## Combined strikeouts: distribution test (`cs_dispersion.py` → `cs_dispersion_report.json`)
- **Method:** the method was committed before the run.
  - Each graded row's own ladder (the posted rung plus its stored alternatives) gives back the model's mean and SD, using a normal approximation with continuity correction. On a synthetic two-binomial case it recovers the mean within 0.08 and the SD within 0.002.
  - z is (actual − model mean) / model SD.
- **Sample:** 123 regular-season rows.

| Quantity | Model | Realized | Reading |
|---|---|---|---|
| Mean combined strikeouts | 10.36 | 9.67 | **Biased about 0.7 strikeouts high** |
| SD around the model mean | 2.83 | 3.62 | **Too narrow** (var(z) = 1.63; 11% of rows have \|z\| > 2) |

- **Forward-chained fix:** shift the mean by b and multiply the SD by s, fitted on earlier weeks only. The fitted values settle at b = −0.7 and s = 1.2.
  - On 102 out-of-sample rows, the stated probability moves from 0.615 to 0.496, against 0.500 realized, so **the fix restores calibration**.
  - Log loss improves from 0.772 to 0.730. **The posted price is still better, at 0.652.**
- **Reading:**
  - The fixed batters-faced independent-binomial assumption is measurably wrong. Its mean is too high and its spread too narrow, consistent with ignoring early exits.
  - Repairing it removes the overconfidence but does not create an edge over the price.
  - The defensible options are to anchor this market's displayed probability to the price, or to stop surfacing it. A model fix alone is not enough.
