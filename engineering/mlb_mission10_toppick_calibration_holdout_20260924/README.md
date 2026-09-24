# MLB Mission 10: Top Pick calibration holdout replication

Workstream ID: `MLB-MISSION10-ACCURACY`. See `DESIGN.md` in this directory
for the full locked, pre-registered design (population, cutoffs, primary/
secondary/exploratory comparisons, and the success/falsification rule),
committed as the first commit on this branch, before any aggregate outcome
statistic in the target population was computed. **Read `DESIGN.md`'s own
"Honest limit on 'pre-registered'" note**: the underlying `results/grades_*`
files already existed in the repository before this design was written, so
this is a self-attested, order-of-commits pre-registration, not a
cryptographically blind one -- the design author could have opened any row
before writing the population/rule text, and the disclosed exclusion below
is evidence that some incidental viewing did happen.

## What this is

A pre-registered, out-of-sample replication check of PR #128's merged
2026-09-18 finding ("Top Pick calibration overconfidence": across the then-
371 real graded public Top Picks, mean stated `hit_probability` was 64.6%
vs. realized 53.6%, an 11.0pp gap, naive binomial `p=0.000009`). PR #128
explicitly did not check whether that gap would persist on fresh data --
this experiment is that check, using real public Top Pick rows graded
strictly after PR #128 was written (`results/grades_2026-09-19.json`
through `results/grades_2026-09-23.json`), which PR #128 never saw.

This is **not** a rerun of PR #188's board-freeze full-population
calibration / winner's-curse test. That test's locked rerun threshold
(n>=30 fair-test-graded Top Picks across >=10 games from
`output/board_freeze_graded_*.json`) was checked first and is **still not
met**: a fourth graded board-freeze date now exists (2026-09-23), but its
668 records are all `grade: "ungraded"` (the game(s) had not gone final at
freeze-grading time), so the real population is unchanged from PR #188 --
**n=10 Top Picks across 4 games**. That test correctly was not rerun.

## Real result (n=69, one row excluded for pre-registration integrity)

| | value |
|---|---|
| n (holdout, 2026-09-19 through 2026-09-23) | **69** |
| mean stated `hit_probability` | **64.4%** |
| realized hit rate | **52.2%** |
| gap (predicted − realized) | **+12.2 pp** |
| naive one-sample binomial two-sided p | **0.043** |
| cluster-bootstrap 90% CI for gap (by game, 10,000 resamples, 32 clusters) | **[+3.9 pp, +20.4 pp]** |
| **Verdict** | **CONFIRMS_PERSISTENT_OVERCONFIDENCE** |

Per the locked rule (gap > 0 AND 90% cluster-bootstrap CI excludes zero AND
n >= 50), this **confirms** that the Top Pick population's overconfidence
found in PR #128 is a persistent, ongoing property of the live selection
process, not a one-time artifact of the 2026-08-18-through-09-17 window PR
#128 inspected. The magnitude (+12.2pp) is close to PR #128's own +11.0pp,
and even the naive (non-cluster-adjusted) test alone reaches significance
at n=69, a much smaller sample than PR #128's 371.

**Supplementary information, not a re-test** (DESIGN.md locked the 90% CI
as the primary rule; this is reported in addition, not in place of it): the
same cluster-bootstrap at the stricter 95% level also excludes zero,
**[+2.5pp, +22.1pp]**, so the confirmatory verdict is not an artifact of
choosing the looser of the two conventional CI levels.

Reproduction command (from the repository root):

```
python3 engineering/mlb_mission10_toppick_calibration_holdout_20260924/run_holdout_replication.py
```

## A real, disclosed contradiction: the per-market concentration does NOT replicate

PR #128 found the overconfidence gap concentrated in `hits_runs_rbis` and
`pitcher_outs`, with plain `hits` "essentially perfectly calibrated." This
holdout's exploratory (pre-declared non-confirmatory) per-market breakdown
shows the **opposite pattern**:

| market (holdout, n=69 total) | n | gap | naive p |
|---|---|---|---|
| `hits_runs_rbis` | 42 | **+6.0pp** | 0.42 (not significant) |
| `pitcher_outs` | 7 | **−5.6pp** (slightly *under*confident) | 1.0 |
| `hits` | 13 | **+30.5pp** | **0.042** (significant at this small n) |
| `strikeouts` | 7 | +33.4pp | 0.11 (not significant, n=7) |

The two markets PR #128 flagged as the problem are, in this fresh window,
roughly calibrated or even mildly underconfident; the market PR #128 called
"essentially perfectly calibrated" is, in this fresh window, the largest
and only individually-significant gap. Per this project's own instruction
to surface contradictions rather than silently resolve them: **this is
reported as a genuine, disclosed conflict with PR #128's mechanism
hypothesis, not smoothed over.** Two honest readings, neither confirmed
here:

1. PR #128's per-market breakdown was itself thinner and noisier than its
   large *aggregate* n suggested (`pitcher_outs` was n=33 there; `hits` is
   n=13 here), and market-level concentration claims regress toward a more
   uniform overconfidence pattern out of sample -- i.e., the *aggregate*
   Top-Pick-wide overconfidence is the real, persistent effect, and the
   *specific market story* was overfit to one window.
2. The market-level effect is itself non-stationary week to week for a
   reason not yet investigated (e.g., which specific players/roles get
   selected as Top Picks shifts with the slate), and a single 5-day, 69-row
   holdout is simply too thin to resolve per-market questions at all -- this
   reading is consistent with every per-market cell here having single- or
   low-double-digit n.

This experiment cannot adjudicate between those two readings, and does not
try to; per the locked design, the per-market breakdown was declared
exploratory/non-confirmatory at any n, precisely because 5 days of data was
expected to be too thin per market. It is reported in full, not cherry-
picked, in `report.json`.

## What this does and does not establish

**Does establish** (primary, confirmatory, locked before the result):
the real, live, published Top Pick population remains measurably
overconfident on data PR #128 never saw -- a persistent, ongoing,
actionable-in-principle gap between stated confidence and realized outcome
at the exact population that reaches the public/customer, not merely the
full candidate pool.

**Does not establish**: which market, mechanism, or code path drives it (the
market breakdown above actively argues against PR #128's specific
`hits_runs_rbis`/`pitcher_outs` mechanism story generalizing), nor any fix.
No model, weight, calibrator, or selection code was touched. Per this
project's own standard against tuning off a small live window, no such
change is proposed here.

**Concrete next milestone** (not attempted this mission, disclosed as a
scope boundary): if the aggregate overconfidence keeps replicating on
future 2-3-week holdout windows while the per-market pattern keeps
shifting, that is itself evidence the effect lives in the
*argmax-selection* step (winner's-curse-style: whichever candidate's
estimation noise ran hot on a given day gets published, regardless of which
market it happens to be in that day) rather than in any one market's
probability formula -- exactly the selection-effect hypothesis PR #128's
own "recommended next work" named and did not yet test against the larger
backtest candidate pool. That backtest-pool test is the next concrete,
falsifiable step, not performed here.

## Files

- `DESIGN.md` -- the locked, pre-registered design (first commit on this
  branch).
- `calibration_holdout_lib.py` -- reusable, dependency-light (no `scipy`)
  statistics: exact two-sided binomial test, cluster-bootstrap CI, pregame-
  integrity assertion, and the `results/grades_*.json` loader/filter.
- `test_calibration_holdout_lib.py` -- 15 unit tests for every reusable
  function above (binomial-test edge cases including a reproduction of PR
  #128's own reported p-value magnitude, gap-direction arithmetic, pregame-
  integrity pass/fail, cluster-bootstrap determinism and degenerate-single-
  cluster behavior, and the loader's grade/exclusion filtering). All 15
  pass.
- `run_holdout_replication.py` -- the one-shot analysis script that produced
  `report.json` from the real, already-committed `results/grades_*.json`
  files. Deterministic given its fixed bootstrap seed (`20260924`).
- `report.json` -- full real output, including every number in this README,
  the full per-market table, and PR #128's own figures reproduced verbatim
  as prior context (not recomputed by this script).

## Real limitations, stated plainly

- n=69 is enough to confirm the *aggregate* effect at the locked 90%
  cluster-bootstrap threshold, but it is thin for anything finer. Every
  per-market cell above should be read as suggestive at best.
- One row (`fc2:822844:player-678218:hits_runs_rbis:1:over`, Brandon
  Valenzuela, 2026-09-20) was incidentally seen (both its `hit_probability`
  and `grade`) during pre-design schema discovery, before `DESIGN.md` was
  written and locked. It is excluded from this analysis for pre-
  registration integrity rather than silently left in; its value is
  recorded in `report.json`'s `population.excluded_row_detail` for full
  transparency. No other row's outcome was inspected before the design was
  locked.
- The 32 clusters (`(slate_date, game_pk)` pairs) behind the 69 rows are not
  large in absolute terms; the cluster-bootstrap CI is the best available
  correction for within-game non-independence given the real data that
  exists today, not a claim that 32 independent clusters is a large sample.
- This experiment, like PR #128, is diagnostic only. It changes no model,
  weight, calibrator, selector, or workflow.

Alligator
