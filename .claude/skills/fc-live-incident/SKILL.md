---
name: fc-live-incident
description: Triage a Full Count live-freshness, lifecycle, or settlement incident with real evidence before concluding a root cause. Use when a dashboard/live data channel looks stale, wrong, or a game's state/settlement seems incorrect.
---

# fc-live-incident

Prevents the failure mode of guessing a root cause instead of pulling
evidence -- the exact discipline `fc-live-sre.md` already requires, made
easy to invoke on demand.

## Steps

1. Delegate to the `fc-live-sre` agent for anything touching game-state
   lifecycle, settlement, publication freeze, or live freshness channels.
2. Pull real evidence before concluding anything:
   - Actual GitHub Actions run IDs/timestamps (via the GitHub MCP tools),
     not an assumption about scheduling.
   - Actual current field values from `docs/live.json` / `docs/data.json`.
   - The specific channel affected -- game-state/settlement, sportsbook
     price, and board-generation freshness are SEPARATE signals with
     separate SLAs. State which one(s) are actually affected; don't
     collapse them into one "stale" claim.
3. State explicitly what's PROVEN (from timestamps/logs) vs merely
   PLAUSIBLE (an explanation not yet confirmed). Don't overstate certainty
   either direction -- see `fc-live-sre.md`'s own rule on this.
4. Confirm the sole-writer/merge-authority contract wasn't violated: an
   older observation must never regress a newer one (a stale "live" poll
   can't un-final a game; an older settlement can't overwrite a newer,
   equal-or-higher-authority one).
5. If the fix requires a settlement-rule change (`dashboard/settlement_rules.py`),
   treat it with the same explicit-authorization discipline as a model
   change -- not a routine infra tweak.
6. Update `engineering/ENGINEERING_HANDOFF.md` with the finding, pointing
   at the actual evidence (run IDs, commit SHAs, doc paths).

## When NOT to use

Routine feature work on the live pipeline that isn't an incident. Use this
specifically when something already looks wrong and needs root-causing.
