# ATL–GB receptions price research checkpoint

Draft-only. No public pick, model promotion, wager, or workflow hook.

The committed `capture_01/snapshot.json` is a genuine FanDuel public-feed
capture for event `36076208`, Atlanta Falcons @ Green Bay Packers. It was
observed **2026-09-23 16:32:24–31 UTC**, with the receiving-props payload
observed at 16:32:30 UTC. Canonical nflverse schedule binding is
`2026_03_ATL_GB`, September 24 at 20:15 Eastern (September 25 00:15 UTC).
The book listed 00:16 UTC; the runner requires the two times within five
minutes and stops at the earlier canonical kickoff.

The source snapshot contains **54** price evaluations: 8 paired standard
receptions and 46 one-sided N+ alternates. All 54 were QUARANTINED on original
capture for unknown official game coverage. The source quote-origin timestamp
was not provided; source-observation time was retained separately. The raw
FanDuel responses are committed losslessly as gzip+base64 wrappers with
SHA-256 and original request/observation metadata. `replay_01.json` is a
forensic re-evaluation under the repaired gates: **54 QUARANTINED, 0 eligible**.
`replay_02.json` applies the final quote/rule/role/B0 gates and also reports
54 quarantines. Both preserve original capture cutoffs and do not make old
prices current.

The roster and nflverse 2023–25 weekly files were fetched and checked against
the existing audited hashes at capture. Their URLs/digests and the exact
2025 prior appearances consumed by each priced player remain in the snapshot;
their full multi-megabyte raw CSVs are not committed. The frozen shared
conditional count PMF and a B0 half-line comparator recomputed from the pinned
2025-prior recipe are
separate research signals. Mathematical EV uses captured American prices,
correct win/loss/push partitions and one-unit net payout. Positive modeled EV
does not remove a quarantine or prove realized profitability.

To verify or replay without any network call:

```sh
PYTHONPATH=. python -m nfl.research.price_aware_offer_capture \
  --replay engineering/nfl_price_aware_20260923/capture_01 \
  --output /path/to/new/replay.json
```

For a new manual pregame capture, run the same module with
`--output /path/to/new/directory`. It creates the directory and evidence
files only when they do not exist. Requires `requests` as in the existing
NFL test environment and available source endpoints. Repeated execution must
use a new directory, never replace a prior observation.

A new manual capture can take `--b0-snapshot /path/to/claude/sealed.json`.
The runner verifies the existing live shadow seal and only joins an earlier
same-game/GSIS B0 row. For primary lines it also requires identical market,
line and both prices; an alternate may borrow the same player's earlier
projection, with the distribution clearly labeled a separate frozen
challenger. The September 23 archive has **no** joined authoritative live
B0 snapshot. Its B0 comparator remains separate from Claude's operational
frozen B0 and cannot be upgraded retroactively to a live B0 match.

## Eligibility and settlement contract for Claude

Read `snapshot.json` only after verifying its seal and raw wrapper digests.
Join on canonical game `2026_03_ATL_GB`, exact FanDuel event id, GSIS player
id, market id, selection id, side, threshold/line, captured American price
and observation time. Compare only to B0 evidence frozen before the same
cutoff. Do not join by name or later line alone. The manual runner cannot
claim official inactive coverage, current 2026 role verification, or a source
quote-origin time. A workflow consumer must supply those as separately
timestamped evidence and preserve the old snapshot.

FanDuel's [New Jersey house rules](https://www.fanduel.com/fanduel-sportsbook-house-rules-nj)
say NFL player props void when the player plays no snap, and league-governing
body receptions determine relevant catches. This public rule is a research
reference, not proof that NJ rules apply to an unknown user's jurisdiction
or an individual accepted wager. No applicable rule bytes or snap counts were
captured for this game. Therefore book action remains uncertified and all
offers stay quarantined. The `settle_record` interface requires an official
final count, a certified total game snap count (including special teams),
timestamps, source digests and
exact identities. It returns **research unit accounting**, never a claim
that FanDuel paid an actual ticket. Actual-price returns and realized
HIT/MISS/VOID require future matched final evidence; none exists yet for
this prospective game.

The current-year role gap matters: this runner deliberately reuses the
authoritative 2025-prior B0 demonstration, while a separately validated
2026 recent-usage path belongs to Claude's live workflow. No claim of an
accuracy improvement, current-week eligible selection, or better realized
winning picks follows from this archive.

Alligator

