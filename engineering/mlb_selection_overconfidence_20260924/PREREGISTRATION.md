# Pre-registration: MLB Top Pick selection-overconfidence diagnostic

Mission 12, Workstream D. Branch `claude/mlb-selection-overconfidence-20260924`.
Written 2026-09-24. Research-only. No model, selector, registry, results,
output or docs file is modified by this workstream.

## What "pre-registered" means here (honest limit)

Every input artifact already exists in the repository. This document is
committed alone, before `analysis.py` exists and before any outcome statistic
listed below is computed. Commit order is the only guarantee; nothing is
cryptographically blinded. The following outcome information was already
known to the author, or was seen during schema discovery, before this was
written:

1. Published Top-Pick aggregates from PR #128 (64.6% stated vs 53.6%
   realized, n=371), PR #192 (holdout: +12.2pp, n=69) and the Mission 11
   figure (about 64.6% stated vs about 53.2% hit, 455 settled of 489).
2. PR #188's board-freeze result: 10 fair-test-graded `top_pick` records,
   realized minus predicted -0.155, and a top-pick-minus-rest CI that
   crosses zero. The full-board gap was about +0.01.
3. During schema inspection the author printed two aggregates by accident:
   `results/grades_2026-09-22.json` `public_top_pick_counts` (14 hits, 10
   misses on that date), and the pooled grade counts over every
   `results/grades_*.json` `picks` row that carries a
   `recommendation_status`: 1125 hit, 1851 miss, 191 ungraded. That second
   count is not split by status, market or band. No hit/miss count was
   viewed by selection status, market, probability band or price band.

## Input data (pinned)

All inputs are read through `git show <DATA_SHA>:<path>`, where
`DATA_SHA = 3890c23a15b17fae29407d01489350f97c570d84` (origin/main at the
start of this workstream). Later regrading on `main` cannot change this
analysis. A rerun on a newer SHA is a new analysis.

Files read:
- `results/grades_<date>.json`: the `picks` field (the final pregame
  displayed board) and the `public_top_picks` field (graded first exposures).
- `data/public_top_picks/registry.json` (immutable first-exposure registry).
- `docs/history.json`. It is used only to verify the Mission 11 figure,
  including at earlier commits through `git log`/`git show`.
- `output/board_freeze_<date>.json` and
  `output/board_freeze_graded_<date>.json`.

## Hypotheses

- **H1, world-model error.** Candidates that were *not* selected, at a given
  market and predicted-probability band, hit less often than predicted.
  Estimand: W, defined below.
- **H2, selection-induced optimism.** Selected (`top_pick`) candidates fall
  short of their predictions by *more* than non-selected candidates in the
  same market and probability band. Estimand: S_sel, defined below.
- **H3, population differences.** Stated-vs-realized gaps, and the stated
  probabilities themselves, differ across these populations: the full frozen
  universe, the operationally eligible frozen population, the frozen
  selected set, the final-run displayed board, and the immutable published
  first-exposure population. They also differ for the same candidate id
  between publication time and later snapshots.

## Populations (cohorts)

The market key is `stat` (for example `hits`, `hits_runs_rbis`,
`strikeouts`, `pitcher_outs`). The slate date is the date in the file name.

**PUB: published first-exposure Top Picks.** Every row of
`public_top_picks` in every `results/grades_<date>.json`, one row per
canonical `id`. Predicted value = the row's `hit_probability`, the snapshot
at first publication. Price = the row's `market_odds`. The sample runs over
every slate date present at DATA_SHA. Rows are cross-checked against the
registry keys, and any mismatch is reported.

**DB: final pregame displayed board.** Every row of `picks` in
`results/grades_<date>.json` that has a non-null `recommendation_status`
(slate dates 2026-08-17 onward). Every other row is excluded, and so is any
row with a null `hit_probability`. These rows come from the last
`generate_picks.py` run of the day, which only scores games that have not
started (`bettable_games`). Each row is therefore a pregame prediction, but
the population covers only games still unstarted at that run. DB is itself a
surfaced subset: the top-10 board, the per-market category boards and the
moonshots. It is not the full candidate universe.
- DB_sel: `recommendation_status == "top_pick"`.
- DB_ref: every other DB row. This is the primary reference population.
- DB_ref_elig: DB_ref rows with `reliability` in {A, B}, a falsy
  `lineup_assumed` and a non-null `market_odds`. These are the rows that were
  operationally eligible for Top Pick consideration and were not selected.
  This is the sensitivity reference.

**FB: frozen full board.** Every record of `output/board_freeze_graded_<date>.json`
whose `source_board_sha256` equals the `board_sha256` of
`output/board_freeze_<date>.json`. Dates without a graded file, or whose
graded records are all ungraded, contribute to the n-predicted counts only.
- FB_U (universe): records with non-null `prediction.hit_probability`.
- FB_E (eligible): FB_U records with `eligibility.qc_status == "kept"`,
  falsy `eligibility.lineup_assumed` and non-null `market.market_odds`. Only
  the artifact's own flags are used.
- FB_S: FB_E records with `selector.recommendation_status == "top_pick"`.
- FB_T10: records with `selector.selected_top_pick == True`. This is the
  main top-10 board surface. It is not Top Pick status (AGENTS.md rule 12),
  and it is reported only descriptively.

FB is descriptive only (see "Validation"). PR #188 already ran the
top-pick-vs-rest test on it, and that test is not repeated as a
confirmatory test here.

## Outcome definition

y = 1 if `grade == "hit"`, y = 0 if `grade == "miss"`. Every other value
(`void`, `push`, `ungraded`, missing, or anything else) is **not settled**.
Those rows are excluded from realized rates and counted in n_predicted.
"Settled" = y defined.

Primary: every settled row. Sensitivity: settled rows with
`fair_test is True`, for every population that carries the field.

Missing-outcome bounds: for each population, the realized rate is also
reported with all unsettled rows set to miss and with all set to hit, along
with the mean predicted p of settled rows vs unsettled rows.

## Probability bands

Band edges on predicted p: [0, 0.30), [0.30, 0.45), [0.45, 0.60),
[0.60, 0.65), [0.65, 0.70), [0.70, 0.75), [0.75, 1.00].
Selected rows are all at p >= 0.60 by policy (`TOP_PICK_MIN_PROB`), so only
the upper four bands matter for matching.

## Estimands

For any set A: `gap(A) = mean(y - p)` over settled rows. Negative means
overconfident.

**Reference cell gap.** For a cell c = (market, band):
`g_ref(c) = mean(y - p)` over the settled reference rows in c.

**Cell assignment with pre-set fallback.** For each selected row i:
1. use (market, band) if it has >= 15 settled reference rows;
2. otherwise use (market, p >= 0.60 pooled) if it has >= 15;
3. otherwise use (all markets, band) if it has >= 15;
4. otherwise the row is "unmatched". It is excluded from S_sel and W and
   counted.

**Curve-implied expectation for a selected row:** `e_i = p_i + g_ref(c_i)`.

**Decomposition over the matched, settled selected rows M:**
- stated gap `G = mean(y_i - p_i)`
- world-model component `W = mean(g_ref(c_i))` (H1 applied to the selected
  population's mix of markets and bands)
- selection component `S_sel = mean(y_i - e_i) = G - W` (H2)

Primary H2/H1 estimand: selected set = PUB, reference = DB_ref.
Corroborating matched estimand: selected set = DB_sel, reference = DB_ref.
Here selected and non-selected rows come from the same run, the same
artifact and the same grader. This is the requested "selected minus matched
non-selected at an equal market × band", with a within-cell adjustment for p.
Sensitivities: reference = DB_ref_elig; fair_test-only outcomes; player
clustering.

**Reference-population world-model check (H1 directly):** gap(DB_ref ∩ p >= 0.60)
and gap(FB_E non-selected ∩ p >= 0.60), plus the overall gaps.

**H3:**
1. gap, n_predicted, n_settled and mean p for PUB, DB, DB_sel, DB_ref,
   FB_U, FB_E, FB_S and FB_T10;
2. for ids in both PUB and DB on the same slate date: `p_pub - p_final`,
   and the DB status of those ids (still `top_pick`, downgraded, or absent
   from the final run);
3. for ids in both PUB and FB on the same date: `p_pub - p_frozen`, and the
   frozen status;
4. exploratory: PUB gap split by final-run status (still top pick /
   downgraded / absent).

**Descriptive splits** for each population where the fields exist:
reliability by band (predicted vs realized per band); market split;
price-band split, which uses real `market_odds` bound in the artifact only
(American odds bands: <= -200, (-200, -150], (-150, -110], > -110) and
reports realized vs predicted vs `market_implied`; selection-intensity split
by `market_edge` (< 0.05, [0.05, 0.10), >= 0.10); a rank split (DB `rank`
tercile; FB `final_rank`); and date stability (the split described below).

## Uncertainty

Cluster bootstrap, percentile 95% intervals, **B = 2000** resamples,
**seed = 20260924** (`random.Random(seed)`).
- Primary cluster = (slate_date, game_pk). For estimands that combine a
  selected set and a reference set, clusters are resampled **jointly** from
  the union of both sets, and g_ref is re-estimated inside every resample.
  The cell assignment is fixed at the full-sample assignment.
- Sensitivity cluster = player_id. Rows without one use the game-level id,
  or the candidate id if no game-level id exists either.
- An estimand with fewer than 2 clusters is reported as "insufficient", with
  no interval.

## Validation split

The sample is too small for a meaningful holdout of the H2 estimands:
DB_sel has about 60 rows in total, and FB_S has about 10 settled rows.
Therefore:
- the primary estimands use every settled slate date at DATA_SHA, and they
  are confirmatory only in the sense that their definitions are locked here;
- date stability: rows are split at slate date 2026-09-08 (dates <= 09-07 as
  the early half, >= 09-08 as the late half), and S_sel and W are reported
  for each half. This is descriptive and is not a holdout test;
- FB results are descriptive only.

## Decision rule (locked)

Minimum data: PUB matched settled >= 200 and DB_sel settled >= 30. If either
minimum is not met, the verdict is "inconclusive (insufficient n)". An
additional requirement: >= 70% of PUB settled rows must be matched at fallback
level 1 or 2. Otherwise H2 cannot be declared supported, because the curve
would rest mostly on the pooled-market fallback.

- **H2 supported (selection adds overconfidence):** the 95% CI of the
  primary S_sel (PUB vs DB_ref, game clusters) lies entirely below 0 **and**
  the DB_sel matched S_sel point estimate is < 0. The support is called
  "strong" if the DB_sel S_sel CI also lies entirely below 0.
- **H1 supported (world-model error at the selected mix):** the 95% CI of the
  primary W lies entirely below 0.
- Both can be supported. The share of G attributable to each is then
  reported as a point estimate with a CI.
- **H2 over H1** is claimed only if H2 is supported **and** W's CI includes 0
  or lies above it.
- **H1 over H2** is claimed only if H1 is supported **and** S_sel's CI
  includes 0 or lies above it.
- A mechanism whose interval straddles 0 is "not demonstrated",
  distinguished from "demonstrated absent". "Demonstrated absent" would
  require the CI to exclude effects more negative than -5pp; it is reported
  if it happens.
- Sensitivities (DB_ref_elig reference, fair_test-only outcomes, player
  clusters) cannot create a verdict. They are reported, and any change of
  sign or CI status is flagged as fragility.

## Known threats (declared before results)

- DB_ref is a surfaced subset, since category boards pick the top rows by p
  within a market. It may itself carry selection optimism, which would bias
  S_sel toward 0 (conservative for H2) and W toward negative.
- PUB covers every game of the day, while DB covers only the games still
  unstarted at the final run. That is a time-of-day and slate-composition
  difference.
- PUB first exposure is the first run in which a candidate reached
  `top_pick` across several runs per day. The number of chances to cross the
  threshold is a selection mechanism that the matched DB_sel comparison does
  not capture. H3 item 2 is meant to expose it.
- Grading paths differ. PUB is graded by `grade_public_pick` from the
  first-exposure snapshot; DB is graded by `grade_pick`. Void handling may
  differ between the two.
- No tuning of any selector or threshold is done on this sample.

Alligator
