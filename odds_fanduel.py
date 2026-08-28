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
from dataclasses import dataclass
from datetime import datetime, timezone

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

# A parsed empty mapping is not proof that FanDuel successfully inspected a
# relevant event. These states preserve the distinction between transport,
# structural, discovery, and exact-market outcomes for the live price owner.
ROOT_FETCH_FAILED = "ROOT_FETCH_FAILED"
ROOT_MALFORMED = "ROOT_MALFORMED"
ROOT_EMPTY = "ROOT_EMPTY"
EVENTS_DISCOVERED = "EVENTS_DISCOVERED"


@dataclass(frozen=True)
class MarketEventObservation:
    """One FanDuel event's evidence for one market family.

    ``complete`` means every tab required to prove absence for that family was
    structurally valid. Parsed matches in ``values`` remain usable even when a
    different tab failed; only a claimed absence requires complete evidence.
    """

    event_id: object
    name: str
    start: object
    complete: bool
    values: dict
    errors: tuple = ()


@dataclass(frozen=True)
class MarketFeedObservation:
    """Structured source evidence returned to lifecycle-sensitive callers."""

    family: str
    root_state: str
    values: dict
    events: tuple
    errors: tuple = ()


@dataclass(frozen=True)
class _GameDiscovery:
    root_state: str
    games: tuple
    errors: tuple = ()

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
    # Found live 2026-08-12 (a real pull, 4 occurrences across 8 games) --
    # never mapped before now. generate_picks.py's _batter_options never even
    # asked for a 2+/3+ home-run probability until the same pass added this,
    # so this entry alone would have priced a market the pipeline could never
    # actually recommend against.
    "TO_HIT_3+_HOME_RUNS":      ("home_runs", 3),
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
    # "To Hit a Moonshot (420+ FT)" -- found live 2026-08-14 from a real
    # screenshot of the user's own FanDuel app under the dedicated
    # "Moonshots" tab (numeric id 386), matched exactly against a live API
    # pull for the same slate and players (Suzuki +1900, Happ +2000,
    # Swanson +2200, all confirmed identical). Checked every game on that
    # live slate: no separate "400+ FT" market type exists anywhere in the
    # API, only this one, despite the FanDuel app UI showing both a
    # 400+ FT and a 420+ FT column -- whatever the app derives that 400+
    # FT column from, it is not a separately priced market this pipeline
    # can fetch.
    "PLAYER_TO_HIT_A_HOME_RUN_420+_FEET": ("moonshot_420", 1),
}

# Markets deliberately NOT mapped, and why. Listing them is the point: an
# unmapped market should be a decision on record, not an oversight.
#
# STALE ENTRIES REMOVED, 2026-08-12 audit: LASER and PITCHER_*_STRIKEOUTS used
# to be listed here as unmapped/not-yet-computed. Both are wrong now --
# score_laser (hard_hit_105/hard_hit_110, both in MARKET_MAP above) and
# fetch_pitcher_strikeouts/attach_market_prices' "strikeouts" branch (a
# separate two-sided feed, since it's a line market rather than yes/no, not a
# MARKET_MAP entry) have shipped since this dict was written. UNMAPPED_REASONS
# itself is never read by any code path -- it is documentation only -- but
# stale documentation here is exactly the "the comment says one thing, the
# code does another" bug class this project keeps finding elsewhere, just
# without a runtime consequence this time.
#
#   PLAYERS_TO_COMBINE_FOR_*  — two or more players in one bet. Pricing these
#     honestly needs the JOINT distribution, and teammates' outcomes are
#     correlated (same game, same pitcher, same run environment). Multiplying
#     two independent probabilities would overstate every one of them, which
#     is exactly the kind of plausible-looking error this project keeps
#     finding. ~640 lines a night, left alone until correlation is modelled --
#     correlation.py now exists (built for parlay_builder.py) and may be
#     reusable here; not yet evaluated for that.
#   RESULT_OF_FIRST_PITCH, BATTER_UP_*, FIRST_SCORING_PLAY — single-pitch and
#     single-plate-appearance micro markets. No rate is computed for them and
#     none could be estimated from game logs.
UNMAPPED_REASONS = {
    "PLAYERS_TO_COMBINE_FOR": "needs a joint distribution — teammates are correlated",
    "RESULT_OF_FIRST_PITCH": "single-pitch market, no rate exists",
    "BATTER_UP": "single-plate-appearance micro market, no rate exists",
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


def _discover_games():
    """Parse the MLB root feed without conflating empty with healthy."""
    try:
        payload = _get(f"content-managed-page?page=CUSTOM&customPageId=mlb&_ak={AK}")
    except Exception as exc:
        return _GameDiscovery(
            ROOT_FETCH_FAILED, (), (f"{type(exc).__name__}: {exc}",),
        )
    if not isinstance(payload, dict):
        return _GameDiscovery(ROOT_MALFORMED, (), ("root payload is not an object",))
    attachments = payload.get("attachments")
    if not isinstance(attachments, dict) or "events" not in attachments:
        return _GameDiscovery(
            ROOT_MALFORMED, (), ("root payload has no attachments.events object",),
        )
    events = attachments.get("events")
    if not isinstance(events, dict):
        return _GameDiscovery(
            ROOT_MALFORMED, (), ("root attachments.events is not an object",),
        )
    if not events:
        return _GameDiscovery(ROOT_EMPTY, (), ("root attachments.events is empty",))

    games = []
    invalid = 0
    for event in events.values():
        if not isinstance(event, dict):
            invalid += 1
            continue
        name = event.get("name") or ""
        event_id = event.get("eventId")
        # Real games carry "AWAY @ HOME"; the feed also lists futures,
        # awards and season-long markets that must not be treated as games.
        if " @ " not in name:
            continue
        if event_id in (None, ""):
            invalid += 1
            continue
        games.append((event_id, name, event.get("openDate")))
    if not games:
        detail = "root contained no usable MLB game events"
        if invalid:
            detail += f" ({invalid} structurally invalid event(s))"
        return _GameDiscovery(ROOT_EMPTY, (), (detail,))
    return _GameDiscovery(EVENTS_DISCOVERED, tuple(games))


def _discovery_failure(discovery, family):
    detail = "; ".join(discovery.errors) or discovery.root_state
    return MarketFeedObservation(
        family=family, root_state=discovery.root_state,
        values={}, events=(), errors=(detail,),
    )


def _require_discovery(discovery, family, strict):
    if discovery.root_state == EVENTS_DISCOVERED:
        return True
    # Transport failures historically propagated to callers; keep that
    # behavior. Structural emptiness remains fail-soft only for legacy
    # research callers, while strict/lifecycle callers reject it.
    if strict or discovery.root_state == ROOT_FETCH_FAILED:
        detail = "; ".join(discovery.errors) or discovery.root_state
        raise RuntimeError(f"indeterminate {family} root feed: {detail}")
    return False


def list_games(strict=False):
    """Tonight's usable MLB events as ``(event_id, name, start_iso)``.

    Legacy callers retain the list interface. Lifecycle-sensitive callers use
    the structured family observations below; ``strict=True`` now correctly
    rejects a malformed or structurally empty root feed.
    """
    discovery = _discover_games()
    if not _require_discovery(discovery, "MLB", strict):
        return []
    return list(discovery.games)


def _matchup_key(value):
    value = re.sub(r"\s*\([^)]*\)", "", str(value or ""))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", value).strip().lower()


def _utc_instant(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _relevant_events(observation, row):
    """Map a canonical dashboard row to exactly one sportsbook event.

    Scheduled UTC start is the strongest key and safely disambiguates
    doubleheaders. Matchup is used as a fallback for source naming/time drift;
    conflicting or multiple matches remain indeterminate rather than borrowing
    proof from an unrelated event.
    """
    row_start = _utc_instant(row.get("game_start"))
    row_matchup = _matchup_key(row.get("matchup"))
    by_start = [event for event in observation.events
                if row_start is not None and _utc_instant(event.start) == row_start]
    by_matchup = [event for event in observation.events
                  if row_matchup and _matchup_key(event.name) == row_matchup]
    both = [event for event in by_start if event in by_matchup]
    if len(both) == 1:
        return both
    if by_start and by_matchup:
        return []
    if len(by_start) == 1:
        return by_start
    if len(by_matchup) == 1:
        return by_matchup
    return []


def market_evidence_for_row(observation, row):
    """Return event-scoped values and whether exact absence is provable."""
    if not isinstance(observation, MarketFeedObservation):
        return {
            "values": {}, "absence_proven": False,
            "reason": "fetcher did not return structured market evidence",
        }
    if observation.root_state != EVENTS_DISCOVERED:
        return {
            "values": {}, "absence_proven": False,
            "reason": "; ".join(observation.errors) or observation.root_state,
        }
    events = _relevant_events(observation, row)
    if len(events) != 1:
        return {
            "values": {}, "absence_proven": False,
            "reason": "no unique relevant FanDuel event was observed",
        }
    event = events[0]
    return {
        "values": event.values,
        "absence_proven": bool(event.complete),
        "reason": "; ".join(event.errors) if event.errors else (
            "relevant event family inspected" if event.complete
            else "relevant event family observation incomplete"
        ),
        "event_id": event.event_id,
    }


def posted_line_for_subject(family, values, row):
    """Does the book post THIS subject's market at a line other than ours?

    ``attach_market_prices`` matches on the exact threshold we published, so
    a row whose threshold the book does not offer simply fails to match. The
    caller then has to decide what that means, and until now it only had one
    answer: ``NOT_POSTED``. That answer is wrong in a specific and expensive
    way, proven live on 2026-08-28 -- Drew Anderson's board row read "Over
    11.5 Outs Recorded / NOT_POSTED" at the same moment FanDuel was posting
    Drew Anderson Outs Recorded Over 14.5 at -132. The row was built at
    06:31 UTC when no outs market existed for him yet, so the threshold was
    model-anchored to his average workload; the book later posted a real
    line three outs higher and nothing ever revisited the row.

    "The book offers nothing here" and "the book offers this, at a number we
    are not tracking" are different facts with different consequences. The
    first is a genuine absence. The second means the price we display cannot
    be bought at the line we display, which is the more dangerous of the two
    precisely because it looks like ordinary missing data.

    Returns None when the subject is absent from the observation entirely
    (a true absence), otherwise a dict describing what the book actually
    posts for it. Deliberately does NOT re-point the row at the book's line:
    a different threshold is a different prediction with a different
    probability, and silently migrating one would let the board be graded on
    a bet it never made.
    """
    proj = row.get("projection") or {}
    stat = STAT_ALIASES.get(proj.get("stat") or row.get("stat"),
                            proj.get("stat") or row.get("stat"))
    needs = proj.get("needs")
    values = values or {}
    if needs is None:
        return None

    if family in ("strikeouts", "pitcher_outs"):
        entry = values.get(normalize_name(row.get("name")))
        if not entry or entry.get("needs") is None:
            return None
        if entry["needs"] == needs:
            return None
        return {"subject": entry.get("player") or row.get("name"),
                "our_needs": needs, "posted_needs": [entry["needs"]],
                "posted_line": entry.get("line"),
                "posted_over": entry.get("over"), "posted_under": entry.get("under")}

    if family == "combined_strikeouts":
        entry = values.get(row.get("matchup"))
        rungs = (entry or {}).get("rungs") or {}
        if not rungs or needs in rungs:
            return None
        posted = sorted(rungs)
        return {"subject": row.get("matchup"), "our_needs": needs,
                "posted_needs": posted, "posted_line": posted[0] - 0.5,
                "posted_over": rungs[posted[0]], "posted_under": None}

    if family == "general_batter":
        entry = values.get(normalize_name(row.get("name"))) or {}
        # Keyed by (stat, needs). Only rungs of the SAME stat are evidence
        # that this player's market exists -- a posted HITS line says
        # nothing about whether a DOUBLES line was offered.
        posted = sorted(n for (s, n) in entry if s == stat and n is not None)
        if not posted or needs in posted:
            return None
        return {"subject": row.get("name"), "our_needs": needs,
                "posted_needs": posted, "posted_line": posted[0] - 0.5,
                "posted_over": entry.get((stat, posted[0])), "posted_under": None}

    # first_inning is a fixed 0.5 game-level line with no ladder to move
    # along, so there is no such thing as a threshold mismatch for it.
    return None


def _market_pages(event_id, tabs):
    markets = []
    failures = []
    for tab in tabs:
        try:
            payload = _get(f"event-page?eventId={event_id}&tab={tab}&_ak={AK}")
        except Exception as exc:
            failures.append(f"event={event_id} tab={tab}: {exc}")
            continue
        if not isinstance(payload, dict):
            failures.append(f"event={event_id} tab={tab}: payload is not an object")
            continue
        attachments = payload.get("attachments")
        if (not isinstance(attachments, dict)
                or "markets" not in attachments
                or not isinstance(attachments.get("markets"), dict)):
            failures.append(
                f"event={event_id} tab={tab}: missing/invalid attachments.markets"
            )
            continue
        markets.extend(attachments["markets"].values())
    return markets, not failures, tuple(failures)


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


def fetch_pitcher_strikeouts(max_workers=8, strict=False, with_evidence=False):
    """Two-sided strikeout markets for tonight's starters.

    Returns {normalized_name: {"line": float, "over": int, "under": int,
                               "needs": int, "true_over": float, "hold": float}}
    where true_over is de-vigged EXACTLY from both sides."""
    import prop_probability as pp
    discovery = _discover_games()
    if discovery.root_state != EVENTS_DISCOVERED:
        if with_evidence:
            return _discovery_failure(discovery, "strikeouts")
        if not _require_discovery(discovery, "strikeouts", strict):
            return {}
    out = {}
    failures = []
    event_observations = []
    for event_id, name, start in discovery.games:
        markets, complete, event_failures = _market_pages(
            event_id, ("pitcher-props", "popular"),
        )
        failures.extend(event_failures)
        event_values = {}
        for m in markets:
            if not isinstance(m, dict) or m.get("marketType") not in TWO_SIDED_MARKETS:
                continue
            if m.get("inPlay"):
                continue
            parsed = _parse_two_sided(m)
            if not parsed:
                continue
            player, line, over, under = parsed
            t_over, t_under, hold = pp.devig_two_sided(over, under)
            event_values[normalize_name(player)] = {
                "player": player, "line": line,
                # "Over 3.5" settles on 4 or more.
                "needs": int(line) + 1 if float(line).is_integer() else int(line + 0.5),
                "over": over, "under": under,
                "true_over": t_over, "true_under": t_under, "hold": hold,
                "game": name,
            }
        out.update(event_values)
        event_observations.append(MarketEventObservation(
            event_id, name, start, complete, event_values, event_failures,
        ))
    if with_evidence:
        return MarketFeedObservation(
            "strikeouts", EVENTS_DISCOVERED, out,
            tuple(event_observations), tuple(failures),
        )
    if strict and failures:
        raise RuntimeError("incomplete strikeouts feed: " + "; ".join(failures[:5]))
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


def fetch_pitcher_outs(strict=False, with_evidence=False):
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
    discovery = _discover_games()
    if discovery.root_state != EVENTS_DISCOVERED:
        if with_evidence:
            return _discovery_failure(discovery, "pitcher_outs")
        if not _require_discovery(discovery, "pitcher_outs", strict):
            return {}
    out = {}
    failures = []
    event_observations = []
    for event_id, name, start in discovery.games:
        markets, complete, event_failures = _market_pages(
            event_id, ("pitcher-props", "popular"),
        )
        failures.extend(event_failures)
        event_values = {}
        for m in markets:
            if not isinstance(m, dict) or not (m.get("marketType") or "").endswith("_OUTS_RECORDED_SB"):
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
            event_values[normalize_name(player)] = {
                "player": player, "line": line,
                "needs": int(line) + 1 if float(line).is_integer() else int(line + 0.5),
                "over": over, "under": under,
                "true_over": t_over, "true_under": t_under, "hold": hold,
                "game": name,
            }
        out.update(event_values)
        event_observations.append(MarketEventObservation(
            event_id, name, start, complete, event_values, event_failures,
        ))
    if with_evidence:
        return MarketFeedObservation(
            "pitcher_outs", EVENTS_DISCOVERED, out,
            tuple(event_observations), tuple(failures),
        )
    if strict and failures:
        raise RuntimeError("incomplete pitcher-outs feed: " + "; ".join(failures[:5]))
    return out


_COMBINED_K_RE = re.compile(r"^(.+?)\s*&\s*(.+?)\s+(\d+)\+\s*Combined Strikeouts$", re.I)


def fetch_combined_pitcher_strikeouts(strict=False, with_evidence=False):
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
    discovery = _discover_games()
    if discovery.root_state != EVENTS_DISCOVERED:
        if with_evidence:
            return _discovery_failure(discovery, "combined_strikeouts")
        if not _require_discovery(discovery, "combined_strikeouts", strict):
            return {}
    out = {}
    failures = []
    event_observations = []
    for event_id, name, start in discovery.games:
        matchup = re.sub(r"\s*\([^)]*\)", "", name).strip()
        markets, complete, event_failures = _market_pages(
            event_id, ("pitcher-props", "popular"),
        )
        failures.extend(event_failures)
        event_values = {}
        for m in markets:
            if (not isinstance(m, dict)
                    or (m.get("marketType") or "") != "STARTING_PITCHER_COMBINED_ALT_STRIKEOUTS"):
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
                event_values[matchup] = {"pitchers": pitchers, "rungs": rungs}
        out.update(event_values)
        event_observations.append(MarketEventObservation(
            event_id, name, start, complete, event_values, event_failures,
        ))
    if with_evidence:
        return MarketFeedObservation(
            "combined_strikeouts", EVENTS_DISCOVERED, out,
            tuple(event_observations), tuple(failures),
        )
    if strict and failures:
        raise RuntimeError("incomplete combined-K feed: " + "; ".join(failures[:5]))
    return out


def fetch_first_inning_totals(strict=False, with_evidence=False):
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
    discovery = _discover_games()
    if discovery.root_state != EVENTS_DISCOVERED:
        if with_evidence:
            return _discovery_failure(discovery, "first_inning")
        if not _require_discovery(discovery, "first_inning", strict):
            return {}
    out = {}
    failures = []
    event_observations = []
    for event_id, name, start in discovery.games:
        matchup = re.sub(r"\s*\([^)]*\)", "", name).strip()
        markets, complete, event_failures = _market_pages(event_id, ("innings",))
        failures.extend(event_failures)
        event_values = {}
        for m in markets:
            if (not isinstance(m, dict)
                    or m.get("marketType") != "***OVER/UNDER_0.5_RUNS_1ST_INNINGS"):
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
                if side == "OVER":
                    over = odds
                elif side == "UNDER":
                    under = odds
            if over is None or under is None:
                continue
            t_over, t_under, hold = pp.devig_two_sided(over, under)
            event_values[matchup] = {
                "over": over, "under": under,
                "true_over": t_over, "true_under": t_under, "hold": hold,
            }
        out.update(event_values)
        event_observations.append(MarketEventObservation(
            event_id, name, start, complete, event_values, event_failures,
        ))
    if with_evidence:
        return MarketFeedObservation(
            "first_inning", EVENTS_DISCOVERED, out,
            tuple(event_observations), tuple(failures),
        )
    if strict and failures:
        raise RuntimeError("incomplete first-inning feed: " + "; ".join(failures[:5]))
    return out


def _event_props(event_id, strict=False, with_evidence=False):
    rows = []
    # REAL BUG, found live 2026-08-13 checking why hard_hit_105/hard_hit_110
    # (the Laser prop) showed 0/17 real prices attached every single night,
    # a 100% miss rate that -- combined with select_main_board's price_clears
    # requirement -- meant this entire prop family could never appear on the
    # main board regardless of the model's read. TO_HIT_A_LASER_(105+_MPH)
    # is a real, live, currently-posted FanDuel market (verified: 15 real
    # runners for tonight's Brewers @ Dodgers game, e.g. Shohei Ohtani
    # +410) -- it was never missing, just posted under its own dedicated
    # "lasers" tab that this loop never fetched. FanDuel's own event-page
    # layout response lists it explicitly ("Lasers", tab id 384) alongside
    # "batter-props" and "popular", which this function already knew about.
    # "moonshots" added 2026-08-14: PLAYER_TO_HIT_A_HOME_RUN_420+_FEET (see
    # MARKET_MAP's own comment) lives ONLY under this tab -- confirmed live,
    # absent from batter-props/popular/lasers across every game checked --
    # same "real market, wrong tab" story as lasers above.
    markets, complete, failures = _market_pages(
        event_id, ("batter-props", "popular", "lasers", "moonshots"),
    )
    for m in markets:
        if not isinstance(m, dict):
            continue
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
    if with_evidence:
        return rows, complete, failures
    if strict and failures:
        raise RuntimeError("incomplete batter-props feed: " + "; ".join(failures[:5]))
    return rows


def fetch_prop_prices(max_workers=8, strict=False, with_evidence=False):
    """Every priced batter prop on the slate.

    Returns {normalized_name: {(stat, needs): american_odds}}. In-play markets
    are excluded: once a game starts the price reflects the remaining at-bats,
    which is a different bet from the pregame one this pipeline models."""
    discovery = _discover_games()
    if discovery.root_state != EVENTS_DISCOVERED:
        if with_evidence:
            return _discovery_failure(discovery, "general_batter")
        if not _require_discovery(discovery, "general batter", strict):
            return {}
    out = {}
    failures = []
    event_observations = []

    def fetch_one(game):
        rows, complete, errors = _event_props(
            game[0], strict=False, with_evidence=True,
        )
        return game, rows, complete, errors

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for game, rows, complete, errors in ex.map(fetch_one, discovery.games):
            event_id, name, start = game
            event_values = {}
            for result in rows:
                if result["in_play"]:
                    continue
                event_values.setdefault(result["norm"], {})[
                    (result["stat"], result["needs"])
                ] = result["american"]
            for player, markets in event_values.items():
                out.setdefault(player, {}).update(markets)
            failures.extend(errors)
            event_observations.append(MarketEventObservation(
                event_id, name, start, complete, event_values, errors,
            ))
    if with_evidence:
        return MarketFeedObservation(
            "general_batter", EVENTS_DISCOVERED, out,
            tuple(event_observations), tuple(failures),
        )
    if strict and failures:
        raise RuntimeError("incomplete batter-props feed: " + "; ".join(failures[:5]))
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


def attach_market_prices(candidates, prices=None, k_prices=None, fi_prices=None, po_prices=None,
                         combined_k_prices=None):
    """Attach the real posted price to every candidate that has one.

    Sets `market_odds`, `market_implied`, and `market_edge` (model probability
    minus the price's implied probability). Leaves them absent rather than
    guessing when the prop is not offered -- an unpriced prop is a gap in
    coverage, not a bet at some assumed number.

    ALSO sets `posted_implied`/`market_fair`/`market_fair_method`/
    `edge_vs_fair` (P0-6 market-edge-semantics fix, additive -- market_odds/
    market_implied/market_edge keep their exact pre-existing meaning). Real
    bug this closes: for the genuinely two-sided markets (pitcher_outs,
    strikeouts, nrfi_combined -- where FanDuel prices BOTH sides and
    prop_probability.devig_two_sided gives an EXACT no-vig probability),
    market_implied already IS the fair value. For every one-sided market
    (combined_strikeouts and the generic branch below -- hits/total_bases/
    home_runs/RBIs/runs/stolen_base/singles/doubles/triples/
    hits_runs_rbis/lasers/moonshots, the majority of the real board),
    market_implied is the RAW posted implied probability, INCLUDING the
    book's ~8% hold, never de-vigged. Both were exposed to callers under
    the identical field name/meaning (`market_edge = model - market_implied`),
    so an "edge" on a one-sided market looked directly comparable to an
    "edge" on a two-sided one when it structurally was not -- one is edge
    against an inflated (hold-included) number, the other against the true
    fair price. market_fair_method ("exact_two_sided" | "assumed_hold")
    makes that distinction explicit and machine-readable; edge_vs_fair is
    the one number that's honestly comparable across every market family.
    eval_lib.market_probability() already built this exact distinction for
    backtest analysis (see its own docstring) -- this brings the same
    honesty to the live product these fields actually ship on.

    Consults price feeds. `prices` (fetch_prop_prices) is the one-sided
    batter feed; pitcher strikeouts are a separate, two-sided market
    (fetch_pitcher_strikeouts) that this function used to never even
    request, which is the entire reason 27 strikeout candidates priced at
    zero -- not a naming mismatch like stolen_base, a missing feed.
    fetch_first_inning_totals is the real combined NRFI/YRFI market, keyed
    by game rather than player -- same "never requested the right tab"
    story as strikeouts, found the same way.

    combined_k_prices was missing entirely until this pass: build_candidates()
    already prices combined_strikeouts inline (score_combined_strikeouts sets
    market_odds itself, at creation time, off the real ladder), so this
    function's main() caller never needed it and this gap stayed invisible.
    But that inline price is never persisted (persist_player_snapshots'
    evaluation dict carries no market_odds at all -- the daily board's own
    in-memory candidates simply keep the value they were created with) and
    this function had no branch to RE-derive it -- so parlay_builder.py,
    which reloads candidates from data/players/*.json and re-prices through
    exactly this function, could never price a combined_strikeouts leg.
    Verified live before this fix: a reconstructed combined_strikeouts leg
    came back with market_odds still None after a full _finalize() pass."""
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
    if combined_k_prices is None:
        try:
            combined_k_prices = fetch_combined_pitcher_strikeouts()
        except Exception:
            combined_k_prices = {}
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
            # market-edge-semantics fix (P0-6 data-integrity audit): see
            # this function's own docstring addendum below for why
            # posted_implied/market_fair/market_fair_method exist alongside
            # the pre-existing market_implied/market_edge (kept, unchanged,
            # for backward compatibility). Two-sided market: market_implied
            # here IS already the exact no-vig fair probability.
            c["posted_implied"] = round(pp.implied_probability(po["over"]), 4)
            c["market_fair"] = c["market_implied"]
            c["market_fair_method"] = "exact_two_sided"
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                c["edge_vs_fair"] = c["market_edge"]
                c["price_clears"] = pp.price_is_acceptable(po["over"], p)
            matched += 1
            continue

        if stat == "combined_strikeouts":
            # Game-level market like nrfi_combined -- keyed by matchup, not
            # player name (the candidate's own "name" is "Pitcher A & Pitcher
            # B", which will never match a real FanDuel runner name). A
            # one-sided ladder like the batter markets in MARKET_MAP (12+,
            # 13+, 14+... escalating odds, no paired Under), so the implied
            # probability is read straight off the price, same as those --
            # matches score_combined_strikeouts' own market_implied
            # computation exactly, for consistency between the price set at
            # creation time and any later re-price through this function.
            ck = (combined_k_prices or {}).get(c.get("matchup"))
            if ck is None or needs is None:
                continue
            odds = (ck.get("rungs") or {}).get(needs)
            if odds is None:
                continue
            c["market_odds"] = odds
            c["market_implied"] = round(pp.implied_probability(odds), 4)
            # market-edge-semantics fix (P0-6): a one-sided ladder like the
            # generic batter markets below -- FanDuel exposes only this one
            # side, so market_fair is the assumed-hold approximation, not
            # an exact no-vig read. Labelled honestly via market_fair_method
            # rather than left indistinguishable from the exact branches
            # above/below.
            c["posted_implied"] = c["market_implied"]
            c["market_fair"] = round(pp.devig(c["market_implied"]), 4)
            c["market_fair_method"] = "assumed_hold"
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                c["edge_vs_fair"] = round(p - c["market_fair"], 4)
                c["price_clears"] = pp.price_is_acceptable(odds, p)
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
            # market-edge-semantics fix (P0-6): two-sided market, exact fair.
            c["posted_implied"] = round(pp.implied_probability(odds), 4)
            c["market_fair"] = c["market_implied"]
            c["market_fair_method"] = "exact_two_sided"
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                c["edge_vs_fair"] = c["market_edge"]
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
            # market-edge-semantics fix (P0-6): two-sided market, exact fair.
            c["posted_implied"] = round(pp.implied_probability(k["over"]), 4)
            c["market_fair"] = c["market_implied"]
            c["market_fair_method"] = "exact_two_sided"
            p = c.get("hit_probability")
            if p is not None:
                c["market_edge"] = round(p - c["market_implied"], 4)
                c["edge_vs_fair"] = c["market_edge"]
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
            # KNOWN RESIDUAL GAP for hard_hit_105/hard_hit_110, verified live
            # 2026-08-14 -- NOT the needs-mismatch bug class pitcher_outs/
            # strikeouts had (hard_hit's `needs` is always 1, so there is no
            # line to miss) and NOT a normalize_name/MARKET_MAP defect.
            # FanDuel's Laser market is (a) only posted for games close to
            # first pitch -- games 2-3+ hours out have zero Laser runners at
            # all -- and (b) even for live games, prices a SUBSET of each
            # lineup: a live re-pull 25 minutes apart on the same game grew
            # from 14 to 17 runners as FanDuel filled the board in, and a
            # few real batters (confirmed absent on both pulls, not a
            # spelling miss) never got a Laser line at all that slate. This
            # caps real-price coverage below 100% by design of FanDuel's own
            # market, not a bug here -- do not re-diagnose this as the same
            # class of issue already fixed for pitcher_outs/strikeouts/
            # lasers-tab without new live evidence.
            continue
        c["market_odds"] = odds
        c["market_implied"] = round(pp.implied_probability(odds), 4)
        # market-edge-semantics fix (P0-6): the majority of the board (every
        # one-sided batter market: hits/total_bases/home_runs/RBIs/runs/
        # stolen_base/singles/doubles/triples/hits_runs_rbis/lasers/
        # moonshots) lands here. FanDuel structurally posts only one side
        # for these (see market_probability()'s own docstring in eval_lib.py,
        # which already built this exact distinction for backtest analysis
        # -- this brings the same honesty to the live product), so
        # market_fair is the assumed-hold approximation, not exact.
        c["posted_implied"] = c["market_implied"]
        c["market_fair"] = round(pp.devig(c["market_implied"]), 4)
        c["market_fair_method"] = "assumed_hold"
        p = c.get("hit_probability")
        if p is not None:
            c["market_edge"] = round(p - c["market_implied"], 4)
            c["edge_vs_fair"] = round(p - c["market_fair"], 4)
            c["price_clears"] = pp.price_is_acceptable(odds, p)
        matched += 1
    return candidates, matched
