# MLB Top Pick selection-overconfidence diagnostic (Mission 12, Workstream D)

Research-only. This workstream only adds new files in this folder. It changes
no model, selector, registry, results, output, docs or workflow file.

**Question:** does the Top Pick *selector* add overconfidence on top of the
probability model's own calibration error?

**Answer under the locked rule: inconclusive.** The published Top Picks are
clearly overconfident, by -11.0pp. The rule could not attribute that gap to
either mechanism. Neither the world-model component nor the
selection-specific component has a 95% interval that excludes zero. The
point estimates lean toward the world model (about two-thirds of the gap).
The descriptive splits show the shortfall wherever the model disagrees with
the market price, whether or not the candidate was selected.

## Provenance

| item | value |
|---|---|
| Pre-registration commit (PREREGISTRATION.md alone) | `c0d87a14885e7bc1dec566cb0e562c297ec04330` |
| Analysis code committed before its first real-data run | `c203ed1c7e0ae36d52173e5129fde0de278ec37c` |
| Data pinned at (`git show`) | `DATA_SHA = 3890c23a15b17fae29407d01489350f97c570d84` (origin/main at start) |
| Bootstrap | cluster = (slate_date, game_pk), B = 2000, seed 20260924, percentile 95% |

Commit order is the only guarantee of pre-registration. PREREGISTRATION.md
lists the outcome information already known to the author before it was
written.

Reproduce (about 4 minutes, no network access):

```
python3 engineering/mlb_selection_overconfidence_20260924/analysis.py
python3 -m unittest engineering/mlb_selection_overconfidence_20260924/test_analysis.py -v
```

## Step 2: verifying the Mission 11 figure

This figure is confirmed exactly, at `docs/history.json` in commit
`8acd197448d549e15a53527bcffeaf8b5a34d2eb` (history generated
2026-09-24T00:59:13Z; same values at `ac9d8ac34b`):

- **Population:** `docs/history.json` `days[].picks`. These are the public,
  first-exposure Top Picks (`recommendation_status == top_pick`), one row per
  canonical id, as recorded in `data/public_top_picks/registry.json` and
  graded from the immutable snapshot.
- **Size and dates:** 489 rows, 31 slate dates, 2026-08-18 to 2026-09-23.
  Dates in the range with no rows: 08-19 to 08-23 and 08-27.
- **Settled:** 455 of 489. The other 34 are 25 ungraded and 9 void.
- **Stated vs realized:** mean stated p = 64.57% on settled rows (64.59% on
  all rows); realized = 53.19%.
- **Market mix:** hits_runs_rbis 265, hits 96, strikeouts 84, pitcher_outs 44.

At DATA_SHA the same population has 491 rows (a 2026-09-24 slate was added)
and 480 settled: 64.57% stated vs 53.54% realized. These 491 ids are exactly
the `public_top_picks` rows in `results/grades_*.json` (PUB below). None are
missing from the registry. The registry has 3 ids that are not yet in the
grades. There are no probability mismatches against the registry snapshots.

## Populations (n predicted / n settled at DATA_SHA)

| population | definition | n pred | n settled | games | mean p | realized | gap (95% CI) |
|---|---|---:|---:|---:|---:|---:|---|
| PUB | published first-exposure Top Picks, 08-18 to 09-24 | 491 | 480 | 224 | 0.646 | 0.535 | **-0.110 [-0.155, -0.064]** |
| DB | final pregame run's displayed board (`grades.picks` with a status), 08-17 to 09-23 | 3167 | 2976 | 235 | 0.395 | 0.378 | -0.017 [-0.037, +0.004] |
| DB_sel | DB `top_pick` | 60 | 59 | 50 | 0.641 | 0.508 | -0.132 [-0.263, +0.006] |
| DB_ref | DB not `top_pick` (primary reference) | 3107 | 2917 | 233 | 0.390 | 0.375 | -0.015 [-0.035, +0.007] |
| DB_ref, p >= 0.60 | | 698 | 674 | 210 | 0.679 | 0.613 | **-0.066 [-0.110, -0.024]** |
| DB_ref_elig | DB_ref with reliability A/B, lineup confirmed, priced | 2528 | 2389 | 223 | 0.366 | 0.367 | +0.001 [-0.021, +0.023] |
| FB_U | frozen full board, p not null (09-20 to 09-24; settled only 09-20 to 09-22) | 2681 | 872 | 18 | 0.320 | 0.304 | -0.016 [-0.043, +0.010] |
| FB_E | FB_U: qc `kept`, lineup confirmed, priced | 1321 | 561 | 18 | 0.331 | 0.316 | -0.016 [-0.059, +0.021] |
| FB_E non-selected, p >= 0.60 | | 338 | 185 | 14 | 0.654 | 0.605 | -0.049 [-0.128, +0.027] |
| FB_S | FB_E `top_pick` | 26 | 13 | 7 | 0.648 | 0.385 | -0.263 [-0.481, -0.047] |
| FB_T10 | `selected_top_pick` (the main top-10 board; not Top Pick status) | 27 | 16 | 10 | 0.532 | 0.500 | -0.032 [-0.284, +0.230] |

"Settled" means graded hit or miss. Void, push and ungraded rows are
excluded from the realized rate. Every candidate in DB and FB was predicted
before its own game started. `bettable_games` drops started games, and every
FB board was sealed before the earliest first pitch; `source_board_sha256`
linkage was verified for every graded date. PUB publication times all fall
before the game start (491 of 491). All populations use model_version
2026.08.15.

## Primary pre-registered result: decomposing the published gap

For each settled published Top Pick, the curve-implied expectation is
`e = p + g_ref(market, band)`. Here `g_ref` is the realized-minus-predicted
gap of *non-selected* DB candidates in the same market and probability band.
All 480 settled PUB rows matched at fallback level 1 or 2: 376 at
(market, band) and 104 at (market, p >= 0.60).

| estimand | PUB vs DB_ref (primary) | DB_sel vs DB_ref (matched, same run and grader) |
|---|---|---|
| n selected settled | 480 | 59 |
| G = stated gap | -0.110 [-0.156, -0.066] | -0.132 [-0.265, +0.008] |
| W = world-model component (H1) | -0.070 [-0.133, +0.001] | **-0.131 [-0.218, -0.050]** |
| S_sel = selection-specific component (H2) | -0.040 [-0.127, +0.035] | -0.001 [-0.157, +0.159] |
| W / G (point) | 0.64 | 0.99 |

**Verdict by the locked decision rule: "inconclusive (neither mechanism
demonstrated)".**
- **H2 not supported.** The primary S_sel CI straddles 0.
- **H1 not supported by the primary criterion.** W's upper bound is +0.001.
- **Neither is shown absent.** Both lower bounds are below -5pp.

**Sensitivities** (they cannot change the verdict):

| sensitivity | W | S_sel |
|---|---|---|
| reference = DB_ref_elig | -0.069 [-0.139, +0.014] | -0.041 [-0.132, +0.040] |
| player-clustered | -0.070 [-0.153, +0.002] | -0.040 [-0.125, +0.051] |
| fair_test only | -0.031 [-0.094, +0.041] | -0.029 [-0.111, +0.048] |
| D2: DB_ref excluding the 24 rows that are also PUB ids | **-0.077 [-0.139, -0.005]** | -0.034 [-0.121, +0.045] |
| D3: DB rows predicted at or after the scheduled start removed | -0.075 [-0.138, +0.001] | -0.036 [-0.123, +0.042] |

W sits right at the zero boundary: removing the overlap rows (D2) pushes its
CI below 0. S_sel never approaches significance in any variant. On the
fair_test-only view, G shrinks to -0.060 [-0.110, -0.009]. That view is
biased, though: `fair_test=False` marks pitchers with fewer than 4 IP and
batters with 2 or fewer PA, and those failures are outcome-dependent and
relevant to the model.

**Date stability** (split at 09-08, descriptive): the attribution is not
stable across halves.
- Early half: W = -0.017 and S_sel = -0.090 [-0.212, +0.024].
- Late half: W = -0.109 [-0.191, -0.016] and S_sel = -0.004.
- G itself is stable: -0.107 early and -0.113 late.

## Descriptive findings (pre-registered splits, interpretation is post hoc)

**1. The shortfall follows model-vs-market disagreement, whether or not the
candidate was selected.** The `market_edge` split:

| edge band | PUB gap | DB_ref (p >= 0.60) gap |
|---|---|---|
| < 0.05 | -0.083 [-0.153, -0.013] (n=237) | -0.017 [-0.065, +0.035] (n=418) |
| 0.05 to 0.10 | -0.098 [-0.171, -0.016] (n=167) | -0.048 [-0.199, +0.096] (n=68) |
| >= 0.10 | **-0.224 [-0.337, -0.109]** (n=76) | **-0.195 [-0.299, -0.094]** (n=121) |

Non-selected candidates with p >= 0.60 and a large disagreement with the
market hit 49.6% against a stated 69.1%. That is as bad as the published
picks with the same disagreement (42.1% vs 64.5%). The price-band split shows
the same pattern:

| price band | PUB gap | DB_ref (p >= 0.60) gap |
|---|---|---|
| >= -110 | -0.209 | -0.250 |
| -110 to -150 | -0.142 | -0.097 |
| -150 to -200 | -0.064 | -0.093 |
| <= -200 | -0.008 | -0.002 |

Near even money, where the model says 60 to 70%, is where the model fails
for both populations. The Top Pick value gate requires a positive edge, so
it concentrates published picks in exactly that region.

An exploratory re-run with reference cells conditioned on edge band (D4)
does not change the picture:
- (edge x band): S_sel -0.044 [-0.123, +0.033].
- (market x edge x band): W -0.070 [-0.126, -0.008], S_sel -0.040 [-0.116, +0.029].

**2. Market prices were closer to the outcomes than the model.** Realized
minus the *raw, vig-inclusive* `market_implied` is:
- PUB: -0.045 [-0.089, +0.003]
- DB_sel: +0.002
- DB_ref (p >= 0.60): -0.041
- FB_E: -0.033

That is roughly the size expected from the vig alone. The model's own gap on
PUB is -0.110.

**3. Market split for PUB (n = 480):**
- pitcher_outs: -0.213 [-0.361, -0.065], n=42
- strikeouts: -0.136 [-0.243, -0.023], n=82
- hits_runs_rbis: -0.103 [-0.162, -0.041], n=262
- hits: -0.061 [-0.162, +0.030], n=94

Non-selected DB candidates at p >= 0.60 show the same ordering:
- combined_strikeouts: -0.231
- pitcher_outs: -0.125
- strikeouts: -0.112
- hits_runs_rbis: -0.019
- hits: -0.001

**4. Reliability by band.** Non-selected DB_ref is well calibrated below
p = 0.60: every band's gap is within about 2pp, and every CI contains 0. At
p >= 0.60 it is overconfident: [0.60, 0.65) has a gap of -0.113
[-0.196, -0.033]. PUB is overconfident in every band that holds more than 2
rows. That looks like a calibration defect in the model's high-p region,
not a property of the selected set alone.

## H3: population differences

- **Probability drift is negligible.** The same id is published and then
  appears in the final run in 80 cases: p_pub - p_final = +0.0007
  [-0.0008, +0.0024]. For published vs frozen on 09-20 to 09-23, n=40:
  +0.0024 [+0.0001, +0.0053]. The published number is not an inflated
  snapshot.
- **Status churn is material.**
  - Of the 80 published ids present in the final run, 23 had been downgraded
    by then (21 lean, 2 neutral).
  - Of the 40 published ids present in the frozen board, 14 were not
    `top_pick` at freeze (10 lean, 1 neutral, 3 with no status).
  - First exposure takes the first run in which a candidate crossed the Top
    Pick threshold, across several runs a day. The frozen and final-run
    selections do not capture that "multiple chances" selection.
- **The populations cover different games.**
  - 212 of 475 PUB rows (on dates with a DB board) are from games with no row
    on the final-run board. Mostly these are earlier games that had already
    started.
  - Another 183 are from games in the final run but no longer on the
    displayed board.
  - Exploratory gaps by final-run status:
    - game not in the final run: -0.141 [-0.210, -0.073]
    - still top_pick: -0.123
    - downgraded: -0.081
    - absent from the board: -0.062
  - The earlier-game half of PUB has no reference rows from the same time of
    day. This is an untestable confound for W.
- **The frozen board is too small to support conclusions.** FB_S is 26
  records with 13 settled over 7 games; PR #188 had 10. Its gap of -0.263
  vs the eligible non-selected p >= 0.60 gap of -0.049 is directionally a
  winner's curse. It is not interpretable at this n.
- **The 09-23 graded board file has all 668 records `ungraded`.** It was
  graded at 01:36Z, before the games were final, and has not been regraded
  at DATA_SHA. This is an evidence-pipeline gap.

## Missing outcomes

- **PUB:** 9 void and 2 ungraded. The realized-rate bounds are 52.3% to 54.6%,
  which does not change anything.
- **DB_sel:** 1 ungraded.
- **FB_S:** 13 of 26 ungraded, so the bounds run from 19% to 69% and are
  uninformative.

## Clustering

Game-cluster bootstrap SEs are only about 1.03 to 1.19 times the iid SEs for
PUB and DB. Player clustering gives similar intervals.

## Deviations from the pre-registration

These are also recorded in `report.json` under `deviations`.

- **D1.** Changed `git ls-tree` to `--full-tree`. The first run crashed
  before it produced any number.
- **D2 and D3.** Added sensitivities (PUB-id overlap; information-cutoff
  check).
- **D4.** Added a post-hoc exploratory edge-conditioned decomposition and a
  realized-minus-market comparison.
- **D5.** The rank split uses fixed cutoffs, not terciles. It is descriptive,
  and it has too few selected rows to read.
- **D6.** The fair_test caveat, noted above.

## Limitations

- **DB is not the full universe.** It is the surfaced board, so DB_ref may
  carry its own selection optimism. That biases S_sel toward 0 and W toward
  negative. FB is the true universe, but it covers 3 graded dates and 18
  games.
- **PUB and DB cover different games** (time of day) and use different grading
  paths (`grade_public_pick` vs `grade_pick`).
- **Reference cells are small.** For example, (pitcher_outs, [0.70, 0.75))
  has n_ref = 30, and (hits_runs_rbis, [0.65, 0.70)) has 32. `g_ref` is noisy,
  which is why the W interval is wide.
- **Raw market_implied includes vig.** No de-vigged fair price is bound on PUB
  rows, and none was manufactured.
- **One model version and roughly 5 weeks.** The results say nothing about
  other regimes.
- **Multiple descriptive splits were examined.** The edge and price findings
  are hypothesis-generating.

## Proposed next falsifiable experiment (one)

The evidence leans toward the world model and points at no selector-specific
excess. The next experiment therefore targets the **world model's
calibration where it disagrees with the market**. It does not tune the
selector.

**Hypothesis.** A market-anchored probability,
`p' = logistic(a + b*logit(p_model) + c*logit(p_market_devig))`, improves
calibration and log loss on the eligible population. It also shrinks the
stated-minus-realized gap of the resulting equal-volume picks.
- a, b and c are fitted per market family on candidates with a slate date of
  2026-09-23 or earlier: DB and FB rows of every status, predictions and
  outcomes only, with no selection information. They are then **frozen**.
- `p_market_devig` is the two-sided de-vigged price where both sides are
  bound in the artifacts. Otherwise the row is excluded, and never imputed.

**Evaluation (forward, prospective).**
- Population: the frozen boards (`board_freeze_graded`) from 2026-09-25
  onward, on FB_E (the eligible population).
- Champion vs challenger on the same candidates: Brier score and log loss,
  paired by candidate, with a game-cluster bootstrap.
- Equal usable volume: each slate, take the champion's Top Pick count N_d.
  The challenger takes the top N_d eligible candidates by its own
  `p' - implied`, under the same p >= 0.60, reliability, lineup and price
  gates. Compare realized hit rate and realized minus stated.

**Falsification.** The challenger fails if either of these holds:
- the paired log-loss improvement's 95% CI includes 0 once there are at least
  1,500 settled FB_E candidates, and at least 300 of them have p >= 0.60;
- its equal-volume picks' gap is not closer to 0 than the champion's across
  at least 60 settled equal-volume picks.

**Prerequisite (evidence pipeline, not a model change).** Frozen boards must
be regraded after the games go final. The 09-23 file is 100% `ungraded`.

## Files

- `PREREGISTRATION.md`: the locked design (first commit).
- `analysis.py`: loads the pinned artifacts via `git show`, computes every
  estimand and writes `report.json`. Its estimand functions are pure.
- `test_analysis.py`: 13 synthetic known-answer tests covering bands, the
  fallback hierarchy, the decomposition, planted selection-only and
  world-model-only effects, bootstrap determinism and the decision rule.
- `report.json`: the full output, including every table above.

Alligator
