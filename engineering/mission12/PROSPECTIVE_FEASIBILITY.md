# How long the prospective PA-v1 experiment actually takes

Measured, not estimated. `feasibility_replay.py` replays protocol §7's
designation rule over **every committed revision of `docs/data.json`** (952
revisions, 19 slate dates) and counts what the primary scoreboard would have
had to work with. `feasibility_result.json` is its output.

## The measurement

| | |
|---|---|
| calendar days observed | 19 |
| dates with a primary epoch | **8** |
| dates with N=0 (no Hits Top Pick exposed at all) | 9 |
| dates with no primary epoch | 2 |
| champion selections, upper bound | **14** |

Rate: **0.42 primary dates/day, 0.74 selections/day.**

Against §13's floor (30 primary dates AND 100 decided per arm):

* 30 primary dates → **~71 days**
* 100 decided per arm → **~136 days**, and that ignores voids and ungraded
  outcomes, so it is optimistic
* **Binding constraint: ~136 days — roughly four and a half months of live
  running from the day shadow persistence is enabled.**

Both numbers are upper bounds on progress: a date counts here if some
deployment exposed Hits Top Picks that were all still pregame, while a real
primary epoch must additionally bind a hash-matched snapshot, survive the
re-gate, and match volume exactly.

## Why this is not the "most dates die" finding

A methodology red team reported that the scoreboard annihilates 14 of 16 dates,
because production is required by §14 to keep already-exposed Top Picks visible
for settlement, so a day's LAST refresh carries them as `live`/`final`,
`champion_hits_picks` drops them, and the epoch fails closed.

The fail-closed behaviour is real and correct — you cannot seal a pregame
receipt for a game that is already final. The inference from it is wrong:
`designate()` considers only epochs that actually **sealed**, so a late
fail-closed deployment is never a designation candidate, and the earlier
all-pregame epoch is designated instead. Their 2-of-16 comes from sampling one
commit per date; over all deployments it is 8 of 19.

`test_prospective_lifecycle_e2e.py` check 17d proves the mechanism by
execution: a late all-`final` deployment fails closed, contributes no sealed
selection, and the date still designates its earlier epoch and keeps its full
champion volume — with the fail-closed epoch reported, not hidden.

## What actually limits the experiment

Not epoch loss. **Exposure volume.** On 9 of 19 dates production exposed no
Hits Top Pick at all, and on the dates it did, N ran 1–4. The experiment is
slow because the champion arm is small, which is a fact about the product, not
a defect in the evidence system.

Three consequences worth Jacob's attention before enablement:

1. **~4.5 months is the honest horizon.** Any plan that assumes weeks is wrong.
2. **Optional stopping is therefore not hypothetical.** Over that horizon the
   §12 report can be re-read many times, and nothing currently records how many
   looks were taken — see `OPTIONAL_STOPPING_RECOMMENDATION.md`. Implementing
   it would change the promotion decision standard and needs authorization.
3. **The floor could be reconsidered** — but that is a protocol amendment, and
   lowering a floor after seeing how slow the data is arriving is exactly the
   move the protocol exists to prevent. Recorded here as a fact, not a proposal.
