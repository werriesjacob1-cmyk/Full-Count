# Candidate-level decision dataset — feasibility & design (Priority 3)

Prepares the research infrastructure requested for the "which of our own
predictions deserve to be trusted" phase, so `rows_canonical.jsonl` can be
attacked immediately once the main backfill completes. Grounded in the ACTUAL
current schemas (`backtest/SCHEMA.md`, `dashboard/publication_registry.py`'s
`SNAPSHOT_FIELDS`, `recommendation_funnel.py`), not assumed.

## What already exists, by requested category

| category | field | backtest rows (`rows.jsonl`/`rows_backfill.jsonl`) | live registry (`publication_registry.py`) | notes |
|---|---|---|---|---|
| **IDENTITY** | date/game/player/stat/line | ✅ `date`, `game_pk`, `player_id`, `player_name`, `prop_type`, `line`, `needs` | ✅ `game_pk`, `player_id`, `team`, `matchup`, `game_start`, `stat`, `market_side` | Both real, well-covered. |
| | side/threshold | ✅ via `line`/`needs` | ✅ `bet_side`, `direction`, `lean`, `projection`, `prop` | |
| **PREDICTION** | hit_probability | ✅ `predicted_prob` (pre-calibration), `calibrated_prob` (Stage 5, `--apply-policy` only) | ✅ `hit_probability` | |
| | prob_ci / support | partial — `--apply-policy` rows carry `reliability`; raw CI bounds NOT stored per-row | ✅ `prob_ci`, `reliability`, `reliability_note`, `sample_n` | Backtest has the RAW signals to recompute a CI post hoc; doesn't store the computed CI itself today. |
| | shrinkage inputs (n0, prior) | ❌ not stored — only the post-shrinkage `predicted_prob` | ❌ same | **Real gap.** Would need `generate_picks.py`'s shrinkage call site instrumented to record its own inputs, not just its output. |
| | stable_lift/lift/base_rate | ❌ not on backtest rows | ✅ (2026-08-25 registry addition — see `SNAPSHOT_FIELDS`) | Forward-only; no historical backfill possible (base_rate/lift are themselves derived from the very history being built). |
| **MARKET** | odds/implied/edge/hold | ❌ **structurally, permanently absent from backtest rows** — `SCHEMA.md`'s own "THE RULE THAT MATTERS MOST" section states market signals are explicitly out of scope for backtesting (no historical line data before 2026-08-05, and even after, backtest never fetches live FanDuel prices by design) | ✅ `market_odds`, `market_implied`, `market_edge`, `market_hold`, `price_clears` | This is the single largest, permanent gap for pre-2026-08-05 (and really pre-registry) history: there is no way to know what a rejected OR published candidate's market price actually was before the registry started capturing it. Any research question needing real market data is bounded to the registry's own forward window. |
| **EVIDENCE/CONTEXT** | reliability grade | `--apply-policy` only | ✅ `reliability`/`reliability_note`/`sample_n` | |
| | lineup confirmed/assumed | ❌ not on backtest rows (backtest doesn't model lineup-confirmation timing at all — see Priority-adjacent open item "assumed vs confirmed lineup accuracy by tier", still pending) | ✅ `lineup_assumed` | |
| | batting slot / projected PA / opposing SP context / component scores | **partial, structured but incomplete**: `signals` dict carries whatever named signals fired (e.g. `platoon`, `l7_form`, `park_hr_index` — see `SCHEMA.md` example) plus `cat_matchup`/`cat_recent_form`/`cat_environment`/`cat_baseline_skill`/`cat_context` (the 5 raw category components `score_batter`/`score_pitcher` are built from) | ❌ **NOT structured** — the equivalent live data (batting slot, projected PA, opposing SP ERA, L7 EV) exists only as free-text sentences inside `why`/`watchouts`, never as separate numeric fields | Real, actionable gap: backtest rows are MORE structured here than the live registry. A candidate-dataset builder should prefer backtest's `signals`/`cat_*` fields over registry's `why` text for anything quantitative. |
| **DECISION** | selected/rejected + exact gate | ❌ not persisted anywhere historically | ❌ not persisted (only exists as a LIVE, re-derivable computation) | `recommendation_funnel.py`'s `gate_trace()` (built for Item 8, already shipped and tested) computes EXACTLY this — which single gate a candidate failed, in `classify_recommendation()`'s own order — but only as a real-time introspection over `output/picks_{date}.json`, never persisted per-candidate over time. **This is the highest-value, most reusable existing building block for the DECISION layer** — it needs to be called and its output PERSISTED at generation time (or reconstructed from any surviving `output/picks_*.json` archives) rather than re-invented. |
| | board rank / category rank / displaced-by | ❌ not persisted anywhere | ❌ not persisted | Would need instrumentation at `select_best_by_category()`/`build_candidates()` call time — real, not-yet-built work. |
| **OUTCOME** | hit/miss/void + actual stat | ✅ `outcome`, `actual`, `actual_pa`, `actual_ip`, `fair_test` | ✅ (via `dashboard/live_state.py`'s `SETTLEMENT_FIELDS`, referenced in this session's earlier Weston-fix work: `settlement_state`, `result_actual`, `result_reason`) | Both well-covered; different vocabularies (`outcome`∈{0,1} vs `settlement_state`∈{hit,miss,void,...}) — a builder must normalize, not assume they match 1:1. |
| **PROVENANCE** | code_git_sha | ✅ (`backtest/provenance.py` already built as a hard gate on this — see 2026-08-25 work) | ✅ `publication_source_commit`, `publication_run_id`, `publication_deployment_id`, `published_top_pick_at` | |
| | model/calibration/feature version | ❌ fields don't exist on real rows yet (`provenance.py`'s `REGIME_FIELDS` is forward-compatible — inspects them if present — but nothing currently WRITES them) | ❌ same | Real gap; only `code_git_sha` is populated today. Adding explicit version tags is real, scoped, low-risk future work (not attempted tonight — no evidence yet that this granularity is needed beyond `code_git_sha`, and the standing instruction is not to build infrastructure speculatively). |

## Bottom line on feasibility

**A trustworthy, point-in-time-safe candidate dataset can be built TODAY for
the PREDICTION + IDENTITY + OUTCOME layers directly from `rows_canonical.jsonl`
once it exists** — that's the vast majority of what Priorities 5 (pairwise
selection), 6 (fragility), and 8 (market specialization, on the model-output
side) actually need, and none of it is blocked on anything new.

**MARKET-layer research (real odds/edge for rejected candidates) is
permanently bounded to the registry's own forward window** — this is not a
tooling gap to close, it's a real historical-data absence stated plainly in
`SCHEMA.md` already. Priority 7 (source/role certainty) and any question
needing real edge/odds must scope itself to registry-covered dates, or work
with backtest's proxy-only PREDICTION-layer signals and say so honestly.

**DECISION-layer (why was THIS candidate selected over THAT one) is the
biggest real gap, but the hard part — computing the answer — already exists**
(`recommendation_funnel.gate_trace()`). The missing piece is persistence, not
invention. This directly serves Priority 5's core question ("were we
selecting the wrong prop from an otherwise good safe pool") — that
INFRASTRUCTURE (a within-slate multi-candidate comparison) does NOT exist yet
in backtest rows today: `build_candidates()` gives each batter exactly ONE
candidate (see `best_of_category_extras()`'s own docstring — "Hits/
hits_runs_rbis/singles structurally win that competition almost every time
... the other six NEVER produced a backtest row before this"), so the
CURRENT backtest pipeline structurally discards the losing candidates
Priority 5 wants to compare against. **This is the single most important
finding of this feasibility pass**: Priority 5 cannot be answered from
`rows_canonical.jsonl` as currently produced — it needs either (a) a modified
backtest run that keeps ALL within-slate competing candidates per player/game
(not just the one `_pick_line` winner), or (b) reconstructing the competing
set from the already-existing `best_of_category_extras()` mechanism (which
DOES surface the other markets a batter could have qualified under, though
not the same-market alternative candidates `_pick_line` discarded). Flagging
this now, before canonical history lands, so Priority 5 isn't blocked by a
late discovery — the reusable-builder work below designs for this.

## Reusable builder design

`backtest/candidate_dataset.py` (module skeleton, not yet a full historical
build — deliberately deferred until `rows_canonical.jsonl` exists, per the
explicit "don't build the entire gigantic artifact before canonical history
exists" instruction):

- `CandidateRecord` — a plain dict-shaped record type (not a heavyweight
  class — matches this codebase's existing convention of plain dicts +
  schema docs over ORMs/dataclasses) with the 7 top-level sections
  (identity/prediction/market/evidence/decision/outcome/provenance) as
  nested keys, each field EITHER a real value OR an explicit `None` with a
  documented reason — never silently absent.
- `from_backtest_row(row)` — maps one `rows_canonical.jsonl` row into the
  IDENTITY/PREDICTION/OUTCOME/PROVENANCE sections directly (1:1, no
  invention), leaves MARKET/DECISION as explicitly `None`
  (`market_unavailable_reason: "backtest rows carry no historical market
  data by design, see SCHEMA.md"`).
- `overlay_registry_snapshot(record, snapshot)` — for the (rare, forward-only)
  case where a backtest-date candidate also has a real registry publication
  record (date overlap between canonical history and the registry's own
  start), fills in MARKET + the "selected" half of DECISION from the
  immutable snapshot. Never invents data for a rejected candidate this way —
  the registry only ever records what WAS published.
- `overlay_gate_trace(record, gate_trace_result)` — accepts
  `recommendation_funnel.gate_trace()`'s own output structure directly (reuse,
  not reimplementation) to fill DECISION's rejection-reason field when
  available.
- Point-in-time safety: the module takes ALREADY-COMPUTED point-in-time-safe
  inputs (backtest rows, which `verify_no_lookahead()` already covers) and
  performs no new fetches, no new joins across time — it is a pure
  reshaping/overlay layer, so it inherits the existing point-in-time
  guarantees rather than needing to re-prove them.

`test_candidate_dataset.py` (written now, against synthetic fixtures — NOT
real historical data, since `rows_canonical.jsonl` doesn't exist yet):
verifies the 3 functions above produce the documented shape, verifies MARKET/
DECISION fields are explicitly `None`-with-reason (never silently missing)
when no overlay is available, verifies the registry overlay never fabricates
data for a candidate absent from the registry, verifies outcome-field
normalization between backtest's `outcome`∈{0,1} and registry's
`settlement_state` vocabulary.

**Explicitly NOT built tonight**: the actual within-slate all-candidates
capture needed for Priority 5 (requires a real, scoped change to
`backtest/engine.py`'s candidate-generation call, which is production-model
code, not tooling — needs its own careful review before touching, even
though it wouldn't touch scoring/probability math itself). Recorded here as
the concrete next step once canonical history exists and Priority 5 is
actually being executed, not before.
