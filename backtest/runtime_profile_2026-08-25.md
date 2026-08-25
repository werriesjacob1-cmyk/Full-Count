# dashboard-live.yml runtime profile — 2026-08-25

Persisted per the interruption-safety protocol. Priority D ("the 15→25 minute
timeout increase is only a patch — profile the pipeline and find the source of
growth") from the governing instruction set.

## The evidence

`Dashboard Live Update` runs, pulled directly via the GitHub Actions API
(`list_workflow_runs` / `list_workflow_jobs`), not estimated:

| run # | created (UTC) | conclusion | total | Grade step | Reprice step | Commit step |
|---|---|---|---|---|---|---|
| 244 | 08-24 06:28 | success | ~14s | — | — | — |
| **245–267** (23 consecutive runs) | 08-24 07:41 → 22:59 | **cancelled** | ~20m each | 4–8 min | 4–8 min (often cancelled mid-step) | often `failure` (ran out of budget mid push-retry) |
| 252 | 08-24 12:50 | cancelled | 20m06s | 7m44s | 7m20s (cancelled) | 4m51s (failure) |
| 260 | 08-24 18:39 | cancelled | 20m03s | 4m36s | 4m38s | 11m08s (failure) |
| 267 | 08-24 22:59 | cancelled | 20m04s | 7m34s | cancelled at 7m31s | never ran |
| **268** (workflow_dispatch, manual recovery) | 08-24 23:29 | success | 26s | 1s | 15s | 3s |
| 269–273 (resumed scheduled cadence) | 08-24 23:30 → 08-25 03:29 | success | 27s–65s | 1–2s | 15–18s | 3–4s |

**23 consecutive scheduled runs were cancelled over a ~15-hour window**, every
one hitting the (then 20-minute) job timeout with "Grade published Top Picks"
and/or "Reprice pregame candidates" each taking 4–8 minutes instead of their
normal 1–2 seconds — a 200–400x slowdown, not a gradual creep. Recovery
required a manual `workflow_dispatch`; every scheduled run since has been
healthy (sub-minute).

This directly matches (and supersedes with real numbers) the prose already in
`dashboard-live.yml`'s own `timeout-minutes` comment, which only had run #267's
single data point.

## Root cause

Both `dashboard/refresh_grades.py` and `dashboard/refresh_prices.py` call
`grade_results.fetch_game_contexts(game_pks, refresh=True)` — once each per
5-minute cycle for grading, twice for repricing (an initial pass over all
props, then a deliberate final revalidation pass over successfully-priced
rows immediately before commit — see that function's own comment on why the
revalidation is real and not redundant). Each call fetches one MLB live-feed
per **distinct** `game_pk` (already deduplicated, already cached-with-`refresh`
override) via `grading_sources.retry_get` — `timeout=20`, `retries=2`,
exponential backoff. The loop itself was a plain sequential `for`.

Real current population: 14 published-registry entries / **7 distinct
`game_pk`s** (checked directly against `data/public_top_picks/registry.json`
— small, not a population-growth problem). But the fetch loop was
**sequential**: worst case per game is up to ~20s + 2s backoff + 20s ≈ 42s if
MLB's API is timing out. Seven games × up to 42s ≈ 5 minutes — this alone
reproduces the observed 4–8 minute step durations, and it happens **three
times per 5-minute cycle** (grading once, repricing twice), which is why both
steps ballooned together and why the whole 20–25 minute budget was consumed
before the commit step could even run.

The 07:41–22:59 UTC window spans essentially the entire MLB game day, and MLB
Stats API real-world slowness/timeouts during that stretch is the most
consistent explanation for a *sustained* multi-hour episode (not tied to a
single slate's live-game count, since the affected population is small and
constant) — this profile does not fabricate a cause for MLB's own API
behavior; it only demonstrates that the pipeline's own fetch loop turned any
such slowness into an ×N (games) ×3 (calls/cycle) amplifier instead of
absorbing it.

## Fix shipped

`grade_results.fetch_game_contexts()` now fetches all distinct games
**concurrently** via a bounded `ThreadPoolExecutor` (8 workers) instead of
sequentially. Same requests, same cache, same retry/backoff, same return
shape — verified via a dedicated sign-reversal test
(`test_fetch_game_contexts_concurrency.py`: fails against the pre-fix
sequential code with real timing, passes against the fix) plus the full
existing suite green. This bounds each of the three per-cycle calls to
roughly the slowest single game's fetch time instead of the sum across all
games — an 7x (current population) reduction in the worst case, growing with
whatever the active/recent population grows to.

Not touched: `refresh_prices.py`'s two-pass fetch structure (initial +
final revalidation) is deliberate wagering-safety behavior (freezing prices
if a game crosses first pitch while sportsbook requests are in flight) and
was left alone — this fix is purely about how each pass fetches, not how
many passes happen or what they decide.

## What this does NOT explain / other suspect ruled out

- **Not population/prop-count growth**: only 14 registry entries / 7 distinct
  games right now — this is not "docs/data.json grew" the way the 2026-08-20
  baseline comment worried about (1075 props, 1.6MB). That earlier concern
  (grading+repricing = 5m19s under normal load) is a separate, smaller,
  real cost that the concurrency fix also helps with proportionally (each
  game's own per-prop grading work inside the loop is unaffected, but the
  network-bound outer loop that dominates wall time is now parallel).
- **Not a code regression in this window**: no commit to `grade_results.py`,
  `refresh_grades.py`, or `refresh_prices.py` shipped between the last known
  healthy day and the 07:41 UTC onset — this points at an external MLB API
  behavior change on 2026-08-24, not a local code change, as the trigger.
  (The pipeline's own amplification of that behavior is still a real, fixed
  defect regardless of what triggered it.)
- **`timeout-minutes` bumps (15→20→25) are still just headroom, not a fix** —
  consistent with the standing instruction not to solve this by raising the
  timeout again. This profile's fix is the first change to the actual
  bottleneck itself.

## Next verification step

Watch the next live scheduled run(s) after this fix ships to `main` for real
step timing during any future MLB API slowness — the network layer this
change touches cannot be forced to reproduce a slow MLB API on demand, so the
strongest available proof today is the sign-reversal test's synthetic timing
plus the architectural argument above; a future real-world slow-MLB-API
window recovering in under a minute per step (instead of repeating the
2026-08-24 cancellation streak) will be the live confirmation.
