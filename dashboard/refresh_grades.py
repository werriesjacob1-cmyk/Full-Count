#!/usr/bin/env python3
"""Advance game and settlement state for deployment-proven Top Picks.

The durable publication registry defines the population. Live threshold
crossings are ``provisional_hit`` and may be corrected by an authoritative
Final observation. This script owns only ``live.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .live_state import (
        apply_live_overlay, atomic_write_json, game_state, load_live_state,
        merge_prop_fields, parse_utc, stable_prop_id, touch_channel, touch_heartbeat, utc_now,
    )
    from .publication_registry import (
        DEFAULT_REGISTRY_PATH, all_published_snapshots, load_registry,
    )
    from .prepare_pages_artifact import normalize_live, normalize_payload
    from .settlement_rules import has_authoritative_game_commencement, player_game_status
except ImportError:
    from live_state import (
        apply_live_overlay, atomic_write_json, game_state, load_live_state,
        merge_prop_fields, parse_utc, stable_prop_id, touch_channel, touch_heartbeat, utc_now,
    )
    from publication_registry import (
        DEFAULT_REGISTRY_PATH, all_published_snapshots, load_registry,
    )
    from prepare_pages_artifact import normalize_live, normalize_payload
    from settlement_rules import has_authoritative_game_commencement, player_game_status


EARLY_HIT_STATS = frozenset((
    "hits", "total_bases", "home_runs", "strikeouts", "combined_strikeouts",
    "runs", "rbis", "hits_runs_rbis", "singles", "doubles", "triples",
    "stolen_base", "walks", "pitcher_outs",
))
LIVE_CORRECTION_WINDOW_HOURS = 72

# 2026-08-19 Live Integrity PR 2 (the Keider Montero incident: needed 17
# outs recorded, was pulled after 15, stayed "open" instead of showing a
# live miss). Deliberately narrower than EARLY_HIT_STATS: this is a
# player's OWN counting stat, and ROLE-TERMINAL (gameStatus.isCurrentPitcher
# going false) only proves that specific player can add no more to it.
# combined_strikeouts is excluded on purpose -- it sums TWO starters, and
# one being pulled says nothing about whether the other's continuing
# outing can still carry the total past the threshold; correctly handling
# that needs both players' role status, not implemented here.
PITCHER_ROLE_TERMINAL_STATS = frozenset(("strikeouts", "pitcher_outs"))


def _active_public_snapshots(snapshots, payload, live, now):
    """Bound five-minute MLB requests without losing unresolved state.

    Current-board wagers and any overlay explicitly unresolved/suspended stay
    active indefinitely. Recently played wagers remain active for official
    corrections. Older absent wagers are owned by the durable morning retry,
    not polled every five minutes for the rest of the season.
    """
    now_dt = parse_utc(now)
    if now_dt is None:
        raise ValueError("active-grading cutoff requires strict UTC")
    recent_cutoff = now_dt - timedelta(hours=LIVE_CORRECTION_WINDOW_HOURS)
    current_ids = {row.get("id") for row in payload.get("props") or [] if row.get("id")}
    selected = []
    for row in snapshots:
        prop_id = row.get("id")
        delta = (live.get("props") or {}).get(prop_id)
        if prop_id in current_ids:
            selected.append(row)
            continue
        if delta is not None:
            settlement = delta.get("settlement_state")
            if (settlement not in ("hit", "miss", "void")
                    or delta.get("game_state") in ("suspended", "postponed")):
                selected.append(row)
                continue
            observed_at = parse_utc(delta.get("settlement_observed_at"))
            if observed_at is not None and observed_at >= recent_cutoff:
                selected.append(row)
                continue
        reference = parse_utc(row.get("game_start")) or parse_utc(row.get("published_top_pick_at"))
        if reference is not None and reference >= recent_cutoff:
            selected.append(row)
    return selected


def _candidate_from_row(row):
    return {
        "type": row.get("type"), "game_pk": row.get("game_pk"),
        "player_id": row.get("player_id"), "team": row.get("team"),
        "matchup": row.get("matchup"), "side": row.get("side"),
        "lean": row.get("lean"), "projection": row.get("projection"),
        "combo_player_ids": row.get("combo_player_ids"), "prop": row.get("prop"),
        "bet_side": row.get("bet_side"), "market_side": row.get("market_side"),
        "direction": row.get("direction"),
    }


def _is_explicit_under(row):
    for key in ("bet_side", "market_side", "direction"):
        if str(row.get(key) or "").strip().lower() == "under":
            return True
    return str(row.get("prop") or "").strip().lower().startswith("under ")


def _can_settle_hit_early(row):
    stat = (row.get("projection") or {}).get("stat") or row.get("stat")
    return stat in EARLY_HIT_STATS and not _is_explicit_under(row)


def _first_inning_provisional_hit(row, context):
    """Return a proven live NRFI/YRFI hit, never a provisional miss."""
    stat = (row.get("projection") or {}).get("stat") or row.get("stat")
    if stat != "nrfi_combined":
        return None
    innings = ((((context or {}).get("feed") or {}).get("liveData") or {})
               .get("linescore", {}).get("innings") or [])
    if not innings:
        return None
    away = (innings[0].get("away") or {}).get("runs")
    home = (innings[0].get("home") or {}).get("runs")
    if away is None or home is None:
        return None
    total = away + home
    lean = str(row.get("lean") or "").upper()
    if lean == "YRFI" and total > 0:
        return {"grade": "hit", "actual": total, "reason": "a first-inning run scored"}
    current_inning = (((context.get("feed") or {}).get("liveData") or {})
                      .get("linescore", {}).get("currentInning"))
    if lean == "NRFI" and total == 0 and isinstance(current_inning, int) and current_inning > 1:
        return {"grade": "hit", "actual": 0, "reason": "first inning completed scoreless"}
    return None


def _game_fact(state, stamp, source="mlb_game_feed_by_game_pk"):
    return {
        "game_state": state,
        "game_state_observed_at": stamp,
        "game_state_source": source,
    }


def _role_terminal_pitcher_removed(row, context):
    """True if the live feed's own gameStatus shows this pick's pitcher has
    left the mound (isCurrentPitcher is false) -- the real, standard MLB
    StatsAPI signal this codebase already relies on for eligibility
    determination (settlement_rules._players/gameStatus.isSubstitute).
    A removed pitcher cannot record another out or strikeout in this game.

    ROLE-TERMINAL, not threshold-terminal: this is evidence about WHO can
    still act, not an independently-provable mathematical impossibility
    (that would require reasoning about outs/innings remaining regardless
    of any one player's status -- deliberately not attempted here, see
    PITCHER_ROLE_TERMINAL_STATS' own comment). Because it is role evidence,
    the caller must re-evaluate this every single cycle and always write
    an explicit fact either way, never remembering a stale conclusion --
    see the call site's own comment."""
    stat = (row.get("projection") or {}).get("stat") or row.get("stat")
    if stat not in PITCHER_ROLE_TERMINAL_STATS:
        return None
    player_id = row.get("player_id")
    status = player_game_status((context or {}).get("feed"), player_id)
    if status is None or "isCurrentPitcher" not in status:
        return None
    return not bool(status["isCurrentPitcher"])


def _settlement_fact(state, authority, stamp, source, result=None):
    result = result or {}
    reason = result.get("reason")
    if not reason:
        if state == "provisional_hit":
            reason = "live statistic reached the displayed threshold"
        elif state == "provisional_miss":
            reason = "pitcher removed from the mound before reaching the displayed threshold"
        elif state in ("hit", "miss"):
            reason = "official final statistic compared with the displayed threshold"
        elif state == "open":
            reason = "awaiting authoritative final settlement"
    return {
        "settlement_state": state,
        "settlement_authority": authority,
        "settlement_observed_at": stamp,
        "settlement_source": source,
        "result_actual": result.get("actual"),
        "result_reason": reason,
    }


def _same_settlement(row, fact):
    return all(row.get(field) == fact.get(field) for field in (
        "settlement_state", "settlement_authority", "settlement_source",
        "result_actual", "result_reason",
    ))


def refresh(data_path, live_path=None, registry_path=DEFAULT_REGISTRY_PATH):
    live_path = live_path or os.path.join(os.path.dirname(os.path.abspath(data_path)), "live.json")
    try:
        with open(data_path, encoding="utf-8") as handle:
            raw_payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"dashboard data is unreadable: {data_path}: {exc}") from exc
    if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("props"), list):
        raise RuntimeError(f"dashboard data has an invalid schema: {data_path}")
    try:
        payload, id_map = normalize_payload(raw_payload)
        if os.path.exists(live_path):
            with open(live_path, encoding="utf-8") as handle:
                raw_live = json.load(handle)
        else:
            raw_live = {"props": {}}
        live = normalize_live(raw_live, id_map)
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"dashboard lifecycle state is unreadable/invalid: {exc}") from exc
    registry = load_registry(registry_path)
    stamp = utc_now()
    snapshots = _active_public_snapshots(
        all_published_snapshots(registry), payload, live, stamp,
    )
    if not snapshots:
        print("No active/recent deployment-proven Top Picks require live grading.")
        return apply_live_overlay(payload, live)

    published_payload = {
        "date": payload.get("date"), "generated_at": payload.get("generated_at"),
        "odds_fetched_at": payload.get("odds_fetched_at"), "props": snapshots,
    }
    published = apply_live_overlay(published_payload, live)["props"]

    import grade_results as gr
    contexts = gr.fetch_game_contexts([row.get("game_pk") for row in published], refresh=True)
    if not contexts:
        print("No MLB game feeds returned by game_pk; preserving all last-known-good state.")
        return apply_live_overlay(payload, live)

    changed_ids = set()
    observed_counts = {key: 0 for key in (
        "pregame", "live", "delayed", "suspended", "postponed", "final",
        "cancelled", "unknown", "provisional_hit", "provisional_miss",
        "hit", "miss", "void", "ungraded",
    )}

    for row in published:
        prop_id = stable_prop_id(row)
        try:
            game_pk = int(row.get("game_pk"))
        except (TypeError, ValueError):
            print(f"Skipping {prop_id}: invalid game_pk")
            continue
        context = contexts.get(game_pk)
        if context is None:
            print(f"Game {game_pk} feed failed; preserving {prop_id} for retry.")
            continue
        # 2026-08-26 Dustin May incident, stronger invariant: a real pitch
        # already thrown (has_authoritative_game_commencement, reading only
        # liveData.plays[].playEvents[].isPitch) is direct proof stronger
        # than any scheduled clock -- when present, trust the feed's own
        # status fields outright (row/now omitted, matching game_state()'s
        # own documented unguarded-fallback contract) so a stale/wrong
        # stored game_start can never suppress genuinely authoritative
        # live/final evidence. When commencement is NOT yet proven, the
        # clock guard added for the original incident still applies.
        commenced = has_authoritative_game_commencement(context.get("feed"))
        current_game_state = game_state(
            context.get("status"),
            row=None if commenced else row,
            now=None if commenced else stamp,
        )
        observed_counts[current_game_state] += 1
        if current_game_state == "unknown":
            print(f"Game {game_pk} returned an unknown status; preserving {prop_id}.")
            continue

        changes = {}
        if (row.get("game_state"), row.get("game_state_source")) != (
                current_game_state, "mlb_game_feed_by_game_pk"):
            changes.update(_game_fact(current_game_state, stamp))
        try:
            if current_game_state == "live" and not commenced:
                # Feed claims live/in-progress play but no real pitch has
                # been proven thrown yet (the exact Dustin May shape, and
                # the delayed-start variant: scheduled time has passed but
                # the game genuinely hasn't started). Never write a
                # LIVE-derived provisional_hit/provisional_miss without
                # commencement evidence -- stay open and say why.
                if row.get("settlement_state") not in ("provisional_hit", "hit", "miss", "void"):
                    fact = _settlement_fact(
                        "open", "live_observation", stamp,
                        "mlb_live_status",
                        {"reason": "awaiting proof the game has actually begun"},
                    )
                    if not _same_settlement(row, fact):
                        changes.update(fact)
            elif current_game_state == "live":
                first_inning_observed = _first_inning_provisional_hit(row, context)
                if first_inning_observed is not None:
                    fact = _settlement_fact(
                        "provisional_hit", "live_observation", stamp,
                        "mlb_live_linescore", first_inning_observed,
                    )
                    if not _same_settlement(row, fact):
                        changes.update(fact)
                    observed_counts["provisional_hit"] += 1
                elif _can_settle_hit_early(row):
                    observed = gr.grade_pick(
                        _candidate_from_row(row), {game_pk: context["status"]},
                        date=row.get("slate_date") or payload.get("date"),
                        allow_in_progress=True,
                    )
                    if observed.get("grade") == "hit":
                        fact = _settlement_fact(
                            "provisional_hit", "live_observation", stamp,
                            "mlb_live_box_score", observed,
                        )
                        if not _same_settlement(row, fact):
                            changes.update(fact)
                        observed_counts["provisional_hit"] += 1
                    elif row.get("settlement_state") not in ("provisional_hit", "hit", "miss", "void"):
                        # Re-derived every cycle, never remembered: role-terminal
                        # evidence is reversible (a later fact can reopen it),
                        # so both branches below must always write an explicit
                        # fact rather than skip writing when the prior cycle's
                        # conclusion no longer holds.
                        removed = (
                            _role_terminal_pitcher_removed(row, context)
                            if observed.get("grade") == "miss" else None
                        )
                        if removed:
                            fact = _settlement_fact(
                                "provisional_miss", "live_observation", stamp,
                                "mlb_live_role_terminal_pitching_change", observed,
                            )
                            if not _same_settlement(row, fact):
                                changes.update(fact)
                            observed_counts["provisional_miss"] += 1
                        else:
                            fact = _settlement_fact(
                                "open", "live_observation", stamp,
                                "mlb_live_box_score", observed,
                            )
                            if not _same_settlement(row, fact):
                                changes.update(fact)
                elif row.get("settlement_state") not in ("provisional_hit", "hit", "miss", "void"):
                    # Unders and non-monotonic markets never settle early.
                    fact = _settlement_fact(
                        "open", "live_observation", stamp,
                        "mlb_live_status", {"reason": "awaiting authoritative final"},
                    )
                    if not _same_settlement(row, fact):
                        changes.update(fact)
            elif current_game_state == "final":
                observed = gr.grade_public_pick(
                    _candidate_from_row(row), context,
                    date=row.get("slate_date") or payload.get("date"),
                )
                final_state = observed.get("settlement_state") or "ungraded"
                if final_state not in ("hit", "miss", "void", "ungraded"):
                    final_state = "ungraded"
                if final_state in ("hit", "miss") and not commenced:
                    # 2026-08-27 independent-audit finding: a feed claiming
                    # Final is not itself proof any pitch was ever thrown --
                    # the exact Dustin May shape (or a delayed start with a
                    # stale scheduled clock) can in principle reach this
                    # branch too. A genuine statistical hit/miss requires
                    # the same direct commencement evidence the live path
                    # already requires; fail closed rather than trust
                    # status/eligibility fields alone. void/ungraded
                    # eligibility outcomes (wrong listed starter, no plate
                    # appearance, cancelled/postponed/suspended) do not
                    # require a played pitch and are deliberately NOT gated
                    # here -- see settlement_rules.settlement_eligibility.
                    final_state = "ungraded"
                    observed = {
                        **observed, "settlement_state": "ungraded",
                        "reason": "awaiting_proof_game_actually_commenced",
                    }
                fact = _settlement_fact(
                    final_state, "official_final", stamp,
                    "mlb_official_final_with_fanduel_eligibility", observed,
                )
                if not _same_settlement(row, fact):
                    changes.update(fact)
                observed_counts[final_state] += 1
            # delayed/suspended/postponed/cancelled update only game state.
            # They are not settlement evidence by themselves.
        except Exception as exc:
            print(f"Grading {prop_id} failed; preserving prior settlement: {exc}")

        if changes:
            merge_prop_fields(live, prop_id, changes, stamp, channel="grades")
            changed_ids.add(prop_id)

    if not changed_ids:
        print("No authoritative game/settlement fact changed; live state preserved.")
        # A no-op cycle is still real evidence the grading channel actually
        # looked -- freshness (2026-08-19 Live Integrity PR 1) depends on
        # this heartbeat, not on grades_updated_at, precisely so a long
        # scoreless stretch is never mistaken for a stopped scheduler.
        touch_heartbeat(live, "grades", stamp)
        atomic_write_json(live_path, live)
        return apply_live_overlay(payload, live)
    touch_channel(live, "grades", stamp)
    touch_heartbeat(live, "grades", stamp)
    atomic_write_json(live_path, live)
    print(
        f"Wrote {live_path} atomically for {len(changed_ids)} published Top Pick(s); "
        f"provisional-hit={observed_counts['provisional_hit']} "
        f"provisional-miss={observed_counts['provisional_miss']} "
        f"final-hit={observed_counts['hit']} "
        f"final-miss={observed_counts['miss']} void={observed_counts['void']} "
        f"ungraded={observed_counts['ungraded']}."
    )
    return apply_live_overlay(payload, live)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"))
    parser.add_argument("--live", default=None)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    args = parser.parse_args()
    if not os.path.exists(args.data):
        print(f"{args.data} does not exist yet -- nothing to grade.")
        return 0
    refresh(args.data, live_path=args.live, registry_path=args.registry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
