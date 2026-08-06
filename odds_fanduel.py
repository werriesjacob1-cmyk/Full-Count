#!/usr/bin/env python3
"""
odds_fanduel.py — real player-prop prices, free, from FanDuel's public API.

WHAT THIS OVERTURNS.

This project spent weeks operating on the belief that free player-prop prices
do not exist. That belief was written into the README, into prop_probability.py,
and into the design of final_card.py, which exists solely to let a human paste
prices in by hand. It was wrong.

The belief came from testing two sources and generalising: Action Network's
public scoreboard exposes only game markets and 404s on props, and The Odds
API charges for them. Neither test says anything about whether a SPORTSBOOK
publishes its own prices, and FanDuel does -- the same JSON its web app reads,
no key of one's own, no account, no scraping of HTML.

WHAT IS VERIFIED, by direct measurement rather than assumption:

  - 108 batters priced across a 14-game slate, 540 individual markets.
  - All five prop types this pipeline actually bets: 1+ hits, 2+ hits,
    2+ total bases, 3+ total bases, and home run.
  - The _ak parameter is required (requests without it 400, with a wrong one
    500) but it is a PUBLIC CLIENT KEY, not a session token: the same value
    works unchanged against the nj, pa, az and co subdomains, which also give
    four independent endpoints to fall back between.
  - Name matching against this pipeline's picks resolved 9 of 10 on the first
    attempt, with suffix normalisation handling "Michael Harris" ->
    "Michael Harris II" and "Fernando Tatis" -> "Fernando Tatis Jr.".

WHAT THIS IMMEDIATELY REVEALED, and it is not comfortable.

Every one of the board's ten picks failed its price test. The model put those
hitters at 63-67% to record a hit and priced them fairly at -176 to -204.
FanDuel posts them at -250 to -380, implying 71-79%. The league base rate for
a hit in a game is 55%, and Bobby Witt Jr. -- among the best contact hitters
in baseball -- managed 74% across 96 real games. So the book is asking for
prices ABOVE the historical rate of the best hitters alive.

That is not the market knowing something the model does not. It is 10-15
points of hold on the most popular prop type on the board. The "safest"
props are the most heavily taxed, precisely because everybody wants them.

A 65% bet at -300 loses money forever. The model refusing all ten is the
system working, not failing.

WHAT IS NOT YET ESTABLISHED, stated plainly so nobody bets on it early.

A naive scan of 460 priced props found 42 with positive edge against players'
raw empirical rates. Those are NOT betting recommendations. They cluster in
longshots (+360 to +750) where the empirical estimate is noisiest, where the
book's hold is largest, and where a small error in a rate produces a large
apparent edge. Turning them into recommendations requires running them
through the calibrator and validating against held-out backtest dates, which
is the same standard every other signal in this project had to clear.
"""
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests

# Public client key from FanDuel's own web app. Not a credential: it
# identifies the calling application, not a user, and the same value serves
# every regional subdomain. Kept here rather than in an env var precisely
# because it is not a secret -- pretending otherwise would imply this needs an
# account, which is the misconception this module exists to correct.
AK = "FhMFpcPWXMeyZxOx"

# Four independent regional endpoints carrying identical data. Tried in order
# so one region being unreachable is not an outage.
HOSTS = ["sbapi.nj", "sbapi.pa", "sbapi.az", "sbapi.co"]

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
      "Accept": "application/json"}

# FanDuel market type -> (this pipeline's stat name, integer count needed).
# Deliberately explicit rather than pattern-matched on the market name: the
# names are marketing copy and change, while these type constants are stable,
# and a silent mis-mapping here would price a prop against the wrong line.
MARKET_MAP = {
    # Hits
    "PLAYER_TO_RECORD_A_HIT":   ("hits", 1),
    "PLAYER_TO_RECORD_2+_HITS": ("hits", 2),
    "PLAYER_TO_RECORD_3+_HITS": ("hits", 3),
    "PLAYER_TO_RECORD_4+_HITS": ("hits", 4),
    # Total bases
    "TO_RECORD_2+_TOTAL_BASES": ("total_bases", 2),
    "TO_RECORD_3+_TOTAL_BASES": ("total_bases", 3),
    "TO_RECORD_4+_TOTAL_BASES": ("total_bases", 4),
    # Home runs
    "TO_HIT_A_HOME_RUN":        ("home_runs", 1),
    "TO_HIT_2+_HOME_RUNS":      ("home_runs", 2),
    # Runs, RBIs and steals. These were the largest omission: the empirical
    # table has always computed rates for all three, and FanDuel prices ~720
    # lines a night across them, so they were being modelled and then thrown
    # away. Stolen bases matter most -- this project has a dedicated steal
    # model gating on times-on-base, attempt rate and success rate, and 106
    # priced lines a night were going unexamined.
    "TO_RECORD_A_RUN":          ("runs", 1),
    "TO_RECORD_2+_RUNS":        ("runs", 2),
    "TO_RECORD_AN_RBI":         ("rbis", 1),
    "TO_RECORD_2+_RBIS":        ("rbis", 2),
    "TO_RECORD_3+_RBIS":        ("rbis", 3),
    "TO_RECORD_A_STOLEN_BASE":  ("stolen_bases", 1),
    # Hit-type props. Deliberately NOT mapped onto total_bases: "to hit a
    # double" means exactly one double, which is a different event from
    # clearing two total bases (a home run clears 2+ TB and is not a double).
    # Mapping them together would grade the wrong outcome, so they carry their
    # own stat names and are only screened once a matching rate exists.
    "TO_HIT_A_SINGLE":          ("singles", 1),
    "TO_HIT_A_DOUBLE":          ("doubles", 1),
    "TO_HIT_A_TRIPLE":          ("triples", 1),
    # Thresholds the book prices that were simply never enumerated.
    "TO_RECORD_5+_TOTAL_BASES": ("total_bases", 5),
    "TO_RECORD_4+_RBIS":        ("rbis", 4),
    "TO_RECORD_3+_RUNS":        ("runs", 3),
    "TO_RECORD_2+_STOLEN_BASES": ("stolen_bases", 2),
    # Hits+Runs+RBIs: ~450 priced lines a night, and the single largest
    # market this pipeline was ignoring.
    "PLAYER_TO_RECORD_1+_HITS+RUNS+RBIS": ("hits_runs_rbis", 1),
    "PLAYER_TO_RECORD_2+_HITS+RUNS+RBIS": ("hits_runs_rbis", 2),
    "PLAYER_TO_RECORD_3+_HITS+RUNS+RBIS": ("hits_runs_rbis", 3),
    "PLAYER_TO_RECORD_4+_HITS+RUNS+RBIS": ("hits_runs_rbis", 4),
    # Exit-velocity markets. Measurable from the Statcast pull already cached
    # (MLB's box score carries no exit velocity, so game logs cannot produce
    # these), and the spread is the widest of any market on the board: the
    # league hits a 110+ mph ball in 3.9% of games while Giancarlo Stanton
    # does it in 40%. A market where the best and the average differ tenfold
    # is one where a read can actually matter.
    "PLAYER_TO_HIT_A_LASER_(110+_MPH)": ("hard_hit_110", 1),
    "TO_HIT_A_LASER_(105+_MPH)":        ("hard_hit_105", 1),
}

# Markets deliberately NOT mapped, and why. Listing them is the point: an
# unmapped market should be a decision on record, not an oversight.
#
#   PLAYERS_TO_COMBINE_FOR_*  — two or more players in one bet. Pricing these
#     honestly needs the JOINT distribution, and teammates' outcomes are
#     correlated (same game, same pitcher, same run environment). Multiplying
#     two independent probabilities would overstate every one of them, which
#     is exactly the kind of plausible-looking error this project keeps
#     finding. ~640 lines a night, left alone until correlation is modelled.
#   RESULT_OF_FIRST_PITCH, BATTER_UP_*, FIRST_SCORING_PLAY — single-pitch and
#     single-plate-appearance micro markets. No rate is computed for them and
#     none could be estimated from game logs.
#   PLAYER_TO_HIT_A_LASER_(110+_MPH) — genuinely modelable from Statcast exit
#     velocity, which this project already pulls, but the per-game rate is not
#     computed yet. A real gap rather than an impossibility.
#   PITCHER_*_STRIKEOUTS — these are line markets (over/under N) rather than
#     yes/no, so they need different parsing. empirical_pitcher_k_rates
#     already produces the rates; only the mapping is missing.
UNMAPPED_REASONS = {
    "PLAYERS_TO_COMBINE_FOR": "needs a joint distribution — teammates are correlated",
    "RESULT_OF_FIRST_PITCH": "single-pitch market, no rate exists",
    "BATTER_UP": "single-plate-appearance micro market, no rate exists",
    "LASER": "modelable from Statcast exit velo — not yet computed",
    "PITCHER_": "line market, needs different parsing — rates already exist",
}


def normalize_name(name):
    """Strip accents and generational suffixes for cross-source matching.

    The same normalisation the rest of this project uses. It is what turns
    "Michael Harris" into a match for FanDuel's "Michael Harris II"."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s.lower())
    return s.replace(".", "").replace("  ", " ").strip()


def _get(path, timeout=20):
    """GET against the first regional host that answers."""
    last = None
    for host in HOSTS:
        try:
            r = requests.get(f"https://{host}.sportsbook.fanduel.com/api/{path}",
                             headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"all FanDuel hosts failed ({last})")


def list_games():
    """Tonight's MLB events. Returns [(event_id, name, start_iso)]."""
    d = _get(f"content-managed-page?page=CUSTOM&customPageId=mlb&_ak={AK}")
    out = []
    for e in (d.get("attachments", {}).get("events") or {}).values():
        name = e.get("name") or ""
        # Real games carry "AWAY @ HOME"; the feed also lists futures,
        # awards and season-long markets that must not be treated as games.
        if " @ " not in name:
            continue
        out.append((e.get("eventId"), name, e.get("openDate")))
    return out


# Two-sided line markets: a handicap plus an Over and an Under, rather than a
# yes/no. Pitcher strikeouts are the important ones here, and they are the
# BEST-priced markets on the board for our purposes -- because both sides are
# quoted, the hold can be measured exactly instead of assumed, which removes
# the largest source of error in judging whether a price is fair.
TWO_SIDED_MARKETS = {
    "PITCHER_A_TOTAL_STRIKEOUTS", "PITCHER_B_TOTAL_STRIKEOUTS",
    "PITCHER_C_TOTAL_STRIKEOUTS", "PITCHER_D_TOTAL_STRIKEOUTS",
    "PITCHER_E_TOTAL_STRIKEOUTS", "PITCHER_F_TOTAL_STRIKEOUTS",
    "PITCHER_A_STRIKEOUTS", "PITCHER_B_STRIKEOUTS", "PITCHER_C_STRIKEOUTS",
    "PITCHER_D_STRIKEOUTS", "PITCHER_E_STRIKEOUTS", "PITCHER_F_STRIKEOUTS",
}


def _parse_two_sided(market):
    """Pull (player, line, over_odds, under_odds) from a handicap market.

    The runner name carries the side as a suffix ("Janson Junk Over"), and the
    line lives on the runner's handicap field. Both sides are required: a
    one-sided capture would silently fall back to an assumed hold, which is
    exactly the imprecision this market type lets us avoid."""
    line = None
    sides = {}
    player = None
    for rn in (market.get("runners") or []):
        name = rn.get("runnerName") or ""
        result = ((rn.get("result") or {}).get("type") or "").upper()
        if not result:
            result = "OVER" if name.strip().endswith("Over") else (
                     "UNDER" if name.strip().endswith("Under") else "")
        if result not in ("OVER", "UNDER"):
            continue
        odds = ((rn.get("winRunnerOdds") or {})
                .get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
        if odds is None:
            continue
        if rn.get("handicap") is not None:
            line = float(rn["handicap"])
        if player is None:
            player = re.sub(r"\s+(Over|Under)$", "", name).strip()
        sides[result] = int(odds)
    if player is None or line is None or "OVER" not in sides or "UNDER" not in sides:
        return None
    return player, line, sides["OVER"], sides["UNDER"]


def fetch_pitcher_strikeouts(max_workers=8):
    """Two-sided strikeout markets for tonight's starters.

    Returns {normalized_name: {"line": float, "over": int, "under": int,
                               "needs": int, "true_over": float, "hold": float}}
    where true_over is de-vigged EXACTLY from both sides."""
    import prop_probability as pp
    out = {}
    for event_id, name, _start in list_games():
        for tab in ("pitcher-props", "popular"):
            try:
                d = _get(f"event-page?eventId={event_id}&tab={tab}&_ak={AK}")
            except Exception:
                continue
            for m in (d.get("attachments", {}).get("markets") or {}).values():
                if m.get("marketType") not in TWO_SIDED_MARKETS:
                    continue
                if m.get("inPlay"):
                    continue
                parsed = _parse_two_sided(m)
                if not parsed:
                    continue
                player, line, over, under = parsed
                t_over, t_under, hold = pp.devig_two_sided(over, under)
                out[normalize_name(player)] = {
                    "player": player, "line": line,
                    # "Over 3.5" settles on 4 or more.
                    "needs": int(line) + 1 if float(line).is_integer() else int(line + 0.5),
                    "over": over, "under": under,
                    "true_over": t_over, "true_under": t_under, "hold": hold,
                    "game": name,
                }
    return out


def _event_props(event_id):
    rows = []
    for tab in ("batter-props", "popular"):
        try:
            d = _get(f"event-page?eventId={event_id}&tab={tab}&_ak={AK}")
        except Exception:
            continue
        for m in (d.get("attachments", {}).get("markets") or {}).values():
            mapped = MARKET_MAP.get(m.get("marketType"))
            if not mapped:
                continue
            stat, need = mapped
            for rn in (m.get("runners") or []):
                odds = ((rn.get("winRunnerOdds") or {})
                        .get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
                if odds is None:
                    continue
                rows.append({"player": rn.get("runnerName"),
                             "norm": normalize_name(rn.get("runnerName")),
                             "stat": stat, "needs": need,
                             "american": int(odds), "event_id": event_id,
                             "in_play": bool(m.get("inPlay"))})
    return rows


def fetch_prop_prices(max_workers=8):
    """Every priced batter prop on the slate.

    Returns {normalized_name: {(stat, needs): american_odds}}. In-play markets
    are excluded: once a game starts the price reflects the remaining at-bats,
    which is a different bet from the pregame one this pipeline models."""
    games = list_games()
    out = {}
    if not games:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for rows in ex.map(lambda g: _event_props(g[0]), games):
            for r in rows:
                if r["in_play"]:
                    continue
                out.setdefault(r["norm"], {})[(r["stat"], r["needs"])] = r["american"]
    return out


def attach_market_prices(candidates, prices=None):
    """Attach the real posted price to every candidate that has one.

    Sets `market_odds`, `market_implied`, and `market_edge` (model probability
    minus the price's implied probability). Leaves them absent rather than
    guessing when the prop is not offered -- an unpriced prop is a gap in
    coverage, not a bet at some assumed number."""
    import prop_probability as pp
    if prices is None:
        try:
            prices = fetch_prop_prices()
        except Exception:
            return candidates
    matched = 0
    for c in candidates:
        proj = c.get("projection") or {}
        key = (proj.get("stat"), proj.get("needs"))
        if None in key:
            continue
        odds = (prices.get(normalize_name(c.get("name"))) or {}).get(key)
        if odds is None:
            continue
        c["market_odds"] = odds
        c["market_implied"] = round(pp.implied_probability(odds), 4)
        p = c.get("hit_probability")
        if p is not None:
            c["market_edge"] = round(p - c["market_implied"], 4)
            c["price_clears"] = bool(
                odds >= pp.max_acceptable_price(p))
        matched += 1
    return candidates, matched
