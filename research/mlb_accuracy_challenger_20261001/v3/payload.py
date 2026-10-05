#!/usr/bin/env python3
"""FC-MLB-001B board identity contract (criteria TASKS/FC-MLB-001B.md §5), spec `fc-mlb-001b-payload-v1` (FROZEN).

The shadow board = SCIENTIFIC PAYLOAD + EVIDENCE ENVELOPE.

  scientific payload  the board with EXACTLY these evidence-generation fields removed:
                        top level:   board_generated_at, sealed_at, board_sha256 (self-referential hash over them)
                        records[*]:  generation_timestamp
                      Everything else, at every depth, is kept verbatim (predictions, rankings, players, games,
                      model/inputs, provenance, labels, counts). Nothing that can influence selection or evaluation
                      is removed: the four stamps are read only by envelope chronology (manifest_v3 board age and
                      sealed_at <= cutoff), never by selection, ranking or evaluation.
  serialization       json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")   -- literal bytes; sha256 of those bytes is the identity.

Replay must regenerate the payload from the replayed board and match the sealed payload BYTE FOR BYTE and by
SHA-256. No tolerance, no fuzzy comparison. The envelope (wall-clock stamps, receipts, TSA) is never
replay-compared; its chronology and integrity are validated instead (envelope_chronology).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

SPEC = "fc-mlb-001b-payload-v1"
ENVELOPE_TOP = ("board_generated_at", "sealed_at", "board_sha256")
ENVELOPE_RECORD = ("generation_timestamp",)


class PayloadError(ValueError):
    pass


def scientific_payload(board):
    if not isinstance(board, dict) or not isinstance(board.get("records"), list):
        raise PayloadError("not a shadow board (records list required)")
    out = {k: v for k, v in board.items() if k not in ENVELOPE_TOP}
    recs = []
    for r in board["records"]:
        if not isinstance(r, dict):
            raise PayloadError("board record is not an object")
        recs.append({k: v for k, v in r.items() if k not in ENVELOPE_RECORD})
    out["records"] = recs
    return out


def canonical_bytes(payload):
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except ValueError as exc:            # NaN/Infinity cannot be serialized canonically
        raise PayloadError(f"payload not canonically serializable: {exc}") from exc


def payload_bytes(board):
    return canonical_bytes(scientific_payload(board))


def payload_sha256(board):
    return hashlib.sha256(payload_bytes(board)).hexdigest()


def compare(sealed_bytes, replayed_board):
    """Literal comparison of the sealed payload bytes with the payload regenerated from a replayed board."""
    rep = payload_bytes(replayed_board)
    h = lambda b: hashlib.sha256(b).hexdigest()
    out = {"spec": SPEC, "record_payload_sha256": h(sealed_bytes), "replay_payload_sha256": h(rep),
           "record_bytes": len(sealed_bytes), "replay_bytes": len(rep), "literal_identical": sealed_bytes == rep}
    if not out["literal_identical"]:
        out["differing_fields"] = _diff_paths(json.loads(sealed_bytes), json.loads(rep))[:50]
    return out


def _diff_paths(a, b, path="payload"):
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in _diff_paths(a.get(k, "<absent>"), b.get(k, "<absent>"), f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diff_paths(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def _t(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def envelope_chronology(board, cutoff_utc, tsa_times, first_pitch_utc, canonical_board_hash):
    """Validate the evidence envelope instead of replaying it: self-hash consistent; every record stamped with the
    board generation time; board_generated_at <= sealed_at <= manifest cutoff <= every TSA genTime < first pitch."""
    problems = []
    if canonical_board_hash(board) != board.get("board_sha256"):
        problems.append("board_sha256 is not the canonical hash of the sealed board")
    gen = board.get("board_generated_at")
    if any(r.get("generation_timestamp") != gen for r in board.get("records") or []):
        problems.append("a record generation_timestamp differs from board_generated_at")
    try:
        g, s, c, fp = _t(gen), _t(board.get("sealed_at")), _t(cutoff_utc), _t(first_pitch_utc)
        if not g <= s:
            problems.append(f"board_generated_at {gen} after sealed_at {board.get('sealed_at')}")
        if not s <= c:
            problems.append(f"sealed_at after manifest cutoff {cutoff_utc}")
        if not tsa_times:
            problems.append("no valid TSA time")
        for name, t in (tsa_times or {}).items():
            tt = _t(t)
            if not c <= tt:
                problems.append(f"TSA {name} {t} precedes the manifest cutoff")
            if not tt < fp:
                problems.append(f"TSA {name} {t} not before first pitch {first_pitch_utc}")
    except (TypeError, ValueError) as exc:
        problems.append(f"unparseable envelope time: {exc}")
    return problems
