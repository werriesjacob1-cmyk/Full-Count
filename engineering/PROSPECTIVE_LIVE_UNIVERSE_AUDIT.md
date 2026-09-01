# Mission 1C — the live Hits universe: RAW CAPTURE SOURCE vs PRIMARY ELIGIBLE POOL

Protocol: `engineering/locked-protocols/FULL_COUNT_PROSPECTIVE_HITS_PA_SHADOW_PROTOCOL_V1_LOCKED_2026-09-01.md`
sha256 `5ce1ae95c4d3034d7948eb0ad7bc2441efcf2cabb234944e36bc315b2b355de7`

Evidence labels: **VERIFIED-REPO** = read in this repository at the cited line.
**VERIFIED-RUNTIME** = executed and observed. **INFERENCE** = reasoned, not
observed. **UNKNOWN** = not established.

---

## 0. The correction this document exists to make

An earlier draft of this audit treated `by_category_full["hits"]` as *the*
prospective universe. That is wrong, and the error is the kind that produces a
scoreboard which looks rigorous and measures nothing.

**`by_category_full["hits"]` is the RAW CAPTURE SOURCE. It is not the primary
eligible pool.**

It is materialized by `select_best_by_category(..., n_per_category=9999,
min_score=0)` (`dashboard/build_dashboard.py:745`, VERIFIED-REPO). Both
arguments are deliberate and are documented in the code's own comment block: a
verbatim product requirement that every market always shows *something* on the
site, so a thin slate renders a research row rather than an empty tab. The list
is therefore, by design and not by accident:

- unbounded in size (`9999`),
- unfiltered by quality (`min_score=0`),
- inclusive of **assumed-lineup** rows, because `combined_candidates =
  candidates + assumed_lineup` (`:729`, VERIFIED-REPO),
- inclusive of rows with **no posted FanDuel price**,
- inclusive of **reliability C/D** rows and rows with **no evidence sample**.

Every one of those is a legitimate research row and an illegitimate wager. The
protocol's §5 requirement is that eligibility be *earned* by each row against
predeclared, policy-independent operational gates. **The existence of a row in
`by_category_full["hits"]` must never become eligibility by convenience.**

Implemented in `backtest/prospective_eligibility.py`; 15 gates, each
individually traced. Tested in `test_prospective_eligibility.py`.

---

## 1. The capture boundary (protocol §4)

Inside `dashboard/build_dashboard.py::run_live_fetch()` (VERIFIED-REPO):

| line | event |
|------|-------|
| `:640` | `_build_and_score()` produces candidate state |
| `:652` | `quality_control()` partitions confirmed vs assumed lineups |
| `:657` | `apply_signal_weights` |
| `:666` | `odds_fetched_at` established |
| `:667-681` | FanDuel price maps built |
| `:690` | prices attached to assumed-lineup rows |
| `:729` | `combined_candidates = candidates + assumed_lineup` |
| `:731-745` | `select_moonshots` / `select_best_by_category(..., 9999, min_score=0)` |
| `:752-762` | already-started games removed |
| `:783` | `gprec.attach_recommendations(...)` |
| **HERE** | **the shadow tap** |
| `:786` | `def clean(rows)` |
| `:822-824` | `clean()` applied — scientific fields stripped |

The tap must sit exactly between `attach_recommendations` and `clean`, because
that is the only point where the board expression is complete *and*
`signals` / `prob_ci` / `reliability` / `lineup_assumed` still exist. PA-v1
scoring needs `signals`; the §5 gates need the other three. `test_prospective_
capture.py` check 8 asserts the ordering by string index in the source, so the
tap cannot drift out of the window unnoticed.

---

## 2. THE IDENTITY TRAP — `side` does not mean what it looks like

**Four distinct identity-bearing facts. None is a synonym for another.**

| fact | where it lives | what it is |
|------|----------------|------------|
| team side | `row["side"]` | `"home"` / `"away"` — which team the subject plays for (`generate_picks.py:2692`, VERIFIED-REPO) |
| wager direction | `live_state.market_side_token(row)` | `"over"` / `"under"` / `"nrfi"` / `"yrfi"` — the actual bet |
| line | `row["projection"]["value"]` | the number printed on the ticket (`0.5`) |
| threshold to win | `row["projection"]["needs"]` | the outcome required (`1`) |

`row["side"]` is a **team** side. Reading it as a wager direction would label
every home-team prop "home" and every away-team prop "away" — producing a
settlement identity that is confidently wrong for 100% of rows while remaining
perfectly self-consistent, which is why it would survive a naive round-trip
test.

`line != needs`: *Over 0.5 Hits* has line `0.5` and needs `1`.

The receipt records all four separately (`prospective_receipt.build_receipt`),
and `test_prospective_receipt.py` check 3 asserts pairwise that they are
different fields.

---

## 3. The identity primitive to reuse — do not invent one

`dashboard/live_state.py:226 prop_identity_key(row)` returns
`(game_pk, subject, stat, threshold_token, market_side_token)` — the canonical
settlement identity (VERIFIED-REPO). `canonical_prop_id(row)` derives the
stable v2 id from it.

These are reused verbatim. They are **not** overloaded into the whole receipt:
the identity key answers "which wager", while the receipt additionally carries
the epoch, the decision state, the price and its timestamp, the model versions,
and the git SHA. Collapsing the two would make a re-priced observation
indistinguishable from the sealed one.

---

## 4. The primary eligible pool — the §5 gates as implemented

All fifteen must hold. Every gate is evaluated (never short-circuited) so the
trace states *every* reason a row was excluded, not merely the first.

| gate | source of truth |
|------|-----------------|
| `stat_is_shadow_market` | `projection.stat == "hits"` |
| `canonical_identity_valid` | `prop_identity_key()` does not raise |
| `wager_expression_complete` | line, needs, direction, stat all present |
| `settlement_supported` | allow-list; `hits` settles at `grade_results.py:662` |
| `game_start_known` | schedule entry carries `start` |
| `before_publication_cutoff` | `admits_new_top_pick()` — see §5 below |
| `commencement_not_occurred` | `schedule[pk]["started"]` is False |
| `not_prior_date_resumption` | `resumed_from` / `rescheduled_from` absent |
| `lineup_confirmed` | see the absent-is-not-False trap below |
| `evidence_sample_nonzero` | `isinstance(sample_n, int) and sample_n != 0` |
| `reliability_a_or_b` | `attach_reliability()`'s grade in {A, B} |
| `real_current_price` | posted odds, and no non-`MATCHED` observation state |
| `price_freshness_valid` | `recommendation.MAX_PRICE_AGE_SECONDS` |
| `board_freshness_valid` | `recommendation.MAX_BOARD_AGE_SECONDS` |
| `no_source_integrity_hold` | see the open gap in §7 |

**No model-policy gate is present, and that is deliberate.** There is no
`predicted_prob >= 0.60` requirement and no ROI/value requirement for pool
membership. Both are mathematically conditional on the champion's own
probability; requiring them would hand the champion its own selection rule as
the challenger's entry ticket. Real odds are preserved on the receipt so
ROI/value diagnostics can still be reported separately.

### The absent-is-not-False trap (VERIFIED-REPO, `generate_picks.py:6993`)

`quality_control()` sets `lineup_assumed = True` on assumed rows and leaves the
key **entirely unset** on confirmed ones. It never writes `False`.

Transcribing the protocol's prose literally as `row.get("lineup_assumed") ==
False` therefore compares `None == False` → `False`, and **rejects every
genuinely confirmed candidate**, emptying the pool while looking like a correct
reading of the rule. The implementation accepts absent, `None`, and `False`
alike, and `test_prospective_eligibility.py` check 2 locks all four cases.

Note also that rows whose lineup is *missing* (as opposed to assumed) never
reach here at all — `quality_control()` rejects them outright — and pitcher
candidates never set the flag. For Hits, every row is `type == "batter"`.

---

## 5. The publication cutoff — a defect found and fixed

Production's rule, VERIFIED-REPO, is exactly:

```
publication_cutoff_at = prepared_at + PUBLICATION_DEPLOYMENT_LEAD_SECONDS   # 15 min
admit only if  publication_cutoff_at < game_start                           # STRICT
```

`dashboard/prepare_pages_artifact.py:403-405` computes the cutoff;
`dashboard/live_state.py:337 before_betting_cutoff()` applies the strict
comparison.

Two details are easy to get wrong, **and both errors favour the challenger**:

1. **Strictness.** The comparison is strict. A row exactly 15:00 from first
   pitch is *rejected* by production.
2. **Which clock.** `prepared_at` is the **Pages artifact preparation**
   instant, inside the later *Dashboard Pages Deploy* run — not the build
   instant inside *Dashboard Refresh*.

The first draft of the gate used `now <= game_start - 900` at build time:
inclusive where production is strict, and against an earlier clock. Both were
corrected. `admits_new_top_pick()` now mirrors production exactly, and
`prospective_epoch.regate_pool()` re-applies it against the **bound
deployment's real `prepared_at`** before any selection happens.

Without that re-gate, the capture-time pool can contain rows the artifact later
excluded, letting PA-v1 select from a strictly larger opportunity set than the
champion ever had — precisely the kind of invisible advantage that manufactures
a win at equal headline volume.

---

## 6. Champion selection is read from the artifact, not reconstructed

The champion arm is `build_publication_manifest()`'s own `candidates` list
(`dashboard/publication_registry.py`, VERIFIED-REPO), which has already applied
the real production exposure gates: `recommendation_status == "top_pick"`,
`supports_public_settlement`, `game_state in (None, "pregame")`, and
`before_betting_cutoff` against the real `publication_cutoff_at`.

The historical `predicted_prob >= 0.60` proxy is **not** used and appears
nowhere in the executable code of `backtest/prospective_selection.py`. A test
asserts this against the AST with docstrings stripped (the docstring names the
proxy precisely in order to say it is forbidden).

---

## 7. Open gap, recorded rather than papered over

**There is no production mechanism named "source-integrity hold" in this
repository.** A repo-wide search for `source_integrity` / `integrity_hold`
across `*.py` returns nothing (VERIFIED-REPO).

The §5 gate is therefore implemented as a **real, injectable check against a
hold registry that is empty by default**. A hold may be keyed by `game_pk`,
team, or canonical prop id, and all three are tested. It is a functioning gate
over an empty set — **not a satisfied requirement**.

Adjacent protections that *do* exist and are relied upon: `quality_control()`'s
rain-risk and opener rejections, the `FETCH_FAILED` vs `NOT_POSTED` distinction
in `dashboard/refresh_prices.py`, and the freshness contract. None of them is a
general source-integrity hold, and none is claimed to be.

---

## 8. Status

| step | state |
|------|-------|
| capture boundary identified | VERIFIED-REPO |
| tap implemented, non-blocking, zero production writes | VERIFIED-RUNTIME |
| §5 gates implemented and tested | VERIFIED-RUNTIME |
| identity trap closed and tested | VERIFIED-RUNTIME |
| cutoff strictness/clock defect found and fixed | VERIFIED-RUNTIME |
| source-integrity hold registry | **OPEN GAP**, empty by default |
| real live dry run against a live slate | see Mission 1 step K |
