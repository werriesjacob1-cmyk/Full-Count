# Selection information-loss audit -- what's computed then discarded, before final board selection

Written 2026-08-25 while the canonical backfill was rebuilding, per the
standing instruction to prepare (not fabricate) future research surface
area. Every claim below was checked directly against the current code
(line references given), not assumed from memory of an older version.

## HIGH VALUE

### 1. `line_options` -- the full alternate-line curve (ALREADY EXPLOITED, not a new gap)

`generate_picks.py`'s `_keep_options()`/`_pick_line()` (around line 4800)
compute the full probability curve across every plausible threshold for a
batter, then collapse to ONE board line via `_pick_line()`. This was the
original discovery motivating `backtest/candidate_funnel_logger.py`
(built earlier this session) -- `c["line_options"]` is already attached
to every candidate and already captured verbatim in the funnel logger's
`decision.alt_lines`/`decision.n_alt_lines`. Listed here for completeness
of the audit, not as new work.

### 2. `quality_control()`'s rejection reason is SHORT-CIRCUITED -- a real, newly-verified gap

Checked directly (`generate_picks.py:6249` on): `quality_control()`
evaluates its checks as a chain of `if reason is None and ...:` blocks --
the OPENER/BF-evidence check, then the lineup-confirmation check, then
(not fully traced here, but same pattern) rain/other checks. **Once
`reason` is set by the first failing check, no subsequent check runs for
that candidate.** A candidate rejected for "used as an opener" might
ALSO have an unconfirmed lineup or a rain risk -- that information is
never computed, so it's not recoverable even from the funnel logger's
`decision.quality_control_reason` field (which stores exactly this one
short-circuited string, verified at `candidate_funnel_logger.py:314`:
`qc_index[...] = ("rejected", c.get("qc_reason"))`).

**Why this matters for the planned "gate regret" metric (Priority 7 of
the current directive)**: a true per-gate regret analysis wants to know,
for every rejected candidate, EVERY reason it was rejected, not just the
first. As currently built, QC-level regret analysis can only cleanly
answer "candidates whose FIRST failing QC check was X," not "candidates
that would have failed QC for reason X specifically, among all their
reasons."

**Important, already-verified counterpoint**: this gap is narrower than
it first appears. The LATER, Top-Pick-funnel gates (`has_prob`,
`meets_prob_floor`, `evidence_ok`, `lineup_ok`, `has_odds`,
`clears_value`, `data_fresh` -- `recommendation_funnel.gate_trace()`,
line 77) are NOT short-circuited -- every gate is evaluated independently
for every candidate (verified by reading `gate_trace()`'s body directly:
each boolean is computed from the candidate's own fields, with no early
return). And `candidate_funnel_logger.py`'s `run_live_snapshot()` already
calls `classify_with_trace()` on the FULL `candidates` list -- including
ones `quality_control()` will separately reject -- confirmed at
`candidate_funnel_logger.py:317-319` (`for c in candidates: ...
gate_traces[...] = funnel.classify_with_trace(c)`, iterating BEFORE the
kept/rejected/assumed split is even used for indexing). **So true
multi-gate regret analysis is already fully supported for every
Top-Pick-funnel gate on every candidate, rejected or not** -- only the
earlier, QC-level short-circuiting (opener/lineup/rain) loses
information.

**Possible future experiment (not built)**: change
`quality_control()` to keep evaluating and collect ALL applicable
rejection reasons for a candidate (not just the first), OR compute the
same checks independently in a research-only wrapper (mirroring
`gate_trace()`'s non-short-circuiting pattern) without touching
production's own single-reason behavior. The latter is lower-risk (no
change to a function multiple other things already depend on for its
current single-string contract).

## MEDIUM VALUE

### 3. Market-price attachment is skipped in the prospective funnel logger -- known, documented, not yet built

`candidate_funnel_logger.py`'s own docstring already states every
record's `market` section is null this run (no `fd.attach_market_prices()`
call) -- not a new finding, restated here only because it directly blocks
one specific future experiment: comparing the model's OWN preferred
alternate line (from `line_options`, already captured) against what
FanDuel actually posts. That specific comparison -- "did the model's
ideal line differ from what was offered" -- is not answerable until
market attachment is added to the prospective logger.

### 4. `cat_environment` is a structurally constant field in canonical history -- confirmed, not a bug

Already found and documented in
`backtest/disagreement_priority1_2_3_2026-08-25.md`: `cat_environment`
is always exactly 50 in every canonical row, because the main backfill
runs with `--no-weather`. Restated here because it means any FUTURE
canonical rebuild that includes real weather (`--no-weather` omitted)
would unlock a currently-unusable component for disagreement/context
research -- a real, concrete lever for expanding this research thread's
data richness, not attempted this pass (would require a much slower
backfill; weather fetches are exactly the kind of per-date external call
this session already found expensive).

## LOW VALUE / explanation-only

### 5. `status_reasons` (human-readable strings on the live board)

`recommendation.classify_recommendation()`'s `status_reasons` field
(present on live candidates, captured in the funnel logger's
`decision.status_reasons`) is prose meant for the board UI, not a
structured signal -- already captured verbatim, but not more useful for
quantitative research than the `gates`/`blocking_gate` structured fields
that sit right next to it. No further work suggested here.

## What was explicitly NOT found to be a gap (checked, not assumed)

- The `signals` dict (every named pregame signal -- `platoon`,
  `lineup_slot`, `days_rest`, `getaway_day`, etc., the exact set this
  session's opportunity/residual-opportunity research already used) is
  fully captured in both backtest rows (`to_row()`) and the prospective
  funnel logger (`evidence.signals`). Nothing lost here.
- The five `cat_*` category components (`cat_matchup`/`cat_recent_form`/
  `cat_environment`/`cat_baseline_skill`/`cat_context`) that this
  session's disagreement research depends on are fully captured in both
  places too -- verified directly at `candidate_funnel_logger.py`'s
  `evidence` section, not assumed.
- `prob_ci` (the confidence interval this project's own CI-lower-bound
  future work, Priority 10 of the current directive, will need) is
  already captured in the funnel logger's `prediction.prob_ci`.

## Summary for future prioritization

Only ONE genuinely new, actionable gap was found (item 2, QC-level
short-circuiting) -- everything else audited either turned out to already
be captured (signals, cat_* components, prob_ci) or was already a
documented, scoped, not-yet-built follow-up (market-price attachment,
weather/`cat_environment`). This is itself a useful result: the
prospective candidate-funnel infrastructure built earlier this session is
more complete than assumed going in -- it does not need major new
instrumentation to support most of the future research this directive
lists (gate regret at the Top-Pick-funnel level, disagreement, CI-based
ranking), only the QC-level multi-reason gap if that specific angle turns
out to matter once real prospective volume accumulates.
