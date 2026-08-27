# Canonical durability and recovery

## What this fixes

On **2026-08-27** an idle container was reclaimed. The filesystem went with it,
including `.git` and every local ref. A canonical run that had completed **421
date checkpoints — 299 `ok`, 122 `no_games`, 0 errors, through 2025-05-27** was
destroyed, along with roughly five hours of generation.

The run *had* a remote push mechanism, and it worked. `push_manifest_snapshot()`
pushed `manifest.json` and the per-date `.meta.json` ledger to
`canonical-run-manifests`, deliberately excluding the bulk `.jsonl` rows to keep
git history small. So the **provenance survived and the data did not**: we knew
exactly which 421 dates had completed and had the rows for none of them.

That branch still exists, untouched, as the dead run's record: `d53e3ebf`, 385
`.meta.json` files through 2025-04-20. It is metadata only. **Do not try to
reconcile a rebuilt row artifact against it** — the rows it describes are gone,
so a mismatch proves nothing.

## The architecture

Branch: **`canonical-durable-checkpoints`**

```
canonical/<run_id>/index.json              recovery entry point
canonical/<run_id>/manifest.json           run contract
canonical/<run_id>/rows/<date>.jsonl.gz    rows, gzipped, write-once
canonical/<run_id>/rows/<date>.meta.json   per-date checkpoint meta
```

A date already present with a matching checksum is never rewritten, so history
grows **linearly in dates**, not in pushes × dates.

Measured on real canonical output (not estimated): **2024-04-01 → 1,537 rows,
1.22 MB raw, 67 KB gzipped (18× compression); 2024-04-02 → 1,436 rows, 1.17 MB
raw, 66 KB gzipped.** At ~66 KB per played date, the full
2024-04-01..2026-08-25 range (~600 dates with games) comes to **roughly 40 MB**
across the entire branch history — comfortably within what git handles well.

All writes use **git plumbing only** — `hash-object`, `write-tree`,
`commit-tree` against a scratch `GIT_INDEX_FILE`. Never `git add`, `git commit`,
or `git checkout`. A canonical run's worktree is a **pinned detached HEAD**, and
an earlier porcelain-based version of the manifest pusher silently advanced it,
which the run's own code-identity guard then correctly refused to run against.

## Bounded loss

`DurabilityPolicy(every_n_dates=10, every_seconds=900)`, plus a mandatory push at
the end of every invocation so a `--max-dates` chunked run never leaves an
undurable tail.

**Maximum work lost to a container death: 10 dates, or 15 minutes of
generation, whichever is smaller.** At the observed rate of the lost run
(421 dates in ~62 minutes, ≈8.8 s/date) the ten-date rule fires roughly every
90 seconds, so the 15-minute rule is a floor for slow stretches rather than the
usual trigger. Against ~5 hours lost on 2026-08-27 that is at least a 20×
reduction in the worst case.

## Durability is opt-in, and that is deliberate

`canonical_run.run()` does **not** push unless a policy is passed. Only the CLI
in `__main__` enables it.

This default was chosen after the opposite one caused a real incident during
this very work: with durability defaulting ON, running the ordinary test suite
pushed **34 synthetic run ids to the real durable branch in about three
minutes**, and 40 by the time it was stopped. Any library caller — a test, a
notebook, an analysis script — would have done the same. A remote push is a side
effect on shared state, so it belongs at the one place a human means to launch a
canonical run.

## Running a canonical backfill

```bash
python3 backtest/canonical_run.py \
    --start 2024-04-01 --end 2026-08-25 \
    --no-weather --sleep 1.0 \
    --cache-mode frozen_cache
```

Durable pushes are on by default here. `--no-durable-push` disables them and
prints a warning; use it only for a local experiment.

## Recovering after a container loss

From a **fresh clone**, with no local state at all:

```bash
python3 backtest/canonical_run.py \
    --start 2024-04-01 --end 2026-08-25 --run-id <run_id> \
    --resume-from-remote --no-weather --cache-mode frozen_cache
```

To see what is recoverable before committing to anything:

```python
import backtest.canonical_durability as cd
cd.fetch_durable_branch()
for r in cd.discover_durable_runs():
    print(r["run_id"], r["dates"], r["updated_at"], r["code_git_sha"])
```

### The resume contract

1. Read the durable index.
2. **Verify identity before writing a single byte to disk.**
3. Restore `manifest.json` if the caller has none.
4. For each date: decompress and **recompute** the sha256 over the raw bytes,
   comparing against the ledger.
5. Report what was restored; `plan_remaining()` then covers only what is missing.

### What fails closed

| Condition | Raises |
|---|---|
| run id, code SHA, schema version, date range, weather mode, repository identity, or any model artifact version differs | `IdentityMismatch` |
| restored rows or meta sha256 ≠ ledger | `DurableIntegrityError` |
| Statcast cache missing required columns, unreadable, empty, or short on coverage | `CacheIntegrityError` |
| cache mode not declared as `fresh_source` or `frozen_cache` | `ValueError` |

Silently resuming across a regime boundary would produce **one artifact
containing two regimes** — complete-looking and not comparable to itself. That is
worse than losing the run, which is why every one of these fails closed rather
than warning.

## Identity recorded per run

**Run contract**: run id, code git SHA, schema version, requested date range,
weather mode, repository identity, model/selection-policy/calibration/feature
versions, evidence regime, candidate identity fields.

**Environment** (`environment_identity()`): Python version and implementation,
platform, machine, pinned versions of nine packages that genuinely change output
(`pybaseball`, `pandas`, `numpy`, `requests`, `scipy`, `scikit-learn`,
`pyarrow`, `python-dateutil`, `pytz`), and a sha256 over the full installed set.

> The same git SHA is **not** the same scientific environment. A `pybaseball` or
> `pandas` upgrade can change the rows produced by byte-identical code.

**Source lineage** (`source_lineage_record()`): per input — source name, request
identity, retrieval timestamp, library and version, row count, schema
fingerprint, content checksum, date coverage, cache mode. This replaces the bare
`source_provider: "mlb_statsapi"` string, which covered several upstream systems
with different revision behavior. Two records with the same request identity and
different content checksums are the signature of an **upstream revision** — which
is what the six-row `platoon_xwoba` mismatch turned out to be, and which a single
string cannot express.

## Statcast cache integrity

`validate_statcast_cache()` checks required columns, parquet readability, row
count, retrieval timestamp, and actual `game_date` coverage — and fails closed.

The weakness it replaces: a cache was accepted because its **filename** covered
the requested range. A filename is a claim by whoever wrote the file. It says
nothing about truncation, missing columns, or a process that died mid-write.

`declare_cache_mode()` forces an explicit `fresh_source` / `frozen_cache`
declaration so hidden pybaseball cache state cannot silently determine the
canonical source vintage.

Observed in production on 2026-08-27, on the real warmed cache:

```
[statcast] cache hit: statcast_2024_through_2026-08-24.parquet
[statcast]   validated: 2151381 rows, 2024-03-15..2026-08-24, sha256 549a08063cb9
```

The row count, real date span, and content checksum are now recorded on every
acceptance — where previously the filename alone was the entire check.

## The proof

`test_canonical_durability.py` — **43 checks, all passing.** It reproduces total
container loss: completes 6 of 10 dates, pushes durably, then deletes the run
directory **and the entire local repository**, clones fresh from the remote, and
recovers. Nothing local survives to rescue it, because that is the only
interruption model worth testing.

Verified: rows restored byte-identical against pre-loss sha256s;
`validate_checkpoint()` accepts every restored checkpoint; resume plans exactly
the 4 missing dates and starts at the right one; completed dates are not
replanned; exact row count with no duplicate candidate identities; run identity,
environment fingerprint, source lineage and cache mode all survive; and nine
fail-closed adversarial cases.

Additionally proven against **real GitHub**, not just a local bare repo: run
`canonical-GITHUBPROOF-20260827` was pushed to `canonical-durable-checkpoints`,
then discovered and fully restored in a throwaway clone.

## Known limitations of this design

Stated rather than left for someone to rediscover.

### The loss bound does not cover Statcast warmup

`DurabilityPolicy`'s "10 dates or 15 minutes" applies **only after warmup**. The
one-time Statcast pull (2024-03-01..2026-08-24, ~2.15 M pitches, ~12 minutes)
writes its parquet **only at the end**, so a container death during warmup loses
the whole warmup. Bounded and modest, but not covered by the headline number.

Not fixed here because the alternative — pushing the 23.7 MB parquet durably —
buys ~12 minutes of protection at the cost of a large binary in git history, and
the mission's instruction was to avoid building a data lake. `~/.pybaseball`
(1.1 GB of HTTP cache) makes a re-pull faster than a cold one anyway.

### `verify_no_lookahead()` is not exercised by CI

It lives in `backtest/engine.py:1470` and is a real six-check proof — including a
positive control, so it cannot pass trivially on an empty dataset. But **no
`test_*.py` calls it**; it is referenced only in docstrings across four modules.
It needs a live store, so it cannot sit in the deterministic suite without making
CI network-dependent and flaky.

Consequence: the leakage guarantee is real but **manually verified, not
continuously verified**.

**Last run: 2026-08-27, against code SHA `68b663a3` (the SHA the current
canonical backfill is pinned to), for 2024-05-15. VERDICT: PASS, 10/10.**

The two positive controls are what make this worth anything — they rule out the
failure mode where a leakage test passes trivially because the dataset is empty:

- The unrestricted store holds **1,928,314 pitches dated 2024-05-15 or later**,
  and **4,209 from 2024-05-15 itself** — *none of which reached any input frame*.
- The cutoff is load-bearing, not decorative: **292 players gained PA** between
  `endDate=2024-05-14` and `endDate=2024-05-15` (e.g. Mark Vientos, 7 PA vs 11).

Also confirmed: all 40 logged input reads end before the date (latest row seen
anywhere: 2024-05-14); season leaderboards raise `LookaheadError` rather than
quietly returning season-to-date numbers; every rolling Statcast window ends on
or before the cutoff; the season-Statcast memo is cleared on entering each
simulated date, so a frame pulled for a later date cannot be reused for an
earlier one; and all 15 graded games are on the date itself — inputs predate it,
outcomes do not.

Re-run it by hand after any change to `backtest/engine.py`, pointing at an
already-warmed cache so it is cheap:

```python
store = StatcastStore(2024, "2024-06-01", cache_dir="<run>/backtest/.cache")
store.load()
ok, checks = verify_no_lookahead("2024-05-15", store)
```

### `hit_distance_sc` is never retained

It is absent from `STATCAST_COLUMNS`, so the backtest store drops it. That is why
runs log `⚠ Moonshot rates: Statcast is missing launch_speed/events/hit_distance_sc`.
**Moonshot and distance-based rates are structurally degraded in the historical
reconstruction.** Pre-existing; adding the column would change what the backtest
produces, which is a science change and out of scope for a durability mission.

## Scientific caveat — do not overclaim

The canonical historical dataset is **confirmed-starting-lineup historical
evidence**. It is *not* an exact replay of what Full Count knew at a specific
historical morning or publication timestamp: some market, live, and context
inputs are unavailable historically, and they must never be fabricated to close
that gap.

**Prospective full-candidate capture** remains the correct bridge to exact
live-policy evaluation.
