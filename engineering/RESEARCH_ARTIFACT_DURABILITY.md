# Research Artifact Durability — Cloudflare R2 Design

Written 2026-08-26. **Design only.** No action taken against the
currently-running canonical rebuild (`backtest/rows_backfill_v2.jsonl`,
PID 1633 on `claude/realized-hit-rate-sprint-01`) -- it was not touched,
copied, or opened for writing at any point while designing this.

## Problem

`backtest/rows_backfill_v2.jsonl` is already 200MB+ and growing, gitignored
(`backtest/*.jsonl` in `.gitignore`), and exists only on this container's
local disk. A container loss destroys hundreds of MB and hours of real
work with no recovery path beyond re-running the whole backfill.
`autosave/worktree-autosave.sh`'s size gate (skip anything >1MB) is
correct and unrelated -- that's for protecting unsaved SOURCE from a
crash, not for archiving large, intentionally-generated research
artifacts. This is a different problem needing a different mechanism.

## Why Cloudflare R2

- Free tier: ~10GB storage, generous free monthly Class A/B operations,
  **zero egress fee** -- the last point matters specifically because a
  future MLflow artifact store or a research session re-downloading a
  canonical bundle for validation would otherwise pay egress on every
  pull.
- Already inside the same Cloudflare account being set up for the live
  heartbeat (section on Cloudflare dashboard access is shared), so no new
  vendor relationship.
- S3-compatible API -- any tool that speaks S3 (including a future MLflow
  artifact store, `boto3`, `rclone`) works against it without a bespoke
  client.

## What gets archived (POST-COMPLETION only)

A completed canonical rebuild produces:
1. The rows artifact itself (`backtest/rows_backfill_v2.jsonl` or
   equivalent).
2. Its state file (`*.jsonl.state.json`).
3. A new **experiment manifest** (does not exist yet -- see below).
4. A checksum file (SHA256 of the rows artifact).
5. A provenance report (already-established discipline --
   `provenance.require_single_regime()`, real `code_git_sha`).

## Manifest schema (new, minimal)

```json
{
  "artifact_name": "rows_backfill_v2",
  "branch": "claude/realized-hit-rate-sprint-01",
  "head_commit": "2ce95fe903526c62640d23659d84d37bbaf1d6d2",
  "engine_command": "backtest/engine.py --start 2024-04-01 --end 2026-08-25 --out backtest/rows_backfill_v2.jsonl --no-weather --sleep 1.0",
  "date_range": {"start": "2024-04-01", "end": "2026-08-25"},
  "row_count": null,
  "completed_dates": null,
  "no_games_dates": null,
  "errored_dates": null,
  "outcome_coverage": null,
  "candidate_identity_version": null,
  "code_git_sha": null,
  "provenance_regime": null,
  "source_fingerprints": {"mlb_statsapi": null, "fanduel": null},
  "checksum_sha256": null,
  "checksum_algorithm": "sha256",
  "archived_at": null,
  "r2_key": null
}
```
Every `null` above is filled in from the actual completed state file and
a real `sha256sum` of the actual artifact -- never invented, matching the
project's existing "absent is not zero" discipline for missing data.

## Duplicate-identity / integrity checks before archival

Before anything is uploaded, validate against the completed state file:
- intended date range vs. actual dates present (no silent gaps)
- `no_games` vs. `errored` vs. `ok` status breakdown accounted for
- row count matches the sum of per-date `rows` in the state file
- no duplicate candidate identity within the artifact (the same
  dedupe-identity check `test_candidate_dataset.py`/`test_candidate_funnel_*`
  already enforce elsewhere in this codebase -- reuse it, don't reinvent)
- `code_git_sha` is single-regime (`provenance.require_single_regime()`)

This is the "canonical artifact validation" checklist already required by
the governing prompt before the locked disagreement experiment may run --
R2 archival should happen as part of that same validation pass, not as a
separate, later afterthought.

## Upload flow (proposed, not yet implemented)

```
canonical rebuild completes
  -> validate (checks above)
  -> compute sha256
  -> write manifest.json
  -> upload {artifact, state file, manifest, checksum} to R2
     under a path keyed by branch + head_commit + date, e.g.:
     r2://fc-research-artifacts/realized-hit-rate-sprint-01/2ce95fe9/2026-08-26/
  -> HEAD-check the uploaded object (confirm size/etag match before
     trusting the upload)
  -> record the R2 key + checksum back into engineering/ENGINEERING_HANDOFF.md
     (a pointer, not a duplicate copy of the manifest)
```
"Immutable-ish": never overwrite an existing key; a re-run produces a new
timestamped path. This mirrors the same immutable-publication-snapshot
discipline already used for `docs/live.json`'s publication contract, not
a new pattern invented for this.

## What this design deliberately does NOT do yet

- Does not touch the currently-running backfill.
- Does not implement the upload script -- this is the design, not the
  code. Implementing it is a small, safe, POST-COMPLETION task for
  whenever the canonical rebuild actually finishes.
- Does not assume R2 credentials exist -- none are configured. If/when
  this is implemented, R2 API token creation is a Cloudflare dashboard
  step (phone-only, same dashboard already being used for the Worker):
  **Cloudflare dashboard -> R2 -> Manage API Tokens -> Create API Token**,
  scoped to Object Read & Write on a dedicated bucket only, stored as
  Worker/CI secrets, never committed.
- Does not upload an incomplete artifact. If a checkpoint system
  (archiving partial progress, not just completed runs) is wanted later,
  that's a deliberate future design, not an accidental side effect of
  this one.
