"""Research-only connection from bound, captured receptions offers to one PMF.

No selector, workflow, model promotion or source acquisition lives here. The PMF
is conditional on playing; DNP is an outcome-layer void, not zero receptions.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from nfl.research.alternate_line_evaluation import breakeven_probability
from nfl.research.receptions_frozen_challenger import FROZEN_NB_FIT
from nfl.research.receptions_outcome_distribution import negative_binomial_pmf

VERSION = "PRICE_AWARE_RECEPTIONS_V1"


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() in {"None", "null"}:
        raise ValueError("missing text/identity")
    return value.strip()


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("nonfinite or nonnumeric value")
    return float(value)


def _time(value: Any) -> datetime:
    raw = _text(value)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _digest(value: Any) -> str:
    value = _text(value)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid SHA-256")
    return value


def frozen_distribution(*, projection: float, event_id: str, gsis_id: str,
                        canonical_game_id: str,
                        feature_cutoff: str, source_available_at: str,
                        generated_at: str, history_sha256: str) -> dict:
    """Use frozen NB; feature_cutoff is the prediction information cutoff."""
    mu = _number(projection)
    if not 0 < mu <= 60:
        raise ValueError("projection outside supported receptions domain")
    result = {
        "model_version": "NEGATIVE_BINOMIAL_POOLED_V1",
        "alpha": FROZEN_NB_FIT["alpha"], "projection": mu,
        "event_id": _text(event_id), "gsis_id": _text(gsis_id),
        "canonical_game_id": _text(canonical_game_id),
        "feature_cutoff": feature_cutoff, "source_available_at": source_available_at,
        "generated_at": generated_at, "history_sha256": _digest(history_sha256),
        "pmf": [negative_binomial_pmf(k, mu, FROZEN_NB_FIT["alpha"], eps=0.0) for k in range(513)],
        "conditioning": "PLAYED", "evidence_status": "RESEARCH_ONLY_NOT_PROMOTED",
    }
    _validate_distribution(result)
    return result


def _validate_distribution(dist: dict) -> None:
    for field in ("event_id", "gsis_id", "model_version"):
        _text(dist[field])
    _digest(dist["history_sha256"])
    if not (_time(dist["source_available_at"]) <= _time(dist["feature_cutoff"])
            <= _time(dist["generated_at"])):
        raise ValueError("future history or unavailable source")
    if dist.get("conditioning") != "PLAYED":
        raise ValueError("distribution must be conditional on playing")
    pmf = dist["pmf"]
    if not isinstance(pmf, list) or not pmf:
        raise ValueError("empty PMF")
    if any(_number(p) < 0 for p in pmf) or not math.isclose(math.fsum(pmf), 1, abs_tol=1e-10, rel_tol=0):
        raise ValueError("PMF must be finite nonnegative and normalized; no tail folding")


def _quote_evidence_valid(candidate: dict, raw_source_sha256: str, captured: datetime) -> bool:
    """A caller-provided timestamp alone is never proof of quote origin.

    The raw-byte verifier in the capture/integration layer must additionally
    compare source_field to the actual market payload before this can be used
    outside a contract test. Current FanDuel responses have no such field.
    """
    evidence = candidate.get("quote_evidence")
    if not isinstance(evidence, dict) or candidate.get("quote_timestamp_status") != "SOURCE_FIELD_VERIFIED":
        return False
    try:
        if (evidence.get("source_sha256") != raw_source_sha256 or
                evidence.get("market_id") != candidate["market_id"] or
                evidence.get("timestamp") != candidate["quote_timestamp"] or
                not _text(evidence.get("source_field"))):
            return False
        return _time(evidence["timestamp"]) <= captured
    except (KeyError, TypeError, ValueError):
        return False


def _role_evidence_valid(candidate: dict, captured: datetime) -> bool:
    # No trusted role-source verifier is connected to this research evaluator.
    # Metadata supplied inside a candidate can describe evidence, but cannot
    # authenticate it. Keep the gate closed until raw source bytes are checked
    # outside the candidate and bound to this player, team, game and cutoff.
    return False


def _rule_evidence_valid(candidate: dict, now: datetime) -> bool:
    # A URL, formatted digest, and caller-declared jurisdiction do not prove
    # the sportsbook's applicable settlement terms. No verified rule-source
    # adapter exists yet, so a self-certified record must never clear this gate.
    return False


def evaluate_offer(candidate: dict, distribution: dict, *, as_of: str,
                   raw_source_sha256: str, max_age_seconds: int = 900) -> dict:
    """Return sealed research evidence, or explicit NO_PLAY with untouched inputs.

    Input is the existing player_prop_markets normalizer followed by the GSIS
    binder. Caller must preserve availability_status; absent coverage quarantines.
    Alternate N+ is X>=N (zero push), unlike primary over N (push at N).
    """
    result = {"schema_version": VERSION, "candidate": deepcopy(candidate),
              "distribution": deepcopy(distribution), "evaluated_at": as_of,
              "source_sha256": raw_source_sha256, "evidence_status": "RESEARCH_ONLY_NOT_PROMOTED",
              "decision_status": "NO_PLAY", "reasons": [], "prices": [],
              "bettable": False, "uncertainty": "POINT_MODEL_UNVALIDATED; NO_CONFIDENCE_INTERVAL",
              "settlement_support": "RESEARCH_COUNT_ONLY; BOOK_ACTION_RULES_NOT_CERTIFIED"}
    try:
        _validate_distribution(distribution)
        _digest(raw_source_sha256)
        now, captured, kickoff = map(_time, (as_of, candidate["captured_at"], candidate["event_open_date"]))
        if type(max_age_seconds) is not int or max_age_seconds <= 0:
            raise ValueError("invalid freshness policy")
        if not captured <= now < kickoff or (now-captured).total_seconds() > max_age_seconds:
            raise ValueError("stale, future or post-kickoff offer")
        if not _time(distribution["generated_at"]) <= now:
            raise ValueError("future model")
        if candidate.get("market_availability") != "AVAILABLE":
            raise ValueError("market not confirmed AVAILABLE")
        if candidate.get("quote_timestamp") is not None:
            quote = _time(candidate["quote_timestamp"])
            if quote > captured or (now - quote).total_seconds() > max_age_seconds:
                raise ValueError("stale or future source quote")
        _text(candidate["canonical_game_id"])
        if distribution.get("canonical_game_id") != candidate["canonical_game_id"]:
            raise ValueError("canonical game mismatch")
        if now >= _time(candidate["canonical_kickoff"]):
            raise ValueError("canonical kickoff passed")
        if candidate.get("source") != "fanduel_nfl":
            raise ValueError("unsupported bookmaker contract")
        if candidate.get("binding_status") != "BOUND":
            raise ValueError("unbound player")
        for key in ("event_id", "gsis_id"):
            if _text(candidate[key]) != distribution[key]:
                raise ValueError("model/offer identity mismatch")
        _text(candidate["market_id"])
        market = candidate["market"]
        if market == "receptions" and candidate.get("shape") == "primary":
            line = _number(candidate["line"])
            if line < 0 or (line * 2) % 1:
                raise ValueError("unsupported count line")
            sides = [("OVER", candidate["over_odds"], candidate["over_selection_id"]),
                     ("UNDER", candidate["under_odds"], candidate["under_selection_id"])]
            if candidate["over_selection_id"] == candidate["under_selection_id"]:
                raise ValueError("duplicate opposing selection identity")
            threshold = line
        elif market == "receptions_alt" and candidate.get("shape") == "alt_ladder":
            threshold = _number(candidate["threshold"])
            if threshold <= 0 or threshold % 1:
                raise ValueError("invalid N+ threshold")
            sides = [("AT_LEAST", candidate["yes_odds"], candidate["selection_id"])]
        else:
            raise ValueError("unsupported market/shape")
        pmf = distribution["pmf"]
        if threshold >= len(pmf) - 1:
            raise ValueError("line outside PMF support")
        prices = []
        for side, odds, selection_id in sides:
            _text(selection_id)
            if type(odds) is not int or abs(odds) < 100:
                raise ValueError("invalid observed American odds")
            win = math.fsum(p for k, p in enumerate(pmf) if
                            (k > threshold if side == "OVER" else
                             k < threshold if side == "UNDER" else k >= threshold))
            push = math.fsum(p for k, p in enumerate(pmf) if k == threshold) if side != "AT_LEAST" else 0.0
            loss = max(0.0, 1.0-win-push)
            profit = odds/100 if odds > 0 else 100/abs(odds)
            prices.append({
                "side": side, "selection_id": selection_id, "odds": odds,
                "decimal_odds": 1.0 + profit,
                "price_band": "PLUS_MONEY" if odds > 100 else "EVEN_MONEY" if abs(odds)==100 else "MINUS_MONEY",
                "win": win, "push": push, "loss": loss,
                "break_even_conditional_on_no_push": breakeven_probability(odds),
                "win_conditional_on_no_push": win/(win+loss) if win+loss else None,
                "expected_net_units": win*profit-loss,
                "probability_is_unvalidated": True,
            })
        if len(prices) == 2:
            implied = [breakeven_probability(p["odds"]) for p in prices]
            for p, q in zip(prices, implied):
                p["market_fair_conditional_on_no_push"] = q / sum(implied)
        result["prices"] = prices
        availability = candidate.get("availability_status", "UNKNOWN_GAME_COVERAGE")
        reasons = []
        if availability != "NOT_LISTED_INACTIVE":
            reasons.append(availability)
        if candidate.get("decision_status") != "SHADOW_ONLY":
            reasons.append("UPSTREAM_ELIGIBILITY_NOT_CLEARED")
        if candidate.get("quote_timestamp") is None:
            reasons.append("QUOTE_TIMESTAMP_NOT_PROVIDED")
        elif not _quote_evidence_valid(candidate, raw_source_sha256, captured):
            reasons.append("QUOTE_PROVENANCE_UNVERIFIED")
        if candidate.get("current_role_status") != "VERIFIED":
            reasons.append("CURRENT_ROLE_NOT_VERIFIED")
        elif not _role_evidence_valid(candidate, captured):
            reasons.append("CURRENT_ROLE_EVIDENCE_MISSING")
        if candidate.get("authoritative_b0_status") != "JOINED":
            reasons.append("AUTHORITATIVE_B0_NOT_JOINED")
        rule = candidate.get("sportsbook_rule")
        if not isinstance(rule, dict) or rule.get("status") != "CERTIFIED":
            reasons.append("BOOK_ACTION_RULES_NOT_CERTIFIED")
        elif not _rule_evidence_valid(candidate, now):
            reasons.append("BOOK_ACTION_RULES_EVIDENCE_MISSING_OR_MISMATCHED")
        if reasons:
            result["decision_status"] = "QUARANTINED"
            result["reasons"] = reasons
        else:
            result["decision_status"] = "SHADOW_ONLY"
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        result["prices"] = []
        result["reasons"] = [str(exc)]
    # Fail on non-JSON inputs rather than silently sanitizing forensic evidence.
    result["record_sha256"] = _hash(result)
    return result


def settle_record(record: dict, outcome: dict) -> dict:
    """Separate official outcome/profit record; original prediction is never edited."""
    payload = {k: v for k, v in record.items() if k != "record_sha256"}
    if _hash(payload) != record.get("record_sha256"):
        raise ValueError("prediction seal mismatch")
    if record.get("decision_status") not in {"QUARANTINED", "SHADOW_ONLY"} or not record.get("prices"):
        raise ValueError("unpriced or invalid prediction cannot settle")
    candidate = record["candidate"]
    for field in ("event_id", "gsis_id"):
        if outcome.get(field) != candidate.get(field):
            raise ValueError("outcome identity mismatch")
    if outcome.get("canonical_game_id") != candidate.get("canonical_game_id"):
        raise ValueError("outcome canonical game mismatch")
    if outcome.get("stat") != "receptions":
        raise ValueError("outcome stat mismatch")
    if outcome.get("authority") != "OFFICIAL_FINAL":
        raise ValueError("requires official final")
    _digest(outcome["source_sha256"])
    if _time(outcome["observed_at"]) < max(_time(candidate["event_open_date"]),
                                         _time(candidate["canonical_kickoff"])):
        raise ValueError("outcome before kickoff")
    # FanDuel's NFL prop rule says any game snap; special-teams participation
    # cannot be misclassified as a DNP just because offensive snaps are zero.
    snaps = outcome.get("total_snaps")
    if type(snaps) is not int or snaps < 0:
        raise ValueError("certified total snap count required")
    _digest(outcome["participation_source_sha256"])
    if type(outcome.get("played")) is not bool or outcome["played"] != (snaps > 0):
        raise ValueError("participation contradicts snap count")
    actual = outcome.get("receptions")
    if not outcome["played"] and actual not in (None, 0):
        raise ValueError("DNP contradicts nonzero receptions")
    if outcome["played"] and (type(actual) is not int or actual < 0):
        raise ValueError("invalid actual count")
    rows = []
    for price in record["prices"]:
        side = price["side"]
        boundary = candidate["threshold"] if side == "AT_LEAST" else candidate["line"]
        if not outcome["played"]:
            status = "VOID_DNP"
        elif side != "AT_LEAST" and actual == boundary:
            status = "PUSH"
        else:
            hit = actual >= boundary if side == "AT_LEAST" else actual > boundary if side == "OVER" else actual < boundary
            status = "HIT" if hit else "MISS"
        profit = price["odds"]/100 if price["odds"] > 0 else 100/abs(price["odds"])
        rows.append({"selection_id": price["selection_id"], "status": status,
                     "net_units": profit if status == "HIT" else -1.0 if status == "MISS" else 0.0})
    return {"prediction_sha256": record["record_sha256"], "outcome": deepcopy(outcome),
            "population": record["decision_status"], "settlements": rows,
            "policy_note": "Research count contract; jurisdiction-specific sportsbook action rules remain external."}


def write_evidence(path: str | Path, payload: dict) -> None:
    """Atomic create-only evidence: never replace an earlier frozen record.

    Same-directory hard-link publishes fully flushed bytes atomically and fails
    if the destination exists. Failed temporary writes are always removed.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".price-evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        with open(tmp, encoding="utf-8") as handle:
            if json.load(handle) != payload:
                raise ValueError("evidence read-back mismatch")
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

