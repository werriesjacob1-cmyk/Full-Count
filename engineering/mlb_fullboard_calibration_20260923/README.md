# MLB full-board calibration / winner's-curse investigation (Mission 9, Workstream E)

Real, research-only analysis of the frozen-full-board evidence
`board_freeze.py` (PR #132/#138/#139) and `board_freeze_grader.py` /
`grade_board_freeze.py` (PR #138/#163) have already been capturing in
production since 2026-09-20. **This workstream builds no new snapshot or
freeze infrastructure** -- it only reads the real, already-committed
`output/board_freeze_<date>.json` and `output/board_freeze_graded_<date>.json`
files. Draft PR #187 added regression tests for that existing wiring; it is
not a new capability and is not duplicated here.

## What this answers

The question PR #131 left open: nobody had ever measured calibration on the
population that is actually subject to selection (the full frozen candidate
pool vs. the argmax-selected/published Top Pick subset), because no frozen
pregame snapshot existed before `board_freeze.py`. It now does, and this is
the first analysis run against the real accumulated boards.

## How to reproduce

```bash
cd engineering/mlb_fullboard_calibration_20260923
python3 analyze_fullboard_calibration.py
python3 -m unittest test_calibration_lib.py -v
```

No network access is used or required. The script only reads
`output/board_freeze_*.json` files already committed to this repository and
writes `report.json` in this directory. Re-running it later, after more real
games are graded, will pick up more real data automatically -- see
"A live example of this happening" below.

## Real data verified (Step 1)

As of this run, the repository contains:

| Date | Board sealed at | Games scheduled | Candidates (record_count) | Graded file? | Grading last ran at |
|---|---|---|---|---|---|
| 2026-09-20 | 2026-09-20T22:33:02Z | 1 | 72 | yes | 2026-09-21T23:11:53Z |
| 2026-09-21 | 2026-09-21T20:54:56Z | 3 | 186 | yes | 2026-09-22T22:53:37Z |
| 2026-09-22 | 2026-09-22T20:15:14Z | 15 | 985 | yes | 2026-09-23T19:59:09Z |
| 2026-09-23 | (sealed today) | 16 | 1027 | **no** | -- games not yet played |

Only the first three dates have both a sealed board and a graded file, so
only those three are used as the analysis population. 2026-09-23 is real and
committed but is correctly excluded from every statistic in `report.json`
(it appears only under `dates_with_board_only_no_graded_file_yet`).

Every `board_freeze_<date>.json`'s `board_sha256` matches the corresponding
`board_freeze_graded_<date>.json`'s `source_board_sha256` for all three
paired dates (`board_to_graded_sha256_linkage_verified` in `report.json`) --
each graded file really was graded from exactly its own sealed board, not
independently re-derived data.

### Real schema (read directly from the files, not assumed)

`board_freeze_<date>.json` top level: `schema_version`, `board_freeze_version`,
`sport`, `evidence_class` (`PROSPECTIVE_FULL_BOARD`), `date`,
`board_generated_at`, `sealed_at`, `game_start_times`, `record_count`,
`records`, `provenance`, `research_only` (`True`), `public_eligible`
(`False`), `board_sha256`.

Each record: `candidate_id`, `game_pk`, `player_id`/`player_name`,
`stat`/`prop_label`/`line`/`market_side`, `eligibility.qc_status` (real
observed values: `kept`, `qc_rejected`, `lineup_assumed_holdout`),
`eligibility.rejection_reason`, `prediction.hit_probability` (0-1 scale;
can be `null`, observed for a couple of `stolen_base` candidates lacking a
modeled rate), `prediction.reliability`/`sample_n`, `market.market_odds`/
`market_implied`/`market_edge`, `selector.recommendation_status` (real
observed values: `top_pick`, `lean`, `value`, `neutral`, and `None` for
`lineup_assumed_holdout` candidates the selector does not classify),
`selector.selected_top_pick` (bool -- can be `True` for more than one
candidate per day because it is evaluated per market category, not
board-wide), `provenance.{model_version,selection_policy_version,
calibration_version,feature_version,git_sha}`.

`board_freeze_graded_<date>.json` adds, on top of every frozen field:
`grade` (real observed values: `hit`, `miss`, `ungraded` -- `ungraded` means
the underlying game was not yet final when the grader ran, not a
prediction failure), `actual`/`actual_stat`, `fair_test` (bool or `None` --
whether the candidate genuinely had a fair opportunity, e.g. wasn't pulled
early; the same field `results/grade_results.py` already uses for its own
production `fair_test_hit_rate`), `opportunity`, `game_innings`,
`shortened_game`, plus `source_board_sha256` and `graded_at` at the top
level.

### PR #131's caveat, re-verified before use

PR #131 (open, unmerged) found that a cheaper real-data join --
`results/grades_*.json`'s `picks` vs. `public_top_picks` -- only matched 16%
of records because `picks` is regenerated at grading time, not a frozen
pregame snapshot, and concluded no trustworthy frozen-full-board population
existed yet. `board_freeze_<date>.json` is exactly the artifact PR #131
called for: it is written once, at generation time, before any outcome is
known, and sealed with a SHA-256 checked against every graded file used
here. That specific population-definition gap is closed for the analysis in
this report. It does **not** mean every population question is closed --
see "Known population blockers" below for one that still is.

## Population definition (Step 3 setup)

The core analysis population is every real candidate, across the three
paired dates, with:

- `grade` in `{hit, miss}` (the underlying game was final when graded), and
- `fair_test is True` (the candidate had a genuine opportunity), and
- a non-null `prediction.hit_probability`.

`ungraded` and `fair_test in {False, None}` candidates are **excluded**, not
folded in as misses. As of this run that leaves **n = 790** real candidates
across **18 distinct real games** (of 19 real games scheduled across the
three dates -- one game had zero settled candidates at grading time). See
`report.json`'s `population_definition.per_date_summary` for the exact
per-date grade/fair_test/qc_status breakdowns this run read.

### A live example of population instability, observed during this investigation

While building this analysis, `output/board_freeze_graded_2026-09-22.json`
was re-graded in place mid-session: an earlier read of that file (grading
timestamp `2026-09-23T01:40:17Z`) showed only 101 of 985 candidates graded
(13 of 15 games still in progress); the version now committed at HEAD
(`d9c3c731d7884a1b1cc61d0a00fd1de9ef59ebf0`, regraded at
`2026-09-23T19:59:09Z`) has 684 of 985 candidates graded. This is exactly
the kind of population instability Full Count's engineering rules require
staying honest about -- this script reads whatever is on disk at run time
and reports it plainly (see `honest_limitations` in `report.json`, which
is generated dynamically from the real timestamps for this reason, not
hardcoded).

## Findings (Step 3 core analysis)

All numbers below are point estimates with real-data-derived, game-clustered
bootstrap 95% confidence intervals (cluster = `game_pk`, since candidates
from the same real game are not independent draws). Full detail in
`report.json`.

### 1. Full-board calibration curve

With n=790 the real sample supports 5 buckets (roughly 158 candidates each)
comfortably, not true deciles (`report.json` explains why in
`calibration_curve.requested_n_buckets` vs. real n). Every one of the 5
buckets' realized-hit-rate cluster-bootstrap CI contains that bucket's own
mean predicted probability, and the overall (realized - predicted) gap is
+0.0095 with a 95% CI of [-0.018, +0.033] -- **consistent with reasonable
calibration across the whole frozen candidate pool** at this sample size.
This is a real, currently-favorable finding, but n=790 over only 18 real
games from 3 calendar dates is still a young sample; a single volatile
market or day-of-week effect could move it.

### 2. Winner's-curse test: selected Top Pick vs. everything else

Restricting to `selector.recommendation_status == 'top_pick'` (the real
production Top Pick classification):

| Group | n | n games | mean predicted | realized hit rate | gap (realized - predicted) | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| Top Pick | 10 | 4 | 0.6553 | 0.500 | -0.1553 | [-0.337, +0.109] |
| Everything else | 780 | 18 | 0.3076 | 0.3192 | +0.0116 | [-0.017, +0.035] |

The point estimate is directionally consistent with a winner's-curse story
(Top Picks realize a lower hit rate than their own predicted probability,
while the rest of the board does not), and the gap-difference point estimate
is -0.167. **The 95% cluster-bootstrap CI for that difference is
[-0.392, +0.248] -- it straddles zero.** With only 10 Top Pick candidates
across 4 real games, this is honestly **inconclusive, not a demonstrated
winner's-curse effect and not a demonstrated absence of one.** See "Locked
next hypothesis" below for the predeclared n this needs before it can be
run with real power.

### 3. QC-status breakdown

| qc_status | n | n games | mean predicted | realized hit rate | gap | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| kept | 552 | 18 | 0.3204 | 0.3207 | +0.0002 | [-0.040, +0.031] |
| lineup_assumed_holdout | 238 | 8 | 0.2925 | 0.3235 | +0.0311 | [-0.006, +0.079] |
| qc_rejected | -- | -- | -- | -- | -- | **no real graded, fair-test data exists** |

**Concrete blocker, not silently worked around:** all 64 real `qc_rejected`
candidates across the paired dates come from 2026-09-22; of those, only 2
have reached a real `hit`/`miss` grade so far, and both of those 2 have
`fair_test == False` (no genuine opportunity). Zero real `qc_rejected`
candidates currently satisfy this report's population bar. A
QC-rejected-vs-kept calibration comparison is not answerable from real data
today -- not because the field is missing, but because the real intersection
of "QC-rejected" and "genuinely graded with a fair opportunity" is currently
empty. Re-running this script once more days accumulate will pick this up
automatically the moment it becomes real.

`kept` and `lineup_assumed_holdout` are both reasonably calibrated at this
n, with `lineup_assumed_holdout` showing a small positive (favorable) gap
whose CI still touches zero -- not distinguishable from `kept` yet.

### 4. Market-specific breakdown

All 8 real markets with any fair-test-graded evidence clear the n>=15
threshold used here for a per-market gap/CI. Most (hard_hit_105, hits,
hits_runs_rbis, stolen_base, strikeouts, pitcher_outs) show small gaps whose
CIs comfortably contain zero. Two are worth naming honestly without
overclaiming:

- `nrfi_combined`: n=16 across 16 games, mean predicted 0.523, realized
  0.750, gap +0.227, 95% CI [+0.032, +0.415] -- the only market whose CI
  currently excludes zero. At n=16 this is a single real day's worth of
  combined-NRFI candidates and should be treated as a lead to watch, not a
  finding; it would need to persist across more real days before it means
  anything about the NRFI model specifically.
- `moonshot_420`: n=29 across 13 games, mean predicted 0.023, realized
  0.103, gap +0.080, 95% CI [-0.024, +0.242] -- directionally similar but
  the CI still includes zero.

See `report.json`'s `market_breakdown` for every market's exact numbers,
including the ones below the n>=15 bar (none currently, but the threshold
and the reporting behavior below it are implemented and tested for when a
new low-volume market appears in a future run).

### 5. Clustering treatment

Every gap/CI above resamples whole real games (`game_pk`), not individual
candidates, because multiple candidates from the same real game share that
game's actual outcome-generating context (weather, umpire, bullpen usage,
park). `calibration_lib.cluster_bootstrap_ci` and
`cluster_bootstrap_group_gap_diff_ci` implement this and refuse to report a
false-precision interval when fewer than 2 real clusters exist for a group
(see their docstrings and `test_calibration_lib.py`).

## Known population blockers (Step 5 disclosure)

- **`qc_rejected` calibration is currently unanswerable** (see above) --
  this is a real, concrete, and currently-unavoidable gap in the *graded*
  data, not a missing capability in `board_freeze.py`/`board_freeze_grader.py`.
  No new infrastructure was built to work around it; it is reported as a
  blocker for a future run to pick up once more `qc_rejected` candidates
  clear both the `hit`/`miss` and `fair_test` gates.

No other blocker required building anything new. The freeze/grade
instrumentation from PR #132/#138/#139/#163 already provides everything
Steps 1-4 of this workstream's brief needed.

## Locked next falsifiable hypothesis (Step 4 deliverable)

**Hypothesis:** the MLB probability pipeline (`attach_hit_probabilities`'
empirical/modeled blend feeding `recommendation.py`'s Top Pick gate) is
subject to a real, measurable winner's-curse effect -- `recommendation_status
== 'top_pick'` candidates realize a hit rate below their own mean predicted
probability by a larger margin than the rest of the frozen board does,
because argmax-style selection over many correlated candidate probabilities
preferentially surfaces candidates whose probability estimate is noisiest on
the high side.

- **Population:** every real `board_freeze_graded_<date>.json` record with
  `grade` in `{hit, miss}`, `fair_test == True`, and a non-null
  `prediction.hit_probability`, from dates with both a sealed board and a
  graded file (the same population this report used), accumulated forward
  from 2026-09-20.
- **Comparison:** `calibration_lib.cluster_bootstrap_group_gap_diff_ci`
  applied to `(realized_hit_rate - mean_predicted)` for
  `recommendation_status == 'top_pick'` minus that same gap for every other
  real graded, fair-test candidate, with real games as the resampling
  cluster -- exactly the computation `build_winners_curse_test()` in
  `analyze_fullboard_calibration.py` already runs.
- **Falsification rule:** the hypothesis is REJECTED if the cluster-bootstrap
  95% CI for the gap difference (top_pick minus non-top_pick) includes 0, or
  is centered at/above 0. It is SUPPORTED only if that CI excludes 0 on the
  negative side **and** the real Top Pick sample has reached a predeclared
  minimum of at least 30 real fair-test-graded Top Pick candidates spanning
  at least 10 distinct real games. As of this run, n=10 across 4 real games
  -- explicitly too small to run this test with any power. This is recorded
  for a **future** workstream to execute once more real graded days
  accumulate; it is not resolved by this report.
- **Secondary falsification target (full-board calibration):** once n is
  large enough to bucket into true deciles (roughly 300-500+ fair-test-graded
  candidates per bucket), if any bucket's realized-hit-rate cluster-bootstrap
  CI excludes that bucket's own mean predicted probability, the probability
  pipeline is measurably miscalibrated in that range and recalibration (not
  merely a selector-policy change) is the falsified target.

## Files

- `calibration_lib.py` -- reusable, dependency-free bucketing and
  cluster-bootstrap functions (no repository I/O).
- `analyze_fullboard_calibration.py` -- reads the real committed
  `output/board_freeze*.json` files and writes `report.json`. No network
  access. Re-run any time; it reflects whatever is on disk.
- `test_calibration_lib.py` -- 16 unit tests for `calibration_lib.py` against
  synthetic fixtures (perfectly-calibrated bucket, planted winner's-curse
  gap, cluster-count refusal behavior, determinism under a fixed seed).
- `report.json` -- the real output of the last `analyze_fullboard_calibration.py`
  run against the real committed data.

## What this workstream explicitly did not do

No change to `board_freeze.py`, `board_freeze_grader.py`,
`grade_board_freeze.py`, `generate_picks.py`, `mlb_daily.py`,
`mlb_sources.py`, any workflow YAML, or PR #187's own test file. No new
snapshot/freeze/grading infrastructure. No model, calibration, or selector
change. No merge, deployment, public pick, or grading activation. This is a
research-only, read-only analysis over real, already-committed evidence.
