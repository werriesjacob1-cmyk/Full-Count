# FC-ACC-013 — pitcher workload-distribution experiment (2026-10-22)

**Decision: STOP for this challenger** (frozen rule, `PREREG.md` §8, committed at `0ace5fbb` before evaluation).
- On FULL COUNT's actual production path, averaging the strikeout probability over a pitcher-specific BF distribution (same mean) does **not** improve 2025 prediction.
- It is significantly **worse** at 6+. It is not better after either calibration treatment.
- It creates no pregame ability to see early exits.
- It only removes Overs, never adds a winner.

**Status:** retrospective, exploratory and outcome-only. 2025 and 2026 are not untouched holdouts. No production or calibration change, and no betting claim.

## 1. Identities

- **Production:** main `5e6deaee19` (2026-10-22). File sha256 values are in `PREREG.md` §1. The strikeouts calibrator is from `calibrators_by_market.json` sha `2f15846e…`, promoted 2026-10-12 and fitted on 2026-09-05..09-22 rows (partly in-sample for 2026).
- **Data:** FC-ACC-004's StatsAPI starter dataset, re-retrieved 2026-10-22 with identical endpoints and file names (`data.py`).
  - 57 of 78 raw files are byte-identical to FC-ACC-004's manifest (`86a09fc094`).
  - 21 per-pitcher log batches differ in bytes but have **identical byte sizes**. FC-ACC-004's raw bytes are not committed, so they cannot be diffed.
  - Parsed population identical to FC-ACC-004: 618 candidate pitchers, 2 invalid stats, 4,856 / 4,860 / 4,858 starts. Eligibility is identical: 2,893 excluded (fewer than 3 prior same-season starts), with **3,883 / 3,907 / 3,891** modelled starts.
  - `out/starts.json` sha `39b93dc1…`; `out/data_manifest.json` lists every raw file's sha256.
- **Outputs:** `out/report.json` sha `16f59f57…`; `out/rows.csv` sha `f5183e75…` (local; regenerate with `experiment.py`). Reruns are byte-identical.

## 2. Frozen arms

| | A (production replica) | B (the only challenger) |
|---|---|---|
| K/BF | exp-weighted, half-life 30 d, strictly-prior same-season starts (≥3 starts and ≥40 BF; otherwise season-to-date: 26 / 12 / 54 rows) | identical |
| Expected BF | `project_pitcher_workload`: L14 mean shrunk n0 = 2.62 to 23.1, clamped to [12, 30] (L14 empty: 167 / 130 / 157 rows → 23.1) | identical mean |
| Workload | point mass at E[BF] | E[BF] + re-centred deviations: the pitcher's last ≤10 prior starts (weight 1/(n+5) each) plus the league pool of prior deviations (weight 5/(n+5)), clamped to [1, 45]; mean change from clamping ≤ 1.3e-4 BF |
| Probability | `0.5·league_t + 0.5·Binom(E[BF], k)`, using production's own `_binom_at_least` | `0.5·league_t + 0.5·E_mix[Binom]` |
| League rate | trailing 30 days, starts with BF ≥ 15, strictly prior | identical |

Dispersion is the **only** difference.

## 3. Results

Paired difference B − A, mean per start; negative is better. 95% CIs from 2,000 pitcher-clustered bootstrap resamples; game-clustered CIs agree in direction (`report.json`).

| Treatment | Season | 4+ log loss [CI] | 5+ log loss [CI] | 6+ log loss [CI] |
|---|---|---|---|---|
| **C0 production shrunk (primary)** | 2024 | −0.00077 [−0.00143, −0.00010] | −0.00011 [−0.00060, +0.00041] | +0.00050 [+0.00001, +0.00100] |
| | **2025** | −0.00028 [−0.00089, +0.00035] | +0.00041 [−0.00010, +0.00094] | **+0.00078 [+0.00037, +0.00121]** |
| | 2026 | −0.00075 [−0.00156, −0.00003] | +0.00010 [−0.00049, +0.00072] | **+0.00068 [+0.00021, +0.00119]** |
| C1 production Platt (identical for A and B) | 2025 | **+0.00166 [+0.00004, +0.00327]** | **+0.00094 [+0.00011, +0.00190]** | **+0.00072 [+0.00037, +0.00107]** |
| | 2026 | −0.00104 [−0.00290, +0.00076] | +0.00078 [−0.00002, +0.00171] | **+0.00059 [+0.00025, +0.00093]** |
| C2 Platt fit on 2024, per arm (identical procedure) | 2025 | −0.00001 [−0.00023, +0.00021] | +0.00019 [−0.00003, +0.00042] | **+0.00035 [+0.00007, +0.00065]** |
| | 2026 | +0.00009 [−0.00021, +0.00039] | +0.00008 [−0.00020, +0.00036] | +0.00024 [−0.00010, +0.00059] |

Brier differences have the same signs (`report.json`). Month by month (C0), B helps 4+ in most months and hurts 6+ in 16 of 18 months, so the result is not one bad month.

**Diagnostic: the unshrunk binomial component alone** (not a production quantity; post-hoc, explanatory).
- Here dispersion helps strongly at every threshold and in every season. 2025 log loss: −0.0095 / −0.0073 / −0.0052, with CIs far from 0.
- Interpretation: the single-BF binomial *is* overconfident, but production's 50% league shrink already absorbs that overconfidence. Layering dispersion on top over-corrects the 6+ tail.
- This is a hypothesis for a future, separately preregistered design (dispersion *instead of* shrinkage). It is **not** evidence for this challenger, and it was not tested here.

**Classification at p ≥ 0.5 (C0).** B and A disagree on very few starts:
- 2025: B is correct where A is wrong on 2 / 3 / 17 starts (4+ / 5+ / 6+); A is correct where B is wrong on 0 / 11 / 28.
- 2026: 0 / 4 / 35 vs 0 / 8 / 27.

**Size of the change.**
- Mean probability changes by −0.006 at 4+, −0.004 at 5+ and ≈0 at 6+.
- The change is nearly uniform across E[BF] bins and across terciles of the pitcher's prior BF SD.
- The pitchers with the most volatile workloads gain nothing distinct; 2025 4+ log loss by SD tercile is −0.0004 / −0.0002 / −0.0002.

**Post-hoc only: target-start exit status (never an input).**
- **Early exits** (< 12 outs; 378 starts in 2025, 465 in 2026): realized 4+ rate .291 / .258, while A says .701 / .704 and B .695 / .697.
- **Normal starts:** B is slightly *worse* (+0.0012 log loss).
- **Exits are not pregame-predictable from workload dispersion.** The challenger moves an early-exit start by about 0.6 pp.

## 4. Selection relevance (fixed floors, C0; outcomes of changed decisions)

| 2025 | Over decisions A → B | Added (wins) | Removed (wins) |
|---|---|---|---|
| 4+, p ≥ .60 | 3,424 → 3,408 | 0 | 16 (8) |
| 5+, p ≥ .60 | 1,410 → 1,337 | 0 | 73 (31) |
| 5+, p ≥ .65 | 872 → 766 | 0 | 106 (78) |
| 6+, p ≥ .55 | 348 → 293 | 0 | 55 (32) |

- B **only removes** Overs, at every floor, threshold and season, and the removed sets still win 42–74% of the time (2025 rows above).
- Removing losers is not an equal-volume improvement. **Matched-volume winner lift and ROI: NOT MEASURABLE**, because there are no authentic historical offers or replacement candidates for 2024–25.
- **Market check (descriptive only).** 94 of the 99 settled 2026 public strikeout Top Picks match regular-season rows; these carry authentic captured odds.
  - Realized .447; replica A .691; B .682; market implied .509.
  - Brier: A .3072, B .3026, published .2742, market .2453.
  - The challenger does not approach the market's information.

## 5. Comparison with FC-ACC-004

- FC-ACC-004 shifted the **mean** count (pitch-count slope, short rest): a marginal count-MAE gain, it removed Over decisions, and added no winners.
- FC-ACC-013 changed only the **dispersion** at a fixed mean: it is neutral or harmful on calibrated probability and again only removes Overs.
- Both negative results agree: the workload information available pregame from the starter's own logs does not identify the starts that end early.
- A separately labelled diagnostic, **not tested**: E[BF] runs above actual BF by 0.45 / 0.44 / 0.71 per start (2024/25/26). Shrunk A overstates 4+ by 1.8 / 3.3 pp (2025/26). That is a **mean-level** bias in the 23.1-anchored workload, distinct from dispersion.

## 6. Pitcher outs (assessment only; nothing fitted)

- Production's outs probability (`score_pitcher_outs`) is already the pitcher's own empirical frequency of ≥ t outs (Beta, 6 starts). It is a pitcher-specific workload **distribution**, not a point-mass model, so the FC-ACC-013 mechanism (point mass → distribution) has nothing to replace there.
- The strikeout result also suggests that more own-log dispersion would not reveal exits.
- The outs overconfidence seen on the public record is more plausibly mean, regime or selection error (late-season hooks, market-known context). Testing that needs a different, separately preregistered hypothesis. **No outs model was modified or fitted.**

## 7. Minimum prospective matched-volume validation (if ever revisited)

1. A preregistered all-candidate opportunity manifest per slate (FC-ACC-001 S1 or successor capture), with authentic offers bound to source and time.
2. The champion and challenger selected at identical counts per slate, from the same eligible universe, and compared with a market-only arm.
3. The primary outcome is additional settled winners at matched volume, plus ROI at captured prices.
4. An independently anchored, pregame-sealed decision chain.

None of this is warranted for this challenger, given STOP.

## Reproduce

```bash
python3 research/fc_acc_013/data.py         # network once; raw/ cached locally; manifest -> out/data_manifest.json
python3 research/fc_acc_013/experiment.py   # deterministic; out/report.json, out/rows.csv
python3 research/fc_acc_013/test_fc_acc_013.py
```

Alligator
