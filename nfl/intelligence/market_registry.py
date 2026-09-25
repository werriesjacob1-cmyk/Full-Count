"""Validation helpers for the permanent NFL sportsbook market registry.

Companion to `angle_registry.py`/`source_registry.py`: a research control
plane, not a model input. Its purpose is to make market coverage claims
falsifiable -- every entry must point at real, checked-in evidence (a
normalizer, binder, grader, model module, or scheduled workflow file that
actually exists in this repository) rather than an aspirational description.
Listing a market here is not a claim that it works end to end; `status`
records exactly how far it has actually gotten.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

# Ordered roughly by maturity. A market's status must never overstate what
# its own `evidence` block actually contains (checked by
# `validate_market_registry`).
ALLOWED_STATUSES = {
    "UNSUPPORTED",   # not offered, or offered but nothing here reads it
    "DEFERRED",      # explicitly scoped out with a recorded reason
    "RESEARCH",      # a hypothesis/design note only, no working code
    "IMPLEMENTED",   # normalize/bind/grade code exists
    "TESTED",        # IMPLEMENTED plus passing unit tests referenced
    "LIVE_SHADOW",   # a scheduled/dispatchable capture workflow exists
    "VALIDATED",     # prospective evidence has been reviewed and accepted
    "BLOCKED",       # attempted; a real external obstacle stopped it
}
ALLOWED_SHAPES = {
    "primary", "alt_ladder", "single_threshold", "two_way_line",
    "moneyline", "unsupported",
}
REQUIRED_MARKET_FIELDS = {
    "market_id", "family", "shape", "status", "evidence", "notes",
}
REQUIRED_EVIDENCE_KEYS = {
    "normalizer", "binder", "model", "grader", "workflow", "tests",
}

# Statuses that assert enough real code exists to require at least one
# non-null evidence pointer (a bare RESEARCH/UNSUPPORTED/DEFERRED entry may
# legitimately have no code yet).
STATUSES_REQUIRING_EVIDENCE = {"IMPLEMENTED", "TESTED", "LIVE_SHADOW", "VALIDATED"}


class NFLMarketRegistryError(ValueError):
    pass


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NFLMarketRegistryError(f"{field} must be a non-empty string")
    return value.strip()


def validate_market_registry(
    payload: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the registry payload. If `repo_root` is given, also confirms
    every non-null evidence path actually exists in the repository -- the
    registry is not allowed to cite code that was never written.
    """
    if not isinstance(payload, Mapping):
        raise NFLMarketRegistryError("registry must be a mapping")
    if payload.get("schema_version") != 1:
        raise NFLMarketRegistryError("unsupported schema_version")
    if payload.get("sport") != "NFL":
        raise NFLMarketRegistryError("sport must be NFL")

    scope = payload.get("sportsbook_scope")
    if not isinstance(scope, list) or not scope or not all(
        isinstance(value, str) and value.strip() for value in scope
    ):
        raise NFLMarketRegistryError("sportsbook_scope must be a non-empty list of strings")

    markets = payload.get("markets")
    if not isinstance(markets, list) or not markets:
        raise NFLMarketRegistryError("markets must be a non-empty list")

    seen: set[str] = set()
    status_counts: dict[str, int] = {}
    for index, market in enumerate(markets):
        if not isinstance(market, Mapping):
            raise NFLMarketRegistryError(f"markets[{index}] must be a mapping")
        missing = REQUIRED_MARKET_FIELDS.difference(market)
        if missing:
            raise NFLMarketRegistryError(
                f"markets[{index}] missing fields: {', '.join(sorted(missing))}"
            )
        market_id = _nonempty_text(market["market_id"], f"markets[{index}].market_id")
        if market_id in seen:
            raise NFLMarketRegistryError(f"duplicate market_id: {market_id}")
        seen.add(market_id)

        _nonempty_text(market["family"], f"{market_id}.family")
        if market["shape"] not in ALLOWED_SHAPES:
            raise NFLMarketRegistryError(f"{market_id}: invalid shape {market['shape']!r}")
        status = market["status"]
        if status not in ALLOWED_STATUSES:
            raise NFLMarketRegistryError(f"{market_id}: invalid status {status!r}")
        _nonempty_text(market["notes"], f"{market_id}.notes")

        evidence = market["evidence"]
        if not isinstance(evidence, Mapping):
            raise NFLMarketRegistryError(f"{market_id}: evidence must be a mapping")
        missing_evidence_keys = REQUIRED_EVIDENCE_KEYS.difference(evidence)
        if missing_evidence_keys:
            raise NFLMarketRegistryError(
                f"{market_id}: evidence missing keys: "
                f"{', '.join(sorted(missing_evidence_keys))}"
            )
        evidence_paths = [
            value for value in evidence.values()
            if isinstance(value, str) and value.strip()
        ]
        if status in STATUSES_REQUIRING_EVIDENCE and not evidence_paths:
            raise NFLMarketRegistryError(
                f"{market_id}: status {status} requires at least one evidence path"
            )
        if repo_root is not None:
            for value in evidence_paths:
                if not (repo_root / value).exists():
                    raise NFLMarketRegistryError(
                        f"{market_id}: evidence path does not exist: {value}"
                    )

        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "market_count": len(markets),
        "market_ids": sorted(seen),
        "status_counts": dict(sorted(status_counts.items())),
    }


def load_and_validate_market_registry(
    path: str | Path,
    *,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload, validate_market_registry(payload, repo_root=repo_root)
