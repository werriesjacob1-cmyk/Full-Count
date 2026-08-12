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


_OUTS_RUNNER_RE = re.compile(r"^(.*?)\s+(Over|Under)\s+([\d.]+)$")


def _parse_outs_runner(name):
    """"Keider Montero Over 15.5" -> ("Keider Montero", "OVER", 15.5).

    A DIFFERENT shape than _parse_two_sided expects, verified live before
    writing this rather than assumed: this market's runner names embed the
    side mid-string with the line as a trailing number, not a suffix side
    with the line in a separate `handicap` field (handicap is 0/unused
    here). Reusing _parse_two_sided against this market would have matched
    zero runners silently -- name.strip().endswith("Over"/"Under") is
    false for "...Over 15.5", so every runner would have been skipped
    without an error."""
    m = _OUTS_RUNNER_RE.match((name or "").strip())
    if not m:
        return None
    return m.group(1).strip(), m.group(2).upper(), float(m.group(3))


def fetch_pitcher_outs():
    """Two-sided "Pitcher Outs Recorded" markets for tonight's starters --
    market type suffix _OUTS_RECORDED_SB (PITCHER_A/B/C/D/E/F_..., same
    per-pitcher-slot naming fetch_pitcher_strikeouts already reuses).
    Found live 2026-08-07 under the same tabs strikeouts already uses
    (pitcher-props, popular) -- never mapped before because it never had a
    scorer producing a candidate for it to price, not a tab problem like
    nrfi_combined's.

    Returns {normalized_name: {"line": float, "over": int, "under": int,
                               "needs": int, "true_over": float,
                               "true_under": float, "hold": float}}."""
    import prop_probability as pp
    out = {}
    for event_id, name, _start in list_games():
        for tab in ("pitcher-props", "popular"):
            try:
                d = _get(f"event-page?eventId={event_id}&tab={tab}&_ak={AK}")
            except Exception:
                continue
            for m in (d.get("attachments", {}).get("markets") or {}).values():
                if not (m.get("marketType") or "").endswith("_OUTS_RECORDED_SB"):
                    continue
                if m.get("inPlay"):
                    continue
                player = line = None
                sides = {}
                for rn in (m.get("runners") or []):
                    parsed = _parse_outs_runner(rn.get("runnerName"))
                    if not parsed:
                        continue
                    p, side, ln = parsed
                    odds = ((rn.get("winRunnerOdds") or {})
                            .get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
                    if odds is None:
                        continue
                    player, line = p, ln
                    sides[side] = odds
                if not player or line is None or "OVER" not in sides or "UNDER" not in sides:
                    continue
                over, under = sides["OVER"], sides["UNDER"]
                t_over, t_under, hold = pp.devig_two_sided(over, under)
                out[normalize_name(player)] = {
                    "player": player, "line": line,
                    "needs": int(line) + 1 if float(line).is_integer() else int(line + 0.5),
                    "over": over, "under": under,
                    "true_over": t_over, "true_under": t_under, "hold": hold,
                    "game": name,
                }
    return out


_COMBINED_K_RE = re.compile(r"^(.+?)\s*&\s*(.+?)\s+(\d+)\+\s*Combined Strikeouts$", re.I)


def fetch_combined_pitcher_strikeouts():
    """"Starting Pitcher Combined Alt Strikeouts" -- the combined strikeout
    total of BOTH starters, found live under the same pitcher-props/popular
    tabs fetch_pitcher_outs already scans. Confirmed real and unmapped:
    a full pull of a real slate's pitcher-props tab showed this market
    sitting right next to PITCHER_*_OUTS_RECORDED_SB with nothing reading
    it. Unlike Pitcher Outs Recorded, this is a ONE-SIDED LADDER (12+, 13+,
    14+, ... Combined Strikeouts, escalating odds, no paired Under side) --
    the same shape as the batter hit/TB ladders in MARKET_MAP, not the
    single-line two-sided shape fetch_pitcher_outs parses. So each rung's
    implied probability is read directly off its own price
    (prop_probability.implied_probability), same as every other one-sided
    market this file prices -- there is no complementary side to devig
    against for a single "X+" runner.

    Keyed by `matchup` (game_meta's own "Away @ Home", no pitcher names) --
    a two-pitcher joint market, the same reason fetch_first_inning_totals is
    keyed by game rather than by either starter alone, and the same
    parenthetical-stripping that function's own docstring explains: raw
    list_games() names embed the probable starters
    ("Team (P Name) @ Team (P Name)"), which never matches generate_picks.py's
    plain matchup field on its own.

    Returns {matchup: {"pitchers": (name_a, name_b),
                        "rungs": {threshold: american_odds}}}."""
    out = {}
    for event_id, name, _start in list_games():
        matchup = re.sub(r"\s*\([^)]*\)", "", name).strip()
        for tab in ("pitcher-props", "popular"):
            try:
                d = _get(f"event-page?eventId={event_id}&tab={tab}&_ak={AK}")
            except Exception:
                continue
            for m in (d.get("attachments", {}).get("markets") or {}).values():
                if (m.get("marketType") or "") != "STARTING_PITCHER_COMBINED_ALT_STRIKEOUTS":
                    continue
                if m.get("inPlay"):
                    continue
                pitchers = None
                rungs = {}
                for rn in (m.get("runners") or []):
                    match = _COMBINED_K_RE.match((rn.get("runnerName") or "").strip())
                    if not match:
                        continue
                    a, b, threshold = match.group(1).strip(), match.group(2).strip(), int(match.group(3))
                    odds = ((rn.get("winRunnerOdds") or {})
                            .get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
                    if odds is None:
                        continue
                    pitchers = (a, b)
                    rungs[threshold] = odds
                if pitchers and rungs:
                    out[matchup] = {"pitchers": pitchers, "rungs": rungs}
    return out


def fetch_first_inning_totals():
    """The REAL both-teams NRFI/YRFI price -- market type
    ***OVER/UNDER_0.5_RUNS_1ST_INNINGS, under the "innings" tab (never
    "batter-props"/"popular"/"pitcher-props", which is the entire reason
    this was never fetched before: no code here had ever requested that tab).

    A simple two-way Over/Under 0.5 runs, BOTH teams combined -- this is
    what generate_picks.py's _build_combined_nrfi computes a model
    probability for. Verified live against FanDuel's raw API (not just app
    screenshots): the runner names are literally "Over"/"Under" with no
    player attached (handicap is fixed at 0, the 0.5 line lives in the
    market name, not the handicap field) -- so unlike every other market
    here, this one is keyed by GAME NAME rather than player.

    KEY FORMAT. list_games() returns "Detroit Tigers (K Montero) @ San
    Francisco Giants (J Brubaker)" -- FanDuel embeds the probable starters
    right in the game name. generate_picks.py's own `matchup` field never
    carries pitcher names ("Detroit Tigers @ San Francisco Giants", built by
    mlb_daily.py as f"{away} @ {home}"), so a raw dict keyed on FanDuel's
    name would never match anything -- caught live before it could ship as
    a second silent zero. Stripped here so the key matches `matchup` exactly.

    Returns {matchup: {"over": int, "under": int, "true_over": float,
                       "true_under": float, "hold": float}}."""
    import prop_probability as pp
    out = {}
    for event_id, name, _start in list_games():
        matchup = re.sub(r"\s*\([^)]*\)", "", name).strip()
        try:
            d = _get(f"event-page?eventId={event_id}&tab=innings&_ak={AK}")
        except Exception:
            continue
        for m in (d.get("attachments", {}).get("markets") or {}).values():
            if m.get("marketType") != "***OVER/UNDER_0.5_RUNS_1ST_INNINGS":
                continue
            if m.get("inPlay"):
                continue
            over = under = None
            for rn in (m.get("runners") or []):
                side = (rn.get("runnerName") or "").strip().upper()
                odds = ((rn.get("winRunnerOdds") or {})
                        .get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
                if odds is None:
                    continue
                if side == "OVER": over = odds
                elif side == "UNDER": under = odds
            if over is None or under is None:
                continue
            t_over, t_under, hold = pp.devig_two_sided(over, under)
            out[matchup] = {"over": over, "under": under,
                           "true_over": t_over, "true_under": t_under, "hold": hold}
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


# This pipeline's internal stat name -> the market's stat name, wherever the
# two differ. "stolen_base" (singular) is baked into grade_results.py,
# backtest/signals.py's weight table, and every display label in
# generate_picks.py -- renaming it everywhere to match FanDuel's own
# "stolen_bases" (see MARKET_MAP above, and mlb_sources._PROP_THRESHOLDS)
# would touch far more than this bug requires. It is one market with two
# spellings; translate at the boundary instead of renaming it live in three
# files. Verified against a real snapshot (data/props/props_2026-08-07.json):
# every stolen_bases row there uses the plural, needs 1 or 2.
STAT_ALIASES = {"stolen_base": "stolen_bases"}


def attach_market_prices(candidates, prices=None, k_prices=None, fi_prices=None, po_prices=None):
    """Attach the real posted price to every candidate that has one.

    Sets `market_odds`, `market_implied`, and `market_edge` (model probability
    minus the price's implied probability). Leaves them absent rather than
    guessing when the prop is not offered -- an unpriced prop is a gap in
    coverage, not a bet at some assumed number.

    Consults THREE price feeds. `prices` (fetch_prop_prices) is the
    one-sided batter feed; pitcher strikeouts are a separate, two-sided
    market (fetch_pitcher_strikeouts) that this function used to never even
    request, which is the entire reason 27 strikeout candidates priced at
    zero -- not a naming mismatch like stolen_base, a missing feed.
    fetch_first_inning_totals is the real combined NRFI/YRFI market, keyed
    by game rather than player -- same "never requested the right tab"
    story as strikeouts, found the same way."""
    import prop_probability as pp
    if prices is None:
        try:
            prices = fetch_prop_prices()
        except Exception:
            prices = {}
    if k_prices is None:
        try:
            k_prices = fetch_pitcher_strikeouts()
        except Exception:
            k_prices = {}
    if fi_prices is None:
        try:
            fi_prices = fetch_first_inning_totals()
        except Exception:
            fi_prices = {}
    if po_prices is None:
        try:
            po_prices = fetch_pitcher_outs()
        except Exception:
            po_prices = {}
    matched = 0
    for c in candidates:
        proj = c.get("projection") or {}
        stat = STAT_ALIASES.get(proj.get("stat"), proj.get("stat"))
        needs = proj.get("needs")

        if stat == "pitcher_outs":
            # Two-sided, same shape as strikeouts: FanDuel posts one line
            # per starter, so a price only exists when our recommended
            # threshold happens to be the one they offered.
            po = po_prices.get(normalize_name(c.get("name")))
            if po is None or needs is None or po.get("needs") != needs:
                continue
            c["market_odds"] = po["over"]
            c["market_implied"] = round(po["true_over"], 4)
            c["market_hold"] = round(po["hold"], 4)
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                c["price_clears"] = pp.price_is_acceptable(po["over"], p)
            matched += 1
            continue

        if stat == "nrfi_combined":
            # Game-level market, keyed by matchup ("Away @ Home") rather than
            # player name -- the runners are literally "Over"/"Under" with no
            # player attached. lean picks which side's price/implied prob we
            # report: YRFI = Over 0.5 (at least one team scores), NRFI = Under.
            fi = fi_prices.get(c.get("matchup"))
            lean = c.get("lean")
            if fi is None or lean not in ("YRFI", "NRFI"):
                continue
            odds = fi["over"] if lean == "YRFI" else fi["under"]
            implied = fi["true_over"] if lean == "YRFI" else fi["true_under"]
            c["market_odds"] = odds
            c["market_implied"] = round(implied, 4)
            c["market_hold"] = round(fi["hold"], 4)
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                c["price_clears"] = pp.price_is_acceptable(odds, p)
            matched += 1
            continue

        if stat == "strikeouts":
            # Two-sided market: FanDuel posts exactly one line per starter,
            # so a price only exists when our recommended threshold happens
            # to be the one line FanDuel offered. market_implied uses the
            # de-vigged true_over rather than a naive single-side reading --
            # with both sides quoted, the exact hold is knowable instead of
            # assumed.
            k = k_prices.get(normalize_name(c.get("name")))
            if k is None or needs is None or k.get("needs") != needs:
                continue
            c["market_odds"] = k["over"]
            c["market_implied"] = round(k["true_over"], 4)
            c["market_hold"] = round(k["hold"], 4)
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                # NOT a raw >= against max_acceptable_price: that returns
                # None outside (0,1) exclusive -- true at the extremes
                # low-probability markets (stolen bases, home runs) actually
                # live in -- and `int >= None` raises TypeError, which
                # crashed this whole function the first time a candidate
                # with a probability near either edge reached it live.
                # price_is_acceptable already exists and handles exactly
                # this.
                c["price_clears"] = pp.price_is_acceptable(k["over"], p)
            matched += 1
            continue

        key = (stat, needs)
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
            c["price_clears"] = pp.price_is_acceptable(odds, p)
        matched += 1
    return candidates, matched
