#!/usr/bin/env python3
"""Reprice only explicitly pregame dashboard rows into ``live.json``.

Sportsbook observations distinguish a successful absence (``NOT_POSTED``)
from a failed fetch (``FETCH_FAILED``). A failure preserves the prior quote,
successful-observation timestamp, and recommendation classification.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .live_state import (
        PRICE_FIELDS, apply_live_overlay, atomic_write_json, before_betting_cutoff,
        game_state, load_live_state, merge_prop_fields, stable_prop_id,
        touch_channel, touch_heartbeat, utc_now,
    )
    from .publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from .prepare_pages_artifact import normalize_live, normalize_payload
except ImportError:
    from live_state import (
        PRICE_FIELDS, apply_live_overlay, atomic_write_json, before_betting_cutoff,
        game_state, load_live_state, merge_prop_fields, stable_prop_id,
        touch_channel, touch_heartbeat, utc_now,
    )
    from publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from prepare_pages_artifact import normalize_live, normalize_payload


OBSERVATION_STATES = frozenset(("MATCHED", "NOT_POSTED", "FETCH_FAILED", "IN_PLAY"))
MARKET_VALUE_FIELDS = (
    "market_odds", "market_implied", "market_edge", "price_clears", "market_hold",
)
LIVE_FIELDS = tuple(sorted(PRICE_FIELDS | frozenset((
    "market_fetch_state", "market_fetch_checked_at",
))))


def _refresh_summary(payload):
    props = payload.get("props") or []
    summary = payload.setdefault("summary", {})
    summary["n_top_pick"] = sum(row.get("recommendation_status") == "top_pick" for row in props)
    summary["n_lean"] = sum(row.get("recommendation_status") == "lean" for row in props)
    summary["n_value"] = sum(row.get("recommendation_status") == "value" for row in props)


def _market_family(row):
    stat = (row.get("projection") or {}).get("stat") or row.get("stat")
    return {
        "strikeouts": "strikeouts",
        "pitcher_outs": "pitcher_outs",
        "nrfi_combined": "first_inning",
        "combined_strikeouts": "combined_strikeouts",
    }.get(stat, "general_batter")


def _fetch_family(name, fetcher):
    try:
        observation = fetcher(strict=True, with_evidence=True)
        if not (hasattr(observation, "root_state")
                and hasattr(observation, "events")
                and hasattr(observation, "values")):
            raise RuntimeError("fetcher omitted structured market-observation evidence")
        return {"family": name, "observation": observation, "error": None}
    except Exception as exc:
        return {"family": name, "observation": None,
                "error": f"{type(exc).__name__}: {exc}"[:300]}


def _game_fact(state, stamp, source="mlb_game_feed_by_game_pk"):
    return {
        "game_state": state,
        "game_state_observed_at": stamp,
        "game_state_source": source,
    }


def _family_args(family, values):
    args = {
        "prices": {}, "k_prices": {}, "fi_prices": {},
        "po_prices": {}, "combined_k_prices": {},
    }
    key = {
        "general_batter": "prices", "strikeouts": "k_prices",
        "first_inning": "fi_prices", "pitcher_outs": "po_prices",
        "combined_strikeouts": "combined_k_prices",
    }[family]
    args[key] = values or {}
    return args


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
    original_live = copy.deepcopy(live)
    effective = apply_live_overlay(payload, live)
    props = effective.get("props") or []
    if not props:
        print(f"{data_path}: no props to reprice -- nothing to do.")
        return effective

    import grade_results as gr
    import odds_fanduel as fd
    import recommendation as gprec

    registry = load_registry(registry_path)
    public_ids = set(registry["entries"])
    initial_at = utc_now()
    contexts = gr.fetch_game_contexts([row.get("game_pk") for row in props], refresh=True)
    if not contexts:
        print("No MLB game feeds returned by game_pk; refusing all new wagering decisions.")
        return effective

    pregame = []
    for row in props:
        prop_id = stable_prop_id(row)
        try:
            game_pk = int(row.get("game_pk"))
        except (TypeError, ValueError):
            continue
        context = contexts.get(game_pk)
        current = game_state((context or {}).get("status"), row=row, now=initial_at)
        if context is None or current == "unknown":
            continue
        if current != "pregame" or not before_betting_cutoff(row, initial_at):
            # Freeze selection/price after the wagering boundary. Game state
            # may advance, but this price owner never reclassifies it.
            merge_prop_fields(live, prop_id, {
                **_game_fact(current, initial_at),
                "market_fetch_state": "IN_PLAY",
                "market_fetch_checked_at": initial_at,
            }, initial_at)
            continue
        pregame.append(row)

    if not pregame:
        # Real contexts were fetched (the `if not contexts` guard above
        # already returned otherwise), so this is a genuine completed check
        # even though no prop needed a new price -- see touch_heartbeat's
        # own docstring for why this must be recorded distinctly from
        # prices_updated_at.
        touch_heartbeat(live, "prices", initial_at)
        atomic_write_json(live_path, live)
        print("No explicitly pregame props remain; prices and classifications are frozen.")
        return apply_live_overlay(payload, live)

    feeds = {
        "general_batter": _fetch_family("general_batter", fd.fetch_prop_prices),
        "strikeouts": _fetch_family("strikeouts", fd.fetch_pitcher_strikeouts),
        "pitcher_outs": _fetch_family("pitcher_outs", fd.fetch_pitcher_outs),
        "first_inning": _fetch_family("first_inning", fd.fetch_first_inning_totals),
        "combined_strikeouts": _fetch_family(
            "combined_strikeouts", fd.fetch_combined_pitcher_strikeouts,
        ),
    }
    fetched_at = utc_now()
    failed_families = {
        name for name, result in feeds.items()
        if result["observation"] is None
        or result["observation"].root_state != fd.EVENTS_DISCOVERED
        or result["observation"].errors
    }

    successful_rows = []
    before = {}
    for row in pregame:
        prop_id = stable_prop_id(row)
        family = _market_family(row)
        before[prop_id] = {field: copy.deepcopy(row.get(field)) for field in LIVE_FIELDS}
        result = feeds[family]
        if result["observation"] is None:
            merge_prop_fields(live, prop_id, {
                "market_fetch_state": "FETCH_FAILED",
                "market_fetch_checked_at": fetched_at,
                "market_fetch_failed_at": fetched_at,
                "market_failure_reason": result["error"],
                "market_family": family,
            }, fetched_at)
            continue
        evidence = fd.market_evidence_for_row(result["observation"], row)
        working = copy.deepcopy(row)
        for field in MARKET_VALUE_FIELDS:
            working[field] = None
        working["stale"] = False
        _, matched = fd.attach_market_prices(
            [working], **_family_args(family, evidence["values"]),
        )
        if not matched and not evidence["absence_proven"]:
            merge_prop_fields(live, prop_id, {
                "market_fetch_state": "FETCH_FAILED",
                "market_fetch_checked_at": fetched_at,
                "market_fetch_failed_at": fetched_at,
                "market_failure_reason": evidence["reason"][:300],
                "market_family": family,
            }, fetched_at)
            failed_families.add(family)
            continue
        working["market_fetch_state"] = "MATCHED" if matched else "NOT_POSTED"
        working["market_observation_state"] = working["market_fetch_state"]
        working["market_fetch_checked_at"] = fetched_at
        working["market_observed_at"] = fetched_at
        working["market_family"] = family
        working["price_basis_board_generated_at"] = effective.get("generated_at")
        working["market_fetch_failed_at"] = row.get("market_fetch_failed_at")
        working["market_failure_reason"] = None
        successful_rows.append(working)

    if successful_rows:
        # This calls the existing, unchanged recommendation policy. Only
        # successfully observed relevant families may be reclassified.
        gprec.attach_recommendations(
            successful_rows, odds_fetched_at=fetched_at,
            board_generated_at=effective.get("generated_at"),
        )
        for row in successful_rows:
            row["recommendation_status"] = row.pop("status", row.get("recommendation_status"))

    # Revalidate every successfully fetched row immediately before committing
    # any price/classification fact. This freezes already-public snapshots too:
    # a game can cross first pitch while sportsbook requests are in flight even
    # when the row is not a newly qualifying Top Pick.
    final_at = utc_now() if successful_rows else None
    final_contexts = gr.fetch_game_contexts(
        [row.get("game_pk") for row in successful_rows], refresh=True,
    ) if successful_rows else {}

    n_changed = 0
    for row in successful_rows:
        prop_id = stable_prop_id(row)
        old = before[prop_id]
        new_status = row.get("recommendation_status")
        newly_public_candidate = new_status == "top_pick" and prop_id not in public_ids
        try:
            game_pk = int(row.get("game_pk"))
        except (TypeError, ValueError):
            game_pk = None
        current = game_state(
            (final_contexts.get(game_pk) or {}).get("status"), row=row, now=final_at,
        )
        if current != "pregame" or not before_betting_cutoff(row, final_at):
            label = "new Top Pick candidate" if newly_public_candidate else "pregame quote"
            print(f"Suppressed post-cutoff {label} {prop_id} during final gate.")
            continue

        new = {field: copy.deepcopy(row.get(field)) for field in LIVE_FIELDS}
        changes = {field: value for field, value in new.items() if old.get(field) != value}
        if not changes:
            continue
        merge_prop_fields(live, prop_id, changes, fetched_at, channel="prices")
        n_changed += 1

    if successful_rows:
        touch_channel(live, "prices", fetched_at)
    # Unconditional: every family was actually attempted this cycle
    # (feeds/failed_families above), whether or not any row ended up
    # successfully repriced -- see touch_heartbeat's own docstring.
    touch_heartbeat(live, "prices", fetched_at)
    atomic_write_json(live_path, live)
    effective = apply_live_overlay(payload, live)
    _refresh_summary(effective)
    print(
        f"Wrote {live_path} atomically ({n_changed} successful-family prop change(s), "
        f"{len(failed_families)} indeterminate family/families preserved); "
        f"left {data_path} unchanged."
    )
    return effective


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"))
    parser.add_argument("--live", default=None)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    args = parser.parse_args()
    if not os.path.exists(args.data):
        print(f"{args.data} does not exist yet -- nothing to reprice.")
        return 0
    refresh(args.data, live_path=args.live, registry_path=args.registry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
