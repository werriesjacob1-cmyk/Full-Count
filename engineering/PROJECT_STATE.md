# Full Count Project State

- Last verified: 2026-08-17
- Code verification base: `main` at `fd20785769e1de25581e873317d2b2230389ba13`

This file is the concise map of the system that exists now. Use
`engineering/ENGINEERING_HANDOFF.md` for chronology and
`engineering/AUDIT/README.md` for audit findings.

## Project stage

- Phases 1–4: completed, merged in PR #49, and deployed.
- Phase V: **NOT STARTED**.
- Current work: **Pre-Phase-V full-system audit and hardening**.
- Current priority: make measurement, provenance, reproducibility, persistence,
  and deployment behavior trustworthy before tuning the model from a young
  forward record.

## Current production architecture

Full Count is a deterministic Python analytics pipeline orchestrated by GitHub
Actions. Scheduled jobs collect MLB and sportsbook data, generate and grade
recommendations, commit generated artifacts to the repository, build a static
dashboard into `docs/`, and deploy that directory to GitHub Pages.

| Layer | Current implementation | Primary outputs |
|---|---|---|
| Data collection | `mlb_daily.py`, `mlb_sources.py` | `output/mlb_daily_*.txt`, `output/run_log_*.json`, in-memory tables used by scoring |
| Candidate generation and scoring | `generate_picks.py` | Scored candidate dictionaries and daily pick artifacts |
| Probability and price math | `prop_probability.py` | Per-line probabilities, intervals, implied probability, ROI, Kelly context, de-vig helpers |
| Sportsbook pricing | `odds_fanduel.py`, `odds_snapshot.py`, `prop_snapshot.py` | Attached FanDuel prices, `data/odds/*.json`, `data/props/*.json` |
| Recommendation policy | `recommendation.py` | `top_pick`, `lean`, `value`, or `neutral`, plus provenance metadata |
| Daily persistence | `generate_picks.write_json()` and workflow commits | `output/picks_YYYY-MM-DD.json`, rendered boards, value-board files |
| Grading and records | `grade_results.py`, `grade_value.py` | `results/grades_*.json`, `results/history.json`, `results/value_screen_record.json`; deployment-proven public Top Pick population from `data/public_top_picks/registry.json` |
| Evaluation | `eval_lib.py`, `backtest/`, `model_health_report.py` | Backtest rows/reports, calibration and market benchmarks, health output |
| Challenger evaluation | `champion_challenger.py` | Shadow files under `data/challengers/` when challengers run |
| Dashboard | `dashboard/build_dashboard.py`, `dashboard/live_state.py`, `dashboard/static/` | Full snapshot in `docs/data.json`; field-level live overlay in `docs/live.json`; static application assets |
| Deployment | `.github/workflows/dashboard-deploy.yml`, `dashboard/prepare_pages_artifact.py` | Normalized and contract-verified GitHub Pages artifact, deployment manifest, and post-deploy exposure confirmation from newest `main` |

## Data ingestion sources

The active source families visible in the production code are:

- MLB Stats API for schedules, game identifiers, probable pitchers, lineups,
  game status, box scores, and other MLB-owned data.
- MLB.com and Rotowire in the lineup fallback chain.
- FanGraphs and Baseball Savant/Statcast, including `pybaseball`, for season,
  recent-form, batted-ball, pitch, and player-quality data.
- UmpScorecards and FantasyInfoCentral for umpire-related context.
- Open-Meteo and the National Weather Service for weather cross-checking.
- Covers.com for public-betting context.
- FanDuel's public web-application endpoints for sportsbook markets and prices.

Fallbacks and availability vary by field. The implementation in
`mlb_daily.py`, `mlb_sources.py`, and `generate_picks.py` is authoritative;
missing source data must remain missing rather than being treated as zero or
positive evidence.

## Candidate generation

`generate_picks._build_and_score()` builds the live input bundle and
`generate_picks.build_candidates()` scores the slate. Current customer-facing
market families include hits, total bases, home runs, runs, RBIs,
hits+runs+RBIs, singles, doubles, triples, stolen bases, pitcher strikeouts,
combined starter strikeouts, pitcher outs, combined NRFI/YRFI, and hard-hit
"Laser" markets. A walk scorer exists but is deliberately disconnected from
`build_candidates()` because the supported FanDuel market was not available.

The candidate pool, the best-in-category board, and the recommendation status
are separate concepts. `select_best_by_category()` can surface the strongest
available candidate in a market without making that candidate a Top Pick.

## Scoring and features

The five general score categories originated in a historical, hand-set shared
synthesis scaffold: 35% matchup, 25% recent form, 15% environment, 15%
baseline skill, and 10% context. That shared split is **not** the current live
general formula.

The promoted live general formulas are different for batters and pitchers:

| Component | Batter | Pitcher |
|---|---:|---:|
| Matchup | 0.04 | 0.11 |
| Recent form | 0.03 | -0.16 |
| Environment | 0.20 | 0.15 |
| Baseline skill | -0.09 | 0.48 |
| Context | 0.64 | 0.10 |

`generate_picks.score_batter()` and `generate_picks.score_pitcher()` apply
those formulas, and `backtest/fit_score_weights.py` mirrors them as
`CURRENT_WEIGHTS_BATTER` and `CURRENT_WEIGHTS_PITCHER`. Specialty-market
scorers can use their own market-specific formulas and are not represented by
the table above.

The concrete signals include platoon and pitch-arsenal interactions, recent
contact or strikeout form, bat-speed and times-through-order information,
park/weather, workload, lineup slot, bullpen context, umpire context, and
other market-specific inputs. General batter/pitcher candidates record the
five component values used to reconstruct their formula, while signal coverage
remains specific to each scorer and the data available for that candidate.

`generate_picks.apply_signal_weights()` can adjust the live quality score from
forward signal measurements in `results/signal_measurement.json`. It is
intentionally excluded from the historical backtest path because today's
settled signal trust would leak future information into historical dates.
The resulting score participates in the `MIN_QUALITY_SCORE` gate and diagnostic
ordering. These score weights are not the final betting probability,
calibration, sportsbook price/value assessment, or recommendation policy;
those are separate downstream layers. Input-quality rejection and
assumed-lineup handling live in `generate_picks.quality_control()`.

## Probability generation

`prop_probability.py` owns the probability distributions and betting math.
`generate_picks.attach_hit_probabilities()` combines empirical, shrunk player
rates with modeled distributions where both exist; the current general blend
weights empirical probability at 0.60. Specialty markets may use their own
documented empirical or distribution basis.

The pipeline stores the selected line's probability, basis, interval when
available, and the fuller per-market line curve used for pricing and category
selection. `MIN_LINE_PROB = 0.60` governs normal recommended-line selection,
but markets that are inherently lower probability can still exist on category
or value research surfaces. A probability is not itself proof that a posted
price is valuable.

## Calibration

Calibration code lives in `backtest/calibration.py`. Production loading occurs
through `generate_picks.load_calibrator()` and currently prefers the
per-market artifact `backtest/calibrators_by_market.json`. At the verification
base, that file contains Platt calibrators for:

- `hits`
- `hits_runs_rbis`
- `strikeouts`

The loader also contains an optional pooled fallback path at
`backtest/calibrator.json`, but no file exists at that path on the verified
`main`. This fallback remains an audit subject, not an assumed production fit.

`backtest/refit_calibrators.py` performs time-based train/held-out evaluation
and only promotes a market fit that clears its predeclared evidence gates. The
weekly workflow writes the per-market artifact and a recheck report. One
known metadata inconsistency must remain visible until audited: the current
`strikeouts` entry is stored under the correct market key but its embedded
`meta.prop_type` is `all`.

## Sportsbook pricing and market comparison

`odds_fanduel.py` fetches and attaches live FanDuel markets. The daily value
screen is produced by `value_board.py`; hourly captured prices are persisted
under `data/props/` for forward settlement by `grade_value.py`.

`eval_lib.market_probability()` uses exact two-sided no-vig probability when
both sides or an already de-vigged value are persisted. One-sided production
recommendation checks currently use `prop_probability.value_verdict()` and
`market_agreement()`, including their documented hold assumptions. Whether
the recommendation path should consume the exact two-sided value when it is
available is an open audit question.

## Recommendation classification

`recommendation.py` is the only policy source allowed to classify a candidate
as Top Pick, Lean, Value, or Neutral. It also owns the current provenance
versions:

- model: `2026.08.15`
- selection policy: `1.0.0`
- calibration: `1.0.0`
- features: `1.0.0`

A Top Pick currently requires at least 60% model probability, reliability A or
B, a non-assumed lineup, real posted odds, a `BET` result from the price/value
test, and board-level freshness. The price/value test uses the lower end of a
line-scoped probability interval when one is present. In the current code,
an absent interval does **not** itself fail the value verdict; that behavior is
an explicit Pre-Phase-V audit item rather than something this document
silently describes as already solved.

The freshness function currently substitutes board generation time when the
price timestamp is missing. That implementation differs from its fail-closed
documentation and is also an explicit audit item.

## Persistence and prediction history

Daily prediction provenance is stored both at the board level and on persisted
rows in `output/picks_YYYY-MM-DD.json`, including version identifiers, git SHA
when available, prediction timestamp, price timestamp, and board timestamp.
Grades are written separately under `results/`.

The repository does not yet contain the planned full Prediction Receipts event
ledger. It does contain a deliberately smaller lifecycle registry at
`data/public_top_picks/registry.json`. Only a successful Pages deployment may
establish a public Top Pick in that registry. The entry stores one immutable
first-exposure snapshot and deployment provenance for the canonical wager;
local qualification or a repository commit is not public exposure. The
registry is the durable public Top Pick grading population even when a later
`output/picks_YYYY-MM-DD.json` or dashboard board no longer contains the wager.

`published_top_pick_at`, `publication_artifact_id`, and
`publication_snapshot` in a dashboard artifact are derived from registry
proof. They are lifecycle facts, not a transition log and not a substitute
for the future receipt/ledger. A bounded rollout imports only Top Picks proven
to exist in the currently deployed public artifact; historical archives are
never guessed into public exposure. Daily generated files remain separate
artifacts. No engineer may silently rewrite historical recommendations.

## Dashboard build

`dashboard/build_dashboard.py` is the sole writer of the complete canonical,
deduplicated `props` array in `docs/data.json`. Candidate generation remains
pregame-only. At commit/retry time, `dashboard/finalize_dashboard_state.py`
rereads current `main`, the durable publication registry, and the live overlay;
it refetches game state and enforces scheduled `game_start` as an absolute,
conservative first-publication cutoff. Only an exact registry-proven public Top
Pick may survive first pitch, a full rebuild, and UTC date rollover. A started
prop that was never public cannot be created or promoted.

Canonical identity schema v2 includes game, stable player/game/combination
subject, market/stat, threshold, and side. Participants are sorted for the
commutative combined-starter strikeout market. Duplicate settlement identities
and supplied IDs inconsistent with their row fail closed. A bounded rollout
normalizes legacy IDs at every writer/deployment boundary; current-schema
identity corruption is not silently rewritten. The build never clears or
writes `docs/live.json`.

Static source assets live in `dashboard/static/` and are copied to `docs/`.
The client implements the Today, All Props, Games, Performance, and Watchlist
views with progressive research sheets. It consumes three independent facts:
`recommendation_status`, `game_state`, and `settlement_state`. Pregame/open is
normal, live/open is yellow, provisional or final hits are green, misses are
red, and void/ungraded are explicit. It hides started non-published props,
restores the immutable publication snapshot after first pitch, and reapplies a
newer live overlay after a full-board poll.

`dashboard.load_track_record()` derives separate current Top Pick and legacy
main-board views from fields in `results/history.json`; `history.json` does
not persist nested `current` and `legacy` objects. At the verification base,
the current Top Pick sample is zero and the historical rows are still recorded
as `unclassified`, so the site must not imply a proven current track record.

## Live updates

The lifecycle schema separates:

- recommendation state: `top_pick`, `lean`, `value`, `neutral`;
- game state: `pregame`, `live`, `delayed`, `suspended`, `postponed`, `final`,
  `cancelled`, `unknown`;
- settlement state: `open`, `provisional_hit`, `hit`, `miss`, `void`,
  `ungraded`.

Result authority is `none < live_observation < official_final`. A threshold
proved during play is `provisional_hit`, not an immutable final result. The
authoritative final feed may confirm or correct it; stale live evidence cannot
override an official final result. Settlement state, actual, reason, source,
authority, and observation timestamp merge as one logical fact.

`dashboard/refresh_prices.py` observes each supported market family as
`MATCHED`, `NOT_POSTED`, `FETCH_FAILED`, or `IN_PLAY`. A matched quote replaces
the quote and advances that family's successful observation time. A successful
exact absence clears bettable fields. A fetch failure preserves the prior quote
without freshness-stamping or reclassification from false absence. Started
markets freeze. The final publication gate refetches game status and also
rejects new publication at or after scheduled `game_start`; unknown fails
closed. `recommendation.py` remains the unchanged classifier.

`dashboard/refresh_grades.py` advances registry-proven public Top Picks by
direct `game_pk` feeds. It uses provisional green hits only for mathematically
definitive live overs. Misses and all unders wait for authoritative Final.
Action eligibility is separate from threshold grading and follows the
documented FanDuel-US rules in `dashboard/settlement_rules.py`. Because the
repository has no configured jurisdiction, a case on which inspected state
rules differ remains ungraded; unsupported special-market rules do as well.
Source failure preserves last-known-good state.

`dashboard/live_state.py` performs atomic stable-ID writes, strict UTC-aware
timestamp validation, per-field recency merges, result-authority comparison,
and bounded compaction. An unresolved/provisional/suspended/postponed or not-
yet-durable result is never removed merely because the UTC date changed.

`.github/workflows/dashboard-live.yml` is the sole `live.json` writer. It
coalesces obsolete five-minute observations, checks out latest `main`, runs
grade then price with failure isolation, and retries by rereading/semantically
merging current state. Significant scheduled or lineup-triggered full builds
use the separate non-cancelling `dashboard-full-rebuild` true queue and finalize
against current `main`; a live observation cannot displace one. The dedicated
Pages workflow stages and normalizes newest `main`, requires a 15-minute
publication window inside a 10-minute deployment timeout, verifies the complete
schema, deploys, then idempotently records exposure. A repository commit alone
is not public delivery.

## Grading

`grade_results.py` retains the established canonical daily-picks and legacy
history semantics. Separately, it grades immutable public snapshots from the
publication registry into `public_top_picks` within each daily grade artifact.
Canonical picks and registry snapshots do not double-count. Public entries are
idempotent by canonical ID, absent-board wagers remain gradeable, voids are
excluded from hit/miss denominators, unavailable games remain retryable, and
authoritative corrections replace the same logical result rather than adding a
duplicate. Direct `game_pk` lookup keeps a prior-slate game gradeable after UTC
midnight. Pre-registry dates are not inferred as public Top Picks.

`dashboard/refresh_grades.py` is the intraday lifecycle path; the daily grader
is the durable historical path. `grading_sources.py` provides a lightweight
requests/MLB-StatsAPI boundary and lazy Statcast dependency, avoiding the full
`mlb_daily.py` import chain in the five-minute job. `grade_value.py` separately
settles the value screen against captured prices.

## Evaluation and backtesting

`eval_lib.py` supplies shared Brier score, log loss, calibration tables,
realized ROI, sample-size labels, and market-probability extraction.
`backtest/engine.py` performs point-in-time replay with explicit lookahead
guards and writes the schema defined in `backtest/SCHEMA.md`. Associated tools
cover signal measurement, fitted-weight comparison, calibration audit,
market benchmarking, model-vs-market information tests, segment reporting,
and threshold sensitivity.

The backtest cannot reconstruct every live production input. Historical line
movement, some market signals, point-in-time sprint speed, some leaderboard
fields, and the live-only forward signal-weight adjustment have documented
coverage gaps. Backtest results therefore test the reconstructable historical
system and must never be presented as identical to live forward performance.

## Champion/challenger

`champion_challenger.py` registers probability challengers outside the live
critical path and writes shadow predictions under `data/challengers/`.
Promotion is never automatic. The current predeclared minimums include at
least 100 graded rows across 14 days plus Brier/log-loss improvement,
non-worsening calibration, and no loss relative to the market benchmark.

## GitHub Actions and deployment

| Workflow | Current role |
|---|---|
| `mlb-daily.yml` | Grades prior picks, measures signals, generates picks/value output, runs the full data package, and commits artifacts on multiple daily windows or manual dispatch |
| `odds-snapshot.yml` | Captures odds and player-prop prices hourly |
| `lineup-watch.yml` | Checks lineups every ten minutes, persists watch state, and triggers a full dashboard rebuild when needed |
| `dashboard-refresh.yml` | Sole `data.json` writer; rebuilds the board and static assets on two-hour/lineup-triggered windows |
| `dashboard-live.yml` | Sole `live.json` writer; failure-isolated grading then pregame repricing every five minutes |
| `dashboard-deploy.yml` | Verifies and deploys newest `main:docs/` after either dashboard writer completes |
| `calibration-recheck.yml` | Runs the weekly held-out per-market calibration recheck and commits promoted fits/report |
| `test.yml` | Installs `requirements.txt` and runs every root `test_*.py` on pushes and pull requests |

Dashboard state ownership is disjoint (`data.json` versus `live.json`) and the
two state writers share one non-cancelling concurrency lane. Pages deployment
has a separate latest-wins concurrency group and always checks out current
`main`, so an older completed writer cannot intentionally deploy its stale
checkout. Other generated-artifact workflows retain their existing ownership
and retry/rebase behavior.

## Important sources of truth

| Concern | Source of truth |
|---|---|
| Engineering rules | `AGENTS.md` |
| Current architecture/state | `engineering/PROJECT_STATE.md` |
| Work chronology and unresolved findings | `engineering/ENGINEERING_HANDOFF.md` |
| Audit process/findings index | `engineering/AUDIT/README.md` |
| Scoring and candidate assembly | `generate_picks.py` |
| Probability/value math | `prop_probability.py` |
| Recommendation status and version identifiers | `recommendation.py` |
| MLB data acquisition | `mlb_daily.py`, `mlb_sources.py` |
| FanDuel market acquisition | `odds_fanduel.py` |
| Persisted daily recommendations | `output/picks_YYYY-MM-DD.json` |
| Durable grade record | `results/grades_*.json`, `results/history.json` |
| Forward price/value record | `data/props/*.json`, `results/value_screen_record.json` |
| Calibration implementation/artifacts | `backtest/calibration.py`, `backtest/calibrators_by_market.json`, `backtest/calibration_recheck_report.json` |
| Backtest contract | `backtest/SCHEMA.md`, `backtest/engine.py` |
| Dashboard payload construction | `dashboard/build_dashboard.py` |
| Dashboard live-state schema/merge | `dashboard/live_state.py` |
| Lightweight grading sources | `grading_sources.py` |
| Dashboard client source | `dashboard/static/index.html`, `dashboard/static/app.css`, `dashboard/static/app.js` |
| Published dashboard artifacts | `docs/` |
| Automation/deployment behavior | `.github/workflows/*.yml` |

When documentation and executable behavior disagree, record the discrepancy
and treat the executable code/data as evidence of current behavior. Do not
quietly edit history or elevate an audit hypothesis into a fact.
