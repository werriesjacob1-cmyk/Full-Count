# MLB Mission 10: Top Pick calibration -- pre-registered holdout replication

Workstream ID: `MLB-MISSION10-ACCURACY`
Locked: 2026-09-24, before any aggregate outcome (hit/miss vs. predicted
probability) statistic in the target population was computed. This file is
committed as the first commit on this branch so git history proves the
design *text* existed before the aggregate result was computed by
`run_holdout_replication.py`.

**Honest limit on "pre-registered," disclosed rather than overclaimed**:
the underlying `results/grades_2026-09-19.json` through
`results/grades_2026-09-23.json` files were already fully committed to
`main` (all five, including the last 2026-09-23 grading pass) hours before
this branch was created or this design was written. Git commit order
therefore proves the *design's population/metric/rule text* predates the
*aggregate statistic*, but it does not, and cannot, prove the authoring
agent was cryptographically or mechanically blind to the individual
outcome rows sitting in the same already-cloned repository -- there was no
technical barrier (hash-lock, separate blind agent, redacted extract)
between the author and the raw files. The one disclosed exclusion
(the Brandon Valenzuela row, seen during pre-design schema discovery) is
evidence the author was in fact reading those files' contents before
locking this design, and its exclusion should be read as a self-reported
correction, not proof the rest of the population was unseen. This
replication should be treated as a self-attested, procedural
pre-registration (order-of-commits plus honest disclosure), not a
blinded one. For future missions where this distinction matters more, a
stronger design would derive the design from a schema-only extract (field
names and types, no values) or from a separate agent instance with no
read access to the outcome files at all.

## Background (why this experiment, not another one)

On 2026-09-18, PR #128 (merged, `1379fb99e4`, documentation only) found that
across all 371 graded real public Top Picks then available
(`results/grades_*.json` -> `public_top_picks`, `grade` in `{hit, miss}`),
mean stated `hit_probability` was 64.6% vs. realized hit rate 53.6% -- an
11.0-point overconfidence gap, `p = 0.000009` under a naive one-sample
binomial test. The effect was concentrated in `hits_runs_rbis` and
`pitcher_outs`; plain `hits` was well calibrated (53.6%... wait, that
specific figure is `hits` at 62.8% vs 62.8%, see PR #128 text). No model,
weight, or selection code was changed as a result -- correctly, per this
project's own rule against tuning off a few live days -- and the entry's own
"recommended next work" explicitly says the *persistence* of the finding on
fresh data was not yet checked.

This experiment is that check: a pre-registered, out-of-sample replication
of the PR #128 finding on real Top Pick rows graded strictly after PR #128's
analysis window, which PR #128 never saw. It reuses PR #128's own primary
statistical test (for direct comparability) and adds a cluster-robust
secondary check that PR #128 did not have (PR #128 treated every row as
independent; picks on the same day can share a game, and rare players repeat
across a short window).

This is NOT a rerun of PR #188's full-board calibration / winner's-curse
test. That test is locked to rerun only at n>=30 fair-test-graded Top Picks
across >=10 games from `output/board_freeze_graded_*.json`. Checked today
(2026-09-24, before touching any outcome): four dates now have a graded
board-freeze file (2026-09-20/21/22/23), but 2026-09-23's 668 records are
all still `grade: "ungraded"` (game(s) not yet final at freeze-grading time),
so the real fair-test-graded population is unchanged from PR #188: **n=10
Top Picks across 4 games, pop n=790 across 18 games**. The threshold is not
met. That test is correctly not rerun this mission.

This experiment instead uses the **immutable public Top Pick ledger**
(`results/grades_*.json`), a different, much larger, independently-collected
evidence class with a genuinely clean pregame frozen probability
(`hit_probability` recorded at `published_top_pick_at`, which the script
verifies is strictly before each row's own `game_start`).

## Population (locked)

- Source: `results/grades_2026-09-19.json` through
  `results/grades_2026-09-23.json` (5 real committed files), field
  `public_top_picks`.
- Row eligibility: `grade` in `{"hit", "miss"}` (settled, official,
  binary-fair rows only -- excludes `void` and `ungraded`).
- Date choice: PR #128's own cumulative row count through
  `results/grades_2026-09-17.json` is 373, essentially matching its stated
  n=371 (the ~2-row difference is late/void reclassification that happens
  between a live snapshot and the currently-committed file -- disclosed,
  not resolved, since the exact live snapshot PR #128 used is not preserved
  separately from the current file). `2026-09-18` is excluded from this
  holdout as ambiguous (it may or may not have been partially included in
  PR #128's own count). The holdout population is therefore rows whose
  underlying slate date is `2026-09-19` through `2026-09-23` inclusive --
  entirely after PR #128's analysis was written and committed.
- **One disclosed exclusion for pre-registration integrity**: candidate id
  `fc2:822844:player-678218:hits_runs_rbis:1:over` (Brandon Valenzuela,
  2026-09-20, `results/grades_2026-09-20.json`) is excluded from this
  analysis. That single row's `hit_probability` and `grade` were both
  incidentally visible during the pre-design data-schema survey (used to
  discover the `public_top_picks` record shape before this design was
  written). No other row's outcome was inspected before this design was
  locked. Excluding this one row keeps the confirmatory test clean at the
  cost of n=1; it is not excluded for any other reason and its value is
  reported separately in the output for transparency.
- Row count (population size only, not outcome) known before running the
  aggregate test, from a pure `COUNT(*)` survey query that does not read
  `hit_probability` or `grade` in aggregate: expected n approximately 69-70
  after the one exclusion above. The exact n is reported by the script.

## Comparison / primary metric (locked)

For the holdout population:

1. `mean_predicted` = mean of each row's own `hit_probability` at
   publication.
2. `realized_rate` = (# rows with `grade == "hit"`) / n.
3. `gap = mean_predicted - realized_rate` (positive = overconfident, same
   sign convention as PR #128).
4. **Primary test** (matches PR #128's own method exactly, for direct
   comparability): two-sided exact binomial test of
   `P(X = observed_hits | n, p = mean_predicted)` computed from the
   binomial PMF/CDF directly (Python `math.comb`, no `scipy` dependency,
   since `scipy` is not a pinned project dependency and this script must
   run under the pinned `requirements.txt`).
5. **Secondary, cluster-robust check** (new relative to PR #128): a
   cluster bootstrap (10,000 resamples, fixed seed 20260924) resampling by
   `(slate_date, game_pk)` with replacement, recomputing `gap` on each
   resample, reporting the resulting 90% CI (5th/95th percentile). This
   accounts for the fact that Top Picks on the same date can share a game
   and are not fully independent draws, which the naive binomial test
   assumes.
6. **Exploratory, non-confirmatory breakdown**: the same `gap` computed
   separately for `stat in {hits_runs_rbis, pitcher_outs}` (the two markets
   PR #128 flagged) vs. every other market, reported descriptively with
   its own naive binomial p-value but explicitly labeled exploratory
   regardless of n, since per-market holdout n is expected to be small
   (single digits to low tens).
7. Integrity check (run before any statistic, fails loudly if violated):
   every row's `published_top_pick_at` timestamp must be strictly earlier
   than that row's own `game_start` timestamp. This is the check that the
   published probability was genuinely frozen pregame and not
   reconstructed after the fact.

## Success / falsification rule (locked)

- **CONFIRMS** (the PR #128 overconfidence finding persists out-of-sample):
  `gap > 0` **and** the cluster-bootstrap 90% CI for `gap` lies entirely
  above 0 (excludes 0), **and** n >= 50.
- **FALSIFIES / does not replicate**: the cluster-bootstrap 90% CI for
  `gap` includes 0, or `gap <= 0`.
- **INCONCLUSIVE (underpowered)**: if n < 50, the result is reported as
  EXPLORATORY regardless of the point estimate or naive p-value -- it may
  still be informative but is not treated as a confirmed replication. This
  threshold is lower than PR #188's locked n>=30-Top-Pick/>=10-game rule
  for the board-freeze full-population test because this is a different,
  narrower, single-population one-sample test (not a full-board
  calibration-plus-winner's-curse comparison across two populations), but
  it is pre-declared here and will not be adjusted after seeing the count.
- The per-market breakdown is never treated as confirmatory by itself, at
  any n, since it was not the primary pre-registered comparison.

## What this experiment does NOT do

- Does not change `mlb_daily.py`, `mlb_sources.py`, `generate_picks.py`,
  `board_freeze.py`, `board_freeze_grader.py`, `grade_board_freeze.py`, any
  calibrator, any selection/ranking weight, any workflow YAML, or any
  published History/ledger file.
- Does not rerun or modify PR #188's board-freeze winner's-curse test.
- Does not propose or apply a fix. A confirmed replication would motivate,
  as its own separate next milestone, the specific mechanism investigation
  PR #128 itself deferred (reading `score_batter()`'s `hits_runs_rbis`
  scoring path; auditing `pitcher_outs`' shrinkage prior against its own
  now-larger graded history) -- not a change made in this mission.
- Does not fabricate any price, probability, or outcome. All rows are read
  verbatim from the already-committed, already-graded real files listed
  above.

Alligator
