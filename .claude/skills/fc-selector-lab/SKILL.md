---
name: fc-selector-lab
description: Run a selector-only experiment — probabilities held fixed, same candidate universe, exact N — to test whether a different ordering produces more hits at identical volume. Use for ranking, Best Expression, redundancy and portfolio work.
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# fc-selector-lab

Delegate to `fc-selector-scientist`. The whole discipline is one sentence:

> **The probabilities do not move.** Same numbers, same candidates, different
> order, same N — does it hit more?

If your result requires a probability to change, it is **not a selector result**.
Hand it to `fc-scientist` and say so. Conflating the two lets a world-model
change masquerade as a free selection win, which is unfalsifiable.

## Preconditions — verify, do not assume

1. **Identical candidate universe** on both arms. Fingerprint it, don't eyeball
   it: hash the sorted thesis identities and compare.
2. **Ranking-input fingerprint.** Hash the exact set of fields the ranker may
   read. If the two arms read different fields, you are comparing information
   access, not ranking skill — and if either can reach a realized-outcome or
   post-event field, the result is **void, not weakened**.
3. **Exact N, per operational slate.** `backtest/equal_volume.py` supplies an
   ORDER over the whole eligible population and the framework slices top-N. A
   policy that can shrink its own volume is being rewarded for selectivity.
   Aggregate-equal N is not enough: match N **per date**, or a challenger can
   quietly take more picks on good days.
4. **Strict Best Expression refill.** Suppression is **demotion below every
   unsuppressed candidate, never deletion**, so exact N is preserved and the
   slate auto-refills. `describe_suppression()` reports `fully_refillable`; if
   that is false, N is no longer equal and the comparison is void.

## Report — all of it, every time

- **Realized hit rate at identical N**, first. Brier and log loss belong in a
  labelled secondary section; they measure the world model, which you did not change.
- **Overlap / added / removed** between champion and challenger slates. All
  three. "The challenger is better" without the overlap number hides how much of
  the slate actually moved — a 2-pick difference and a 40-pick difference are
  very different claims.
- **Clustered uncertainty** — paired bootstrap resampling games, then players.
  Two picks on the same game are one shock; two on the same player more so.
- **Per-slate distribution**, not just the pooled number.

## Then

Every serious result goes to `fc-methodology-red-team` via `/fc-break-it`.
Nothing is promoted here. Thresholds, Top Pick policy and calibrators stay
frozen without explicit human authorization.
