# LATENT: `first_inning_run` has no stable settlement identity

**Status:** recorded, NOT fixed. Deliberately left alone.
**Found:** 2026-08-28, while proving the root cause of the P0 live-board
integrity incident.
**Do not fix as part of that P0.** It is a genuinely separate defect that
happens to share a code path, and bundling it would have made the incident
fix impossible to review.

## What it is

`dashboard/live_state.py` carries two sets that disagree about whether
`first_inning_run` is a game-level market:

```python
_GAME_LEVEL_STATS      = frozenset(("nrfi_combined",))
_FIXED_HALF_RUN_STATS  = frozenset(("nrfi_combined", "first_inning_run"))
```

`_subject_identity()` consults `_GAME_LEVEL_STATS`. A `first_inning_run`
row is per-pitcher-side but is not treated as game-level, so if it ever
reached `canonical_prop_id()` without a resolvable player subject it would
raise

    ValueError: prop has no stable player/combo/game-level subject

which is the exact error that took the board down on 2026-08-28.

## Why it is NOT the cause of that incident

It reproduces the error text, which is why it was worth chasing, but it
cannot reach the dashboard. `generate_picks.py:6424` filters every
`first_inning_run` candidate out before the board is assembled:

```python
... if (c.get("projection") or {}).get("stat") != "first_inning_run"
```

The market no longer ships as a standalone board pick (see the comment at
`generate_picks.py:5787`); it survives only as an input to
`_build_combined_nrfi`. So the row never reaches `clean()`.

The real 2026-08-28 cause was a Rotowire-projected batter with no
resolvable MLBAM id (Walker Jenkins, Twins, gamePk 823666) -- proven by
instrumented reproduction against the live feed, 12 rows, all his. That is
fixed in the quarantine commit on `p0/live-board-integrity`.

This entry exists so the near-miss is on the record rather than
rediscovered, and so nobody later reads the discarded hypothesis in the
transcript as if it had been the finding.

## Why it still matters

It is armed. The moment anything re-enables `first_inning_run` as a
shipped market -- a re-promotion, a new consumer, a refactor that drops
the 6424 filter -- a side whose pitcher id fails to resolve raises inside
the per-row loop. The quarantine boundary added in this P0 now contains
that blast radius (one row is dropped, not the board), so the failure mode
is bounded, but the underlying inconsistency is still wrong.

## The actual question to answer before fixing

Not "add it to `_GAME_LEVEL_STATS`." Decide what a `first_inning_run` row
IS. It is one half-inning attributed to one pitcher, so its true subject is
arguably (game, half) rather than either a player or the whole game --
which is what `_FIXED_HALF_RUN_STATS` already half-encodes. Picking the
wrong one mints ids that collide across the two sides of the same game, and
a collided id mis-grades. Settle the semantics first; the code change is
small once that is decided.

## Suggested triage

Low urgency, low risk while the 6424 filter stands. Should be resolved
BEFORE any work that re-enables the market, not after.
