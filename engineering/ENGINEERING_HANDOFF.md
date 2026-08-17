# Full Count Engineering Handoff

This is the append-only chronological engineering record. Read
`engineering/PROJECT_STATE.md` first for the current system map. Do not erase
prior entries when later evidence corrects them; append the correction and
link the relevant code, commit, test, or PR.

# Historical handoff

## Product goal

Full Count should become an unusually transparent, baseball-native MLB betting
research system emphasizing:

- calibrated probability
- strong underlying baseball reasoning
- sportsbook price/value
- honest track record
- deep but usable explanations
- market-specific research
- high-quality mobile UX

## Phase 1

Deep audit identified recommendation-layer problems including low-probability
picks being presented too confidently, mismatched confidence intervals, mixed
performance populations, weak uncertainty semantics, and historical/versioning
issues.

## Phase 2

Recommendation architecture rebuilt around:

- Top Pick
- Lean
- Value
- Neutral

A real 60% Top Pick probability floor was introduced. Mismatched market/CI
problems were repaired. Current performance was separated from inappropriate
legacy metrics.

Key historical commit: `0b83b28`

## Phase 3

Evaluation/validation framework added:

- model/policy/calibration/feature versions
- git SHA prediction metadata
- historical integrity tiers
- shared Brier/log-loss/calibration/ROI primitives
- exact two-sided no-vig market probability where available
- market benchmark
- calibration audit
- model-vs-market information testing
- champion/challenger framework
- threshold sensitivity
- model health reporting

Key historical commit: `407d28b`

## Phase 4

Website rebuilt into a static analytics application with Today, All Props,
Games, Performance, and Watchlist.

The rebuild introduced a flat canonical props payload, a small `live.json`
delta architecture, progressive-disclosure research sheets, current-vs-legacy
Performance separation, and mobile/accessibility improvements.

Key historical commit: `5de1f66c14383151cbaa155e0630ef2e718659cf`

Merged in PR #49.

Merge commit: `a8833c65259ca043d91317a181642db3124a80dc`

## Product improvements planned before Phase V

Immediate:

- show odds immediately on every prop card
- show exact bet/threshold
- dedicated leaderboards for each market such as 1+ Hits, 2+ TB, HR, RBI,
  Runs, SB, Ks, and Outs
- category rank context (#1 of N)
- keep Top Picks mathematically separate from merely being best-in-market
- more technical baseball explanations
- entity-aware baseball search
- keep picks visible once games begin
- yellow while live
- green when mathematically won
- red when mathematically lost/final
- official headline performance = Top Picks
- internally track all modeled recommendations

Planned differentiated features:

- Why #1?
- Pitch Arsenal Matchup Engine
- Matchup DNA
- historical matchup archetypes
- Path to the Prop / Path to Cash
- Baseball Edge vs Market Edge
- Full Count vs The Book
- Countercase / What Could Go Wrong
- Fade Board
- Better Bet
- What Changed?
- rank/probability movement attribution
- Prediction Receipts
- immutable recommendation history
- Postgame Autopsy
- Full Count Slate Stories
- Slate Map / power/contact/whiff environments
- Prop Lab / sensitivity analysis
- Model Fingerprints
- rich Player pages
- rich Game pages
- My Card correlation analysis
- future CLV/odds movement

These features are not permission to implement all of them in a single task.

## Pre-Phase-V audit findings already identified

Treat these as hypotheses/findings to independently verify, not unquestionable
truth and not blanket authorization to change production:

1. `live.json` repository updates may not reach deployed GitHub Pages without
   a new Pages artifact deployment.
2. The live grading workflow has failed because of reduced dependency
   installation/import chains.
3. Individual stale prices may survive a fresh board timestamp when a
   specific FanDuel market disappears.
4. Published Top Picks do not yet have a fully immutable recommendation
   lifecycle/ledger.
5. Started Top Picks can disappear during later full dashboard rebuilds.
6. Freshness logic may substitute board generation time when price timestamp
   is unavailable.
7. Recommendation policy may ignore exact two-sided de-vig market information
   available elsewhere.
8. Recommendation uncertainty gating may allow Top Picks without a defensible
   probability interval.
9. Multiple workflows modify overlapping generated JSON under separate
   concurrency groups.
10. Backtest cannot reproduce several live inputs and must not be treated as
    identical to live production.
11. Some signal-evaluation reconstruction appears stale relative to currently
    promoted production scoring weights.
12. Forward signal trust may be too pooled across prop types and may
    underestimate dependence between correlated observations.
13. Calibration metadata can incorrectly label a per-market fit as
    `prop_type=all`.
14. A dormant pooled calibrator fallback conflicts with newer project policy.
15. The current PA-distribution approximation has documented variance
    limitations.
16. Search currently matches team names against the full matchup string,
    causing searches such as Phillies to surface opposing players.
17. The public track record of the rebuilt Top Pick architecture is still
    young and should not drive premature model tuning.

## Important current principle

Do **not** optimize model parameters because of a few live days. First make
measurement and reproducibility correct.

## 2026-08-17 — Establish repository-native engineering memory

Agent: Codex

Branch: `pre-phase-v/engineering-memory`

Commit(s): Documentation-foundation commit containing this entry; resolve the immutable
SHA from this branch or its PR history.

PR: Draft PR titled **Pre-Phase-V: establish shared engineering memory** against
`main`.

Objective: Create only the shared engineering-memory/documentation foundation before any
Pre-Phase-V production hardening.

What I inspected:

- Repository/default-branch metadata and the full `main` tree.
- Existing root, `docs/`, `.claude/`, `.github/workflows/`, `dashboard/`,
  `backtest/`, `results/`, and key data paths.
- `README.md`, all eight workflows, the Phase 4 merge/history, and the current
  dashboard deployment path.
- Current ingestion, scoring, probability, calibration, recommendation,
  persistence, dashboard, grading, evaluation, and champion/challenger code.
- Current calibration artifacts and `results/history.json`.

What I found:

- `AGENTS.md`, `engineering/PROJECT_STATE.md`, and
  `engineering/ENGINEERING_HANDOFF.md` did not exist on `main`.
- `docs/` is the GitHub Pages publication root; the full directory is uploaded
  as the Pages artifact. Engineering memory therefore belongs under
  `engineering/`, not `docs/`.
- The working branch was synchronized from `main` at
  `7a42ae0e8aa9de7e6006fefbe11bb71b4e390290` immediately before creation.
- The production calibration file contains per-market entries for `hits`,
  `hits_runs_rbis`, and `strikeouts`; the `strikeouts` entry's internal
  metadata says `prop_type=all`.
- Production code still supports an optional pooled
  `backtest/calibrator.json` fallback, but that file is absent on the verified
  tree.
- `results/history.json` has no graded Top Picks under the rebuilt policy at
  this snapshot. The dashboard derives current-vs-legacy views rather than
  reading nested `current`/`legacy` objects from that file.
- Historical descriptions cite different test counts because they describe
  different moments or scopes. No canonical test count was asserted without
  rerunning the complete suite.

What I changed:

- Added the short root `AGENTS.md` rulebook.
- Added `engineering/PROJECT_STATE.md` as the current technical map.
- Added this append-only `engineering/ENGINEERING_HANDOFF.md`.
- Added `engineering/AUDIT/README.md` as the Pre-Phase-V audit index and
  severity/authorization contract.

Architectural decisions:

- Engineering documentation stays outside `docs/` so it is not part of the
  dashboard's Pages artifact.
- `AGENTS.md` is a map/rulebook, not a historical archive.
- `PROJECT_STATE.md` records verified current behavior;
  `ENGINEERING_HANDOFF.md` records chronology; `engineering/AUDIT/` records
  evidence-backed findings.
- Supplied audit findings remain explicitly provisional until reproduced.

Tests added: None. This task adds documentation only.

Test results:

- Verified all four new documents against the current repository tree and
  cited implementation files.
- Verified the change scope contains only the four requested documentation
  files.
- Production tests were not run because no executable, generated, workflow,
  model, recommendation, or dashboard behavior changed; this is not a
  production PR under rule 20.

Behavior intentionally unchanged:

- Data ingestion, candidate generation, scoring, probability, calibration,
  pricing, recommendation classification, persistence, grading, evaluation,
  workflows, deployment, and website behavior.
- `README.md`, generated dashboard files, generated data, and prediction
  history.

Risks / known limitations:

- Repository state changes frequently because automation commits generated
  data to `main`; the branch may require a current-main update before merge.
- Documentation can drift. Future meaningful tasks must update this handoff
  and correct `PROJECT_STATE.md` when behavior changes.
- The audit hypotheses above have mixed verification states; none should be
  presented as a completed root-cause analysis without evidence.

New issues discovered:

- The `strikeouts` per-market calibrator metadata mismatch is present in the
  current artifact.
- The optional pooled-calibrator fallback exists in code while its expected
  artifact is absent and newer refit policy says not to use a pooled fit.
- Live price/grade workflows commit Pages payload changes but do not deploy a
  new Pages artifact themselves.

Recommended next work:

- Begin the audit by converting the highest-risk hypotheses into reproducible,
  severity-classified findings without changing production behavior.
- Prioritize prediction-history integrity, live deployment correctness,
  per-price freshness, and workflow write ownership before model tuning.

Information Claude should know when resuming:

- Phase V has not begun.
- This task intentionally changed documentation only.
- Start with `AGENTS.md`, then `engineering/PROJECT_STATE.md`, then this file.
- Treat `engineering/AUDIT/README.md` as the contract for audit findings.
- Challenge this map when code or data disagrees; append evidence rather than
  silently rewriting historical entries.
