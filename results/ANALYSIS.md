# Historical data integrity — Phase 3, item 7

Written 2026-08-16. What can and cannot be trusted in this project's
historical data, and why, so nobody (including a future session) has to
re-derive this from scratch or guess.

## The core problem

Before this Phase 3 pass, **nothing on disk recorded which version of the
scoring code produced a given row.** `generate_picks.py` has been touched by
69 separate commits over this project's history, many of them real formula
changes (weight refits, calibration fixes, bug fixes that changed a
computed number) — see that file's own extensive inline audit comments for
the specific dates and reasons. Two pieces of data can therefore blend rows
from materially different formulas with no way to tell them apart after the
fact:

- **`backtest/rows.jsonl`** — a backtest *replays a past date through
  today's scoring code*, not the code that existed on that date. Two
  backtest runs of the identical historical date range, run weeks apart on
  two different commits, can legitimately produce different `predicted_prob`
  values for the same row — and did, across this project's history (task
  history confirms the 242,776-row file was built across at least two
  separate chunk runs: 2024 Apr–Jul, then 2025 Jul–Oct, run at different
  real times).
- **`results/history.json` / `results/grades_*.json`** — real forward
  picks, graded against real box scores as they happened. The selection
  logic and label semantics behind those picks changed materially over
  time (most recently and most sharply at the 2026-08-15 recommendation-
  layer rebuild, commit `0b83b28`), with no version tag before now.

## What was fixed going forward (this commit)

- `backtest/engine.py`'s `to_row()` now stamps every row with
  `code_git_sha` (which commit's scoring code produced it) and
  `backtest_generated_at` (a real run-level timestamp) — see
  `backtest/SCHEMA.md`. Computed once per process, not per row.
- `generate_picks.py`'s `write_json()` now stamps every saved pick with
  `model_version`, `selection_policy_version`, `calibration_version`,
  `feature_version`, `git_sha`, `prediction_timestamp`, `odds_timestamp`,
  `lineup_checked_at`, `lineup_assumed` — see Phase 2/3's `recommendation.py`
  and `test_phase3_versioning.py`. Preserved through `grade_results.py`
  grading automatically (`grade_pick()` spreads `{**pick, ...}`).

Every row/pick from this point forward is self-describing. The rest of this
document is about everything written *before* that fix existed.

## What was checked, and what it found

- `backtest/rows.jsonl` is listed in `.gitignore` (`backtest/*.jsonl`) and
  has **zero git history** (`git log -- backtest/rows.jsonl` returns
  nothing). There is no commit trail to reconstruct *when* any existing
  chunk was appended, and therefore no way to infer *which* commit's
  formula scored it. This is not a gap that can be closed after the fact —
  the information was never recorded anywhere, by this file or by git.
- `results/history.json` currently holds exactly **11 graded days**
  (2026-08-04 through 2026-08-14) — entirely **before** the 2026-08-15
  recommendation-layer rebuild. There is not yet a single graded day under
  the new architecture. That is a real, honest fact, not a gap to paper
  over: the new system's forward track record starts at zero, by
  construction (see "official starting point" below).
- No commit touching `generate_picks.py` or `prop_probability.py` (the two
  files that determine scoring/pricing) landed between commit `0b83b28`
  (2026-08-15 rebuild) and this Phase 3 pass. The scoring formula has been
  stable for the entire lifetime of the new recommendation architecture so
  far — confirmed by `git log --oneline 0b83b28..HEAD -- generate_picks.py
  prop_probability.py` returning nothing, not assumed.

## The tiers

**Tier 1 — Clean (self-describing, trustworthy without qualification).**
Any row/pick carrying a real `code_git_sha` / `git_sha` from this commit
forward. Nothing qualifies yet as of 2026-08-16; this tier starts filling
the next time the pipeline runs.

**Tier 2 — Partially trustworthy (inferable, not persisted).** Every graded
pick from 2026-08-15 (commit `0b83b28`) through immediately before this
Phase 3 commit. These picks lack a persisted `git_sha`/version block, but
the surrounding commit history *positively confirms* no scoring-relevant
file changed during that window — so it is safe to treat this whole window
as `model_version="2026.08.15"` / `selection_policy_version="1.0.0"` for
analysis purposes, as an inference stated explicitly as such, never
silently presented as if it had been recorded live. Currently this tier is
also empty in `results/history.json` (grading lags one day; the first
picks in this window will be graded the morning after they're made) but
will begin filling from live boards generated on/after 2026-08-15.

**Tier 3 — Contaminated / not comparable as a single series.**
`results/history.json`'s current 11 days (2026-08-04 to 2026-08-14) and all
picks made before commit `0b83b28`. This is the **legacy** system: the old
price-clears/quality-score Locks logic, audited and replaced in Phase 2 —
different selection rules, different labels, no `recommendation_status`
field at all. Already correctly isolated by `by_recommendation_status`'s
`"unclassified"` bucket (grade_results.py) rather than blended into the
new system's numbers. Real, valid historical record of what the *old*
system did — never to be presented as evidence about the *new* one.

**Tier 4 — Cannot be reconstructed.** `backtest/rows.jsonl`'s existing
242,776 rows, with respect to *which specific commit* produced any given
row. No external record (git history, timestamps, anything) exists to
recover this after the fact — it is gone, permanently, for data written
before this commit. This does **not** mean the file is useless: every row
still carries real signals, a real outcome, and a real `predicted_prob`
from *some* honest point-in-time replay with no lookahead (the file's core
guarantee, proven separately by `backtest/engine.py --verify` and unaffected
by the missing version tag). It means claims that require knowing the
*exact* formula behind a specific row — precise calibration curves
attributed to "today's model," for instance — cannot be made from this
file as a whole. Signal-ablation and feature-value questions (does platoon
separate hits from misses at all, does park_hand_index survive a
multivariate fit) are far more robust to this kind of drift, since most
individual signals (platoon, park factors, recent form) were not the part
that changed release to release — but this is a robustness argument, not a
guarantee, and is stated as such everywhere this file's results are used.

## The rule this project will not break

**Backtests and live forward performance are, and must remain, different
kinds of evidence.** A backtest answers "what would this exact code have
said about a past date, with only information available before that date."
Forward performance answers "what did this exact deployed system actually
do." Rewriting old predictions with today's model and presenting that as
historical *live* performance would collapse that distinction and produce
exactly the kind of confident, fabricated track record this whole Phase 3
pass exists to prevent. Nothing in this project does that, and nothing
should.
