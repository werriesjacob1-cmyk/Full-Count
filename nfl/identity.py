#!/usr/bin/env python3
"""Sport-explicit NFL identity that structurally cannot collide with MLB.

READ nfl/docs/IDENTITY_INVESTIGATION.md for the coupling audit and the designs
that were rejected. This module implements only the part that is genuinely
irreversible, and deliberately leaves the rest open.

WHAT IS DECIDED HERE, AND WHY IT IS SAFE TO DECIDE NOW
The NAMESPACE. Every NFL identity begins with the literal token `fcnfl1:`.
MLB v2 identities begin with `fc2:` (dashboard/live_state.canonical_prop_id).
Because the namespace is the FIRST colon-delimited field and the two literals
differ, no NFL identity can ever equal an MLB identity, whatever either side's
remaining fields contain. This is a structural property of the string, not a
convention anyone has to maintain.

It also means MLB's own validator rejects NFL ids with no change to MLB code:
`stable_prop_id` admits a row only when `identity_version == 2` OR the id starts
with `fc2:`, and raises "unsupported identity version" otherwise. An NFL id fed
into MLB production fails closed today, on the code as it already stands.

`identity_schema_version` IS NOT BUMPED. It stays 2. Bumping 2 -> 3 would mean
editing dashboard/verify_pages_artifact.py's hard `!= 2` assertion plus twelve
root test files that assert the literal 2, across the six MLB modules that
consume the identity functions -- a broad change to MLB publication-lifecycle
surface that buys NFL nothing the prefix does not already give it for free.

WHAT IS DELIBERATELY NOT DECIDED HERE
The internal field layout below is PROVISIONAL, and no NFL identity has been
minted anywhere in this repository -- there are no NFL candidates, no NFL rows,
and no NFL estate files. Nothing is locked in. The genuinely open question is
which game-id space is canonical for settlement: nflverse's durable
`2026_01_TB_CIN` form, ESPN's numeric event id, or FanDuel's numeric event id.
They are three different id spaces, only one of them is reconstructable years
later from a public bulk source, and picking wrong bakes a vendor into a season
of settlement keys. That decision belongs with the first real candidate, not
with a mission whose deadline is archival. Raw archives carry NO canonical
identity precisely so this can be settled later without contradicting them.

So: use `namespace_is_disjoint_from_mlb` and the guards below as the invariant.
Treat `nfl_prop_id` as a proposal that a future mission may change freely, and
change it only while zero NFL identities exist anywhere.
"""
from __future__ import annotations

from urllib.parse import quote

# The whole guarantee, in one literal. Do not change it once any NFL identity
# has been persisted anywhere.
NFL_NAMESPACE = "fcnfl1"

# MLB's namespace, recorded here as the thing we must never equal. Duplicated
# rather than imported: nfl/ must not import MLB production modules, and this
# module exists partly to prove the two spaces are disjoint.
MLB_NAMESPACE_PREFIXES = ("fc2:",)

# Provisional. See the module docstring: the canonical game-id space is an open
# decision, so the source is carried IN the identity rather than assumed.
GAME_ID_SOURCES = ("nflverse", "espn", "fanduel")


class IdentityError(ValueError):
    """The row cannot be given a stable NFL identity."""


def namespace_is_disjoint_from_mlb(identity: str) -> bool:
    """True if `identity` cannot be mistaken for, or collide with, an MLB id."""
    if not isinstance(identity, str) or not identity:
        return False
    if not identity.startswith(NFL_NAMESPACE + ":"):
        return False
    return not any(identity.startswith(p) for p in MLB_NAMESPACE_PREFIXES)


def _threshold_token(value) -> str:
    """Normalise a line so 0.5 and 0.50 cannot become two identities."""
    if value is None:
        raise IdentityError("NFL prop identity is missing its threshold")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IdentityError(f"threshold {value!r} is not numeric") from exc
    # Trailing-zero-free fixed form: 0.5 -> "0.5", 60.0 -> "60".
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def nfl_prop_id(
    game_id_source: str,
    game_id: str,
    subject: str,
    market: str,
    threshold,
    side: str,
) -> str:
    """PROVISIONAL NFL settlement identity. See the docstring before relying on it.

    The game-id SOURCE is part of the identity rather than an assumption, so
    that an id minted today stays interpretable after the canonical id space is
    decided -- and so that two ids built from different sources can never
    silently be treated as the same wager.
    """
    if game_id_source not in GAME_ID_SOURCES:
        raise IdentityError(
            f"unknown game_id_source {game_id_source!r}; expected one of "
            f"{GAME_ID_SOURCES}. The canonical NFL game-id space is an open "
            "decision and must not be guessed per call site."
        )
    for name, value in (("game_id", game_id), ("subject", subject),
                        ("market", market), ("side", side)):
        if value is None or str(value).strip() == "":
            raise IdentityError(f"NFL prop identity is missing {name}")
    side_token = str(side).strip().lower()
    if side_token not in ("over", "under", "yes", "no"):
        raise IdentityError(
            f"unsupported side {side!r}; NFL markets settle over/under or yes/no"
        )
    parts = (
        NFL_NAMESPACE, game_id_source, str(game_id), str(subject),
        str(market).strip().lower(), _threshold_token(threshold), side_token,
    )
    identity = ":".join(quote(str(p), safe="+-_.") for p in parts)
    # Belt and braces: the invariant is asserted on the way out, so a future
    # edit to the field layout cannot silently break the one guarantee that
    # actually matters.
    if not namespace_is_disjoint_from_mlb(identity):
        raise IdentityError(
            f"constructed identity {identity!r} is not provably disjoint from "
            "the MLB identity space. This is the one invariant that must never "
            "regress."
        )
    return identity
