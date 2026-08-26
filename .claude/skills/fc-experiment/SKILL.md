---
name: fc-experiment
description: Start a new Full Count research experiment/challenger with the record-measure-promote discipline pre-wired (locked definition, equal-volume comparison, no post-test tuning). Use when starting new predictive research, not for routine bug fixes.
---

# fc-experiment

Scaffolds a new challenger/experiment under `backtest/` correctly the first
time, instead of relitigating the same methodology mistakes every time.

## Steps

1. Delegate the actual research to the `fc-scientist` agent -- do not do
   scoring/signal work directly in the main thread.
2. Before any result exists, write the experiment's definition file (cutoffs,
   eligible markets, buckets) and mark it LOCKED if it's meant to be a real
   pre-registered test. This must happen BEFORE looking at outcomes -- see
   `AGENTS.md` rule 15 and `fc-scientist.md`'s "no post-test tuning" rule.
3. Confirm point-in-time safety: `backtest/signals.py`'s `PointInTime` class
   and `verify_no_lookahead()` must pass before any measurement from this
   experiment is trusted.
4. Require an equal-volume comparison with exact overlap/added/removed
   counts and their hit rates -- never just "the new one is better."
5. Require year/season-phase stability and a bootstrap or binomial CI on
   every headline number.
6. Once fc-scientist reports a result, hand it to the `fc-methodology-red-team`
   agent (or invoke `/fc-break-it`) before it's trusted or promoted.
7. Never promote to `generate_picks.py`'s live scoring path without an
   explicit human decision after seeing the report.

## When NOT to use

A routine bug fix, a UX change, or anything that isn't proposing a new
signal/challenger/scoring change. Use the normal workflow for those.
