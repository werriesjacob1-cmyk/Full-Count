# RED TEAM REPORT — HR Contact-State EXECUTION Spec v1

Independent read-only methodology review of
`engineering/PREREG_HR_EXECUTION_V1.md`, run 2026-09-01 against draft
`f17cebea9afb3d256d50a4f587b5670629ce1385`.

## VERDICT: **PREREG NEEDS REVISION BEFORE LOCK**

The reviewer's own summary of what the draft got right: §0's enumeration of
estimator degrees of freedom is "correct and unusual," the strict `<` leakage
discipline is well built, per-slate volume is the right instinct, and §9's
refusal to build the park join after seeing results is exactly right.

What it got wrong: **one inverted scientific justification, one estimator that
cannot be built as written, one contract whose mechanism does not exist, one
undefined selection policy, and a power profile that makes its own
permanent-closure rule dangerous.**

---

## MUST-FIX BEFORE LOCK (10)

| # | finding | why it blocks |
|---|---|---|
| **F1** | **Power.** The frozen `>=500` gate counts *candidates*, but the primary estimator rests on `k` = swapped picks, plausibly 45–135. Estimated MDE ±9–18 pp against a plausible true effect of +1–3 pp → **power ≈ 9%**. The frozen §7 then records a null as "mechanism CLOSED — do not iterate," permanently. | A ~90%-likely false negative is written into the permanent record as a positive finding. Highest severity. |
| **F2** | **The intercept is not inert.** Under §5 the transform applies to SUPPORTED rows only, so the intercept is a *differential* shift promoting regular starters over bench bats. **The challenger can win with `beta = 0`.** The draft's justification is not merely misleading — it is inverted. | A headline win attributable entirely to coverage, pre-disclaimed as "not itself a finding." |
| **F3** | **Fixed unit coefficient on `logit(p_champion)`.** If the champion's logit is mis-scaled (slope ≠ 1), any feature collinear with hitter power absorbs that mis-scaling into `beta`. `bat_speed` is exactly such a feature. | `beta ≠ 0` is satisfiable by champion miscalibration alone, with zero new physical information. |
| **F4** | **`equal_volume.py` cannot implement §7.** It takes a single global integer `volume` and does global top-N — the design §7 opens by declaring wrong. Also: defaults `iterations=2000, seed=20260827` (spec requires 5,000 / 20260828); CI is on overall hit-rate delta, not added-minus-removed; empty draws silently dropped. | The per-slate wrapper and the primary-quantity bootstrap are unwritten code whose choices land exactly where the stop rule fires. |
| **F5** | **"The locked selection policy" does not exist.** `n_D` is undefined. The production selector cannot be used because the frozen §5 verifies no prices exist for this population, so the real board would select zero rows. | The widest unlocked knob in the design — wider than anything §0 enumerates. |
| **F6** | **sklearn 1.9.0 cannot express the offset** (verified: no offset param on `__init__` or `.fit`). The "fallback" is the only estimator, and its normalization is unspecified — sklearn's `C*sum(loss)+0.5‖w‖²` vs `mean(loss)+(1/2C)‖w‖²` differ in shrinkage by a factor of **n**. | `C = 1.0` is **not locked**. The escape hatch is the normalization convention. |
| **F7** | **Park rule unevaluable + noise-dominated.** §9.7 forbids building the venue map against 2026; §10 kills on unresolved `game_pk`s in the evaluation population, which *is* 2026. And "incomplete" has no defined disposition. Separately, leave-one-park-out sign-flip on a 5–10 pp SE flips by noise alone. | The frozen park stop rule either can't run, fires spuriously, or gets resolved after the effect size is known. |
| **F9** | **4–5 holdout reads, no multiplicity control, no named primary arm.** Family-wise false-positive ≈ 15–18%. | Combined with F1: ~15% chance of a spurious win, ~90% chance of missing a real one. The worst possible ratio. |
| **F14 (1,4,5)** | SUPPORTED defined two ways (a row can be `supported=True` with a `None` feature → NaN or silent zero-imputation, which the frozen §4 forbids); `OutcomePolicy` unnamed; `prior_swings` sorts by date only, so same-date tie-breaking depends on incoming row order → **features not reproducible across differently-ordered reads.** | Silent contract violations and run-to-run feature drift. |
| **F(new)** | **Holdout end date and artifact SHA are not frozen.** §6 says only `date >= 2026-01-01`. The season is live, so the holdout grows daily and a later re-run on a superset is not currently forbidden. | Undeclared population growth between reads. |

## NICE-TO-HAVE (7)

- **F10** — `A_recency` placebo arm (trailing-window feature with no bat-tracking content) plus a max-age cap on the 100-swing window. Without it, an April win is prior-season carryover, not swing geometry. Also: `bat_speed` **already** reaches the board via `bat_speed_bonus → form` (`generate_picks.py:1475-1479`), so arm B may rediscover a signal production already uses.
- **F8** — per-date SUPPORTED coverage and supported-fraction-on-selected as mandatory pre-effect-size reports. Coverage varies hugely by month, so the challenger's *effective* freedom concentrates on late-season dates; per-slate equality equalizes counts, not where the challenger may act.
- **F11** — the pairing claim overstates precision: clusters are paired, rows are not, and where arms pick different games a cluster contributes to one side only.
- **F12** — report within-day concentration and per-arm park distribution. **Do not cap picks-per-game** — a cap hands back a knob.
- **F13** — report the `code_git_sha` distribution across training and holdout; the champion is a mixture of versions, not a fixed function.
- **F15** — state plainly that this matches a *research* selection volume, not board volume, and that a positive result is necessary but not sufficient for board improvement.
- **F14 (2,3,6)** — arm E's effective threshold is 1 batted ball, not 30; standardization population unstated; `bat_speed = 0` is a tracking artifact, not signal.

## Corrections the reviewer says to REFUSE

Explicitly flagged as **adding** researcher freedom, and therefore worse than
the flaws they repair:

- any power or park criterion stated as analyst judgment rather than a fixed constant;
- restricting evaluation to SUPPORTED rows (violates the frozen §6.5);
- a picks-per-game cap;
- restricting training to one `code_git_sha`;
- switching the interval method (percentile → BCa) after seeing a marginal result.

> Where the choice is between a crude rule and a better rule with a soft
> threshold, keep the crude one and demote it to a diagnostic.

## Disposition

**Four findings were factual errors in the draft and are corrected inline**
in `PREREG_HR_EXECUTION_V1.md`, marked `[RED TEAM CORRECTION]`: F6 (sklearn
cannot express the offset), F2 (inverted intercept justification), F4 (the
`VERIFIED` label verified existence, not capability — a mislabeled evidence
claim), F5 (`n_D` undefined).

**Everything else changes the experiment's design** — adding control arms
(`A_shift`, `A_recal`, `A_recency`), adding an `INCONCLUSIVE-BY-POWER` verdict
with a numeric threshold, naming arm D as the single primary arm, freezing the
holdout end date. Those are scientific decisions for Jacob and are **recorded
as recommendations, not applied.**

The draft is **NOT LOCKED** and must not be treated as review-complete until
they are resolved.
