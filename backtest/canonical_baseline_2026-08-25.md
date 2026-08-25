# Canonical control baseline -- 2026-08-25

The main backfill (PID 3663, `--start 2024-04-01 --end 2026-06-30`) finished
at 2026-08-25T05:00:47Z. This is the first report run against real,
reconciled canonical history. **This is the control, not a promotion
experiment** -- every future challenger measures itself against these same
numbers.

## Reconciliation (`backtest/build_canonical_backtest.py`)

821 calendar dates processed by the backfill; 578 had real games ("ok"),
243 correctly classified `no_games` (offseason/All-Star break/etc.) -- zero
gaps, zero unexplained failures.

Stitched per the deterministic rule discovered earlier this session (root
cause: commit `919456e5` fixed a `predicted_prob`-nullification bug in
`backtest/engine.py`'s `to_row()`):
- `2024-04-01..2025-02-26`: from `rows_backfill_repair.jsonl` (400,207 rows,
  all `code_git_sha=6b748538`, postdates the fix)
- `2025-02-27..2026-06-30`: from `rows_backfill.jsonl`'s later portion
  (627,255 rows, all `code_git_sha=6b748538`) -- its early portion
  (399,250 rows, `code_git_sha=c182b186`, predates the fix) was correctly
  dropped as superseded.

Result: **1,027,462 canonical rows, 578 dates, 2024-04-01..2026-06-30**,
written to `backtest/rows_canonical.jsonl` (gitignored, regenerate via the
build script). `provenance.require_single_regime()` **PASSED** -- the
canonical file is confirmed single-regime, not a silent mix of two code
eras.

## Control baseline (`backtest/canonical_baseline_report.py`)

Full report: see this run's own JSON output (not committed -- regenerate
with `python3 backtest/canonical_baseline_report.py backtest/rows_canonical.jsonl`).
Headline numbers, OBSERVED unless noted RECONSTRUCTED:

**Coverage**: 1,027,462 rows, 100% carry an outcome (0 missing) --
`n_rows_missing_outcome_field=0` is expected by `SCHEMA.md`'s own design
(ungraded rows are omitted entirely, never encoded), not evidence every
candidate graded cleanly; see the report's own coverage_caveat.

**Per-market hit rates** (OBSERVED, all in the sane band -- no market above
~70%, matching the backfill's own leakage-check output):
doubles 15.8%, hard_hit_105 6.0%, hits 60.8%, hits_runs_rbis 68.7%,
home_run 11.5%, nrfi_combined 51.0%, pitcher_outs 58.1%, rbis 30.2%,
runs 38.6%, singles 44.7%, strikeouts 57.9%, total_bases 35.1%, triples 1.4%.

**Probability-bucket calibration (RECONSTRUCTED bucket, OBSERVED
predicted_prob/outcome)** -- realized hit rate rises almost monotonically
with the model's own stated probability, across 1M+ rows:

| bucket | n | hit rate |
|---|---|---|
| 0.00-0.05 | 158,580 | 2.1% |
| 0.15-0.20 | 81,435 | 16.6% |
| 0.30-0.35 | 101,836 | 34.1% |
| 0.45-0.50 | 48,593 | 49.3% |
| 0.55-0.60 | 43,920 | 58.5% |
| 0.60-0.65 | 64,147 | 63.8% |
| 0.65-0.70 | 46,295 | 67.9% |
| 0.70-0.75 | 25,476 | 71.4% |
| 0.75-0.80 | 5,829 | 60.6% (n drops sharply here -- thin-sample noise, not a reversal) |

**Selection-like population (RECONSTRUCTED proxy, `predicted_prob >= 0.60`,
generate_picks.py's own `MIN_LINE_PROB` floor -- NOT real production
eligibility, omits the evidence/reliability/lineup/price gates)**:
141,998 rows, **66.39% hit rate**. This lands inside the main board's
intended 60-80% design band. It is the single most load-bearing number in
this report -- it says the model's own probability floor, at real
multi-year scale, on a single verified code regime, produces realized
outcomes consistent with what the board claims.

**What this does NOT prove**: real production eligibility (evidence grade,
lineup confirmation timing, price/value gates are not reconstructable from
backtest rows -- see the report's own UNAVAILABLE notes for
`lineup_assumed` and fallback-source flags), and this is a POOLED number
across every date/market/probability level at `>=0.60`, not a same-nominal-
probability subgroup analysis (that is Priority 6, not yet run).

## What's next

This baseline is the reference point for:
- Priority 6: same-nominal-probability subgroup trustworthiness analysis
  (which 65% predictions are actually trustworthy, controlling for the
  probability the model already believes).
- Priority 7-11: fragility, disagreement, market specialization, shrinkage
  audit, opportunity modeling -- all now unblocked.
- Priority 12: prospective policy research design (Champion vs Shadows),
  informed by whatever Priority 6-11 find.

Do not re-run `build_canonical_backtest.py`/`canonical_baseline_report.py`
casually -- they are fully deterministic and idempotent, but the underlying
`rows_backfill*.jsonl` files should not change again unless a new repair
run is deliberately launched.
