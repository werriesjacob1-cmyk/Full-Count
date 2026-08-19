#!/usr/bin/env python3
"""Canonical public-dashboard identity, lifecycle, and merge primitives.

The v3 contract keeps three independent facts independent:

* ``recommendation_status`` is the pregame recommendation classification.
* ``game_state`` is MLB game progress.
* ``settlement_state`` is what is known about the wager outcome.

Live observations are lower authority than an official final observation.
Settlement fields are therefore merged as one indivisible fact rather than as
independent keys. This permits an early green ``provisional_hit`` to be
corrected at Final without mixed state/actual/reason metadata.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote


SCHEMA_VERSION = 3
IDENTITY_SCHEMA_VERSION = 2
LEGACY_LIVE_SCHEMA_MAX = 2

RECOMMENDATION_STATES = frozenset(("top_pick", "lean", "value", "neutral"))
GAME_STATES = frozenset((
    "pregame", "live", "delayed", "suspended", "postponed", "final",
    "cancelled", "unknown",
))
SETTLEMENT_STATES = frozenset((
    "open", "provisional_hit", "hit", "miss", "void", "ungraded",
))
RESULT_AUTHORITIES = frozenset(("none", "live_observation", "official_final"))
RESULT_AUTHORITY_RANK = {"none": 0, "live_observation": 1, "official_final": 2}
TERMINAL_SETTLEMENT_STATES = frozenset(("hit", "miss", "void"))

PRICE_FIELDS = frozenset((
    "market_odds", "market_implied", "market_edge", "price_clears",
    "market_hold", "recommendation_status", "status_reasons", "stale",
    "market_observation_state", "market_observed_at", "market_family",
    "market_fetch_failed_at", "market_failure_reason", "market_fetch_state",
    "market_fetch_checked_at", "price_basis_board_generated_at",
))
SETTLEMENT_FIELDS = frozenset((
    "settlement_state", "settlement_authority", "settlement_observed_at",
    "settlement_source", "result_actual", "result_reason",
))
GAME_FIELDS = frozenset(("game_state", "game_state_observed_at", "game_state_source"))
PUBLICATION_FIELDS = frozenset((
    "published_top_pick_at", "publication_artifact_id", "publication_source_commit",
    "publication_run_id", "publication_deployment_id",
))
PROP_META_FIELDS = frozenset(("_field_updated_at",))
DURABLE_FIELDS = SETTLEMENT_FIELDS | PUBLICATION_FIELDS

_GAME_LEVEL_STATS = frozenset(("nrfi_combined",))
_COMMUTATIVE_COMBO_STATS = frozenset(("combined_strikeouts",))
_FIXED_HALF_RUN_STATS = frozenset(("nrfi_combined", "first_inning_run"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value):
    """Return an aware UTC datetime; reject naive/non-UTC/malformed new state."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if (parsed.tzinfo is None or parsed.utcoffset() is None
            or parsed.utcoffset().total_seconds() != 0):
        return None
    return parsed.astimezone(timezone.utc)


def parse_legacy_utc(value):
    """Bounded schema-v1/v2 migration helper for historical naive stamps."""
    parsed = parse_utc(value)
    if parsed is not None:
        return parsed
    if not value or not isinstance(value, str):
        return None
    try:
        candidate = datetime.fromisoformat(value)
    except ValueError:
        return None
    if candidate.tzinfo is not None:
        return candidate.astimezone(timezone.utc)
    return candidate.replace(tzinfo=timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt is not None else None


def _newer(left, right):
    """Return the newer strictly valid UTC timestamp, retaining left on ties."""
    left_dt, right_dt = parse_utc(left), parse_utc(right)
    if right_dt is None:
        return left
    if left_dt is None or right_dt > left_dt:
        return right
    return left


def _threshold_token(value):
    if value is None:
        raise ValueError("prop identity is missing threshold/needs")
    try:
        token = format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid prop threshold {value!r}") from None
    return token.rstrip("0").rstrip(".") if "." in token else token


def market_side_token(row):
    """Return an explicit identity-bearing side for every supported wager."""
    stat = (row.get("projection") or {}).get("stat") or row.get("stat")
    if stat in _FIXED_HALF_RUN_STATS:
        lean = str(row.get("lean") or "").strip().lower()
        if lean in ("nrfi", "yrfi"):
            return lean
    for key in ("bet_side", "market_side", "direction"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value.replace(" ", "_")
    if str(row.get("prop") or "").strip().lower().startswith("under "):
        return "under"
    return "over"


def _subject_identity(row, stat):
    # Game-level markets are keyed by the game itself even when the legacy
    # candidate carries a synthetic player_id solely for old grader plumbing.
    if stat in _GAME_LEVEL_STATS:
        return ("game",)
    combo = [str(value) for value in (row.get("combo_player_ids") or ())]
    if combo:
        if not all(value and value != "None" for value in combo):
            raise ValueError("combo identity contains an empty participant id")
        if stat in _COMMUTATIVE_COMBO_STATS:
            if len(combo) != 2 or len(set(combo)) != 2:
                raise ValueError("combined-strikeout identity requires two distinct starters")
            combo = sorted(combo)
        return ("combo", *combo)
    if row.get("player_id") is not None:
        return ("player", str(row.get("player_id")))
    raise ValueError("prop has no stable player/combo/game-level subject")


def prop_identity_key(row):
    """Canonical settlement identity, independent of display name and order."""
    game_pk = row.get("game_pk")
    if game_pk is None or str(game_pk).strip() == "":
        raise ValueError("prop identity is missing game_pk")
    projection = row.get("projection") or {}
    stat = str(projection.get("stat") or row.get("stat") or "").strip()
    if not stat:
        raise ValueError("prop identity is missing stat/market")
    needs_value = projection.get("needs")
    if needs_value is None and stat in _FIXED_HALF_RUN_STATS:
        needs_value = 0.5
    needs = _threshold_token(needs_value)
    subject = _subject_identity(row, stat)
    return (str(game_pk), subject, stat, needs, market_side_token(row))


def canonical_prop_id(row):
    """Return the v2 stable id for the row's complete settlement identity."""
    game_pk, subject, stat, needs, side = prop_identity_key(row)
    if subject[0] == "combo":
        subject_token = "combo-" + "+".join(subject[1:])
    elif subject[0] == "player":
        subject_token = "player-" + subject[1]
    else:
        subject_token = "game"
    parts = ("fc2", game_pk, subject_token, stat, needs, side)
    return ":".join(quote(str(part), safe="+-_.") for part in parts)


def stable_prop_id(row):
    """Validate and return a supplied id matching the v2 row identity."""
    prop_id = row.get("id")
    if not isinstance(prop_id, str) or not prop_id.strip():
        raise ValueError("dashboard prop is missing its canonical id")
    expected = canonical_prop_id(row)
    version = row.get("identity_version")
    if version == IDENTITY_SCHEMA_VERSION or prop_id.startswith("fc2:"):
        if prop_id != expected:
            raise ValueError(
                f"dashboard prop id {prop_id!r} conflicts with canonical identity {expected!r}"
            )
        return prop_id
    raise ValueError(f"dashboard prop {prop_id!r} uses unsupported identity version {version!r}")


def validate_payload_identities(payload):
    props = payload.get("props")
    if not isinstance(props, list):
        raise ValueError("payload props must be a list")
    ids, identities = set(), set()
    for row in props:
        if not isinstance(row, dict):
            raise ValueError("payload prop must be an object")
        prop_id = stable_prop_id(row)
        identity = prop_identity_key(row)
        if prop_id in ids:
            raise ValueError(f"duplicate canonical prop id: {prop_id}")
        if identity in identities:
            raise ValueError(f"duplicate settlement identity: {identity!r}")
        ids.add(prop_id)
        identities.add(identity)
    return {"ids": ids, "identities": identities}


def game_state(status, row=None, now=None):
    """Map known MLB status values without treating unknown as live evidence."""
    status = status or {}
    abstract = str(status.get("abstractGameState") or "").strip().lower()
    coded = str(status.get("codedGameState") or "").strip().upper()
    detailed = str(status.get("detailedState") or "").strip().lower()
    combined = f"{abstract} {detailed}"
    if "cancelled" in combined or "canceled" in combined or coded == "C":
        return "cancelled"
    if "postponed" in combined:
        return "postponed"
    if "suspended" in combined:
        return "suspended"
    if "delay" in combined or coded == "D":
        return "delayed"
    if coded in ("F", "O") or "final" in detailed or "completed" in detailed or "game over" in detailed:
        return "final"
    if abstract == "live" or coded in ("I", "M") or detailed in (
        "in progress", "manager challenge", "review",
    ):
        return "live"
    if abstract == "preview" and detailed in (
        "", "scheduled", "pre-game", "pregame", "warmup", "warm-up",
    ):
        return "pregame"
    return "unknown"


def game_phase(status, row=None, now=None):
    """Compatibility name for callers; returns the full structured state."""
    return game_state(status, row=row, now=now)


def before_betting_cutoff(row, now=None):
    """True only strictly before the canonical scheduled first pitch."""
    start = parse_utc(row.get("game_start"))
    now_dt = parse_utc(now) if isinstance(now, str) else now
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    return start is not None and now_dt < start


def can_publish_new_top_pick(row, status, now=None):
    return (
        row.get("recommendation_status") == "top_pick"
        and game_state(status, row=row, now=now) == "pregame"
        and before_betting_cutoff(row, now)
    )


def default_live_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "updated_at": None,
        "prices_updated_at": None,
        "grades_updated_at": None,
        # Heartbeats, distinct from the *_updated_at triplet above. The
        # updated_at fields only advance when a channel actually WROTE a
        # changed fact -- exactly the semantics test_lifecycle_contract_v3.py/
        # test_state_races.py lock in, so they must not change meaning here.
        # A live game that simply hasn't produced a new fact yet (a long
        # scoreless stretch) would otherwise look identical, from the
        # timestamp alone, to a scheduler that silently stopped running.
        # *_checked_at instead advances every time the channel completes a
        # real attempt against a live upstream source, whether or not
        # anything changed -- see touch_heartbeat(). The freshness contract
        # (2026-08-19 Live Integrity PR 1) is built on these, not on
        # *_updated_at, precisely because "nothing changed" and "nobody is
        # watching" must never be indistinguishable to a viewer.
        "grades_checked_at": None,
        "prices_checked_at": None,
        "props": {},
    }


def carries_durable_state(delta):
    """True if a live overlay delta records a settlement or publication fact.

    Settlement (what is known about a wager's outcome) and publication (proof
    it actually reached a public deployment) are the only facts a live
    overlay entry can hold that this repository does not already regenerate
    from scratch every cycle -- market/price fields are re-fetched fresh,
    game-progress fields are re-derived from the live MLB feed, and
    recommendation_status is recomputed pregame. Anything durable is exactly
    SETTLEMENT_FIELDS union PUBLICATION_FIELDS; nothing else in a delta can
    represent state this repository cannot simply recompute."""
    if not isinstance(delta, dict):
        return False
    return bool(DURABLE_FIELDS.intersection(delta))


def _migrate_legacy_live(live):
    """One-way in-memory migration for pre-v3 overlays.

    A legacy early ``hit`` cannot prove final authority, so it becomes a
    provisional hit. Legacy miss/void were emitted only on a Final path and
    retain official-final authority. Compatibility is accepted only when the
    input schema is absent/v1/v2; v3 state must already be strict.
    """
    migrated = default_live_state()
    for key in ("updated_at", "prices_updated_at", "grades_updated_at"):
        migrated[key] = _iso(parse_legacy_utc(live.get(key)))
    for prop_id, old in (live.get("props") or {}).items():
        if not isinstance(old, dict):
            raise RuntimeError(f"legacy live delta {prop_id!r} is not an object")
        delta = {k: copy.deepcopy(v) for k, v in old.items()
                 if k not in ("grade", "lifecycle_state", "_field_updated_at")}
        field_times = {}
        for field, stamp in (old.get("_field_updated_at") or {}).items():
            parsed = parse_legacy_utc(stamp)
            if parsed is not None:
                field_times[field] = _iso(parsed)
        state = old.get("lifecycle_state") or old.get("grade")
        stamp = (_iso(parse_legacy_utc((old.get("_field_updated_at") or {}).get("lifecycle_state")))
                 or migrated.get("grades_updated_at") or migrated.get("updated_at"))
        if state in ("pregame", "live") and stamp:
            delta.update({
                "game_state": state, "game_state_observed_at": stamp,
                "game_state_source": "legacy_schema_v2",
            })
        elif state == "hit" and stamp:
            delta.update({
                "settlement_state": "provisional_hit",
                "settlement_authority": "live_observation",
                "settlement_observed_at": stamp,
                "settlement_source": "legacy_schema_v2",
            })
        elif state in ("miss", "void", "ungraded") and stamp:
            delta.update({
                "settlement_state": state,
                "settlement_authority": "official_final",
                "settlement_observed_at": stamp,
                "settlement_source": "legacy_schema_v2_final_path",
            })
        delta["_field_updated_at"] = field_times
        migrated["props"][prop_id] = delta
    return migrated


def migrate_legacy_live(live):
    """Explicit bounded migration entry point for the rollout artifact."""
    version = (live or {}).get("schema_version")
    if version not in (None, 1, 2):
        raise ValueError(f"only legacy schema v1/v2 may use migration, got {version!r}")
    return _migrate_legacy_live(live or {"props": {}})


def validate_live_state(live, strict_ids=False):
    if not isinstance(live, dict) or not isinstance(live.get("props"), dict):
        raise ValueError("live state must be an object with a props object")
    if live.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported live schema version {live.get('schema_version')!r}")
    if live.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
        raise ValueError("unsupported live identity schema version")
    for key in ("updated_at", "prices_updated_at", "grades_updated_at",
                "grades_checked_at", "prices_checked_at"):
        if live.get(key) is not None and parse_utc(live[key]) is None:
            raise ValueError(f"live state has invalid UTC timestamp {key}={live[key]!r}")
    for prop_id, delta in live["props"].items():
        if not isinstance(prop_id, str) or not prop_id:
            raise ValueError("live state contains an empty/non-string id")
        if strict_ids and not prop_id.startswith("fc2:"):
            raise ValueError(f"live state contains a legacy id: {prop_id}")
        if not isinstance(delta, dict):
            raise ValueError(f"live delta {prop_id!r} is not an object")
        if "game_state" in delta:
            _validate_game_fact(delta)
        if "settlement_state" in delta:
            _validate_settlement_fact(delta)
        for stamp in (delta.get("_field_updated_at") or {}).values():
            if parse_utc(stamp) is None:
                raise ValueError(f"live delta {prop_id!r} has an invalid field timestamp")
    return True


def load_live_state(path):
    """Load state; missing is empty, unreadable/invalid is a hard failure."""
    if not os.path.exists(path):
        return default_live_state()
    try:
        with open(path, encoding="utf-8") as handle:
            live = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"live state is unreadable: {path}: {exc}") from exc
    if not isinstance(live, dict) or not isinstance(live.get("props", {}), dict):
        raise RuntimeError(f"live state has an invalid schema: {path}")
    version = live.get("schema_version")
    if version in (None, 1, 2):
        live = _migrate_legacy_live(live)
    try:
        validate_live_state(live)
    except ValueError as exc:
        raise RuntimeError(f"live state has an invalid schema: {path}: {exc}") from exc
    return live


def atomic_write_json(path, value, *, indent=None):
    """fsync a temporary file and atomically replace only after success."""
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=None if indent else (",", ":"), indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _fallback_field_timestamp(live, field):
    if field in SETTLEMENT_FIELDS or field in GAME_FIELDS:
        return live.get("grades_updated_at")
    if field in PRICE_FIELDS:
        return live.get("prices_updated_at")
    return live.get("updated_at")


def _field_timestamp(live, delta, field):
    return (delta.get("_field_updated_at") or {}).get(field) or _fallback_field_timestamp(live, field)


def _validate_game_fact(fact):
    state = fact.get("game_state")
    if state not in GAME_STATES:
        raise ValueError(f"invalid game state {state!r}")
    if parse_utc(fact.get("game_state_observed_at")) is None:
        raise ValueError("game fact requires a strict UTC observed_at")
    if not isinstance(fact.get("game_state_source"), str) or not fact.get("game_state_source"):
        raise ValueError("game fact requires a source")


def _validate_settlement_fact(fact):
    state = fact.get("settlement_state")
    authority = fact.get("settlement_authority")
    if state not in SETTLEMENT_STATES:
        raise ValueError(f"invalid settlement state {state!r}")
    if authority not in RESULT_AUTHORITIES:
        raise ValueError(f"invalid settlement authority {authority!r}")
    if parse_utc(fact.get("settlement_observed_at")) is None:
        raise ValueError("settlement fact requires a strict UTC observed_at")
    if not isinstance(fact.get("settlement_source"), str) or not fact.get("settlement_source"):
        raise ValueError("settlement fact requires a source")
    if state == "provisional_hit" and authority != "live_observation":
        raise ValueError("provisional_hit requires live_observation authority")
    if state in TERMINAL_SETTLEMENT_STATES and authority != "official_final":
        raise ValueError(f"{state} requires official_final authority")
    if authority == "official_final" and state not in (*TERMINAL_SETTLEMENT_STATES, "ungraded"):
        raise ValueError("official_final authority requires a final/void/ungraded settlement")
    if authority == "live_observation" and state not in ("open", "provisional_hit"):
        raise ValueError("live observation cannot authoritatively settle hit/miss/void")
    if authority == "none" and state not in ("open", "ungraded"):
        raise ValueError("authority none is valid only for open/ungraded")
    if state in ("provisional_hit", "hit", "miss") and fact.get("result_actual") is None:
        raise ValueError(f"{state} requires an observed result_actual")


def _settlement_fact(delta):
    return {field: copy.deepcopy(delta.get(field)) for field in SETTLEMENT_FIELDS}


def _game_fact(delta):
    return {field: copy.deepcopy(delta.get(field)) for field in GAME_FIELDS}


def _accept_settlement(current, incoming):
    if not current.get("settlement_state"):
        return True
    current_rank = RESULT_AUTHORITY_RANK[current.get("settlement_authority")]
    incoming_rank = RESULT_AUTHORITY_RANK[incoming.get("settlement_authority")]
    if incoming_rank != current_rank:
        return incoming_rank > current_rank
    current_at = parse_utc(current.get("settlement_observed_at"))
    incoming_at = parse_utc(incoming.get("settlement_observed_at"))
    if incoming_at > current_at:
        return True
    if incoming_at < current_at:
        return False
    return _settlement_fact(current) == _settlement_fact(incoming)


def _apply_atomic_fact(current, fields, fact, field_times, stamp):
    for field in fields:
        if field in fact:
            current[field] = copy.deepcopy(fact[field])
        else:
            current.pop(field, None)
        field_times[field] = stamp


def merge_prop_fields(live, prop_id, changes, updated_at, channel=None):
    """Merge a delta by recency; game/settlement facts are indivisible."""
    if not isinstance(prop_id, str) or not prop_id:
        raise ValueError("live update requires a stable prop id")
    if parse_utc(updated_at) is None:
        raise ValueError(f"live update timestamp is not valid strict UTC ISO-8601: {updated_at!r}")
    if not isinstance(changes, dict):
        raise ValueError("live changes must be an object")

    live["schema_version"] = SCHEMA_VERSION
    live["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
    props = live.setdefault("props", {})
    current = props.setdefault(prop_id, {})
    field_times = current.setdefault("_field_updated_at", {})

    settlement_keys = SETTLEMENT_FIELDS.intersection(changes)
    if settlement_keys:
        required = {"settlement_state", "settlement_authority", "settlement_observed_at", "settlement_source"}
        if not required.issubset(changes):
            raise ValueError("settlement metadata must update as one complete fact")
        incoming = _settlement_fact(changes)
        _validate_settlement_fact(incoming)
        if _accept_settlement(current, incoming):
            _apply_atomic_fact(current, SETTLEMENT_FIELDS, incoming, field_times,
                               incoming["settlement_observed_at"])

    game_keys = GAME_FIELDS.intersection(changes)
    if game_keys:
        if not GAME_FIELDS.issubset(changes):
            raise ValueError("game state metadata must update as one complete fact")
        incoming_game = _game_fact(changes)
        _validate_game_fact(incoming_game)
        current_game = current.get("game_state")
        current_at = parse_utc(current.get("game_state_observed_at"))
        incoming_at = parse_utc(incoming_game["game_state_observed_at"])
        accept = current_at is None or incoming_at > current_at
        if incoming_game["game_state"] == "unknown" and current_game not in (None, "unknown"):
            accept = False
        if current_game == "final" and incoming_game["game_state"] != "final":
            accept = False
        if accept or (incoming_at == current_at and _game_fact(current) == incoming_game):
            _apply_atomic_fact(current, GAME_FIELDS, incoming_game, field_times,
                               incoming_game["game_state_observed_at"])

    for field, value in changes.items():
        if field in PROP_META_FIELDS or field in SETTLEMENT_FIELDS or field in GAME_FIELDS:
            continue
        previous_at = _field_timestamp(live, current, field)
        prev_dt = parse_utc(previous_at)
        next_dt = parse_utc(updated_at)
        # Current-main is the authoritative left operand during push retry.
        # Equal timestamps provide no evidence that an incoming conflicting
        # value is newer, so retain the current fact on ties too.
        if prev_dt is not None and (
                next_dt < prev_dt or (next_dt == prev_dt and field in current)):
            continue
        if field == "recommendation_status" and value not in RECOMMENDATION_STATES:
            raise ValueError(f"invalid recommendation state {value!r}")
        if field == "published_top_pick_at" and current.get(field):
            old_dt, new_dt = parse_utc(current[field]), parse_utc(value)
            if old_dt is not None and (new_dt is None or old_dt <= new_dt):
                continue
        current[field] = copy.deepcopy(value)
        field_times[field] = updated_at

    if not field_times:
        current.pop("_field_updated_at", None)
    live["updated_at"] = _newer(live.get("updated_at"), updated_at)
    if channel == "prices":
        live["prices_updated_at"] = _newer(live.get("prices_updated_at"), updated_at)
    elif channel == "grades":
        live["grades_updated_at"] = _newer(live.get("grades_updated_at"), updated_at)
    elif channel is not None:
        raise ValueError(f"unknown live-state channel: {channel!r}")
    return live


def touch_channel(live, channel, updated_at):
    if parse_utc(updated_at) is None:
        raise ValueError(f"live update timestamp is not valid strict UTC ISO-8601: {updated_at!r}")
    live["schema_version"] = SCHEMA_VERSION
    live["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
    live["updated_at"] = _newer(live.get("updated_at"), updated_at)
    if channel == "prices":
        live["prices_updated_at"] = _newer(live.get("prices_updated_at"), updated_at)
    elif channel == "grades":
        live["grades_updated_at"] = _newer(live.get("grades_updated_at"), updated_at)
    else:
        raise ValueError(f"unknown live-state channel: {channel!r}")
    return live


def touch_heartbeat(live, channel, checked_at):
    """Record that a channel completed a real attempt against its live
    upstream source, whether or not that attempt produced a changed fact.

    Deliberately separate from touch_channel(): *_updated_at must keep
    meaning "the last time a fact actually changed" (existing merge/race
    tests depend on that), while *_checked_at means "the last time this
    channel successfully looked." A caller should only call this after a
    genuine check happened -- e.g. refresh_grades.py calls it once real
    game contexts were fetched, never on a total upstream-fetch failure,
    so an unadvanced heartbeat always means "nobody has actually looked
    since then," never "looked and found nothing new" (that case DOES
    advance the heartbeat)."""
    if parse_utc(checked_at) is None:
        raise ValueError(f"live update timestamp is not valid strict UTC ISO-8601: {checked_at!r}")
    live["schema_version"] = SCHEMA_VERSION
    live["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
    if channel == "prices":
        live["prices_checked_at"] = _newer(live.get("prices_checked_at"), checked_at)
    elif channel == "grades":
        live["grades_checked_at"] = _newer(live.get("grades_checked_at"), checked_at)
    else:
        raise ValueError(f"unknown live-state channel: {channel!r}")
    return live


def merge_live_states(base, incoming):
    """Semantic merge: stale full documents cannot overwrite newer facts."""
    merged = copy.deepcopy(base or default_live_state())
    incoming = incoming or default_live_state()
    for prop_id, delta in (incoming.get("props") or {}).items():
        if SETTLEMENT_FIELDS.intersection(delta):
            stamp = delta.get("settlement_observed_at")
            merge_prop_fields(merged, prop_id, _settlement_fact(delta), stamp, channel="grades")
        if GAME_FIELDS.intersection(delta):
            stamp = delta.get("game_state_observed_at")
            merge_prop_fields(merged, prop_id, _game_fact(delta), stamp, channel="grades")
        for field, value in delta.items():
            if field in PROP_META_FIELDS or field in SETTLEMENT_FIELDS or field in GAME_FIELDS:
                continue
            stamp = _field_timestamp(incoming, delta, field)
            if stamp is None:
                continue
            channel = "prices" if field in PRICE_FIELDS else None
            merge_prop_fields(merged, prop_id, {field: value}, stamp, channel=channel)
    for key in ("updated_at", "prices_updated_at", "grades_updated_at",
                "grades_checked_at", "prices_checked_at"):
        merged[key] = _newer(merged.get(key), incoming.get(key))
    validate_live_state(merged)
    return merged


def _overlay_fact(row, delta, fields, accept):
    if accept:
        for field in fields:
            if field in delta:
                row[field] = copy.deepcopy(delta[field])
            else:
                row.pop(field, None)


def apply_live_overlay(payload, live):
    """Apply newer overlay fields without allowing authority regression."""
    effective = copy.deepcopy(payload)
    props = effective.get("props") or []
    by_id = {}
    for row in props:
        prop_id = row.get("id")
        if prop_id in by_id:
            raise ValueError(f"duplicate prop id while applying live overlay: {prop_id}")
        if prop_id:
            by_id[prop_id] = row
    board_odds_at = parse_utc(effective.get("odds_fetched_at") or effective.get("generated_at"))
    for prop_id, delta in (live.get("props") or {}).items():
        row = by_id.get(prop_id)
        if row is None:
            continue
        if "settlement_state" in delta:
            incoming = _settlement_fact(delta)
            _validate_settlement_fact(incoming)
            _overlay_fact(row, incoming, SETTLEMENT_FIELDS, _accept_settlement(row, incoming))
        if "game_state" in delta:
            incoming_game = _game_fact(delta)
            _validate_game_fact(incoming_game)
            existing_at = parse_utc(row.get("game_state_observed_at"))
            incoming_at = parse_utc(incoming_game["game_state_observed_at"])
            accept = existing_at is None or incoming_at >= existing_at
            if incoming_game["game_state"] == "unknown" and row.get("game_state") not in (None, "unknown"):
                accept = False
            if row.get("game_state") == "final" and incoming_game["game_state"] != "final":
                accept = False
            _overlay_fact(row, incoming_game, GAME_FIELDS, accept)
        for field, value in delta.items():
            if field in PROP_META_FIELDS or field in SETTLEMENT_FIELDS or field in GAME_FIELDS:
                continue
            if field in PRICE_FIELDS and board_odds_at is not None:
                field_at = parse_utc(_field_timestamp(live, delta, field))
                if field_at is not None and field_at < board_odds_at:
                    continue
            row[field] = copy.deepcopy(value)
    effective["prices_updated_at"] = live.get("prices_updated_at")
    effective["grades_updated_at"] = live.get("grades_updated_at")
    return effective


def is_published_top_pick(row):
    """True only for deployment-proven exposure."""
    return bool(
        row.get("published_top_pick_at")
        and row.get("publication_artifact_id")
        and parse_utc(row.get("published_top_pick_at")) is not None
    )


def compact_live_state(live, *, current_ids, published_ids, durable_settlements,
                       protected_game_states=None):
    """Remove only terminal facts already durably represented elsewhere."""
    protected_game_states = protected_game_states or {}
    compacted = copy.deepcopy(live)
    for prop_id, delta in list((compacted.get("props") or {}).items()):
        if prop_id in current_ids:
            continue
        state = delta.get("settlement_state")
        if state not in TERMINAL_SETTLEMENT_STATES:
            continue
        if delta.get("settlement_authority") != "official_final":
            continue
        if protected_game_states.get(prop_id) in ("suspended", "postponed"):
            continue
        if prop_id not in published_ids:
            continue
        durable = durable_settlements.get(prop_id)
        if not durable or durable[0] != state:
            continue
        durable_at = parse_utc(durable[1])
        live_at = parse_utc(delta.get("settlement_observed_at"))
        if durable_at is None or live_at is None or durable_at < live_at:
            continue
        del compacted["props"][prop_id]
    validate_live_state(compacted)
    return compacted


def state_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()
