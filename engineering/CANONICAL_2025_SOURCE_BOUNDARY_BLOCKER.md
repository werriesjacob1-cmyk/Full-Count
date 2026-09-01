# CANONICAL RUN BLOCKED AT THE 2024 → 2025 SOURCE BOUNDARY

**Status: HARD BLOCKER. Not resolvable without a decision from Jacob.**
**Nothing is corrupted. No data was lost. The gate did its job.**

Run: `canonical-20260828T153143Z-2b79304f`
Pinned SHA: `fc589447ec157bff9a96071edc3ceb6c7dc734eb`
Discovered: 2026-09-01, on the fourth container-reclamation recovery.

---

## 1. What happened

The run resumed cleanly — all ten identity gates passed, 324 durable dates
restored, 0 failed — then advanced past the last 2024 date and died on:

```
backtest.canonical_durability.SourceVintageMismatch:
this run is bound to a source artifact that is not present:
  expected /root/.fc-statcast-cache/statcast_2025_through_2026-08-24.parquet
  bound    ['85c41d79570ac25169726419ed2bcb55cbba78139e8786b6005f71f40a877685']
Refusing to repull, because a fresh pull is a DIFFERENT vintage and would
silently split this run across two source regimes. Restore the exact
artifact, or start a NEW run id.
```

**This is the source-identity gate working exactly as designed.** It refused to
silently pull a fresh 2025 Statcast artifact whose vintage would differ from the
2024 artifact pulled on 2026-08-28. That refusal is correct and must not be
worked around casually.

## 2. Why it is structural, not transient

`backtest/engine.py:315` derives the Statcast store path **per calendar year**:

```python
return os.path.join(self.cache_dir, f"statcast_{self.year}_through_{self.through}.parquet")
```

So the run needs a *different file per year*:

| year | required artifact | status |
|---|---|---|
| 2024 | `statcast_2024_through_2026-08-24.parquet` | present, bound, SHA `85c41d79…a877685` |
| 2025 | `statcast_2025_through_2026-08-24.parquet` | **never pulled, never bound, not in the durable branch** |
| 2026 | `statcast_2026_through_2026-08-24.parquet` | never pulled |

The bound lineage carries exactly one record, with `request_identity`
`statcast:2024:through=2026-08-24`. The run never reached a 2025 date before
this point — it died at `[55/783]`, then at 164, then at 324 — so the 2025
artifact was never created. **It does not exist to be restored.** The durable
branch's `source/` directory holds only the 2024 file.

## 3. The internal inconsistency this exposes

`canonical_durability.assert_source_identity()` explicitly anticipates this
case. Its own docstring:

> *"A source present now but not bound is fine (a run may legitimately reach a
> source later). A source bound but MISSING now, or bound with a different
> digest, is fatal."*

But the runtime gate at `canonical_run.py:1043` implements something stricter:

```python
bound = _cd.bound_source_records(_durable_index or {})
expected = os.path.join(_sc_dir or os.path.dirname(_sc_path), os.path.basename(_sc_path))
if bound and not os.path.exists(expected):
    raise _cd.SourceVintageMismatch(...)
```

It tests `if bound` — *any* lineage record exists — `and not os.path.exists(expected)`.
It never checks whether `expected` corresponds to one of the **bound** records.

So once any source is bound, reaching a genuinely new year's artifact — which
the durability module calls legitimate — is treated as fatal. **A multi-year
canonical run cannot cross a calendar-year boundary under this code.**

The run's declared range is `2024-04-01 → 2026-08-25`. It spans three calendar
years. It was therefore never able to complete as specified.

## 4. Why this was not fixed here

Every available workaround is on the explicitly forbidden list, or breaks
canonical identity:

| option | why not |
|---|---|
| Pull the 2025 artifact fresh | A fresh pull is a different vintage. This is the exact thing the gate exists to prevent, and "refetch and pretend it is the same vintage" is forbidden. |
| Copy/rename the 2024 artifact to the 2025 filename | "Rename another source into this identity" — forbidden, and it would bind a 2024-request artifact under a 2025 request identity. |
| `--no-source-identity` | The flag's own help: *"NOT for canonical work — a run started this way cannot prove its source vintage."* |
| Patch `canonical_run.py:1043` | Changes the pinned code SHA. Canonical identity requires generation at `fc589447`. A different SHA makes the artifact mixed-regime and needs a formal equivalence proof plus an overlap replay before it could be called canonical again. |
| `--max-dates` / narrow `--end` | Already proven unsafe: `StatcastStore` derives source-vintage expectations from the truncated remaining range. |

None of these is an engineering call. **All of them are Jacob's.**

## 5. Current state — intact and safe

| | |
|---|---|
| durable tip | `cf214dd0d25b3ccf7db79ca0c05462e03675a5a5`, 2026-08-29T02:20:06Z |
| processed | **324 of 877** — 204 `ok` + 120 `no_games` + 553 `never_run` |
| source lineage | 1 record, `statcast:2024:through=2026-08-24`, SHA `85c41d79…a877685`, 2,151,381 rows |
| lineage fingerprint | `ad5513f6cebbb7fed02e1ead301a84ce77d218053849773b247b7d7ae0914ce2` — unchanged |
| environment fingerprint | `40969dfaa45ec2541d6e29d409c92eb897b20e8cf0444506c28608902c668fcf` — unchanged, zero drift |
| run id / pinned SHA | unchanged |

**No rows are lost, no identity is compromised, nothing needs repair.** The run
is stopped at a boundary it cannot cross, holding 324 verified dates.

## 6. Options, for the record — not a recommendation to act

1. **Accept 2024-only as the canonical artifact.** Re-scope the run's declared
   range to what it can actually produce under its own identity. Honest, and
   available immediately. Costs the 2025–2026 rows the HR experiment's
   train/holdout split depends on.
2. **Fix the gate at a new SHA and formally re-baseline.** The gate is
   arguably wrong — it contradicts the durability module's stated contract.
   Fixing it means a new pinned SHA, an equivalence proof, and an overlap
   replay, per `resume_canonical.sh`'s own header.
3. **Start a new run id** on the corrected code, as the error message itself
   suggests, and treat `canonical-20260828T153143Z-2b79304f` as a completed
   2024 partial.

Option 2 is the only one that yields the full 2024–2026 artifact under a single
regime, and it is the most expensive. **This is a scientific-integrity decision
about what "canonical" means for this dataset, not a scheduling problem.**

## 7. Related finding

`backtest/canonical_run.py` and `backtest/canonical_durability.py` exist at
**no branch tip in this repository** — only at pinned SHA `fc589447`, reachable
by `git fetch origin <sha>`. Recorded separately in
`.claude/CAPABILITY_MATRIX.md` on `tooling/superclaude-activation-01`. Whatever
is decided above will require touching that code, which currently has no ref
protecting it.
