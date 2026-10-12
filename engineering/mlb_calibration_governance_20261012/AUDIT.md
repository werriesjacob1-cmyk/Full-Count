# MLB calibration governance audit (2026-10-12)

Status: **finding, Jacob decision required.** This PR changes no calibrator
value. Production `backtest/calibrators_by_market.json` is left exactly as main
has it, so the new governance test fails on purpose until Jacob chooses.

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
- The bar compares against raw, not against the shipped calibrator. Oct 5
  strikeouts beat the shipped calibrator by only 0.0006 Brier
  (0.25572 -> 0.25512, 236 held-out backtest rows); Oct 12: 0.26446 -> 0.26093
  (194 rows) while shrinking the supported region.
- pitcher_outs had been manually rejected; the bot added it Sep 14 and refit it
  four more times (A: 3.47 -> 2.83 -> 2.05 -> 2.04 -> 3.02).

Verdict: **unauthorized under the audit rule, and not validated forward.**
This is a governance failure, not a test defect.

## Evidence (kept separate by source)

Historical backtest (held-out rows used by the bot): numbers above.

Prospective live candidates, graded, strikeouts:

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
- Eligibility at the 0.60 bar flips across versions for 29 strikeouts
  candidates (raw ~0.62-0.67) and 66 pitcher_outs candidates.

## What this PR does

1. `backtest/calibration_registry.json` + byte-exact fixture of the last
   human-approved set (v1.0.0, `a052e79a`, sha256 `43576599...c877`).
   The five bot commits are listed under `unapproved_history`.
2. `test_calibration_governance.py`: production must equal an approved entry
   market for market (method, params, supported region) and
   `CALIBRATION_VERSION` must match; the recheck workflow must run `--dry-run`
   and never stage production. **Fails now, intentionally.**
3. `calibration-recheck.yml` now writes candidates only
   (`--dry-run --candidates-out backtest/calibrator_candidates.json`).
   `refit_calibrators.py` reports `production_written` and `promotion_mode`.
4. The two old tests are re-pointed at the approved fixture, so they test the
   code paths they were written for instead of pinning whatever production
   holds. Their coefficient guard role moves to the governance test.

Nothing updates expected coefficients to the bot's latest output and nothing
reverts production.

## Jacob decision required

| Option | Change | Consequence |
|---|---|---|
| (a) Restore v1.0.0 | copy fixture to production | strikeouts/pitcher_outs revert to Aug behaviour; pitcher_outs uncalibrated (raw); governance test passes |
| (b) Approve a set as v1.1.0 | registry entry + `CALIBRATION_VERSION` bump | needs forward evidence first (none clean today) |
| (c) Partial: v1.0.0 strikeouts + approve pitcher_outs | registry entry v1.1.0 | strikeouts has no live gain; pitcher_outs gain is leakage-favoured |
| (d) Keep current, record as unapproved | none | test stays red; not recommended |

Recommendation: (a) now, then promote any refit only through (b) after a
pre-registered forward window, with a minimum margin over the *shipped*
calibrator.

Alligator
