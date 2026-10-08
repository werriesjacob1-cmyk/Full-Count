#!/usr/bin/env python3
"""Same-cutoff eligible candidate manifest, preregistration v2 -- research only.

Implements section 5 of `research/mlb_accuracy_challenger_prereg_v2_20261001.md`.

ONE frozen full board + the FanDuel player-prop captures in `data/props` that
were taken at or before that board's cutoff  ->  ONE manifest per slate:
every board record, its family status, its quote provenance, and the single
exclusion reason (or eligibility). Every arm, the champion included, draws
only from the manifest's eligible PRIMARY rows. The manifest is hashed.

The builder never receives an outcome: there is no graded-file parameter.
Price provenance is the captured quote, never `data/odds` (game lines), never
an inferred, closing or consensus price, and no opposite side is ever invented.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter

import harness as H

MANIFEST_VERSION = "mlb-challenger-manifest-v2"

# ---- locked by preregistration v2 (section 5) -----------------------------------------
BOOK = "fanduel"
MAX_QUOTE_AGE_S = 45 * 60          # = recommendation.MAX_PRICE_AGE_SECONDS (published Top Pick path)
MAX_BOARD_AGE_S = 4 * 60 * 60      # = recommendation.MAX_BOARD_AGE_SECONDS
PUBLIC_RELIABILITY = ("A", "B")    # = recommendation.TOP_PICK_MIN_RELIABILITY
PRICE_BAND = H.PRICE_BAND          # (0.40, 0.70), unchanged from v1
MODEL_VERSIONS = H.MODEL_VERSIONS  # ("2026.08.15",), unchanged from v1

PRIMARY_FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
RED_FLAG_FAMILIES = ("combined_strikeouts",)   # own status; never in the primary universe
NOT_PLAYER_PROP = ("nrfi_combined", "first_inning_run")
TWO_SIDED_STATS = ("strikeouts", "pitcher_outs")   # captured in `two_sided_snapshots`
STAT_ALIASES = {"stolen_base": "stolen_bases"}     # = odds_fanduel.STAT_ALIASES


def family_status(stat):
    if stat in PRIMARY_FAMILIES:
        return "PRIMARY"
    if stat in RED_FLAG_FAMILIES:
        return "RED_FLAG_SEPARATE"
    if stat in NOT_PLAYER_PROP:
        return "NOT_A_PLAYER_PROP"
    return "EXPLORATORY"


def normalize_name(name):
    """Byte-for-byte the rule of odds_fanduel.normalize_name (the key data/props uses)."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s.lower())
    return s.replace(".", "").replace("  ", " ").strip()


def canonical_hash(obj, drop):
    body = {k: v for k, v in obj.items() if k != drop}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _int_needs(raw):
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else None


def cutoff_captures(props_files, cutoff):
    """The latest one-sided and the latest two-sided capture taken at or before the cutoff,
    across the given props files (a UTC-dated file can hold a slate's captures, so callers
    pass the cutoff's UTC date and the day before). Later captures are invisible."""
    one, two = {}, {}
    for p in props_files:
        for s in (p or {}).get("snapshots") or []:
            if H._utc(s["taken_at"]) <= cutoff:
                one[s["taken_at"]] = s
        for s in (p or {}).get("two_sided_snapshots") or []:
            if H._utc(s["taken_at"]) <= cutoff:
                two[s["taken_at"]] = s
    latest = lambda d: d[max(d, key=H._utc)] if d else None
    return latest(one), latest(two)


def resolve_quote(rec, pn, needs, one, two, cutoff):
    """Return (quote dict, None) or (None, reason). Pure; uses only the cutoff captures."""
    stat, side = rec.get("stat"), rec.get("market_side")
    two_sided = stat in TWO_SIDED_STATS
    snap = two if two_sided else one
    if snap is None:
        return None, "NO_CAPTURE_AT_OR_BEFORE_CUTOFF"
    taken = H._utc(snap["taken_at"])
    age = (cutoff - taken).total_seconds()
    if age > MAX_QUOTE_AGE_S:
        return None, "QUOTE_STALE"
    if two_sided:
        rows = [x for x in snap.get("rows") or [] if x.get("market") == stat
                and x.get("player_norm") == pn and _int_needs(x.get("needs")) == needs]
    else:
        want = STAT_ALIASES.get(stat, stat)
        rows = [x for x in snap.get("rows") or [] if x.get("stat") == want
                and x.get("player_norm") == pn and _int_needs(x.get("needs")) == needs]
    if not rows:
        return None, "NOT_QUOTED_AT_CUTOFF"          # pulled, suspended, or never offered
    if any(x.get("in_play") for x in rows):
        return None, "IN_PLAY"
    if not two_sided and any(x.get("start_time") and H._utc(x["start_time"]) <= cutoff for x in rows):
        return None, "GAME_STARTED"
    if two_sided:
        if side not in ("over", "under"):
            return None, "SIDE_NOT_OFFERED"
        prices = {(x.get("over_odds"), x.get("under_odds")) for x in rows}
        if len(prices) != 1:
            return None, "AMBIGUOUS_QUOTE"
        over, under = prices.pop()
        price = over if side == "over" else under
        if price is None:
            return None, "SIDE_NOT_OFFERED"
        q_devig = None
        if over is not None and under is not None:   # a REAL captured opposite side only
            a, b = H.implied(over), H.implied(under)
            q_devig = (a if side == "over" else b) / (a + b)
    else:
        if side != "over":                             # one-sided Yes/Over capture only
            return None, "SIDE_NOT_OFFERED"
        prices = {x.get("american") for x in rows}
        if len(prices) != 1:
            return None, "AMBIGUOUS_QUOTE"
        price, q_devig = prices.pop(), None
        if price is None:
            return None, "SIDE_NOT_OFFERED"
    return {"quote_odds": price, "quote_taken_at": snap["taken_at"], "quote_age_s": round(age, 3),
            "quote_source": "data/props:" + ("two_sided_snapshots" if two_sided else "snapshots"),
            "q_devig": q_devig, "devig": "TWO_SIDED_CAPTURED" if q_devig is not None else "ONE_SIDED_NO_DEVIG"}, None


def _settlement_supported(rec):
    return rec.get("market_side") == "over" and _int_needs(rec.get("needs")) is not None


def build_manifest(board, props_files, *, source_paths=None):
    """board: a frozen full board. props_files: parsed data/props files. No outcome input."""
    if H.canonical_board_hash(board) != board.get("board_sha256"):
        raise ValueError(f"{board.get('date')}: board hash does not rebuild")
    cutoff = H._utc(board["sealed_at"])
    board_age = (cutoff - H._utc(board["board_generated_at"])).total_seconds()
    one, two = cutoff_captures(props_files, cutoff)
    starts = board.get("game_start_times") or {}
    ident = Counter((str(r.get("game_pk")), str(r.get("player_id")), r.get("stat"), str(r.get("needs")),
                     r.get("market_side")) for r in board.get("records") or [])
    rows = []
    for r in sorted(board.get("records") or [], key=lambda x: x["candidate_id"]):
        elig, mkt, pred, sel = (r.get("eligibility") or {}), (r.get("market") or {}), \
            (r.get("prediction") or {}), (r.get("selector") or {})
        stat, status = r.get("stat"), sel.get("recommendation_status")
        fstat, pn, needs = family_status(stat), normalize_name(r.get("player_name")), _int_needs(r.get("needs"))
        key = (str(r.get("game_pk")), str(r.get("player_id")), stat, str(r.get("needs")), r.get("market_side"))
        start = starts.get(str(r.get("game_pk")))
        quote, reason = None, None
        if fstat not in ("PRIMARY", "EXPLORATORY"):
            reason = "FAMILY_" + fstat
        elif ident[key] > 1:
            reason = "DUPLICATE_IDENTITY"
        elif not _settlement_supported(r):
            reason = "SETTLEMENT_UNSUPPORTED"
        elif elig.get("qc_status") != "kept":
            reason = "QC_NOT_KEPT"
        elif elig.get("lineup_assumed") is not False:
            reason = "LINEUP_OR_STARTER_NOT_CONFIRMED"
        elif pred.get("hit_probability") is None:
            reason = "NO_PROBABILITY"
        elif pred.get("sample_n") == 0:
            reason = "NO_TRACK_RECORD"
        elif pred.get("reliability") not in PUBLIC_RELIABILITY:
            reason = "RELIABILITY_NOT_PUBLIC_ELIGIBLE"
        elif status is None:
            reason = "NO_RECOMMENDATION_STATUS"
        elif (r.get("provenance") or {}).get("model_version") not in MODEL_VERSIONS:
            reason = "MODEL_VERSION_NOT_FROZEN"
        elif mkt.get("market_odds") is None:
            reason = "NO_BOARD_PRICE"
        elif board_age > MAX_BOARD_AGE_S:
            reason = "BOARD_STALE"
        elif not start or H._utc(start) <= cutoff:
            reason = "GAME_STARTED"
        else:
            quote, reason = resolve_quote(r, pn, needs, one, two, cutoff)
            if quote and quote["quote_odds"] != mkt["market_odds"]:
                reason = "PRICE_MISMATCH_BOARD_VS_CAPTURE"
            elif quote and not PRICE_BAND[0] <= H.implied(quote["quote_odds"]) <= PRICE_BAND[1]:
                reason = "PRICE_OUTSIDE_BAND"
        row = {"candidate_id": r["candidate_id"], "game_pk": str(r.get("game_pk")),
               "player_id": str(r.get("player_id")), "player_name": r.get("player_name"), "player_norm": pn,
               "stat": stat, "family_status": fstat, "needs": needs, "line": r.get("line"),
               "side": r.get("market_side"), "game_start_utc": start, "board_odds": mkt.get("market_odds"),
               "p0": pred.get("hit_probability"), "reliability": pred.get("reliability"),
               "recommendation_status": status, "champion": status == "top_pick",
               "eligible": reason is None, "exclusion_reason": reason}
        if quote:
            row.update(quote)
            row["q_raw"] = H.implied(quote["quote_odds"])
        rows.append(row)
    elig_primary = [x for x in rows if x["eligible"] and x["family_status"] == "PRIMARY"]
    m = {"manifest_version": MANIFEST_VERSION, "date": board["date"], "cutoff_utc": board["sealed_at"],
         "board_sha256": board["board_sha256"], "board_generated_at": board["board_generated_at"],
         "board_age_s": round(board_age, 3),
         "rules": {"book": BOOK, "max_quote_age_s": MAX_QUOTE_AGE_S, "max_board_age_s": MAX_BOARD_AGE_S,
                   "public_reliability": list(PUBLIC_RELIABILITY), "price_band": list(PRICE_BAND),
                   "model_versions": list(MODEL_VERSIONS), "primary_families": list(PRIMARY_FAMILIES),
                   "red_flag_families": list(RED_FLAG_FAMILIES), "not_player_prop": list(NOT_PLAYER_PROP),
                   "price_match": "captured quote american == board market_odds, exact",
                   "implied": "raw one-sided implied probability of the captured price"},
         "sources": {"board": source_paths.get("board") if source_paths else None,
                     "props_files": source_paths.get("props") if source_paths else None,
                     "never_used": "data/odds (game lines only)",
                     "one_sided_capture_taken_at": one["taken_at"] if one else None,
                     "two_sided_capture_taken_at": two["taken_at"] if two else None},
         "counts": {"records": len(rows), "eligible_primary": len(elig_primary),
                    "eligible_exploratory": sum(1 for x in rows if x["eligible"] and x["family_status"] == "EXPLORATORY"),
                    "champion_total": sum(1 for x in rows if x["champion"]),
                    "champion_in_primary_universe": sum(1 for x in elig_primary if x["champion"]),
                    "champion_excluded_by_reason": dict(Counter(x["exclusion_reason"] for x in rows
                                                                if x["champion"] and not x["eligible"])),
                    "champion_exploratory": sum(1 for x in rows if x["champion"] and x["eligible"]
                                                and x["family_status"] == "EXPLORATORY"),
                    "exclusions": dict(Counter(x["exclusion_reason"] for x in rows if not x["eligible"]))},
         "rows": rows}
    m["manifest_sha256"] = canonical_hash(m, "manifest_sha256")
    return m


def verify_manifest(m):
    if canonical_hash(m, "manifest_sha256") != m.get("manifest_sha256"):
        raise ValueError(f"{m.get('date')}: manifest hash does not rebuild")
    if m.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError(f"{m.get('date')}: unexpected manifest version")
    return m
