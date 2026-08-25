# Front-end customer-experience audit — 2026-08-25

## PASS 2/3 UPDATE (same day, later session): Today redesign + a real P0 URL bug

Building on PASS 1 below. Two research-correctness re-audits requested
first, then Today-page simplification + Explore by Prop.

**Research Correctness Check 1 (candidate_key alternate-line safety)**:
verified empirically against 143,237 real rows from the in-progress
canonical rebuild that `line` genuinely varies across the dataset but is
currently always 1:1 with `(date, game_pk, player_id, prop_type)` --
the backtest engine only ever grades one line per player-game-market
today, so the prior 4-field key wasn't producing false positives, but it
also wasn't future-proof. Added `line` (required) and `side` (optional,
never present in real backtest rows today -- this product's backtest
only tracks Over bets) to `candidate_key()`. Full requested test matrix
added: alternate lines distinct, opposite side distinct, same candidate
reconstructed as a separate object still raises, different game/date
distinct. See `backtest/pa_opportunity_model.py`'s `candidate_key()` own
docstring for the complete empirical basis.

**Research Correctness Check 2 (Top Pick ordering, re-audited)**: the
PASS 1 fix below (`_assign_top_pick_rank()`) was itself re-examined
against an explicit "don't assume this is canonical just because it's
production code" directive. Conclusion: it is NOT an official ranking.
`generate_picks.rank_for_board()`'s reliability/edge/probability policy
belongs to a genuinely different, CAPPED pipeline (the static top10
board), while this live dashboard's `top_pick` population is UNCAPPED
(every gate-clearing candidate ships, no top-N selection at all). There
is no real "official order of already-selected Top Picks" for this
population to preserve. Correction applied: `pickCard()` no longer
renders a "TOP PICK #N" ordinal badge at all (the directive's own PASS 4
card mockup doesn't show one either) -- `p.rank` is kept only as an
internal, undisplayed backend-owned tiebreak for stable card ordering
across renders, explicitly documented as NOT a ranking claim in
`_assign_top_pick_rank()`'s own docstring.

**Source/generated file safety**: added a clear "THIS IS THE SOURCE
FILE" header comment to all three `dashboard/static/{index.html,app.css,
app.js}` files, plus `StaticSourceParityTests` in `test_build_dashboard.py`
-- a byte-for-byte comparison of every static file against its `docs/`
copy, run on every test-suite invocation (this project's own existing
pre-commit discipline). Verified the guard actually fires (deliberately
broke parity, confirmed the check failed with the right message, restored
it). This is the smallest robust protection that would have caught this
session's own earlier mistake automatically, without new build tooling.

**New P0 found and fixed**: `onRouteChange()`'s hash router discarded the
query-string half of every route (`.split("?")[0]`), so `href="#/props?
status=lean"` links -- used by the Leans and (former) Radar "See all
research →" links -- were dead: real navigation, zero actual effect, the
filter never applied. Fixed by parsing `family`/`status` params on route
entry (only when actually present in the URL, so an absent param never
resets a filter already set via the page's own UI). This also makes the
new Explore by Prop navigator (below) work via real URLs.

**Today-page PASS 2 simplification implemented**: eight competing
homepage concepts (Top Picks / Best Value / Longshots / Leans / Full
Count Radar / Suggested Parlay / Hot Streaks / Games) collapsed to the
directive's own proposed shape, with zero backend recommendation states
removed -- every card still carries its own real Lean/Value/Longshot
label:
- Glance tiles: now real tappable links into a filtered All Props view
  (previously inert numbers with nowhere to go).
- **Best Bets** (renamed from "Top Picks" as a section heading only --
  "Top Pick" stays the glossary/badge term).
- **Explore by Prop** (new): Hits / 2+ Bases / HR / H+R+RBI / Ks / Outs
  quick-nav strip with real per-family counts (a family with 0 real props
  tonight gets no chip -- never a dead tap), plus a "More" chip to the
  full board. Reuses `familyFilterValue()` (not reimplemented) so
  "moonshot" correctly maps to "home_runs".
- **More Picks** (merged Best Value + Longshots + Leans + Full Count
  Radar into one list, ranked by each item's own real edge/lift, capped
  at 18, each row still individually labeled).
- **Tonight's Games** (moved up, ahead of Trends, matching the
  directive's own ordering).
- **Trends** (renamed from "Hot Streaks," explicit "context, not a
  recommendation" subhead per PASS 13).
- **Suggested Parlay** (moved to the very bottom -- demoted, not removed;
  still the real correlation-screened engine, unchanged).

Full Count Radar's *content* wasn't deleted -- it's folded into More
Picks (same real remainder-of-the-board rows). The directive's own
"What Is Changing Right Now?" reframing for Radar was explicitly NOT
attempted (no real live-delta data exists yet to back it -- would be
exactly the kind of fabricated capability the directive forbids). Left
as a documented future direction for the Live workstream, not built.

New Node-harness checks in `test_build_dashboard.py` (URL routing
correctness, Explore by Prop's real-counts-only rendering, no invented
ordinal badge) plus the source-parity check -- 73/73 passing in that file
alone. The 6 new `candidate_key()` alternate-line tests live separately
in `test_pa_opportunity_model.py` (36/36 passing there).

---

Written as PASS 1 of the website workstream's own recommended sequence
("Audit + P0 correctness fixes" before any broader redesign). Scope
covered this pass: `docs/index.html`, `docs/app.js`/`dashboard/static/app.js`
(read in full, ~1480 lines), `docs/app.css`/`dashboard/static/app.css`
(structure + mobile breakpoints), and the relevant slice of
`dashboard/build_dashboard.py` (payload construction, ordering,
`copy_static_assets()`). This is a real, substantive first pass, not an
exhaustive one — see "Not yet covered" at the end for what PASS 2+ still
needs to look at.

**Important context this audit confirmed before looking for new
problems**: this codebase already went through a full "Phase 4" rebuild
earlier in this session (see `HANDOFF_STATUS.md` items 83-95) that
directly implemented much of what a naive audit would otherwise flag as
missing — honest empty states, frozen-publication safety on already-live
games, a real live-freshness contract with a measured (not assumed)
staleness threshold, no server-side Top-Pick capping, evidence-quality
language with a defensible definition, and a real accessibility pass
(the existing `test_build_dashboard.py` records a completed 20-combination
Playwright sweep with zero console errors and zero horizontal overflow).
Several items on the requesting directive's checklist are consequently
**already done, not gaps** — flagged explicitly below so they aren't
duplicated.

## P0 — broken or misleading (fixed this pass)

### 1. Top Pick ordering was an independently-invented frontend ranking

`docs/app.js`'s `renderToday()` sorted the `top_pick` population purely by
`market_edge` descending — a ranking computed in JavaScript, with no
backend field behind it. This is exactly what the project's own stated
frontend/backend boundary forbids ("frontend must not independently...
invent new ranking").

Traced where a real canonical order DOES exist: `generate_picks.py`'s
`rank_for_board()` (used only by the separate static top10 board/
markdown pipeline, not the live dashboard) sorts its `price_clears=True`
population by `(reliability tier, market_edge, hit_probability)`,
reusing `_RELIABILITY_ORDER = {"A": 0, "B": 0, "C": 0, "D": 1}`. Since
`TOP_PICK_MIN_RELIABILITY = ("A", "B")` means only A/B-grade candidates
can ever reach `top_pick` status at all, and A/B are ranked identically
by `_RELIABILITY_ORDER`, this reduces to edge-then-probability for the
population that matters — meaning the frontend's edge-only sort produced
numerically correct results in practice. But this was incidental, not a
designed contract: the live dashboard (`run_live_fetch()` in
`build_dashboard.py`) computes its `top_pick` population completely
independently of `rank_for_board()`/`select_main_board()` (different
function, different candidate pool), so the two orderings could silently
diverge the moment either policy changes, with nothing to catch it.

**Fix applied**: added `_assign_top_pick_rank()` to
`dashboard/build_dashboard.py` — attaches an explicit, 1-indexed `rank`
to every `top_pick` row using the same `(reliability, edge, probability)`
tiebreak, reusing `generate_picks._RELIABILITY_ORDER` via import rather
than reimplementing it. `docs/app.js`/`dashboard/static/app.js`'s
`renderToday()` now sorts by `p.rank` when present, falling back to the
old edge-only sort only for a stale cached `data.json` that predates this
field (never a second, competing policy for current data). 7 new checks
in `test_build_dashboard.py` (69/69 passing), including a dedicated case
proving the A-vs-B reliability tie resolves correctly by edge.

### 2. `docs/app.js` is a generated copy, not the source — a real risk this audit tripped over live

`dashboard/build_dashboard.py`'s `copy_static_assets()` copies
`dashboard/static/{index.html,app.css,app.js}` into `docs/` on every
build, unconditionally overwriting whatever is there. Both directories
are git-tracked, and until this audit they were byte-for-byte identical,
which makes it easy to accidentally edit `docs/app.js` directly (as this
audit initially did) — a change that silently vanishes on the next real
`build_dashboard.py` run. **This is now called out explicitly so it
doesn't recur**: `dashboard/static/` is the only real source; `docs/` is
build output. Both copies were kept in sync for this pass's fix, but any
future frontend change must edit `dashboard/static/` first.

## P1 — high friction / real gaps, not yet fixed

### 3. `docs/app.js`'s Top Pick badge and the static top10 board's own `rank` field are two unrelated numbers

Even after fix #1, the live dashboard's `rank` and the separate
`picks_{date}.json` static board's own `rank` (written by
`generate_picks.write_json()`'s `_row(i, c)`) are computed by two
independent pipelines with no shared candidate identity check between
them. They now follow the *same policy*, but are not guaranteed to
produce the *same order* on a given night, since the two pipelines can
select from different candidate snapshots (the live dashboard re-scores
independently via `run_live_fetch()`, rather than reading
`picks_{date}.json`). Worth a scoped follow-up: either reconcile these
into one true source of truth, or explicitly document that "Top Pick #1"
on the website and "Pick #1" in the static markdown board are separate,
independently-computed artifacts that usually — but do not provably
always — agree.

### 4. Search groups results into two buckets, not four

`initSearch()` in `app.js` already groups matches into "Players & Props"
and "Games" (not naive flat substring dumping — a real design choice,
not an oversight) but doesn't produce the requested `TEAM` / `GAME` /
`PLAYERS` / `PROPS` four-way split (e.g. a `TEAM` header line for
"Philadelphia Phillies" itself, distinct from the players/games that
happen to match). Current behavior is good enough to ship (not
misleading, not broken), but doesn't yet match the fuller navigation-like
grouping described in the website directive's search priority. Scoped
follow-up for a later pass, not urgent.

### 5. Today page still carries the full original taxonomy

Top Picks, the probability/value explainer, Best Value, Longshots &
High Variance, Leans, Full Count Radar, Suggested Parlay, Hot Streaks,
and Tonight's Games all compete on one page — the exact "too many major
concepts" complaint the website directive raises. `Full Count Radar`
specifically has real, honest justification in its own code comment
(the real remainder of the Lean/Value pool beyond the featured handful,
not an invented bucket) but is still a fifth-plus concept on an already
dense page. Recommend the directive's own proposed simplification (Best
Bets / Explore by Prop / More Picks / Tonight's Games / optional lower
Trends) as PASS 2 — deliberately not attempted in this pass, since it's
a real information-architecture change that deserves its own dedicated
pass with before/after verification, not a rushed addition alongside a
correctness pass.

### 6. No "Explore by Prop" quick-jump section

The homepage has no fast, mobile-friendly prop-family jump strip (Hits /
2+ Bases / HR / Ks / Outs / More) as the website directive's Priority 4
describes. The `All Props` page's family filter `<select>` covers the
same underlying data but requires a page navigation plus a dropdown
interaction, not a single tap from the homepage. Real, scoped PASS 3
work.

## P2 — polish, not urgent

- **"Live" is correctly absent from primary nav** — confirmed
  `index.html`'s nav is `Today | All Props | Games | Performance |
  Watchlist`, with no fabricated Live tab. This matches the explicit
  instruction not to market unproven live capability as production-ready;
  no fix needed, noted so a future pass doesn't "helpfully" add a fake one.
- **Casino-hype language check**: grepped `docs/`, `dashboard/static/`,
  and `dashboard/build_dashboard.py` for lock/hammer/guaranteed/can't
  miss/free money/smash — zero user-facing matches (the few hits were
  unrelated code comments, e.g. "before lineups lock"). The product
  copy already uses "Top Pick," explains probability vs. value as two
  separate questions, and explicitly frames a 67%-style probability
  honestly in the Performance page's explainer text. No fix needed.
- **Suggested Parlay** already reuses the real correlation-screened
  `parlay_builder.py` engine (not a naive same-slate leg combiner) and
  degrades honestly to nothing when fewer than 2 real legs exist (see
  `test_build_dashboard.py` check 7). Framing/copy could still be
  reviewed against the directive's "BUILD FROM TONIGHT'S PICKS, not an
  optimized parlay" language preference — a copy-only PASS 2/3 item, not
  a correctness issue.
- **Performance page** already separates current vs. legacy architecture
  completely (never blended), explains calibration in plain language,
  and is honest about the "not proven either way" market-comparison
  result rather than spinning it. Matches the directive's Priority 19
  almost exactly already.

## P3 — future / premium (Live page, "What Changed", Compare mode)

Not attempted this pass, per the directive's own instruction not to
fabricate live capability. `docs/app.js` already has real building
blocks a future Live page can build on without rework: `pollLive()` +
`ingestLiveDocument()`/`applyCachedLive()`'s field-level, authority-
ranked merge logic (settlement authority ranking, per-field staleness
timestamps) is a genuinely reusable foundation for a "what changed"
feed — it already tracks exactly this kind of delta internally for the
existing live-price/settlement overlay, just doesn't surface it as its
own UI section yet.

## Not yet covered this pass (honest scope boundary)

- Full mobile re-verification (a real Playwright sweep already exists
  from the Phase 4 work per `test_build_dashboard.py` check 13's own
  note — not re-run fresh this pass; CSS structure spot-checked and
  looks consistent with that prior pass: real breakpoints at 480/560/
  640/720/899px, explicit `min-height: 44px` tap targets on nav,
  deliberate horizontal-scroll strips for schedule/streak chips).
- Detail sheet field-by-field re-audit against the directive's proposed
  "WHY IT COULD HIT / WHY IT COULD MISS / OPPORTUNITY / MATCHUP / PLAYER
  FORM / ENVIRONMENT / MODEL vs MARKET / EVIDENCE" structure (current
  `detailBody()` already has most of this under different section
  names — a real PASS 4 comparison, not done here).
- "Why not a Top Pick?" gate-trace surfacing (Priority 8) — real gate
  data already exists (`recommendation_funnel.gate_trace()`, confirmed
  earlier this session to run on every candidate, not just rejected
  ones) but is not yet wired into the detail sheet UI. Real, scoped,
  not attempted this pass.
- Watchlist staleness/ID-collision deep audit beyond what `snapshotOf()`/
  `watchChanges()`/`freezePublishedSnapshot()` already handle (spot-read
  only, not exhaustively tested against a real multi-day id churn
  scenario).
- Compare mode (Priority 22) — not started, correctly deferred as
  explicitly optional/future in the directive itself.
