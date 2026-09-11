# NFL identity: coupling audit, designs considered, and what was decided

Investigated on branch `claude/practical-maxwell-wgs4l6`, based at
`origin/main` = `97c3dab64a29cd3146a478895f30a40d9922b510` (2026-09-10 22:01 UTC).
Every claim below was re-verified against that tree, not inherited from a prompt.

## The requirement

Four things were fixed in advance and are not negotiable:

1. NFL identities MUST be sport-explicit.
2. NFL identity MUST be structurally unable to collide with MLB identity.
3. Existing MLB v2 IDs MUST remain byte-identical and readable.
4. Already-published MLB entries MUST NOT be rewritten.

## What MLB identity actually is

`dashboard/live_state.py` is the only definition. Three functions matter:

- `prop_identity_key(row)` (line 226) — the settlement identity as a tuple:
  `(game_pk, subject, stat, needs, side)`, where `subject` is `("game",)`,
  `("player", id)`, or `("combo", *sorted_ids)`.
- `canonical_prop_id(row)` (line 243) — renders that as
  `fc2:<game_pk>:<subject_token>:<stat>:<needs>:<side>`. The `fc2` literal is
  hardcoded in the `parts` tuple.
- `stable_prop_id(row)` (line 256) — validates a supplied id. It admits a row
  only when `row["identity_version"] == 2` **or** the id starts with `fc2:`,
  then requires exact equality with `canonical_prop_id(row)`. Anything else
  raises `unsupported identity version`.

`IDENTITY_SCHEMA_VERSION = 2` is defined once, at `dashboard/live_state.py:28`.

## The coupling, measured

Six MLB modules consume the identity functions:

| module | what it does with identity |
| --- | --- |
| `dashboard/build_dashboard.py` | mints ids, keys published rows, validates payload |
| `dashboard/publication_registry.py` | registry keys, snapshot verification |
| `dashboard/prepare_pages_artifact.py` | mints ids, stamps the version, validates |
| `dashboard/verify_pages_artifact.py` | validates, **and hard-asserts the literal 2** |
| `dashboard/refresh_prices.py` | resolves ids on live re-price |
| `dashboard/refresh_grades.py` | resolves ids on grading |

Two hard literal couplings to the number 2, outside the constant:

- `dashboard/verify_pages_artifact.py:111` —
  `if data.get("schema_version") != 3 or data.get("identity_schema_version") != 2:`
- **12 root test files** assert `"identity_schema_version": 2` as a literal
  (pattern: `grep -rln '"identity_schema_version": 2' --include=test_*.py .`).

`ledger_integrity.py` compares identity **sets** between two commits and does not
parse id structure at all, so it is indifferent to the namespace — which is why a
per-sport estate extension is a separate, additive change rather than an identity
change.

## Designs considered

### A. Bump `identity_schema_version` 2 → 3 and add a sport field — REJECTED

An earlier draft proposed this. It is not wrong in principle, but it is
strictly dominated. It requires editing `verify_pages_artifact.py`'s hard
assertion and 12 root test files, across the six modules above, touching MLB
publication-lifecycle surface. Requirement 3 (byte-identical MLB ids) then has
to be *re-established by proof* rather than being true by construction. And it
buys NFL nothing that design C does not already give for free.

### B. Reuse `fc2:` and add a sport token inside the payload — REJECTED, DANGEROUS

`stable_prop_id` admits **any** id starting with `fc2:`. An NFL id in that
namespace would be accepted into MLB's validator and then fail on a *content*
mismatch rather than a *namespace* mismatch — a much later, much more confusing
failure, and one that depends on field layout rather than on a structural
property. It converts a guarantee into a coincidence.

### C. A distinct NFL namespace prefix, zero MLB change — CHOSEN

`nfl/identity.py` mints identities beginning with the literal `fcnfl1:`.
Because the namespace is the first colon-delimited field and `fcnfl1` and `fc2`
differ, **no NFL identity can equal an MLB identity regardless of any other
field**. That is a property of the strings, not a convention.

It satisfies all four requirements with **no MLB edit whatsoever**:

- Requirements 3 and 4 hold because nothing MLB-side changed.
  `nfl/tests/test_identity_isolation.py` re-derives real MLB ids through the
  production `canonical_prop_id` and compares them byte for byte, and asserts
  `IDENTITY_SCHEMA_VERSION` is still 2.
- Requirement 2 is proved structurally, and exercised: mutating
  `NFL_NAMESPACE` to `"fc2"` makes the suite fail (observed).
- Requirement 1 holds — `fcnfl1` names the sport in the identity itself.

A bonus that fell out of the audit rather than being designed: **MLB production
already rejects NFL ids today, with no change.** An `fcnfl1:` id passed to
`stable_prop_id` raises `unsupported identity version`, because it neither
carries `identity_version == 2` nor starts with `fc2:`. That is tested.

## Why this was decided rather than escalated

The procedure said to stop and ask Jacob if multiple sound designs remained with
materially different implications for immutable evidence. They did not. C
dominates A on every axis that matters (MLB blast radius, strength of the
guarantee, cost) and B is unsafe. Separate evidence estates were already a fixed
architectural decision, which removes the only motivation A ever had.

## What is deliberately NOT decided, and must not be rushed

**The canonical NFL game-id space is an open question**, and it is the genuinely
irreversible one.

Three candidates, all real, all different id spaces:

| space | example | reconstructable later? |
| --- | --- | --- |
| nflverse | `2026_01_TB_CIN` | yes — public bulk data, durable |
| ESPN | `401872925` | vendor-controlled |
| FanDuel | `35610167` | vendor-controlled, and the book we price against |

Baking a vendor id into settlement keys for a season, and then finding it
unreconstructable, is exactly the class of mistake this mission exists to avoid.
So `nfl_prop_id` carries the **source as part of the identity** and refuses an
unrecognised one, and two vendors' ids for the same game therefore cannot
silently collapse into one wager.

**Nothing is locked in.** There are no NFL candidates, no NFL rows, and no NFL
estate files; `nfl_prop_id` is called by nothing but its own tests. The field
layout may be changed freely by a future mission **while that remains true**.
Raw archives under `nfl/raw/` carry no canonical identity at all, precisely so
this decision can be made later without contradicting a season of evidence.

The one invariant that must survive any such change is the namespace
disjointness, which is why `nfl_prop_id` re-asserts it on every construction.
