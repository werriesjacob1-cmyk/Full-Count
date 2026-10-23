# FC-ACC-016 Amendment A1 (protocol v1.1.0) — preregistered, inactive, research only

**Authority.** SUPERCHAD's G0 decision (2026-10-23): option (b) for 2+ total bases, with (c) as a separate primary hit-rate test.

**What A1 changes.** Only the experiment designations, E2's primary outcome, and an E2 evaluability gate.

**What A1 preserves.**
- The v1.0.0 protocol at `417184c0` is preserved unchanged. Its five core files are pinned by sha256 in `amendment_a1.json` and re-verified on every import of `a1.py`.
- Frozen models: unchanged.
- No prospective outcome exists. A1 is committed before any.

## 1. Changes (complete list)

1. **E1 (primary, hit rate).**
   - The frozen **hits 1+** challenger in lane TP is compared against the **actual Top Picks** at equal legitimate operational volume.
   - Its outcomes, uncertainty treatment and verdict label are the v1.0.0 rules, unchanged.
2. **E2 (separate primary, ROI).**
   - The frozen **TB 2+** challenger runs in the **existing value lane** (lane V), anchored to the actual value-lane count.
   - Primary outcome: offered-price flat-stake ROI difference B − A.
   - **Label `SUPPORTED_PENDING_AUTHORIZATION`** requires all of:
     - the slate-clustered 95% lower bound of ΔROI > 0;
     - D ≥ 200 discordant settled picks;
     - realized volume ≥ 95%;
     - no more than 10% of units non-comparable;
     - calibration within the v1.0.0 bounds.
   - Wins, volume, shortfalls and calibration are secondary.
3. **No pooling.**
   - `a1.evaluate_experiment` hard-binds E1 → (TP, hits) and E2 → (V, total_bases); the caller cannot choose a lane or market.
   - `a1.top_pick_hit_rate` refuses anything but E1. No value-lane selection ever enters a Top Pick hit-rate denominator.
4. **E2 structural evaluability gate (new, outcome-free).**
   - Before any E2 capture may even be proposed for authorization, a production feasibility check must show both:
     - TB 2+ rows carrying a production interval (at least 50% of rows over at least 8 in-season snapshots);
     - at least 1 frozen-challenger TB 2+ row per full slate passing the **unchanged** lane-V gates.
   - A passing gate means "eligible for capture authorization", not "evaluated".

**Unchanged:**
- the 0.60 Top Pick floor;
- every v1.0.0 gate (authentic, fresh FanDuel offer; lineup and reliability rules; `value_verdict(min_roi = 0.05, require_robust = True)`; SUSPECT rule; one pick per player; sealing and integrity);
- the borrowed-interval rule, which fails closed when there is no champion interval;
- decision units and N anchoring;
- the frozen models;
- the uncertainty framework and the fixed horizon.

A1 does **not**:
- relax any gate;
- add any interval for TB;
- refit anything.

## 2. Value-lane eligibility and shortfall assessment (outcome-free; `feasibility.py` → `out/feasibility.json`)

**Data**
- 8 production dashboard snapshots (`docs/data.json`). Each is the last commit at or before 22:30 UTC on 2026-08-20, 08-25, 08-30, 09-04, 09-09, 09-15, 09-20 and 09-25; commit SHAs are listed in `feasibility.py`.
- Frozen-challenger probabilities come from the reproduced FC-ACC-014 feature rows, which exist only through 2026-08-25.
- Prices are production's own captures and are **not authenticated** under FC-OPS-001.
- No outcome is read: feature `y` values are deleted on load.

**E2: TB 2+ in lane V. Not evaluable.**

| Measure | Value |
|---|---|
| TB 2+ dashboard rows / priced | 1,098 / 940 |
| Rows carrying a production interval (`prob_ci`) | **0**. Basis is `modelled_shrunk` (809), `league_only` (19) or none (270); production gives these bases no interval by design. |
| Champion TB 2+ `value` rows | **0** |
| Frozen-challenger TB rows scored with a price (Aug 20 and 25) | 264 |
| Passing the **unchanged** lane-V gate | **0**. With no champion interval, the robust test fails closed. |
| *Sensitivity only, never a rule:* passing if an interval of half-width 0.02 / 0.04 / 0.06 existed | 5 / 0 / 0 |
| Value-lane N per snapshot | 0–16 (total 69), almost all `hard_hit_105` |

- **Conclusion:** under production's own value standard, the TB 2+ challenger has **zero eligible opportunities**, so arm B always equals arm A and D = 0.
- **Gate result:** `a1.e2_structural_gate` → `NOT_EVALUABLE_STRUCTURAL` (`TB2_PRODUCTION_INTERVAL_ABSENT`, `TB2_VALUE_LANE_ELIGIBILITY_BELOW_MINIMUM`).
- **Even with an interval, volume would be tiny.** At +200, the value-and-not-SUSPECT window admits only p ≈ 0.35–0.38 with a lower bound above 0.333, and even a narrow hypothetical interval admits only about 2% of priced rows.
- **The only remedy is a production change.** Production would have to supply a defensible per-line interval for TB 2+. That requires separate authorization and its own held-out evidence, followed by a new amendment (A2) and a fresh feasibility check, all before capture.

**E1: hits 1+ in lane TP. Feasible, on thin evidence.**
- **Champion volume:** the champion's graded Top Picks (`docs/history.json`, 34 slates, 2026-09-08 → 10-19) include **64 hits 1+ picks, about 1.9 per slate**.
- **Aug 25, the only full-slate snapshot with challenger features:**
  - 148 hits rows pass the non-probability gates with an interval;
  - the challenger qualifies 7 of them under the unchanged Top Pick gates;
  - it **rejects both** of the champion's hits Top Picks;
  - so arm B would differ from arm A on that slate (discordance about 4).
- **Aug 20:** no hits row cleared the gates.
- **Interpretation:** E1 can produce discordant selections, but one slate cannot estimate its volume. Accrual of D ≥ 200 over a season is plausible but unproven.

## 3. Bridge fields for Codex (coordination via Issue #91; FC-OPS-002 is neither assumed complete nor edited)

- **Unchanged from v1.0.0 §8.**
- **A1 adds two requested fields:** per-row `probability_basis` and `prob_ci_source`, so that E2's structural gate can be re-checked from exported rows.
- **Export scope:** the export must include every value-status and hits 1+ row, plus graded outcomes for every hits 1+ and TB 2+ row.

## 4. Verdict

| Experiment | Readiness |
|---|---|
| **E1, hits 1+ vs Top Picks** | **MODIFY.** Research-ready; capture is blocked on the FC-OPS-002 bridge fields, the decision-time feature builder and the parity check, each needing separate authorization. |
| **E2, TB 2+ value lane** | **STOP (structurally not evaluable).** 0 of 940 priced TB 2+ rows carry the production interval the unchanged value gate requires, so there are zero eligible opportunities. It is not to be captured or declared evaluable unless an authorized production interval exists and A2 passes the structural gate. |
| Strikeouts | Research diagnostic only (unchanged) |

## 5. Limitations

- The snapshots are taken at 22:30 UTC. They are not the final pregame state for every game.
- The prices are unauthenticated.
- Challenger features exist only through 2026-08-25, so the E2 eligibility count rests on 2 dates and the E1 discordance figure on 1 full slate.
- The feasibility features carry retrospective (C-class) lineup-slot and starter inputs.
- The champion's Top Pick volume figure comes from graded history, not from PR #230 exports.
- None of this is evidence about winners or ROI.

Alligator
