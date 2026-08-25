# Priority 4 -- durable persistence for the canonical backfill artifact

## Why this exists

`backtest/rows_canonical.jsonl` (and its inputs, `rows_backfill.jsonl`/
`rows_backfill_repair.jsonl`) are gitignored by design
(`backtest/*.jsonl`) and existed ONLY on the ephemeral session
container's local disk. Two container restarts in one session each wiped
them, forcing a ~7-hour backfill to restart. This document evaluates
realistic options to make the FINISHED artifact durable, so a future
restart never again loses a completed multi-hour computation.

## Options evaluated, against what this session can actually do

Checked against real constraints, not assumed: this session has git
push access to the repo (via the existing origin remote) and the GitHub
MCP server's tools, but **no `create_release`/`upload_release_asset`
tool exists in the available GitHub MCP toolset** (only
`get_latest_release`/`get_release_by_tag`/`list_releases` -- read-only),
no `gh`/`hub` CLI (explicitly disabled per this environment's own system
prompt), and `git-lfs` is not installed in this container
(`which git-lfs` -> not found). These are not theoretical constraints --
each was checked directly before ruling an option in or out.

| Option | Persistence | Size limit | Cost | Complexity | Auth needed | Survives container replacement | Fresh session can retrieve |
|---|---|---|---|---|---|---|---|
| A. GitHub Actions artifact | 90 days default (configurable up to 400) | 10GB/artifact (plan-dependent) | Free (public repo) or counts toward storage quota (private) | Requires converting the backfill into a GH Actions job; standard runners cap at 6h/job, our backfill has taken 7h+ -- would need date-range sharding across parallel jobs | None beyond existing repo access | Yes -- runs on GH-hosted infra, independent of this session | Yes, via `actions_get`/`download_workflow_run_artifact` (already available) |
| B. Git LFS | Indefinite (as long as repo exists) | Free tier: 1GB storage + 1GB bandwidth/month | Free tier likely exhausted by ONE ~1GB push + retrieval | `git-lfs` **not installed in this container** -- would need to be installed first, adds a real dependency | git push access (already have) | Yes | Yes, via `git lfs pull` (if installed) |
| C. GitHub Release asset | Indefinite, no separate retention window | 2GB/file | Free | Simple in principle (one gzip + one upload) | **No create/upload-release tool available to this session** -- ruled out, not a real option today | Yes | Yes, but only once a way to upload exists |
| D. External object storage (S3/GCS/etc.) | Indefinite | Provider-dependent | Provider-dependent | New credentials required | **New credentials needed -- explicitly against the standing "do not invent credentials" discipline** (same boundary as the external heartbeat's Cloudflare/GitHub-token requirement) | Yes | Only with the same new credentials |
| E. Compressed chunks committed directly to git (regular tracked files, `git add -f` overriding the gitignore rule for those specific files only) | Indefinite (lives in git history) | GitHub's hard per-file limit: 100MB without LFS | Free | Low -- gzip + split + git add/commit/push, all tools already available | None beyond existing repo access | Yes | Yes, via a normal `git pull`/`git show`, no new tooling needed at all |

## Recommendation: Option E now, Option A as a real future improvement

**Option E (gzip-compressed chunks, committed directly to git)** is the
only option that is BOTH durable AND actually implementable with this
session's real, verified tool access today -- no new credentials, no
missing tooling, no waiting on an MCP server capability that doesn't
exist. `rows_canonical.jsonl` is plain JSONL with heavily repeated field
names/values (every row shares the same ~20 keys); gzip typically
compresses this kind of data 5-10x. At ~1GB raw, a realistic compressed
size is 100-250MB -- likely 2-3 chunks under GitHub's 100MB hard limit.
Plan, ready to execute the moment the current backfill finishes:

```
gzip -c backtest/rows_canonical.jsonl > /tmp/rows_canonical.jsonl.gz
split -b 90M /tmp/rows_canonical.jsonl.gz backtest/rows_canonical_snapshot_2026-08-25.jsonl.gz.part-
sha256sum backtest/rows_canonical_snapshot_2026-08-25.jsonl.gz.part-* > backtest/rows_canonical_snapshot_2026-08-25.sha256
git add -f backtest/rows_canonical_snapshot_2026-08-25.jsonl.gz.part-* backtest/rows_canonical_snapshot_2026-08-25.sha256
git commit -m "Durable compressed snapshot of the canonical backfill (see durable_artifact_persistence_2026-08-25.md)"
```

To restore in a future session: `cat backtest/rows_canonical_snapshot_2026-08-25.jsonl.gz.part-* > /tmp/rc.gz && sha256sum -c backtest/rows_canonical_snapshot_2026-08-25.sha256 && gunzip -c /tmp/rc.gz > backtest/rows_canonical.jsonl`.

**Option A (GitHub Actions, date-range-sharded across parallel jobs)** is
the better LONG-TERM answer -- it would also solve the "backfill dies
with this session's container" problem entirely, since the computation
would run on GH-hosted infrastructure independent of this session. Not
attempted this pass: it requires writing and testing a new workflow file
that shards `backtest/engine.py`'s already-resumable date range across
several jobs (each safely under the 6-hour runner cap) and a
finalization job that concatenates + validates provenance -- a real,
non-trivial change that itself carries execution risk if a third restart
interrupted it half-built. Flagged as real future work, not silently
dropped.

## What was NOT done

Did not commit the actual compressed snapshot yet -- the source backfill
(PID 3304 at last check) has not finished. This is the concrete next
step once it does; see `HANDOFF_STATUS.md` for current backfill status.
