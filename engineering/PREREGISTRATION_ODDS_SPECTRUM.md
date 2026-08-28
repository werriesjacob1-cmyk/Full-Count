# PRE-REGISTRATION — Full-Odds Accuracy Program

**Status: LOCKED on authorship. Written before any canonical result for the
evaluation window existed.**

The commit that introduces this file is the timestamp. Its parent contains no
odds-spectrum result. Any change to the rules below must be a NEW commit that
states what changed and why, so that "the rules moved" is always visible in
`git log` rather than inferable only by memory.

**North Star (unchanged):** REALIZED MLB PROP HIT RATE / PREDICTIVE ACCURACY AT
THE SAME LEGITIMATE USABLE PICK VOLUME.

**Secondary objective:** find economically valuable mispricing across the full
actionable odds spectrum.

ROI does not replace the North Star. Neither does Brier, log-loss, or a
best-season backtest.

---

## 0. Evidence labels used throughout

`VERIFIED` measured in this repo · `INFERRED` follows from verified facts ·
`HYPOTHESIS` plausible, untested · `UNKNOWN` not established.

---

## 1. Why this document exists

Maximising hit rate has a trivial degenerate solution: bet the shortest prices.
Maximising ROI has the mirror one: bet longshots and wait for variance. A
program that measures either alone can be satisfied without the system getting
better.

`MIN_LINE_PROB = 0.60` currently excludes every prop the model prices below 60%.
Measured over `data/props/`: that is **2,398,594 of 2,817,401** archived priced
rows — **85.1%** of what FanDuel actually offers, **1,647,480** of them at +300
or longer. (VERIFIED.) Whether the model is any good there is *unmeasured*, not
unmeasurable.

The purpose of locking rules now is to make the eventual answer *findable*
rather than *searchable*.

---

## 2. FROZEN POPULATION DEFINITIONS

Four populations, deliberately distinct. Conflating them is how selection
quality gets mistaken for model quality.

### 2.1 CANDIDATE POPULATION
Every row the canonical generator emitted for a slate date, from the pinned
code SHA, with `status == "ok"` on its durable checkpoint. A `no_games` date
contributes zero rows and is not a failure. An `error` date disqualifies the
run, not just the date.

A **candidate** is uniquely `(date, game_pk, player_id, prop_type, line)` —
`equal_volume.CANDIDATE_IDENTITY_FIELDS`. Verified 0 duplicate identities in
93,937 cells. (VERIFIED.)

### 2.2 ELIGIBLE POPULATION
The candidate population filtered by, and only by, rules fixed here:

1. `outcome` present (ungradeable rows are omitted by the generator, never
   encoded as 0 — `backtest/SCHEMA.md`).
2. `score` and `predicted_prob` present.
3. A **valid pregame price** exists (§2.4).
4. `fair_test` — **declared explicitly per experiment, never defaulted**
   (`backtest/calibration.py`'s standing rule). The primary analysis uses
   `fair_test_only = False`; excluding no-opportunity picks is a
   *secondary* diagnostic, because a pick that never got a chance is a real
   outcome for a bettor who staked it.

Eligibility never depends on the model's own opinion. `MIN_LINE_PROB`,
`MIN_QUALITY_SCORE` and `MIN_POSITIVE_LIFT` are **selection policy**, not
eligibility — putting them here would delete the plus-money space before it
could be measured, which is the exact failure this program exists to correct.

### 2.3 SELECTED POPULATION
The top-N of an ordering produced by a `SelectionPolicy` over the whole
eligible population. Champion and challenger receive the identical
`EligiblePopulation` object.

### 2.4 OPERATIONALLY USABLE POPULATION
Eligible **and** actionable in the real world:

- **Actionable price**: American odds in **[-400, +2000]**. Pre-registered on
  operational grounds, not observed returns — outside that range FanDuel limits
  are small and the market is thin. Anything outside is `OUT_OF_SCOPE`, counted
  and reported, never silently dropped.
- **Valid pregame price**: a quote with `in_play == False` **and**
  `taken_at < start_time`. Both conditions, always. (See §6.)
- **Usable volume** is measured in *this* population. A challenger that wins by
  betting props nobody could actually take has not won.

### 2.5 Deterministic resolution rules

| situation | rule |
|---|---|
| no price exists | row leaves the eligible population; counted in the coverage table; never imputed |
| multiple valid prices | `LAST_PREGAME` — the last quote strictly before first pitch (§6) |
| outcome missing | row is not eligible. Within a *selected* set, `OutcomePolicy` is declared before the run; primary uses `OUTCOME_EXCLUDE_PAIRWISE` so both sides lose the same rows |
| identity cannot be joined | counted as `UNJOINED` and reported per market; never silently dropped. If `UNJOINED` exceeds **10%** for a market, that market is `DESCRIPTIVE ONLY` for that experiment |
| price exists, candidate not operationally selected | it stays in the ELIGIBLE population. This is the point: we are measuring what the model *could* have found, not only what the board shipped |
| duplicate canonical rows | forbidden by `EligiblePopulation`, which raises on duplicate identity |
| many candidates → one price key | the join is many-to-one on the input side (VERIFIED: 388 joined records mapped to 336 graded keys). Primary analysis deduplicates on candidate identity **before** joining |

---

## 3. PRICE BANDS (pre-registered)

Fixed bands for product reporting; continuous analysis to prevent boundary
artefacts. **Both**, as directed.

| band | American range | rationale |
|---|---|---|
| B1 heavy favorite | ≤ -300 | where the board currently lives |
| B2 favorite | -299 … -150 | |
| B3 slight favorite | -149 … -101 | |
| B4 near even | -100 … +109 | straddles the coin-flip |
| B5 modest plus | +110 … +174 | "+150-ish" |
| B6 plus | +175 … +249 | "+200-ish" |
| B7 strong plus | +250 … +399 | "+300-ish" |
| B8 longshot | +400 … +899 | |
| B9 extreme longshot | +900 … +2000 | HR/moonshot territory |
| — | outside [-400, +2000] | `OUT_OF_SCOPE` |

Boundaries were chosen on round implied-probability landmarks and product
legibility **before** any band's realized performance was computed. They are
frozen. (VERIFIED: chosen before any per-band result was produced.)

### 3.1 Sparse-band rule — fixed now, not after

Per band, per experiment, by **`n` and effective clusters (games)** only —
never by the band's realized performance:

- **SUFFICIENT** — n ≥ 200 **and** ≥ 100 distinct games.
- **LOW POWER** — n ≥ 50 and ≥ 30 games. Reported with intervals; may not
  support a promotion claim alone.
- **DESCRIPTIVE ONLY** — n ≥ 10. Reported; never used in any test.
- **UNUSABLE** — n < 10. Reported as a count only.

Bands are **never merged, deleted, or re-cut after seeing outcomes.** If a band
is underpowered, that is the finding.

---

## 4. CONTINUOUS PRICE ANALYSIS

Run alongside the bands, pre-specified:

1. **Primary continuous form**: logistic regression of `outcome` on
   `logit(posted_implied)` plus a model term, with games as the clustering
   unit. Reports whether edge varies smoothly with price.
2. **Calibration-in-price**: realized hit rate vs `posted_implied` across the
   whole range, with the identity line as reference.
3. **Non-parametric**: LOWESS of outcome on `logit(posted_implied)`, for shape
   only, never for a p-value.

The bands and the continuous fit must agree qualitatively. If they disagree,
that disagreement is reported as the result — not resolved by picking whichever
looks better.

---

## 5. EQUAL VOLUME

`backtest/equal_volume.py` remains authoritative. Champion and challenger get
the same `EligiblePopulation` and the same N.

**Primary volume policy — CHAMPION-MATCHED MARKET MIX.** The champion selects
its N; its per-market counts are recorded; the challenger must match those
counts exactly, reordering only within each market.

Justification, measured before this document: unconstrained pooled ranking
degenerates into base-rate mining. Ranking all rows by `predicted_prob` put
23/25 of the top 25 into the two highest base-rate markets and scored 0.726 —
against **0.693 for a no-model strategy** that simply bets the highest-base-rate
market at random. Roughly 90% of that apparent win was market mix. (VERIFIED.)

**Price bands are a REPORTING STRATIFICATION, not a volume allocator.** N is set
once, globally. Per-band N is whatever the selection produces. Optimising N per
band after seeing results is explicitly forbidden — it is the "challenger wins
by betting less" failure wearing a different hat.

If a band cannot support the volume its stratum implies, that is **reported as a
limitation**, never fixed by changing N.

---

## 6. PRICE SNAPSHOT — audit and decision

`LAST_PREGAME` is **retained** as the canonical rule. Audited, not assumed:

- **Lead time before first pitch**: median **47.1 min**, p10 14.2, p90 190.5,
  across 147,862 priced props. Consistent across all 13 markets (per-market
  medians 41–49 min). (VERIFIED.)
- **Availability**: ~7,041 priced (player, stat, needs) props per day with a
  valid pregame quote. (VERIFIED.)
- **Lookahead**: 1.7% of archived rows are post-first-pitch. `in_play` agreed
  with the timestamp test on **1,023,166 rows with zero disagreements in either
  direction** — but both are checked anyway, because ten days of agreement is
  not a guarantee and the cost of being wrong is a fabricated longshot edge.
  (VERIFIED.)
- **Survivorship/availability bias**: `HYPOTHESIS` — a prop pulled before first
  pitch (scratch, injury) keeps its last quote and may never settle. Handled by
  the missing-outcome rule (§2.5), and the `UNJOINED`/no-price counts are
  reported per market so the bias is visible rather than assumed absent.

**Price movement is itself information** (median intraday range 150 American
points; 70.1% of props move ≥50). That is a **FUTURE CHALLENGER experiment**,
not a change to the primary. `OPENING` exists in the code for that declared
comparison. There is deliberately **no "best price" rule**, and a test asserts
it is unreachable — it would be a post-hoc maximiser wearing a policy's clothes.

---

## 7. ENDPOINTS

### 7.1 PRIMARY CONFIRMATORY (exactly one)

> Within the OPERATIONALLY USABLE population, at champion-matched market mix
> and equal N, does the challenger achieve a **higher realized hit rate** than
> the champion?

One test. One number. Paired cluster bootstrap on `game_pk`, 5,000 resamples,
95% CI. Clustering is not optional: on the one comparison run so far it widened
the interval by ~35% and flipped the conclusion from "significant" to
"indistinguishable from zero". (VERIFIED.)

### 7.2 SECONDARY (pre-specified, multiplicity-controlled)

Per price band and per prop family, each reporting **all seven** required
quantities together:

1. realized hit rate 2. break-even hit rate 3. hit-rate edge vs break-even
4. ROI 5. sample size 6. 95% CI (clustered) 7. usable pick volume

Hit rate and ROI are **always reported side by side**. Neither may be quoted
alone. A band with a low hit rate and positive edge-vs-break-even is a
*positive* finding; a band with a high hit rate and negative edge is a
*negative* one.

### 7.3 EXPLORATORY

Everything in §9. Exploratory results are **hypothesis-generating only** and can
never justify a production change on their own. They must be labelled
EXPLORATORY wherever reported.

---

## 8. MULTIPLE TESTING

- **Primary**: one test, α = 0.05, no correction needed because there is one.
- **Secondary**: Benjamini–Hochberg FDR at q = 0.10 across the pre-registered
  secondary family. The family is **enumerated before results** — 9 bands × the
  prop families present — and does not grow afterward.
- **Exploratory**: no correction, and correspondingly no promotion authority.
- **The family may not be enlarged after seeing results.** A test thought of
  later is exploratory, permanently.
- **Every experiment is registered** by writing its manifest — population
  fingerprint, policy identities, volume, outcome policy, quote rule, code SHA —
  before it runs. `EqualVolumeExperiment` already emits
  `experiment_manifest_id` for this.

---

## 9. TEMPORAL DESIGN

Random shuffling of MLB dates is **forbidden** — it leaks future form into past
predictions. All splits are chronological (`backtest/calibration.py`'s
`time_based_split` makes the same argument).

- **Train / development**: earliest 60% of canonical dates.
- **Validation**: next 20%. Iteration is allowed here.
- **Holdout**: final 20%, locked by `accuracy_lab.lock_holdout()`, which
  computes the cutoff once and never recomputes it.
- **Season stability**: every primary and secondary result is additionally
  reported **per season** (2024, 2025, 2026).

**A challenger strong in one season and weak in another is NOT promoted.** It is
recorded as season-dependent and returned to research. Consistency of sign
across seasons is required; equality of magnitude is not.

Note (VERIFIED): the price archive spans 2026-08-06→26, so the *priced* window
is a 20-day slice at the very end of the canonical range. Price-conditioned
analyses therefore have **no cross-season replication available yet**. Until
they do, every price-band result is at most `LOW POWER` and cannot alone
promote anything. This is a stated limitation, not a defect to be argued away.

---

## 10. PROMOTION CRITERIA

A challenger is promoted only if **all** hold:

1. **Hit rate**: primary endpoint CI for the hit-rate difference **excludes
   zero** in the challenger's favour, on the locked holdout.
2. **Volume**: equal N by construction, and usable volume not reduced —
   `fully_refillable` where suppression is involved.
3. **Temporal robustness**: sign consistent across every season with a
   `SUFFICIENT` sample.
4. **Coverage**: does not achieve its gain by collapsing into one market or one
   price band. Market mix is champion-matched, so a collapse shows up as a band
   concentration and is reported.
5. **Economic sanity**: ROI not materially negative where the hit-rate gain is
   claimed. ROI is a **veto, not a driver** — it can block a promotion, never
   cause one.
6. **Pre-registration**: the experiment was registered before it ran.

**No fixed "must improve by X%" threshold.** The existing evidence does not
support choosing one honestly: the only equal-volume comparison run to date gave
+0.047 with a 95% CI of [-0.008, +0.102] — an interval too wide to calibrate a
threshold against. Inventing a number now would be arbitrary. The CI-excludes-
zero rule is the deterministic decision framework instead. `DATA-DEPENDENT`:
once the full canonical run yields a holdout-scale interval, a minimum
*practically meaningful* effect may be set — from the observed **width** of
intervals, never from the observed **direction** of effects.

---

## 11. PRE-COMMITTED SCENARIO INTERPRETATIONS

Written now so the answer cannot be reinterpreted after it arrives.

| scenario | pre-registered interpretation |
|---|---|
| Strong at +100…+300 | Real finding **if** it survives the sparse-band rule and season check. Action: raise coverage by lowering `MIN_LINE_PROB` as a *challenger selection policy*, tested at equal volume. Not a threshold tweak |
| +300 amazing, tiny n | `LOW POWER` or `DESCRIPTIVE ONLY`. **Not promotable.** Action: keep collecting; revisit at a pre-set n, not at a pre-set result |
| +150 consistently strong | Strongest available evidence, since it has both sample and price room. Still requires the primary endpoint |
| Favorites win hit rate, plus-money wins ROI | **Expected, not a paradox** — it is arithmetic. North Star governs: hit rate at equal usable volume decides promotion. The ROI finding is reported as a genuine secondary result and may justify a *separate* product surface, not a change to the main board |
| Challenger helps plus-money, hurts overall hit rate | **Not promoted** as champion. Recorded as a candidate *price-conditioned* policy for a future stratified experiment |
| Prop types differ in optimal odds | `HYPOTHESIS` worth a per-family challenger. Does **not** license per-family threshold tuning, which is a threshold forest (§13) |
| Nothing works anywhere | A real and publishable result. The plus-money space is then measured-and-negative rather than unmeasured, which is strictly better than today |

---

## 12. PLUS-MONEY & FULL-ODDS HYPOTHESES

Recorded, **not implemented**. Each: rationale · upside · evidence needed ·
leakage risk · complexity · canonical support · changes eligibility/ranking/
volume · priority.

**H1 — Market-implied probability as a model input.** Rationale: the market is a
strong baseline; the question is whether the model adds information *beyond* it.
Upside: high. Evidence: info-beyond-market framework already exists (Phase 3.7).
Leakage: **HIGH** — the price must be the pregame price, never a later one.
Complexity: medium. Canonical support: only in the 20 priced days. Changes
ranking, not eligibility or volume. **Priority 1.**

**H2 — Model-vs-market residual ranking.** Rank by `predicted_prob −
posted_implied` rather than `predicted_prob`. Rationale: directly targets
mispricing and is naturally price-aware. Upside: high. Leakage: same as H1.
Complexity: **low** — a `SelectionPolicy`, nothing else. Changes ranking only.
**Priority 1** — the single cheapest real test.

**H3 — Price-conditioned ranking.** Separate orderings within price strata.
Upside: medium. Risk: multiplies the test family fast. Complexity: medium.
**Priority 3.**

**H4 — Plus-money-specific challenger.** A policy trained/tuned only on
`posted_implied < 0.5`. Upside: medium-high. Evidence: needs cross-season
replication that does not exist yet. **Priority 2.**

**H5 — Uncertainty-aware ranking.** Use `prob_ci` width to downweight thin
samples. Rationale: longshots have the thinnest evidence, so this bites hardest
exactly where we are expanding. Upside: medium. Complexity: low. **Priority 2.**

**H6 — Per-prop-family models.** Upside: unknown. Complexity: **high**, and
risks a model zoo. Requires H2/H5 to be exhausted first. **Priority 4.**

**H7 — Line-movement as signal.** Median intraday move is 150 points; steam may
carry information. Leakage: **HIGH** — trivially becomes lookahead. Requires
`OPENING` vs `LAST_PREGAME` as a declared pair. **Priority 3.**

**H8 — Alternate-line selection.** The canonical dataset carries exactly one
line per player-market — 0 of 93,937 cells have two (VERIFIED). `select_shadow_
tracking()` recovers demoted alternates but runs only in live `main()`, never in
the backtest. Line choice is therefore invisible to research. Upside: **high and
entirely unexplored.** Complexity: low-medium. **Priority 2.**

---

## 13. WHAT WE SHOULD NOT BUILD

- A general betting-market abstraction layer. One join module is enough.
- Per-band or per-prop-family threshold tuning — a threshold forest, and the
  fastest route to overfitting historical prices.
- A model zoo before H2 and H5 are exhausted. Two good ranking policies beat
  nine mediocre specialised ones.
- Any "best price across snapshots" rule. Post-hoc maximiser; already blocked
  by a test.
- ROI-maximising selection. Bets longshots and waits for variance.
- Metrics infrastructure beyond the seven required quantities.
- Re-cutting price bands to improve statistical appearance.
- A new artifact-management platform. Durability already works — measured this
  session: **142 dates / 295,999 rows survived a container reclamation with
  bounded loss.**

---

## 14. KNOWN LIMITATIONS OF THIS PROGRAM

1. Priced window is **21 days**; canonical range is 877 dates. No cross-season
   price replication exists. (VERIFIED.)
2. Batter props are archived **one-sided** (`american` only) — true no-vig
   probability is not recoverable for them. Pitcher props are two-sided with
   `hold` removed. (VERIFIED.)
3. Canonical rows carry **no `calibrated_prob`** by schema design.
4. `moonshot_420` cannot be produced by the canonical generator —
   `hit_distance_sc` is absent from `STATCAST_COLUMNS` — yet it **is** priced in
   the archive. Any moonshot analysis is therefore price-side only. (VERIFIED.)
5. wRC+/Stuff+ absent: ~40% of the batter baseline-skill component is missing
   from every backtested row, by the engine's own account.
6. The shadow-record sample is **not representative** — see §15.

---

## 15. SELECTION-BIAS REGISTER (shadow records)

Every restriction on the 2,176 shadow predictions, documented so model quality
is never confused with selection-policy quality or market availability:

- **Not all candidates.** `select_shadow_tracking(candidates, n_per_key=1)`
  keeps **one candidate per (stat, needs) key** — the best one. This is a
  top-of-ranking sample, not a random sample. (VERIFIED.)
- **13 slate dates only**, 2026-08-14→26. (VERIFIED.)
- **Surfaced by one policy**, at one time of day, from whichever markets
  FanDuel had posted at capture time.
- **Composition**: batter 1,100 · pitcher 594 · pitcher_combo 397 · game 85.
  Status: neutral 1,278 · lean 307 · value 11 · top_pick 3 · none 577.
  (VERIFIED.)

**Therefore**: shadow records may be used for pipeline diagnostics and
hypothesis generation. They are **not** a valid evaluation population for the
primary endpoint. The canonical population is.
