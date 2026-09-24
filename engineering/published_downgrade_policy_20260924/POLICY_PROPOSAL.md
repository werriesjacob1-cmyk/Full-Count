# Published-pick downgrade/withdrawal display policy — proposal for Jacob

Mission 12, Workstream C. Candidate branch:
`claude/published-downgrade-display-20260924`. Not merged, not deployed, not
posted to Issue #91 -- this document is for Jacob's review of the isolated
candidate.

## 1. Exact proposed customer-facing policy text

For Jacob to approve verbatim (or edit):

> Once Full Count publishes a Top Pick, we never delete it or make it
> disappear. New information -- a price move, a lineup change, or new data
> -- can change whether that pick is still one of today's actionable Top
> Picks, but it can never rewrite what we originally said, when we said it,
> or at what odds and probability. If a published Top Pick is later
> downgraded or withdrawn before first pitch, it stays visible on the Today
> page in its own clearly labelled "Published earlier — no longer a Top
> Pick" group, showing both the original published price/probability and
> the current status and reason, until midnight Central. It always remains
> part of Full Count's permanent published-pick performance record,
> unchanged by anything that happens after publication.

## 2. What changes

- **Today page display.** A same-slate, still-pregame published Top Pick
  that the current scoring pass has reclassified (e.g. to Lean, Value, or
  Neutral) or dropped entirely (e.g. a lineup scratch, a failed quality-
  control check, vanished source data) now appears in a distinct sub-group
  inside the Top Picks ("Best Bets") area: **"Published earlier — no longer
  a Top Pick."** Each such card carries a **"Downgraded after publication"**
  or **"Withdrawn"** chip, the original published odds and probability, and
  the current status/reason. It never carries the Top Pick chip and is
  excluded from "More Picks" (so it is shown exactly once, not duplicated
  under its current Lean/Value status).
- **New backend field.** `withdrawn_since_publication` (boolean, present
  only on the "dropped entirely" case) on a dashboard row. This is a
  presentation/lifecycle marker, not a new classification: the row's
  `recommendation_status` stays a member of the same four-value enum
  (`top_pick`/`lean`/`value`/`neutral`) the deployed Pages contract already
  validates (`dashboard/live_state.RECOMMENDATION_STATES`,
  `dashboard/verify_pages_artifact.py`); a withdrawn row's
  `recommendation_status` is set to `neutral` with an explicit
  `status_reasons` entry, never a fifth invented status value.
- **New optional summary count.** `summary.n_published_downgraded`: how many
  currently-displayed rows were published as a Top Pick and are not one now
  (downgraded + withdrawn combined). Purely additive; `summary.n_top_pick`
  ("Top Picks today") is unchanged and continues to count only current,
  actionable Top Picks.
- **`refresh_prices.py`** now explicitly skips repricing/reclassifying any
  row carrying `withdrawn_since_publication` (in addition to the existing
  skip for a pick carried from a different build slate).

## 3. What does NOT change

- **The immutable publication registry** (`data/public_top_picks/
  registry.json`) is never read for a different purpose, never written
  differently, and never touched by this candidate at all.
- **Grading.** `dashboard/refresh_grades.py` and `grade_results.py` /
  `build_history` (`results/history.json`) read the registry directly
  (`all_published_snapshots` / `published_snapshots_for_date`), independent
  of the Today-page reconciliation this candidate touches. Every published
  Top Pick keeps being graded exactly as before, whatever its current
  display status. (Existing `test_refresh_grades.py` and
  `test_public_top_pick_grading.py` pass unmodified against this candidate.)
- **The selector/recommendation policy** (`recommendation.py`,
  `generate_picks.py`'s selection logic) is untouched. A pick's CURRENT
  classification is still decided exactly as it always was; this candidate
  only changes how an already-published pick's *display* behaves once that
  classification later diverges from what was published.
- **Re-registration.** A withdrawn/downgraded row can never become a new
  publication candidate: `publication_registry.build_publication_manifest`
  already skips any id already in `registry["entries"]`, regardless of its
  current `recommendation_status` -- unaffected by this change, and covered
  by a new regression test
  (`test_withdrawn_pregame_pick_is_never_a_publication_candidate`).
- **Repricing.** A withdrawn row is never sent to FanDuel for a fresh quote
  (see `refresh_prices.py` change above); a downgraded row (still present in
  the current scoring pass) continues to be repriced/reclassified exactly as
  before -- that is the intended, pre-existing "current status can change"
  behavior, not a new gap.

## 4. Interaction with the public record

The immutable audit trail (`publication_snapshot` on the row, sourced from
the registry's own stored snapshot -- original `recommendation_status`,
`market_odds`, `hit_probability`, `status_reasons`, `why`, and everything
else in `publication_registry.SNAPSHOT_FIELDS`) is **never altered** by this
change. It is the single source of truth this candidate reads from, not a
second one it introduces: "published, now downgraded/withdrawn" is *derived*
by comparing `publication_snapshot.recommendation_status == "top_pick"`
against the row's own current `recommendation_status` -- never stored as an
independent flag that could drift out of sync with the two real fields it
describes. The only new stored field, `withdrawn_since_publication`, marks a
distinct *mechanism* (dropped from the current pass vs. reclassified within
it) that genuinely cannot be derived by comparing two `recommendation_status`
values, because in that case there is no "current" classification to compare
against at all.

## 5. Open question for Jacob

**Retention window for a withdrawn/downgraded card.** This candidate carries
a withdrawn/downgraded published pick on the Today page using the exact same
same-slate/Central-midnight retention rule PR #195 already established for
every other published pick (`reconcile_public_lifecycle`'s existing
`_prior_slate_still_displayed` / Central-day logic, unchanged by this
candidate). That means a withdrawn pick disappears from Today at the same
midnight-Central boundary a still-actionable published pick does, then lives
on only in History/grading via the registry, exactly like any other
published pick. Is that the intended lifecycle, or should a withdrawn/
downgraded pick instead be surfaced longer (e.g., until its game reaches a
terminal state) precisely because a customer may not have seen the
downgrade before it happened? No inspection of engineering/PROJECT_STATE.md,
ENGINEERING_HANDOFF.md, or Issue #91 turned up a prior decision on this
specific point, so it is left open rather than assumed.
