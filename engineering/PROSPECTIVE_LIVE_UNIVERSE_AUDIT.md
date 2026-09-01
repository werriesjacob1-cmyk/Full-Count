# Mission 1C — the live Hits expression universe, audited at source

Audited against current `main` @ `a301f25c005ef1de1ad45e17a96fa16d564f1a86`.
This is the bridge from the historical PA mechanism to the live wager
expression. Nothing here was assumed from the historical canonical row shape.

## 1. The capture boundary

`dashboard/build_dashboard.py::run_live_fetch()` is a single production
generation event. The decisive window is:

| line | what has happened |
|---|---|
| `:640` | `gp._build_and_score()` — real scored candidates |
| `:652` | `gp.quality_control(...)` → `candidates` (confirmed) + `assumed_lineup` |
| `:657` | `gp.apply_signal_weights(candidates, trust=signal_trust)` |
| `:666` | `odds_fetched_at` — ONE honest timestamp for the whole pass |
| `:667-681` | real FanDuel prices fetched and attached to confirmed candidates |
| `:690` | prices also attached to `assumed_lineup` |
| `:729` | `combined_candidates = candidates + assumed_lineup` |
| `:731-745` | `select_moonshots(...)`, `select_best_by_category(..., n_per_category=9999, min_score=0)` |
| `:752-762` | **game-start filter** — `_game_schedule(TODAY)`, drop `started` games |
| `:783` | `gprec.attach_recommendations(..., odds_fetched_at=odds_fetched_at, ...)` |
| **← TAP HERE →** | **everything above is true; nothing has been stripped yet** |
| `:786` | `def clean(rows)` |
| `:822-824` | `clean()` applied — scientific/identity fields removed for the public payload |

**The tap must sit between `:783` and `:822`.** Earlier loses recommendation
classification; later loses `player_id`/`game_pk`/`projection` because `clean()`
strips them before the payload is embedded in a public page.

No second scoring or network pass is required or permitted for the primary
scoreboard — every fact above belongs to this one event.

## 2. THE IDENTITY TRAP — `side` does not mean what it looks like

**This is the single most important finding of the audit.**

A live candidate already carries a field literally named `side`, and it is
**not** the wager direction:

```
generate_picks.py:2692   "team": gm["away_team"] if side == "away" else gm["home_team"], "side": side
```

`row["side"]` is the **home/away team side**. Writing it into a receipt as the
wager direction would silently corrupt every expression identity.

The real wager direction is `dashboard/live_state.py:191 market_side_token(row)`:
resolves `nrfi`/`yrfi` for fixed-half-run stats, then `bet_side` / `market_side`
/ `direction`, then parses a leading `"under "` in `prop`, defaulting to
`over`.

So four facts are genuinely distinct and must never be conflated:

| fact | source | meaning |
|---|---|---|
| team side | `row["side"]` | home / away — **NOT the wager** |
| wager direction | `market_side_token(row)` | over / under / nrfi / yrfi |
| line | `row["projection"]["value"]` | the posted threshold |
| threshold-to-win | `row["projection"]["needs"]` | count needed to settle a win |

The historical canonical rows carry `needs` but no independent sportsbook-side
field. **`needs` must never be generalized as the wager side in prospective
infrastructure.**

## 3. The identity primitive to reuse — do not invent one

`dashboard/live_state.py:226 prop_identity_key(row)` already returns exactly
the tuple the prospective receipt contract requires:

```
(game_pk, subject_identity, stat, threshold_token, market_side_token)
```

with `canonical_prop_id()` (`:243`) rendering it as a stable `fc2:` string and
`stable_prop_id()` (`:256`) validating a supplied id against it. It is
production code, already carries `IDENTITY_SCHEMA_VERSION = 2`, and handles
game-level and combo subjects.

**Decision: the prospective receipt binds expression identity via
`prop_identity_key` / `canonical_prop_id`, plus the receipt-only facts that
identity does not cover — decision epoch, book, and the odds observation
timestamp.** Inventing a parallel identity would create a second source of
settlement truth, which is the failure this program exists to prevent.

## 4. The Hits expression universe

`by_category_full["hits"]` after `:745`, i.e.
`select_best_by_category(combined_candidates, prices, fd, n_per_category=9999,
min_score=0)`.

Audited properties:

- **QC partition:** `combined_candidates = candidates + assumed_lineup`, so the
  universe deliberately includes **both** confirmed and assumed-lineup rows.
  Each row carries `lineup_assumed`, so the state is explicit, never silent.
  This is a real divergence from the historical canonical population and is the
  main reason the bridge had to be audited rather than assumed.
- **`n_per_category=9999, min_score=0`:** deliberately NOT the board's
  `MIN_QUALITY_SCORE` gate — the full scored universe, not the board slice.
- **Started games are already removed** at `:752-762` before recommendations
  attach, so the tap inherits pregame-only filtering rather than re-deriving it.
- **Market availability:** prices attach to both partitions from the same
  fetch; a row may still carry `market_odds=None`, and `market_fetch_state`
  distinguishes `MATCHED` / `LINE_MOVED` / `NOT_POSTED` / `FETCH_FAILED`.
- **Alternate lines:** the candidate universe can contain more than one line for
  the same player-game. `prop_identity_key` separates them by threshold, which
  is exactly why the threshold must be an identity field.

## 5. Wagerable vs scored — a distinction the scoreboard must not blur

The full scored universe is not the wagerable universe. A row with
`market_odds is None` has no purchasable expression; a `LINE_MOVED` row's
posted line is not the line the probability was computed for. The prospective
promotion scoreboard must operate on the legitimate wagerable expression
universe, and the receipt must record which universe each row belonged to
rather than letting the runner pick whichever is convenient.

## 6. Settlement support

Settlement must reuse the production grader semantics keyed to the exact
frozen receipt. `candidate_funnel_grader.load_latest_records()` collapses the
changelog to the latest candidate state and is therefore **unusable** for
grading an expression frozen at an earlier epoch — a changed line or odds move
would silently regrade a different wager.

## 7. Status

Sections 1–6 are source-verified against current main. The remaining
prospective contract clauses — decisive epoch definition, champion selection
rule for a prospective epoch, repeated-snapshot handling, and the exact
publication/deployment binding fields — are specified by
`FULL_COUNT_PROSPECTIVE_HITS_PA_SHADOW_PROTOCOL_V1_LOCKED_2026-09-01.md`
(sha256 `5ce1ae95c4d3034d7948eb0ad7bc2441efcf2cabb234944e36bc315b2b355de7`),
which is **not present in this container and not in the repository**. Those
clauses are deliberately NOT invented here.
