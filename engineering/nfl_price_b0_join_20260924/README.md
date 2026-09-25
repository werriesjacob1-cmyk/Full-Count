# ATL–GB price-to-B0 research integration, September 24

This is an isolated, manual research lane stacked on PR #196. It reads the
existing sealed FanDuel capture and Claude's **already sealed** authoritative
receptions B0 board. It does not run or change the B0 workflow, publish a pick,
or authorize a wager. `nfl/research/price_to_b0_integration.py` is the sole new
adapter. The original B0 projection/probabilities and the separate PR #196
negative-binomial challenger remain distinct in each integration record.

## Authentic source and observed execution

`capture_01/` is an isolated FanDuel observation beginning
`2026-09-24T14:51:32Z`. Its sealed snapshot SHA-256 is
`bb20de3bae427dd593c17a7d1dc8a3c9ff9542f714912764ff9cd11b70c8e2e3`.
Eleven compressed, lossless book responses are present with per-response SHA-256
and observed-at timestamps. The source normalizer produced 57 distinct offers:
9 two-sided primary receptions lines and 48 alternate N+ thresholds. The
captured book event was 36076208, Atlanta Falcons at Green Bay Packers.
The independently pinned nflverse schedule row binds it to
`2026_03_ATL_GB`, local date September 24, canonical kickoff
`2026-09-25T00:15:00Z`; FanDuel advertised open date 00:16Z, so the earlier
canonical kickoff controls. Captured roster/history/schedule CSV SHA-256s are
in the source manifest; those larger CSV bytes are not committed, and the
verifier separately reports whether they are present. The book bytes needed to
recheck every quote are committed.

`integration_01.json` is a create-only execution at
`2026-09-24T14:54:28Z`, SHA-256 seal
`a5d9a4bddca4b8dd13cee564b0eaf93c9461064161ab030469001b461ee6ac31`.
It verifies all 57 price-record seals, source response hashes, raw market,
selection IDs, odds, event, and player normalization. The result is **57
QUARANTINED, zero eligible**. No authentic B0 artifact bytes were available
to this isolated run. The contemporaneous GitHub Actions B0 artifact identified
was run 35890373204, artifact 10764582093, digest
`sha256:4b8d8ac55a1b73d8460c7852712c0869e36701b520615bd650452b4eb8b9c005`,
but its binary download was denied to this environment. It is therefore
recorded as metadata only, not treated as verified or joined evidence. A
previous September 23 book capture predates that run and cannot be joined
retroactively. This September 24 capture has since aged out; replay is
forensic, not a current quote.

The authentic book feed lacks a quote-origin timestamp. Other current blockers
are no same-day official inactive coverage, unverified current player roles,
unknown applicable FanDuel action/settlement jurisdiction, and no verified B0
bytes. Every offer retains its own reasons. Alternate thresholds have no
authoritative B0 distribution and cannot inherit a primary-line probability.
No gate is waived to manufacture eligibility.

## Claude interface

After its existing scheduled B0 workflow seals a board, pass the actual
downloaded `snapshot.json` (or a JSON object with a `snapshot` member) and a
**new** independently captured book directory to:

```text
python -m nfl.research.price_to_b0_integration \
  --capture-dir PATH_TO_NEW_CAPTURE \
  --b0-snapshot PATH_TO_SEALED_B0_JSON \
  --output PATH_TO_NEW_INTEGRATION_JSON
```

The output path must not exist. The adapter rechecks the B0 content seal,
schema/cardinality, slate date, exact market/game/GSIS/market ID/line/both
odds, source bytes, and B0-seal-before-offer chronology. It computes B0 price
value only for an exact two-sided half-line match, preserving the challenger
record reference separately. Later B0, mismatched line or prices, unknown
source quote time, or missing eligibility evidence remains quarantined.
The caller should archive the B0 artifact bytes and its Actions artifact
identity beside future integration evidence; metadata alone is insufficient.

Focused boundary verification:
`python -m unittest nfl.tests.test_price_to_b0_integration nfl.tests.test_price_aware_capture nfl.tests.test_price_aware_offers`.
The tests use authentic committed book bytes for binding and synthetic sealed
B0 fixtures only to challenge chronology, identity, price, duplicate, and
source-integrity boundaries. The synthetic fixture does not establish a live
B0 join.

## September 25 eligibility-source audit and integration boundary

The three outstanding gates are the exact reasons emitted by
`price_aware_offers.evaluate_offer`: `QUOTE_TIMESTAMP_NOT_PROVIDED`,
`CURRENT_ROLE_NOT_VERIFIED`, and `BOOK_ACTION_RULES_NOT_CERTIFIED`. The
September 24 authentic capture sets each source field to unknown. These are
real blockers, not a count to optimize away. The newer code additionally
rejects a caller that merely flips a status flag without matching provenance.

| Gate | Available evidence | Exact remaining blocker |
|---|---|---|
| Quote origin | The archived FanDuel response contains OPEN, non-in-play markets, active runners, exact prices and observation time; raw bytes and SHA-256 are sealed. | The response has no market/runner price-update timestamp. `marketTime` equals the scheduled event time (`2026-09-25T00:16:00Z`), not quote origin. The integration verifier forbids using it as such. Observation time establishes when *we saw* a displayed quote, not how long the book had displayed it or that a wager would be accepted at that price. |
| Current role | The B0 shadow board can establish official inactive coverage and identity; a prior-season model alone cannot verify current projected routes/snaps or a limited role. | No source-backed, game/player/team-bound current role evidence is in either pricing capture. A `VERIFIED` string without source digest and available-at time is insufficient. |
| Book action/settlement | FanDuel's [NJ house rules](https://www.fanduel.com/fanduel-sportsbook-house-rules-nj) (effective July 30, 2026) describe full-game NFL props' no-snap void condition and league-stat settlement. | The NJ page does not establish the applicable jurisdiction/product for every customer, nor that an individual ticket is accepted. No rules bytes/jurisdiction binding were captured with these offers. A `CERTIFIED` string or a URL/hash alone cannot establish applicability. |

The published rules also say displayed odds can change before acceptance and
accepted odds control. The archived market is therefore authentic *observed*
FanDuel data, not proof of an executable historical price. No retrospective
repair may add these three missing observations to the immutable capture.

### Market-support inventory, not an offer claim

The September 24 archive contains 228 observed market entries across 133 raw
market types, but this particular pricing consumer accepted only 57 receptions
offers (9 primary two-sided and 48 N+ alternates). All 57 were quarantined.
Other raw market entries are an inventory of displayed markets, not normalized
offers with a compatible probability, action rule, eligibility and grade.

| Family | Current component status | Actionable/customer status |
|---|---|---|
| Receptions, standard and N+ | Authentic offer capture, GSIS binding, PMF/price math, sealed B0 primary-line join and research settlement exist. B0 alternate probabilities are deliberately unsupported. | No official eligibility: all three gates above; no public selector/pick output. |
| Passing yards | Dedicated live normalizer/B0, pregame shadow and outcome grade exist elsewhere. This adapter has no passing-yards quote-to-B0 join. | Not connected to this price-aware eligible-pick path. |
| Rushing/receiving yards, rushing/passing attempts, passing TDs, interceptions, anytime TD and combined player yards | Some observed raw market types, normalizers or research models exist for subsets. They do not establish the complete six-part offer → probability → identity/rule → eligibility → customer output → grade chain here. | Unsupported by this pricing integration; do not infer availability from a market label. |
| Spreads, totals, moneylines, team totals, alternate/plus-money game markets | Some raw book markets and separate game-market research exist. No customer-certified joint distribution/eligibility/settlement path is connected here. | Research only; not actionable through #196/#199. |

`price_to_b0_integration.integrate` still writes `research_only=true` and
`bettable=false` for every row. The NFL website publication guard requires
`public_selector_validated=false`. A contract-level synthetic fixture can
exercise all positive and negative gate branches, but it cannot establish a
current, jurisdiction-compatible offer or authorize a customer pick. A
legitimate future capture needs source quote-vintage evidence or an explicitly
approved observation-freshness policy, a separately sourced current role, and
book/market/jurisdiction action-rule certification before selection policy can
be separately reviewed by Jacob. Neither archived ATL–GB capture can be
upgraded into such evidence.
