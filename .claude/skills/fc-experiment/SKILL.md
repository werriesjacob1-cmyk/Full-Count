---
name: fc-experiment
description: Preregister a consequential accuracy experiment before looking at any result — hypothesis, dataset identity, eligible population, equal volume, outcome policy and metrics locked up front. Use when starting predictive research, not for routine bug fixes.
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# fc-experiment

The point is **preregistration**. Everything below is written down and committed
*before* a result is seen. An experiment whose definition moved after the first
look is a new experiment wearing the old one's name, and its p-value means
nothing.

Delegate the research itself to `fc-scientist`. Do not do scoring or signal
work in the main thread.

## The registration — all fields, before any run

1. **Hypothesis** — one falsifiable sentence. "X improves things" is not one.
2. **Dataset identity** — run id, code SHA, generation regime classification,
   environment fingerprint, source-lineage fingerprint. `MIXED_NON_EQUIVALENT`
   is disqualifying; `MIXED_UNPROVEN` is not evidence yet.
3. **Eligible population** — the exact candidate set both arms rank. Both arms
   see the same one, or you are measuring eligibility, not skill.
4. **Operational equal volume** — N, and **N matched per operational slate/date**,
   not merely in aggregate. Aggregate-equal N can hide a challenger taking more
   picks on good days and fewer on bad ones, which is a bet on the day, not skill.
5. **Ranking inputs** — every field the ranker may read. Anything not on this
   list is out of bounds, which is how outcome leakage gets caught.
6. **Outcome policy** — what counts as a hit, and **how missing or ungraded
   outcomes are handled**. Dropping them silently changes the denominator and
   inflates the rate. Decide before you know which rows are missing.
7. **Primary metric** — realized hit rate at identical N. Brier, log loss and
   calibration are secondary diagnostics in a labelled section, never the lead.
8. **Uncertainty** — paired cluster bootstrap resampling **games**, not rows.
   Picks in one game share a shock; an unclustered interval is too narrow and
   the error always flatters the challenger.
9. **Stability cuts** — declared in advance: by season, by prop family, by month.
   Cuts chosen after seeing the headline are not evidence.
10. **Negative controls / placebos** — where a shuffled or nonsense variant
    should show no effect. If a placebo "works", the harness is broken.
11. **Discovery vs confirmation** — say which this is. A discovery result is a
    hypothesis for a later confirmation run, never a promotion case.

## Then, and only then

Run it. Report the primary metric first. Send every serious conclusion to
`fc-methodology-red-team` (`/fc-break-it`) before anyone acts on it —
self-certification is not evidence.

## Hard limits

No probability, calibrator, weight, threshold, Top Pick policy, settlement or
grading change without explicit human authorization. Never fabricate a
historical sportsbook price, and never claim exact historical production
eligibility from data that carried no price.
