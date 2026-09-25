"""Tier 1 NFL intelligence: the shared factor/feature contract.

Every Tier 1 factor feeds ONE research pathway:

    game & team context -> personnel & availability -> expected team
    opportunities -> player opportunity share -> individual outcome
    distribution -> sportsbook market evaluation

A factor is a producer of *feature rows*. A *consumer* is a research
challenger that turns feature rows (plus B0's own inputs) into a prediction
for a player-game or team-game. Consumers are scored against B0 by
`nfl.research.tier1.harness`, on matched rows, so every report can say which
information changed a prediction and whether the change improved accuracy.

Rules every producer and consumer must follow (enforced where checkable):

* Strictly prior information. A historical feature row for (season, week)
  may use only games that sort strictly before (season, week). A live row
  carries the real `available_at` time of its newest source and must not be
  later than the prediction cutoff.
* Unknown stays unknown. A value that the source does not support is the
  literal string ``UNKNOWN`` -- never 0, never None, never an imputation the
  row does not declare.
* Provenance. Every row names its source ids; a consumer's report names the
  factor ids it read.
* Research only. Nothing here changes authoritative B0, a public pick, or a
  selector. `MILESTONES` below are three different claims; BUILT is not
  VALIDATED.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Mapping

UNKNOWN = "UNKNOWN"

# Workstream ownership (Mission "NFL operational completion + Tier 1").
FACTORS: dict[str, dict[str, str]] = {
    "F1_GAME_CONTEXT": {
        "name": "Sportsbook game context (spread, total, implied team total)",
        "workstream": "C", "layer": "game_context"},
    "F2_SNAP_SHARE_ROLE": {
        "name": "Snap share, trends and role changes",
        "workstream": "B", "layer": "player_share"},
    "F3_TARGET_AIR_YARDS": {
        "name": "Target share, air-yards share, aDOT, WOPR",
        "workstream": "B", "layer": "player_share"},
    "F4_RED_ZONE": {
        "name": "Red-zone and goal-line opportunity",
        "workstream": "D", "layer": "player_share"},
    "F5_PASS_TENDENCY_PACE": {
        "name": "Team pass rate over expected, neutral pass rate, pace",
        "workstream": "C", "layer": "team_opportunity"},
    "F6_ENVIRONMENT": {
        "name": "Weather forecast, roof, surface",
        "workstream": "C", "layer": "game_context"},
    "F7_REST_TRAVEL": {
        "name": "Rest, short week, bye, travel, time zone",
        "workstream": "C", "layer": "game_context"},
    "F8_INJURY_PRACTICE": {
        "name": "Official injury and practice reports",
        "workstream": "B", "layer": "availability"},
    "F9_ABSENCE_REDISTRIBUTION": {
        "name": "Teammate-absence opportunity redistribution",
        "workstream": "B", "layer": "availability"},
    "F10_OPPONENT_POSITION": {
        "name": "Opponent allowed-by-position and matchup context",
        "workstream": "C", "layer": "team_opportunity"},
}

PATHWAY_LAYERS = ("game_context", "availability", "team_opportunity",
                  "player_share", "outcome_distribution", "market")

# Report statuses (mission definition of completion).
MILESTONES = ("NOT_STARTED", "PARTIAL", "BLOCKED", "BUILT", "LIVE_RESEARCH",
              "VALIDATED", "REJECTED")

HISTORICAL_PRIOR_WEEKS = "PRIOR_WEEKS_ONLY"

REQUIRED_KEYS = ("factor_id", "season", "week", "game_id", "team", "gsis_id",
                 "features", "source_ids", "information_cutoff")


class FeatureContractError(ValueError):
    pass


def is_unknown(value: Any) -> bool:
    return value == UNKNOWN


def _parse_utc(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise FeatureContractError(f"timestamp without timezone: {value!r}")
    return parsed


def validate_feature_row(row: Mapping[str, Any], *, prediction_cutoff: str | None = None) -> None:
    """Raise FeatureContractError unless `row` satisfies the contract.

    ``information_cutoff`` is either ``PRIOR_WEEKS_ONLY`` (historical rows
    built from strictly earlier games) or an ISO-8601 UTC timestamp -- the
    real time the newest source was available. For a live row, pass the
    prediction cutoff and the row must not be later than it.
    """
    missing = [key for key in REQUIRED_KEYS if key not in row]
    if missing:
        raise FeatureContractError(f"missing keys {missing}")
    if row["factor_id"] not in FACTORS:
        raise FeatureContractError(f"unknown factor_id {row['factor_id']!r}")
    if not isinstance(row["season"], int) or not isinstance(row["week"], int):
        raise FeatureContractError("season/week must be int")
    if not row["game_id"] or not row["team"]:
        raise FeatureContractError("game_id and team are required")
    if row["gsis_id"] is not None and not str(row["gsis_id"]).strip():
        raise FeatureContractError("gsis_id must be None (team row) or a non-empty id")
    if not row["source_ids"] or not all(isinstance(s, str) and s for s in row["source_ids"]):
        raise FeatureContractError("source_ids must be a non-empty list of ids")
    features = row["features"]
    if not isinstance(features, Mapping) or not features:
        raise FeatureContractError("features must be a non-empty mapping")
    for name, value in features.items():
        if value is None:
            raise FeatureContractError(f"{name}: None is not allowed; use UNKNOWN")
        if is_unknown(value) or isinstance(value, (bool, str)):
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise FeatureContractError(f"{name}: non-finite or non-numeric value {value!r}")
    cutoff = row["information_cutoff"]
    if cutoff == HISTORICAL_PRIOR_WEEKS:
        if prediction_cutoff is not None:
            raise FeatureContractError("a live prediction needs a timestamped information_cutoff")
        return
    available = _parse_utc(str(cutoff))
    if prediction_cutoff is not None and available > _parse_utc(prediction_cutoff):
        raise FeatureContractError(
            f"information available at {cutoff} is after the prediction cutoff {prediction_cutoff}")


def assert_strictly_prior(history: Iterable[tuple[int, int]], target: tuple[int, int]) -> None:
    """Every (season, week) in `history` must sort strictly before `target`."""
    for key in history:
        if tuple(key) >= tuple(target):
            raise FeatureContractError(f"history {key} is not strictly before target {target}")


def feature_row(factor_id: str, *, season: int, week: int, game_id: str, team: str,
                gsis_id: str | None, features: Mapping[str, Any], source_ids: list[str],
                information_cutoff: str = HISTORICAL_PRIOR_WEEKS) -> dict[str, Any]:
    row = {"factor_id": factor_id, "season": season, "week": week, "game_id": game_id,
           "team": team, "gsis_id": gsis_id, "features": dict(features),
           "source_ids": list(source_ids), "information_cutoff": information_cutoff}
    validate_feature_row(row)
    return row


def status_record(factor_id: str, *, milestone: str, consumer: str | None, evidence: str,
                  blockers: list[str] | None = None, activation: Mapping[str, Any] | None = None,
                  evaluation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One factor's evidence-backed status line for the mission report."""
    if factor_id not in FACTORS:
        raise FeatureContractError(f"unknown factor_id {factor_id!r}")
    if milestone not in MILESTONES:
        raise FeatureContractError(f"unknown milestone {milestone!r}")
    if milestone in ("BUILT", "LIVE_RESEARCH", "VALIDATED", "REJECTED") and not consumer:
        raise FeatureContractError(f"{milestone} requires a connected consumer")
    if milestone == "VALIDATED" and not evaluation:
        raise FeatureContractError("VALIDATED requires an evaluation result")
    return {"factor_id": factor_id, **FACTORS[factor_id], "milestone": milestone,
            "consumer": consumer, "evidence": evidence, "blockers": list(blockers or []),
            "activation": dict(activation or {}), "evaluation": dict(evaluation or {})}
