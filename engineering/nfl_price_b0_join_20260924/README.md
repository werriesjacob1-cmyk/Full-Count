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
