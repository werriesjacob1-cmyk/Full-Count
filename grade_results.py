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
from datetime import datetime, timedelta

import mlb_daily as m

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

YESTERDAY = os.environ.get("GRADE_DATE") or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
HISTORY_FILE = os.path.join(RESULTS_DIR, "history.json")

# How far back a catch-up pass will look. Bounded so a long-running season
# doesn't re-walk hundreds of days every morning, and so genuinely
# unresolvable days (a pick whose player was scratched, say) stop being
# retried forever.
CATCHUP_WINDOW_DAYS = 14


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
    return sorted(out)


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


def grade_pick(pick, game_statuses):
    game_pk = pick.get("game_pk")
    player_id = pick.get("player_id")
    if not game_pk or not player_id:
        return {**pick, "grade": "ungraded", "reason": "missing game_pk/player_id"}
    status = game_statuses.get(game_pk)
    if not is_final(status):
        detail = (status or {}).get("detailedState", "unknown")
        return {**pick, "grade": "ungraded", "reason": f"game not final yet (status: {detail})"}

    stat = (pick.get("projection") or {}).get("stat")

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
    hit = actual > threshold
    return {**pick, "grade": "hit" if hit else "miss", "actual": actual,
            "actual_stat": actual_stat, "threshold": threshold,
            **opportunity_context(pick, row, game_pk)}


def grade_day(date) -> bool:
    """Grade one date. Returns True if it wrote grades, False if it no-opped."""
    PICKS_JSON = picks_path(date)
    GRADES_FILE = grades_path(date)
    YESTERDAY = date  # local alias: this function used to be hardcoded to yesterday
    if not os.path.exists(PICKS_JSON):
        print(f"No picks file for {YESTERDAY} ({PICKS_JSON}) — nothing to grade.")
        return 0
    # generate_picks.py writes this file with a direct json.dump (no temp-file +
    # atomic rename), so a process kill mid-write (OOM, step timeout) can leave it
    # truncated/invalid. Same "must never block the rest of the pipeline" contract
    # as the missing-file case above -- a decode failure degrades to a no-op
    # instead of an uncaught exception that would kill this step and, with no
    # continue-on-error on it, the "Run daily pipeline" step behind it.
    try:
        with open(PICKS_JSON, encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Picks file for {YESTERDAY} unreadable ({e}) — nothing to grade.")
        return 0
    picks = payload.get("picks", [])
    if not picks:
        print(f"No picks recorded for {YESTERDAY} — nothing to grade.")
        return False

    game_statuses = fetch_game_statuses(YESTERDAY)
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
            graded.append(grade_pick(p, game_statuses))
        except Exception as e:
            graded.append({**p, "grade": "ungraded", "reason": f"grader error: {e}"})
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

    with open(GRADES_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": YESTERDAY, "hits": hits, "misses": misses, "ungraded": ungraded,
                   "fair_hits": fair_hits, "fair_misses": fair_misses,
                   "picks": graded}, f, indent=2)

    history = {"days": []}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history["days"] = [d for d in history.get("days", []) if d["date"] != YESTERDAY]  # avoid dup on reruns
    history["days"].append({"date": YESTERDAY, "hits": hits, "misses": misses, "ungraded": ungraded,
                            "fair_hits": fair_hits, "fair_misses": fair_misses})
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

    # Rolling last-14-day rate, so a slow start doesn't permanently anchor the headline number
    recent = history["days"][-14:]
    recent_graded = sum(d["hits"] + d["misses"] for d in recent)
    history["last_14_days_hit_rate"] = (
        round(sum(d["hits"] for d in recent) / recent_graded, 3) if recent_graded else None
    )

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    day_rate = round(hits / (hits + misses), 3) if (hits + misses) else "n/a"
    fair_rate = round(fair_hits / (fair_hits + fair_misses), 3) if (fair_hits + fair_misses) else "n/a"
    print(f"Graded {YESTERDAY}: {hits} hits / {misses} misses / {ungraded} ungraded (day rate: {day_rate})")
    print(f"  Of picks that got a fair test: {fair_hits} hits / {fair_misses} misses (rate: {fair_rate})")
    print(f"Overall to date: {totals['hits']} hits / {totals['misses']} misses "
          f"(rate: {history['overall_hit_rate']}, last 14 days: {history['last_14_days_hit_rate']})")
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
