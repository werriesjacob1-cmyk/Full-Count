# Detail sheet data audit — 2026-08-25

Required before touching the detail sheet's design (per explicit
directive: "DO NOT COLOR OR LABEL A COMPONENT POSITIVE/NEGATIVE UNTIL ITS
SEMANTICS ARE VERIFIED"). Read `score_batter()`/`score_pitcher()` in
`generate_picks.py` in full, plus `dashboard/build_dashboard.py`'s
`clean()` (the actual payload field boundary the frontend can ever see).

## The critical finding: raw `cat_*` component values are NOT safely
## gradable as Supportive/Concern by simple thresholding

`score_batter()`'s real, currently-shipping fitted formula (promoted
2026-08-14, replacing the original hand-set 35/25/15/15/10 split):

```
score = clamp(matchup*0.04 + form*0.03 + env*0.20 + skill*-0.09 + context*0.64)
```

`score_pitcher()`'s real fitted formula:

```
score = clamp(matchup*0.11 + form*-0.16 + env*0.15 + skill*0.48 + context*0.10)
```

Two components carry a **negative** fitted weight -- `skill` for
batters, `form` for pitchers -- and which component is inverted differs
by market type. This means a raw `cat_skill` value of 75/100 (an
objectively strong hitter) is very close to model-neutral, or even a
mild net negative, once its actual (negative) weight is applied --
the opposite of what a naive "value > 50 = Supportive" badge would
imply. The comment at `score_batter()`'s formula explains why: "season-
level power/contact stats are largely already priced in by the market
itself." This is real, subtle, and exactly the kind of signed-but-
counterintuitive relationship the project's own prior Weston Wilson
incident (explanation text with the wrong sign) already burned us on
once.

**Consequence for this pass**: a "THE CASE" component-grade table
(Opportunity/Matchup/Recent/Context: Supportive/Mixed/Concern) is NOT
built this pass. Building it honestly would require exposing the actual
fitted weights (which differ per market family and are Python constants
today, never persisted per-candidate) so the frontend could compute a
a real signed contribution -- real, additive, non-trivial backend work
that risks getting the semantics wrong under this pass's time budget.
Per the directive's own explicit fallback ("if defensible labels are
not currently possible, a better version may be... another
understandable representation based on actual semantics"), this pass
instead surfaces only facts that are safe to state without needing the
fitted-weight sign: `why[]`/`watchouts[]` text (already correctly
signed by the SAME code that computes score), and a new plain
`OPPORTUNITY` fact (batting order) that needs no weight to interpret.

## Field-by-field map (what actually reaches the live dashboard payload)

Source: `dashboard/build_dashboard.py`'s `clean()` (the payload
boundary) cross-referenced against `score_batter()`/`score_pitcher()`
(what's computed at all).

### A. Real directional evidence (safe to show as-is, already correctly signed)
- `why[]` (payload caps at 4) -- positive reasons, generated inline by
  the same code that computes `score`, sign already correct (platoon,
  matchup exploit, recent form, environment, context, sharp money,
  regression signal all route through here with their real sign baked
  in).
- `watchouts[]` (payload caps at 2) -- negative/caution reasons, same
  guarantee (thin sample, BABIP-driven cool-off risk, IL/callup
  uncertainty, weather disagreement, public-money fade).
- `status_reasons[]` (payload: **uncapped**, already present) -- the
  real gate-trace-derived reason a non-Top-Pick candidate isn't one
  (`recommendation.classify_recommendation()`'s own `_result()` calls).
  Already plain English, already safe, needs zero new backend work to
  surface as "Why Not a Top Pick?".
- `market_odds` / `market_implied` / `market_edge` / `market_hold` --
  objective market facts, safe.
- `prob_ci` -- objective Wilson-style interval, safe (already
  conditionally rendered as "Not defensible for this line" when absent).
- `lift` / `stable_lift` / `base_rate` -- real, already-signed (higher =
  more favorable vs. the market/league base rate), documented meaning.
- `reliability` / `sample_n` -- evidence-quality facts, safe (already
  has a defensible A/B/C/D definition shipped).
- `lineup_assumed` -- objective boolean (confirmed vs. projected slot),
  safe.
- `game_context[].weather` / `.umpire` / `.away_sp` / `.home_sp` --
  objective per-game facts, already surfaced, safe.

### B. Informational, NOT directionally interpretable without more context
- `score` (0-100 quality score) -- a real number but an already-blended
  composite; showing it as an opaque summary stat (as today's
  "Underlying data" panel already does) is fine, decomposing it into a
  colored breakdown is not (see the critical finding above).
- **`signals.lineup_slot` / batting order** -- computed
  (`_sig(signals, "lineup_slot", order, lineup_context)`,
  `generate_picks.py:1660`) but **not currently exposed in the live
  payload at all**. Safe to add and safe to display as a plain fact
  ("Batting 2nd") since order itself needs no weight-sign judgment to
  state -- it's objectively true regardless of how much it matters to
  score. Added this pass (see below).

### C. Redundant transforms / unsafe to label without more work
- `cat_matchup` / `cat_recent_form` / `cat_environment` /
  `cat_baseline_skill` / `cat_context` -- **not exposed in the live
  dashboard payload at all** (confirmed: absent from `clean()`'s field
  list; these only exist in `backtest/rows_canonical*.jsonl` for
  research). Even if exposed, per the critical finding above, they are
  NOT safely gradable without the fitted weights. Not surfaced this
  pass.

### D. Explanation-only
- `why[]`/`watchouts[]` themselves are the explanation layer -- not
  text describing a separately-shown number, they ARE the authoritative
  reasoning trail. Treated as primary evidence, not decoration.

### E. Unavailable for particular markets (confirmed, not assumed)
- `market_hold`: only real (not null) on genuinely two-sided markets
  (strikeouts/pitcher_outs/nrfi_combined).
- `lift_reference_rate`/`stable_lift`: only real on
  hits_runs_rbis/runs/rbis.
- `prob_ci`: absent when not statistically defensible for that line.
- **Batting order / Opportunity**: batter markets only. No equivalent
  safe pregame workload fact exists for pitcher markets -- confirmed
  earlier this session (`backtest/pitcher_workload_data_audit_2026-08-25.md`):
  no `days_rest`-equivalent pregame pitcher signal is wired into
  `score_pitcher()` at all; only `tto_penalty` is a plausible lead and
  it's sparse (19.7% presence) and not exposed to the payload either.
  OPPORTUNITY is correctly omitted (not faked) for pitcher props.

## What this pass builds as a result

- `OPPORTUNITY`: real batting-order fact for batters, omitted (not
  faked) for pitchers/when unavailable.
- `WHY IT COULD HIT` / `WHY IT COULD MISS`: `why[]`/`watchouts[]`,
  humanized via the existing `humanizeReason()` translator (unchanged),
  plus two additional SAFE structured bullets when real (lineup not yet
  confirmed; evidence graded C/D) -- never fabricated to force symmetry.
  An honest "No major model-side concern beyond normal baseball
  variance" message when watchouts is genuinely empty.
- `WHY NOT A TOP PICK`: `status_reasons[]`, shown only for a real
  non-Top-Pick candidate, reusing the existing reason-categorization
  logic already proven in `topPickGapSummary()`.
- `MODEL VS MARKET`: reformatted as the requested three-line
  Full-Count/Market/Difference block, same underlying fields.
- `EVIDENCE`: promoted from the collapsed "Underlying data" toggle into
  its own named Level-2 section (reliability/sample/CI); raw component
  values (score, base_rate, lift) stay in a `DEEPER DATA` progressive-
  disclosure toggle, unchanged in meaning.
- **NOT built this pass**: "THE CASE" colored component-grade table
  (Supportive/Mixed/Concern) -- see critical finding above.
