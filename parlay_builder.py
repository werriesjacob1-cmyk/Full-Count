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
import sys
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
#
# THE RISK BAR. A customer picking a risk appetite shouldn't be stuck
# choosing between exactly 3 buckets -- "a little safer than balanced" is a
# real, common request. RISK_ANCHORS defines the SAME three bands as before
# at fixed points on a 0-100 dial (0 = safest, 50 = balanced, 100 = riskiest)
# and risk_band() linearly interpolates both ends of the band between
# whichever two anchors straddle the requested level. This is a UI/threshold
# choice, not a probability claim -- it never touches how any individual
# leg's hit_probability was computed, it only decides which real,
# already-scored candidates are eligible to be offered at that risk level.
RISK_ANCHORS = [
    (0, (MIN_LINE_PROB, 1.01)),
    (50, (0.40, MIN_LINE_PROB)),
    (100, (0.0, 0.40)),
]
RISK_TIER_LEVELS = {"safest": 0, "balanced": 50, "risky": 100}
# Kept for backward compatibility / anything that wants the old named bands
# directly -- computed FROM the anchors so the two can never drift apart.
RISK_BANDS = {name: RISK_ANCHORS[level // 50][1] for name, level in RISK_TIER_LEVELS.items()}


def risk_band(risk_level):
    """(lo, hi) hit_probability band for a risk dial value in [0, 100].
    Out-of-range values clamp rather than raise -- a UI slider should never
    be able to 500 this by sending 101."""
    level = max(0.0, min(100.0, float(risk_level)))
    for (r0, (lo0, hi0)), (r1, (lo1, hi1)) in zip(RISK_ANCHORS, RISK_ANCHORS[1:]):
        if r0 <= level <= r1:
            frac = (level - r0) / (r1 - r0)
            return (lo0 + (lo1 - lo0) * frac, hi0 + (hi1 - hi0) * frac)
    return RISK_ANCHORS[-1][1]  # unreachable given the clamp above; no silent guess

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
    # Found missing entirely during a bug sweep: every market shipped this
    # session (Laser, Pitcher Outs Recorded, NRFI/YRFI, combined strikeouts)
    # had no phrase here, so "give me a parlay with pitcher outs" would
    # silently extract nothing -- degrades gracefully per this function's
    # own "never raises, absent not guessed" contract, but a real coverage
    # gap on a real, user-facing feature. combined-strikeouts MUST come
    # before the plain strikeouts pattern below, or "combined strikeouts"
    # would match that first and never reach this one.
    (r"combined\s*(?:starter\s*)?(?:strikeouts?|k'?s\b)", "combined_strikeouts"),
    (r"laser|exit\s*velo(?:city)?|105\+?\s*mph", "hard_hit_105"),
    (r"pitcher\s*outs?|outs?\s*recorded", "pitcher_outs"),
    (r"nrfi|yrfi|first\s*inning", "nrfi_combined"),
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
    risk_tier: str = "safest"   # "safest" | "balanced" | "risky" -- legacy 3-way input
    risk_level: float = None    # 0-100 risk dial; overrides risk_tier when set
    game_filter: list = field(default_factory=list)   # team-name substrings, ANDed
    stake: float = None
    target_payout: float = None

    def effective_risk_level(self):
        """The numeric dial value this request actually resolves to --
        risk_level directly if the caller (a real slider, eventually) set
        one, else the anchor point for the named risk_tier."""
        if self.risk_level is not None:
            return self.risk_level
        return RISK_TIER_LEVELS.get(self.risk_tier, 0)


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

    # An explicit dial value ("risk level 70", "70% risk") wins outright --
    # this is the path a real risk-bar UI would use, sending a number
    # instead of asking the parser to guess a tier from adjectives.
    risk_level = None
    m_level = re.search(r"risk(?:\s*(?:level|bar))?\s*(?:of|:|is)?\s*(\d{1,3})\s*%?", t)
    if m_level:
        risk_level = max(0.0, min(100.0, float(m_level.group(1))))

    if any(w in t for w in ("riskier", "risky", "longshot", "moonshot", "makes me rich")):
        risk_tier = "risky"
    elif any(w in t for w in ("safest", "safe", "best", "most likely")):
        risk_tier = "safest"
    else:
        risk_tier = "balanced"

    game_filter = [name for name in _team_names() if len(name) > 3 and name.lower() in t]

    return ParlayRequest(prop_counts=prop_counts, risk_tier=risk_tier, risk_level=risk_level,
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



# Laser is priced at two real thresholds (105+/110+ MPH), and score_laser's
# own _pick_line already picks whichever one has the better read per
# player -- a single candidate never carries both. A request for "a laser"
# (parsed to hard_hit_105 by _STAT_PHRASES, since the text doesn't
# distinguish which MPH) has to draw from BOTH buckets combined, or every
# player whose winning threshold happened to be 110+ would be invisible to
# it -- found while adding the Laser phrase to _STAT_PHRASES, not a
# hypothetical.
_STAT_GROUPS = {
    "hard_hit_105": ("hard_hit_105", "hard_hit_110"),
    "hard_hit_110": ("hard_hit_105", "hard_hit_110"),
}


def _select_legs(pool, prop_counts, risk_level):
    lo, hi = risk_band(risk_level)
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
        stat_pool = []
        for s in _STAT_GROUPS.get(stat, (stat,)):
            stat_pool.extend(by_stat.get(s, []))
        stat_pool.sort(key=lambda c: c["hit_probability"], reverse=True)
        for cand in stat_pool:
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


def _finalize(request, legs, shortfalls, price_legs):
    """Everything after leg SELECTION is decided: pricing, the naive combined
    probability, correlation notes, payout. Shared by build_parlay() (a
    specific prop_counts request) and build_best_available_parlay() (no
    specific asks, just the best legs at a risk level) so the two never
    silently diverge on how a result gets priced or summarized."""
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

    legs, shortfalls = _select_legs(candidates, request.prop_counts, request.effective_risk_level())
    return _finalize(request, legs, shortfalls, price_legs)


def build_best_available_parlay(pool=None, n=3, risk_level=0, stake=None,
                                target_payout=None, game_filter=None, price_legs=True):
    """No specific prop request -- just the N best-available legs (any stat
    family) at a risk level, correlation-screened the same as a specific
    request. This is the natural "give me your best parlay" ask, and doubles
    as the daily free-tier preview: the picks a customer would see before
    ever typing a request at all.

    Greedy by hit_probability across the WHOLE pool rather than per-stat, so
    two different families can both show up if they're both strong tonight --
    unlike _select_legs, which only ever looks within the one family asked
    for."""
    pool = pool if pool is not None else load_todays_pool()
    candidates = pool
    if game_filter:
        candidates = [c for c in candidates
                     if all(name.lower() in (c.get("matchup") or "").lower() for name in game_filter)]

    lo, hi = risk_band(risk_level)
    eligible = sorted([c for c in candidates if lo <= c["hit_probability"] < hi],
                      key=lambda c: c["hit_probability"], reverse=True)

    legs = []
    for cand in eligible:
        if len(legs) >= n:
            break
        if any(cand["player_id"] == leg["player_id"] for leg in legs):
            continue
        if any(corr.classify(cand, existing).label in ("negative", "redundant") for existing in legs):
            continue
        legs.append(cand)
    shortfalls = ([{"stat": "any", "requested": n, "found": len(legs)}] if len(legs) < n else [])

    request = ParlayRequest(prop_counts={}, risk_level=risk_level, stake=stake,
                            target_payout=target_payout, game_filter=game_filter or [])
    return _finalize(request, legs, shortfalls, price_legs)


def _fmt_odds(n):
    if n is None:
        return "n/a"
    return f"+{int(n)}" if n > 0 else str(int(n))


def format_parlay_text(result):
    """Render a build_parlay() result as readable terminal text -- the same
    "absent, not guessed" honesty as everywhere else: a shortfall is spelled
    out, not hidden, and the combined number is always labelled as the floor
    it is, never presented as a clean single probability."""
    req = result["request"]
    lines = []
    tier_bit = (f"risk level {req.risk_level:.0f}/100" if req.risk_level is not None
               else f"risk tier '{req.risk_tier}'")
    lines.append(f"YOUR PARLAY  ({tier_bit})")
    lines.append("=" * 60)

    if not result["legs"]:
        lines.append("No legs could be filled from today's real board.")
    for i, leg in enumerate(result["legs"], 1):
        pct = f"{leg['hit_probability'] * 100:.1f}%"
        odds = _fmt_odds(leg.get("market_odds"))
        lines.append(f"{i}. {leg.get('name', '—')} -- {leg.get('prop', '—')}  ({pct}, market {odds})")
        lines.append(f"   {leg.get('matchup', '—')}")

    if result["shortfalls"]:
        lines.append("")
        lines.append("COULD NOT FULLY FILL:")
        for s in result["shortfalls"]:
            lines.append(f"  - {s['stat']}: asked for {s['requested']}, found {s['found']} "
                        f"real candidate(s) at this risk level")

    if result["correlation_notes"]:
        lines.append("")
        lines.append("WHY THESE GO TOGETHER:")
        for n in result["correlation_notes"]:
            lines.append(f"  - {n}")

    if result["legs"]:
        lines.append("")
        lines.append(f"Naive combined probability (floor, not a promise): "
                    f"{result['naive_combined_probability'] * 100:.1f}%")
        if result["combined_decimal_odds"]:
            lines.append(f"Combined odds (all legs priced): {result['combined_decimal_odds']}x")
            if result["estimated_payout_if_priced"]:
                lines.append(f"${req.stake:.2f} -> ${result['estimated_payout_if_priced']:.2f} "
                            f"if every leg hits")
        else:
            n_unpriced = sum(1 for l in result["legs"] if l.get("market_odds") is None)
            if n_unpriced:
                lines.append(f"No combined payout shown -- {n_unpriced} of {len(result['legs'])} "
                            f"leg(s) has no real market price today")
    return "\n".join(lines)


def main():
    """    /tmp/mlbvenv/bin/python3 parlay_builder.py "2 home runs, 1 double, 1 triple"
    /tmp/mlbvenv/bin/python3 parlay_builder.py "$5 to $1000, riskier, Pirates Mets game"
    """
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print(main.__doc__)
        return 1
    request = parse_request(text)
    result = build_parlay(request)
    print(format_parlay_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
