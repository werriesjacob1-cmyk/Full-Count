#!/usr/bin/env python3
"""Externally anchored pregame seal -- preregistration v3, sections 7 and 8.

A seal record binds, for one slate unit (date, window): the prereg version,
challenger version (frozen coefficients hash), the frozen shadow champion
identity, and the shadow-board, capture, schedule and manifest hashes. Records
form a hash chain (prev_seal_sha256) on the append-only evidence ref.

Git author/committer times prove nothing. A seal counts only with EXTERNAL,
server-observed receipts dated strictly before the earliest covered first pitch:
  R1  a GitHub Issue #91 comment whose body contains the seal_sha256
      (GitHub's server `created_at`);
  R2  at least one RFC 3161 timestamp token over the seal_sha256 bytes from an
      independent TSA (FreeTSA and/or DigiCert), verified cryptographically.
Both R1 and R2 are required. Anything else -> SLATE_INVALID_NO_CONFIRMATORY_USE.
A late seal is never repaired, superseded or backfilled.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from capture import canonical_sha256

SEAL_VERSION = "mlb-challenger-seal-v3"
EVIDENCE_REF = "claude/mlb-challenger-v3-evidence"
TSAS = {"freetsa": "https://freetsa.org/tsr", "digicert": "http://timestamp.digicert.com"}
_CERTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tsa_certs")
TSA_TRUST = {"freetsa": {"ca_file": os.path.join(_CERTS, "freetsa_cacert.pem"),
                         "untrusted": os.path.join(_CERTS, "freetsa_tsa.crt")},
             "digicert": {}}   # DigiCert chains to the system CA store


def utc(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {s}")
    return t.astimezone(timezone.utc)


def build_seal(manifest, *, prev_seal_sha256, prereg_sha256, challenger_sha256, shadow_id, created_at,
               artifacts_sha256):
    """artifacts_sha256: {file name in the unit directory: sha256 of its exact bytes}."""
    rec = {"seal_version": SEAL_VERSION, "prereg_version": "v3", "prereg_sha256": prereg_sha256,
           "challenger_version": challenger_sha256, "frozen_shadow_champion": shadow_id,
           "date": manifest["date"], "window": manifest["window"], "cutoff_utc": manifest["cutoff_utc"],
           "manifest_sha256": manifest["manifest_sha256"], "shadow_board_sha256": manifest["shadow_board_sha256"],
           "capture_sha256": manifest["capture_sha256"], "schedule_sha256": manifest["schedule_sha256"],
           "covered_games": manifest["covered_games"], "earliest_first_pitch_utc": manifest["earliest_first_pitch_utc"],
           "artifacts_sha256": dict(sorted(artifacts_sha256.items())),
           "prev_seal_sha256": prev_seal_sha256, "created_at_claimed": created_at}
    rec["seal_sha256"] = canonical_sha256({k: v for k, v in rec.items() if k != "seal_sha256"})
    return rec


def receipt_comment_body(seal):
    return (f"MLB V3 SEAL RECEIPT\n\n- slate unit: {seal['date']} {seal['window']}\n"
            f"- seal_sha256: `{seal['seal_sha256']}`\n- manifest_sha256: `{seal['manifest_sha256']}`\n"
            f"- capture_sha256: `{seal['capture_sha256']}`\n- shadow_board_sha256: `{seal['shadow_board_sha256']}`\n"
            f"- earliest covered first pitch: {seal['earliest_first_pitch_utc']}\n\nAlligator")


# ---- RFC 3161 -----------------------------------------------------------------------
def tsa_request(seal_sha256, url, timeout=30):
    import requests
    with tempfile.TemporaryDirectory() as d:
        dg = os.path.join(d, "d.bin")
        with open(dg, "wb") as fh:
            fh.write(seal_sha256.encode())
        q = os.path.join(d, "q.tsq")
        subprocess.run(["openssl", "ts", "-query", "-data", dg, "-sha256", "-cert", "-out", q], check=True,
                       capture_output=True)
        r = requests.post(url, data=open(q, "rb").read(), headers={"Content-Type": "application/timestamp-query"},
                          timeout=timeout)
        r.raise_for_status()
        return base64.b64encode(r.content).decode()


def tsa_gen_time(token_b64):
    with tempfile.NamedTemporaryFile(suffix=".tsr") as fh:
        fh.write(base64.b64decode(token_b64))
        fh.flush()
        txt = subprocess.run(["openssl", "ts", "-reply", "-in", fh.name, "-text"], capture_output=True,
                             text=True, check=True).stdout
    m = re.search(r"Time stamp: ([A-Z][a-z]{2}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2})(?:\.\d+)? (\d{4}) GMT", txt)
    if "Status: Granted" not in txt or not m:
        raise ValueError("TSA token not granted or genTime unreadable")
    mon, day, hh, mm, ss, yr = m.groups()
    return datetime.strptime(f"{mon} {day} {hh}:{mm}:{ss} {yr}", "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)


def tsa_verify_named(seal_sha256, token_b64, tsa):
    return tsa_verify(seal_sha256, token_b64, **TSA_TRUST[tsa])


def tsa_verify(seal_sha256, token_b64, ca_file=None, untrusted=None, ca_path="/etc/ssl/certs"):
    """True only if the token cryptographically covers sha256(seal_sha256 bytes) under a trusted chain."""
    with tempfile.TemporaryDirectory() as d:
        dg, tok = os.path.join(d, "d.bin"), os.path.join(d, "t.tsr")
        with open(dg, "wb") as fh:
            fh.write(seal_sha256.encode())
        with open(tok, "wb") as fh:
            fh.write(base64.b64decode(token_b64))
        cmd = ["openssl", "ts", "-verify", "-data", dg, "-in", tok]
        cmd += ["-CAfile", ca_file] if ca_file else ["-CApath", ca_path]
        if untrusted:
            cmd += ["-untrusted", untrusted]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode == 0 and "Verification: OK" in r.stdout


# ---- verification from RAW evidence only ---------------------------------------------------
ISSUE_91_API_URL = "https://api.github.com/repos/werriesjacob1-cmyk/Full-Count/issues/91"


def tsa_check(digest_hex, token_b64, tsa):
    """Cryptographic check of stored token bytes: signature + trust chain + message imprint over
    `digest_hex` bytes; genTime is read from the token itself. Returns gen_time (aware UTC) or None."""
    if tsa not in TSA_TRUST or not token_b64:
        return None
    try:
        if not tsa_verify_named(digest_hex, token_b64, tsa):
            return None
        return tsa_gen_time(token_b64)
    except Exception:  # noqa: BLE001 -- any malformed token is simply not evidence
        return None


def verify_receipts_raw(seal, github_comment, tokens, *, earliest_first_pitch_utc):
    """github_comment: the comment JSON exactly as the GitHub API returned it to the verifier.
    tokens: [{"tsa": name, "token_b64": ...}] as stored; any other stored field is ignored.
    Returns (status, detail)."""
    if canonical_sha256({k: v for k, v in seal.items() if k != "seal_sha256"}) != seal.get("seal_sha256"):
        return "SEAL_HASH_MISMATCH", "seal record does not rebuild"
    efp = utc(earliest_first_pitch_utc)
    gh = github_comment or {}
    if gh.get("issue_url") != ISSUE_91_API_URL or seal["seal_sha256"] not in (gh.get("body") or ""):
        return "MISSING_EXTERNAL_RECEIPT", "no Issue #91 comment carrying this seal hash"
    if not gh.get("created_at") or not utc(gh["created_at"]) < efp:
        return "LATE_SEAL", f"GitHub receipt {gh.get('created_at')} not before first pitch {efp.isoformat()}"
    checked = [(t.get("tsa"), tsa_check(seal["seal_sha256"], t.get("token_b64"), t.get("tsa"))) for t in tokens or []]
    valid = [(n, g) for n, g in checked if g is not None]
    if not valid:
        return "MISSING_EXTERNAL_RECEIPT", "no cryptographically valid RFC 3161 token"
    pregame = [(n, g.isoformat()) for n, g in valid if g < efp]
    if not pregame:
        return "LATE_SEAL", "every valid RFC 3161 token is dated at/after first pitch"
    return "ON_TIME", {"github_created_at": gh["created_at"], "tsa_valid_pregame": pregame,
                       "tsa_invalid": [n for n, g in checked if g is None]}


def verify_chain(seals, genesis_sha256):
    """Append-only check: each record's prev hash is the previous record's seal hash, starting from
    genesis; at most one seal per (date, window). Raises on any break (rewrite, deletion, reorder)."""
    prev, seen = genesis_sha256, set()
    for s in seals:
        if canonical_sha256({k: v for k, v in s.items() if k != "seal_sha256"}) != s.get("seal_sha256"):
            raise ValueError(f"seal {s.get('date')}/{s.get('window')} was modified")
        if s["prev_seal_sha256"] != prev:
            raise ValueError(f"chain break at {s['date']}/{s['window']}: history rewritten, removed or reordered")
        unit = (s["date"], s["window"])
        if unit in seen:
            raise ValueError(f"second seal for slate unit {unit}: superseding is not allowed")
        seen.add(unit)
        prev = s["seal_sha256"]
    return prev


def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()
