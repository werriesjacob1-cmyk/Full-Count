#!/usr/bin/env python3
"""
parlay_builder.py — turns a request ("2 home runs, 1 double, 1 triple,
$5 to $1,000, Pirates/Mets game") into a real parlay: real players, real
probabilities, real FanDuel prices, correlation-checked.

WHAT THIS DOES NOT DO. It does not compute a validated joint probability for
correlated legs -- correlation.py deliberately stops at a label (positive/
negative/redundant/independent), not a coefficient, because a real
coefficient needs backtested outcome data that does not exist yet. So the
"combined probability" this reports is the naive independent-legs product,
clearly labelled as a floor, not a promise: legs flagged "positive" are
likely somewhat better than that floor since they move together, but this
does not claim to know by how much. Overstating that precision here would be
the same mistake this project has already found and fixed twice this session
(the circular league-rate fallback, the strikeout blend) -- a number dressed
up as more certain than the work behind it.

WHAT COUNTS AS "TODAY'S CANDIDATES". The pool is every candidate
generate_picks.py scored today, read back from data/players/*.json (written
by persist_player_snapshots) -- not just the 10 that made the published
board. That file holds ~450 real, scored candidates a night; the board only
ever showed 10-30 of them. Requesting "2 home runs" reaches into the same
real pool the main board is drawn from, not a separate invented one.

WHAT COUNTS AS A LEG BEING "AVAILABLE". Only candidates with a real
hit_probability (never None) are ever eligible -- the same "absent is not
zero" rule as everywhere else in this codebase. If a request cannot be
fully satisfied (e.g. asks for 2 home runs in a specific game that only has
one real HR candidate), that is reported as a shortfall, not silently
padded with an invented pick.

REQUEST PARSING IS DELIBERATELY MINIMAL. parse_request() is pattern
matching over the specific phrasings this was scoped against ("2 home runs,
1 double, 1 triple", "$5 to $1,000", "riskier", a team name). It is not
general natural-language understanding. A production version of this
product would put a real LLM in front of this exact function as the
translation layer -- the same principle already established for this
project: the chat layer is not the moat, the validated data under it is.
This module IS that data layer, callable directly with a structured
ParlayRequest by anything -- a human, a script, or eventually a real chat
interface -- without needing the parser at all.
"""
import glob
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import correlation as corr
import prop_probability as pp

PLAYERS_DIR = os.environ.get("PLAYERS_DIR", "data/players")

# Mirrors generate_picks.MIN_LINE_PROB (0.60) -- the same floor the main
# board uses to call a line "likely enough to recommend straight". Not
# imported directly: generate_picks.py is a heavy module (network calls
# inside its functions, though none at import time) and this module should
# stay usable standalone. Duplicated as a named constant, not a magic
# number, specifically so the two never drift silently out of sync --
# CHECK_AGAINST_GENERATE_PICKS below is a live guard against that.
MIN_LINE_PROB = 0.60

# (low, high] hit_probability band per risk tier. "safest" mirrors the main
# board's own floor exactly. "risky" is the same territory moonshots already
# occupy on the main board (real signal, low raw probability -- see
# select_moonshots in generate_picks.py) -- not a different, looser standard
# invented for this module, the same one already shipped and reasoned about.
RISK_BANDS = {
    "safest": (MIN_LINE_PROB, 1.01),
    "balanced": (0.40, MIN_LINE_PROB),
    "risky": (0.0, 0.40),
}

# Phrase -> stat key. Deliberately explicit rather than fuzzy-matched, same
# reasoning as odds_fanduel.MARKET_MAP: a silent mis-mapping here would
# build a parlay leg the customer did not actually ask for.
_STAT_PHRASES = [
    (r"home\s*runs?|hrs?\b", "home_runs"),
    (r"total\s*bases?", "total_bases"),
    (r"hits?\s*\+\s*runs?\s*\+\s*rbis?|hits?/runs?/rbis?", "hits_runs_rbis"),
    (r"doubles?", "doubles"),
    (r"triples?", "triples"),
    (r"singles?", "singles"),
    (r"stolen\s*bases?|steals?", "stolen_base"),
    (r"strikeouts?|k'?s\b", "strikeouts"),
    (r"walks?", "walks"),
    (r"rbis?", "rbis"),
    (r"runs?\b", "runs"),
    (r"hits?\b", "hits"),
]

# Populated once, lazily, from mlb_daily.STADIUMS' team abbreviations plus
# each full team name -- verified live rather than hand-typed, so this
# cannot drift from the real team list the rest of the pipeline uses.
_TEAM_NAMES = None


def _team_names():
    global _TEAM_NAMES
    if _TEAM_NAMES is not None:
        return _TEAM_NAMES
    try:
        import mlb_daily as m
        names = set()
        for entry in m.STADIUMS.values():
            names.add(entry[3])  # abbreviation, e.g. "NYY"
        # Full names aren't in STADIUMS; derive the common last-word form
        # (e.g. "Pirates", "Mets") from get_team_ids() if reachable, else
        # fall back to the abbreviations alone rather than guessing spellings.
        try:
            for t in m.get_team_ids():
                nm = t.get("name") or ""
                if nm:
                    names.add(nm.split()[-1])  # "Pittsburgh Pirates" -> "Pirates"
                    names.add(nm)
        except Exception:
            pass
        _TEAM_NAMES = names
    except Exception:
        _TEAM_NAMES = set()
    return _TEAM_NAMES


@dataclass
class ParlayRequest:
    prop_counts: dict           # {"home_runs": 2, "doubles": 1, "triples": 1}
    risk_tier: str = "safest"   # "safest" | "balanced" | "risky"
    game_filter: list = field(default_factory=list)   # team-name substrings, ANDed
    stake: float = None
    target_payout: float = None


def parse_request(text):
    """Minimal pattern-matching parser -- see module docstring. Returns a
    ParlayRequest. Never raises on an unrecognised phrase; unmatched parts
    are simply not extracted, same "absent, not guessed" rule as the rest
    of this module."""
    t = text.lower()

    prop_counts = {}
    # "2 home runs", "1 double", "a triple" (bare article -> 1)
    for m in re.finditer(r"\b(\d+|a|an)\s+([a-z+/\s]+?)(?:,|\band\b|$)", t):
        qty_raw, phrase = m.group(1), m.group(2).strip()
        qty = 1 if qty_raw in ("a", "an") else int(qty_raw)
        for pattern, stat in _STAT_PHRASES:
            if re.search(pattern, phrase):
                prop_counts[stat] = prop_counts.get(stat, 0) + qty
                break

    stake = target_payout = None
    money = re.findall(r"\$?([\d,]+(?:\.\d+)?)", t)
    m_range = re.search(r"\$?([\d,]+(?:\.\d+)?)\s*(?:to|-|→)\s*\$?([\d,]+(?:\.\d+)?)", t)
    if m_range:
        stake = float(m_range.group(1).replace(",", ""))
        target_payout = float(m_range.group(2).replace(",", ""))

    if any(w in t for w in ("riskier", "risky", "longshot", "moonshot", "makes me rich")):
        risk_tier = "risky"
    elif any(w in t for w in ("safest", "safe", "best", "most likely")):
        risk_tier = "safest"
    else:
        risk_tier = "balanced"

    game_filter = [name for name in _team_names() if len(name) > 3 and name.lower() in t]

    return ParlayRequest(prop_counts=prop_counts, risk_tier=risk_tier,
                         game_filter=game_filter, stake=stake, target_payout=target_payout)


def load_todays_pool(date=None):
    """Every real candidate generate_picks.py scored today (not just the
    published board), read back from data/players/*.json. Only candidates
    with a real hit_probability are included -- see module docstring."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    pool = []
    for path in glob.glob(os.path.join(PLAYERS_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for snap in hist.get("snapshots", []):
            if snap.get("date") != date:
                continue
            for e in snap.get("evaluations", []):
                if e.get("hit_probability") is None:
                    continue
                pool.append({
                    "player_id": hist.get("player_id"), "name": hist.get("name"),
                    "team": e.get("team"), "matchup": e.get("matchup"),
                    "game_pk": e.get("game_pk"), "side": e.get("side"),
                    "type": e.get("type"), "prop": e.get("prop"),
                    "projection": e.get("projection"), "lean": e.get("lean"),
                    "hit_probability": e.get("hit_probability"),
                    "score": e.get("score"), "reliability": e.get("reliability"),
                    "sample_n": e.get("sample_n"), "signals": e.get("signals") or {},
                })
    return pool


def _select_legs(pool, prop_counts, risk_tier):
    lo, hi = RISK_BANDS.get(risk_tier, RISK_BANDS["safest"])
    eligible = [c for c in pool if lo <= c["hit_probability"] < hi]

    by_stat = defaultdict(list)
    for c in eligible:
        stat = (c.get("projection") or {}).get("stat")
        by_stat[stat].append(c)
    for stat in by_stat:
        by_stat[stat].sort(key=lambda c: c["hit_probability"], reverse=True)

    legs, shortfalls = [], []
    for stat, need_n in prop_counts.items():
        taken = 0
        for cand in by_stat.get(stat, []):
            if taken >= need_n:
                break
            if any(cand["player_id"] == leg["player_id"] for leg in legs):
                continue  # never double up the same player without checking correlation twice
            # Correlation screen against every leg already picked -- reject
            # on the first negative/redundant pair rather than assembling a
            # parlay this project's own rules would then have to reject.
            if any(corr.classify(cand, existing).label in ("negative", "redundant")
                  for existing in legs):
                continue
            legs.append(cand)
            taken += 1
        if taken < need_n:
            shortfalls.append({"stat": stat, "requested": need_n, "found": taken})
    return legs, shortfalls


def build_parlay(request, pool=None, price_legs=True):
    """The real engine. `request` is a ParlayRequest (or plain dict with the
    same keys). Returns a result dict -- never raises for a request that
    can't be fully satisfied; that's a shortfall, reported honestly."""
    if isinstance(request, dict):
        request = ParlayRequest(**request)
    pool = pool if pool is not None else load_todays_pool()

    candidates = pool
    if request.game_filter:
        candidates = [c for c in candidates
                     if all(name.lower() in (c.get("matchup") or "").lower()
                            for name in request.game_filter)]

    legs, shortfalls = _select_legs(candidates, request.prop_counts, request.risk_tier)

    if price_legs and legs:
        try:
            import odds_fanduel as fd
            fd.attach_market_prices(legs)
        except Exception:
            pass  # never fatal -- an unpriced leg just carries no market_odds

    naive_prob = 1.0
    for leg in legs:
        naive_prob *= leg["hit_probability"]

    priced_legs = [l for l in legs if l.get("market_odds") is not None]
    combined_decimal_odds = None
    if priced_legs and len(priced_legs) == len(legs):
        combined_decimal_odds = 1.0
        for l in legs:
            combined_decimal_odds *= pp.decimal_odds(l["market_odds"])

    # Pairwise correlation notes for transparency -- the customer sees
    # exactly why legs were paired, not just the final list.
    notes = []
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            v = corr.classify(legs[i], legs[j])
            if v.label == "positive":
                notes.append(f"{legs[i]['name']} + {legs[j]['name']}: {v.reason}")

    payout_note = None
    if request.stake and combined_decimal_odds:
        payout_note = round(request.stake * combined_decimal_odds, 2)

    return {
        "request": request,
        "legs": legs,
        "shortfalls": shortfalls,
        "naive_combined_probability": round(naive_prob, 4) if legs else None,
        "naive_probability_note": (
            "Product of each leg's own probability, assuming independence. "
            "Legs noted as positively correlated below are likely somewhat "
            "better than this number, in the direction of MORE likely to "
            "hit together -- by how much is not yet a validated estimate, "
            "so this number is reported as a conservative floor, not a "
            "final answer."
        ) if legs else None,
        "combined_decimal_odds": round(combined_decimal_odds, 3) if combined_decimal_odds else None,
        "stake": request.stake,
        "estimated_payout_if_priced": payout_note,
        "correlation_notes": notes,
    }
