# MLB slate-date contract audit: UTC build date vs Central customer day

Branch `claude/central-slate-contract-20260924`, based on PR #195
(`claude/product-published-today-ct-20260924` @ `175bf7ce1a`). Mission 12,
Workstream B. Audit date: 2026-09-24.

Reproduce every number below with:

```
python3 engineering/central_slate_contract_20260924/replay_evidence.py --since 2026-09-10
```

It reads only git objects and committed files. It writes nothing and makes no
network calls.

## 1. Verdict

1. **The slate identity is sound. The slate selector is not.**
   - Every date-derived identity in the MLB pipeline is the MLB **official
     game date**, which is the schedule API's `date` parameter:
     - `picks_{date}.json`, `board_freeze_{date}.json` and
       `grades_{date}.json`;
     - the payload `date`;
     - registry `slate_date`.
   - All **494 of 494** publication-registry entries have `slate_date` equal to
     the Central calendar date of their game's first pitch, with 0 mismatches.
     Grading, History and the ledger all key on that identity, and it is
     correct.
   - What is wrong is **which** official date the pipeline treats as
     "current". That is `mlb_daily.TODAY = datetime.now()`, the process date.
     On GitHub runners that is the UTC date, so the current slate advances at
     7 pm CDT / 6 pm CST, while that evening's games are still pregame.
2. **Real failures remain that PR #195 does not fix** (evidence in section 3):
   - **A.** Games, All Props and "Tonight's Games" drop tonight's still-pregame
     games at the first build after 00:00 UTC. This happened on 11 of 14
     nights: 1 to 5 games and 158 to 876 research rows, from about 7:10 pm CDT
     until as late as 9:15 pm CDT. PR #195 carries only *published Top Picks*.
   - **B.** The delayed 22:30 and 23:30 UTC MLB Daily runs routinely execute
     after 00:00 UTC: 28 of 81 "Picks" commits landed between 00:00 and 02:59
     UTC, including two on every night since 2026-09-14. Instead of re-scoring tonight's prime-time
     games with posted lineups, which is the reason those crons exist
     (`mlb-daily.yml` comments), they build **tomorrow's** board with almost no
     lineups.
   - **C.** The hourly market snapshots are also keyed by UTC date:
     - after 00:00 UTC, game-line snapshots stop covering tonight's late games;
     - player-prop closing prices cross slate files, which contaminates the
       value-screen settlement.
   - **D.** Minor: public History briefly lists the next Central day as its top
     row before Central midnight. Separately, the late-evening grading passes
     grade the in-progress Central slate. That is harmless because grading
     waits for a Final status.
3. **No code change was made.** The obvious candidate, `mlb_daily.TODAY` →
   the Central date, is **not safe** as a small change:
   - **Counterfactual D.** It would make the post-00:00 UTC MLB Daily runs
     overwrite the canonical `picks_{D}.json` and `board_freeze_{D}.json` with
     only the last 2 or 3 still-pregame games. For 2026-09-23 that is 10
     games, 127 pick rows and 668 frozen records, replaced by 3, then 2
     games. That silently shrinks the model-accuracy and full-board
     calibration population. AGENTS.md rule 10 forbids silently altering
     prediction history.
   - A consistent move also needs workflow edits (`value_board_$(date -u)`),
     which are out of scope here.
   - On the dashboard side, it also forces a product decision about what the
     board shows between the last first pitch and Central midnight, and
     overnight.

   A staged plan follows, with the decisions Jacob must make.

## 2. Terms

| Class | Meaning here |
|---|---|
| UTC execution date | `datetime.now()` on a runner (the process date). |
| MLB official date | The schedule API `date` param / `officialDate` (the venue-local game date). |
| Market date | The date parameter a sportsbook or odds API is queried with. |
| Public slate date | What the customer sees as "today". PR #195 sets it to the Central calendar date (`display_date`). |
| Immutable artifact identity | A file name or registry key that must never be rewritten. |
| UTC instant | A timestamp, used for provenance and ordering. It is not a date. |

## 3. Evidence

**A. Nightly rollover of `docs/data.json`** (first build whose `date`
advanced; section A of the replay):

| Rollover | At (UTC / CDT) | Prior-slate games still pregame | Last first pitch | Research rows dropped |
|---|---|---|---|---|
| 09-11 → 09-12 | 00:07Z / 7:07 pm | 4 | 02:15Z | 725 |
| 09-12 → 09-13 | 00:07Z | 1 | 01:40Z | 178 |
| 09-14 → 09-15 | 00:09Z | 3 | 01:40Z | 538 |
| 09-15 → 09-16 | 00:22Z | 3 | 01:40Z | 537 |
| 09-16 → 09-17 | 00:07Z | 3 | 01:40Z | 550 |
| 09-17 → 09-18 | 00:09Z | 1 | 01:38Z | 172 |
| 09-18 → 09-19 | 00:06Z | 4 | 02:15Z | 709 |
| 09-19 → 09-20 | 00:12Z | 3 | 01:38Z | 535 |
| 09-21 → 09-22 | 00:21Z | 1 | 01:45Z | 158 |
| 09-22 → 09-23 | 00:14Z | 5 | 02:10Z | 876 |
| 09-23 → 09-24 | 00:13Z / 7:13 pm | 4 | 02:10Z / 9:10 pm | 712 |

Concrete pair:
- `bde36d41d2` (00:02Z, `date` 2026-09-23) lists ARI@COL 00:40Z, LAA@ATH
  01:40Z, SD@LAD 02:10Z and HOU@SEA 02:10Z, with 712 research rows.
- `d668cc3566` (00:13Z, `date` 2026-09-24) lists 12 games of 09-24. Only 20
  Sept-23 rows remain, and those are PR #195's carried Top Picks.

The Games page and "Tonight's Games" therefore showed tomorrow's games while
tonight's four games were still bettable.

**B. MLB Daily runs after 00:00 UTC** (section B):
- Across the whole history, 28 of 81 "Picks" commits landed between 00h and
  02h UTC. Since 2026-09-14 it is 20 of 60: **exactly two on every one of the
  10 nights**, landing at 00:35–02:05Z (7:35–9:05 pm CDT). Each wrote
  `picks_{D+1}.json`.
- `baaad1a5f8` (00:53Z Sept 24) and `917e6976ba` (01:51Z) created
  `output/picks_2026-09-24.json`, `board_freeze_2026-09-24.json`,
  `early_look_2026-09-24.md` and the rest. Their generation times were 00:47Z
  and 01:45Z, which is 7:47 and 8:45 pm CDT on Sept 23.
- Both boards have 30 probability-basis rows, against 127 for the 23:03Z
  Sept-23 board. That is consistent with next-day lineups not being posted.
- The same runs graded "yesterday" = 09-23 while it was in progress. The
  result was `output/board_freeze_graded_2026-09-23.json` with 668 of 668
  records `ungraded` ("game not final yet (status: In Progress / Warmup /
  Pre-Game)"). It is overwritten by the next day's runs, which is how
  `board_freeze_graded_2026-09-22.json` reached 217 hits, 467 misses and 301
  ungraded.

**C. Publication registry** (section C):
- 494 entries. `slate_date` equals the Central date of the game's first pitch
  for all 494.
- 33 entries (6.7%) were published on the *previous Central evening*. These
  are the "early picks" produced by UTC-rollover builds, and PR #195 labels
  them "Early picks for <date>".

**D. Counterfactual Central build date** (section D):
- `picks_2026-09-23.json` at `b42a17fe8c` (generated 23:03Z) covers 10 games
  and 127 rows. `board_freeze_2026-09-23.json` has 668 records over the same
  10 games.
- A Central-dated run at 00:47Z would rebuild 2026-09-23 from the 3 games still
  pregame. At 01:45Z it would use 2.
- `generate_picks.archive_existing_picks` keeps the earlier board only as
  `picks_{D}_{stamp}.json`. `grade_results.grade_day` grades only the canonical
  `picks_{D}.json`, and the board freeze is also replaced wholesale.

**E. Market snapshots:**
- `data/odds/odds_2026-09-24.json`, snapshot at 01:09Z Sept 24 (8:09 pm CDT
  Sept 23): the Action Network query was `date=20260924`. Every row belongs to
  the 09-24 slate (start times 09-24 and 09-25 UTC). None of Sept 23's late
  games is captured after 00:00Z.
- `data/props/props_2026-09-23.json` holds 6,239 rows from **Sept-22-slate**
  games captured at 00:03Z Sept 23. `grade_value.closing_prices("2026-09-23")`
  keys on `(player, stat, needs)` with no game, so 718 of its 8,189 "closing"
  keys come from Sept-22 games.
- `props_2026-09-22.json`'s last snapshot is at 21:49Z, so the true closes for
  the Sept-22 night games sit in the Sept-23 file.

**F. History:** `0b12922081` (grading catch-up at 03:56Z Sept 23, which is
10:56 pm CDT Sept 22) published `docs/history.json` with a top row dated
`2026-09-23` (0/0, pending). That is the *next* Central day.

## 4. Contract table

| # | Contract | Code location | Current semantics | Observed failure (evidence) | Proposed handling |
|---|---|---|---|---|---|
| 1 | Game identity and official start | `mlb_daily.fetch_lineups` (`gameDate` → `game_start_utc`), `build_dashboard._game_schedule`, `live_state.before_betting_cutoff` | `game_pk` plus a UTC instant; the cutoff compares instants | None. Instant-based, so the time zone does not matter | Keep as is |
| 2 | Slate identity | schedule `date=` param in `fetch_lineups`, `_game_schedule`, `grade_results.fetch_game_statuses` | MLB official date | None: 494/494 registry entries match the game's Central date | Keep. **Immutable** |
| 3 | Current-slate selector | `mlb_daily.py:56-57` `TODAY`/`YESTERDAY = datetime.now()` | UTC execution date, computed once at import | A and B: nightly | Stage 1/2 (section 6), Jacob decides |
| 4 | Statcast window caps | `mlb_daily.py:62-71` `L*_END = YESTERDAY`, `:779` season cap | UTC date minus 1 | **Suspected, not demonstrated.** Between 00:00 and 05:00Z, `YESTERDAY` is the in-progress US day. The 2026-08-13 root-cause note says Savant rejects that day, which would degrade windowed inputs in post-00Z runs. There are no committed run logs to prove it | Follows #3. Check the post-00Z run logs before relying on it |
| 5 | Pick generation artifacts | `generate_picks.py:66-67, 3640-3671, 4953-5016, 5185-5265`: `picks_`, `top10_picks_`, `early_look_`, `board_`, `full_board_`, `parlay_example_`, `board_freeze_{TODAY}` | UTC execution date → artifact identity | B: post-00Z runs write **tomorrow's** files at 7 to 9 pm CT. D: a naive Central switch would shrink `D`'s canonical population | Stage 2 needs a canonical-overwrite guard *before* any date switch |
| 6 | Inputs keyed on `TODAY` | `fetch_lineups(m.TODAY)`, Action Network `generate_picks.py:500`, `stable_base_rate(..., m.TODAY)`, `mlb_sources` windows, `fetch_bvp(TODAY)` | Follow #3 | Consistent with #3 | Move together with #3, never piecemeal |
| 7 | Dashboard build slate | `build_dashboard.py:644, 762, 817` (`gp.m.TODAY`), `:1737` final schedule | UTC execution date | A: 11 of 14 nights | Stage 1, Jacob decides the late-evening rule |
| 8 | Customer day | `build_dashboard.DISPLAY_TIMEZONE`, `display_slate_date`, `_prior_slate_still_displayed`, and the browser's `centralDateNow` (PR #195) | Central calendar date | Covers Top Picks only (by design) | Keep. It is the reference for any change |
| 9 | Publication identity | `publication_registry.py:199, 216` `slate_date = payload["date"]` | Official date of the build slate | None in identity. 33/494 were published on the previous Central evening | **Never rewrite.** New entries keep "slate_date = the game's official date" |
| 10 | Publication and deploy times | `prepare_pages_artifact.py:256, 304`, `confirm_publication.py` | UTC instants (Last-Modified) | None | Keep (provenance) |
| 11 | Candidate and offer timestamps | `recommendation.py:122`, `build_dashboard.py:667, 785` (`odds_fetched_at`, `board_generated_at`), `lineups_observed_at` | UTC instants | None | Keep |
| 12 | Pregame freeze | `board_freeze.py` (`sealed_at`, `game_start_times`); `generate_picks.py:4953-4966` (`date=m.TODAY`) | File: UTC execution date. Contents: instants | B: `board_freeze_{D+1}` is created at 7 to 9 pm CT, then replaced by D+1's runs | Same guard as #5 |
| 13 | Freeze grading | `grade_board_freeze.py:61`: UTC now minus 1 day (explicit UTC) | UTC execution date minus 1 | Post-00Z runs grade the in-progress Central day and commit an all-ungraded file (evidence B); the next day overwrites it | Harmless. Follow #3 in Stage 2 so it grades only completed Central days |
| 14 | Canonical and public grading | `grade_results.dates_needing_grading` (back 1..14 days from `datetime.now()`, plus every registry `slate_date`, plus a 3-day UTC correction window); `grade_day(date)` | UTC execution date as the anchor. Grading waits for Final status. Idempotent per date | The 04:17Z catch-up (11:17 pm CDT) grades the in-progress Central day, leaving it partly ungraded and retried. `grades_2026-09-24.json` was created at 09:21Z before any game, through registry early picks | Correct today. See section 5 ("No duplicate grading") |
| 15 | `grade_results.YESTERDAY` | `grade_results.py:33` | Documentation only; `main()` reads `GRADE_DATE` directly | Nothing reads it | Leave it, or delete it in Stage 2 |
| 16 | Live grading | `refresh_grades.py:317, 364` `date = row slate_date or payload date` | Official date per row | None: carried picks use their own `slate_date` | Keep |
| 17 | Public History | `build_history.py:55-88` (grade files by slate date; UTC retention) | Official date. Rows appear as soon as a grade file exists | F: the next Central day appears before Central midnight | Stage 1b: hide or label rows with `date > display_date` |
| 18 | Lineup watch | `dashboard/check_lineups.py:55` `today()`, `lineup_watch_state.json` | UTC execution date | Consistent with #7 now. After 00:00Z it watches tomorrow (the live observer's `reconcile.py` is the load-bearing path) | Must move together with #7 |
| 19 | Scratch re-check and value board | `check_scratches.py:154` (`--date` default is the UTC date); `mlb-daily.yml`: `value_board_$(date -u +'%F').json`; `final_card.py:218`, `render_board.py:317`, `parlay_builder.py:233`, `render_full_board.py:265` defaults | UTC execution date | Consistent with #5 now | Stage 2 must change these together, including the **workflow** |
| 20 | Game-line snapshots | `odds_snapshot.py:91` `odds_{UTC}.json`; Action Network `date=` | Market date = UTC execution date | E: after 00:00Z, tonight's late games are no longer captured | Stage 3: query the Central date. The file identity stays |
| 21 | Prop snapshots and value settlement | `prop_snapshot.py:175` `props_{UTC}.json` (all open FanDuel events); `grade_value.closing_prices(date)` keyed without game | UTC execution date; not slate-scoped | E: 718 of 8,189 "closing" keys for 09-23 come from 09-22 games. Real closes for night games sit in the next file | Stage 3: scope closes by event start or official date across D and D+1. **Re-settles `results/value_screen_record.json`**, so Jacob decides |
| 22 | Reconciliation | `dashboard/reconcile.py:310-320` (`published_slate_date` vs board date) | Official date | None (PR #195) | Keep |
| 23 | Ledger integrity | `ledger_integrity.py` | No dates (identities) | None | Keep |
| 24 | Prediction ledger | `dashboard/prediction_ledger.py:231-235` `grades_{slate_date}` | Official date | None | Keep |
| 25 | Data-package display time | `mlb_daily.py:301-302` `gameDate - 4h`, labelled "ET" | EDT hard-coded | In EST, after Nov 1 (postseason), the "ET" label and `game_hour` are off by 1 h | Low. Use `ZoneInfo("America/New_York")` separately |
| 26 | Backtest point-in-time | `backtest/engine.py:477-481, 1519` reassigns `m.TODAY`, `YESTERDAY` and `L*` | Simulated date | None | Any helper must keep these module attributes assignable |

### Workflow schedule against the day boundary

| Workflow | UTC cron | CDT (CST) | "Today" it computes | Crosses 00:00Z? |
|---|---|---|---|---|
| `mlb-daily.yml` | 14:30, 15:30, 17:00, 20:00, 22:30, 23:30 | 9:30a–6:30p (8:30a–5:30p) | UTC date, equal to the Central date **when on time** | **Yes in practice.** The concurrency queue plus GitHub delay push the last 1 or 2 runs to 00:35–02:05Z, which build D+1 |
| `dashboard-refresh.yml` | 13, 15, 17, 19, 21, 23, **01, 03** | 8a … 6p, **8p, 10p** (7a … 5p, 7p, 9p) | UTC date | **01 and 03 always** build D+1 during Central day D. Lineup-watch and MLB Daily dispatches add more |
| `mlb-grading-catchup.yml` | 04:17, 06:17, 08:17, 10:17, 12:17 | **11:17p**, 1:17a, 3:17a, 5:17a, 7:17a (**10:17p**, **12:17a**, …) | Back 1..14 from the UTC date, plus registry dates | 04:17 targets the in-progress Central day (grading waits for Final, so it is harmless). Under CST, 06:17 is 12:17 am, just after midnight |
| `dashboard-live.yml` | every 5 min | — | None of its own (payload plus row `slate_date`) | No |
| `lineup-watch.yml` | every 10 min | — | `check_lineups.today()`, the UTC date | Watches D+1 after 00:00Z |
| `odds-snapshot.yml` | hourly | — | UTC date for the file and the Action Network query | Yes (evidence E) |
| `ledger-integrity.yml` | :17 every 4 h | — | None | No |
| `calibration-recheck.yml` | Mon 09:00 | — | Commit label only | No |
| `live-freshness-watchdog.yml`, `dashboard-deploy.yml` | every 5 min / dispatch | — | UTC instants only | No |

The question the task raised: under a Central `TODAY`, should the 04:17Z
catch-up, which runs at 11:17 pm CDT on the same Central day, grade "the day
before"? That is **correct and harmless**:
- `grade_day` is an idempotent rewrite keyed by date.
- Day D would be graded at 06:17Z, which is 1:17 am CDT or 12:17 am CST, once
  its games are final. The 14-day window and the registry-date loop still
  catch everything.
- Today, the same 04:17Z slot grades day D while it is in progress.

## 5. Migration risks

- **Midnight.**
  - `TODAY` is computed once at import, so a run that straddles midnight
    stays on one slate. That is good, and any helper must keep it.
  - A Central selector moves the stale window from 7 to 9 pm CT (when people
    bet) to 12 to 8 am CT. That window is not new: builds still stop
    overnight today, and the browser already applies its 4-hour board-age
    limit.
  - After Central midnight, PR #195 already expires the previous day's Top
    Picks in the browser.
- **DST.**
  - The Central offset moves from −5 to −6 on Nov 1 2026, which can fall
    inside the World Series. The spring change (Mar 8) is before Opening Day.
  - UTC crons do not move, so in CST:
    - the rollover is at 6 pm;
    - Central midnight is 06:00Z;
    - the 06:17Z catch-up lands 17 minutes after midnight.
  - Use `ZoneInfo("America/Chicago")` and never a fixed offset. See #25 for
    the one fixed-offset display bug.
- **Late West Coast games.** A 7:10 pm PT first pitch is 02:10Z (9:10 pm CDT)
  and ends around 05:10Z, which is after Central midnight. Under any
  selector:
  - the game stays on its official date `D`;
  - the live and suspended carry (PR #195) keeps its Top Picks while it is
    live;
  - it grades under `grades_{D}` the next morning.
- **Doubleheaders.**
  - Both games have distinct `game_pk`s and the same official date. The
    selector does not change that.
  - Game 2 of a traditional doubleheader has a placeholder start time.
    `bettable_games` and status gating already handle it.
  - The only risk is a Central selector that tests "is any game of D still
    pregame": game 2 keeps D current until it starts.
- **Delayed and suspended games.**
  - A rain-delayed game is still "live" and is carried.
  - A suspended game keeps its `game_pk` and original official date. Its
    resumption can appear on a later date's schedule. Grading retries for 14
    days through direct `game_pk` lookup.
  - PR #195's still-open tuple leaves out the "delayed" status. That is a
    known limitation and is unchanged here.
- **Postponed games.**
  - The makeup may keep its `game_pk` under a new official date.
  - Registry `slate_date` stays the original date, and grading voids or
    retries against the original date's status.
  - Unchanged by any selector. Do not re-key the registry.
- **Next-day settlement.** Grades are keyed by the slate's official date, not
  by grading time. Moving the selector does not change `grades_{D}` names or
  the registry dates they collect.
- **Existing artifact names and registry `slate_date`.** All historical files
  and registry keys already equal the official date (494/494). A selector
  change touches only **future** artifacts. Nothing may be renamed or
  re-keyed:
  - the 33 prior-evening entries stay as they are;
  - the past next-day files from post-00Z runs (`picks_{D+1}` created on
    evening D) were overwritten by D+1's own runs and archived, and stay as
    they are.
- **No duplicate grading.**
  - `grade_day` rewrites `grades_{D}` whole.
  - `history.json` de-duplicates by date.
  - Public picks are idempotent by canonical ID.
  - Canonical and registry populations are counted separately by design.
  - A selector change can make one catch-up slot re-grade D−1, which is an
    idempotent overwrite. It cannot add rows.
  - The real population risk is the **canonical overwrite** (counterfactual
    D), not double counting.

## 6. Staged plan (no code in this PR)

- **Stage 0 (done here):** this audit and a replay script.
- **Stage 1: the dashboard slate selector (fixes A).** One helper,
  `build_dashboard.build_slate_date(now, schedule_for)`. It returns the
  Central date `D` while `D`'s schedule still has a pregame game (or, by
  Jacob's choice, until Central midnight). After that it returns `D+1`.
  - `run_live_fetch` would set `gp.m.TODAY` or `YESTERDAY` from it before
    scoring. The build process writes no canonical files, so `picks_{D}` and
    the freezes are untouched.
  - `check_lineups.today()` uses the same helper.
  - Registry `slate_date` stays equal to the official date.
  - Tests required:
    - 18:59/19:00 CDT with late games pending;
    - the last first pitch;
    - 23:59/00:00 CDT;
    - DST start and end;
    - a doubleheader whose game 2 is pending;
    - a schedule outage, which falls back to today's UTC behaviour and never
      to an empty board.
- **Stage 1b (fixes F):** `build_history` or the frontend hides or labels
  `date > display_date` as "upcoming".
- **Stage 2: MLB Daily (fixes B).** First add a canonical-overwrite guard: a
  run must not replace `picks_{D}.json` or `board_freeze_{D}.json` with a
  board covering fewer games than the existing one after `D`'s first pitch.
  Merging is the alternative.
  - Then move `mlb_daily.TODAY`, `YESTERDAY` and the `L*` windows to the
    same Central helper, together with `check_scratches`,
    `grade_board_freeze` and the **workflow's**
    `value_board_$(date -u +'%F')` in one change.
  - Every workflow change needs its own authorisation.
- **Stage 3: market snapshots (fixes E).** `odds_snapshot` queries the Central
  date. `grade_value.closing_prices` scopes rows by event start or official
  date, reading `props_{D}` and `props_{D+1}`. This re-settles
  `results/value_screen_record.json`, so it is announced as a correction, not
  done silently.

## 7. Decisions for Jacob

1. **Late-evening board.** After the last first pitch of Central day `D` and
   before Central midnight, should the Games and All Props board:
   - (a) roll to `D+1` (today's behaviour, keeps "Early picks for <date>");
   - (b) stay on `D` until Central midnight, with nothing pregame left?

   Before the last first pitch, keeping `D` is not controversial. Evidence A is
   a defect under either choice.
2. **Evening publication of next-day Top Picks.** Under (b) the 33-style
   evening "early picks" (6.7% of the registry) stop, and `D+1` Top Picks
   first publish after Central midnight. Is that acceptable?
3. **Canonical daily board.** Should `picks_{D}.json` and
   `board_freeze_{D}.json` (the model-accuracy and calibration population) be:
   - (a) the last full-slate board before first pitch, protected by a guard;
   - (b) the merged union of every pregame board;
   - (c) unchanged, with Stage 2 skipped?

   Stage 2 cannot ship before this is decided.
4. **Value-screen re-settlement** (Stage 3): can `results/value_screen_record.json`
   be re-settled with slate-scoped closing prices?
5. **Workflow authority:** Stages 2 and 3 need edits to `mlb-daily.yml` (the
   value board file name) and possibly `odds-snapshot.yml`.

Alligator
