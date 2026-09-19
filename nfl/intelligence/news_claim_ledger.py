#!/usr/bin/env python3
"""Atomic claim-ledger schema for the NFL Genius News/Practice/Press-Conference
Brain (`engineering/NFL_GENIUS_NEWS_BRAIN_2026-09-18.md`).

This is the first real implementation of that design doc's core principle:

    A public statement or reporter post is not automatically a fact.

Every item the News Brain ingests becomes one ATOMIC CLAIM record with who
said/reported it, when it was published, when FULL COUNT observed it, what
team/player/game it concerns, whether it was directly observed vs a quote /
sourced report / inference / opinion / official transaction, its source tier
(A-F per the design doc), its claim category (the design doc's taxonomy), and
fields for corroboration / contradiction / correction tracking.

Mirrors the fail-closed, self-checking validation pattern already established
by `nfl/intelligence/source_registry.py` and
`nfl/intelligence/team_intelligence_registry.py`: a required-field set, enum
validation, and explicit errors on malformed input. Nothing here fetches data;
this module only defines and validates the claim record shape plus the
temporal-safety and reliability-scoring functions that operate on it.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from nfl.intelligence.team_intelligence_registry import EXPECTED_TEAMS

LEDGER_SCHEMA_VERSION = 1

# Source tiers A-F, exactly as defined in the design doc.
SOURCE_TIERS = frozenset({"A", "B", "C", "D", "E", "F"})

# Evidence class -- "whether the claim is a quote, sourced report, inference,
# opinion, or official transaction" per the design doc's Evidence class list.
EVIDENCE_CLASSES = frozenset({
    "OFFICIAL_EVENT",
    "DIRECT_QUOTE",
    "DIRECT_PRACTICE_OBSERVATION",
    "NAMED_SOURCE_REPORT",
    "UNNAMED_SOURCE_REPORT",
    "REPORTER_INFERENCE",
    "FILM_ANALYSIS",
    "AGGREGATION",
    "OPINION",
})

# The design doc's claim taxonomy, verbatim.
CLAIM_TYPES = frozenset({
    "AVAILABILITY",
    "PRACTICE_PARTICIPATION",
    "FIRST_TEAM_REPS",
    "DEPTH_POSITION",
    "SNAP_LIMIT",
    "ROLE_INCREASE",
    "ROLE_DECREASE",
    "STARTER_CHANGE",
    "REPLACEMENT",
    "OL_COMBINATION",
    "ROUTE_ALIGNMENT",
    "BACKFIELD_ROLE",
    "GOAL_LINE_ROLE",
    "THIRD_DOWN_ROLE",
    "TWO_MINUTE_ROLE",
    "RETURN_SPECIAL_TEAMS_ROLE",
    "SCHEME_CHANGE",
    "PERSONNEL_PACKAGE",
    "PLAYCALLING_CHANGE",
    "COACHING_PHILOSOPHY",
    "MATCHUP_PLAN",
    "WEATHER_ROOF",
    "TRANSACTION",
    "DISCIPLINE",
    "REST_MANAGEMENT",
    "INJURY_RECOVERY",
    "POSTGAME_ROLE_EXPLANATION",
    "FUTURE_ROLE_EXPECTATION",
    "OTHER",
})

# Contradiction-graph relation types: "Claims can corroborate, refine,
# supersede, contradict, retract."
CORROBORATION_RELATIONS = frozenset({"CORROBORATE", "REFINE"})
CONTRADICTION_RELATIONS = frozenset({"SUPERSEDE", "CONTRADICT", "RETRACT"})

RESOLUTION_OUTCOMES = frozenset({"CONFIRMED", "REFUTED", "PARTIALLY_CONFIRMED"})

REQUIRED_FIELDS = frozenset({
    "ledger_schema_version",
    "claim_id",
    "source_id",
    "source_tier",
    "evidence_class",
    "claim_type",
    "direct_observation",
    "reporter",
    "team",
    "player",
    "concerns_game_id",
    "concerns_teams",
    "postgame_of_game_id",
    "published_at",
    "observed_at",
    "effective_from",
    "effective_until",
    "corrected_at",
    "content_summary",
    "corroborations",
    "contradictions",
    "correction_of",
    "resolution",
})


class NewsClaimLedgerError(ValueError):
    """Raised when a claim (or a batch of claims) fails validation.

    Fail-closed by contract: malformed input never becomes a silently
    accepted, half-valid claim record.
    """


def make_claim_id(*parts: Any) -> str:
    """Deterministic claim id from stable identifying parts.

    Reusing the same parts (same source, artifact, claim subject) always
    yields the same id, so re-ingesting the same official document is
    idempotent rather than creating duplicate claims.
    """
    joined = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]
    return f"nc_{digest}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_ts(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise NewsClaimLedgerError(f"{field} must be a non-empty ISO-8601 string")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise NewsClaimLedgerError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise NewsClaimLedgerError(f"{field} must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def _optional_ts(value: Any, field: str) -> None:
    if value is None:
        return
    _parse_ts(value, field)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NewsClaimLedgerError(f"{field} must be a non-empty string")
    return value.strip()


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise NewsClaimLedgerError(f"{field} must be a bool")
    return value


def _validate_reporter(reporter: Any) -> None:
    if not isinstance(reporter, Mapping):
        raise NewsClaimLedgerError("reporter must be a mapping")
    _text(reporter.get("name"), "reporter.name")
    _text(reporter.get("outlet"), "reporter.outlet")
    _text(reporter.get("role"), "reporter.role")


def _validate_team(team: Any) -> None:
    if team is None:
        return
    if team not in EXPECTED_TEAMS:
        raise NewsClaimLedgerError(f"team must be a known NFL team abbreviation or None, got {team!r}")


def _validate_concerns_teams(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise NewsClaimLedgerError("concerns_teams must be None or exactly 2 teams")
    a, b = value
    if a == b:
        raise NewsClaimLedgerError("concerns_teams must name two distinct teams")
    for team in (a, b):
        if team not in EXPECTED_TEAMS:
            raise NewsClaimLedgerError(f"concerns_teams contains an unknown team: {team!r}")


def _validate_player(player: Any) -> None:
    if player is None:
        return
    if not isinstance(player, Mapping):
        raise NewsClaimLedgerError("player must be a mapping or None")
    _text(player.get("player_name"), "player.player_name")
    for key in ("gsis_id", "source_player_href", "source_player_slug", "listed_position"):
        if key in player and player[key] is not None and not isinstance(player[key], str):
            raise NewsClaimLedgerError(f"player.{key} must be a string or None")


def _validate_relation_list(value: Any, field: str, allowed_relations: frozenset) -> None:
    if not isinstance(value, list):
        raise NewsClaimLedgerError(f"{field} must be a list")
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise NewsClaimLedgerError(f"{field}[{i}] must be a mapping")
        _text(item.get("claim_id"), f"{field}[{i}].claim_id")
        relation = item.get("relation")
        if relation not in allowed_relations:
            raise NewsClaimLedgerError(
                f"{field}[{i}].relation must be one of {sorted(allowed_relations)}, got {relation!r}"
            )
        _parse_ts(item.get("noted_at"), f"{field}[{i}].noted_at")


def _validate_resolution(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise NewsClaimLedgerError("resolution must be a mapping or None")
    resolved = _bool(value.get("resolved"), "resolution.resolved")
    outcome = value.get("outcome")
    if resolved:
        if outcome not in RESOLUTION_OUTCOMES:
            raise NewsClaimLedgerError(
                f"resolution.outcome must be one of {sorted(RESOLUTION_OUTCOMES)} when resolved is True"
            )
        _parse_ts(value.get("resolved_at"), "resolution.resolved_at")
    else:
        if outcome not in (None, "UNRESOLVED"):
            raise NewsClaimLedgerError("resolution.outcome must be None/UNRESOLVED when resolved is False")
    notes = value.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise NewsClaimLedgerError("resolution.notes must be a string or None")


def validate_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one atomic claim record. Fails closed on any malformed field.

    Returns a small summary dict on success; raises `NewsClaimLedgerError`
    (never a bare exception, never a silent pass) on any violation.
    """
    if not isinstance(claim, Mapping):
        raise NewsClaimLedgerError("claim must be a mapping")

    missing = REQUIRED_FIELDS.difference(claim)
    if missing:
        raise NewsClaimLedgerError(f"claim missing required fields: {', '.join(sorted(missing))}")

    if claim["ledger_schema_version"] != LEDGER_SCHEMA_VERSION:
        raise NewsClaimLedgerError(
            f"unsupported ledger_schema_version: {claim['ledger_schema_version']!r}"
        )

    claim_id = _text(claim["claim_id"], "claim_id")
    _text(claim["source_id"], "source_id")

    source_tier = claim["source_tier"]
    if source_tier not in SOURCE_TIERS:
        raise NewsClaimLedgerError(f"source_tier must be one of {sorted(SOURCE_TIERS)}, got {source_tier!r}")

    evidence_class = claim["evidence_class"]
    if evidence_class not in EVIDENCE_CLASSES:
        raise NewsClaimLedgerError(
            f"evidence_class must be one of {sorted(EVIDENCE_CLASSES)}, got {evidence_class!r}"
        )

    claim_type = claim["claim_type"]
    if claim_type not in CLAIM_TYPES:
        raise NewsClaimLedgerError(f"claim_type must be one of {sorted(CLAIM_TYPES)}, got {claim_type!r}")

    _bool(claim["direct_observation"], "direct_observation")
    _validate_reporter(claim["reporter"])
    _validate_team(claim["team"])
    _validate_player(claim["player"])

    concerns_game_id = claim["concerns_game_id"]
    if concerns_game_id is not None and not isinstance(concerns_game_id, str):
        raise NewsClaimLedgerError("concerns_game_id must be a string or None")

    _validate_concerns_teams(claim["concerns_teams"])

    postgame_of_game_id = claim["postgame_of_game_id"]
    if postgame_of_game_id is not None and not isinstance(postgame_of_game_id, str):
        raise NewsClaimLedgerError("postgame_of_game_id must be a string or None")

    _optional_ts(claim["published_at"], "published_at")
    _parse_ts(claim["observed_at"], "observed_at")  # observed_at is mandatory and non-null
    _optional_ts(claim["effective_from"], "effective_from")
    _optional_ts(claim["effective_until"], "effective_until")
    _optional_ts(claim["corrected_at"], "corrected_at")

    _text(claim["content_summary"], "content_summary")

    _validate_relation_list(claim["corroborations"], "corroborations", CORROBORATION_RELATIONS)
    _validate_relation_list(claim["contradictions"], "contradictions", CONTRADICTION_RELATIONS)

    correction_of = claim["correction_of"]
    if correction_of is not None and not isinstance(correction_of, str):
        raise NewsClaimLedgerError("correction_of must be a string or None")

    _validate_resolution(claim["resolution"])

    return {
        "claim_id": claim_id,
        "source_tier": source_tier,
        "evidence_class": evidence_class,
        "claim_type": claim_type,
        "team": claim["team"],
    }


def validate_claims(claims: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate a batch. Fails closed on any single malformed claim or a
    duplicate claim_id within the batch (an ingestion bug, not a real
    re-observation -- a genuine re-observation gets a new claim referencing
    the prior one via `corroborations`/`correction_of`, never the same id).
    """
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        raise NewsClaimLedgerError("claims must be a sequence of mappings")
    if not claims:
        raise NewsClaimLedgerError("claims must be non-empty")

    seen: set[str] = set()
    by_tier: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_team: dict[str, int] = {}
    for i, claim in enumerate(claims):
        try:
            summary = validate_claim(claim)
        except NewsClaimLedgerError as exc:
            raise NewsClaimLedgerError(f"claims[{i}]: {exc}") from exc
        if summary["claim_id"] in seen:
            raise NewsClaimLedgerError(f"duplicate claim_id within batch: {summary['claim_id']}")
        seen.add(summary["claim_id"])
        by_tier[summary["source_tier"]] = by_tier.get(summary["source_tier"], 0) + 1
        by_type[summary["claim_type"]] = by_type.get(summary["claim_type"], 0) + 1
        team = summary["team"] or "UNRESOLVED"
        by_team[team] = by_team.get(team, 0) + 1

    return {
        "claim_count": len(claims),
        "unique_claim_ids": len(seen),
        "by_tier": by_tier,
        "by_claim_type": by_type,
        "by_team": by_team,
    }


def claim_eligible_for_game(
    claim: Mapping[str, Any], game_id: str, kickoff_at: str
) -> dict[str, Any]:
    """Enforce the design doc's core temporal-safety principle in code.

    "A claim is eligible only if it existed before the relevant FULL COUNT
    freeze." Concretely:

    1. A claim's own `observed_at`/`published_at` must never be usable to
       inform a prediction for a game whose kickoff has already passed at
       that timestamp -- if either is at-or-after the target game's kickoff,
       the claim is ineligible for that game, full stop.
    2. A claim explicitly tagged `postgame_of_game_id == game_id` (a
       postgame explanation OF that exact game) can never be attached back
       to that same game as a pregame feature, even if some other timestamp
       bookkeeping error would otherwise make it look eligible. This is a
       second, independent, semantic barrier -- not merely a timestamp
       check -- because a postgame claim's whole reason for existing is to
       inform FUTURE games, never the one it is commenting on.

    Returns `{"eligible": bool, "reason": str}`. Never raises for a
    well-formed but ineligible claim; raises `NewsClaimLedgerError` only for
    malformed input (fail-closed on bad data, not merely "ineligible").
    """
    validate_claim(claim)
    game_id = _text(game_id, "game_id")
    kickoff = _parse_ts(kickoff_at, "kickoff_at")

    if claim["postgame_of_game_id"] == game_id:
        return {
            "eligible": False,
            "reason": "POSTGAME_CLAIM_CANNOT_INFORM_ITS_OWN_GAME",
        }

    observed = _parse_ts(claim["observed_at"], "observed_at")
    if observed >= kickoff:
        return {
            "eligible": False,
            "reason": "OBSERVED_AT_OR_AFTER_TARGET_KICKOFF",
        }

    published_raw = claim["published_at"]
    if published_raw is not None:
        published = _parse_ts(published_raw, "published_at")
        if published >= kickoff:
            return {
                "eligible": False,
                "reason": "PUBLISHED_AT_OR_AFTER_TARGET_KICKOFF",
            }

    return {"eligible": True, "reason": "ELIGIBLE_PREGAME_FEATURE"}


def reporter_reliability_scoreboard(
    claims: Sequence[Mapping[str, Any]], shrinkage_k: float = 5.0
) -> dict[str, Any]:
    """A SCOREABLE (not hand-picked) source-reliability framework.

    Per the design doc: reliability must be claim-type specific, must use
    hierarchical shrinkage (league baseline -> evidence class -> outlet ->
    reporter -> reporter x claim type), and must never punish a reporter for
    an untestable claim. This function computes exactly that hierarchy given
    however many *resolved* claims exist.

    With near-zero real resolved claims today (the honest current state of
    this ledger), most cells below the league baseline come back as
    `n: 0, raw_agreement_rate: None` -- a disclosed limitation of available
    data volume, not a defect of the framework or a hidden subjective
    ranking standing in for it.
    """
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        raise NewsClaimLedgerError("claims must be a sequence of mappings")
    for claim in claims:
        validate_claim(claim)

    testable = [
        c for c in claims
        if isinstance(c.get("resolution"), Mapping)
        and c["resolution"].get("resolved") is True
        and c["resolution"].get("outcome") in RESOLUTION_OUTCOMES
    ]

    weight = {"CONFIRMED": 1.0, "PARTIALLY_CONFIRMED": 0.5, "REFUTED": 0.0}

    def _score(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {"n": 0, "raw_agreement_rate": None}
        raw = sum(weight[r["resolution"]["outcome"]] for r in rows) / n
        return {"n": n, "raw_agreement_rate": raw}

    league = _score(testable)
    league_rate = league["raw_agreement_rate"]

    def _shrink(sub: dict[str, Any]) -> dict[str, Any]:
        if sub["n"] == 0 or league_rate is None:
            sub["shrunk_agreement_rate"] = None
            return sub
        n = sub["n"]
        sub["shrunk_agreement_rate"] = (
            sub["raw_agreement_rate"] * n + league_rate * shrinkage_k
        ) / (n + shrinkage_k)
        return sub

    def _group_and_score(key_fn) -> dict[str, Any]:
        buckets: dict[str, list[Mapping[str, Any]]] = {}
        for c in testable:
            buckets.setdefault(key_fn(c), []).append(c)
        return {key: _shrink(_score(rows)) for key, rows in buckets.items()}

    by_evidence_class = {ec: _shrink(_score([c for c in testable if c["evidence_class"] == ec]))
                          for ec in sorted(EVIDENCE_CLASSES)}
    by_outlet = _group_and_score(lambda c: c["reporter"].get("outlet") or "UNKNOWN_OUTLET")
    by_reporter = _group_and_score(lambda c: c["reporter"].get("name") or "UNKNOWN_REPORTER")
    by_reporter_x_claim_type = _group_and_score(
        lambda c: f"{c['reporter'].get('name') or 'UNKNOWN_REPORTER'}::{c['claim_type']}"
    )

    return {
        "total_claim_count": len(claims),
        "testable_claim_count": len(testable),
        "league_baseline": league,
        "by_evidence_class": by_evidence_class,
        "by_outlet": by_outlet,
        "by_reporter": by_reporter,
        "by_reporter_x_claim_type": by_reporter_x_claim_type,
        "note": (
            "Hierarchical shrinkage league->evidence_class->outlet->reporter->"
            "reporter_x_claim_type per the News Brain design doc's Reliability "
            "model. Claims with no resolution or an UNRESOLVED outcome are "
            "excluded entirely -- 'never punish a reporter for a claim that "
            "was not actually testable.' Near-zero real resolved claims today "
            "is expected and disclosed, not smoothed over."
        ),
    }
