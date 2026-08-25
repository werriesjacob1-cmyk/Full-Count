# Backfill runtime investigation -- is the ~14h projection real?

## The question

An earlier status report extrapolated PID 3304's pace (~86s/date across its
first 15 dates, all in early April 2024) to a ~14-hour total runtime for
all 821 dates. The prior successful backfill (PID 3663, the one this
session lost to a container restart) completed in ~7h07m. This document
checks whether the current run is genuinely ~2x slower, or whether the
14h projection was an artifact of extrapolating from an unrepresentative
sample.

## The arithmetic (real numbers, not estimates)

**Current run (PID 3304), from its own state file, first 18 dates
(2024-04-01 through 2024-04-18, all early April):**

| date | seconds | games | rows |
|---|---|---|---|
| 2024-04-01 | 87.4 | 14 | 1,542 |
| 2024-04-06 | 93.0 | 15 | 1,662 |
| 2024-04-13 | 104.2 | 17 | 1,884 |
| 2024-04-17 | 104.6 | 16 | 1,771 |

Average across these 18 dates: ~85s/date, ~6.2s/game.

**Prior run (PID 3663), from the state-file excerpt captured earlier this
session before it was lost, same early-April window:**

| date | seconds | games |
|---|---|---|
| 2024-04-01 | 114.9 | 14 |
| 2024-04-06 | 126.0 | 15 |
| 2024-04-13 | 143.0 | 17 |

Average across the same dates: ~115-140s/date, ~8.2s/game.

**The current run's early-April dates are not slower than the prior
run's early-April dates -- if anything, modestly faster** (~85s vs
~115-140s for the same calendar dates). Day-to-day MLB Stats API/Statcast
latency variance is the more likely explanation than any code-path
regression; this session made no changes to `simulate_date()`,
`build_inputs()`, or any scoring/fetch code before or during this run.

## Where the 14h projection actually went wrong

The prior run's own real total: started 2026-08-24T21:53:13Z, finished
2026-08-25T05:00:47Z = **7h07m34s = 25,654 seconds**, for 821
date-attempts (578 real game dates + 243 `no_games` dates).

`no_games` dates cost almost nothing: `simulate_date()` returns as soon
as `m.fetch_lineups(date)` (one schedule call) comes back empty --
no per-player/per-game work ever starts (`backtest/engine.py`, the
`no_games` branch fires directly off `build_inputs()`'s early return).
243 such dates at a few seconds each is on the order of ~500-1,000s
total, not a meaningful fraction of 25,654s.

That leaves **~24,700-25,150 seconds for 578 real game dates -- an
implied SEASON-WIDE AVERAGE of ~43-44s/date**, roughly HALF of the
~85-140s observed for early-April dates specifically (both runs).

**Conclusion: early April is the most expensive part of the season to
process, not representative of the whole date range.** A 14-hour
projection built by extrapolating April's per-date cost across all 821
dates was always going to overshoot -- the real historical evidence
(the prior run's own true 7h07m completion) implies the current run
should land in a similar ~7-hour ballpark if its per-date cost profile
continues to track the prior run's (which the early-date comparison
above supports).

**Why is April specifically more expensive?** Not conclusively
determined here (would require deeper per-call profiling that risks
competing with PID 3304's own API usage, which this investigation
deliberately avoided -- see "what was not done" below). Plausible,
unconfirmed candidates: thinner season-to-date sample sizes early in the
year may force more fallback/estimation code paths per player (each with
their own network calls) than a well-established mid-season sample;
early-season roster/lineup volatility (call-ups, injury returns) may
trigger more retry/fallback lineup-source paths (the `MLB.com fallback` /
`Rotowire fallback` lines visible in PID 3304's own live log are exactly
this kind of fallback chain). Neither claim is verified against
call-level timing data -- flagged as real, scoped future work, not
asserted as fact.

## Resume-hardening runtime cost -- proven negligible

This session's atomic-write change (one `f.write()` call per date instead
of one per row) and the new `check_regime_consistency()` WARN-level check
(one linear scan of the output file, only on a genuine resume, i.e. only
when `in_output` is non-empty) are the only source changes made this
session. Neither executes on PID 3304 (already running before either
change existed in memory) nor adds meaningful cost to a future run:
`check_regime_consistency()` runs once at startup, not per-date, and a
single `write()` call is strictly cheaper than N separate calls for the
same data, never more expensive.

## Safe future optimizations (not applied -- would require restarting a
healthy run, explicitly against this session's own instruction)

Not implemented or benchmarked this pass, since doing so would mean
either interfering with PID 3304 or spending real network calls
competing with it. Recorded here as real, scoped candidates for a FUTURE
run only:
- If early-season fallback-chain frequency is confirmed as the dominant
  cost (per the unconfirmed hypothesis above), caching/memoizing
  fallback-source lookups across a run (rather than the current
  per-date-per-player pattern implied by the live log's `L7 form`/
  `batter arsenals`/`PA compositions` counts, which change every date)
  could help -- but this needs real profiling data first, not a guess.
- `StatcastStore` is already loaded once per run (`store.load()` before
  the date loop, per `run_backtest()`), so that specific historical
  N+1-load concern does not apply here.

## What this investigation did NOT do

Did not profile a single date's internal call graph (no cProfile run,
no per-endpoint timing breakdown) -- that would mean either running a
duplicate date against the same live APIs PID 3304 is using (real
interference risk) or profiling against a synthetic/mocked fixture (which
wouldn't reproduce real network-latency-driven costs, the more likely
dominant factor here). The arithmetic comparison above is real and
sufficient to answer the actual question asked ("is 14h a real
projection or an extrapolation artifact?") without that deeper, riskier
work.

## Verdict

**The ~14h projection was very likely an extrapolation artifact, not
evidence of a real slowdown.** The current run's per-date cost on the
identical early-April window is not worse than the prior successful
run's, and the prior run's own true total (7h07m) implies the season-wide
average is roughly half of April's per-date cost. No code-path regression
found. No optimization applied (correctly, per the standing instruction
not to touch a healthy PID 3304). Continue monitoring PID 3304
opportunistically; a revised ETA should be checked once it reaches
mid-season dates (May-July), where the season-wide average implies a much
faster observed pace than the April-only sample showed.
