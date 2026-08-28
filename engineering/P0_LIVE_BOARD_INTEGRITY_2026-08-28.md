# P0 — Live Board Integrity, 2026-08-28

Branch `p0/live-board-integrity`. **Not merged. Not deployed.**
No model, calibration, or recommendation-threshold changes.

---

## A. What a customer actually saw

Between 06:32 and 16:40 UTC the site served a board whose projections,
lineups and candidate set were computed at **06:31:57Z** — up to **10.1
hours old** — with a **2-minute-old** FanDuel price overlay painted on top.

The freshness bar said:

> Board built 10 hours ago · odds updated 2 minutes ago

Both statements true. Neither answered the question that mattered.

Concretely, on the served board at 16:40:

| What the customer saw | What was actually true |
|---|---|
| Drew Anderson **Over 11.5** Outs Recorded, *"FanDuel hasn't posted a price for this line yet"* | FanDuel was posting **Over 14.5 at −132** |
| Kevin McGonigle 1+ H+R+RBI at **−260** | The overlay had **−230**, matched at 16:26 |
| Sal Stewart & Pete Crow-Armstrong badged **"lineup not confirmed"** | MLB had posted **both** lineups for gamePk 824638 |

## B. Root cause — proven, not inferred

`dashboard/build_dashboard.py:537`, inside the per-row loop of `clean()`:

```python
cleaned["id"] = canonical_prop_id(cleaned)
```

No exception handling. One `ValueError` aborts `clean()` → `run_live_fetch()`
→ the entire build. `docs/data.json` is never rewritten, and the site keeps
serving the last good board while `refresh_prices` faithfully repaints
current prices onto it.

The row that raised it, identified by instrumented reproduction against the
live 2026-08-28 feed:

```
Walker Jenkins · Minnesota Twins · gamePk 823666
player_id: null · lineup_assumed: true · 12 rows (6 markets × 2 sides)
```

He reached the board through `mlb_daily.py`'s **Tier-3 Rotowire fallback**,
which backfills MLBAM ids by name against the active roster:

```python
pid = roster_hit["id"] if roster_hit else None
```

Verified against MLB's own API: Walker Jenkins is on **neither** the Twins'
26-man active roster **nor** the 40-man. The id is genuinely unresolvable —
this is not a name-matching bug, and the code already anticipates the case
and logs `[MLBAM id not matched]`. `canonical_prop_id` was then correctly
refusing to mint a settlement identity for a prop with no stable subject.

**Everything upstream behaved correctly. The defect was that one
unresolvable row could delete a 2,895-row board.**

### A hypothesis that was chased and discarded

`first_inning_run` reproduces the identical error text — `live_state.py`
holds two sets that disagree about whether it is game-level. It is **not**
the cause: `generate_picks.py:6424` filters every such candidate out before
the board is assembled, so it never reaches `clean()`. Recorded separately
in `engineering/AUDIT/LATENT_first_inning_run_identity.md` and deliberately
left unfixed, per instruction.

## C. Fix 1 — quarantine, not rescue

A row with no stable identity is dropped and recorded with its reason and
full field context. The rest publish. Identity is **never synthesised from
a name** — a name is not a settleable subject, and a fabricated id
mis-grades silently.

If the failure is *widespread* it is not one bad call-up but systemic
corruption upstream, and the build **fails closed** rather than quietly
publishing a decimated board. Budget: `max(5, 2% of considered)` — an
absolute floor so a small slate is not held to a rate that rounds to zero.

Verified end to end on the real feed: **12 quarantined, 2,895 published,
all 22 sections present.** Under the old code: zero.

## D. Fix 2 — `LINE_MOVED`

`attach_market_prices` matches on the exact published threshold, so a row
the book no longer offers at that number simply fails to match — and every
non-match was reported as `NOT_POSTED`, i.e. *"the book offers nothing
here."*

That was false, and not rarely:

| Family | Rows | LINE_MOVED | MATCHED | NOT_POSTED |
|---|---|---|---|---|
| `pitcher_outs` | 23 | **17** | 5 | 1 |
| `strikeouts` | 28 | **5** | 23 | 0 |

Seventeen of twenty-three starters were displaying a line FanDuel does not
offer, each captioned as though the book were silent. Drew Anderson was not
an isolated case — just the one that got noticed.

The fix **reports and deliberately does not repair**. The published
threshold is never rewritten: a different line is a different prediction
with a different probability, and silently migrating one would let the
board be graded on a bet it never made.

## E. Fix 3 — first paint was fail-open

`boot()` rendered from `data.json` and *then* fetched `live.json`, swallowing
failure silently. `data.json` is the **base** payload: prices as of the last
build, `stale=false` on every row, and none of the suppression reasons —
all of which live exclusively in the overlay.

On the real payload:

| | Base vs. effective |
|---|---|
| Props | 2,584 |
| Different market price | **1,691** |
| Rendered `stale=false` that the overlay marks stale | **1,897** |
| Different `recommendation_status` | 47 |

So every page load briefly showed a fail-open board, and a browser that
could never reach `live.json` showed one indefinitely with nothing admitting
it. `boot()` now awaits the overlay before painting and states an
unreachable overlay out loud.

**This is where the −260 came from.** Price propagation was never broken.

## F. Fix 4 — four clocks, stated separately

`recommendation.py` had correctly refused to publish Top Picks all day
(*"board is 9.9h old (limit 4.0h)"*). The customer was simply never told.

A board carries four ages and they are not interchangeable:

| Clock | Means |
|---|---|
| `model_basis_at` | when the projections were computed |
| `lineups_observed_at` | when anyone last **looked** at the lineups |
| `market_prices_at` | when prices were read off FanDuel |
| `live_game_observed_at` | when the game itself was last checked |

**Lineups had no representation at all** — which is exactly the clock that
was ten hours behind. It is now captured at the real fetch, not at the end
of the scoring pass (an end-of-pass stamp reports lineups fresher than they
are). Today it still equals the build time, because no lineup-only refresh
exists. That is a stated limitation, not a placeholder.

Board-age staleness is now judged client-side from the model basis, so it
holds even when `live.json` is missing.

## G. Detection — and what it honestly cannot promise

`check_board_freshness.py` reads the board's **own** `generated_at`, never
git commit age (which advanced every five minutes throughout the incident
from price pushes and would have read "seconds old" the whole time). It
never writes; `docs/data.json` keeps exactly one semantic writer.

Threshold ordering is asserted, not left to comments:

| 180 min | dispatch a recovery rebuild |
| 240 min | stop publishing Top Picks (`recommendation.py`) |
| 360 min | declare the board unusable |

*Try to recover before you must suppress; suppress well before calling it
dead.*

### The scheduler will not do what the cron says

Lineup Watch declares `*/10 * * * *`. Measured over 29 consecutive runs:

- **12.4 runs/day — 9% of declared cadence**
- median gap **51 min**, worst observed gap **11.0 h**
- **0 of 29** intervals anywhere near 10 minutes
- **no run at all** in the 9.6 h covering today's lineup postings

The new watchdog runs on the same best-effort queue. Its comment now says
so rather than claiming a 20-minute SLA, and a test asserts it does not
reacquire that overclaim. **It shortens a ten-hour outage; it does not
bound one.** A real bound needs an off-platform check, outside this repo.

## H. Why no watchdog caught this

The Live Freshness Watchdog was green throughout — **correctly**. Prices
really were updating. It proves prices are moving; it says nothing about
the board underneath them. That was the entire blind spot, and it is the
one thing the new checker exists to close.

## I. A defect class this hit three times

A new live field must clear **three** whitelists:

1. `refresh_prices.LIVE_FIELDS`
2. `live_state.PRICE_FIELDS`
3. `app.js`'s `LIVE_PRICE_FIELDS` — a hand-maintained duplicate **in another
   language**, whose own comment admits it is "kept in sync … by hand"

Miss the third and the backend is right, the overlay is right, and the
browser silently never sees the field. `live_state.py` already carries two
separate comment blocks about this exact failure. The two sets are now
**asserted equal**, so the next omission is a failing test rather than a
vanishing field.

## J. Verification

Every finding above was measured against the **live** FanDuel and MLB feeds
on 2026-08-28, not against fixtures.

- Real-feed verified: Drew Anderson, Kevin McGonigle, Sal Stewart, Pete Crow-Armstrong
- **54 new regression tests** across 5 files (12 required)
- Full suite **125/125 files**, zero failures
- Browser E2E **127/127**, dashboard **147/147**
- First-paint suite **12/12** in real headless Chromium, including a blocked
  `live.json` producing the fail-closed banner

## K. What is NOT in this branch

- No merge, no deploy
- No model, calibration, or recommendation-threshold change
- No fix for the `first_inning_run` latent inconsistency (recorded separately)
- The canonical run `canonical-20260828T153143Z-2b79304f` was never touched

## L. Recommended order of merge

1. **Quarantine** (`f9792c74`) — alone stops the outage class
2. **LINE_MOVED** (`0365c630`) — stops the false `NOT_POSTED`
3. **First paint + clocks + watchdog** (`cebded5b`) — the customer-facing half

Each is independently revertable.
