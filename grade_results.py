#!/usr/bin/env python3
"""
grade_results.py — grades yesterday's picks against actual box scores.

Reads output/picks_{YESTERDAY}.json (written by generate_picks.py), fetches
each pick's actual result via MLB Stats API boxscore_data (keyed by the
game_pk already stored on each pick), compares against the pick's own
projection, and writes:
  - results/grades_{YESTERDAY}.json   (per-pick grade detail)
  - results/history.json              (running accumulated accuracy record)

Grading uses each pick's own projected number as the threshold (projection -
0.5, the same "Over X.5" convention generate_picks.py commits to when writing
the prop text) rather than a real sportsbook line, since none is fetched by
this pipeline. This is a self-consistency check — "did the model's own call
turn out right" — not a market-beating claim.

Runs each morning before that day's picks are generated. If yesterday's picks
file doesn't exist (first run, or picks generation failed/was skipped that
day), this is a no-op — it must never block the rest of the pipeline.
"""
import glob, os, sys, json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import grading_sources as m

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

YESTERDAY = os.environ.get("GRADE_DATE") or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
HISTORY_FILE = os.path.join(RESULTS_DIR, "history.json")
PUBLIC_REGISTRY_FILE = os.environ.get("PUBLIC_TOP_PICK_REGISTRY") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "public_top_picks", "registry.json"
)

# How far back a catch-up pass will look. Bounded so a long-running season
# doesn't re-walk hundreds of days every morning, and so genuinely
# unresolvable days (a pick whose player was scratched, say) stop being
# retried forever.
CATCHUP_WINDOW_DAYS = 14
PUBLIC_CORRECTION_RECHECK_DAYS = 3


def picks_path(date):  return os.path.join(OUTPUT_DIR, f"picks_{date}.json")
def grades_path(date): return os.path.join(RESULTS_DIR, f"grades_{date}.json")


def dates_needing_grading():
    """Every date with a picks file that isn't fully graded yet.

    The pipeline used to grade *only* yesterday. That silently made missed
    runs unrecoverable: if a day's workflow didn't fire (verified as a real
    risk -- GitHub delayed a scheduled run by ~43 minutes, and documents that
    it drops them entirely under load), that day's picks were never graded,
    and since the next morning only ever looked at ITS yesterday, that day's
    accuracy data was lost permanently. The accuracy record is the whole
    point of this project, so losing days to a scheduling hiccup is the one
    failure that actually compounds.

    Also re-grades days that are only PARTIALLY graded (ungraded > 0). Those
    are usually games that hadn't gone Final when grading ran -- a
    suspended/postponed game finishing the next day is exactly the case that
    should resolve on a later pass rather than sit ungraded forever.
    """
    today = datetime.now()
    out = []
    for back in range(1, CATCHUP_WINDOW_DAYS + 1):
        d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        if not os.path.exists(picks_path(d)):
            continue
        gp = grades_path(d)
        if not os.path.exists(gp):
            out.append(d); continue
        try:
            with open(gp, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("ungraded", 0) > 0:
                out.append(d)
        except (json.JSONDecodeError, OSError):
            out.append(d)  # unreadable grades file -> regrade it
    # Deployment-proven Top Picks are a durable population independent of
    # the mutable daily canonical file. Their unresolved dates remain
    # retryable even when the canonical board later omitted the wager.
    try:
        from dashboard.publication_registry import load_registry
        registry = load_registry(PUBLIC_REGISTRY_FILE)
        expected_by_date = defaultdict(int)
        for entry in registry["entries"].values():
            if entry.get("slate_date"):
                expected_by_date[entry["slate_date"]] += 1
        public_dates = sorted(expected_by_date)
    except RuntimeError:
        raise
    except Exception:
        public_dates = []
    for d in public_dates:
        gp = grades_path(d)
        if not os.path.exists(gp):
            out.append(d)
            continue
        try:
            with open(gp, encoding="utf-8") as handle:
                prior = json.load(handle)
            counts = prior.get("public_top_pick_counts") or {}
            recorded = sum(counts.get(key, 0) for key in ("hits", "misses", "voids", "ungraded"))
            if recorded != expected_by_date[d] or counts.get("ungraded", 0) > 0:
                out.append(d)
        except (json.JSONDecodeError, OSError):
            out.append(d)
    # FanDuel may resettle when the official result changes. Recheck the
    # recent deployment-proven public population even when it is already
    # terminal, so a final scoring correction updates history rather than
    # only the live overlay. Older corrections remain available through an
    # explicit GRADE_DATE rerun instead of re-fetching the whole season.
    correction_floor = (
        datetime.now(timezone.utc).date() - timedelta(days=PUBLIC_CORRECTION_RECHECK_DAYS)
    ).isoformat()
    out.extend(d for d in public_dates if d >= correction_floor)
    return sorted(set(out))


def days_with_no_board():
    """Past days on which a slate existed but this pipeline produced nothing.

    THE FAILURE THIS MAKES VISIBLE. dates_needing_grading() above skips any
    day with no picks file, because there is nothing to grade. That is right
    for grading and completely wrong for monitoring: a day the workflow never
    ran looks exactly like a day that was already handled. The record simply
    has a hole in it and nothing ever says so.

    That is not a hypothetical. Of the first 19 runs of this pipeline, only
    THREE were triggered by the schedule and two of those were cancelled
    after sitting fifteen minutes in the queue without ever being assigned a
    runner (runner_id 0, no steps array — the job never started). Every other
    successful run was launched by hand. A system whose whole value is an
    unattended daily record was, in practice, running when someone remembered
    to press the button.

    Reports rather than fixes, because the cause is on GitHub's side of the
    line. What it buys is knowing on the next run that yesterday was missed,
    instead of discovering weeks later that the accuracy record has gaps.
    """
    today = datetime.now()
    # Only days after the first board this pipeline ever produced can be
    # "missed". Before that there was nothing to miss, and reporting those as
    # gaps would mean eleven false alarms on the first run — a monitor that
    # cries wolf on day one is a monitor that gets ignored by day three.
    existing = sorted(glob.glob(os.path.join(OUTPUT_DIR, "picks_[0-9]*.json")))
    if not existing:
        return []
    first = os.path.basename(existing[0])[6:16]

    missing = []
    for back in range(1, CATCHUP_WINDOW_DAYS + 1):
        d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        if d < first or os.path.exists(picks_path(d)):
            continue
        # No picks file. Distinguish "we missed it" from "there was no
        # baseball": an off-day with no games is not a gap in the record.
        try:
            r = m.retry_get(f"https://statsapi.mlb.com/api/v1/schedule",
                            params={"sportId": 1, "date": d},
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=20, retries=1)
            n_games = sum(len(x.get("games", [])) for x in (r.json() or {}).get("dates", []))
        except Exception:
            continue  # cannot tell — say nothing rather than cry wolf
        if n_games:
            missing.append((d, n_games))
    return missing


def fetch_game_statuses(date):
    """Verified live: grading a game before it's Final silently reads an
    all-zeros box score line and would score every pick on it as a false
    "miss" — this gates grading on the schedule's actual game status first."""
    try:
        r = m.retry_get("https://statsapi.mlb.com/api/v1/schedule", params={"sportId": 1, "date": date},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        games = r.json().get("dates", [{}])[0].get("games", [])
        return {g["gamePk"]: g.get("status", {}) for g in games}
    except Exception as e:
        m.warn(f"Grading: couldn't fetch game statuses for {date}: {e}")
        return {}


_GAME_FEED_CACHE = {}


def fetch_game_feed(game_pk, refresh=False):
    """Fetch one game by stable game identity, independent of slate date."""
    if not refresh and game_pk in _GAME_FEED_CACHE:
        return _GAME_FEED_CACHE[game_pk]
    try:
        r = m.retry_get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20, retries=2)
        r.raise_for_status()
        feed = r.json()
        if not isinstance(feed, dict) or not (feed.get("gameData") or {}).get("status"):
            raise ValueError("MLB game feed omitted gameData.status")
        _GAME_FEED_CACHE[game_pk] = feed
        return feed
    except Exception as exc:
        m.warn(f"Grading: couldn't fetch game {game_pk} by identity: {exc}")
        return None


def fetch_game_contexts(game_pks, refresh=False):
    """Return last current status/feed per game; failures remain absent."""
    contexts = {}
    for raw_game_pk in sorted({value for value in game_pks if value is not None}, key=str):
        try:
            game_pk = int(raw_game_pk)
        except (TypeError, ValueError):
            continue
        feed = fetch_game_feed(game_pk, refresh=refresh)
        if feed is None:
            continue
        contexts[game_pk] = {
            "status": ((feed.get("gameData") or {}).get("status") or {}),
            "feed": feed,
        }
    return contexts


def is_final(status):
    if not status: return False
    coded = status.get("codedGameState", "")
    detailed = status.get("detailedState", "")
    return coded in ("F", "O") or "final" in detailed.lower() or "completed" in detailed.lower()


_BOX_CACHE = {}

def get_box_line(game_pk, player_id, is_pitcher):
    # Cached per game_pk. Live this saves ~10 duplicate boxscore pulls a
    # morning (several picks routinely share a game); it becomes load-bearing
    # for backtest/engine.py, which grades every candidate on the slate rather
    # than the top 10 -- ~600 picks across ~15 games, i.e. 600 identical
    # boxscore fetches per date without this. Cached by game only: the
    # per-player scan below still runs on every call, so behaviour is
    # unchanged.
    if game_pk in _BOX_CACHE:
        box, err = _BOX_CACHE[game_pk]
        if box is None:
            return None, err
    else:
        try:
            box = m.statsapi.boxscore_data(game_pk)
            _BOX_CACHE[game_pk] = (box, None)
        except Exception as e:
            err = str(e)[:150]
            _BOX_CACHE[game_pk] = (None, err)
            return None, err
    sides = ["awayPitchers", "homePitchers"] if is_pitcher else ["awayBatters", "homeBatters"]
    for side in sides:
        for row in box.get(side, []):
            if row.get("personId") == player_id:
                return row, None
    return None, "player not found in box score (scratched or DNP)"


_LINESCORE_CACHE = {}

def fetch_first_inning_linescore(game_pk):
    """Cached per game_pk (multiple NRFI picks can share a game). Real bug found
    on review: grade_pick() used to grade every non-pitcher pick as total_bases
    and every pitcher pick as strikeouts, regardless of the pick's actual prop —
    silently mis-grading stolen-base/walk picks against total bases and NRFI/YRFI
    picks against a strikeout threshold that never made sense. This is the correct
    per-prop path for first_inning_run instead."""
    if game_pk in _LINESCORE_CACHE:
        return _LINESCORE_CACHE[game_pk]
    result = None
    try:
        r = m.retry_get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        innings = r.json().get("liveData", {}).get("linescore", {}).get("innings", [])
        result = innings[0] if innings else None
    except Exception as e:
        m.warn(f"Grading: couldn't fetch linescore for game {game_pk}: {e}")
    _LINESCORE_CACHE[game_pk] = result
    return result


_INNINGS_CACHE = {}

def _game_innings(game_pk):
    """Innings actually played. A rain-shortened or 7-inning doubleheader game
    gives every batter fewer chances than the pick assumed, which is context
    a bare hit/miss can't express.

    Cached per game_pk for the same reason get_box_line is -- this is called
    once per graded pick, and every pick in a game gets the same answer."""
    if game_pk in _INNINGS_CACHE:
        return _INNINGS_CACHE[game_pk]
    _INNINGS_CACHE[game_pk] = None   # negative-cache a failure too, don't retry per pick
    try:
        r = m.retry_get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20, retries=2)
        r.raise_for_status()
        n = len(r.json().get("liveData", {}).get("linescore", {}).get("innings", []))
        _INNINGS_CACHE[game_pk] = n
        return n
    except Exception:
        return None


_MOONSHOT_CACHE = {}

def _date_batter_moonshot(date, threshold_ft=420):
    """{(batter_id, game_pk): True/False, did he hit a threshold_ft+ HR that
    day}, one Statcast pull per date (cached), same reasoning and same
    per-date-pull pattern as _date_batter_hr_ev right below -- box scores
    have no hit distance either, so "To Hit a Moonshot" picks can only be
    graded from Statcast itself. Mirrors mlb_sources.moonshot_rates' own
    definition exactly: a real home run (not just any long batted ball)
    that travelled threshold_ft+ feet."""
    key = (date, threshold_ft)
    if key in _MOONSHOT_CACHE:
        return _MOONSHOT_CACHE[key]
    out = {}
    try:
        df = m.pyb.statcast(start_dt=date, end_dt=date)
        need = {"batter", "game_pk", "events", "hit_distance_sc"}
        if df is not None and not df.empty and need.issubset(df.columns):
            hr = df[(df["events"] == "home_run") & df["hit_distance_sc"].notna()]
            if not hr.empty:
                hr = hr.copy()
                hr["cleared"] = hr["hit_distance_sc"] >= threshold_ft
                cleared = hr.groupby(["batter", "game_pk"])["cleared"].max()
                out = {(int(b), int(g)): bool(v) for (b, g), v in cleared.items()}
    except Exception as e:
        m.warn(f"Grading: couldn't fetch Statcast for {date}: {e}")
    _MOONSHOT_CACHE[key] = out
    return out


_EV_CACHE = {}

def _date_batter_hr_ev(date, threshold_mph):
    """{(batter_id, game_pk): True/False, did he hit a HOME RUN at
    threshold_mph+ exit velocity that day} -- NOT "did he hit any ball
    that hard." One Statcast pull per (date, threshold) (cached) instead
    of one per pick.

    REAL BUG, found live 2026-08-14 from the actual FanDuel app's own
    "Full details" text for this market: "Laser = HR with Specified MPH
    Exit Velocity." This used to grade off peak exit velocity alone
    (_date_batter_peak_ev, now removed) -- a hard-hit groundout or single
    graded identically to a home run at the same speed. Confirmed two
    ways before the fix: real live market odds that same night (Fernando
    Tatis Jr. +650, Jo Adell +900 -- both far too long to be "any hard-hit
    ball," a common event) and the season data itself (unconditioned rate
    21.9% league average vs. 4.7% once conditioned on home_run, the
    latter tracking real market prices far better). Mirrors
    mlb_sources.hard_hit_game_rates's corrected definition and
    _date_batter_moonshot's identical shape/pattern right above -- box
    scores have no exit velocity at all, so this can only be graded from
    Statcast itself."""
    key = (date, threshold_mph)
    if key in _EV_CACHE:
        return _EV_CACHE[key]
    out = {}
    try:
        df = m.pyb.statcast(start_dt=date, end_dt=date)
        need = {"batter", "game_pk", "launch_speed", "events"}
        if df is not None and not df.empty and need.issubset(df.columns):
            bb = df[df["launch_speed"].notna()]
            if not bb.empty:
                bb = bb.copy()
                bb["cleared"] = (bb["events"] == "home_run") & (bb["launch_speed"] >= threshold_mph)
                cleared = bb.groupby(["batter", "game_pk"])["cleared"].max()
                out = {(int(b), int(g)): bool(v) for (b, g), v in cleared.items()}
    except Exception as e:
        m.warn(f"Grading: couldn't fetch Statcast for {date}: {e}")
    _EV_CACHE[key] = out
    return out


def _num(v, default=0):
    try: return float(v)
    except (TypeError, ValueError): return default


def opportunity_context(pick, row, game_pk):
    """Did this pick actually get a fair test?

    Grading answers "was the pick right". It does NOT answer "did the pick
    get a real chance to be right", and conflating those two corrupts every
    conclusion drawn from the record. A batter who pinch-hits once in the 8th
    and makes an out is a miss identical, in the data, to one who started and
    went 0-for-5 -- but only the second is evidence the model was wrong. The
    first is evidence of nothing at all.

    This matters specifically because the point of the accuracy record is to
    learn which SIGNALS work. If circumstance-invalidated picks are mixed in
    with genuine misses, signals get blamed for outcomes they never had a
    chance to influence, and the weights derived from that are wrong.

    Deliberately does NOT alter hit/miss. A miss stays a miss -- this only
    annotates, so analysis can ask "of picks that got a full opportunity,
    what's the rate?" without anyone having license to quietly discard
    inconvenient losses.
    """
    ctx = {}
    innings = _game_innings(game_pk)
    if innings is not None:
        ctx["game_innings"] = innings
        # Fewer than 9 means a shortened game (7-inning doubleheader, rain,
        # or a called game). Note 8.5 is normal -- a home team leading doesn't
        # bat in the 9th -- so 8 is NOT automatically short.
        ctx["shortened_game"] = innings < 8

    stat = (pick.get("projection") or {}).get("stat")
    if stat == "nrfi_combined":
        # Both-teams NRFI/YRFI resolves in the 1st inning, same as the
        # one-sided read below -- every game reaches it regardless of how
        # either start actually went.
        ctx["fair_test"] = True
        ctx["opportunity"] = "full (first inning always played)"
        return ctx

    if pick.get("type") == "pitcher":
        ip = _num(row.get("ip"), None) if row else None
        if ip is not None:
            ctx["actual_ip"] = ip
        if row and row.get("p"):
            ctx["pitch_count"] = _num(row.get("p"))
        if stat == "first_inning_run":
            # An NRFI/YRFI lean resolves in the 1st inning, which every game
            # reaches -- it always gets a fair test regardless of workload.
            ctx["fair_test"] = True
            ctx["opportunity"] = "full (first inning always played)"
        elif ip is None:
            ctx["fair_test"] = None
            ctx["opportunity"] = "unknown (no IP recorded)"
        elif ip < 4.0:
            ctx["fair_test"] = False
            ctx["opportunity"] = (f"limited — only {ip} IP; a strikeout prop can't be judged "
                                  f"when the start ended early (injury, blowout, or quick hook)")
        else:
            ctx["fair_test"] = True
            ctx["opportunity"] = f"full ({ip} IP)"
        return ctx

    # Batter
    if not row:
        ctx["fair_test"] = False
        ctx["opportunity"] = "none — did not appear"
        return ctx
    ab = _num(row.get("ab")); bb = _num(row.get("bb"))
    pa = ab + bb  # close enough; HBP/SF are rare and not exposed per-row here
    ctx["actual_ab"] = int(ab); ctx["actual_pa_est"] = int(pa)
    ctx["was_substitute"] = bool(row.get("substitution"))
    bo = row.get("battingOrder")
    if bo:
        try: ctx["batting_order"] = int(int(bo) / 100)
        except (TypeError, ValueError): pass
    if pa == 0:
        ctx["fair_test"] = False
        ctx["opportunity"] = "none — appeared but recorded no plate appearance"
    elif ctx["was_substitute"] and pa <= 2:
        ctx["fair_test"] = False
        ctx["opportunity"] = (f"limited — entered as a substitute with only {int(pa)} PA; "
                              f"the pick assumed a starter's workload")
    elif pa <= 2:
        ctx["fair_test"] = False
        ctx["opportunity"] = (f"limited — only {int(pa)} PA (early exit, shortened game, "
                              f"or removed for a pinch hitter)")
    else:
        ctx["fair_test"] = True
        ctx["opportunity"] = f"full ({int(pa)} PA)"
    return ctx


def _is_under_pick(pick):
    """True only for an explicitly identified under; never infer from name."""
    for key in ("bet_side", "market_side", "direction"):
        if str(pick.get(key) or "").strip().lower() == "under":
            return True
    return str(pick.get("prop") or "").strip().lower().startswith("under ")


def grade_pick(pick, game_statuses, date=None, allow_in_progress=False):
    game_pk = pick.get("game_pk")
    player_id = pick.get("player_id")
    if not game_pk or not player_id:
        return {**pick, "grade": "ungraded", "reason": "missing game_pk/player_id"}
    status = game_statuses.get(game_pk)
    final = is_final(status)
    if not final and not allow_in_progress:
        detail = (status or {}).get("detailedState", "unknown")
        return {**pick, "grade": "ungraded", "reason": f"game not final yet (status: {detail})"}

    stat = (pick.get("projection") or {}).get("stat")

    if stat == "combined_strikeouts":
        # The one prop family this pipeline settles from TWO player box
        # scores instead of one -- combo_player_ids carries both starters
        # (score_combined_strikeouts in generate_picks.py), since pick's own
        # player_id is only the away starter (kept for persistence, which
        # is keyed one-file-per-player and needs a single real id).
        ids = pick.get("combo_player_ids") or []
        if len(ids) != 2 or not all(ids):
            return {**pick, "grade": "ungraded", "reason": "missing combo_player_ids"}
        needs = (pick.get("projection") or {}).get("needs")
        if needs is None:
            return {**pick, "grade": "ungraded", "reason": "no needs threshold on pick"}
        total_k = 0
        for pid in ids:
            row, err = get_box_line(game_pk, pid, is_pitcher=True)
            if row is None:
                return {**pick, "grade": "ungraded",
                        "reason": f"box line unavailable for one starter: {err}"}
            total_k += float(row.get("k", 0) or 0)
        hit = total_k < needs if _is_under_pick(pick) else total_k >= needs
        return {**pick, "grade": "hit" if hit else "miss", "actual": total_k,
                "actual_stat": "combined_strikeouts",
                **(opportunity_context(pick, None, game_pk) if final else {})}

    if stat in ("hard_hit_105", "hard_hit_110"):
        # Not on the pick's own record -- the caller knows what date it's
        # grading (that's how it fetched game_statuses in the first place);
        # this is the one prop family where the box score literally cannot
        # answer the question, so it's the only branch that needs it.
        if not date:
            return {**pick, "grade": "ungraded", "reason": "no date supplied for Statcast lookup"}
        thr = 105 if stat == "hard_hit_105" else 110
        hr_ev = _date_batter_hr_ev(date, thr).get((player_id, game_pk))
        if hr_ev is None:
            return {**pick, "grade": "ungraded", "reason": "no batted-ball Statcast data for this game"}
        hit = hr_ev
        # opportunity_context's batter branch reads PA/substitute status off
        # a real box line -- passing None here (as this used to) makes EVERY
        # hard_hit pick fall into "row missing" and get marked fair_test=False,
        # "did not appear", even a graded HIT, which is a batted ball that by
        # definition required the batter to appear. Found via the backtest
        # coverage report: 5,914 hard_hit_105 rows, n_fair exactly 0. The box
        # score has no exit velocity (why this branch exists at all) but it
        # still has AB/BB/substitution, which is all opportunity_context
        # actually needs -- so fetch it like every other batter stat does.
        row, _ = get_box_line(game_pk, player_id, is_pitcher=False)
        return {**pick, "grade": "hit" if hit else "miss", "actual": hit,
                "actual_stat": "hr_at_exit_velocity",
                **opportunity_context(pick, row, game_pk)}

    if stat == "moonshot_420":
        # Same reasoning as hard_hit_105/110 immediately above: the box
        # score has no hit distance, so this is the one other prop family
        # that can only be graded from Statcast itself.
        if not date:
            return {**pick, "grade": "ungraded", "reason": "no date supplied for Statcast lookup"}
        cleared = _date_batter_moonshot(date, 420).get((player_id, game_pk))
        if cleared is None:
            return {**pick, "grade": "ungraded", "reason": "no batted-ball Statcast data for this game"}
        row, _ = get_box_line(game_pk, player_id, is_pitcher=False)
        return {**pick, "grade": "hit" if cleared else "miss", "actual": cleared,
                "actual_stat": "moonshot_420",
                **opportunity_context(pick, row, game_pk)}

    if stat == "first_inning_run":
        # away_sp pitches to the home team in the bottom of the 1st (after the away
        # team bats top 1st); home_sp pitches to the away team in the top of the 1st.
        side = pick.get("side")
        lean = pick.get("lean")
        # Verified live on a real gap: output/picks_2026-08-04.json was written
        # before generate_picks.py's write_json() added explicit side/lean keys
        # to first-inning candidates, so all 4 real NRFI picks in that file have
        # neither field -- every one came back "ungraded: linescore or side
        # unavailable" even though both values are fully recoverable from data
        # already on the pick. side is exact, not guessed: pick["team"] must
        # equal one half of pick["matchup"] ("{away} @ {home}") -- there is no
        # third option. lean is read off pick["prop"]'s literal text ("NRFI
        # lean (his starts)" / "YRFI lean (...)"), the same string that run
        # already generated, not re-derived from a threshold that could drift
        # from generate_picks.py's own. Re-graded 2026-08-04 with this in place:
        # all 4 now resolve (3 hit / 1 miss) against real box scores instead of
        # sitting ungraded forever.
        if side not in ("away", "home"):
            matchup_parts = [p.strip() for p in pick.get("matchup", "").split(" @ ")]
            team = pick.get("team")
            if team and len(matchup_parts) == 2:
                if team == matchup_parts[0]: side = "away"
                elif team == matchup_parts[1]: side = "home"
        if lean not in ("YRFI", "NRFI"):
            prop_text = (pick.get("prop") or "").upper()
            if "YRFI" in prop_text: lean = "YRFI"
            elif "NRFI" in prop_text: lean = "NRFI"
        inning1 = fetch_first_inning_linescore(game_pk)
        if not inning1 or side not in ("away", "home"):
            return {**pick, "grade": "ungraded", "reason": "linescore or side unavailable"}
        runs_against = inning1.get("home" if side == "away" else "away", {}).get("runs")
        if runs_against is None or lean not in ("YRFI", "NRFI"):
            return {**pick, "grade": "ungraded", "reason": "missing runs/lean data"}
        actual_yrfi = runs_against > 0
        hit = actual_yrfi if lean == "YRFI" else not actual_yrfi
        return {**pick, "grade": "hit" if hit else "miss", "actual": runs_against,
                "actual_stat": "first_inning_runs_allowed",
                **opportunity_context(pick, None, game_pk)}

    if stat == "nrfi_combined":
        # The real both-teams market: hit only if BOTH halves match the
        # lean (both scoreless for NRFI; at least one team scores for YRFI).
        lean = pick.get("lean")
        inning1 = fetch_first_inning_linescore(game_pk)
        if not inning1 or lean not in ("YRFI", "NRFI"):
            return {**pick, "grade": "ungraded", "reason": "linescore or lean unavailable"}
        away_runs = inning1.get("away", {}).get("runs")
        home_runs = inning1.get("home", {}).get("runs")
        if away_runs is None or home_runs is None:
            return {**pick, "grade": "ungraded", "reason": "missing runs data"}
        actual_yrfi = (away_runs > 0) or (home_runs > 0)
        hit = actual_yrfi if lean == "YRFI" else not actual_yrfi
        return {**pick, "grade": "hit" if hit else "miss",
                "actual": away_runs + home_runs,
                "actual_stat": "first_inning_runs_total",
                **opportunity_context(pick, None, game_pk)}

    is_pitcher = pick["type"] == "pitcher"
    row, err = get_box_line(game_pk, player_id, is_pitcher)
    if row is None:
        return {**pick, "grade": "ungraded", "reason": err,
                **opportunity_context(pick, None, game_pk)}
    try:
        if stat == "strikeouts":
            actual = float(row.get("k", 0) or 0)
            actual_stat = "strikeouts"
        elif stat == "stolen_base":
            actual = float(row.get("sb", 0) or 0)
            actual_stat = "stolen_bases"
        elif stat == "walks":
            actual = float(row.get("bb", 0) or 0)
            actual_stat = "walks"
        elif stat == "hits":
            actual = float(row.get("h", 0) or 0)
            actual_stat = "hits"
        elif stat == "home_runs":
            actual = float(row.get("hr", 0) or 0)
            actual_stat = "home_runs"
        elif stat == "runs":
            actual = float(row.get("r", 0) or 0)
            actual_stat = "runs"
        elif stat == "rbis":
            actual = float(row.get("rbi", 0) or 0)
            actual_stat = "rbis"
        elif stat == "hits_runs_rbis":
            # The market is literally the sum, including the double-count when
            # a player drives himself in on a home run -- that is how FanDuel
            # settles it (verified against mlb_sources._empirical_batter_one's
            # own "hits_runs_rbis": h + runs_ + rbi_, the same convention this
            # market's empirical rate table already uses), so that is how this
            # has to grade too.
            actual = (float(row.get("h", 0) or 0) + float(row.get("r", 0) or 0)
                      + float(row.get("rbi", 0) or 0))
            actual_stat = "hits_runs_rbis"
        elif stat == "singles":
            h = int(row.get("h", 0) or 0)
            d = int(row.get("doubles", 0) or 0)
            t = int(row.get("triples", 0) or 0)
            hr = int(row.get("hr", 0) or 0)
            actual = float(max(0, h - d - t - hr))
            actual_stat = "singles"
        elif stat == "doubles":
            actual = float(row.get("doubles", 0) or 0)
            actual_stat = "doubles"
        elif stat == "triples":
            actual = float(row.get("triples", 0) or 0)
            actual_stat = "triples"
        elif stat == "pitcher_outs":
            # Same whole.frac -> outs conversion mlb_sources.empirical_pitcher_outs_rates
            # and score_pitcher_outs already use, reading MLB's own
            # inningsPitched notation directly rather than reconstructing
            # outs from raw events -- box scores already resolve double
            # plays, sac flies, etc. correctly server-side.
            ip = str(row.get("ip") or "0")
            whole, _, frac = ip.partition(".")
            actual = float(int(whole) * 3 + int(frac or 0))
            actual_stat = "pitcher_outs"
        elif stat == "total_bases":
            # Verified against generate_picks.py's own prop-text branches (the three
            # f-strings feeding off project_batter_tb(), ~line 690-696): unlike
            # strikeouts -- where the number shown to the user IS proj-0.5, e.g.
            # f"Over {projected_ks-0.5} Strikeouts" -- none of "Home Run / 2+ Total
            # Bases", "Over 1.5 Hits", or "Over 1.5 Total Bases" ever interpolates the
            # projection into its line; all three are hardcoded fixed lines, and only
            # the "(proj. X TB)" parenthetical moves with the model's number. Grading
            # this against the generic proj-0.5 formula was silently wrong in both
            # directions and contradicted this file's own docstring promise to grade
            # "the same convention...commits to when writing the prop text": a real
            # 2026-08-04 pick (Griffin Conine, proj 2.6 TB, "2+ Total Bases") got
            # threshold 2.1 -- requiring 3+ TB to grade a hit though the displayed line
            # only needs 2+ -- and a low-projection pick (proj < 1.5, still routed into
            # the "Over 1.5 TB" branch) would let a mere single (1 TB) grade as a false
            # hit. "Over 1.5 Hits" is a different stat entirely (literal hit count, not
            # total bases) that generate_picks.py still tags "total_bases" (all three
            # branches share one projection dict) -- route it to the real box-score
            # field instead of total bases.
            if "Hits" in pick.get("prop", "").split("(")[0]:
                actual = float(row.get("h", 0) or 0)
                actual_stat = "hits"
            else:
                h = int(row.get("h", 0) or 0)
                d = int(row.get("doubles", 0) or 0)
                t = int(row.get("triples", 0) or 0)
                hr = int(row.get("hr", 0) or 0)
                actual = (h - d - t - hr) * 1 + d * 2 + t * 3 + hr * 4
                actual_stat = "total_bases"
        else:
            return {**pick, "grade": "ungraded", "reason": f"unrecognized projection stat '{stat}'"}
    except (TypeError, ValueError):
        return {**pick, "grade": "ungraded", "reason": "unparseable box score line"}

    proj = (pick.get("projection") or {}).get("value")
    if proj is None:
        return {**pick, "grade": "ungraded", "reason": "no projection on pick"}
    # total_bases/hits grade against the fixed 1.5 line generate_picks.py actually
    # displays (see above) -- strikeouts/stolen_base/walks genuinely are
    # proj-0.5 per their own f-strings ("Over {ks-0.5} Strikeouts", stolen_base's
    # fixed value=1 -> 0.5, walks' fixed value=0.7 -> 0.2, all matching their
    # displayed "Over 0.5"-style lines exactly).
    # A pick made after the hit-probability pass carries the integer count it
    # actually needs ("needs": 2 for an Over 1.5), because the threshold is now
    # CHOSEN by which line is most likely to cash rather than inferred from a
    # projection. When it's there, use it -- it is the recommendation itself,
    # not a reconstruction of one, and it removes the whole class of bug where
    # the graded threshold and the displayed line disagreed.
    needs = (pick.get("projection") or {}).get("needs")
    if needs is not None:
        threshold = float(needs) - 0.5
    else:
        # Legacy picks (generated before thresholds were chosen by probability)
        # keep the old reconstruction, which is documented at length above.
        threshold = 1.5 if stat == "total_bases" else proj - 0.5
    hit = actual < threshold if _is_under_pick(pick) else actual > threshold
    return {**pick, "grade": "hit" if hit else "miss", "actual": actual,
            "actual_stat": actual_stat, "threshold": threshold,
            **(opportunity_context(pick, row, game_pk) if final else {})}


def grade_public_pick(pick, context, date=None):
    """Authoritative public settlement with structured action eligibility."""
    from dashboard.live_state import game_state
    from dashboard.settlement_rules import settlement_eligibility

    context = context or {}
    status = context.get("status") or {}
    feed = context.get("feed")
    current_game_state = game_state(status)
    eligibility = settlement_eligibility(pick, feed, current_game_state)
    if eligibility["eligibility"] == "void":
        return {
            **pick, "grade": "void", "settlement_state": "void",
            "reason": eligibility["reason_code"], "eligibility": eligibility,
        }
    if eligibility["eligibility"] not in ("eligible", "conditional"):
        return {
            **pick, "grade": "ungraded", "settlement_state": "ungraded",
            "reason": eligibility["reason_code"], "eligibility": eligibility,
        }
    result = grade_pick(pick, {pick.get("game_pk"): status}, date=date)
    result["eligibility"] = eligibility
    result["settlement_state"] = result.get("grade", "ungraded")
    if eligibility["eligibility"] == "conditional":
        stat = (pick.get("projection") or {}).get("stat") or pick.get("stat")
        under = _is_under_pick(pick)
        unequivocal = (
            stat == "nrfi_combined"
            or (not under and result.get("grade") == "hit")
            or (under and result.get("grade") == "miss")
        )
        if not unequivocal:
            return {
                **pick, "grade": "ungraded", "settlement_state": "ungraded",
                "reason": eligibility["reason_code"], "eligibility": eligibility,
            }
    return result


def merge_durable_public_result(previous, incoming):
    """Preserve authoritative public settlement across retries/failures.

    A new official hit/miss/void may correct an older official result. An
    unavailable/unsupported retry may not erase one. Repeating an identical
    final observation keeps the prior record byte-stable, including its first
    authoritative observation timestamp.
    """
    if not previous:
        return incoming
    ranks = {"none": 0, "live_observation": 1, "official_final": 2}
    previous_rank = ranks.get(previous.get("settlement_authority"), -1)
    incoming_rank = ranks.get(incoming.get("settlement_authority"), -1)
    if incoming_rank < previous_rank:
        return previous
    previous_terminal = previous.get("settlement_state") in ("hit", "miss", "void")
    incoming_terminal = incoming.get("settlement_state") in ("hit", "miss", "void")
    if previous_terminal and not incoming_terminal:
        return previous
    if previous_rank == incoming_rank == 2 and previous_terminal and incoming_terminal:
        semantic_fields = (
            "grade", "settlement_state", "actual", "actual_stat", "threshold",
            "reason", "eligibility",
        )
        if all(previous.get(field) == incoming.get(field) for field in semantic_fields):
            return previous
    return incoming


def grade_day(date) -> bool:
    """Grade one date. Returns True if it wrote grades, False if it no-opped."""
    PICKS_JSON = picks_path(date)
    GRADES_FILE = grades_path(date)
    YESTERDAY = date  # local alias: this function used to be hardcoded to yesterday
    from dashboard.publication_registry import load_registry, published_snapshots_for_date
    registry = load_registry(PUBLIC_REGISTRY_FILE)
    public_picks = published_snapshots_for_date(registry, date)
    payload = {}
    picks = []
    if not os.path.exists(PICKS_JSON):
        print(f"No canonical picks file for {YESTERDAY} ({PICKS_JSON}).")
    # generate_picks.py writes this file with a direct json.dump (no temp-file +
    # atomic rename), so a process kill mid-write (OOM, step timeout) can leave it
    # truncated/invalid. Same "must never block the rest of the pipeline" contract
    # as the missing-file case above -- a decode failure degrades to a no-op
    # instead of an uncaught exception that would kill this step and, with no
    # continue-on-error on it, the "Run daily pipeline" step behind it.
    else:
        try:
            with open(PICKS_JSON, encoding="utf-8") as f:
                payload = json.load(f)
            picks = payload.get("picks", [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"Canonical picks file for {YESTERDAY} unreadable ({e}); "
                  "public registry grading remains independent.")
    if not picks and not public_picks:
        print(f"No canonical picks or deployment-proven public Top Picks for {YESTERDAY}.")
        return False

    game_statuses = fetch_game_statuses(YESTERDAY) if picks else {}
    # Verified live: grade_pick() reads pick["type"] via direct bracket access (kept
    # that way deliberately -- see below), and this module's docstring commits to
    # "never block the rest of the pipeline". But before this, nothing stood between
    # a single malformed pick record and the whole batch: a pick missing "type"
    # raised an uncaught KeyError that propagated straight out of this
    # comprehension, out of main(), and killed grade_results.py's own exit code.
    # Since the workflow's "Grade yesterday's picks" step has no continue-on-error,
    # that failure would have stopped the job before "Run daily pipeline" ever ran
    # -- the exact opposite of the documented guarantee, for the exact kind of
    # schema-drift bug this project already hit once for real (the missing
    # side/lean fields fixed above). Deliberately NOT papering over pick["type"]
    # with .get() -- a pick missing a required field should surface as a visible
    # "ungraded: grader error" below, not silently default to batter semantics and
    # grade nonsense data as a real hit/miss. One bad pick record now degrades to a
    # single ungraded entry instead of taking down the whole run.
    graded = []
    for p in picks:
        try:
            graded.append(grade_pick(p, game_statuses, date=YESTERDAY))
        except Exception as e:
            graded.append({**p, "grade": "ungraded", "reason": f"grader error: {e}"})

    # Public Top Picks are graded from immutable first-exposure snapshots,
    # never from the later overwriteable canonical board. Direct game_pk
    # lookup keeps prior-slate West Coast/suspended games gradeable after UTC
    # rollover. On source failure, retain the prior logical record for retry.
    prior_public = {}
    if os.path.exists(GRADES_FILE):
        try:
            with open(GRADES_FILE, encoding="utf-8") as handle:
                prior_grade_payload = json.load(handle)
            prior_public = {row.get("id"): row for row in
                            (prior_grade_payload.get("public_top_picks") or []) if row.get("id")}
        except (OSError, json.JSONDecodeError):
            prior_public = {}
    public_contexts = fetch_game_contexts(
        [pick.get("game_pk") for pick in public_picks], refresh=True,
    ) if public_picks else {}
    from dashboard.live_state import utc_now
    public_observed_at = utc_now()
    public_graded = []
    for pick in public_picks:
        try:
            game_pk = int(pick.get("game_pk"))
        except (TypeError, ValueError):
            game_pk = None
        context = public_contexts.get(game_pk)
        if context is None:
            previous = prior_public.get(pick.get("id"))
            if previous:
                public_graded.append(previous)
            else:
                public_graded.append({
                    **pick, "grade": "ungraded", "settlement_state": "ungraded",
                    "reason": "MLB game feed unavailable by game_pk; retryable",
                })
            continue
        try:
            result = {**pick, **grade_public_pick(pick, context, date=YESTERDAY)}
        except Exception as exc:
            result = {**pick, "grade": "ungraded", "settlement_state": "ungraded",
                      "reason": f"public grader error: {exc}"}
        from dashboard.live_state import game_state as lifecycle_game_state
        authoritative = lifecycle_game_state(context.get("status")) == "final"
        result["settlement_authority"] = "official_final" if authoritative else "none"
        result["settlement_observed_at"] = public_observed_at
        result["settlement_source"] = (
            "durable_morning_grader" if authoritative else "durable_morning_status_observation"
        )
        public_graded.append(merge_durable_public_result(
            prior_public.get(pick.get("id")), result,
        ))
    hits = sum(1 for g in graded if g["grade"] == "hit")
    misses = sum(1 for g in graded if g["grade"] == "miss")
    ungraded = sum(1 for g in graded if g["grade"] == "ungraded")
    # Tracked alongside, never instead of, the raw numbers. A pick that never
    # got a real chance (2 PA off the bench, a start cut short) is evidence
    # about circumstance, not about whether the model's read was right --
    # counting it as a signal failure teaches the wrong lesson. Raw stays the
    # headline so nothing can be quietly excused.
    fair_hits = sum(1 for g in graded if g["grade"] == "hit" and g.get("fair_test"))
    fair_misses = sum(1 for g in graded if g["grade"] == "miss" and g.get("fair_test"))

    # BY CATEGORY, ADDITIVE ONLY -- found 2026-08-12 while catching up 5 days
    # of ungraded picks: the raw/fair totals above blend three groups with
    # deliberately very different intended hit rates into one number --
    # "main" (the actual top10 recommendation, meant to run 60-80%),
    # "moonshot" (home runs, meant to run 15-25% by design), and
    # "best_of_category" (explicitly includes sub-60%-floor picks, flagged
    # with a warning icon on the board for exactly this reason). A reader
    # of the blended headline (measured 45.2% over the 5 days just caught
    # up) would reasonably conclude the model is barely better than a coin
    # flip, when the main board alone measured 57.8% over the same window
    # -- still worth watching, but a very different picture. New keys only;
    # every existing key here is untouched, so nothing already reading this
    # file breaks.
    _GRADE_TO_KEY = {"hit": "hits", "miss": "misses", "void": "voids",
                     "ungraded": "ungraded"}
    by_category = defaultdict(lambda: {"hits": 0, "misses": 0, "ungraded": 0})
    for g in graded:
        cat = g.get("category") or "main"
        by_category[cat][_GRADE_TO_KEY[g["grade"]]] += 1
    by_category = {k: dict(v) for k, v in by_category.items()}

    # BY RECOMMENDATION STATUS, same additive-only reasoning as by_category
    # above, but along the axis the 2026-08-15 rebuild actually introduced:
    # top_pick/lean/value/neutral (recommendation.classify_recommendation),
    # not the prop-family axis by_category already covers. Direct request:
    # "The public Top Pick hit rate should measure the bets Full Count
    # actually designated as Top Picks" -- by_category's "main" bucket is
    # every pick that reached the top10 board by rank_for_board's ordering,
    # which is NOT the same claim as "this specific pick cleared the real
    # Top Pick floor" (a top10 pick can legitimately be a Lean-grade read
    # that simply out-ranked everything else on a thin night). Picks
    # graded before this rebuild shipped have no recommendation_status at
    # all -- bucketed "unclassified" rather than guessed into one of the
    # four real states, so old and new results are never silently blended
    # under a label neither the model nor the audit ever actually assigned.
    # Preserve the established all-modelled-recommendations population on this
    # axis. Deployment-proven public Top Picks are tracked separately below;
    # replacing this bucket with the public subset would erase analytical
    # history for qualified-but-never-deployed recommendations.
    by_rec_status = defaultdict(lambda: {"hits": 0, "misses": 0, "ungraded": 0})
    for g in graded:
        rs = g.get("recommendation_status") or "unclassified"
        key = _GRADE_TO_KEY.get(g.get("grade"), "ungraded")
        by_rec_status[rs]["ungraded" if key == "voids" else key] += 1
    public_counts = {"hits": 0, "misses": 0, "voids": 0, "ungraded": 0}
    for result in public_graded:
        key = _GRADE_TO_KEY.get(result.get("grade"), "ungraded")
        public_counts[key] += 1
    if public_graded:
        by_rec_status["top_pick"] = dict(public_counts)
    by_rec_status = {k: dict(v) for k, v in by_rec_status.items()}

    # SHADOW TRACKING -- direct request: "There should be no prop not rated
    # and bet on to know the hit percentage... I understand if it isn't
    # included in the final card but I still want to know." generate_picks.
    # select_shadow_tracking() recovers the alternate thresholds (hard_hit_110
    # chief among them) that _pick_line demoted on every candidate, so their
    # real hit rate can finally be measured. Graded through the exact same
    # grade_pick() path as everything else, but kept in a COMPLETELY separate
    # key from hits/misses/by_category above -- these were never bettable
    # picks, so folding them into the headline number would understate the
    # real board's accuracy by diluting it with picks nobody could place.
    shadow_picks = payload.get("shadow_tracking", [])
    shadow_graded = []
    for p in shadow_picks:
        try:
            shadow_graded.append(grade_pick(p, game_statuses, date=YESTERDAY))
        except Exception as e:
            shadow_graded.append({**p, "grade": "ungraded", "reason": f"grader error: {e}"})
    shadow_by_category = defaultdict(lambda: {"hits": 0, "misses": 0, "ungraded": 0})
    for g in shadow_graded:
        key = f"{(g.get('projection') or {}).get('stat')}@{(g.get('projection') or {}).get('needs')}"
        shadow_by_category[key][_GRADE_TO_KEY[g["grade"]]] += 1
    shadow_by_category = {k: dict(v) for k, v in shadow_by_category.items()}

    from dashboard.live_state import atomic_write_json
    atomic_write_json(GRADES_FILE, {
        "date": YESTERDAY, "hits": hits, "misses": misses, "ungraded": ungraded,
        "fair_hits": fair_hits, "fair_misses": fair_misses,
        "by_category": by_category,
        "by_recommendation_status": by_rec_status,
        "public_top_pick_counts": public_counts,
        "public_top_picks": public_graded,
        "shadow_by_category": shadow_by_category,
        "picks": graded,
        "shadow_tracking": shadow_graded,
    }, indent=2)

    history = {"days": []}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history["days"] = [d for d in history.get("days", []) if d["date"] != YESTERDAY]  # avoid dup on reruns
    history["days"].append({"date": YESTERDAY, "hits": hits, "misses": misses, "ungraded": ungraded,
                            "fair_hits": fair_hits, "fair_misses": fair_misses,
                            "by_category": by_category, "by_recommendation_status": by_rec_status,
                            "public_top_pick_counts": public_counts,
                            "shadow_by_category": shadow_by_category})
    history["days"].sort(key=lambda d: d["date"])

    totals = {"hits": sum(d["hits"] for d in history["days"]),
              "misses": sum(d["misses"] for d in history["days"]),
              "ungraded": sum(d["ungraded"] for d in history["days"])}
    history["totals"] = totals
    graded_total = totals["hits"] + totals["misses"]
    history["overall_hit_rate"] = round(totals["hits"] / graded_total, 3) if graded_total else None
    fh = sum(d.get("fair_hits", 0) for d in history["days"])
    fm = sum(d.get("fair_misses", 0) for d in history["days"])
    history["fair_test_totals"] = {"hits": fh, "misses": fm}
    history["fair_test_hit_rate"] = round(fh / (fh + fm), 3) if (fh + fm) else None

    # BY-CATEGORY TOTALS, same reasoning as the per-day file above: the
    # blended overall_hit_rate mixes "main" (the actual top10 recommendation)
    # with "moonshot" (deliberately long-shot HR bets) and
    # "best_of_category" (deliberately includes sub-floor picks). main_
    # hit_rate is the number that answers "how is the board Jacob would
    # actually bet actually doing" -- the blended headline above cannot
    # answer that on its own. Days written before this change have no
    # by_category key at all (.get(..., {}) below), so they're silently
    # excluded from this breakdown rather than crashing or fabricating one.
    cat_totals = defaultdict(lambda: {"hits": 0, "misses": 0, "ungraded": 0})
    for d in history["days"]:
        for cat, counts in d.get("by_category", {}).items():
            for k in ("hits", "misses", "ungraded"):
                cat_totals[cat][k] += counts.get(k, 0)
    history["by_category_totals"] = {k: dict(v) for k, v in cat_totals.items()}
    main = cat_totals.get("main")
    if main and (main["hits"] + main["misses"]) > 0:
        history["main_hit_rate"] = round(main["hits"] / (main["hits"] + main["misses"]), 3)
    else:
        history["main_hit_rate"] = None

    # RECOMMENDATION-STATUS TOTALS -- the rebuild's own axis, accumulated
    # across days the same way by_category_totals is. This is what makes
    # "the public Top Pick hit rate measures the bets Full Count actually
    # designated as Top Picks" a checkable fact rather than a promise: it
    # counts only picks that carried recommendation_status=="top_pick" on
    # the day they were made, never a pick that merely reached the top10
    # board or scored well by quality score alone.
    rec_status_totals = defaultdict(lambda: {"hits": 0, "misses": 0,
                                              "ungraded": 0})
    for d in history["days"]:
        for rs, counts in d.get("by_recommendation_status", {}).items():
            for k in ("hits", "misses", "ungraded"):
                rec_status_totals[rs][k] += counts.get(k, 0)
    history["by_recommendation_status_totals"] = {k: dict(v) for k, v in rec_status_totals.items()}
    modeled_tp = rec_status_totals.get("top_pick")
    if modeled_tp and (modeled_tp["hits"] + modeled_tp["misses"]) > 0:
        history["modeled_top_pick_hit_rate"] = round(
            modeled_tp["hits"] / (modeled_tp["hits"] + modeled_tp["misses"]), 3,
        )
    else:
        history["modeled_top_pick_hit_rate"] = None

    # PUBLIC TOP PICK TOTALS -- the official customer-facing record is based
    # only on deployment-proven registry snapshots. This is deliberately
    # separate from by_recommendation_status_totals, which continues to track
    # every modelled recommendation and therefore preserves its old semantics.
    public_totals = {"hits": 0, "misses": 0, "voids": 0, "ungraded": 0}
    for d in history["days"]:
        counts = d.get("public_top_pick_counts") or {}
        for key in public_totals:
            public_totals[key] += counts.get(key, 0)
    history["public_top_pick_totals"] = public_totals
    public_graded_total = public_totals["hits"] + public_totals["misses"]
    history["top_pick_hit_rate"] = (
        round(public_totals["hits"] / public_graded_total, 3)
        if public_graded_total else None
    )

    # SHADOW TOTALS -- accumulates every alternate-threshold pick's real
    # outcome across days, keyed "stat@needs" (e.g. "hard_hit_110@1"), so
    # "what's hard_hit_110's real hit rate" becomes answerable from history
    # instead of needing a fresh one-off pull like the first time this was
    # asked. Deliberately its own top-level key, never merged into
    # by_category_totals/overall_hit_rate above.
    shadow_totals = defaultdict(lambda: {"hits": 0, "misses": 0, "ungraded": 0})
    for d in history["days"]:
        for cat, counts in d.get("shadow_by_category", {}).items():
            for k in ("hits", "misses", "ungraded"):
                shadow_totals[cat][k] += counts.get(k, 0)
    history["shadow_by_category_totals"] = {k: dict(v) for k, v in shadow_totals.items()}

    # Rolling last-14-day rate, so a slow start doesn't permanently anchor the headline number
    recent = history["days"][-14:]
    recent_graded = sum(d["hits"] + d["misses"] for d in recent)
    history["last_14_days_hit_rate"] = (
        round(sum(d["hits"] for d in recent) / recent_graded, 3) if recent_graded else None
    )

    # ROLLING 14-DAY TOP-PICK-ONLY RATE -- deliberately a SEPARATE number
    # from last_14_days_hit_rate above, never displayed as though the two
    # are the same claim. Direct instruction: "do not display blended
    # 14-day record beside Top Pick record as comparable... track Top
    # Picks/Best Value/Longshots/Leans independently." last_14_days_hit_rate
    # blends every graded pick (main board + moonshots + best-of-category,
    # the audit's own concern (c) -- Top Picks mixed with longshots/other
    # categories in the headline number); this counts only the picks that
    # actually carried recommendation_status=="top_pick" on the day they
    # shipped. Days graded before this rebuild have no
    # by_recommendation_status at all, so they contribute zero to this
    # window rather than being silently folded in as unclassified hits.
    recent_modeled_tp_hits = sum(
        d.get("by_recommendation_status", {}).get("top_pick", {}).get("hits", 0)
        for d in recent
    )
    recent_modeled_tp_misses = sum(
        d.get("by_recommendation_status", {}).get("top_pick", {}).get("misses", 0)
        for d in recent
    )
    recent_modeled_tp_n = recent_modeled_tp_hits + recent_modeled_tp_misses
    history["last_14_days_modeled_top_pick_hit_rate"] = (
        round(recent_modeled_tp_hits / recent_modeled_tp_n, 3)
        if recent_modeled_tp_n else None
    )
    history["last_14_days_modeled_top_pick_n"] = recent_modeled_tp_n
    recent_tp_hits = sum(
        (d.get("public_top_pick_counts") or {}).get("hits", 0) for d in recent
    )
    recent_tp_misses = sum(
        (d.get("public_top_pick_counts") or {}).get("misses", 0) for d in recent
    )
    recent_tp_graded = recent_tp_hits + recent_tp_misses
    history["last_14_days_top_pick_hit_rate"] = (
        round(recent_tp_hits / recent_tp_graded, 3) if recent_tp_graded else None
    )
    history["last_14_days_top_pick_n"] = recent_tp_graded

    atomic_write_json(HISTORY_FILE, history, indent=2)

    day_rate = round(hits / (hits + misses), 3) if (hits + misses) else "n/a"
    fair_rate = round(fair_hits / (fair_hits + fair_misses), 3) if (fair_hits + fair_misses) else "n/a"
    print(f"Graded {YESTERDAY}: {hits} hits / {misses} misses / {ungraded} ungraded (day rate: {day_rate})")
    print(f"  Of picks that got a fair test: {fair_hits} hits / {fair_misses} misses (rate: {fair_rate})")
    print(f"Overall to date: {totals['hits']} hits / {totals['misses']} misses "
          f"(rate: {history['overall_hit_rate']}, last 14 days: {history['last_14_days_hit_rate']})")
    if history["main_hit_rate"] is not None:
        m_totals = history["by_category_totals"].get("main", {})
        print(f"  Main board only (the actual top10 recommendation, excludes moonshots/"
              f"best-of-category): {m_totals.get('hits', 0)} hits / {m_totals.get('misses', 0)} "
              f"misses (rate: {history['main_hit_rate']})")
    if history["top_pick_hit_rate"] is not None:
        tp_totals = history["public_top_pick_totals"]
        # Deliberately printed as its own line, never averaged with the
        # blended rate above -- this is the number the rebuild exists to
        # make trustworthy: only picks recommendation.classify_recommendation
        # actually designated top_pick, not "reached the top10 board."
        print(f"  Public Top Picks only (deployment-proven exposure): "
              f"{tp_totals.get('hits', 0)} hits / {tp_totals.get('misses', 0)} misses "
              f"(all-time rate: {history['top_pick_hit_rate']}, last 14 days: "
              f"{history['last_14_days_top_pick_hit_rate']} over "
              f"{history['last_14_days_top_pick_n']} graded)")
    return True


def main() -> int:
    """Grades yesterday plus any earlier day left ungraded (see
    dates_needing_grading). An explicit GRADE_DATE env var still forces a
    single specific day, which is what manual re-grades use."""
    if os.environ.get("GRADE_DATE"):
        grade_day(os.environ["GRADE_DATE"])
        return 0
    # Report gaps BEFORE grading, so a missed day is the first thing seen in
    # the log rather than something to be inferred from its absence.
    gaps = days_with_no_board()
    if gaps:
        print(f"  MISSED DAYS ({len(gaps)}) — a slate was played and this pipeline "
              f"produced no board:")
        for d, n in gaps:
            print(f"    {d}  ({n} games)")
        print("  These cannot be recovered: picks are a point-in-time read and")
        print("  the lineups, prices and weather behind them are gone. Check")
        print("  whether the scheduled run was assigned a runner at all.")
        print()

    pending = dates_needing_grading()
    if not pending:
        print("Nothing to grade — all recent days with picks are already fully graded.")
        return 0
    if len(pending) > 1:
        print(f"Catch-up: {len(pending)} day(s) need grading: {', '.join(pending)}")
    for d in pending:
        try:
            grade_day(d)
        except Exception as e:
            # One bad day must not stop the others, and must never fail the
            # workflow step -- same contract as the rest of this module.
            m.warn(f"Grading {d} failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
