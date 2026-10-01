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
