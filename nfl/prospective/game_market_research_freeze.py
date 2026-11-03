"""Immutable pre-kickoff research freeze for NFL spread/total/moneyline offers.

One record per event per freeze run. It binds, by content hash:
- the sealed FanDuel market snapshot (``game_market_snapshot``),
- the nflverse identity binding (``game_identity``) incl. ESPN id,
- the strictly-prior B0 input and schedule-source digest,
- the frozen model parameters (``game_market_v1_params.json``) and code SHA,
- every offered side's line/price/observation time, model probabilities under
  B0/F1/M0/C1, no-vig/break-even/edge/EV, and an explicit decision + reasons,
- a grading identity that the grader must match exactly.

Records are written create-only and verified by recomputing their hash. Nothing
here is public or a pick. A freeze at or after kickoff is refused outright.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from nfl.prospective.game_market_pricing import (
    LEAN_CONFIG,
    MAX_QUOTE_AGE_MINUTES,
    GameMarketPricingError,
    evaluate_price,
    implied_probability,
    lean_decision,
    no_vig_pair,
    american_to_decimal,
)
from nfl.prospective.game_market_snapshot import verify_game_market_snapshot
from nfl.research.game_market_models import (
    CONFIGS,
    GameMarketParams,
    centers,
    distribution,
    params_sha256,
)

SCHEMA = "fc-nfl-004-game-market-research-freeze-v1"
SCHEMA_V2 = "fc-nfl-004-game-market-research-freeze-v2"  # adds the slate discovery binding
DISCOVERY_KEYS = ("root_sha256", "root_bytes", "root_observed_at", "root_url",
                  "process_event_ids", "process_event_ids_sha256", "discovery_sha256")
FAMILIES = ("spread", "game_total", "moneyline")
SIDES = {"spread": ("HOME", "AWAY"), "game_total": ("OVER", "UNDER"), "moneyline": ("HOME", "AWAY")}


class GameMarketFreezeError(ValueError):
    """Raised when a research freeze would be unsafe or ambiguous."""


def _dt(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GameMarketFreezeError(f"{field} must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GameMarketFreezeError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GameMarketFreezeError(f"{field} must be timezone-aware")
    return parsed


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _round(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(float(value), places)


def _qb_reasons(qb_state: Mapping[str, Any]) -> list[str]:
    reasons = set()
    for side in ("home", "away"):
        state = (qb_state or {}).get(side) or {}
        status = state.get("status")
        if status == "CHANGED":
            reasons.add("QB_CHANGE_UNMODELLED")
        elif status != "UNCHANGED":
            reasons.add("QB_STATE_UNKNOWN")
    return sorted(reasons)


def _side_view(family: str, market: Mapping[str, Any], side: str) -> dict[str, Any]:
    prefix = side.lower()
    line = None
    if family == "spread":
        line = market.get(f"{prefix}_line")
    elif family == "game_total":
        line = market.get("line")
    return {
        "line": line,
        "odds": market.get(f"{prefix}_odds"),
        "selection_id": market.get(f"{prefix}_selection_id"),
    }


def _outcome(dist, family: str, side: str, line: float | None):
    if family == "spread":
        return dist.spread(side, line)
    if family == "game_total":
        return dist.total(side, line)
    return dist.moneyline(side)


def build_research_freeze(
    *,
    market_snapshot: Mapping[str, Any],
    identity_binding: Mapping[str, Any] | None,
    b0_prediction: Mapping[str, Any],
    qb_state: Mapping[str, Any],
    params: GameMarketParams,
    code_sha: str,
    schedule_source: Mapping[str, Any],
    frozen_at: str,
    discovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable research freeze for one sealed event snapshot.

    ``discovery`` (schema v2) binds the record to the archived FanDuel root
    response that discovered this event; the event must be in its window.
    """
    snapshot_sha = verify_game_market_snapshot(market_snapshot)
    event_id = str(market_snapshot["event_id"])
    frozen_dt = _dt(frozen_at, "frozen_at")
    if _dt(market_snapshot.get("sealed_at"), "snapshot.sealed_at") > frozen_dt:
        raise GameMarketFreezeError("freeze cannot precede its market snapshot seal")
    if not isinstance(code_sha, str) or not code_sha.strip() or code_sha.strip().upper() == "UNKNOWN":
        raise GameMarketFreezeError("code_sha must identify the exact code")

    gate: set[str] = set()
    kickoff = None
    if identity_binding is None:
        gate.add("IDENTITY_UNBOUND")
    else:
        if str(identity_binding.get("sportsbook_event_id")) != event_id:
            raise GameMarketFreezeError("identity binding is for a different sportsbook event")
        if identity_binding.get("sportsbook_snapshot_sha256") != snapshot_sha:
            raise GameMarketFreezeError("identity binding is for a different snapshot")
        body = {k: v for k, v in identity_binding.items() if k != "binding_sha256"}
        if canonical_sha256(body) != identity_binding.get("binding_sha256"):
            raise GameMarketFreezeError("identity binding hash mismatch")
        kickoff = _dt(identity_binding.get("scheduled_kickoff"), "binding.scheduled_kickoff")
        if frozen_dt >= kickoff:
            raise GameMarketFreezeError("POST_KICKOFF_FREEZE: research freeze must precede kickoff")
        if str(b0_prediction.get("game_id")) != identity_binding.get("nflverse_game_id"):
            raise GameMarketFreezeError("B0 prediction game_id does not match identity binding")
        if (str(b0_prediction.get("home_team")), str(b0_prediction.get("away_team"))) != (
            identity_binding.get("home_team"), identity_binding.get("away_team")
        ):
            raise GameMarketFreezeError("B0 prediction teams do not match identity binding")
    gate.update(_qb_reasons(qb_state))

    markets: dict[str, dict] = {}
    for raw in market_snapshot.get("records") or []:
        markets[str(raw["market"]).lower()] = deepcopy(dict(raw))
    for family, market in markets.items():
        captured = _dt(market.get("captured_at"), f"{family}.captured_at")
        if kickoff is not None and captured >= kickoff:
            raise GameMarketFreezeError(f"{family} quote observed at/after kickoff")

    b0_ok = b0_prediction.get("eligibility") == "ELIGIBLE"
    b0m = b0_prediction.get("predicted_home_margin") if b0_ok else None
    b0t = b0_prediction.get("predicted_total") if b0_ok else None
    spread = markets.get("spread")
    total = markets.get("game_total")
    line_margin = None if spread is None or spread.get("home_line") is None else -float(spread["home_line"])
    line_total = None if total is None or total.get("line") is None else float(total["line"])

    dists: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    for config in CONFIGS:
        cm, ct = centers(config, params, b0_margin=b0m, b0_total=b0t,
                         line_margin=line_margin, line_total=line_total)
        if cm is None and ct is None:
            dists[config] = None
            summaries[config] = {"available": False}
            continue
        dist = distribution(config, params, cm, ct)
        dists[config] = dist
        summaries[config] = {
            "available": True,
            "margin_center": _round(cm), "total_center": _round(ct),
            "expected_home_margin": _round(dist.expected_margin) if cm is not None else None,
            "expected_total": _round(dist.expected_total) if ct is not None else None,
            "fair_home_spread": dist.fair_home_spread() if cm is not None else None,
            "fair_total": dist.fair_total() if ct is not None else None,
            "fair_home_win_probability": _round(dist.fair_home_win_probability()) if cm is not None else None,
        }

    selections: list[dict[str, Any]] = []
    for family in FAMILIES:
        market = markets.get(family)
        if market is None:
            selections.append({
                "market": family, "decision": "QUARANTINED",
                "reasons": sorted(gate | {"MARKET_NOT_NORMALIZED"}),
            })
            continue
        captured = _dt(market["captured_at"], f"{family}.captured_at")
        age_min = (frozen_dt - captured).total_seconds() / 60.0
        market_gate = set(gate)
        if age_min < 0:
            market_gate.add("QUOTE_AFTER_FREEZE")
        elif age_min > MAX_QUOTE_AGE_MINUTES:
            market_gate.add("STALE_QUOTE")
        a, b = SIDES[family]
        views = {s: _side_view(family, market, s) for s in (a, b)}
        try:
            nv = no_vig_pair(views[a]["odds"], views[b]["odds"])
            no_vig = {a: nv[0], b: nv[1]}
            overround = nv[2]
        except GameMarketPricingError:
            market_gate.add("ONE_SIDED_MARKET")
            no_vig, overround = {}, None
        if family == "spread" and None not in (views[a]["line"], views[b]["line"]):
            if not math.isclose(float(views[a]["line"]) + float(views[b]["line"]), 0.0, abs_tol=1e-9):
                raise GameMarketFreezeError("spread handicaps must be opposites")
        for side in (a, b):
            v = views[side]
            side_gate = set(market_gate)
            if v["odds"] is None or not v["selection_id"] or (family != "moneyline" and v["line"] is None):
                side_gate.add("MISSING_PRICE")
            models: dict[str, Any] = {}
            if "MISSING_PRICE" not in side_gate:
                for config, dist in dists.items():
                    try:
                        line = None if v["line"] is None else float(v["line"])
                        out = _outcome(dist, family, side, line) if dist is not None else None
                    except Exception:  # noqa: BLE001 - absent marginal => unavailable
                        out = None
                    models[config] = (
                        None if out is None
                        else {k: _round(x) for k, x in evaluate_price(out, v["odds"]).items()}
                    )
            lean = models.get(LEAN_CONFIG)
            gap = None
            c1 = summaries.get(LEAN_CONFIG) or {}
            if family == "spread" and c1.get("margin_center") is not None and line_margin is not None:
                gap = abs(c1["margin_center"] - line_margin)
            elif family == "game_total" and c1.get("total_center") is not None and line_total is not None:
                gap = abs(c1["total_center"] - line_total)
            decision, reasons = lean_decision(
                market=family, gate_reasons=side_gate, lean_eval=lean, model_market_gap=gap)
            identity = {
                "event_id": event_id, "market_id": str(market["market_id"]), "market": family,
                "side": side, "selection_id": v["selection_id"], "line": v["line"],
                "american_odds": v["odds"], "captured_at": market["captured_at"],
            }
            selections.append({
                "market": family,
                "selection_key": "fcnfl004:" + canonical_sha256(identity)[:32],
                "grading_identity": {
                    **identity,
                    "nflverse_game_id": (identity_binding or {}).get("nflverse_game_id"),
                    "espn_event_id": (identity_binding or {}).get("nflverse_espn"),
                },
                "sportsbook": market.get("sportsbook"),
                "source_payload_sha256": market.get("source_payload_sha256"),
                "quote_age_minutes": round(age_min, 3),
                "observation_basis": "CAPTURE_OBSERVED_AT (book quote-origin time unavailable)",
                "decimal_odds": None if v["odds"] is None else round(american_to_decimal(v["odds"]), 6),
                "implied_probability": None if v["odds"] is None else _round(implied_probability(v["odds"])),
                "no_vig_probability": _round(no_vig.get(side)) if no_vig else None,
                "overround": _round(overround),
                "model_market_gap_points": _round(gap),
                "models": models,
                "lean_config": LEAN_CONFIG,
                "decision": decision,
                "reasons": reasons,
            })

    keys = [s["selection_key"] for s in selections if "selection_key" in s]
    if len(keys) != len(set(keys)):
        raise GameMarketFreezeError("duplicate selection identity within freeze")

    body = {
        "schema": SCHEMA,
        "sport": "NFL",
        "evidence_class": "PROSPECTIVE_RESEARCH_FREEZE",
        "research_only": True,
        "public_eligible": False,
        "event_id": event_id,
        "frozen_at": frozen_at,
        "scheduled_kickoff": None if kickoff is None else kickoff.isoformat(),
        "market_snapshot_sha256": snapshot_sha,
        "market_snapshot": deepcopy(dict(market_snapshot)),
        "identity_binding": deepcopy(dict(identity_binding)) if identity_binding else None,
        "input_state": {
            "b0_prediction": deepcopy(dict(b0_prediction)),
            "qb_state": deepcopy(dict(qb_state or {})),
            "schedule_source": deepcopy(dict(schedule_source)),
        },
        "model": {
            "params_sha256": params_sha256(params),
            "params_provenance": dict(params.provenance),
            "configs": list(CONFIGS),
            "lean_config": LEAN_CONFIG,
            "code_sha": code_sha.strip(),
        },
        "config_summaries": summaries,
        "selections": selections,
    }
    if discovery is not None:
        missing = [k for k in DISCOVERY_KEYS if k not in discovery]
        if missing:
            raise GameMarketFreezeError(f"discovery binding missing: {missing}")
        if canonical_sha256(sorted(str(e) for e in discovery["process_event_ids"])) != discovery[
                "process_event_ids_sha256"]:
            raise GameMarketFreezeError("discovery processing event ids do not match their hash")
        if event_id not in [str(e) for e in discovery["process_event_ids"]]:
            raise GameMarketFreezeError("event was not discovered in the bound slate window")
        if _dt(discovery["root_observed_at"], "discovery.root_observed_at") > _dt(
                market_snapshot.get("captured_at"), "snapshot.captured_at"):
            raise GameMarketFreezeError("event payload captured before slate discovery")
        body["schema"] = SCHEMA_V2
        body["discovery"] = {k: discovery[k] for k in DISCOVERY_KEYS}
    body["input_state_sha256"] = canonical_sha256(body["input_state"])
    return {**body, "record_sha256": canonical_sha256(body)}


def verify_research_freeze(record: Mapping[str, Any]) -> str:
    """Recompute the record hash and the embedded snapshot hash."""
    expected = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if canonical_sha256(body) != expected:
        raise GameMarketFreezeError("research freeze hash mismatch (record mutated)")
    if verify_game_market_snapshot(record["market_snapshot"]) != record["market_snapshot_sha256"]:
        raise GameMarketFreezeError("embedded market snapshot mismatch")
    if canonical_sha256(record["input_state"]) != record["input_state_sha256"]:
        raise GameMarketFreezeError("input state hash mismatch")
    return expected


def freeze_path(root: Path, record: Mapping[str, Any]) -> Path:
    binding = record.get("identity_binding") or {}
    season = binding.get("season") or "unbound"
    week = binding.get("week") or "unbound"
    stamp = _dt(record["frozen_at"], "frozen_at").strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}_{record['record_sha256'][:12]}.json"
    return Path(root) / str(season) / f"week_{week}" / f"event_{record['event_id']}" / name


def write_research_freeze(root: Path, record: Mapping[str, Any]) -> Path:
    """Create-only write. An existing path is never overwritten."""
    verify_research_freeze(record)
    path = freeze_path(root, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(payload)
    except FileExistsError as exc:
        raise GameMarketFreezeError(f"frozen record already exists: {path}") from exc
    return path


def load_research_freeze(path: Path) -> dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_research_freeze(record)
    return record


def qb_state_from_schedule(schedule_rows: Sequence[Mapping[str, Any]], game_id: str) -> dict[str, Any]:
    """Expected starter (nflverse pre-populated) vs the team's previous started game.

    UNKNOWN when either is missing. This is a gate input, not a model feature.
    """
    target = next((r for r in schedule_rows if r.get("game_id") == game_id), None)
    if target is None:
        return {"home": {"status": "UNKNOWN"}, "away": {"status": "UNKNOWN"}}
    key = (int(target["season"]), int(target["week"]))
    out = {}
    for side in ("home", "away"):
        team = target[f"{side}_team"]
        expected = (target.get(f"{side}_qb_id") or "").strip() or None
        prev_row, prev_key = None, None
        for r in schedule_rows:
            if str(r.get("game_type") or "").upper() != "REG" or not str(r.get("home_score") or "").strip():
                continue
            try:
                rk = (int(r["season"]), int(r["week"]))
            except (TypeError, ValueError):
                continue
            if rk >= key or team not in (r.get("home_team"), r.get("away_team")):
                continue
            if prev_key is None or rk > prev_key:
                prev_row, prev_key = r, rk
        previous = None
        if prev_row is not None:
            role = "home" if prev_row["home_team"] == team else "away"
            previous = (prev_row.get(f"{role}_qb_id") or "").strip() or None
        status = "UNKNOWN" if expected is None or previous is None else (
            "UNCHANGED" if expected == previous else "CHANGED")
        out[side] = {
            "team": team, "expected_qb_id": expected,
            "expected_qb_name": (target.get(f"{side}_qb_name") or "").strip() or None,
            "previous_game_id": None if prev_row is None else prev_row["game_id"],
            "previous_qb_id": previous, "status": status,
            "basis": "NFLVERSE_PREPOPULATED_EXPECTED_STARTER_UNTIMESTAMPED",
        }
    return out
