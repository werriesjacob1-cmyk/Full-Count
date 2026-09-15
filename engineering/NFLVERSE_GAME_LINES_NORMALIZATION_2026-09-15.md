# FULL COUNT nflverse historical game-line normalization

Date: 2026-09-15

Source class: `NFLVERSE_SCHEDULE_UNKNOWN_BOOK`

## Result

The digest-pinned `games.csv` source now has a deterministic research normalizer. A full replay normalized all 7,292 settled games and retained all 256 future or unsettled rows as explicit `UNSETTLED_GAME` exclusions. It did not infer any missing scores, lines, prices, sportsbook identity, or line timestamps.

The normalized table makes the source spread convention explicit. nflverse's positive `spread_line` means the home team was favored, so the derived sportsbook-style handicaps are `away_handicap = spread_line` and `home_handicap = -spread_line`. Cover and total outcomes include explicit `PUSH` states.

Full-file output counts:

- 7,292 normalized settled games.
- 5,311 games with complete two-sided spread prices.
- 5,308 games with complete two-sided total prices.
- Spread outcomes: 3,616 away covers, 3,480 home covers, 196 pushes.
- Total outcomes: 3,632 unders, 3,554 overs, 106 pushes.
- 256 exclusions, all future or unsettled games.

The 2026-09-14 DEN–KC source row normalizes to Kansas City covering 2.5 and the 41-point result finishing under 42.5. This is an outcome-integrity replay of the unknown-book source. It is not a retrospective substitution for FULL COUNT's sealed FanDuel capture, whose total was 43.5.

## Fail-closed contract

Every normalized row carries the source repository, exact source commit, full-file SHA-256, acquisition timestamp, and original game ID. The normalizer rejects or excludes:

- invalid source commit, digest, or acquisition-time provenance;
- missing required fields or game identity;
- duplicate game IDs;
- invalid or identical team identities;
- unset final scores;
- disagreement between scores and the provided result or total;
- missing, non-finite, or non-positive total lines.

Missing historical prices do not erase a valid line/outcome row. They remain null with explicit `spread_prices_complete` and `total_prices_complete` flags. `book_specific_eligible` and `line_movement_eligible` are always false.

No model, selector, prospective capture, grader, public output, workflow, or production path is activated by this work.

Alligator
