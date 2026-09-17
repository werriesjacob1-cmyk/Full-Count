# Player-prop settlement contract — extends `game_market_grader.py`'s pattern

Written for tonight's DET@BUF coverage expansion (2026-09-18T00:15Z kickoff).
Codex owns implementation; this is the spec, so both sides build against the
same contract instead of improvising independently.

## Why extend rather than invent

`nfl/prospective/game_market_grader.py` (PR #101, already merged pattern-wise
on main) already does exactly the right thing for game markets: deterministic,
outcome-only, fails closed on non-final/naive-time/tie/malformed input, never
mutates inputs, returns a `grade_sha256` over canonical JSON. Player props need
the same shape with three genuinely new problems game markets don't have:
a real line to compare against a stat (not just a binary winner), boolean/count
thresholds instead of two-sided lines, and — the one most likely to get missed
under tonight's deadline — **a player who had a market posted but did not
appear in the final box score is not the same fact as a player who appeared
and recorded zero.**

## New `canonical_market` values

Two DIFFERENT line shapes exist and must not be conflated -- verified against
live payloads, not assumed (see "Corrected finding" below for the mistake this
replaces).

**Primary markets** (`PLAYER_X_<STAT>_HIGH/LOW/MEDIUM`, no `_ALT_`): a single
paired OVER/UNDER line, exactly like `fanduel_passing.py`'s existing contract
-- two runners, `result.type` in {OVER, UNDER}, matching `handicap` on both
sides, one American price each.

**Alt-ladder markets** (`PLAYER_X_ALT_<STAT>_HIGH/LOW`): NOT a two-sided line.
Verified live (event 35599552, passing-props tab, sha256
`86736dcda7f34fa8035e47d804c3f8f7959f1c951ada3e792edc9ea32cfcbfa9`): each is a
ladder of independent one-sided runners, e.g. `"Jared Goff 175+ Yards"`,
`"200+ Yards"`, `"225+ Yards"` ... each runner carries `handicap=0`,
`result={}` (no OVER/UNDER side field at all), one American price, and its
threshold embedded only in `runnerName` text. Each rung settles independently:
a player can clear the 175+ rung and miss the 300+ rung in the same game, and
both are real, separate graded outcomes, not one line.

| canonical_market | source_market_type | shape | stat compared |
|---|---|---|---|
| `passing_yards` | `PLAYER_X_PASSING_YARDS_HIGH` | primary OVER/UNDER | final passing yards |
| `passing_yards_alt` | `PLAYER_X_ALT_PASSING_YARDS_HIGH` | ladder, threshold parsed from `runnerName` | final passing yards |
| `passing_touchdowns` | `PLAYER_X_PASSING_TOUCHDOWNS_HIGH` | primary OVER/UNDER | final passing TDs |
| `passing_touchdowns_alt` | `PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH` | ladder | final passing TDs |
| `rushing_yards` | `PLAYER_X_RUSHING_YARDS_HIGH/LOW` | primary OVER/UNDER | final rushing yards |
| `rushing_yards_alt` | `PLAYER_X_ALT_RUSHING_YARDS_HIGH/LOW` | ladder | final rushing yards |
| `receiving_yards` | `PLAYER_X_RECEIVING_YARDS_HIGH/LOW` | primary OVER/UNDER | final receiving yards |
| `receiving_yards_alt` | `PLAYER_X_ALT_RECEIVING_YARDS_HIGH/LOW` | ladder | final receiving yards |
| `receptions` | `PLAYER_X_RECEPTIONS_HIGH/LOW` | primary OVER/UNDER | final receptions |
| `receptions_alt` | `PLAYER_X_ALT_RECEPTIONS_HIGH/LOW` | ladder | final receptions |
| `rush_plus_rec_yards` | `PLAYER_X_RUSHING_+_RECEIVING_YARDS` | primary OVER/UNDER | rushing + receiving yards, summed after binding both stats to the SAME player, never summed across two different rows |
| `anytime_touchdown` | `ANY_TIME_TOUCHDOWN_SCORER` | YES/NO (no line) | any TD credited to the player (rushing, receiving, or return -- see settlement rule) |
| `two_plus_touchdowns` | `TO_SCORE_2+_TOUCHDOWNS` | YES/NO, threshold=2 | count of TDs >= threshold |
| `three_plus_touchdowns` | `TO_SCORE_3+_TOUCHDOWNS` | YES/NO, threshold=3 | count of TDs >= threshold |
| `four_plus_touchdowns` | `TO_SCORE_4+_TOUCHDOWNS` | YES/NO, threshold=4 | count of TDs >= threshold |
| `record_a_sack` | `TO_RECORD_1+_SACK` | YES/NO, threshold=1 | defensive player's sack count >= 1 |
| `reception_yardage_threshold` | `PLAYERS_WITH_10+/15+/20+/30+_YARDS_RECEPTION` | YES/NO, threshold=10/15/20/30 | player recorded at least one single reception of >= threshold yards (a longest-single-catch threshold -- must be checked against per-target play-by-play, not the box-score season total) |

Confirmed NOT offered on this book for this game, do not build for tonight:
completions, attempts, interceptions thrown, tackles+assists, individual
kicker/FG-made markets. (Live-checked against event 35599552, all 8 tabs,
2026-09-17.)

### Corrected finding (2026-09-17, ~16:08Z)

The first version of this spec described alt markets as two-sided OVER/UNDER,
matching the primary-market shape. That was wrong, caught before it was coded:
alt markets are one-sided threshold ladders. Confirmed independently against
the live payload a second time (sha256 match exact) before revising this
table. `_ALT_` markets therefore need their OWN normalizer branch, not a reuse
of the primary two-sided parser with a relaxed check.

## Player identity binding

Reuse `nfl/normalize/market_roster_binding.py` and
`nfl/normalize/inactive_roster_binding.py` exactly as already built for
passing yards — same GSIS-id binding, same fail-closed behavior on an
unbindable name. Do not build a second binding path for the new markets.

## The settlement states, and the one that's new

Extend `game_market_grader`'s vocabulary. Existing: `HIT`, `MISS`, `PUSH`,
`UNRESOLVED_TIE`. Add:

- **`VOID_DNP`** — the bound player has NO appearance record at all in the
  final box score (not zero stats — genuinely absent, e.g. inactive after the
  market posted, or removed from the game before recording a single relevant
  stat). This must be structurally distinct from a real `MISS` at 0. A market
  graded `MISS` asserts "he played and fell short"; a market graded `VOID_DNP`
  asserts "the premise of the bet never had a chance to resolve." Collapsing
  these is a real, direct number-quality risk if this data is ever used to
  measure anything: it would make the model's zero-outcome markets not look
  worse than they are by burying non-participation inside the miss column, but
  it would also just be a fabricated MISS in the reverse direction — the
  observation "the bet never got a chance to resolve" is different, and
  a system that hides it produces a worse number, not a better one.
- **`OVER`/`UNDER` numeric lines**: exact tie to the line (rare on half-point
  lines, real on some alt lines) → `PUSH`, matching the existing spread/total
  pattern.
- **YES/NO markets** (`anytime_touchdown`, the N+ touchdown family,
  `record_a_sack`, `reception_yardage_threshold`): no `PUSH` state exists —
  either the threshold was met (`HIT`) or wasn't (`MISS`), and `VOID_DNP`
  still applies if the player never appeared.
- **`anytime_touchdown` mechanism note**: a defensive/special-teams TD
  (pick-six, fumble return, kick/punt return) credited to a skill player who
  also has this market posted must still count as a HIT — do not restrict
  the check to rushing/receiving TDs only, or a real hit gets misgraded MISS.

## Required fields (extends the game-market shape 1:1)

Same envelope as `grade_game_market`'s return: `event_id`, `market_id`,
`canonical_market`, plus `player_gsis_id`, `line` (null for YES/NO markets),
`threshold` (for the count-based family), `final_stat_value` (null if
`VOID_DNP`), `settlement`, `source_payload_sha256`, `grade_sha256`. Inputs are
never mutated, same as the existing grader — verified by the existing test's
`test_inputs_are_not_mutated_and_grade_is_deterministic` pattern; the new
tests should include the same check.

## What "graded" means in tonight's summary — do not blur this

- `passing_yards` / `passing_touchdowns`: this is the ONLY family with a
  validated model (B0) behind it. Report both the settlement AND the B0
  prediction/edge that was recorded pregame.
- Every other market above: report settlement only. No prediction, no
  edge, no "the model said." If asked "did we get this right," the honest
  answer for these markets is "we don't have a model for this yet — here is
  what the market said and what happened," not a hit/miss framed as ours.

Alligator
