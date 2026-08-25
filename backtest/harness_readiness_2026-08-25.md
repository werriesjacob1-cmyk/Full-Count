# Harness readiness for market specialization / CI-lower-bound / shrinkage (Priorities 8-10)

Written 2026-08-25 as an honest scope-triage note, not a claim that
separate full modules exist for each of these. Given the volume of real,
tested infrastructure already built this session while the canonical
backfill reruns (restart-safety hardening, the disagreement one-command
runner + locked protocol, the selection-information-loss and
pitcher-workload audits, the shadow-policy framework, prospective
reporting tooling), building four MORE separate harness scripts each with
their own test suites risked spreading this session's remaining budget
too thin to keep everything genuinely correct. This note records exactly
what's already reusable for each, and what would still need to be
written.

## Priority 9 (CI-lower-bound) -- already covered, not a gap

`backtest/shadow_policy_framework.py`'s `ci_lower_bound_policy()`
(19 tests passing) already implements exactly what this priority asked
for: ranks by CI lower bound ONLY among candidates that structurally
carry a `prob_ci`, never fabricates one for a market that doesn't have
one (tested explicitly:
`test_no_ci_market_excluded_not_fabricated`), and composes with
`compare_policies()`/`grade_policy_selection()` for an equal-volume
comparison against `probability_first_policy()` or `champion_policy()`.
Nothing further needs building here -- running it against real canonical
data once it exists is the remaining step, not new code.

## Priority 8 (market specialization) -- the general machinery exists; a per-market runner does not

Every equal-volume/comparison function built this session
(`equal_volume_ranking_comparison()` in `pa_opportunity_model.py`,
`compare_policies()` in `shadow_policy_framework.py`) already takes a
`market` or an already-filtered candidate list as an argument -- running
any of them per-market is a one-line filter at the call site, not new
logic. What's genuinely NOT built: a single script that loops over
`hits`/`total_bases`/`home_run`/`hits_runs_rbis`/`strikeouts`/
`pitcher_outs`, runs each predeclared policy per market, and assembles
one consolidated report. This is real, scoped, small remaining work --
deliberately not written yet because writing it without real data to
validate against risks a script with an undetected bug in its
market-filtering logic sitting untested for however long the backfill
takes. Build it once canonical history returns, immediately before
running Priority 9/10 of the ORIGINAL disagreement-phase directive
(market specialization after the equal-volume disagreement result).

## Priority 10 (shrinkage sweep) -- explicitly deferred, correctly

The standing instruction is unambiguous: shrinkage work is a separate
thread from disagreement/opportunity, must never touch the closed
stable-lift decision, and its own priority ordering in every directive
this session has received places it AFTER disagreement and market
specialization are resolved. No harness was built for it this pass --
building speculative sweep tooling before knowing which markets even
have a real shrinkage-sensitive probability path (a fact this session
has not re-verified since the restarts) would be exactly the kind of
premature, disconnected-from-data work the standing "do not manufacture
work" instruction warns against. When this priority's turn comes, the
first step is re-locating which probability paths actually have a
tunable shrinkage parameter today (likely largely unchanged from prior
segments' findings, but not re-verified here), not writing sweep
infrastructure blind.

## Bottom line

Two of three (CI-lower-bound, and the underlying equal-volume machinery
market specialization needs) are already real and tested. The market-
specialization RUNNER script itself and the shrinkage harness are the
two genuinely outstanding items, both correctly sequenced behind the
disagreement equal-volume test (which is still blocked on the canonical
rebuild) rather than built speculatively ahead of it.
