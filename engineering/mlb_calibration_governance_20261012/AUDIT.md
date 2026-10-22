# MLB calibration governance audit (2026-10-12)

Status: **Jacob approved Option A (restore v1.0.0) on 2026-10-12, subject to
independent verification before merge.** This PR now restores production
`backtest/calibrators_by_market.json` byte-for-byte to the v1.0.0 artifact
(sha256 `435765993301e212e2b9db2b0348bafa49b3d9ecf4face483990e67cafc1c877`).
This restores the last pre-bot, manually curated artifact, now explicitly
authorized by Jacob for restoration (no separate written approval existed in
August). It is **not** a newly
demonstrated predictive improvement (see Impact and Unresolved risks).

## Failing tests on main and their cause

| Test | Fails since | Cause |
|---|---|---|
| `test_h1_ci_calibration_scale_integrity.py` §6 | `8a5901521f` (Oct 5) | pins strikeouts Platt params of the approved Aug set; the bot refit them |
| `test_strikeout_alternatives_stat_fix.py` | `34382f8af2` (Oct 12) | the Oct 12 refit narrowed strikeouts support to [0.55, 0.75), so fixed-input expectations moved |

Both commits are by the weekly `calibration-recheck.yml` bot (`contents: write`),
which runs `backtest/refit_calibrators.py` and commits any fit that beats *raw*
by >= 0.0015 Brier straight into production.

## Authorization and validity

- `engineering/AUDIT/README.md`: calibration changes "require a separate
  validation task, held-out evidence, explicit versioning, and a clear
  distinction between backtest and forward performance." The auto-promotion
  (bdbb6179, Aug 12) predates that rule and was never reconciled with it.
- No version bump: `recommendation.CALIBRATION_VERSION` stayed `1.0.0` across
  five different production calibrator sets (9e820aab47, b87e01a6df,
  ea4f992089, 8a5901521f, 34382f8af2). Picks from Sep 14 to Oct 12 therefore
  carry a version label that does not identify the calibrator used.
- The bar needs >= 0.0015 Brier over raw, but only a strictly positive
  margin (any amount) over the shipped calibrator. Oct 5
  strikeouts beat the shipped calibrator by only 0.0006 Brier
  (0.25572 -> 0.25512, 236 held-out backtest rows); Oct 12: 0.26446 -> 0.26093
  (194 rows) while shrinking the supported region.
- pitcher_outs had been manually rejected; the bot added it Sep 14 and refit it
  four more times (A: 3.47 -> 2.83 -> 2.05 -> 2.04 -> 3.02).

Verdict: **unauthorized under the audit rule, and not validated forward.**
This is a governance failure, not a test defect.

## Evidence (kept separate by source)

Historical backtest (held-out rows used by the bot): numbers above.

Prospective live candidates, graded, strikeouts (first pass, prediction
ledger; superseded by the board-freeze table in "Restoration impact" below,
which disagrees on direction -- see the correction there):

| Window | n | v1.0.0 (Aug) | Oct 5 set | Oct 12 set | Note |
|---|---|---|---|---|---|
| Sep 1 - Oct 4 | 134 | **0.2524** | 0.2534 | 0.2543 | inside the refits' training windows (biased toward refits); Aug still best |
| Oct 6 - 11 | 13 | - | - | best | too small, noise |

pitcher_outs, graded since Sep 14 (n=124): Brier raw 0.2837 -> 0.2651 under
the Oct 12 set, but every one of those windows is leakage-favoured. No clean
forward evidence exists for any bot set.

Immutable published Top Pick ledger (`data/public_top_picks/registry.json`,
read only, not modified): Sep 14 - Oct 12 contains 39 strikeouts and 20
pitcher_outs Top Picks that were scored under bot-promoted, unversioned
calibrators. The snapshots do not record the calibrator, so they can only be
attributed by date. Nothing in this PR rewrites them.

## Production impact

- Shipped probabilities match the Oct 5 set on Oct 6-11 and the Oct 12 set on
  Oct 12.
- Oct 7: Max Fried O4.5 K became a top pick at 0.6002 (v1.0.0 would give
  0.599). Not publicly exposed because Pages deploys were stalled (PR #224).
- Oct 12 set has a discontinuity: raw 0.74 -> 0.6245 calibrated, raw 0.76 is
  outside support and ships raw 0.76.
- (First pass, all candidate sources.) Eligibility at the 0.60 bar flips across versions for 29 strikeouts
  candidates (raw ~0.62-0.67) and 66 pitcher_outs candidates.

## What this PR does

1. `backtest/calibration_registry.json` + byte-exact fixture of the last
   pre-bot, manually curated set (v1.0.0, `a052e79a`, sha256 `43576599...c877`),
   now explicitly authorized for restoration.
   The five bot commits are listed under `unapproved_history`.
2. `test_calibration_governance.py`: production must equal an approved entry
   market for market (method, params, supported region) and
   `CALIBRATION_VERSION` must match; the recheck workflow must run `--dry-run`
   and never stage production. (Failed intentionally at `acfbec58`, before
   Jacob's decision; passes 19/19 after the restoration.)
3. `calibration-recheck.yml` now writes candidates only
   (`--dry-run --candidates-out backtest/calibrator_candidates.json`).
   `refit_calibrators.py` reports `production_written` and `promotion_mode`.
4. The two old tests are re-pointed at the approved fixture, so they test the
   code paths they were written for instead of pinning whatever production
   holds. Their coefficient guard role moves to the governance test.

5. (After Jacob's Option A.) Production `calibrators_by_market.json`
   restored byte-exact to v1.0.0. All five bot versions are archived byte-exact
   under `backtest/fixtures/unapproved/`.

No expected coefficient was updated to the bot's latest output.

## Jacob decision (recorded)

Options presented: (a) restore v1.0.0, (b) approve a set as v1.1.0 after a
forward window, (c) partial, (d) keep current with the test red.
**Jacob chose (a) on 2026-10-12**, subject to independent verification
before merge. Recorded in `backtest/calibration_registry.json`
(`approved[0].authorization`).

## Restoration (Option A)

### Provenance of v1.0.0 (verified)

| Item | Value |
|---|---|
| Artifact | `backtest/fixtures/calibrators_by_market_v1.0.0_a052e79a.json` |
| sha256 | `435765993301e212e2b9db2b0348bafa49b3d9ecf4face483990e67cafc1c877`, equal to `git show a052e79a:backtest/calibrators_by_market.json` |
| Markets | hits, hits_runs_rbis, strikeouts (Platt) |
| Lineage | 4118bc69 (08-12 per-market refit; pitcher_outs, nrfi_combined, singles deliberately not fit), 3c047e5b (08-14 hard_hit_105 removed), 83f1f6a0 (08-15 strikeouts refit), b64e3ed5 and a052e79a (08-19 support_bins added, params byte-identical) |
| Original authorization | Jacob-directed engineering sessions; no separate written approval existed. a052e79a was the last production write before the unattended bot's first one (9e820aab47, 09-14). No other commit touched the file in between. |
| Authorization now | Jacob, 2026-10-12, Option A |

`recommendation.CALIBRATION_VERSION` stays `1.0.0`, because production really
is v1.0.0 again. Records from 09-14 to 10-12 carry the label `1.0.0` although
bot sets were in effect. Those records are **not** altered; use the dated
`in_production` windows in the registry to attribute them.

### Audit records preserved

Every bot-written version that reached production is kept byte-exact under
`backtest/fixtures/unapproved/`. Its full commit SHA, sha256, production
window and markets are in `registry.unapproved_history`.

| In production | Commit | sha256 (prefix) | Markets |
|---|---|---|---|
| 09-14 .. 09-20 | 9e820aab47 | 87f34efc1e4b | hits, hits_runs_rbis, pitcher_outs, strikeouts |
| 09-21 .. 09-27 | b87e01a6df | 32ac17bd0f62 | same |
| 09-28 .. 10-04 | ea4f992089 | 3423b1c24e23 | same |
| 10-05 .. 10-11 | 8a5901521f | 5951ef195a41 | same |
| 10-12 .. merge | 34382f8af2 | 2f15846e40af | same |

### Enforcement (registry-driven, not coefficient pinning)

`test_calibration_governance.py` reads the registry and fixtures; it has no
hard-coded coefficients. It requires all of the following:
- Production bytes equal an approved artifact digest and match it market for
  market (method, params, supported region).
- `CALIBRATION_VERSION` equals that entry's version.
- Each approved entry reproduces from its fixture and records who authorized
  it.
- Every audit record stays byte-exact and is never listed as approved.
- The weekly recheck runs `--dry-run` and never stages production.

Negative checks run locally. Each of these is rejected:
- any of the 5 bot versions placed in production;
- a meta-only edit with params unchanged;
- a version bump without a registry entry;
- removing `--dry-run` from the workflow.

## Restoration impact (measured counterfactual)

**Data.** The 20 frozen pre-game MLB boards, `output/board_freeze_2026-09-20`
through `10-12`. These are prospective records, graded where available.

**Method.**
- Each record's raw probability is re-run through `_calibrate_one` twice:
  once with v1.0.0, and once with the Oct 12 bot set that this PR replaces.
- Main-board (published Top Pick) selection is then re-run with the real
  `rank_for_board` and `select_main_board`, using the same quality gate,
  lift floor and price check.
- The published ledger is neither used nor changed.

**Fidelity check.** Re-running with the probabilities actually shipped
reproduces the recorded `selected_top_pick` set exactly (80/80).

**Limits.** This is a historical counterfactual on candidates already
generated, not forward evidence. The Sep 20 – Oct 4 dates fall inside the
training windows of the Oct 5 and Oct 12 refits, so those dates favour the
refits.

**Probabilities**
- hits, hits_runs_rbis: unchanged (identical parameters).
- strikeouts (n=159): 99 change; mean shift +0.0005; range -0.166 to +0.022.
  The largest shifts are raw values >= 0.75. The Oct 12 set shipped those
  raw (outside its narrowed support); v1.0.0's support is [0.55, 0.80), so
  it calibrates them again.
- pitcher_outs (n=145): goes back to raw, because v1.0.0 has no pitcher_outs
  calibrator (as approved on 08-12). Mean shift +0.069, up to +0.118.

**Top Pick volume and market mix (20 slates)**

| Main-board picks | Oct 12 bot set | v1.0.0 |
|---|---|---|
| Total | 76 | 83 (+7) |
| pitcher_outs | 12 | 22 (+10) |
| hits | 19 | 18 |
| stolen_base | 26 | 25 |
| combined_strikeouts | 2 | 1 |
| hits_runs_rbis | 10 | 10 |
| nrfi_combined | 7 | 7 |
| strikeouts | 0 | 0 |

- **Picks added under v1.0.0:** 10, all pitcher_outs. 4 of the 9 graded hit.
  Their mean displayed probability is 0.621; the Oct 12 set would have shown
  0.533.
- **Picks removed:** 3, of which 2 hit.
- **Selected-set outcomes:** Oct 12 set 76 selected / 75 graded / 39 hits
  (52.0%); v1.0.0 83 / 81 / 41 (50.6%). This is an unequal-volume
  retrospective comparison on frozen odds. Codex reproduced these figures
  independently (review 6135014770).
- **Classification level** (status, not board selection): pitcher_outs gains
  4 top_pick and 27 lean statuses; strikeouts gains 3 top_pick.

**Live calibration by version (Brier on graded board candidates)**

| Market | Window | n | raw | v1.0.0 | Oct 5 | Oct 12 |
|---|---|---|---|---|---|---|
| strikeouts | 09-20..10-04 (in refit training) | 137 | 0.2572 | 0.2535 | 0.2534 | 0.2527 |
| strikeouts | 10-05..10-12 | 16 | 0.2577 | 0.2452 | 0.2457 | 0.2367 |
| pitcher_outs | 09-20..10-04 (in refit training) | 124 | 0.2495 | 0.2495 (raw) | 0.2465 | 0.2422 |
| pitcher_outs | 10-05..10-12 | 15 | 0.3972 | 0.3972 (raw) | 0.3516 | 0.3526 |

**Correction to the first pass above.** On this source the Oct 12
strikeouts set scores slightly better than v1.0.0 (0.2510 vs 0.2526,
n=153). That is the opposite direction from the prediction-ledger figure
(Sep 1 – Oct 4, n=134). Both differences are under 0.002 Brier, both windows
favour the refits, and the forward slices are only 15–16 rows. **There is
no reliable evidence either way for strikeouts.**

**pitcher_outs.** Raw probabilities are overconfident on these candidates:
mean raw 0.619 against a 0.460 hit rate (n=139). Where raw is >= 0.70, it
averages 0.788 against a 0.600 hit rate.

## Independent review and merge condition

Codex review [6135014770](https://github.com/werriesjacob1-cmyk/Full-Count/pull/225#issuecomment-6135014770)
on head `911b44d5` returned **CONDITIONAL GO**, with no P0 or P1 defects.

Codex independently confirmed:
- the byte identity of every artifact;
- the 80/80 replay of recorded selections;
- the counterfactual counts.

The condition: Jacob explicitly accepts the quantified pitcher_outs exposure
under Option A. That exposure is about +10 board picks per 20 slates, at raw,
overconfident displayed probabilities.

The review also holds any claim of predictive improvement, and any change to
Top Pick eligibility. If Jacob does not accept the exposure, a separate policy
decision is needed. Neither exclusion nor the unapproved refit is substituted
here.

The Oct 12 pitcher_outs curve is kept only as an unapproved research
challenger, in `backtest/fixtures/unapproved/`.

## Unresolved risks

1. **pitcher_outs ships raw, and raw looks overconfident on live data.**
   - Restoring v1.0.0 adds about 10 pitcher_outs main-board picks per 20
     slates, displayed at higher probabilities than the bot calibrator gave.
   - Raw is the approved behaviour as of 08-12, when the calibrator failed
     held-out. The live record since Sep 20 points the other way, with
     leakage caveats.
   - What to do about pitcher_outs is a separate decision for Jacob and is
     not made here. Options include a reviewed calibrator as v1.1.0 after a
     forward window, or excluding uncalibrated pitcher_outs from Top Pick.
2. **The bot on main can still write production until this merges.** The
   next scheduled `calibration-recheck` run is Monday 2026-10-19 09:00 UTC.
   If this PR is unmerged by then, main's copy of the workflow can promote
   again.
3. **Historical records are mislabelled but unchanged.** Records from 09-14
   to 10-12 carry `calibration_version 1.0.0` while bot sets were in effect.
   They stay unaltered and can be attributed to a version by date, through
   the registry only.
4. **Approval is not cryptographically authenticated.** The registry's
   `authorization` is a reviewed text field; CI cannot prove Jacob approved a
   future entry if code and registry are edited together in one PR. The
   standalone `refit_calibrators.py` CLI also keeps an explicit
   production-write mode for manual use. The scheduled bot has no
   production-write path, and any production change still fails
   `test_calibration_governance.py` unless a registry entry is added in a
   reviewed PR, so approval rests on PR review and Jacob's merge control
   (Codex review 6135014770, P2).
5. **Counterfactual limits.** The quality score, odds and CIs are held fixed
   as frozen. Only primary lines are covered; alternate-line effects are not
   simulated.

Alligator
