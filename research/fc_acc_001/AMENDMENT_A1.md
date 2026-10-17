# FC-ACC-001 Amendment A1: post-audit corrections (2026-10-13)

**Status: POST-HOC.** This amendment is written after Codex's independent audit
([PR #226 comment 6151131715](https://github.com/werriesjacob1-cmyk/Full-Count/pull/226#issuecomment-6151131715))
and after its results were known. It corrects evidence and semantics. It does
not re-run or replace the preregistered experiment.

The original records stay immutable:
- `PREREG.md` (commit bb8600e8f5, sha256 `e6466a71…dbbb`, anchored in Issue #91
  comment 6135666313);
- `competition.py`'s preregistered arms;
- `out/competition.json`, `out/baseline_public.json` and `out/posthoc_checks.json`.

Code: `amendment_a1.py`. Output: `out/amendment_a1.json`. Tests:
`test_fc_acc_001_research.py` at the repository root.

## A1.1 Public baseline (P1): corrected from authoritative sources

**Original error.** The original loader read grades only from the rolling
45-day `docs/history.json`. It reported "57 picks without retained grades".
That was wrong: all 57 have settlements in the retained `results/grades_*.json`
files.

**Correction.** `common.load_public_reconciled()` reads both sources. It raises
on any disagreement and imputes nothing.

**Evidence window (pinned).** Published slates and frozen boards **≤ 2026-10-11**,
all with final grades: `common.EVIDENCE_CUTOFF`. Later data never shifts these
numbers; it is reported separately. Since the audit, `main` has appended picks
and graded the 2026-10-12 slate.

The audit counted 629 picks. The 20 it reported as not settled were 9 voids
plus the 11 then-ungraded 2026-10-12 picks.

| | Value |
|---|---|
| Registered public Top Picks, ≤ 2026-10-11 | 618 |
| Grade source: both agree | 561 |
| Grade source: results files only | 57 |
| Grade source: conflicts | **0** |
| Settled | **609**: 321 wins, 288 losses |
| Void | 9 |
| **Post-window (new evidence, not in the audit)** | 2026-10-12+: 12 published, 11 settled, **6–5**, ROI −11.1%, 1 ungraded |
| Realized hit rate | **52.7%** |
| ROI at published odds, flat 1 unit | **−11.2%** (−68.0 units) |
| Mean offered-price break-even at published odds | **59.1%** |
| Hit rate vs break-even | **−6.35 pp** |
| Stored `market_implied` mean | 58.3%. Mixed semantics (A1.2); not a break-even. |

By market (P1):

| Market | Settled | Hit rate | Mean break-even | Hit − break-even | ROI |
|---|---|---|---|---|---|
| hits | 110 | 55.5% | 57.9% | −2.5 pp | −4.0% |
| hits_runs_rbis | 360 | 55.0% | 61.2% | −6.2 pp | −10.4% |
| pitcher_outs | 43 | 46.5% | 55.0% | −8.5 pp | −17.1% |
| strikeouts | 96 | 43.8% | 54.0% | −10.3 pp | −19.8% |

## A1.2 Price and probability semantics

The stored freeze and registry field `market_implied` is **semantically mixed**:
- When `market_hold` is present (two-sided markets: strikeouts, pitcher_outs,
  nrfi_combined), it is a **no-vig fair** probability.
- Otherwise (one-sided batter props), it equals the **raw offered-price
  break-even**.

Verified on every priced freeze record.

From now on these four quantities are kept separate:

| Quantity | Definition | Code |
|---|---|---|
| `break_even_raw` | from the offered American odds, vig included; uniform across markets | `common.break_even_raw` |
| `fair_novig` | only where a two-sided de-vig exists; one-sided markets are **unknown**, never imputed | `common.fair_novig` |
| `p` | FULL COUNT model probability (`hit_probability`) | |
| ranking score | arm-specific, e.g. (p + break_even_raw)/2 | |

## A1.3 Corrected arms, alongside the originals

Setup: same 19 graded sealed slates, same research pool U_d, same N_d, same
tie rule.

| Arm | Wins / 91 | Hit rate | ROI | Mean break-even | Hit − break-even | Picks shorter than −200 |
|---|---|---|---|---|---|---|
| Champion (existing `top_pick` policy) | 48 | 52.7% | −10.5% | 59.2% | −6.4 pp | 3% |
| CH1 original (q = stored, mixed) | **68** | 74.7% | +2.7% | 72.3% | +2.5 pp | 84% |
| **CH1u (q = uniform break-even)** | **67** | 73.6% | +1.4% | 71.9% | +1.7 pp | 81% |
| CH2 / CH2u (market-only control) | **66** | 72.5% | −1.7% | 73.5% | −1.0 pp | 91% |

The original 68/91 stays visible and reproducible. It is an exploratory
observation under mixed price semantics.

Slate-cluster bootstrap (10,000 resamples, seed 20261015):

| Comparison | Wins Δ (90% CI) | ROI Δ (90% CI) |
|---|---|---|
| CH1 − champion | +20 [+11, +29] | +13.2 pp [+1.3, +26.1] |
| **CH1 − market-only** | **+2 [−5, +9]** | +4.3 pp [−6.3, +17.0] |
| CH1u − champion | +19 [+11, +28] | +11.9 pp [+0.8, +23.8] |
| **CH1u − market-only** | **+1 [−6, +9]** | +3.1 pp [−7.7, +16.4] |

**Reading.** Almost all of the gain over the champion is also achieved by
ranking on the sportsbook price alone. **There is no demonstrated model
advantage over sportsbook prices.**

Uniform-price residual test on 2,056 candidates:
`hit ~ logit(break_even_raw) + d` gives d = +0.045, 95% CI [−0.21, +0.34].
This means "no demonstrated incremental signal". It is not a reliably negative
signal.

The original report's "adverse selection" wording is withdrawn as a causal
claim. Odds and market mix shift across disagreement bands. The observation is
descriptive.

## A1.4 Populations: never pooled, gains never transferred

| Population | Definition | Count |
|---|---|---|
| **P1** published customer Top Picks | immutable public registry | 629 (609 settled) |
| **P2** publication-eligible candidate Top Picks | freeze `recommendation_status == top_pick` under the existing policy | 91 on 19 slates: **60 published**, 31 unpublished (2026-10-06..10-11 Pages outage, PR #224) |
| **P3** research-board selections | freeze `selected_top_pick` (top-10 main board; research artifact) | 80 |
| **P4** alternative-policy shadow selections | CH1 / CH1u / CH2 / CH2u on the research pool | 91 each |

P4 arms do **not** satisfy the existing Top Pick policy. CH1's 91 picks break
down as:
- **79** have `lean` status and 12 have `top_pick`;
- **62** fail `price_clears`;
- 3 have reliability C;
- 1 was published.

The "+20 winners" is an exploratory P4-versus-P2 reranking result on late-day
frozen boards. It is **not** 20 additional published winners, and it is **not**
20 additional currently eligible winners.

## A1.5 Loader hardening and window pin

`common.load_freezes()` now:
- re-verifies every board with the production `board_freeze.verify_board_seal`;
- **requires** each graded file's `source_board_sha256` to equal the verified
  hash. A missing binding fails closed.

All 19 graded boards pass.

**Window pin.** `competition.py` and `amendment_a1.py` now load boards only
through `EVIDENCE_CUTOFF`. `main` now also holds a graded 2026-10-12 board, which
was ungraded when the preregistered run happened. The amendment refuses to run
unless its board and grade files are byte-identical to those recorded in the
original `out/competition.json`.

Every preregistered arm result reproduces exactly. The test
`OriginalResultsReproduce` checks selected, settled, hits and units for all
arms. The committed original output files themselves are left untouched.

## A1.6 What is withdrawn or qualified from the original report

- **"57 picks have no retained grade"**: wrong. Corrected in A1.1.
- **Public 54.0% / −8.8%**: true for the history-retained subset only. The full
  population is 52.7% / −11.2%.
- **"Same eligible pool"**: CH arms share the research pool, not the existing
  Top Pick eligibility policy (A1.4).
- **Market-anchored excess "+2.7 pp"**: under uniform semantics it is +1.7 pp,
  and it is indistinguishable from the market-only control.
- **"Adverse selection" (causal)**: downgraded to a descriptive observation.

Alligator
