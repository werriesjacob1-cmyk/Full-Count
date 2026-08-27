#!/usr/bin/env python3
"""Structured FanDuel-US eligibility rules for supported MLB props.

Verified 2026-08-17 against current official FanDuel house rules:

* Illinois: https://www.fanduel.com/fanduel-sportsbook-house-rules-il
* Pennsylvania: https://www.fanduel.com/fanduel-sportsbook-house-rules-pa
* Tennessee: https://www.fanduel.com/fanduel-sportsbook-house-rules-tn

The repository has no configured operating jurisdiction. This module settles
only outcomes on which the inspected state rule sets agree and that can be
proved from the MLB live feed. A jurisdiction-dependent or unavailable rule
remains ``ungraded`` rather than being invented.
"""
from __future__ import annotations


RULESET_VERSION = "fanduel-us-conservative-2026-08-17-v2"
RULE_SOURCE_URLS = (
    "https://www.fanduel.com/fanduel-sportsbook-house-rules-il",
    "https://www.fanduel.com/fanduel-sportsbook-house-rules-pa",
    "https://www.fanduel.com/fanduel-sportsbook-house-rules-tn",
)

BATTER_START_OR_PA_REQUIRED = frozenset((
    "total_bases", "runs", "rbis", "stolen_base",
))
BATTER_PA_REQUIRED = frozenset(("hits_runs_rbis",))
BATTER_STATS = (
    frozenset(("hits", "home_runs"))
    | BATTER_START_OR_PA_REQUIRED | BATTER_PA_REQUIRED
)
PITCHER_STATS = frozenset(("strikeouts", "pitcher_outs"))
PUBLIC_SETTLEMENT_STATS = (
    BATTER_STATS | PITCHER_STATS
    | frozenset(("combined_strikeouts", "nrfi_combined"))
)


def supports_public_settlement(pick):
    """Whether a prospective public Top Pick has a structured final path.

    This is a publication capability gate, not recommendation policy. Markets
    with a statistical grader but no verified cross-jurisdiction action rule
    (currently singles/doubles/triples and other specialties) remain research
    rows and cannot become a new official public Top Pick.
    """
    stat = (pick.get("projection") or {}).get("stat") or pick.get("stat")
    return stat in PUBLIC_SETTLEMENT_STATS


def _players(feed):
    teams = ((feed or {}).get("liveData") or {}).get("boxscore", {}).get("teams", {})
    out = {}
    for side in ("away", "home"):
        for value in ((teams.get(side) or {}).get("players") or {}).values():
            person_id = ((value.get("person") or {}).get("id"))
            if person_id is not None:
                out[int(person_id)] = value
    return out


def player_game_status(feed, player_id):
    """The live feed's own gameStatus object for one player, or None if
    unavailable. Real MLB StatsAPI field (isCurrentPitcher/isSubstitute/
    isCurrentBatter/isOnBench) -- the same boxscore.teams.*.players shape
    _players() above already reads for eligibility determination. Used by
    dashboard/refresh_grades.py's role-terminal pitcher-removed detection
    (2026-08-19 Live Integrity PR 2)."""
    if player_id is None:
        return None
    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        return None
    player = _players(feed).get(pid)
    if player is None:
        return None
    return player.get("gameStatus") or {}


def has_authoritative_game_commencement(feed):
    """True only when the live feed shows at least one real pitch has
    actually been thrown -- the one MLB StatsAPI field reserved
    specifically for that (``playEvents[].isPitch``), as opposed to any
    field that can be populated in a misleading pregame combination.

    2026-08-26 Dustin May incident (game_pk 823584): the live feed
    briefly reported ``abstractGameState=="live"`` 19 minutes before
    that game's own scheduled first pitch, and every other field this
    session checked pregame was ALSO already populated with real-looking
    data at that point -- ``liveData.plays.allPlays`` was non-empty (a
    "Game Advisory / Status Change - Pre-Game" entry, typed as an
    ``atBat`` result), ``liveData.linescore.offense``/``defense`` had a
    real lineup, and ``liveData.linescore.innings[0]`` existed. None of
    those prove a pitch was thrown. The pregame advisory play's own
    ``playEvents`` entry has ``isPitch: false``; a genuine pitch (real
    velocity/strike-zone data in ``pitchData``) has ``isPitch: true``.
    That is the one unambiguous signal found by inspecting real MLB
    payloads (a live in-progress game, a genuinely pregame game, and a
    completed game) rather than assumed from field names.

    Deliberately independent of the row's own stored ``game_start`` or
    the wall clock -- this reads only what the feed itself proves
    happened, so a stale/wrong scheduled time can never suppress real
    commencement evidence, and a feed claiming "live" early can never
    manufacture it either. Also deliberately state-history-agnostic:
    once a real pitch has been thrown, this stays true regardless of
    what happens next (delay, suspension, resumption, Final) -- a pitch
    cannot un-happen. Fails closed (False) on any missing/malformed feed
    data, never guesses.
    """
    feed = feed or {}
    if not isinstance(feed, dict):
        return False
    plays = ((feed.get("liveData") or {}).get("plays") or {})
    if not isinstance(plays, dict):
        return False
    candidates = list(plays.get("allPlays") or [])
    current_play = plays.get("currentPlay")
    if isinstance(current_play, dict):
        candidates.append(current_play)
    for play in candidates:
        if not isinstance(play, dict):
            continue
        for event in (play.get("playEvents") or []):
            if isinstance(event, dict) and event.get("isPitch") is True:
                return True
    return False


def _int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _eligible(reason, **extra):
    return {"eligibility": "eligible", "reason_code": reason,
            "ruleset_version": RULESET_VERSION, **extra}


def _void(reason, **extra):
    return {"eligibility": "void", "reason_code": reason,
            "ruleset_version": RULESET_VERSION, **extra}


def _ungraded(reason, **extra):
    return {"eligibility": "ungraded", "reason_code": reason,
            "ruleset_version": RULESET_VERSION, **extra}


def _conditional(reason, **extra):
    return {"eligibility": "conditional", "reason_code": reason,
            "ruleset_version": RULESET_VERSION, **extra}


def _game_completion(feed):
    game = ((feed or {}).get("gameData") or {}).get("game") or {}
    try:
        scheduled = int(game.get("scheduledInnings") or 9)
    except (TypeError, ValueError):
        return "unknown", None, None
    innings = ((((feed or {}).get("liveData") or {}).get("linescore") or {})
               .get("innings") or [])
    if not innings:
        return "unknown", scheduled, 0
    played = len(innings)
    return ("complete" if played >= scheduled else "shortened"), scheduled, played


def settlement_eligibility(pick, feed, current_game_state):
    """Separate wager action eligibility from statistical threshold grading."""
    if current_game_state in ("postponed", "cancelled", "suspended"):
        return _ungraded(
            f"{current_game_state}_requires_verified_sportsbook_settlement",
            game_state=current_game_state,
        )
    if current_game_state != "final":
        return _ungraded("game_not_authoritatively_final", game_state=current_game_state)
    if not isinstance(feed, dict) or not feed:
        return _ungraded("mlb_game_feed_unavailable")

    completion, scheduled_innings, innings_played = _game_completion(feed)
    if completion == "unknown":
        return _ungraded("official_game_completion_unavailable")

    def completed_action(result):
        if result["eligibility"] == "eligible" and completion == "shortened":
            return _conditional(
                "shortened_game_requires_unequivocally_determined_result",
                scheduled_innings=scheduled_innings, innings_played=innings_played,
                action_reason_code=result["reason_code"],
            )
        result["scheduled_innings"] = scheduled_innings
        result["innings_played"] = innings_played
        return result

    stat = (pick.get("projection") or {}).get("stat") or pick.get("stat")
    players = _players(feed)

    if stat == "combined_strikeouts":
        ids = [int(value) for value in (pick.get("combo_player_ids") or ()) if value is not None]
        if len(ids) != 2:
            return _ungraded("combined_starter_identity_missing")
        missing = []
        for player_id in ids:
            raw = players.get(player_id)
            started = _int((((raw or {}).get("stats") or {}).get("pitching") or {}).get("gamesStarted"))
            if started < 1:
                missing.append(player_id)
        if missing:
            return _void("one_or_both_listed_pitchers_did_not_start", player_ids=missing)
        return completed_action(_eligible("both_listed_pitchers_started"))

    if stat in PITCHER_STATS:
        try:
            player_id = int(pick.get("player_id"))
        except (TypeError, ValueError):
            return _ungraded("listed_pitcher_identity_missing")
        raw = players.get(player_id)
        started = _int((((raw or {}).get("stats") or {}).get("pitching") or {}).get("gamesStarted"))
        if started < 1:
            # This deliberately remains void even when the listed player later
            # appears in relief: FanDuel requires the listed pitcher to start.
            return _void("listed_pitcher_did_not_start", player_id=player_id)
        return completed_action(_eligible("listed_pitcher_started"))

    if stat in BATTER_STATS:
        try:
            player_id = int(pick.get("player_id"))
        except (TypeError, ValueError):
            return _ungraded("batter_identity_missing")
        raw = players.get(player_id)
        if raw is None:
            return _void("batter_not_in_starting_lineup_and_no_plate_appearance",
                         player_id=player_id)
        batting = (((raw.get("stats") or {}).get("batting")) or {})
        pa = _int(batting.get("plateAppearances"))
        game_status = raw.get("gameStatus") or {}
        batting_order = str(raw.get("battingOrder") or "").strip()
        was_starter = bool(batting_order) and not bool(game_status.get("isSubstitute"))
        if stat == "hits":
            # Illinois/Tennessee require the listed hitter to start;
            # Pennsylvania requires a plate appearance. With no product
            # jurisdiction configured, only the intersection (both) and
            # unanimous void case (neither) are authoritative.
            if was_starter and pa > 0:
                return completed_action(_eligible(
                    "batter_started_and_recorded_plate_appearance",
                    plate_appearances=pa, was_starter=was_starter,
                ))
            if not was_starter and pa == 0:
                return _void(
                    "batter_not_in_starting_lineup_and_no_plate_appearance",
                    player_id=player_id, plate_appearances=pa,
                    was_starter=was_starter,
                )
            return _ungraded(
                "jurisdiction_specific_hit_action_requirement",
                player_id=player_id, plate_appearances=pa,
                was_starter=was_starter,
            )
        elif stat == "home_runs":
            # Illinois and Tennessee require a start; Pennsylvania requires a
            # start plus PA. Only unanimous outcomes are authoritative.
            if was_starter and pa > 0:
                return completed_action(_eligible(
                    "batter_started_and_recorded_plate_appearance",
                    plate_appearances=pa, was_starter=was_starter,
                ))
            if not was_starter:
                return _void(
                    "home_run_batter_not_in_starting_lineup",
                    player_id=player_id, plate_appearances=pa,
                    was_starter=was_starter,
                )
            return _ungraded(
                "jurisdiction_specific_home_run_action_requirement",
                player_id=player_id, plate_appearances=pa,
                was_starter=was_starter,
            )
        elif stat in BATTER_PA_REQUIRED:
            # Illinois/Pennsylvania require a PA for H+R+RBI; Tennessee
            # requires the batter to be in the starting lineup.
            if was_starter and pa > 0:
                return completed_action(_eligible(
                    "batter_started_and_recorded_plate_appearance",
                    plate_appearances=pa, was_starter=was_starter,
                ))
            if not was_starter and pa == 0:
                return _void(
                    "batter_not_in_starting_lineup_and_no_plate_appearance",
                    player_id=player_id, plate_appearances=pa,
                    was_starter=was_starter,
                )
            return _ungraded(
                "jurisdiction_specific_hrr_action_requirement",
                player_id=player_id, plate_appearances=pa,
                was_starter=was_starter,
            )
        else:
            # Tennessee requires core batter props to start. Illinois and
            # Pennsylvania allow a start OR a recorded PA. A starter is
            # unanimous action; a pinch hitter with a PA is jurisdictional.
            if was_starter:
                return completed_action(_eligible(
                    "batter_in_starting_lineup",
                    plate_appearances=pa, was_starter=was_starter,
                ))
            if pa == 0:
                return _void(
                    "batter_not_in_starting_lineup_and_no_plate_appearance",
                    player_id=player_id, plate_appearances=pa,
                    was_starter=was_starter,
                )
            return _ungraded(
                "jurisdiction_specific_core_batter_action_requirement",
                player_id=player_id, plate_appearances=pa,
                was_starter=was_starter,
            )

    if stat == "nrfi_combined":
        innings = (((feed.get("liveData") or {}).get("linescore") or {}).get("innings") or [])
        if innings and (innings[0].get("away") or {}).get("runs") is not None \
                and (innings[0].get("home") or {}).get("runs") is not None:
            return completed_action(_eligible("official_first_inning_complete"))
        return _ungraded("official_first_inning_result_unavailable")

    # The project has pitcher-specific first-inning research reads, but no
    # verified FanDuel settlement clause tying that custom representation to
    # a bettable market. Do not infer a rule from a display label.
    if stat == "first_inning_run":
        return _ungraded("unsupported_special_market_rule")

    return _ungraded("unsupported_market_rule", stat=stat)
