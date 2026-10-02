# MLB V3 prospective-collection ops (wiring only)

This branch holds **operational wiring only**. It is not part of the activated V3 implementation and changes no V3 code, prereg, coefficient or seal.

| Item | Value |
|---|---|
| Activated implementation | `504ca9cdb972d218907eb4eec13852cf5a7981d2` (v3 tree `a7c94431628c7714cfadd0e9ed911c490687ad20`) |
| Prereg | `15fb1d539c16d368bd8835254d93bb8f0ccd4609`, sha256 `5eb56f2837e25d29c5821043955eefe52c0d5f0513e9b6099ac5facb2ff63442` |
| Jacob authorization | Issue #91 comment `5953838152` (2026-10-02T13:49:54Z) |
| Activation record | evidence ref `ACTIVATION/ACTIVATION_5953838152.json`, sha256 `99d2655192506817b2ae18339adfe14dfa8131c6ed90448cbee38afba1a5794e` |

## What `v3_dispatch.py` does
Each invocation (`python3 v3_dispatch.py --repo <Full-Count clone>`) does the following:
1. Creates or reuses a detached worktree at exactly `504ca9cdb9`, and refuses to continue on any head, tree or prereg-blob mismatch.
2. Restores `v3/ACTIVATION.json` from the evidence ref and checks its sha256.
3. Installs the hash-pinned `requirements-v3.txt` if it is missing.
4. Runs the **existing** `activation.verify_activation` with `MLB_V3_ACTIVATION=JACOB_AUTHORIZED`, and refuses unless the result is `(True, [])`.
5. Plans today's America/New_York DAY and NIGHT units with the **existing** `schedule_plan.plan` (first-pitch-aware, 55-minute budget, TBD games excluded).
6. Runs the **existing** `runner.py --mode prospective` for each unit that is all of:
   - after the boundary `2026-10-02T06:00Z`;
   - not already sealed on the evidence ref;
   - within 0–75 minutes of its `latest_start_utc`.

   The runner itself:
   - re-verifies activation;
   - guards every step against first pitch;
   - seals append-only;
   - posts the #91 receipt;
   - requests two RFC 3161 tokens and verifies them from their bytes.
7. Reports a unit whose latest start has passed as `MISSED_NO_BACKFILL`, and never runs it.
8. Optionally runs `--verify-sealed`, which calls `verify_evidence.verify_unit` on every chained seal. That re-hashes artifacts, rebuilds the manifest, re-fetches the receipt, verifies the TSA tokens and replays the shadow board. **No outcomes are read.**

`--check` bootstraps, verifies and plans only. It never runs a unit. `--simulate-now` is allowed only together with `--check`.

## Schedule
- An hourly Routine (cron `7 13-23,0-1 * * *` UTC) wakes a dedicated runner session, which runs the dispatcher.
- Because the 75-minute window is longer than the 60-minute firing interval, every unit gets a firing before its latest start.
- A unit is run once. A missed deadline means the unit is lost.

Alligator.
