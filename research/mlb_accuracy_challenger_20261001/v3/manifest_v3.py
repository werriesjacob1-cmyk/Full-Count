#!/usr/bin/env python3
"""Common operational candidate manifest -- preregistration v3, sections 5, 6 and 10.

Inputs, all produced BEFORE the cutoff and none of them an outcome:
  shadow board  -- the frozen shadow champion's full board (shadow.py);
  capture       -- the dedicated FanDuel capture (capture.py);
  schedule      -- MLB statsapi schedule snapshot for the date (game_pk, teams,
                   start, gameType, doubleheader, probable pitchers).
Output: one hashed manifest per slate unit (date, window). Every shadow-board
record of the window appears once, with a pass/fail entry for every gate and
the first failing reason (or eligible). Every arm, the shadow champion
included, draws only from eligible PRIMARY rows.

A quote binds to the EXACT offer: book + mapped FanDuel event (one-to-one with
the board's game_pk) + market type/id + selection id + player name + team slug
(when FanDuel shows one) + side + exact threshold/line + American odds +
capture id. Equal player/stat/needs/price in another game never matches.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone

from capture import BOOK, canonical_sha256

MANIFEST_VERSION = "mlb-challenger-manifest-v3"

# ---- locked by preregistration v3 -----------------------------------------------------
MAX_QUOTE_AGE_S = 45 * 60          # ceiling; normal path is ~minutes
MAX_BOARD_AGE_S = 4 * 60 * 60
PUBLIC_RELIABILITY = ("A", "B")
PRICE_BAND = (0.40, 0.70)
EVENT_MATCH_TOLERANCE_S = 90 * 60
WINDOW_SPLIT_ET_HOUR = 17          # DAY: first pitch before 17:00 America/New_York; NIGHT: at/after
PRIMARY_FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
RED_FLAG_FAMILIES = ("combined_strikeouts",)
NOT_PLAYER_PROP = ("nrfi_combined", "first_inning_run")
PITCHER_FAMILIES = ("strikeouts", "pitcher_outs")
# One-sided Yes markets: (stat, needs) -> FanDuel marketType. Copied from
# odds_fanduel.MARKET_MAP at the shadow pin; must be one-to-one.
ONE_SIDED_TYPES = {
    ("hits", 1): "PLAYER_TO_RECORD_A_HIT", ("hits", 2): "PLAYER_TO_RECORD_2+_HITS",
    ("hits", 3): "PLAYER_TO_RECORD_3+_HITS", ("hits", 4): "PLAYER_TO_RECORD_4+_HITS",
    ("hits_runs_rbis", 1): "PLAYER_TO_RECORD_1+_HITS+RUNS+RBIS",
    ("hits_runs_rbis", 2): "PLAYER_TO_RECORD_2+_HITS+RUNS+RBIS",
    ("hits_runs_rbis", 3): "PLAYER_TO_RECORD_3+_HITS+RUNS+RBIS",
    ("hits_runs_rbis", 4): "PLAYER_TO_RECORD_4+_HITS+RUNS+RBIS",
    ("total_bases", 2): "TO_RECORD_2+_TOTAL_BASES", ("total_bases", 3): "TO_RECORD_3+_TOTAL_BASES",
    ("total_bases", 4): "TO_RECORD_4+_TOTAL_BASES", ("total_bases", 5): "TO_RECORD_5+_TOTAL_BASES",
    ("home_runs", 1): "TO_HIT_A_HOME_RUN", ("runs", 1): "TO_RECORD_A_RUN", ("rbis", 1): "TO_RECORD_AN_RBI",
    ("stolen_base", 1): "TO_RECORD_A_STOLEN_BASE", ("singles", 1): "TO_HIT_A_SINGLE",
    ("doubles", 1): "TO_HIT_A_DOUBLE", ("triples", 1): "TO_HIT_A_TRIPLE",
    ("hard_hit_105", 1): "TO_HIT_A_LASER_(105+_MPH)", ("moonshot_420", 1): "PLAYER_TO_HIT_A_HOME_RUN_420+_FEET",
}
K_TYPES = frozenset(f"PITCHER_{s}_{k}" for s in "ABCDEF" for k in ("TOTAL_STRIKEOUTS", "STRIKEOUTS"))
OUTS_SUFFIX = "_OUTS_RECORDED_SB"
FAMILY_TABS = {"one_sided": ("batter-props", "popular"), "pitcher": ("pitcher-props", "popular"),
               "lasers": ("lasers",), "moonshots": ("moonshots",)}
_OUTS_RE = re.compile(r"^(.*?)\s+(Over|Under)\s+([\d.]+)$")


def utc(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {s}")
    return t.astimezone(timezone.utc)


def implied(odds):
    o = float(odds)
    return (-o) / (-o + 100.0) if o < 0 else 100.0 / (o + 100.0)


def norm_name(name):
    """Byte-for-byte odds_fanduel.normalize_name."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s.lower())
    return s.replace(".", "").replace("  ", " ").strip()


def norm_team(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def team_slug_of(name):
    return norm_team(name).replace(" ", "_")


def odds_int(v):
    """Harmless representation only: '+100' / 100 / 100.0 -> 100. Never a substitution."""
    if v is None:
        return None
    f = float(str(v).replace("+", ""))
    if f != int(f):
        raise ValueError(f"non-integer American odds {v}")
    return int(f)


def window_of(start_utc):
    from zoneinfo import ZoneInfo
    return "DAY" if utc(start_utc).astimezone(ZoneInfo("America/New_York")).hour < WINDOW_SPLIT_ET_HOUR else "NIGHT"


def family_status(stat):
    if stat in PRIMARY_FAMILIES:
        return "PRIMARY"
    if stat in RED_FLAG_FAMILIES:
        return "RED_FLAG_SEPARATE"
    if stat in NOT_PLAYER_PROP:
        return "NOT_A_PLAYER_PROP"
    return "EXPLORATORY"


def _int_needs(raw):
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else None


# ---- event mapping ----------------------------------------------------------------------
def _event_teams(event_name):
    if " @ " not in (event_name or ""):
        return None, None
    away, home = event_name.split(" @ ", 1)
    strip = lambda s: norm_team(re.sub(r"\([^)]*\)", "", s))
    return strip(away), strip(home)


def map_events(capture, schedule_games):
    """One-to-one game_pk <-> FanDuel event. Returns ({game_pk: event}, {game_pk: reason})."""
    cands = {}
    for g in schedule_games:
        gp = str(g["game_pk"])
        hits = []
        for ev in capture.get("events") or []:
            a, h = _event_teams(ev.get("event_name"))
            if a != norm_team(g["away_team"]) or h != norm_team(g["home_team"]) or not ev.get("open_date"):
                continue
            if abs((utc(ev["open_date"]) - utc(g["game_date"])).total_seconds()) <= EVENT_MATCH_TOLERANCE_S:
                hits.append(ev)
        cands[gp] = hits
    claimed = Counter(str(ev["event_id"]) for hits in cands.values() for ev in hits)
    mapped, why = {}, {}
    for gp, hits in cands.items():
        if not hits:
            why[gp] = "EVENT_NOT_MAPPED"
        elif len(hits) > 1 or claimed[str(hits[0]["event_id"])] > 1:
            why[gp] = "EVENT_MAPPING_AMBIGUOUS"
        else:
            mapped[gp] = hits[0]
    return mapped, why


# ---- quote resolution ---------------------------------------------------------------------
def _tabs_for(stat):
    if stat in PITCHER_FAMILIES:
        return FAMILY_TABS["pitcher"]
    if stat == "hard_hit_105":
        return FAMILY_TABS["lasers"]
    if stat == "moonshot_420":
        return FAMILY_TABS["moonshots"]
    return FAMILY_TABS["one_sided"]


def _runner_matches(rec, market, pn, side, line):
    """Runners of `market` that are this player's requested side/line."""
    stat, out = rec["stat"], []
    for rn in market["runners"]:
        name = rn.get("runner_name") or ""
        if stat == "strikeouts":
            rside = (rn.get("result_type") or ("OVER" if name.endswith(" Over") else "UNDER" if name.endswith(" Under") else "")).upper()
            rname = re.sub(r"\s+(Over|Under)$", "", name).strip()
            rline = rn.get("handicap")
        elif stat == "pitcher_outs":
            m = _OUTS_RE.match(name.strip())
            if not m:
                continue
            rname, rside, rline = m.group(1), m.group(2).upper(), float(m.group(3))
        else:
            rname, rside, rline = name, "OVER", None
        if norm_name(rname) != pn or rside != side.upper():
            continue
        if stat in PITCHER_FAMILIES and (rline is None or round(float(rline), 1) != round(float(line), 1)):
            continue
        out.append(rn)
    return out


def _opposite(rec, market, pn, line):
    other = "under" if rec["market_side"] == "over" else "over"
    return _runner_matches(rec, market, pn, other, line)


def resolve_quote(rec, event, capture, cutoff):
    """(quote, None) or (None, reason). Exact-offer identity; fails closed."""
    done = capture.get("capture_completed_at")
    if not done:
        return None, "CAPTURE_INCOMPLETE"
    if utc(done) > cutoff:
        return None, "CAPTURE_COMPLETED_AFTER_CUTOFF"
    age = (cutoff - utc(done)).total_seconds()
    if age > MAX_QUOTE_AGE_S:
        return None, "QUOTE_STALE"
    tabs = _tabs_for(rec["stat"])
    if event.get("status") == "NOT_FETCHED_BUDGET" or any((event.get("tabs") or {}).get(t) != "OK" for t in tabs):
        return None, "EVENT_NOT_OBSERVED"
    if not event.get("fetch_completed_at") or utc(event["fetch_completed_at"]) > cutoff:
        return None, "EVENT_NOT_OBSERVED"
    stat, side, pn, needs = rec["stat"], rec["market_side"], norm_name(rec["player_name"]), _int_needs(rec["needs"])
    if stat == "strikeouts":
        markets = [m for m in event["markets"] if m.get("market_type") in K_TYPES]
    elif stat == "pitcher_outs":
        markets = [m for m in event["markets"] if (m.get("market_type") or "").endswith(OUTS_SUFFIX)]
    else:
        want = ONE_SIDED_TYPES.get((stat, needs))
        if want is None:
            return None, "MARKET_TYPE_UNSUPPORTED"
        markets = [m for m in event["markets"] if m.get("market_type") == want]
    hits = [(m, rn) for m in markets for rn in _runner_matches(rec, m, pn, side, rec.get("line"))]
    if not hits:
        return None, "MARKET_ABSENT"                   # event fully observed, offer not posted
    if len(hits) > 1:
        return None, "QUOTE_AMBIGUOUS"
    m, rn = hits[0]
    if rn.get("team_slug") and rn["team_slug"] != team_slug_of(rec.get("team")):
        return None, "QUOTE_TEAM_MISMATCH"
    if m.get("in_play"):
        return None, "IN_PLAY"
    if m.get("market_status") != "OPEN" or rn.get("runner_status") != "ACTIVE":
        return None, "MARKET_SUSPENDED"
    if rn.get("american") is None:
        return None, "QUOTE_NOT_PROVEN"
    q = {"book": BOOK, "capture_sha256": capture["capture_sha256"], "capture_completed_at": done,
         "quote_age_s": round(age, 3), "event_id": event["event_id"], "event_name": event["event_name"],
         "market_id": m["market_id"], "market_type": m["market_type"], "selection_id": rn["selection_id"],
         "runner_name": rn["runner_name"], "team_slug": rn.get("team_slug"), "side": side,
         "line": rec.get("line"), "needs": needs, "american": odds_int(rn["american"]), "q_devig": None,
         "devig": "ONE_SIDED_NO_DEVIG"}
    if stat in PITCHER_FAMILIES:
        opp = [o for o in _opposite(rec, m, pn, rec.get("line")) if o.get("runner_status") == "ACTIVE"
               and o.get("american") is not None]
        if len(opp) == 1:   # a REAL captured opposite side only; never invented
            a, b = implied(q["american"]), implied(opp[0]["american"])
            q["q_devig"], q["devig"] = a / (a + b), "TWO_SIDED_CAPTURED"
    return q, None


# ---- manifest -----------------------------------------------------------------------------
GATES = ("family", "schedule", "window", "duplicate_identity", "settlement", "qc", "lineup", "starter",
         "probability", "sample_n", "reliability", "recommendation_status", "shadow_version", "board_price",
         "board_fresh", "game_not_started", "event_mapping", "quote", "price_match", "price_band")


def build_manifest(shadow_board, capture, schedule, *, window, cutoff_utc, shadow_provenance=None):
    """No outcome input exists in this signature (tested)."""
    from shadow import verify_shadow_board
    verify_shadow_board(shadow_board)
    cutoff = utc(cutoff_utc)
    games = {str(g["game_pk"]): g for g in schedule["games"]}
    mapped, map_why = map_events(capture, schedule["games"])
    board_age = (cutoff - utc(shadow_board["board_generated_at"])).total_seconds()
    sealed_ok = utc(shadow_board["sealed_at"]) <= cutoff
    ident = Counter((str(r.get("game_pk")), str(r.get("player_id")), r.get("stat"), str(r.get("needs")),
                     r.get("market_side")) for r in shadow_board.get("records") or [])
    rows = []
    for r in sorted(shadow_board.get("records") or [], key=lambda x: x["candidate_id"]):
        gp = str(r.get("game_pk"))
        g = games.get(gp)
        if g is None or window_of(g["game_date"]) != window:
            if g is not None:
                continue              # another window's game: not part of this slate unit
        elig, mkt, pred, sel = (r.get("eligibility") or {}), (r.get("market") or {}), \
            (r.get("prediction") or {}), (r.get("selector") or {})
        stat, status = r.get("stat"), sel.get("recommendation_status")
        key = (gp, str(r.get("player_id")), stat, str(r.get("needs")), r.get("market_side"))
        g_ok = g is not None
        starter = None
        if stat in PITCHER_FAMILIES and g_ok:
            starter = str(r.get("player_id")) in {str(p) for p in (g.get("probable_pitcher_ids") or [])}
        checks = {
            "family": family_status(stat) in ("PRIMARY", "EXPLORATORY"),
            "schedule": g_ok,
            "window": g_ok and window_of(g["game_date"]) == window,
            "duplicate_identity": ident[key] == 1,
            "settlement": r.get("market_side") == "over" and _int_needs(r.get("needs")) is not None,
            "qc": elig.get("qc_status") == "kept",
            "lineup": elig.get("lineup_assumed") is False,
            "starter": True if stat not in PITCHER_FAMILIES else bool(starter),
            "probability": pred.get("hit_probability") is not None,
            "sample_n": pred.get("sample_n") not in (0, None),
            "reliability": pred.get("reliability") in PUBLIC_RELIABILITY,
            "recommendation_status": status is not None,
            "shadow_version": str((r.get("provenance") or {}).get("git_sha") or "")[:10] == shadow_board["provenance"]["git_sha"][:10]
                              and (r.get("provenance") or {}).get("model_version") == shadow_board["provenance"]["model_version"],
            "board_price": mkt.get("market_odds") is not None,
            "board_fresh": sealed_ok and board_age <= MAX_BOARD_AGE_S,
            "game_not_started": g_ok and utc(g["game_date"]) > cutoff,
            "event_mapping": gp in mapped,
        }
        quote, qreason = None, None
        if all(checks.values()):
            quote, qreason = resolve_quote(r, mapped[gp], capture, cutoff)
        checks["quote"] = quote is not None if all(checks.values()) else None
        checks["price_match"] = (quote is not None and odds_int(mkt["market_odds"]) == quote["american"]) if quote else None
        checks["price_band"] = (PRICE_BAND[0] <= implied(quote["american"]) <= PRICE_BAND[1]) if checks["price_match"] else None
        reason = None
        for gname in GATES:
            v = checks.get(gname)
            if v is False or v is None:
                reason = {"family": "FAMILY_" + family_status(stat), "event_mapping": map_why.get(gp, "EVENT_NOT_MAPPED"),
                          "quote": qreason, "price_match": "PRICE_MISMATCH_BOARD_VS_CAPTURE"}.get(gname, gname.upper() + "_FAIL")
                break
        row = {"candidate_id": r["candidate_id"], "game_pk": gp, "player_id": str(r.get("player_id")),
               "player_name": r.get("player_name"), "team": r.get("team"), "stat": stat,
               "family_status": family_status(stat), "needs": _int_needs(r.get("needs")), "line": r.get("line"),
               "side": r.get("market_side"), "game_start_utc": g["game_date"] if g_ok else None,
               "game_type": g.get("game_type") if g_ok else None, "board_odds": mkt.get("market_odds"),
               "p0": pred.get("hit_probability"), "reliability": pred.get("reliability"),
               "recommendation_status": status, "shadow_champion": status == "top_pick",
               "starter_status": (None if stat not in PITCHER_FAMILIES else
                                  "MLB_PROBABLE_LISTED_NOT_INDEPENDENTLY_CONFIRMED" if starter else "NOT_MLB_PROBABLE"),
               "gates": checks, "eligible": reason is None, "exclusion_reason": reason, "quote": quote}
        if quote:
            row["q_raw"] = implied(quote["american"])
        rows.append(row)
    covered = sorted({x["game_pk"] for x in rows if x["game_start_utc"]})
    starts = [games[gp]["game_date"] for gp in covered]
    prim = [x for x in rows if x["eligible"] and x["family_status"] == "PRIMARY"]
    m = {"manifest_version": MANIFEST_VERSION, "prereg_version": "v3", "date": shadow_board["date"], "window": window,
         "cutoff_utc": cutoff_utc, "shadow_board_sha256": shadow_board["board_sha256"],
         "shadow_board_generated_at": shadow_board["board_generated_at"], "shadow_provenance": shadow_provenance,
         "capture_sha256": capture["capture_sha256"], "capture_started_at": capture["capture_started_at"],
         "capture_completed_at": capture.get("capture_completed_at"), "capture_status": capture.get("status"),
         "schedule_sha256": canonical_sha256(schedule), "schedule_fetched_at": schedule.get("fetched_at"),
         "covered_games": covered, "earliest_first_pitch_utc": min(starts, key=utc) if starts else None,
         "event_mapping": {gp: {"event_id": ev["event_id"], "event_name": ev["event_name"]} for gp, ev in mapped.items()
                           if gp in covered},
         "event_mapping_failures": {gp: w for gp, w in map_why.items() if gp in covered},
         "rows": rows,
         "counts": {"records": len(rows), "eligible_primary": len(prim),
                    "shadow_champion_total": sum(x["shadow_champion"] for x in rows),
                    "shadow_champion_in_primary_universe": sum(x["shadow_champion"] for x in prim),
                    "shadow_champion_excluded_by_reason": dict(Counter(x["exclusion_reason"] for x in rows
                                                                       if x["shadow_champion"] and not x["eligible"])),
                    "exclusions": dict(Counter(x["exclusion_reason"] for x in rows if not x["eligible"]))}}
    m["manifest_sha256"] = canonical_sha256({k: v for k, v in m.items() if k != "manifest_sha256"})
    return m


def verify_manifest(m):
    if canonical_sha256({k: v for k, v in m.items() if k != "manifest_sha256"}) != m.get("manifest_sha256"):
        raise ValueError(f"{m.get('date')}/{m.get('window')}: manifest hash does not rebuild")
    if m.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("unexpected manifest version")
    return m
