# FC-ACC-001: winning-pick accuracy, first findings (2026-10-13)

> **CORRECTION NOTICE (Amendment A1, 2026-10-13).** This is the ORIGINAL
> report. It is kept unchanged below as historical evidence. Codex's audit
> ([PR #226 comment 6151131715](https://github.com/werriesjacob1-cmyk/Full-Count/pull/226#issuecomment-6151131715))
> found errors, and these statements are superseded by `AMENDMENT_A1.md`:
> - **Public baseline.** It is **321/609 = 52.7%, ROI −11.2%**, 6.35 pp below
>   the break-even of the published odds, not 298/552 = 54.0%. All 57
>   "ungraded" picks have retained grades in `results/grades_*.json`.
> - **`market_implied` has mixed meaning.** It is no-vig fair for two-sided
>   markets and raw break-even otherwise. With a uniform raw break-even, CH1 is
>   **67/91** (+1.4% ROI). The original 68/91 stays visible as an exploratory
>   observation.
> - **CH1 against market-only** is only **+2 wins, 90% CI [−5, +9]**. There is
>   **no demonstrated model advantage over sportsbook prices.**
> - **Eligibility.** CH arms share the research pool, **not** the existing Top
>   Pick eligibility: 79/91 of CH1's picks are `lean` and 62 fail
>   `price_clears`. "+20 winners" is a shadow-reranking observation, not 20
>   more published or eligible winners.
> - **"Adverse selection"** is descriptive, not a demonstrated causal
>   mechanism.
> - **The S1 protocol in §7** is replaced by `S1_PROTOCOL.md`, which is
>   currently **BLOCKED** on authentic offer provenance.


Research only. Nothing here is promoted, deployed or merged. Preregistration:
`PREREG.md`, commit `bb8600e8f5`, sha256 `e6466a71…dbbb`, anchored on Issue #91
(comment 6135666313) before any challenger result was computed.

**Bottom line.**
1. The current Top Pick selector loses against its own prices. It systematically picks where the model most disagrees with the market, and those picks hit **below** what the frozen price implies:
   - −4.3 pp on 552 public picks;
   - −5.9 pp on the 91 sealed-freeze picks.
2. Re-ranking the **same eligible pool at the same volume** by a market-anchored blend, (p + q)/2, turned 48/91 winners into 68/91. That is +20 net winners. ROI went from −10.5% to +2.7% at the frozen price.
3. That gain is mostly a move to short favorites (median −260), so a high hit rate is cheap and is not by itself profit.
4. The evidence is exploratory: 19 late-day slates, partly seen before.

**Verdict for the primary arm:** EXPLORATORY_POSITIVE, not promotable. Next step: a sealed forward shadow (§7).

Reproduce:

```bash
python3 research/fc_acc_001/baseline_and_losses.py   # Regime A baseline + loss attribution
python3 research/fc_acc_001/competition.py            # preregistered competition (Regime B)
python3 research/fc_acc_001/posthoc_checks.py         # post-hoc qualifiers (not preregistered)
```

Outputs, with every input file's sha256, are in `out/`.

## 1. Champion baseline by evidence regime (never pooled)

### A. Public Top Pick ledger (what customers received)

| Item | Value |
|---|---|
| Source | `data/public_top_picks/registry.json` (629 picks, 2026-08-18..10-12, 42 days) |
| Grades | from retained `docs/history.json` (45-day retention) |
| Ungraded | 57 picks dated 08-18..08-28 and 10-06..10-12 have no retained grade. They are counted, never imputed. |
| Policy | recommendation status `top_pick`: p ≥ 0.60, evidence grade, confirmed lineup, robust price/value test, fresh data |
| Version labels | model 2026.08.15, selection 1.0.0, calibration label 1.0.0 throughout. The calibrator actually in effect changed on 09-14, 09-21, 09-28, 10-05 and 10-12 (PR #225 registry), so the label is not one policy. |

| Window | Settled | Hits | Hit rate (95% CI) | ROI at published price | Mean stated p | Mean implied q |
|---|---|---|---|---|---|---|
| Full, 08-29..10-05 | 552 | 298 | 54.0% [49.8, 58.1] | −8.8% | 0.647 | 0.583 |
| Before 09-20 (hypothesis window) | 341 | 189 | 55.4% | −6.1% | 0.646 | 0.578 |
| 09-20 onward | 211 | 109 | 51.7% | −13.3% | 0.649 | 0.590 |

**Daily volume:** median 15 published per day, range 2–29.

**By market, full period:**

| Market | n | Hit rate | Implied | Excess | ROI |
|---|---|---|---|---|---|
| hits | 99 | 57.6% | 0.580 | −0.4 pp | −0.3% |
| hits_runs_rbis | 326 | 56.1% | 0.611 | −5.0 pp | −8.2% |
| pitcher_outs | 38 | 50.0% | 0.514 | −1.4 pp | −10.9% |
| strikeouts | 89 | 43.8% | 0.509 | −7.1 pp | −19.7% |

All public picks are overs.

### B. Prospective full-candidate freezes (sealed, pregame)

| Item | Value |
|---|---|
| Boards | 20 (`output/board_freeze_2026-09-20..10-12`); 19 graded (10-12 has no graded file) |
| Candidates | 5,643 graded; 2,786 priced at the freeze |
| Pregame check | every game started after `sealed_at` (verified per board) |
| Caveat | each freeze is sealed once, late (about 19:40–01:50 UTC), so it covers only the day's remaining games: 1–16 per board, and a single game on most postseason dates. It is a subset of the day, not the full public slate. |
| Champion | freeze `top_pick` statuses: 91 settled over 19 slates. 62/62 of them are in the public ledger on dates where publication worked; publication stalled 10-06..10-11, as found in PR #224. |
| Champion result | 48/91 = 52.7%, ROI −10.5%, mean q 0.586, **excess −5.9 pp** |

### C. Historical research

`backtest/rows.jsonl` is gitignored and absent here. Historical reconstructed prices have no timestamps. It is used for nothing in this report.

PR #220's exploratory equal-volume study (75 picks, 2026-08-31..09-27) is cited only as prior context. It found the same pattern: champion 53.3%; price-ranked 80.0% with ROI ≈ 0.

## 2. Where we lose (Regime A; hypotheses from before 09-20 only)

Sample: 341 settled public picks.

1. **Model-over-market disagreement.**

   | Model − market | n | Hit rate |
   |---|---|---|
   | +3–6 pp | 200 | 59.5% |
   | +6–10 pp | 80 | 55.0% |
   | +10–15 pp | 38 | 42.1% |
   | ≥ +15 pp | 22 | 45.5% |

   The selector requires the model to beat the price, so it preferentially keeps the model's **errors**. This is adverse selection, also called the winner's curse.

   Over the full period, every disagreement band hits below its own implied probability. Pooled, that is 298/552 (54.0%), with the whole CI below the mean implied 0.583.
2. **Price band.**

   | Odds | n | Hit rate |
   |---|---|---|
   | > +100 | 17 | 29% |
   | −110..−139 | 106 | 47% |
   | −140..−179 | 168 | 61% |
   | −180..−249 | 39 | 67% |
3. **Marginal probability.** Picks with stated p in 0.60–0.63, just over the floor, hit 47% (n=136, ROI −14.5%).
4. **Market.** pitcher_outs 42% (n=31) and strikeouts 50% (n=66), against hits 64% (n=70) in the window.
5. **Thin evidence.** sample_n < 40 hit 46% (n=97), against 59% at ≥ 80 (n=175).
6. **Concentration.** Not a loss source here: single-pick games hit 49%, while 4+ picks in one game hit 59%.

**Not measurable from retained evidence.** No price history or line movement is recorded per public pick. Lineup state is null on public snapshots. Opponent and playing-time context exist only as free-text `why` strings. None of these is claimed.

## 3. Equal-volume selector competition (Regime B, preregistered)

Setup:
- Same eligible pool per slate, U_d: priced, QC-kept, lineup not assumed, reliability A/B/C.
- Exactly N_d picks per slate, where N_d is the champion count.
- One line per (player, stat, side).
- No arm was ever short of N_d. All 19 slates are comparable for CH1–CH4.
- Champion picks outside U_d: 0.

| Arm | Picks (settled) | Hits | Hit rate | ROI at frozen price | Mean q | Δ hit vs champion | One-sided 95% LB (game / slate) | Markets |
|---|---|---|---|---|---|---|---|---|
| Champion (`top_pick`) | 91 | 48 | 52.7% | −10.5% | 0.586 | — | — | HRR 63, hits 13, K 11, outs 4 |
| **CH1 BLEND, (p+q)/2 (primary)** | 91 | **68** | **74.7%** | **+2.7%** | 0.720 | **+22.0 pp** | +15.6 / +12.6 | HRR 76, hits 9, outs 6 |
| CH2 MARKET, q (chalk benchmark) | 91 | 66 | 72.5% | −1.7% | 0.735 | +19.8 pp | +11.6 / +9.2 | HRR 61, hits 29, NRFI 1 |
| CH3 CAPPED_MODEL | 91 | 57 | 62.6% | −11.6% | 0.701 | +9.9 pp | +2.1 / +3.9 | HRR 86, hits 5 |
| CH4 DEPTH (n ≥ 40) | 91 | 58 | 63.7% | −10.1% | 0.702 | +11.0 pp | +3.3 / +4.1 | HRR 87, hits 4 |
| CH5 ONE_PER_GAME (falsification) | 32 | 14 | 43.8% | −29.4% | 0.598 | NON_COMPARABLE (short 59) | — | — |

- Holm-adjusted one-sided p across the secondary arms: CH2 < 0.001, CH3 0.021, CH4 0.021.
- **Stability.** CH1 hit 81.3% (26/32, ROI +3.5%) in the 2026 regular season and 71.2% (42/59, ROI +2.2%) in the postseason. The champion hit 53.1% and 52.5%.

**Overlap and added/removed winners (against champion):**

| Arm | Shared | Winners retained | Winners removed | Losers removed | Winners added | Losers added | Net winners |
|---|---|---|---|---|---|---|---|
| CH1 | 12 | 8 | 40 | 39 | 60 | 19 | **+20** |
| CH2 | 7 | 5 | 43 | 41 | 61 | 23 | +18 |
| CH3 | 26 | 14 | 34 | 31 | 43 | 22 | +9 |
| CH4 | 26 | 14 | 34 | 31 | 44 | 21 | +10 |

**Primary verdict (CH1): EXPLORATORY_POSITIVE.** The one-sided lower bound is > 0. The chalk guard does not fire, because CH1's ROI exceeds the champion's. Under PREREG §3 it is **not promotable**.

### Post-hoc qualifiers (not preregistered; they can only weaken the result)

| Arm | Excess over market (hit − mean q) | ROI Δ vs champion (90% CI, game bootstrap) | Excess Δ vs champion (90% CI) | Median odds | Share shorter than −200 |
|---|---|---|---|---|---|
| Champion | **−5.9 pp** | — | — | −145 | 3% |
| CH1 | **+2.7 pp** | +13.2 pp [+2.9, +24.9] | +8.6 pp [+2.8, +15.2] | −260 | 84% |
| CH2 | −1.0 pp | +8.8 pp [−3.9, +21.7] | [−2.7, +12.6] | −270 | 91% |
| CH3 | −7.5 pp | −1.1 pp | [−9.3, +6.3] | −230 | 70% |
| CH4 | −6.5 pp | +0.4 pp | [−8.4, +7.3] | −230 | 71% |

Reading: **most of CH1's hit-rate gain comes from shorter prices.** Pure chalk (CH2) also reaches 72.5%, but only matches the market (−1.0 pp) and loses money. The parts of CH1 that look real are smaller:
- it stops the champion's −5.9 pp adverse selection;
- it shows a small positive excess (+2.7 pp).

Capping disagreement or requiring depth while still ranking by the model's p (CH3, CH4) moves to chalk but keeps the negative excess.

### Pool-level test (preregistered §8; 2,056 graded eligible candidates, 60 games)

| Test | Result |
|---|---|
| `hit ~ logit(q) + d`, d = model log-odds over market | coefficient on d = +0.11, 95% CI [−0.13, +0.40] |
| Core markets only | +0.04 [−0.44, +0.61] |
| AUC: p / q / blend | 0.836 / 0.843 / 0.844 |
| AUC, core markets: p / q / blend | 0.587 / 0.616 / 0.615 |

**The model's disagreement with the price carries no demonstrated information about who wins.** The price ranks winners at least as well as the model does.

## 4. Answering the 72% target honestly

A 72% hit rate is reachable at equal volume, in this window, by ranking toward short favorites (CH1 74.7%, CH2 72.5%). At a median price near −260, though, the break-even rate is about 72%. **Hit rate alone does not identify a winning selector.** A selector that only buys chalk hits ~72% and loses the vig.

Measure "winning picks" together with:
- (a) excess over the market-implied rate;
- (b) ROI at the actual price.

Otherwise the target rewards chalk.

## 5. Ranked shortlist of accuracy interventions

| Rank | Intervention | Added winners at equal volume | Evidence reliability | Economics at offered price | Cost |
|---|---|---|---|---|---|
| 1 | **Market-anchored ranking (CH1 blend) as a sealed forward-shadow arm.** | +20 / 91 here | Exploratory: 19 late-day slates, prior exposure, in-sample regime | +2.7% ROI and +2.7 pp excess here, but at −260 median prices with thin margins | Low: a re-rank of the existing pool, no new model |
| 2 | **Stop selecting on model-over-market disagreement.** The `top_pick` value test turns model error into picks. | Explains the champion's −4.3 / −5.9 pp excess | Consistent across Regimes A and B and the #220 exploration | Removes a known negative-excess source | Low, but it is a selection-policy change: Jacob decides, after shadow |
| 3 | **Change the success metric** to equal-volume hits **plus** excess-over-market **plus** ROI. | n/a | Definitional | Prevents chalk-driven "wins" | Very low |
| 4 | **Model-level research on the residual over price.** This is PR #220 V3's C2 arm: p2 − p1, already preregistered with sealed capture. | Unknown | Pool test says the current model shows no information beyond price | Only route to a real edge | Built; needs Jacob's activation |
| — | Evidence-depth filter (CH4), disagreement cap with model ranking (CH3), one-per-game (CH5) | — | No excess gain; CH5 worse | — | **Not recommended** |

Escalation criterion met: the existing model scores do not discriminate winners beyond the price (§3, pool test). Market-level model research (rank 4) is justified, not more selector tuning alone.

## 6. NO overclaim

- No challenger is promotable.
- The improvement found is exploratory, chalk-heavy, from late-day partial slates, in a regime this author had partially seen.
- The robust finding is negative: the champion's selection rule loses against its own prices.

## 7. Forward shadow specification (FC-ACC-001-S1, not activated)

| Item | Specification |
|---|---|
| Universe | Per slate unit (DAY / NIGHT, as in PR #220 V3 §4): a full pregame candidate freeze sealed **before the unit's first pitch**, with exact captured FanDuel prices and capture timestamps. Today's single late-evening freeze does not cover the whole slate; the V3 runner already does this. |
| Arms | Champion (frozen shadow `top_pick`, as V3 §8); **CH1 BLEND (primary)**; CH2 MARKET (chalk benchmark); RAW p. Each takes exactly N_d per unit, one line per (player, stat, side), ties by candidate_id. |
| Primary metric | Equal-volume **hits**, reported with **excess over market** (hit rate − mean captured q) and **ROI at the captured price**. |
| Success | Promotion candidate (Jacob decides) only if all three hold: the hits difference has a one-sided 95% game-clustered LB > 0; the excess difference LB > 0; and the ROI difference is > 0. |
| Chalk-only result | A hits gain without an excess gain is reported as CHALK and is not adoptable. |
| Regimes | 2026 postseason: descriptive. **2027 regular season: confirmatory.** |
| Sample floor | ≥ 250 settled champion picks and ≥ 60 units. One analysis, no interim outcome looks. |
| Implementation | Reuse PR #220 V3 capture, seal and evaluation. Add CH1 and CH2 as **separately preregistered** arms under a new anchor. V3's own prereg and verdict stay unchanged. |
| Activation | Needs Jacob's explicit authorization, like V3 §16. |

## 8. Provenance

| Item | Value |
|---|---|
| Base | main `3d409488c` |
| Branch | `claude/fc-acc-001-winning-pick-accuracy` |
| Input sha256 | written in `out/baseline_public.json` (`provenance`) and `out/competition.json` (`inputs`, per board and graded file) |
| Bootstrap | B = 2000; seeds 20261013 (preregistered) and 20261014 (post-hoc) |
| Not changed | published picks, grades, ledger, production code, calibration, selector, PRs #220 / #223 / #224 / #225 |

Alligator
