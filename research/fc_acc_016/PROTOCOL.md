# FC-ACC-016 — prospective portfolio-challenge protocol (preregistration)

**Status: PREREGISTERED, INACTIVE, RESEARCH ONLY.**
- Nothing here captures data, changes scoring or the selector, publishes, or promotes anything.
- The rules below are fixed **before any prospective observation exists**.
- Any later change must be a new, versioned protocol committed before the slates it governs. Results under different versions are never pooled.
- Machine-readable twin: `protocol.json`. Model identity: `model_lock.json`. Reference implementation and synthetic tests: `checker.py`, `synthetic.py`, `test_fc_acc_016.py`.

North Star context:
- **72% is a target, not an established or expected rate.** Nothing in this protocol or its history shows that 72% is attainable.
- Probability, hit rate and ROI are reported separately and never substituted for one another.

## 1. Frozen predictive model identity and feature contract

| Item | Value |
|---|---|
| Artifact | `research/fc_acc_015/out/frozen_models.json` @ `84b294fc4d74409c9f5ef2bdde57aeb369fae075`, sha256 **`07115a546d839aeb7cf1ff0df45d9c9c27ac3a7757fc242d1393d38f8157207b`** |
| **Primary challenger** | entry `total_bases`, entry sha256 `68f2e65d33379d92b471f542d1083dcd37c7da978b0b43334d42fbee6e7b8fb9` |
| **Secondary challenger** (evaluated separately) | entry `hits`, entry sha256 `f64c95949252af2f8d0d13a8a6fc24cb08e5716d1b8ed89ba4cd54803cf19f7b` |
| Research diagnostic only | `strikeouts_{4,5,6}_RESEARCH_ONLY`: logged; never enters an arm or a promotion decision |
| Family | L2-penalised logistic regression (λ = 1, Newton-IRLS), FC-ACC-014 M2, V1 input set (no weather) |
| Training period | seasons 2024–2025 (FC-ACC-014 fold-2 training set), fitted once. **No refitting, recalibration or retuning** on any 2025/26 or prospective outcome. |
| Inputs | 49, in the exact order in `model_lock.json → feature_order` (sha256 per entry). Coefficients: 81 values, in `coef_order` (intercept, 49 standardised inputs, 31 missing indicators). |
| Transform | `z = ([x, NaN→train median] ‖ 1[x missing] for the 31 listed inputs − mu) / sd;  p = sigmoid(coef · [1, z])`. mu, sd and median are frozen. |
| Missing values | An input in `missing_indicator_for` takes the train median plus indicator = 1, exactly as fitted. One of the 18 inputs **never missing in training** (`critical_never_missing_in_training`, e.g. `skill_tb`, `opp_slot`, `base_logit`) missing at decision time → the challenger **cannot score the row**. It is never imputed and never filled with a later value. |
| Threshold | TB: P(official total bases ≥ 2) = FanDuel `TO_RECORD_2+_TOTAL_BASES`, `needs = 2`, line 1.5, Over / Yes, **one-sided** (no Under is listed, so no exact no-vig exists). Hits: `PLAYER_TO_RECORD_A_HIT`, `needs = 1`, line 0.5, one-sided. |
| Interpretation | A model probability for a batter in a **confirmed starting lineup**, used as-is. It is not a price, not an edge and not a guarantee. |
| Integrity | `checker.load_frozen_models()` re-verifies the file sha, every entry sha, the feature order and the coefficient hash before any use. Each unit records `model_artifact_sha256`; a mismatch makes the unit invalid. |

**Decision-time input sources:**
- **Computation:** the same frozen FC-ACC-014 feature definitions (`research/fc_acc_014/features.py` @ `c3f3362d`, sha `52493ed3…`), computed at the decision timestamp from data **strictly before the slate date** (`features_data_through < slate_date`).
- **Production fields:** `base_logit` = logit of production's own raw (pre-calibration) probability for the same line; `sig_*` = production's own candidate signals from the same run.
- **Retrospective-only rules replaced:**
  - **slot** only from a lineup **confirmed at decision time** (`slot_source = CONFIRMED_LINEUP_AT_DECISION`);
  - **opposing starter** only the **announced probable at decision time** (`sp_source = ANNOUNCED_PROBABLE_AT_DECISION`);
  - **team / home** from the decision-time lineup side, never from a post-game log;
  - **weather** is not an input (V1).
- **Starter change after decision:** the decision stands. It is never re-scored, and it is settled exactly as the book settles it (batter props are not voided by a pitching change). It is flagged `SP_CHANGED_AFTER_DECISION` and covered by a pre-declared sensitivity analysis that excludes flagged rows. *This supersedes the FC-ACC-015 §8 "voided" wording, which conflated model validity with settlement.*

## 2. Arms (same opportunity universe, same cutoff)

**Decision unit `u`**
- Lineups and prices are decided game by game, so cross-game reallocation at one slate-wide time would require hindsight.
- A unit is one champion export run `R` (the PR #230 decision export) together with every game whose **final pregame** export is `R` (the latest run with `prepared_at ≤ first_pitch − 10 min`).
- Its cutoff is that run's `publication_cutoff_at`. Every arm decides unit `u` from exactly the rows and authentic offers of `R`.
- Slate results are the sum of their units.

**N**
- `N_u` is the number of champion rows carrying the lane's status in `R` (PR #230 `recommendation_status`; Top Pick count = `new_publication_policy_N` scope).
- It is the **actual operational count before any research exclusion**, never re-derived.
- `N_u = 0` → every arm selects nothing. No pick is invented.

**Arms**
- **A — operational champion:** the actual champion rows, unchanged.
- **B — champion + frozen challenger for the same N_u slots:**
  - for rows of the challenger market (TB 2+, or hits 1+ in the secondary test), the frozen challenger probability replaces the champion probability in **every** probability-dependent gate;
  - every other champion pick keeps its own probability;
  - the pool is the non-challenger champion picks plus the challenger-market rows that pass all gates (§3);
  - B takes the top `N_u` by the lane's ordering (§3.9).
  - A champion pick of the challenger market that fails under the challenger is removed and counted as such.
- **C — market-only:**
  - pool: every row in the same unit universe that passes the lane's non-probability gates (authentic, fresh offer; lineup and reliability rules of the lane; data fresh);
  - ranking: raw price-implied probability from the actual American price (ties → `canonical_id`); top `N_u`;
  - the same per-player concentration rule applies;
  - **C_market** = the same ranking restricted to the challenger market (a secondary, model-free benchmark inside TB 2+).
  - For two-sided markets the exact no-vig is reported beside it. TB 2+ and hits 1+ are one-sided, so only the raw break-even exists.

**Lanes (preregistered; see §9 for the governance choice)**

| Lane | Champion status | Probability rule | Evidence | Lineup | SUSPECT | Ordering for slot competition |
|---|---|---|---|---|---|---|
| **TP**, mandated primary | `top_pick` | p ≥ 0.60 | reliability A/B | confirmed | not a gate | pessimistic probability ↓, then pessimistic ROI ↓, then `canonical_id` |
| **V**, preregistered alternative primary | `value` | p < 0.60 | none (production) | production rule (challenger rows always need a confirmed lineup by §1) | blocks | pessimistic ROI ↓, then pessimistic probability ↓, then `canonical_id` |

**Primary hypothesis**
- **H1-TB (per lane):** across comparable units, wins(B) − wins(A) > 0 at equal pick counts, with no unacceptable offered-price ROI deterioration (§7).
- Lane TP is the mandated primary. Lane V becomes the TB primary **only if** chosen in writing at gate G0 before capture starts (§9).

**Secondary hypotheses (evaluated separately, never pooled with the primary)**
- **H2-HITS:** the same test with the frozen hits challenger governing hits 1+ in lane TP.
- **H3:** B vs C and B vs C_market.
- **H4:** prospective calibration of each challenger.
- Strikeouts: logged diagnostics only.

## 3. Rules that prevent artificial model wins (identical for every arm unless stated)

1. **Eligibility:**
   - the row exists in the champion export run `R` for a game in unit `u`;
   - a challenger row additionally needs every challenger-input rule in §1.
2. **Book / source:**
   - FanDuel only;
   - an offer must carry the FC-OPS-001/PR #226 label `MATCHED_RESEARCH_ONLY` and `authenticated = true`;
   - market, needs, line and side must equal the row's.
   - Offers are produced upstream by Codex, never by this protocol. An isolated parser result is not enough.
3. **Quote validity and freshness:**
   - observed at or before the unit cutoff, no more than 45 minutes (2,700 s) before it, and strictly before first pitch;
   - the price is an integer with |American| ≥ 100.
   - A later price is a new observation, never the decision price.
4. **Confirmed lineups:**
   - lane TP: for every arm;
   - challenger rows: always (the slot input requires it).
5. **Starting-pitcher changes:** per §1. The decision stands, settlement follows the book, the row is flagged, and sensitivity results are reported.
6. **Exact market threshold:** per §1; whole-number lines are out of scope.
7. **Minimum evidence reliability:**
   - lane TP: production's A/B grade;
   - lane V: production imposes none beyond the robust interval test, and the protocol adds none.
8. **Price and uncertainty gates:** production's own functions, read-only, with the sha pinned in `protocol.json`:
   - `value_verdict(p, price, prob_lo, min_roi = 0.05, require_robust = True)` must return BET, plus the lane's probability rule; lane V also requires `market_agreement ≠ SUSPECT`;
   - **challenger interval:** `p_lo_challenger = p_challenger − max(0, p_champion − p_lo_champion)` for the same line. The frozen model has no interval of its own, so it borrows the champion's downside width; no interval → the robust test fails closed.
   - **A TB candidate never enters on probability alone.** It needs an authentic, fresh offer whose price clears the same ROI and robustness tests.
9. **Candidate replacement ordering:**
   - the lane ordering above, applied to the governing probability and interval;
   - deterministic, with ties broken by `canonical_id`;
   - B can admit a challenger row only by displacing the lowest-ordered pick, and never exceeds `N_u`.
10. **Correlation / concentration:**
    - at most one selection per player per unit;
    - a challenger row cannot enter for a player already selected (including a player holding a non-challenger champion pick);
    - market-only applies the same cap;
    - arm A is never altered; its native duplicates are counted and reported.
11. **Missing data:**
    - challenger inputs per §1 (critical missing fails closed);
    - a champion pick without an authentic, fresh offer makes the unit **NON-COMPARABLE**. It never silently shrinks N.
12. **Shortfall:** if B or C cannot fill `N_u` from qualifying rows, it selects what qualifies, records the shortfall, and is excluded from the primary analysis. Shortfall units are reported in a sensitivity analysis.
13. **Integrity (unit invalid if any fail):**
    - every time is present;
    - `prepared_at ≤ cutoff`;
    - the run is final pregame for every game in the unit;
    - selections are sealed (`sealed_at`) before the unit's first pitch;
    - coverage is `COMPLETE` or a proven `ZERO_PICK` (PR #230 `coverage_status`; `UNVERIFIED`, `PARTIAL` and `MISSING` are invalid);
    - the model sha is recorded and matches.
14. **Outcome blindness:** selection code never reads outcomes. This is tested by injecting outcomes and requiring identical arms.

## 4. Outcomes

**Settlement**
- Official final game and player statistics under the book's own market rules.
- `WIN` / `LOSS` count. `PUSH` / `VOID` (DNP, postponement) return the stake and are excluded from settled counts but reported.
- `UNRESOLVED` holds the unit pending; it is never imputed.
- Flat 1-unit stake at the **captured decision-time American price**.

**Primary**
1. Additional settled wins at equal legitimate counts: ΔW = Σ_u (wins_B − wins_A) over comparable, fully settled, non-shortfall units.
2. Offered-price ROI, B vs A: ΔROI = profit_B/stakes_B − profit_A/stakes_A.
3. Opportunity shortfalls and realised operational volume: N requested, B realised, non-comparable / shortfall / pending units, and actual champion volume per slate.

**Secondary**
- Calibration (calibration-in-the-large, slope), log loss and Brier, computed on **every challenger-scored row** of its market with a settled outcome (this requires grading all such rows, §8).
- Discrimination (AUC, once enough rows exist).
- Model-minus-raw-break-even gap.
- Market- and slate-level breakdowns.
- Selection overlap: added and removed counts and their wins (discordance D).
- Player/game concentration.
- B vs C, C_market.
- First-half vs second-half stability.
- Odds-band composition.

**Reporting**
- No profitability claim from synthetic, archived or non-authenticated observations.
- Hit rates are always shown alongside realised volume and ROI.

## 5. Uncertainty and sample-size framework (no guaranteed sample sizes)

1. **Primary interval:** slate-clustered bootstrap, 10,000 reps, seed 20270101. Equal counts hold within every slate, so ΔW resamples are equal-volume by construction.
2. **Sensitivity:** game- and player-clustered intervals, computed as rate differences scaled to the settled count (FC-ACC-015 showed that naive per-player sums do not preserve equal counts).
3. **Information requirement:**
   - the effect comes only from discordant selections, D = |added| + |removed| settled;
   - with win-rate variance near 0.24 per pick, a 95% interval for ΔW excludes 0 only when roughly **D/2 > 1.84/δ²**, where δ is the true win-rate difference between added and removed picks;
   - so δ = 0.15 needs D ≈ 160, δ = 0.10 needs D ≈ 370, and δ = 0.05 needs D ≈ 1,500.
   - These are planning arithmetic, not promises.
   - **Below D = 200 the result is `NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE`**, which is not a negative finding.
4. **Training/serving parity (outcome-free; must pass before shadow start):**
   - on captured slates, compare each input's missing rate and standardised mean against the frozen artifact (the mu of each missing indicator is the training missing rate);
   - tolerances: missing-rate gap ≤ 0.10, standardised mean shift ≤ 0.5 SD (`checker.serving_skew`);
   - any flag halts the protocol before outcomes are examined. It is fixed only by a new versioned protocol, never by refitting.
5. **Fixed horizon:**
   - all 2027 regular-season units, unless Jacob fixes another end date in writing before capture starts;
   - **no optional stopping**, and no efficacy decision before the horizon;
   - one mid-season, safety-only look (integrity, volume, ROI deterioration).
6. **Prospective structural expectation (recorded before any observation; prediction-only, no outcomes read; `floor_reach.py`, `out/floor_reach.json`):**
   - over all 121,566 retrospective lineup-batter TB 2+ rows (2024–26), the frozen TB challenger reaches the 0.60 Top Pick floor on **3 / 6 / 1 rows per season** (max 0.619 / 0.627 / 0.603), **before** any price or interval gate; production's own TB probability never does (max 0.555);
   - the hits challenger reaches it on about 55% of rows;
   - therefore **H1-TB in lane TP is expected to yield D ≈ 0 and be NOT_EVALUABLE**. A null there means "untestable under the Top Pick standard", never "TB adds nothing".

## 6. Verdict labels (computed by `checker.verdict_label`)

- **`NOT_EVALUABLE_INTEGRITY`:** more than 10% of units non-comparable.
- **`NOT_EVALUABLE_VOLUME_SHORTFALL`:** B realises less than 95% of requested N.
- **`NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE`:** D < 200.
- **`SUPPORTED_PENDING_AUTHORIZATION`:** all of:
  - the slate-clustered lower bound of ΔW is > 0;
  - the ΔROI lower bound is ≥ −0.02 per unit staked and the ΔROI point estimate is ≥ 0;
  - ΔW > 0 in both season halves;
  - the challenger's prospective calibration has |calibration-in-the-large| ≤ 0.03 and slope in [0.7, 1.3].
- **`NOT_SUPPORTED`:** otherwise.

## 7. Promotion evidence standards (separate gates; each needs Jacob's separate written authorization)

| Gate | Requirement | Status today |
|---|---|---|
| **R — research readiness** | Frozen model lock verified; protocol and checker committed; labeled synthetic suite and tests pass; governance choice G0 recorded | Protocol, lock and checker: **done**. G0: **pending**. |
| **L — live-data capture readiness** | FC-OPS-002 (Codex) emits certified `fc-acc-016-bundle-1` units: authenticated S1 offers, the PR #230 champion export with the §8 additions, same-unit seals, and graded outcomes for every row of the challenger markets. Bounded live diagnostic authorized. | **Not ready** (FC-OPS-002 inactive; PR #226/#230 not certified together) |
| **S — shadow-test readiness** | Decision-time challenger feature builder (research-only) reproduces the frozen definitions; serving-parity check (§5.4) passes on captured slates without reading outcomes; sealing before first pitch demonstrated; Jacob authorizes a shadow. | **Not ready** (builder not built; no captured slates) |
| **P — production promotion readiness** | Full fixed horizon; `SUPPORTED_PENDING_AUTHORIZATION` on the primary; no integrity failure; results reproducible from sealed bundles; held-out evidence and explicit model versioning per AGENTS.md rule 15; SUPERCHAD audit; **separate final authorization by Jacob** | **Not ready** |

- Passing any gate never implies the next.
- `SUPPORTED_PENDING_AUTHORIZATION` is evidence, not promotion.
- A hit-rate gain that comes with a ROI loss beyond the margin is **not** a promotion case.
- **TB 2+ value picks are plus-money propositions, roughly 35–45% to win.** They can serve ROI but cannot move the 72% hit-rate goal; putting them in Top Pick slots would lower the hit rate. Only H2-HITS is aligned with the hit-rate North Star.

## 8. Interface requirements (conflict-free with Codex; `interface_version = fc-acc-016-bundle-1`)

The checker **consumes** units; it never parses pages, joins offers or exports champions.

| Field | Producer | Notes |
|---|---|---|
| Unit: `unit_id`, `run_id`, `prepared_at`, `decision_cutoff_at` (= `publication_cutoff_at`), `sealed_at` + seal hash, `games[].first_pitch_at`, `coverage_status` | **Codex** (FC-OPS-002 / PR #230) | Existing export fields plus the seal and final-pregame assignment |
| Row: `canonical_id`, `game_pk`, `player_id`, `team`, `market`, `needs`, `wager_side`, `line`, `recommendation_status`, `hit_probability` | **Codex** (PR #230 `decision_rows`) | Exist today |
| Row additions: `prob_ci`, `reliability`, `lineup_confirmed` (+ observed_at), `data_fresh`, `raw_hit_probability`, `signals`, announced probable SP id (+ observed_at) | **Codex** | **Not in the current PR #230 export**; required |
| Row `offer`: FC-OPS-001 accepted offer + `join_label`, `authenticated`, source IDs, raw sha, `observed_at`, `american` | **Codex** (FC-OPS-001 + PR #226) | Exists as an inactive contract |
| `outcomes`: per `canonical_id` status (WIN/LOSS/PUSH/VOID/UNRESOLVED) for **all** rows of the challenger markets, not only selected ones | **Codex** grading path | Needed for calibration |
| Row `challenger`: frozen-definition features, `features_built_at`, `features_data_through`, `slot_source`, `sp_source`; unit `model_artifact_sha256` | **Claude** (future FC-ACC research-only builder; separate authorization) | Not built; never written into Codex files |

- **No-edit rule:** Claude edits no FC-OPS-002 / FC-OPS-001 / PR #226 / PR #230 file, and Codex edits no `research/fc_acc_01*` file.
- **Schema changes:** a change to the bundle requires a new interface version agreed in Issue #91 before capture.

## 9. Governance choice G0 (must be recorded before capture starts)

Lane TP, the mandated primary, is expected to be non-evaluable for TB 2+ (§5.6). Before any capture, Jacob/SUPERCHAD must record one of:
- **(a)** keep lane TP as the TB primary and accept an expected `NOT_EVALUABLE`;
- **(b)** make lane V (production's own `value` standard) the TB primary. This is the only operational standard under which TB 2+ can legitimately compete. **Recommended.**
- **(c)** make H2-HITS (lane TP) the primary hit-rate test and keep TB as a lane-V research secondary.

No choice may be made or changed after any prospective outcome is observed.
