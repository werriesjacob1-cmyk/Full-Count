#!/usr/bin/env python3
"""Evidence regimes, enforced in code -- preregistration v3, section 11.

A regime is chosen by name only. There is no date-range override: every slate
unit must satisfy the regime's calendar window AND every covered game's MLB
statsapi gameType (recorded in the pregame schedule snapshot inside the
manifest). One violating game rejects the whole evaluation call.
"""
from __future__ import annotations

REGIMES = {
    "2026_POSTSEASON_SHADOW": {"game_types": frozenset("FDLW"), "first": "2026-09-29", "last": "2026-11-15",
                               "confirmatory": False},
    "2027_REGULAR_CONFIRMATORY": {"game_types": frozenset("R"), "first": "2027-03-01", "last": "2027-10-10",
                                  "confirmatory": True},
    "SMOKE_TEST_SYNTHETIC": {"game_types": frozenset("RFDLWS"), "first": "2099-01-01", "last": "2099-12-31",
                             "confirmatory": False},
}


def check_regime(regime, manifests):
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    spec = REGIMES[regime]
    for m in manifests:
        if not spec["first"] <= m["date"] <= spec["last"]:
            raise ValueError(f"{m['date']}: outside the {regime} calendar window")
        bad = {r["game_type"] for r in m["rows"] if r.get("game_type") is not None} - spec["game_types"]
        if bad or any(r.get("game_type") is None and r.get("eligible") for r in m["rows"]):
            raise ValueError(f"{m['date']}/{m['window']}: game types {sorted(bad) or ['UNKNOWN']} not allowed in {regime}")
    return spec
