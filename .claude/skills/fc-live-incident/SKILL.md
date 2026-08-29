---
name: fc-live-incident
description: Triage a live freshness, lifecycle, settlement or publication incident with real evidence before concluding a root cause. Use when a live data channel looks stale or wrong, or a game's state or settlement seems incorrect.
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# fc-live-incident

Delegate to `fc-live-sre`. This skill exists to stop the failure mode of
**guessing a root cause and being confidently wrong**.

## Step 1 — preserve evidence before touching anything

The live state is the crime scene, and it is overwritten on a schedule. Copy
first, diagnose second:

```bash
cp docs/live.json /tmp/incident-live-$(date -u +%H%M%S).json
cp -r output/ /tmp/incident-output-$(date -u +%H%M%S)/   # if relevant
```

Capture the actual upstream feed for the affected game, and the actual workflow
run, before either rotates. A conclusion drawn after the evidence expired cannot
be checked by anyone.

## Step 2 — the four questions, answered from data

1. **Authority.** What settled this, and at what rank? `none` <
   `live_observation` < `official_final`. Did a lower authority overwrite a
   higher one?
2. **Freshness.** How old is the data actually, measured against a real clock —
   not "the file exists". When freshness cannot be established the correct
   output is `unknown`, never a plausible-looking value.
3. **Commencement.** Did the game actually start? `playEvents[].isPitch == True`
   is the **only** field structurally reserved for a real thrown pitch. Pregame
   feeds populate `abstractGameState == "live"`, `gameStatus.isCurrentPitcher`,
   `linescore.currentInning`, `.offense`, `.defense`, and even a non-empty
   `plays.allPlays` (a "Game Advisory / Status Change - Pre-Game" entry typed as
   a real `atBat`). Every one of those will lie to you.
4. **Sole writer.** Who else was writing? Two writers to one state file is
   corruption waiting for a slow day. Lock staleness is decided by **liveness**,
   not elapsed time: a verifiably live owner is never stale, a verifiably dead
   one is immediately reclaimable, and heartbeat age decides only when liveness
   is unknowable.

## Step 3 — the settlement invariant, if grading is involved

**No statistical `hit` or `miss` may be written without commencement proof** —
on the live/provisional path, the FINAL path, and durable morning grading alike.
Fail closed to `ungraded` with reason `awaiting_proof_game_actually_commenced`.

It does **not** block legitimate `void`/`cancelled`/`postponed` outcomes; those
are not statistical hits or misses, and blocking them is a different bug. The
stale-clock inverse holds too: a stale feed must not read as "still pregame".

**Enumerate every writer.** Assuming the block you are looking at is the only
one that can write `hit`/`miss` is exactly how the FINAL-path hole survived a
review.

## Step 4 — fix, with a test that fails first

Publication freeze, settlement semantics and public lifecycle stay **frozen
without explicit human authorization**. You may read and propose freely. If the
real fix lives in predictive code, say so and stop — that boundary is not yours
to cross.

Every real bug gets a regression test that fails against the old code before it
passes against the new one.
