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
18. Verified scoring-description drift remains outside this documentation PR:
    the `generate_picks.py` module text, component headings, comments, and
    diagnostic copy; `backtest/SCHEMA.md`; `backtest/signals.py`; and some
    adjacent test/engine comments still describe or reconstruct the original
    shared 35/25/15/15/10 scaffold. The live general formulas and their current
    formula tests use separate promoted batter and pitcher weights. Any change
    to executable reconstruction requires its own evidence-backed audit and is
    not authorized by this finding.

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

## 2026-08-17 — Correct canonical live scoring-weight documentation

Agent: Codex

Branch: `pre-phase-v/engineering-memory`

Commit(s): Documentation correction commits on PR #50; resolve the immutable
SHAs from the PR history.

PR: Draft PR #50, **Pre-Phase-V: establish shared engineering memory**.

Objective: Correct the canonical scoring architecture from verified current
code while keeping this PR strictly documentation-only.

What I inspected:

- The live general formulas in `generate_picks.score_batter()` and
  `generate_picks.score_pitcher()` on current `main`.
- `CURRENT_WEIGHTS_BATTER`, `CURRENT_WEIGHTS_PITCHER`, and the scope notes in
  `backtest/fit_score_weights.py`.
- `test_score_batter.py`, `test_score_pitcher.py`,
  `test_fit_score_weights.py`, and `test_current_weight_score.py`.
- Every remaining section of `engineering/PROJECT_STATE.md` for historical
  behavior incorrectly presented as current behavior.
- All repository references to the original shared 35/25/15/15/10 split that
  could indicate related documentation or reconstruction drift.

What I found:

- The first version of `PROJECT_STATE.md` incorrectly presented the original
  shared 35/25/15/15/10 synthesis scaffold as the current principal score.
- Current live general batter weights are matchup 0.04, recent form 0.03,
  environment 0.20, baseline skill -0.09, and context 0.64.
- Current live general pitcher weights are matchup 0.11, recent form -0.16,
  environment 0.15, baseline skill 0.48, and context 0.10.
- Batter and pitcher use different promoted formulas. Specialty-market scorers
  can use their own formulas.
- The quality score is distinct from downstream betting probability,
  calibration, sportsbook value, and recommendation policy.
- No other section of `PROJECT_STATE.md` described superseded historical
  architecture as current architecture at this verification base.
- Stale 35/25/15/15/10 descriptions and reconstruction remain in source,
  schema, and test-adjacent locations listed in audit item 18. They were not
  changed because executable changes are outside this PR's authorization.
- At the start of this correction, `main` had advanced from `7a42ae0e...` to
  `a31fa26d...` through one automated commit affecting only the current odds
  and props generated-data snapshots.
- While the correction and its first CI run were in progress, `main` advanced
  again to `3d3e1ea...` through one automated commit affecting only
  `docs/data.json` and `docs/live.json`.

What I changed:

- Replaced the incorrect shared current formula in
  `engineering/PROJECT_STATE.md` with the verified promoted batter and pitcher
  formulas and their scope.
- Clarified component/signal recording and separated quality score from the
  downstream probability, price/value, and recommendation layers.
- Updated the `PROJECT_STATE.md` code verification base to the inspected
  current `main` commit.
- Added audit item 18 and this chronological correction entry.

Architectural decisions:

- The original shared 35/25/15/15/10 split is retained only as historical
  context, not current architecture.
- General batter weights, general pitcher weights, and specialty-market
  formulas are distinct concepts.
- Quality scoring, probability generation, sportsbook value, and
  recommendation classification remain separate layers in the canonical map.
- Documentation drift does not authorize changing model weights or executable
  reconstruction in this PR.

Tests added: None. This correction changes documentation only.

Test results:

- `test_fit_score_weights.py`: 11/11 checks passed.
- `test_score_batter.py`: 18/18 checks passed.
- `test_score_pitcher.py`: 17/17 checks passed.
- `test_current_weight_score.py`: 15/15 checks passed.
- Focused total: 61/61 checks passed using a temporary environment with the
  repository requirements installed.

Behavior intentionally unchanged:

- All model weights and executable scoring, signal, probability, calibration,
  pricing, recommendation, persistence, workflow, dashboard, and generated-data
  behavior.
- The large `README.md`, source comments/docstrings, schemas, tests, and
  generated artifacts.

Risks / known limitations:

- Some executable-adjacent descriptions still contradict the promoted general
  weights and can mislead future maintainers until separately audited.
- `backtest/signals.py` contains more than prose: its reconstruction still
  encodes the historical shared weights, so a future correction must first
  determine the intended evaluation semantics and add regression evidence.
- Automated generated-data commits can continue advancing `main` while this
  documentation PR remains open.

New issues discovered:

- The stale-weight references are broader than the two live scorer headings;
  they also include module/diagnostic text, backtest schema/reconstruction, and
  adjacent comments. Audit item 18 records the affected scope without changing
  it.

Recommended next work:

- Audit the stale score descriptions and signal reconstruction as a separate,
  evidence-backed task before changing any executable file.
- Preserve the promoted weights unless held-out validation and explicit model
  versioning justify a future change.

Information Claude should know when resuming:

- The code and focused scoring tests agree on separate promoted batter and
  pitcher formulas; the old shared split is historical only.
- No model weight or production behavior changed in this correction.
- Phase V has not begun; this remains Pre-Phase-V audit/hardening work.

## 2026-08-17 — Pre-Phase-V live lifecycle hardening

Agent: Codex

Branch: `pre-phase-v/live-lifecycle-hardening`

Commit(s): `4dcfdc171e60e9264f3f923a06cee722b582a48b` (implementation);
the repository-handoff commit containing this entry immediately follows it on
PR #51.

PR: Draft PR #51, **Pre-Phase-V: harden live pick lifecycle** —
https://github.com/werriesjacob1-cmyk/Full-Count/pull/51

Objective: Make the existing public Top Pick lifecycle reliable from pregame
publication through live play and final resolution without changing model or
recommendation logic.

What I inspected:

- `dashboard-refresh.yml`, the former `dashboard-prices.yml` and
  `dashboard-grades.yml`, `lineup-watch.yml`, their concurrency/commit paths,
  and the GitHub Pages upload/deploy path.
- GitHub Actions run `32049619252` (successful refresh/build/deploy), price run
  `32057441977` (repository update without deploy), and failed grade run
  `32056821159`, job `95468790326` (missing `pybaseball` import chain). The
  latest 20 grading runs inspected were failures.
- `dashboard/build_dashboard.py`, `dashboard/refresh_prices.py`,
  `dashboard/refresh_grades.py`, `grade_results.py`, `mlb_daily.py`,
  `odds_fanduel.py`, `recommendation.py`, and the frontend's full/live polling
  and rendering paths.
- The stable prop-ID construction, game/player/market/threshold/side identity,
  started-game filtering, price attachment behavior, terminal settlement
  rules, atomicity/failure behavior, and all relevant tests.
- Current `main` repeatedly during the task. The final published implementation
  is based on `bb392e7257f93dbe9ac3e78bc1d64e57a72a8e7b`; intervening advances were
  automated generated-data or lineup-watch-state commits, not source changes.

What I found:

- **CONFIRMED:** committing `docs/live.json` did not mutate the already
  uploaded Pages artifact. Only a later full dashboard deployment exposed the
  update publicly.
- **CONFIRMED:** the reduced grader imported `mlb_daily.py`, which eagerly
  required `pybaseball`; the reduced workflow did not install it and failed.
- **CONFIRMED:** correct pregame-only candidate filtering meant a later full
  rebuild omitted a started Top Pick, then replaced the payload and cleared its
  live state.
- **CONFIRMED / QUALIFIED:** three separate writer workflows used different
  concurrency groups and whole-file writes. Git push/rebase rejected some
  conflicts but could not merge independent grade/price facts or enforce field
  recency.
- **QUALIFIED:** the frontend polled `live.json` and displayed live/hit/miss
  chips, but lacked void/ungraded, lifecycle card/row colors, timestamp-aware
  merges, and reapplication of a newer live overlay after a board swap.
- **CONFIRMED during implementation review:** an individually disappeared
  FanDuel market retained the previous quote while the former price pass
  stamped the observation as fresh and reran classification.
- Existing canonical IDs did not always represent NRFI/YRFI side or both
  players in a combined-starter market. A legacy ID collision could therefore
  transfer lifecycle state unless the full settlement identity was checked.

What I changed:

- Added `dashboard/live_state.py` as the schema-v2 live-state boundary:
  atomic JSON replacement, strict reads, UTC timestamps, per-field recency,
  immutable first-publication time, stable full settlement identity, and
  terminal outcome monotonicity.
- Kept scoring and recommendation generation pregame-only. Added
  `reconcile_public_lifecycle()` to carry only an exact previously published
  Top Pick through first pitch, a full rebuild, and a UTC date rollover. It
  never introduces a never-published started prop.
- Reworked the live grader to write only `live.json`, grade all published Top
  Picks, accept early hits only for mathematically monotonic overs, wait until
  Final for misses/unders, distinguish proven void from honest ungraded, and
  preserve prior state on any per-pick failure.
- Added `grading_sources.py` so ordinary box-score grading no longer imports
  the entire research pipeline or eagerly requires `pybaseball`; Statcast-only
  markets load it lazily.
- Reworked the price pass to write only field deltas for explicitly pregame
  games, preserve grades, clear an old quote before attaching a current market,
  and call the unchanged authoritative recommendation policy.
- Added explicit frontend states and yellow/green/red lifecycle treatment,
  timestamp-aware delta caching, terminal-result protection, published-pick
  visibility, and live-overlay reapplication after a full-board poll.
- Replaced the two competing live workflows with one serialized
  `dashboard-live.yml`; made the full rebuild the sole `data.json` writer; and
  added `dashboard-deploy.yml` to verify and deploy newest `main:docs/` after
  either state writer completes.
- Added `dashboard/verify_pages_artifact.py` and updated the canonical project
  map plus `engineering/AUDIT/live-lifecycle-2026-08-17.md`.

Architectural decisions:

- `docs/data.json` has one owner (full rebuild) and `docs/live.json` has one
  owner (consolidated live update). The owners share a non-cancelling writer
  lane because lifecycle reconciliation depends on prior publication state.
- Pages deployment is a separate latest-wins consumer. It checks out newest
  `main` rather than deploying an older writer's checkout.
- Price and grade state merge by canonical ID and full settlement identity at
  field granularity. Timestamps are UTC; terminal hit/miss/void cannot regress
  or acquire conflicting result metadata.
- The narrow `published_top_pick_at` marker is sufficient for this lifecycle
  objective but is explicitly not the planned immutable recommendation ledger.
- Unknown stays unknown. Missing price becomes unavailable; missing grade data
  becomes ungraded; neither becomes favorable evidence.

Tests added:

- Added `test_live_lifecycle.py` coverage for pregame-to-live persistence,
  UTC rollover, side-collision protection, 1+ Hit/2+ TB/HR/K early hits,
  non-premature unders, unresolved live state, final miss, void/ungraded,
  failure preservation, price/grade field coexistence, newest-field merges,
  reduced-environment imports, workflow ownership, and Pages artifact content.
- Extended price tests for disappeared-market fail-closed behavior and updated
  build/grade/price tests for the explicit lifecycle and ownership contract.

Test results:

- Focused final set: 151/151 checks passed (`test_live_lifecycle.py` 16,
  `test_refresh_grades.py` 17, `test_refresh_prices.py` 18,
  `test_grade_results.py` 36, `test_build_dashboard.py` 64).
- Complete repository suite: 70/70 root `test_*.py` files passed;
  1,454/1,454 reported test/check units passed. The failure-signature scan was
  clean.
- Workflow YAML: all eight current files parsed successfully.
- Python: affected modules passed `py_compile`.
- JavaScript: `node --check dashboard/static/app.js` passed.
- Pages: `dashboard/verify_pages_artifact.py docs` passed after the final
  current-main rebase; source/deployed JS and CSS copies matched byte-for-byte.
- `git diff --check` passed.

Behavior intentionally unchanged:

- Score weights, features, probabilities, calibration, model versions,
  recommendation thresholds, and `recommendation.py` classification policy.
- Pregame-only generation of new candidates/recommendations.
- Durable daily grading/history, backtesting, and champion/challenger policy.
- Generated `docs/data.json`, generated `docs/live.json`, prediction history,
  odds/prop snapshots, and all other generated data are outside the PR diff.
- This does not implement Phase V or the full Prediction Receipts ledger.

Risks / known limitations:

- The new scheduled workflow and Pages deployment cannot execute from an
  unmerged feature branch. PR CI validates the code; the first real live cycle
  and public Pages artifact require post-merge operational observation.
- Unders intentionally wait for Final. Proving a player/pitcher is definitively
  finished before Final was not necessary for correctness and is not claimed.
- The lifecycle publication marker protects public visibility but does not
  provide a complete immutable record of every recommendation transition.
- A workflow already running under a deleted pre-merge workflow definition can
  complete once during rollout; serialized ownership is authoritative after
  the merged definitions take effect.

New issues discovered:

- `mlb_daily.TODAY` uses the runner process timezone, so a GitHub-hosted run can
  advance the slate date at UTC midnight while late West Coast games remain
  pregame/live. This PR prevents published Top Picks from disappearing across
  that boundary, but the broader slate-date convention deserves a separate
  audit rather than an incidental global-time rewrite here.
- The existing prop ID omitted explicit side for NRFI/YRFI and preferred one
  `player_id` over the full pair for combined-starter markets. This PR corrects
  future ID construction and adds migration-compatible full-identity matching.

Recommended next work:

- Independently review PR #51 and its final required CI result; do not merge
  without explicit user authorization.
- If merged later, observe the first `Dashboard Live Update` and
  `Dashboard Pages Deploy` runs, fetch the public `live.json`, and verify one
  real published Top Pick through live/final state.
- Audit the system-wide MLB slate-date/timezone convention separately.
- Build the immutable recommendation ledger/Prediction Receipts only as its own
  authorized task; do not expand this lifecycle marker silently.

Information Claude should know when resuming:

- Phase V has **not** begun.
- PR #51 is intentionally draft and unmerged.
- This PR changes lifecycle/delivery/grading/state ownership only. It makes no
  model or recommendation-policy change.
- The detailed evidence is in
  `engineering/AUDIT/live-lifecycle-2026-08-17.md`; the canonical architecture
  is updated in `engineering/PROJECT_STATE.md`.
- Start review with state ownership/concurrency, terminal merge semantics,
  cross-UTC persistence, exact identity migration, and the dedicated Pages
  deploy trigger.

## 2026-08-17 — Live lifecycle adversarial correction pass

Agent:
Codex

Branch:
`pre-phase-v/live-lifecycle-hardening`

Commit(s):
`6e423c295077e5894deb44b0ab3cc8fd838b283a` (correction implementation);
the handoff commit containing this entry follows it on PR #51.

PR:
Draft PR #51, **Pre-Phase-V: harden live pick lifecycle** —
https://github.com/werriesjacob1-cmyk/Full-Count/pull/51

Objective:
Adversarially correct the first live-lifecycle pass so public exposure,
recommendation immutability, game progress, settlement authority, odds-source
failure, durable grading, workflow scheduling, and Pages delivery remain
truthful under races and failures. No model or recommendation-policy change.

What I inspected:

- The complete PR #51 implementation and every caller/writer/consumer of
  `docs/data.json`, `docs/live.json`, daily grades, public metrics, and static
  frontend lifecycle state.
- Current official FanDuel Illinois and Pennsylvania house rules, verified
  2026-08-17, for MLB official results/resettlement, game completion,
  batter/pitcher action, hits, home runs, total bases, H+R+RBI, pitcher outs,
  pitcher strikeouts, and combined-starter strikeouts.
- Current official GitHub Actions concurrency documentation and the 2026-05-07
  `queue: max` release. Default concurrency retains only one pending run;
  `queue: max` permits up to 100 pending runs and cannot be paired with
  destructive in-progress cancellation.
- Publication timing from local qualification through repository persistence,
  Pages artifact staging/deployment, and post-deploy provenance persistence.
- Same-base stale-writer races, legacy-ID rollout, UTC rollover, corrupt files,
  atomic replacement failure, compaction, and frontend board/live poll order.
- Latest `main` before the final rebase. It advanced from
  `2a1e2e4660d3b2d24899fad0f71ff30e21f31e4b` to
  `e1f296b9bfa6d461f5163a96badcc18ce339f60c`; the intervening changes were
  automated generated data and lineup-watch state, not source architecture.

What I found:

- Every numbered correction concern was confirmed or qualified; the detailed
  dispositions are in `engineering/AUDIT/live-lifecycle-2026-08-17.md`.
- A live mathematical hit must be provisional. Permanent terminal protection
  prevented an MLB scoring correction from producing the authoritative result.
- A publication marker created before `deploy-pages` was not evidence that a
  user could see the recommendation. The historical population needed
  deployment proof and recovery provenance.
- The canonical daily pick file could omit a previously public wager, so it
  could not be the public Top Pick grading population.
- First-pitch safety also applies while Pages is deploying. Merely checking at
  price/build time left a final exposure race.
- The real checked-in artifact still had legacy IDs. Normalizing only during
  deployment would allow the first live writer to publish a mixed-ID state and
  wedge deployment; every write/retry boundary needs bounded normalization.
- The original correction accidentally replaced the established
  `by_recommendation_status` analytical population with public-only Top Picks.
  The complete suite caught this. Those populations must remain separate.
- Illinois and Pennsylvania differ on standard hits/home-run action in edge
  cases. With no configured product jurisdiction, mixed cases cannot honestly
  be labeled action or void and must remain ungraded.
- Direct workflow CLI execution exposed an import that passed module tests but
  failed when `dashboard/verify_pages_artifact.py` ran as a script.

What I changed:

- Separated recommendation, game, and settlement schemas. Added explicit
  provisional hits and the authority order
  `none < live_observation < official_final`; settlement metadata merges as one
  atomic fact and final corrections are idempotent.
- Added explicit MLB game-state parsing for pregame/live/delayed/suspended/
  postponed/final/cancelled/unknown, with unknown preserving old state and
  blocking new wagering decisions.
- Enforced scheduled `game_start` plus a final status refetch in price/full
  writers. Staged artifacts reserve a 15-minute publication window inside a
  10-minute deploy timeout; late candidates are omitted without changing source
  `docs/`.
- Added canonical identity schema v2, commutative combo-K participants,
  supplied-ID/duplicate detection, and bounded legacy normalization at every
  live/full/deploy retry boundary.
- Added the minimal authoritative registry at
  `data/public_top_picks/registry.json`, written only after successful Pages
  deployment. It stores one immutable exposure snapshot and deployment
  provenance and can recover idempotently from a deployed manifest if the
  registry push fails. This is lifecycle infrastructure, not Prediction
  Receipts.
- Made durable grading consume registry snapshots separately from canonical
  daily picks. Public results remain present when later boards omit/demote a
  wager, retry without duplication, exclude voids from hit/miss, and accept
  official corrections. Existing modeled recommendation-status and legacy
  main-board metrics retain their established populations; only
  `public_top_pick_totals` drives the official public Top Pick record.
- Added direct-`game_pk` public grading across UTC midnight and bounded recent
  correction checks.
- Added structured settlement eligibility. Cases agreed by inspected official
  rules settle; jurisdiction-dependent hit/HR edge cases and unsupported
  specialty rules remain ungraded.
- Added independent `MATCHED`, `NOT_POSTED`, `FETCH_FAILED`, and `IN_PLAY`
  observations for each supported sportsbook family so a failed feed never
  clears or freshness-stamps its prior quote.
- Split workflow scheduling: significant full/lineup builds use a true
  non-cancelling queue; replaceable five-minute observations coalesce and make
  a current observation from latest `main`. All push retries reread and
  semantically merge current state.
- Added safe live-overlay compaction, strict new UTC timestamps, a complete
  Pages schema/provenance verifier, deployment staging/confirmation, and
  frontend immutable-snapshot/result-authority handling.

Architectural decisions:

- Public exposure is the first successful Pages deployment containing the
  exact Top Pick, not local qualification or a repository commit.
- Recommendation snapshot fields are immutable after first pitch. Only game
  and settlement facts advance.
- Live observations are useful for immediate display but are lower authority
  than official final settlement.
- `by_recommendation_status` continues to track all modeled recommendations;
  deployment-proven public results have a separate population and official
  headline.
- Unknown, source failure, jurisdiction ambiguity, and unsupported rules stay
  unknown/ungraded. They never become favorable evidence or fabricated voids.
- Full and live workflow correctness is merge-based; workflow serialization is
  defense in depth, not the sole lost-update prevention mechanism.

Tests added:

- New focused files cover lifecycle schema/authority/identity/UTC/compaction,
  stale-state races, publication registry and deployment recovery, Pages
  preparation/contract failure, durable public grading, and actual frontend
  behavior.
- Existing build, price, grade, lifecycle, and recommendation-status tests were
  strengthened for first-pitch races, every market-family failure, actual
  public-vs-modeled populations, jurisdiction ambiguity, direct workflow CLI
  execution, and legacy normalization.

Test results:

- Final focused lifecycle set: 172/172 tests/checks passed.
- Complete CI-equivalent root suite: 77/77 `test_*.py` files and 1,478/1,478
  reported tests/checks passed; failure-signature scan clean.
- Eight workflow YAML files parsed.
- Full Python `compileall`, both JavaScript `node --check` calls,
  publication-registry verification, `git diff --check`, and source/deployed
  CSS/JS byte comparison passed.
- The actual staged Pages artifact verified with 1,624 canonical props, 627
  retained live deltas, one known public exposure, zero unsafe new exposure
  candidates, and artifact ID
  `7f23c7cc5abf7906ec65a817625475317b7f9e5bb5f1b538b14ac27453300718`.

Behavior intentionally unchanged:

- `recommendation.py`, score weights, features, probabilities, calibration,
  selection thresholds, value thresholds, signal trust, and model versions.
- Pregame-only generation of new candidates.
- Generated `docs/data.json`, `docs/live.json`, prediction history, model and
  calibration artifacts, odds/prop snapshots, and daily output files are not
  changed by this PR.
- No Phase V feature and no full Prediction Receipts event ledger.

Risks / known limitations:

- Scheduled live/deploy workflows and a real public browser cannot execute
  from an unmerged PR. The 25-step post-merge checklist in the audit is
  mandatory.
- GitHub's full queue holds at most 100 pending runs; extreme service outages
  can still exceed platform retention. Each run finalizes against latest main.
- The actual consolidated live runtime relative to the five-minute cadence must
  be measured after merge; its dependency set is intentionally minimal.
- The product has no configured FanDuel jurisdiction. Where inspected rules
  differ, results remain ungraded until that context is defined.
- Automatic official-correction polling is bounded to recent dates; older
  corrections need an explicit date rerun.

New issues discovered:

- Public and all-modeled Top Pick metrics require separate source-of-truth
  fields; a shared `top_pick` bucket invites survivorship bias or destroys
  internal evaluation coverage.
- Settlement jurisdiction is currently absent from product configuration.
- Workflow-script import behavior must be tested at the executable boundary,
  not only through package imports.

Recommended next work:

- Independently review this correction commit and final PR CI; do not merge
  without explicit user authorization.
- If later merged, execute every post-merge observability check in the audit,
  including a real first-pitch transition, provisional-to-final correction,
  registry write, durable next-day grade, full-build overlap, and artifact hash
  comparison.
- Configure the product's applicable FanDuel jurisdiction in a separately
  reviewed operational task before relaxing any ungraded settlement branch.
- Keep the future full Prediction Receipts ledger as a separate authorized
  project.

Information Claude should know when resuming:

- The first-pass historical handoff remains unchanged but is superseded where
  it says terminal-result monotonicity or treats `published_top_pick_at` alone
  as public proof. Read the updated audit and project map.
- The canonical invariant is result authority, not permanent live-terminal
  protection.
- PR #51 remains draft and unmerged. Phase V has not begun.

## 2026-08-17 — PR #51 CI dependency correction

Agent:
Codex

Branch:
`pre-phase-v/live-lifecycle-hardening`

Commit(s):
`faddb4fbd61b2c2830be3e9f131cff9fca66e07c` (declare YAML validation dependency);
the handoff commit containing this entry follows it on PR #51.

PR:
[#51 — Pre-Phase-V: harden live pick lifecycle](https://github.com/werriesjacob1-cmyk/Full-Count/pull/51) (draft, unmerged)

Objective:
Correct the single CI-only dependency failure found after publishing the full
lifecycle correction, without changing lifecycle, model, recommendation,
workflow, dashboard, or generated-data behavior.

What I inspected:
GitHub Actions Test Suite run `32076556881`, job `95530920118`, its complete
job log, `requirements.txt`, and `test_pages_contract_v3.py`.

What I found:
The workflow-contract regression test imports `yaml`, but PyYAML was available
only in the earlier development environment and was not declared in the
dependency set installed by CI. The run's only failure was
`ModuleNotFoundError: No module named 'yaml'`; all later test files completed
successfully.

What I changed:
Declared `PyYAML~=6.0.2` in `requirements.txt`, matching the repository's
patch-compatible pinning convention, so the workflow YAML validation gate is
reproducible in a clean CI environment. Updated the project verification base
after automated main commits advanced it to
`fd20785769e1de25581e873317d2b2230389ba13`.

Architectural decisions:
Workflow YAML parsing remains a real CI contract test. The missing dependency
is declared rather than weakening or deleting that test.

Tests added:
None; this fixes the clean-environment execution of the existing seven-check
Pages/workflow contract test.

Test results:
`test_pages_contract_v3.py`: 7/7 passed. Complete CI-equivalent root suite:
77/77 files and 1,478/1,478 reported tests/checks passed with exit code 0.

Behavior intentionally unchanged:
All production lifecycle behavior, model/scoring/probability/calibration code,
`recommendation.py`, workflow semantics, dashboard behavior, and generated
artifacts.

Risks / known limitations:
Scheduled workflow and public Pages behavior remain operationally untestable
until merge. The audit's post-merge checklist remains mandatory.

New issues discovered:
The earlier local environment masked an undeclared test dependency. Clean CI
is the authoritative dependency-reproducibility check.

Recommended next work:
Wait for the replacement PR CI run, then leave PR #51 draft and unmerged for
independent review.

Information Claude should know when resuming:
The lifecycle correction itself did not fail. The only failing check was the
undeclared PyYAML dependency used by the workflow-contract test; it is now
declared and the complete suite passes locally. Phase V has not begun.
