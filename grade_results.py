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
import os, sys, json
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


def get_box_line(game_pk, player_id, is_pitcher):
    try:
        box = m.statsapi.boxscore_data(game_pk)
    except Exception as e:
        return None, str(e)[:150]
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
                "actual_stat": "first_inning_runs_allowed"}

    is_pitcher = pick["type"] == "pitcher"
    row, err = get_box_line(game_pk, player_id, is_pitcher)
    if row is None:
        return {**pick, "grade": "ungraded", "reason": err}
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
    threshold = 1.5 if stat == "total_bases" else proj - 0.5
    hit = actual > threshold
    return {**pick, "grade": "hit" if hit else "miss", "actual": actual,
            "actual_stat": actual_stat, "threshold": threshold}


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

    with open(GRADES_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": YESTERDAY, "hits": hits, "misses": misses, "ungraded": ungraded,
                   "picks": graded}, f, indent=2)

    history = {"days": []}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history["days"] = [d for d in history.get("days", []) if d["date"] != YESTERDAY]  # avoid dup on reruns
    history["days"].append({"date": YESTERDAY, "hits": hits, "misses": misses, "ungraded": ungraded})
    history["days"].sort(key=lambda d: d["date"])

    totals = {"hits": sum(d["hits"] for d in history["days"]),
              "misses": sum(d["misses"] for d in history["days"]),
              "ungraded": sum(d["ungraded"] for d in history["days"])}
    history["totals"] = totals
    graded_total = totals["hits"] + totals["misses"]
    history["overall_hit_rate"] = round(totals["hits"] / graded_total, 3) if graded_total else None

    # Rolling last-14-day rate, so a slow start doesn't permanently anchor the headline number
    recent = history["days"][-14:]
    recent_graded = sum(d["hits"] + d["misses"] for d in recent)
    history["last_14_days_hit_rate"] = (
        round(sum(d["hits"] for d in recent) / recent_graded, 3) if recent_graded else None
    )

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    day_rate = round(hits / (hits + misses), 3) if (hits + misses) else "n/a"
    print(f"Graded {YESTERDAY}: {hits} hits / {misses} misses / {ungraded} ungraded (day rate: {day_rate})")
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
