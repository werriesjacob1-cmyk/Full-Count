# Competitive Parity Matrix — FULL COUNT vs. OddsJam / Outlier / Action Network / Pikkit

Written 2026-08-26. Research-only, evidence-based comparison — not a clone
roadmap. FULL COUNT is not trying to become any of these four products;
this exists to separate what's genuinely TABLE STAKES (users of serious
competing products will notice its absence) from what's a real
DIFFERENTIATOR (FULL COUNT's own proprietary-model approach that none of
these four attempt) from what's simply NOT NEEDED (a feature that fits
those products' business models, not FULL COUNT's).

## What was actually researched (methodology, so gaps are honest)

Direct official-page fetches succeeded for **Outlier** (outlier.bet) and
**Pikkit** (pikkit.com/pikkit.com/pro). **OddsJam** (oddsjam.com) and
**Action Network** (actionnetwork.com) returned HTTP 403/blocked to
direct fetch this session — their entries below rest on multiple
cross-checked third-party review sites and app-store listings, not a
first-party fetch. Anything below marked UNVERIFIED should be treated
as exactly that, not as fact. Full source detail lives in this session's
research agent output; this file distills it into the parity decision.

## The four products, in one line each

- **OddsJam**: +EV/arbitrage-finding, multi-book odds-comparison
  platform for professional/sharp bettors. Not MLB/prop-specific — broad
  sport coverage, broad book coverage (100-150+ books claimed).
- **Outlier**: player-prop-focused research tool (hit-rate data, EV+
  alerts, one-click bet placement into a connected book). Closest of the
  four to FULL COUNT's actual product shape (props-first), but market-
  derived (no proprietary probability model) rather than model-derived.
- **Action Network**: betting media/companion app — odds + expert
  picks/analysis + auto-synced bet tracking + social feed. Content/media
  business, not a quantitative-edge tool.
- **Pikkit**: free-first bet tracker / bankroll + CLV analytics with
  social copy-bet features, built around auto-sync from sportsbooks
  rather than any odds-scanning or picks product.

## Feature parity table

| Capability | OddsJam | Outlier | Action Network | Pikkit | FULL COUNT today |
|---|---|---|---|---|---|
| Multi-book odds comparison | Yes (100-150+ books claimed, UNVERIFIED exact count) | Yes (major books) | Yes | Yes (line shopping, 30+ books) | **No — FanDuel only** |
| Real-time/live odds refresh | Yes (claimed sub-second, UNVERIFIED) | Yes | Yes | Yes (synced, not scanned) | **No — homemade FanDuel observer has not yet confirmed a single price change; static per-slate pipeline** |
| Own proprietary probability model (not market-derived) | No | No | No | No | **Yes — this is FULL COUNT's actual product** |
| +EV / arbitrage finder vs. market | Yes | Yes | Not confirmed | No (not its purpose) | Partial — `value_board.py` screens FanDuel's own priced props against FULL COUNT's model, not cross-book arb |
| CLV (closing line value) tracking | Yes | Included per Outlier's page, mechanism unspecified | UNVERIFIED — no source mentions it | Yes (Pro tier, per-bet + aggregate %) | **No** |
| Bet tracking / bankroll management | Yes (in-app) | Not primary focus | Yes (BetSync auto-sync) | Yes — this is its entire product | **No** |
| One-click bet placement into a book | UNVERIFIED | Yes (pre-filled betslip) | Not confirmed | No | **No** |
| Line-movement history/charts | UNVERIFIED | Yes | Yes | Not confirmed | Partial — `sharp_divergence`/`line_movement` signals exist internally but aren't a customer-facing chart |
| Prop-specific hit-rate/trend research UI | Not its focus | Yes — a core feature | Not confirmed | Not its focus | **Yes** — `board_*.html`, prop detail views, STREAKS section |
| Self-graded accuracy/backtest transparency | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED (none found) | **Yes** — `results/history.json`, `main_hit_rate`, real backtest engine with point-in-time discipline, calibration audits |
| Multi-sport coverage | Broad (NFL/NBA/MLB/NHL/soccer/golf/MMA/tennis, etc.) | Confirmed NBA/MLB/NFL/NCAAB; broader list UNVERIFIED | Broad (NFL/NBA/MLB/NCAAF/NCAAB/golf/UFC, etc.) | UNVERIFIED (not enumerated) | **MLB only** |
| Social/community features | Not primary | Not primary | Yes — follow experts, share tickets | Yes — follow bettors, copy-bet | **No** |
| Mobile app | Yes | Yes | Yes | Yes | **No — static site only** |
| Browser extension | UNVERIFIED | Not found | Not found | Not found | No |
| Free tier | UNVERIFIED (likely no meaningful free tier) | No (7-day trial only) | UNVERIFIED | **Yes — core tracking is free** | **Yes — entire product is free today** |

## TABLE STAKES (users of serious competing products will notice the absence)

These are the gaps most likely to make FULL COUNT feel behind to anyone
who has used Outlier or OddsJam, independent of whether FULL COUNT's
underlying model edge is real:

1. **Multi-book pricing** — FanDuel-only is the single biggest visible
   gap vs. all four competitors. This is already the direction
   `FULL_COUNT_NORTH_STAR.md`'s market-data-neutrality section points,
   not a new finding.
2. **Live/real-time odds movement that's actually proven to update** —
   the current homemade observer's unconfirmed-repricing finding is the
   real blocker here, not a UI gap.
3. **Line-movement history as a visible feature**, not just an internal
   signal.
4. **A real mobile-usable experience** — FULL COUNT is already
   mobile-web-first per the Phase 4 UX rebuild, so this is partially
   addressed; a dedicated app is a much larger, separate decision not
   implied by this table.

## DIFFERENTIATOR (what none of the four attempt, and FULL COUNT already has)

1. **A genuine proprietary probability model**, not a market-derived
   consensus/no-vig number. None of the four researched products build
   their own predictive model — they compare, track, or surface market
   odds. This is FULL COUNT's actual edge claim and the one thing a
   commodity terminal structurally cannot copy without becoming a
   different kind of product.
2. **Public, honest self-grading against real outcomes** — `results/
   history.json`, `main_hit_rate` segmented from moonshot/best-of-category,
   a real backtest engine with point-in-time-safety proofs. No evidence
   any of the four researched competitors publish an equivalent
   transparent accuracy record.
3. **The Live Brain / World-State direction** (`live_brain/`, still
   frozen this session) — thesis/expression/actionable-window concepts
   aimed at deciding *when the market is wrong*, not just displaying it.
   None of the four are building toward this; they are display/tracking
   tools over someone else's market.

## NOT NEEDED (fits their business model, not FULL COUNT's)

1. **Social/copy-bet features** (Action Network, Pikkit) — FULL COUNT is
   not a social product; copying another bettor's action is the opposite
   of "FULL COUNT decides when the market is wrong."
2. **Generalized bankroll/bet-tracking across a user's entire betting
   history** (Pikkit's entire product, Action Network's BetSync) — a
   large, separate product surface (needs per-user accounts, sportsbook
   sync/auth) that isn't implied by anything in the North Star document
   and isn't necessary to prove the model edge.
3. **Arbitrage/middling tools** (OddsJam, Outlier) — these are pure
   market-inefficiency-exploitation features orthogonal to "does FULL
   COUNT's own model beat the market," and would require the multi-book
   feed FULL COUNT doesn't have yet regardless.
4. **One-click bet placement into a sportsbook** (Outlier) — a
   deep sportsbook-integration feature (and likely an affiliate/partner
   relationship) that's a business decision, not a research or
   engineering gap.

## What this is not

Not a decision to build any of the TABLE STAKES items now — this is a
research artifact for Jacob's judgment, per the governing instruction
("do not turn into a clone roadmap"). CLV tracking is the one item that
appears in 3 of 4 competitors' feature sets and would be relatively cheap
to add on top of FULL COUNT's *existing* FanDuel price data once genuine
price-change observability is proven — worth flagging as the most
leveraged small addition if/when that happens, not a recommendation to
build it today.
