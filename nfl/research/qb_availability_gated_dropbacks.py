#!/usr/bin/env python3
"""Current-week-safe QB-availability gate for the QB-continuity-aware team-
dropback consumer (Mission 9 Workstream B).

## The exact gap this closes

PR #185's `qb_change_team_dropbacks.resolve_incumbent_qb` identifies the last
OBSERVED prior-game starter -- a real, strictly-prior HISTORICAL-incumbent
proxy, but never a confirmation of who is actually expected to start THIS
week. SUPERCHAD's Mission 8 checkpoint (Issue #91 comment `5800978133`)
correctly flagged this: "a newly announced Week N starting-QB switch could
be missed until after he first starts; or the latest prior-game starter may
have become unavailable/benched. ... Never infer current-week starter from
previous game as confirmed."

This module does not solve that fully -- this repo ingests no depth-chart or
official-starter-designation source (see `qb_continuity_features.py`'s own
docstring), so the IDENTITY of a new starter cannot be determined in
advance. What real, already-ingested, already-built, previously-unconsumed
evidence CAN tell us, safely and for the current week: `nfl/research/
injury_availability_features.py` already ingests nflverse's real weekly
injury report (filed Wednesday-Friday, i.e. genuinely BEFORE that week's own
games -- see that module's own docstring for the pregame-safety argument)
and already computes, for the CURRENT week's own filed report, whether the
prior-week incumbent QB carries a game-affecting designation. That module's
own docstring states plainly it "does not evaluate that hypothesis...or wire
anything into a model or selector." Grepping this repo confirms it has no
other caller. This is a second, real instance of the "tested but
unconsumed" failure mode this project has already found and fixed twice
(the coaching filter, PR #179 -> #181; and now this).

## What this module adds

`classify_current_week_qb_availability` reuses `injury_availability_
features.build_prior_starter_availability_features` UNMODIFIED (by feeding
it a single synthetic QB-continuity-shaped row carrying the real incumbent
identity `qb_change_team_dropbacks.resolve_incumbent_qb` already resolved,
reused unmodified) and refines its five raw `availability_status` values
into four real, explicitly-labeled current-week states -- exactly the
CONFIRMED / DISPUTED / EXPECTED-UNAVAILABLE / UNKNOWN distinction Mission 9
Section 5 requires:

- `CONFIRMED_AVAILABLE`: not listed on the current week's own filed report
  at all.
- `DISPUTED`: listed `Questionable` on the current week's own filed report
  -- real, genuine ambiguity, not resolved either way.
- `EXPECTED_UNAVAILABLE`: listed `Out` or `Doubtful` on the current week's
  own filed report -- real, current-week, pregame-safe evidence the
  historical incumbent will likely NOT play this week.
- `UNKNOWN`: no real prior-starter identity to look up, or a season this
  source does not cover -- never silently treated as "healthy."

`gate_qb_aware_prediction_by_current_week_availability` then does the
honest thing when real current-week evidence contradicts the QB-aware
consumer's implicit continuity assumption: when the incumbent is
`EXPECTED_UNAVAILABLE` or `DISPUTED` this week, the QB-SPECIFIC rolling
window (built entirely from games HE started) is real evidence about a
player who may not play this week, so trusting it as this week's own
volume estimate is exactly the unsafe inference SUPERCHAD flagged. The gate
falls back to the "no QB-identity adjustment" naive control (the same
control `predict_team_pass_dropbacks_qb_aware` already computes and
preserves for exactly this kind of comparison) rather than the identity-
specific number, and records the real reason. It does NOT invent a new
starter's identity or a redistributed target/route/carry effect -- Mission
9 Section 5 is explicit that a team-passing-volume change must be kept
separate from a claim about teammate-level route/target/chemistry effects,
and this module makes no such claim.

## What this module reuses, never rebuilds

- `injury_availability_features.build_prior_starter_availability_features`
  (real weekly-injury-report ingestion, fail-closed validation, PIT-safety
  argument) -- called, never reimplemented.
- `qb_change_team_dropbacks.resolve_incumbent_qb`,
  `predict_team_pass_dropbacks_qb_aware` -- reused unmodified; this module
  makes zero changes to that file or to `injury_availability_features.py`.

## Fail-closed discipline

`classify_current_week_qb_availability` raises rather than guesses if
`build_prior_starter_availability_features` (itself fail-closed) ever
returns a raw `availability_status`/`report_status_raw` combination this
module's four-bucket mapping does not recognize -- a real future nflverse
schema change should be a loud failure here, not a silently misclassified
bucket.

## Disclosed limitation: `UNKNOWN` never gates

`UNKNOWN` (no real prior-starter identity, or a pre-2009 season the injury
source does not cover -- see `injury_availability_features.
EARLIEST_COVERED_SEASON`) is deliberately NOT in `GATE_TRIGGERING_BUCKETS`.
For the "no real prior identity" case this is provably a no-op: `qb_change_
team_dropbacks.predict_team_pass_dropbacks_qb_aware` already makes `qb_aware`
and `naive_control` numerically identical whenever no incumbent resolves, so
there is nothing to gate. For the real pre-2009-season case, a genuine
incumbent CAN resolve (this repo's QB-continuity substrate itself has no
2009 floor) while the injury source cannot confirm his real current-week
status -- meaning `qb_aware` and `naive_control` COULD legitimately differ
there with no real availability evidence either way. This module reports
that combination as `UNKNOWN` rather than fabricating a decision, but does
NOT gate it: absent real evidence the incumbent is unavailable, defaulting
to trusting the historical-continuity assumption (the same default `qb_
change_team_dropbacks.py` itself makes) is the more conservative choice
than defaulting to distrust it. This repo's real current-week usage is
always a modern (2009+), covered season, so this combination is a
theoretical historical-backtest edge case, not a live-usage gap -- disclosed
explicitly here rather than silently untested.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from nfl.research.injury_availability_features import (
    InjuryAvailabilityError,
    build_prior_starter_availability_features,
)
from nfl.research.qb_change_team_dropbacks import (
    TeamOpportunityChallengerError,
    predict_team_pass_dropbacks_qb_aware,
    resolve_incumbent_qb,
)

CONFIRMED_AVAILABLE = "CONFIRMED_AVAILABLE"
DISPUTED = "DISPUTED"
EXPECTED_UNAVAILABLE = "EXPECTED_UNAVAILABLE"
UNKNOWN = "UNKNOWN"

# Buckets for which the QB-specific rolling window is treated as unsafe to
# trust as this week's own volume estimate. DISPUTED is included: a real
# "Questionable" designation is genuine, disclosed uncertainty, not a
# healthy-and-confirmed signal -- Mission 9 Section 5 requires distinguishing
# confirmed from disputed, not collapsing disputed into confirmed-available.
GATE_TRIGGERING_BUCKETS = frozenset({EXPECTED_UNAVAILABLE, DISPUTED})


def classify_current_week_qb_availability(
    *,
    team: str,
    opponent_team: str,
    target_season: int,
    target_week: int,
    incumbent_player_id: str | None,
    injury_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Real, current-week-safe availability classification for one team's
    real strictly-prior incumbent QB, from that week's own real filed
    nflverse injury report (reused unmodified).

    `incumbent_player_id` is expected to come from `qb_change_team_
    dropbacks.resolve_incumbent_qb`, called by the caller -- this function
    does not resolve it itself so callers can reuse one resolution across
    multiple calls (e.g. gating both the receptions and a future market's
    projection from the same real incumbent lookup).
    """
    qb_continuity_row = {
        "season": target_season,
        "week": target_week,
        "game_type": "REG",
        "team": team,
        "opponent_team": opponent_team,
        "features": {"prior_starter_player_id": incumbent_player_id},
    }
    [availability_row] = build_prior_starter_availability_features(
        list(injury_rows), [qb_continuity_row],
    )

    status = availability_row["availability_status"]
    raw = availability_row["report_status_raw"]

    if status in ("UNKNOWN_NO_PRIOR_STARTER_IDENTITY", "SEASON_NOT_COVERED_BY_SOURCE"):
        bucket = UNKNOWN
    elif status in ("LISTED_OUT", "LISTED_DOUBTFUL"):
        bucket = EXPECTED_UNAVAILABLE
    elif status == "NOT_LISTED_GAME_AFFECTING_STATUS" and raw == "QUESTIONABLE":
        bucket = DISPUTED
    elif status == "NOT_LISTED_GAME_AFFECTING_STATUS" and raw in (None, ""):
        # `raw is None`: genuinely no row filed for this player/team/week.
        # `raw == ""`: a real row WAS filed (e.g. practice-report-only
        # participation) but carries no game-affecting designation --
        # `injury_availability_features.NOT_GAME_AFFECTING_STATUSES` already
        # treats blank the same as no real game-status risk; this module
        # preserves that same real-data equivalence rather than treating an
        # empty string as an unrecognized case.
        bucket = CONFIRMED_AVAILABLE
    else:
        raise TeamOpportunityChallengerError(
            f"unrecognized real availability combination status={status!r} raw={raw!r} "
            "-- refusing to guess a bucket for an unexpected source shape"
        )

    return {
        "incumbent_availability_bucket": bucket,
        "availability_status": status,
        "report_status_raw": raw,
        "incumbent_player_id": incumbent_player_id,
        "source_class": availability_row["source_class"],
    }


def predict_team_pass_dropbacks_availability_gated(
    team_box_score_rows,
    *,
    team: str,
    opponent_team: str,
    target_season: int,
    target_week: int,
    starters,
    injury_rows: Iterable[Mapping[str, Any]],
    opponent_defense_allowed: float | None,
    opponent_defense_prior_games_n: int,
    rolling_window: int = 5,
) -> dict[str, Any]:
    """The real availability-gated consumer: computes the QB-aware
    prediction exactly as `predict_team_pass_dropbacks_qb_aware` already
    does (reused unmodified), then gates it by the real current-week
    availability evidence above.

    Never fabricates a redistribution or a new starter's identity: when the
    gate triggers, the reported number falls back to the SAME real
    naive-control value the QB-aware consumer already computes and
    preserves, never a guessed number for a starter this repo cannot name.
    """
    qb_result = predict_team_pass_dropbacks_qb_aware(
        team_box_score_rows, team=team, target_season=target_season, target_week=target_week,
        starters=starters, opponent_defense_allowed=opponent_defense_allowed,
        opponent_defense_prior_games_n=opponent_defense_prior_games_n,
        rolling_window=rolling_window,
    )
    incumbent_id = qb_result["qb_note"]["incumbent_player_id"]
    availability_info = classify_current_week_qb_availability(
        team=team, opponent_team=opponent_team, target_season=target_season,
        target_week=target_week, incumbent_player_id=incumbent_id, injury_rows=injury_rows,
    )

    gate_applied = availability_info["incumbent_availability_bucket"] in GATE_TRIGGERING_BUCKETS
    if gate_applied:
        gated_prediction = qb_result["predicted_dropbacks_naive_control"]
        gated_basis = qb_result["naive_control_basis"]
        gate_reason = (
            "CURRENT_WEEK_INCUMBENT_"
            f"{availability_info['incumbent_availability_bucket']}"
            "_REAL_CONTINUITY_ASSUMPTION_UNSAFE"
        )
    else:
        gated_prediction = qb_result["predicted_dropbacks_qb_aware"]
        gated_basis = qb_result["qb_aware_basis"]
        gate_reason = None

    return {
        **qb_result,
        "predicted_dropbacks_availability_gated": gated_prediction,
        "availability_gated_basis": gated_basis,
        "availability_gate_applied": gate_applied,
        "availability_gate_reason": gate_reason,
        "incumbent_availability": availability_info,
    }


__all__ = [
    "CONFIRMED_AVAILABLE",
    "DISPUTED",
    "EXPECTED_UNAVAILABLE",
    "UNKNOWN",
    "GATE_TRIGGERING_BUCKETS",
    "classify_current_week_qb_availability",
    "predict_team_pass_dropbacks_availability_gated",
    "resolve_incumbent_qb",
]
