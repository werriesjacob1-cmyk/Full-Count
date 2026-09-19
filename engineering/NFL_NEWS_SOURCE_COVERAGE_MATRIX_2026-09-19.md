# NFL News/Practice Brain — 32-Team Source Coverage Matrix (2026-09-19)

Real verification only. No reporter accreditation, practice access, or
historical reporting is fabricated anywhere in this document. A cell is
marked verified only when it was actually checked live today; every other
cell is marked `NOT_VERIFIED_THIS_PASS`, not assumed working.

This is independent of, and does not touch, the pending News Brain audit
(draft PR #146, its follow-on audit PR #151, or `team_intelligence_registry.json`,
which those PRs own). Raw check output: `engineering/evidence/nfl_news_source_coverage_2026-09-19.json`.

## Category 1 — Official league-wide sources (`nfl.com`, via `official_nfl.py`)

Real, live `official_nfl.capture()` run today discovered and fetched (all
`CHECKED_AND_FOUND`):

| Source | Real state today |
|---|---|
| `injuries_report` (league injury report page) | Discovered, accessible. **Not yet parsed into claim-ledger records** — no parser exists for this page shape (only the per-game inactive report is parsed, by PR #146's `official_inactives.parse_report`). |
| `inactives` (index page) | Discovered, accessible. Feeds the per-game inactive-report discovery already proven end-to-end (PR #146: 13 real validated claims for BUF/DET's Thursday game). |
| `transactions` (league transactions wire) | Discovered, accessible (206,732 bytes fetched). **Not yet parsed into claim-ledger records** — no parser exists for this page shape. A real, currently-untapped Tier-A source. |
| `scores`, `standings` | Discovered, accessible. Not relevant to claim ingestion (structured results, not claims). |
| Per-game inactive reports | **Exactly 1 discovered right now**: Week 2 TNF, Buffalo Bills at Detroit Lions. This is the real, current, honest state — Sunday's 14-game slate's inactive reports are not published yet (official reports post ~90 minutes pregame); this is expected, not a gap. |

## Category 2 — Official team news (`nfl.com/teams/<slug>/`)

Real HTTP check today, all 32 teams, `GET` with redirect-following:

**32/32 team pages returned HTTP 200 and are reachable right now.**

| Team | Slug checked | Reachable |
|---|---|---|
| ARI | arizona-cardinals | Yes |
| ATL | atlanta-falcons | Yes |
| BAL | baltimore-ravens | Yes |
| BUF | buffalo-bills | Yes |
| CAR | carolina-panthers | Yes |
| CHI | chicago-bears | Yes |
| CIN | cincinnati-bengals | Yes |
| CLE | cleveland-browns | Yes |
| DAL | dallas-cowboys | Yes |
| DEN | denver-broncos | Yes |
| DET | detroit-lions | Yes |
| GB | green-bay-packers | Yes |
| HOU | houston-texans | Yes |
| IND | indianapolis-colts | Yes |
| JAX | jacksonville-jaguars | Yes |
| KC | kansas-city-chiefs | Yes |
| LV | las-vegas-raiders | Yes |
| LAC | los-angeles-chargers | Yes |
| LAR | los-angeles-rams | Yes |
| MIA | miami-dolphins | Yes |
| MIN | minnesota-vikings | Yes |
| NE | new-england-patriots | Yes |
| NO | new-orleans-saints | Yes |
| NYG | new-york-giants | Yes |
| NYJ | new-york-jets | Yes |
| PHI | philadelphia-eagles | Yes |
| PIT | pittsburgh-steelers | Yes |
| SEA | seattle-seahawks | Yes |
| SF | san-francisco-49ers | Yes |
| TB | tampa-bay-buccaneers | Yes |
| TEN | tennessee-titans | Yes |
| WAS | washington-commanders | Yes |

**What "reachable" does and does not mean**: source discovered = yes, source
currently accessible = yes, for all 32. Publication-timestamp verification,
ingestion, identity binding, and prospective-research eligibility are each
**separately false for all 32** — reachability of a URL is not evidence
those downstream steps exist. No parser reads these team pages into the
claim ledger today.

## Category 3 — Press conferences, beat writers, local reporters, direct practice observations

**NOT_VERIFIED_THIS_PASS for all 32 teams.** No accredited beat-writer
identity, no press-conference transcript, no practice-access claim, and no
local-outlet URL was checked or fabricated in this pass. This matches PR
#146/#151's own honest disclosure (real coverage exists only for BUF/DET's
`OFFICIAL_INJURY_PRACTICE` channel) and does not attempt to extend it —
extending Tier B-F coverage requires per-source accreditation/access
verification that a URL-reachability check cannot substitute for, and is
explicitly out of scope for this bounded pass.

## Summary table (7 required categories x current real state)

| Category | Discovered | Accessible | Timestamp verified | Ingestion implemented | Evidence captured | Identity bound | Eligible for prospective research |
|---|---|---|---|---|---|---|---|
| Official injury/practice reports | Yes (league-wide index) | Yes | Yes (per-game, BUF/DET only) | Yes (per-game inactive report only) | Yes, 13 real claims (BUF/DET) | Yes (13/13 BOUND, per PR #151's audit) | Yes, for the 13 captured claims only |
| Official team news | Yes, all 32 | Yes, all 32 | No | No | No | N/A | No |
| Transactions | Yes (league-wide wire) | Yes | No | No | No | N/A | No |
| Coach/coordinator/player press conferences | No | No | No | No | No | No | No |
| Accredited beat writers | No | No | No | No | No | No | No |
| Local team reporters | No | No | No | No | No | No | No |
| Direct practice observations | No | No | No | No | No | No | No |

## Honest conclusion

Real, working, end-to-end Tier-A coverage exists for exactly 2 of 32 teams
(BUF, DET) on exactly 1 of 7 categories (official injury/practice reports),
via one real Thursday-night game report. Two additional real, currently-
accessible league-wide official sources (`injuries_report`, `transactions`)
were discovered today but have no parser wired to the claim ledger yet — a
concrete, bounded next step, not a new source-discovery problem. All 32
teams' official news pages are reachable, which is a real but narrow fact
(reachability, not ingestion). The remaining five categories are honestly
unstarted for all 32 teams. This document does not claim comprehensive
coverage anywhere it was not actually verified.

Alligator
