---
globs: ["dashboard/**", "mlb_daily.py", "odds_fanduel.py", "odds_snapshot.py", "check_scratches.py", "grading_sources.py", ".github/workflows/**"]
---

# Live rule

## The commencement invariant — the most important thing in this directory

`playEvents[].isPitch == True` is the **only** MLB StatsAPI field structurally
reserved for a real thrown pitch. Every other "the game started" signal is
populated in pregame feeds and will lie to you:

- `abstractGameState == "live"` — populated pregame
- `gameStatus.isCurrentPitcher` — populated pregame
- `linescore.currentInning`, `.offense`, `.defense` — populated pregame
- non-empty `plays.allPlays` — pregame feeds carry a "Game Advisory / Status
  Change - Pre-Game" entry typed as a real `atBat`

**No statistical `hit` or `miss` may be written without commencement proof** — on
the live/provisional path, on the FINAL path, and in durable morning grading
alike. Fail closed to `ungraded` with reason
`awaiting_proof_game_actually_commenced`.

This does **not** block legitimate `void` / `cancelled` / `postponed` outcomes —
those are not statistical hits or misses. And the stale-clock inverse protection
stands: a stale feed must not be read as "still pregame" any more than a pregame
feed may be read as "started."

Settlement authority ranks `none` < `live_observation` < `official_final`. A
lower authority never overwrites a higher one.

When touching settlement, enumerate **every** path that can write `hit`/`miss`
and confirm each is gated. Assuming one block is the only writer is exactly how
the FINAL-path hole survived a previous review.

## Operating discipline

- **Evidence before root cause.** Pull the actual feed, the actual state file,
  the actual workflow run. Never infer from the shape of the code what the data
  did.
- **Fail closed.** When freshness or authority cannot be established, the correct
  output is "unknown", not a plausible-looking value.
- **Sole writer.** Two writers to one state file is corruption waiting for a slow
  day. Lock staleness must be decided by *liveness*, not elapsed time: a
  verifiable live owner is never stale, a verifiably dead one is immediately
  reclaimable, and heartbeat age decides only when liveness is unknowable.
- `docs/` is build output. Never resolve a generated-state conflict by replacing
  live state with branch-era output.

## Not yours from here

Predictive weights, probabilities, calibrators, and recommendation gates. If a
live bug's real fix is in predictive code, say so and stop.
