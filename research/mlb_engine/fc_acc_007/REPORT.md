# FC-ACC-007 — published MLB Top Pick failure anatomy

**Research decision: the most consequential measured failure is selection-stage
overconfidence relative to both results and the posted price.** Test a fixed,
price-aware ranking challenger prospectively against the actual champion and
market-only ranking. Do not change public policy from this retrospective audit.
The 72% stretch target is not established by these records.

This report is derived only from deployment-proven entries in
`data/public_top_picks/registry.json` and same-slate `public_top_picks` in
`results/grades_*.json` at main `53810a27c4ac5439e0153e38589c69959593896a`.
`reconcile.py` independently joins all canonical identities and recomputes
the tables in `summary.json`. No research-board candidate is treated as a
published pick. Flat-stake return assumes one unit staked on every win/loss
and the snapshot's published American odds, with no staking on void/ungraded.
It is **documented-price** return, not verified executable or actual cash ROI.

## Reconciliation and provenance

- 648 exposed wagers, August 18–October 16, 2026, across 46 publication
  dates. 636 win/loss settlements (336 wins, 300 losses), nine voids, three
  ungraded. Hit rate is **336/636 = 52.83%**; documented-price return is
  **−11.08% per settled one-unit wager**. October 11 checkpoint exactly
  reproduces 609 settled, 321 wins, 288 losses, nine voids and −11.17% ROI.
- All 648 registry IDs have exactly one grade row, on the matching slate.
  Game/player/market/side/line/projection/price/probability and first-exposure
  timestamp in each grade row equal the registry snapshot. No duplicate ID or
  snapshot/grade mismatch was found. The registry has 46 exposed dates but
  only 45 dates with a win/loss settlement, because October 16's two picks
  remain ungraded in this snapshot.
- 646 settlement rows claim `official_final` via `durable_morning_grader`;
  two say the game was not authoritatively final. One otherwise final row is
  ungraded for a jurisdiction-specific H+R+RBI action requirement. Five
  batter voids were nonstarters with zero PA; four pitcher voids were listed
  pitchers who did not start. Voids were not scored as losses.
- No publication timestamp follows its recorded game start. All odds-fetch
  timestamps precede publication. However 34 published prices are over 30
  minutes old at exposure; 14 exceed 60 minutes, maximum 148.77 minutes.
  This is a freshness risk, not proof that the offer had been withdrawn.
- The publication-only SHA chain validates internally but contains just six
  August 18 events; **642 registry publications are not represented in it**.
  The registry and Git history remain the source for those exposures.
  Settlement corrections are visible in successive Git grade commits but
  have no append-only settlement-event ledger. The current grade files alone
  do not enumerate every historical state transition.
- The field `market_implied` has two intentional semantics in production:
  raw posted break-even for 502 batter wagers and lower, two-sided no-vig
  probability for 146 pitcher wagers. `odds_fanduel.py` documents this.
  Every price statistic here recomputes **raw break-even from market_odds**;
  mixing stored `market_implied` values would corrupt the comparison. The
  registry retains the offered line/odds and fetch time, but does not retain
  a per-selection raw FanDuel response digest, suspension state or verified
  execution receipt. Periodic normalized `data/props` observations cannot
  prove the exact first-exposure offer was executable.
- All 648 published wagers are Overs. `lineup_assumed` is null for every
  immutable snapshot, although all status reasons say “confirmed lineup.”
  The snapshot does not bind an independently archived lineup observation;
  later nonstarter voids cannot be causally assigned to a prepublication
  lineup mistake from this record alone.

## Loss concentration

Mean model probability is 64.78%, against a 52.83% realized hit rate:
**+11.94 percentage-point overstatement**. A descriptive two-way
game/player-clustered 95% interval for that gap is +8.19 to +15.70 points;
slate-clustered interval is +7.55 to +16.34. Mean raw price break-even is
59.13%, 6.30 points above realization. Published model Brier is 0.26096
versus 0.24888 when using the posted price's raw break-even on the *same
selected wagers*. That is not a candidate-pool selector contest.

| Published market | W/L | Hit | Published model | Raw break-even | Flat-stake ROI | Model gap, game/player 95% interval |
|---|---:|---:|---:|---:|---:|---:|
| Hits | 61/50 | 55.0% | 62.6% | 58.0% | −4.9% | +7.6pp [−1.0,+16.3] |
| H+R+RBI | 212/171 | 55.4% | 66.1% | 61.2% | −9.8% | +10.8pp [+6.1,+15.5] |
| Pitcher outs | 20/23 | 46.5% | 66.5% | 55.0% | −17.0% | +20.0pp [+6.6,+33.4] |
| Pitcher strikeouts | 43/56 | 43.4% | 61.2% | 54.1% | −20.5% | +17.8pp [+8.2,+27.3] |

H+R+RBI accounts for 383/636 settled wagers and 171/300 losses. The two
pitcher markets account for 142 wagers and 79 losses. Hits have a smaller
and imprecisely estimated model gap. August was 43/96 (44.8%, −25.8% ROI),
September 261/476 (54.8%, −7.2%), October 32/64 (50.0%, −18.0%); these
months also differ in market mix and schedule, and are not independent
confirmatory regimes.

| Posted odds band | W/L | Hit | Mean raw break-even | ROI |
|---|---:|---:|---:|---:|
| −199 to −150 | 169/122 | 58.1% | 62.4% | −6.9% |
| −149 to −110 | 139/146 | 48.8% | 56.8% | −14.2% |
| > +100 | 6/18 | 25.0% | 46.6% | −46.4% |

The omitted bands have only 1–18 settled picks each; `summary.json` contains
their full counts. Very expensive favorites (≤−200) are only 18 picks, so
they are not the main loss driver. The >+100 subgroup is mostly a small,
different market mix. Published 60–64.9% predictions went 174/357 (48.7%,
−14.2% ROI); 65–69.9% went 127/223 (57.0%, −8.2%). At raw model-minus-price
edge ≥15 points, 6/19 won, but high “edge” is concentrated in pitcher markets
and longer odds. Do not mine that 19-row subgroup into a retrospective rule.

The portfolio is concentrated: 507/636 settled picks occurred in 143 games
with multiple picks; 48 same-player/game pairs covered 96 picks (45 pairs
are hits plus H+R+RBI). The most crowded game had 11 picks. This can
amplify game-level variance and bankroll drawdowns; it does not explain the
negative **per-pick** mean ROI by itself. We estimate uncertainty by both
game/player and slate clusters rather than pretending 636 independent bets.

## What failed, and what is only a hypothesis

**World model:** On the selected public population, published probabilities
are overconfident in aggregate and for H+R+RBI/strikeouts; pitcher-outs
overstatement is large but based on only 43 settled picks. The market's raw
price is also above realized rate on this selected set because it includes
vig and selection effects. Hits do not show a precisely estimated defect.
This does not locate the faulty feature or establish model behavior on the
full candidate pool.

**Selector:** The model's excess over raw posted break-even grows as return
generally worsens, but market/odds mix changes simultaneously. Only 24
same-day board freezes were retained; 241 public picks lie on those dates,
but only 124 identities occur in the retained freeze and 407 public picks
fall outside freeze dates. Even an ID overlap does not authenticate the
same exposure-time price, status and full eligibility. Multiple publication
runs and overwritten daily freezes prevent an equal-volume replay. Thus
**REPLACEMENT OUTCOME: NOT MEASURABLE**. Removing losers, restricting
pitcher markets or selecting favorites would not prove more winners.

**Playing time/role:** The printed “Projected PA” text and final grade
`actual_pa_est` co-occur for 491 of 494 settled batter picks. Hits misses
averaged about 0.78 fewer PA than projected; H+R+RBI misses about 0.50
fewer. These are postgame descriptions conditional on a miss, not proof that
the shortfall was predictable before publication. Nine start-status voids
and the null lineup evidence field expose operational uncertainty. PR #228's
prior-start exit signal produced only a tiny retrospective hit-proxy gain;
PR #227's simple pitcher workload challenger was negative. Neither should
be promoted on this analysis.

## Ranked, falsifiable corrections

1. **Fixed price-aware ranking at equal volume.** Freeze the champion,
   market-only and one predeclared market/model blend over the *same*
   authentic publication-eligible offers. The intended replacement for each
   downgraded selection is the next eligible candidate on that same slate,
   with exact price and outcome frozen before first pitch. This addresses
   broad selected-set overconfidence and is operationally simpler than new
   data ingestion. It fails if the blend merely buys shorter odds, loses
   return, shrinks eligible volume, or adds no net winners. The present
   ledger cannot name historical replacements.
2. **Hitter exposure/lineup challenger for H+R+RBI and hits.** Before the
   cutoff, use authenticated confirmed batting order and a frozen recent
   starter/exit signal to alter only the PA distribution of candidates
   whose projected exposure changes materially; replace any affected pick
   with a same-slate eligible, priced hitter. This is plausible because
   383 H+R+RBI selections and many misses involve short realized PA, but
   causes are unproven and the FC-ACC-006 gain was tiny. It fails if the
   signal is already priced or does not add settled winners at matched
   volume.
3. **Start-specific pitcher role restriction.** Require timestamped,
   explicit pregame pitch/inning-limit evidence and verified starter status
   before allowing a role adjustment to Ks/outs distributions; otherwise
   leave champion forecasts unchanged. Affected pitcher picks would be
   replaced by the next same-slate authentically priced eligible candidate.
   This targets 142 published pitcher picks, but is rare/uncertain and
   PR #227's generic recent workload did not work. It fails if coverage is
   too sparse, the restriction arrives too late, or market prices already
   incorporate it.

The first experiment is the smallest valuable 2027 test. Preregister one
fixed blend and market-only comparator without choosing weights on the 2026
public outcomes. At each publication cutoff, retain the entire eligible
candidate pool, raw two-sided sportsbook responses and observation times,
lineup/role evidence, source hashes, each arm's exact ordered decisions,
identical per-slate count, and explicit no-pick/missing-slate states. Seal
before games, publish an independent external anchor under separate
authorization, then grade every arm. Primary endpoints: **added minus
removed settled winners and flat-stake return at actually offered prices**;
secondary: hit rate, overlap, odds/market composition and game/player/slate
cluster uncertainty. If complete capture or anchor authority is absent,
remain inactive and report an operational gap, not a win claim.

No production scoring, selection, public record, workflow, capture or
research PR #226/#227/#228 was changed here.
