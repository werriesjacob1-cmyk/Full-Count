# Full Count — session handoff status

Last updated: 2026-08-25 ~04:55 UTC by Claude (this session).
Purpose: let a fresh session resume with zero hidden chat context.

## Branch / HEAD

- Branch: `claude/gridiron-continuation-dvaljm` at `fc438e40`, fully pushed,
  clean working tree, matches `origin/main` exactly (no divergence).
- PR #64 (Weston fix + provenance validator) MERGED, sha `1ead2fb1`.
- PR #65 (event-targeted observer upgrade + on_1b fix) MERGED, sha `610cfe17`.
- No open PRs. Everything on this branch this update is already on `main`.
- Repo has moved to `werriesjacob1-cmyk/Full-Count` (GitHub redirects
  PROJECT-GRIDIRON pushes there).

## Long-running background jobs

- **Main backfill**: PID 3663, `backtest/engine.py --start 2024-04-01 --end
  2026-06-30 --out backtest/rows_backfill.jsonl --no-weather`. Started
  2026-08-24 21:53:13 UTC. At last check: ~6h51m elapsed, healthy. **Still
  the long pole for everything below "BLOCKED ON CANONICAL HISTORY."**
  Check with `ps -p 3663 -o pid,etime,cmd`. **When it completes**: verify
  821/821 intended dates, no gaps, inspect failures, then run
  `backtest/build_canonical_backtest.py` (already written, tested, and
  wired to `provenance.require_single_regime()` as a hard gate) to produce
  `backtest/rows_canonical.jsonl`. Do NOT launch another giant backfill.

## CLOSED work this session — do not redo

1. **H+R+RBI stable-lift** merged, PR #62, sha `6c2ce0d6`. Scope:
   hits_runs_rbis Lean gate ONLY.
2. **Live-freshness watchdog + dashboard-live.yml timeout bump** merged,
   PR #63, sha `d4aaec8e`.
3. **Fail-fast provenance/regime validator**: `backtest/provenance.py`,
   wired into `build_canonical_backtest.py` as a real gate.
4. **Weston Wilson stale-explanation bug fixed, MERGED, live-verified**
   (`1ead2fb1`). Fixed via `FROZEN_PUBLICATION_FIELDS` allowlist. Honest,
   PERMANENT boundary case documented: the original Weston registry entry
   still shows pre-fix text because `bettable_games(allow_started=False)`
   means no rebuild ever re-scores an already-started game — not a bug,
   not something to patch with a historical-registry rewrite (forbidden).
5. **FanDuel passive observer CLOSED** — zero real odds/line changes,
   ~85 min, 96-100 markets.
6. **Event-targeted observer built, run to completion, upgraded, MERGED**
   (`610cfe17`). Full run: 27 triggers, 233 polls, 0 endpoint failures, 58
   status_change events (30 suspend/28 reopen) bound tightly to inning
   transitions specifically — reads as routine market rollover, not a
   leverage reaction. **Still zero confirmed odds/line changes across 3
   runs, ~130 min total observation.** Full writeup:
   `backtest/fanduel_observer_final_report.md`.
7. **Priority D runtime bottleneck FOUND + FIXED + forward-verified twice**:
   `47a75920` (parallelized `fetch_game_contexts()`). Forward proof #1: a
   manually-recovered run completed in 35s during a later, UNRELATED
   scheduling incident (see #8). Runtime is not the open question anymore.
8. **Scheduling-delivery incident investigated, partially resolved, real
   architecture built for the rest**: `dashboard-live.yml` AND its
   independent watchdog both went silent on their own 5-minute crons
   simultaneously (~03:29-04:19 UTC, ~50 min). Zero queued/in-progress
   runs during the gap (ruled out capacity — this was trigger-DELIVERY).
   Manual dispatch restored freshness (35s) but did NOT restore the
   recurring schedule for ~50 minutes; **a real scheduled run (`event:
   schedule`) DID resume on its own at 04:19:16 UTC** (10 min after the
   manual dispatch) — the incident appears to have cleared on its own by
   then, though the exact mechanism (GitHub-side congestion vs. something
   the manual dispatch indirectly triggered) was never conclusively
   identified and is NOT re-investigatable now (evidence window has
   passed). Full timeline: `backtest/live_incident_2026-08-25_0329.md`.
   Architecture recommendation: `backtest/independent_recovery_design_2026-08-25.md`
   (Option A: external heartbeat). **Built the actual Option A
   implementation**: `ops/external_heartbeat/` — a Cloudflare Worker
   (`worker.js`), 9 passing tests on synthetic timestamps
   (`test_worker.mjs`, zero network/credentials), deployment config
   (`wrangler.toml`), and a README documenting the exact 7 one-time user
   actions needed to deploy (Cloudflare account, wrangler auth, KV
   namespace, least-privilege GitHub token, deploy, dry-run verify,
   go-live). **NOT deployed** — stops at the credential/account boundary
   per the standing instruction; needs the user to actually create the
   Cloudflare account and GitHub token, since those are real credentials
   this session cannot safely create on its own.
9. **Candidate-level decision dataset: feasibility analysis + reusable
   builder**: `backtest/candidate_dataset_feasibility_2026-08-25.md` +
   `backtest/candidate_dataset.py` (14 tests). Key finding: Priority 5-style
   within-slate pairwise selection can't be answered from
   `rows_canonical.jsonl` as currently produced (one candidate per batter).
10. **Prospective full-candidate research funnel logger — built AND
    LIVE-VALIDATED**: `backtest/candidate_funnel_logger.py` (19 tests).
    Discovered `generate_picks.py` already computes and discards the full
    alternate-line curve per batter via `_keep_options()`/`line_options`
    ("THE MODEL ALREADY COMPUTES THIS AND THREW IT AWAY" — that function's
    own docstring) — this logger persists what's already computed, no new
    scoring logic. Mirrors `dashboard/build_dashboard.py`'s
    `run_live_fetch()` isolation pattern exactly (own scratch OUTPUT_DIR,
    cannot touch production). **Ran for real against tonight's actual
    slate**: 969 real candidates captured, 297 with 2+ alternate lines, 906
    correctly flagged `assumed_lineup` (exact match to generate_picks.py's
    own console output), gate traces on all 969. Confirmed live that
    `output/` was untouched and the new `.jsonl` file correctly matches the
    `backtest/*.jsonl` gitignore rule. **Honest gap**: market prices aren't
    attached yet (every record's `market` section is null this run) — a
    real, scoped, documented follow-up, not hidden.

## OPEN THREADS

### BLOCKED on canonical history (main backfill, PID 3663)
Once it finishes: verify date coverage/gaps/failures/row counts, reconcile
via `backtest/build_canonical_backtest.py` (already gated by
`provenance.py`), produce `rows_canonical.jsonl`, persist the reconciliation
report. THEN, and only then: Track A historical accuracy research (fragility,
source/role certainty, model disagreement, market specialization, shrinkage
strength, opportunity modeling, conditional calibration, fallback-source
value) — full question design already exists in this session's own
instruction history. Track B (prospective, using the new candidate-funnel
log) can start accumulating data independently of canonical history, but
needs many days of real logged slates before it has enough volume to
analyze — not blocked, just not yet informative with one day's data.

### NOT blocked, but correctly deferred tonight (do not manufacture)
- **Priority 12 (narrower FanDuel player-prop experiment)**: checked for a
  live game at ~04:53 UTC — only ONE live game exists (the same
  Pirates@Padres game already tracked extensively), and it's genuinely in
  the 9th inning, tied 2-2, likely ending soon or heading to extras. Not
  enough runway to properly set up a deliberate, well-targeted player-prop
  experiment (confirm the target prop is posted, identify its stable
  market/runner id, THEN watch a real trigger window) per the priority's
  own explicit setup requirement. **Next actionable step**: run this the
  moment a fresh slate's games are live with real time to prepare (ideally
  a few hours before first pitch on the next slate) — target actual
  player-prop markets (hit/TB/HR, K/outs), not the inning-special market
  that dominated the last run.
- **Candidate funnel logger's market-price gap**: scoped, documented,
  not built tonight — see item 10 above.
- **`dashboard-live.yml`'s scheduling-gap root mechanism**: cleared on its
  own; the evidence window to diagnose WHY has passed. If it recurs, the
  external heartbeat (once deployed) will both recover it faster AND
  produce independent KV-logged evidence of exactly when/how often it
  happens — a much better position than trying to diagnose retroactively
  from GitHub's own run history again.

## How to resume

1. `git status` / `git log --oneline -5` to confirm you're at `fc438e40`
   or later, clean, matching `origin/main`.
2. `ps -p 3663` for the main backfill. If it's gone, canonical-history
   reconciliation is the immediate top priority (see OPEN THREADS above)
   — do NOT start any accuracy analysis before it's validated.
3. If a fresh MLB slate is live with real pregame runway, consider: (a)
   running `backtest/candidate_funnel_logger.py` again to keep building
   Track B's prospective dataset (safe, isolated, already proven live), and
   (b) Priority 12's targeted player-prop FanDuel experiment (deferred
   tonight, see OPEN THREADS above).
4. If ops/external_heartbeat/ hasn't been deployed yet and the user wants
   to proceed, walk them through README.md's 7 steps (needs their own
   Cloudflare account + GitHub token — cannot be done by an agent alone).
5. Run `for f in test_*.py; do /tmp/mlbvenv/bin/python3 "$f" || echo "FAIL: $f"; done`
   before every commit. Fully green as of `fc438e40`. (The
   `ops/external_heartbeat/` JS tests are separate: `node
   ops/external_heartbeat/test_worker.mjs`, also passing.)
