"""SYNTHETIC fixtures with real FanDuel / MLB statsapi response SHAPES, for
tests and the explicitly non-live dry run. Nothing here is market evidence.

FanDuel shapes follow fields this repository already handles from live
responses (market ``eventId``/``marketStatus``/``inPlay``, runner
``runnerStatus``/``selectionId``/``winRunnerOdds``; see nfl/normalize/
fanduel_passing.py and backtest/fanduel_live_observer.py). The MLB schedule
shape follows mlb_daily.fetch_lineups' live-verified notes. The board is a
real ``board_freeze.seal_board`` output over synthetic records.
"""
import copy
import json
import sys

import cutoff as CT
import fanduel as FDC
import schedule as SCH
import sources as S
import workload as W
from fanduel import ROOT_PATH

sys.path.insert(0, FDC.ROOT)
import board_freeze as BF  # noqa: E402

SYNTHETIC = True
DATE = "2031-05-01"                                     # far-future: cannot be confused with real slates
DECISION = f"{DATE}T21:30:00Z"
UNIT = {"date": DATE, "window": "NIGHT", "decision_at": DECISION}
FIRST_PITCH = {"900001": f"{DATE}T22:00:00Z", "900002": f"{DATE}T22:40:00Z"}   # 18:00 / 18:40 EDT: NIGHT
TEAMS = {"900001": ("Alpha Aces", "Beta Bears"), "900002": ("Gamma Goats", "Delta Dogs")}
EVENTS = {"E1": ("Alpha Aces (A Pitcher) @ Beta Bears (Ace Arm)", f"{DATE}T22:01:00Z"),
          "E2": ("Gamma Goats (C Pitcher) @ Delta Dogs (D Pitcher)", f"{DATE}T22:41:00Z")}
VERSIONS = {"model_version": "2031.05.01-synthetic", "selection_policy_version": "1.0.0",
            "calibration_version": "1.0.0", "feature_version": "1.0.0", "git_sha": "SYNTHETIC"}


def _odds(n):
    return {"americanDisplayOdds": {"americanOddsInt": n}}


def _runner(sel, name, odds, **kw):
    return dict({"selectionId": sel, "runnerName": name, "runnerStatus": "ACTIVE", "winRunnerOdds": _odds(odds)}, **kw)


def _market(mid, eid, mtype, runners, **kw):
    return dict({"marketId": mid, "eventId": eid, "marketType": mtype, "marketStatus": "OPEN", "inPlay": False, "runners": runners}, **kw)


def root_bytes():
    evs = {eid: {"eventId": eid, "name": nm, "openDate": od} for eid, (nm, od) in EVENTS.items()}
    evs["FUT"] = {"eventId": "FUT", "name": "World Series 2031 Winner", "openDate": f"{DATE}T00:00:00Z"}
    return json.dumps({"attachments": {"events": evs}}).encode()


def event_bytes(eid, tab, overrides=None):
    o = overrides or {}
    mk = {}
    if eid == "E1" and tab == "batter-props":
        mk["m1"] = _market("m1", eid, "PLAYER_TO_RECORD_A_HIT", [
            _runner(11, "Sam Slugger", o.get("slugger_hit", -250)), _runner(12, "Ted Toolsy", -180),
            _runner(13, "Ugo Utility", -140)])
        mk["m2"] = _market("m2", eid, "PLAYER_TO_RECORD_1+_HITS+RUNS+RBIS", [_runner(21, "Sam Slugger", -300)])
        mk["live"] = _market("live", eid, "PLAYER_TO_RECORD_A_HIT", [_runner(99, "Ugo Utility", 500)], inPlay=True)
    if eid == "E1" and tab == "pitcher-props":
        k_runners = [_runner(31, "Ace Arm Over", -125, handicap=5.5), _runner(32, "Ace Arm Under", 100, handicap=5.5)]
        if o.get("mixed_two_sided"):
            k_runners[1] = _runner(32, "Other Arm Under", 100, handicap=5.5)
        if o.get("split_line"):
            k_runners[1] = _runner(32, "Ace Arm Under", 100, handicap=6.5)
        mk["k1"] = _market("k1", eid, "PITCHER_A_TOTAL_STRIKEOUTS", k_runners,
                           **({"marketStatus": "SUSPENDED"} if o.get("suspend_k") else {}))
        mk["o1"] = _market("o1", eid, "PITCHER_A_OUTS_RECORDED_SB", [_runner(41, "Ace Arm Over 16.5", -110),
                                                                     _runner(42, "Ace Arm Under 16.5", -115)])
    if eid == "E2" and tab == "batter-props":
        mk["m3"] = _market("m3", eid, "PLAYER_TO_RECORD_A_HIT", [_runner(51, "Vic Veteran", -200),
                                                                 _runner(52, "Wes Whiffer", 120)])
    if eid == "E2" and tab == "popular" and o.get("conflict"):
        mk["m3dup"] = _market("m3dup", eid, "PLAYER_TO_RECORD_A_HIT", [_runner(51, "Vic Veteran", -210)])
    if eid == "E2" and tab == "popular" and o.get("foreign_event"):
        mk["m9"] = _market("m9", "E1", "PLAYER_TO_RECORD_A_HIT", [_runner(91, "Vic Veteran", -500)])
    if o.get("no_status"):
        for m in mk.values():
            m.pop("marketStatus", None)
            for r in m["runners"]:
                r.pop("runnerStatus", None)
    return json.dumps({"attachments": {"markets": mk}}).encode()


def make_fetch(overrides=None, fail_paths=()):
    def fetch(path):
        if any(fp in path for fp in fail_paths):
            raise RuntimeError("synthetic fetch failure")
        if path == ROOT_PATH:
            return "synthetic", root_bytes()
        q = dict(kv.split("=", 1) for kv in path.split("?", 1)[1].split("&"))
        return "synthetic", event_bytes(q["eventId"], q["tab"], overrides)
    return fetch


def make_clock(hour=21, start_minute=0):
    state = {"s": 0}

    def clock():
        state["s"] += 1
        m, s = divmod(state["s"], 60)
        return f"{DATE}T{hour:02d}:{start_minute + m:02d}:{s:02d}.000000Z"
    return clock


# ---- MLB schedule (confirmed lineups + probable starters) ----
LINEUPS = {"900001": (["1", "2", "3", "7", "8"], ["21", "22"]), "900002": (["4", "5", "6"], ["31"])}
STARTERS = {"900001": ("19", "9"), "900002": ("29", "39")}


def schedule_bytes(**over):
    games = []
    for pk in ("900001", "900002"):
        away, home = TEAMS[pk]
        al, hl = over.get("lineups", {}).get(pk, LINEUPS[pk])
        asp, hsp = over.get("starters", {}).get(pk, STARTERS[pk])
        games.append({"gamePk": int(pk), "gameDate": over.get("first_pitch", {}).get(pk, FIRST_PITCH[pk]),
                      "status": {"abstractGameState": over.get("state", {}).get(pk, "Preview"),
                                 "detailedState": "Pre-Game"},
                      "teams": {"away": {"team": {"name": away}, "probablePitcher": {"id": int(asp), "fullName": "x"}},
                                "home": {"team": {"name": home}, "probablePitcher": {"id": int(hsp), "fullName": "y"}}},
                      "lineups": {"awayPlayers": [{"id": int(i), "fullName": f"P{i}"} for i in al],
                                  "homePlayers": [{"id": int(i), "fullName": f"P{i}"} for i in hl]}})
    return json.dumps({"dates": [{"date": DATE, "games": games}]}).encode()


def schedule_source(archive, *, minute=5, **over):
    fetch = lambda req: ("statsapi", schedule_bytes(**over))  # noqa: E731
    return S.fetch(archive, fetch, make_clock(21, minute), source=SCH.SOURCE, request=SCH.request_for(DATE))


def commitment(archive, *, hour=18, minute=0, **over):
    """A3 cutoff commitment from a schedule observed 14:00 EDT (before the 15:00 NIGHT deadline)."""
    src = S.fetch(archive, lambda req: ("statsapi", schedule_bytes(**over)), make_clock(hour, minute),
                  source=SCH.SOURCE, request=SCH.request_for(DATE))
    return CT.make(archive, src, {"date": DATE, "window": "NIGHT"})


def gamelog_bytes():
    splits = [{"date": "2031-04-14", "stat": {"gamesStarted": 1, "outs": 18, "numberOfPitches": 98}},
              {"date": "2031-04-20", "stat": {"gamesStarted": 1, "outs": 15, "numberOfPitches": 91}},
              {"date": "2031-04-26", "stat": {"gamesStarted": 1, "inningsPitched": "5.2", "numberOfPitches": 87}},
              {"date": "2031-04-29", "stat": {"gamesStarted": 0, "outs": 3, "numberOfPitches": 15}},     # relief: ignored
              {"date": DATE, "stat": {"gamesStarted": 1, "outs": 21, "numberOfPitches": 101}}]           # same day: never used
    return json.dumps({"stats": [{"splits": splits}]}).encode()


def gamelog_source(archive, *, player_id="9", hour=21, minute=10):
    fetch = lambda req: ("statsapi", gamelog_bytes())  # noqa: E731
    return S.fetch(archive, fetch, make_clock(hour, minute), source=W.SOURCE, request=W.request_for(player_id, 2031))


# ---- sealed production-format board ----
def _rec(cid, pk, name, pid, team, stat, side, needs, line, p, rel, status, odds, qc="kept", lineup_assumed=False):
    return {"candidate_id": cid, "identity_version": 2, "game_pk": pk, "player_id": pid, "combo_player_ids": None,
            "player_name": name, "team": team, "matchup": " @ ".join(TEAMS[pk]), "stat": stat, "needs": str(needs),
            "line": line, "market_side": side, "prop_label": f"{name} {stat}",
            "prediction": {"hit_probability": p, "reliability": rel},
            "market": {"market_odds": odds},
            "eligibility": {"qc_status": qc, "lineup_assumed": lineup_assumed},
            "selector": {"recommendation_status": status},
            "provenance": {k: VERSIONS[k] for k in ("model_version", "selection_policy_version",
                                                    "calibration_version", "feature_version", "git_sha")},
            "generation_timestamp": f"{DATE}T20:55:00Z"}


def records():
    A, B, G = "Alpha Aces", "Beta Bears", "Gamma Goats"
    return [
        _rec("c1", "900001", "Sam Slugger", "1", A, "hits", "over", 1, 0.5, 0.74, "A", "lean", -250),
        _rec("c2", "900001", "Ted Toolsy", "2", A, "hits", "over", 1, 0.5, 0.66, "A", "top_pick", -180),
        _rec("c3", "900001", "Ugo Utility", "3", A, "hits", "over", 1, 0.5, 0.61, "B", "top_pick", -140),
        _rec("c4", "900001", "Sam Slugger", "1", A, "hits_runs_rbis", "over", 1, 0.5, 0.80, "A", "lean", -300),
        _rec("c5", "900001", "Ace Arm", "9", B, "strikeouts", "over", 6, 5.5, 0.57, "A", "top_pick", -125),
        _rec("c6", "900001", "Ace Arm", "9", B, "pitcher_outs", "over", 17, 16.5, 0.55, "A", "lean", -110),
        _rec("c7", "900002", "Vic Veteran", "4", G, "hits", "over", 1, 0.5, 0.69, "A", "lean", -200),
        _rec("c8", "900002", "Wes Whiffer", "5", G, "hits", "over", 1, 0.5, 0.40, "C", "neutral", 120),
        _rec("c9", "900002", "Nobody Priced", "6", G, "hits", "over", 1, 0.5, 0.65, "A", "lean", -170),
        _rec("c10", "900001", "Lineup Guess", "7", A, "hits", "over", 1, 0.5, 0.70, "A", None, -200,
             qc="lineup_assumed_holdout", lineup_assumed=True),
    ]


def board(recs=None, *, sealed_at=f"{DATE}T20:56:00Z", generated_at=f"{DATE}T20:55:00Z", starts=None):
    return BF.seal_board(date=DATE, board_generated_at=generated_at, sealed_at=sealed_at,
                         game_start_times=dict(starts or FIRST_PITCH), records=copy.deepcopy(recs or records()),
                         provenance=VERSIONS)


def raw_candidates():
    return {"c1": {"projected_pa": 4.4, "lineup_assumed": False, "signals": {"lineup_slot": 87.5, "platoon": 80.0}},
            "c2": {"projected_pa": 4.1, "lineup_assumed": False, "signals": {"lineup_slot": 75.0}},
            "c3": {"lineup_assumed": False, "signals": {}},                                   # projected_pa missing
            "c4": {"projected_pa": 4.4, "lineup_assumed": False, "signals": {"lineup_slot": 87.5}},
            "c5": {"signals": {"opp_team_k_pct": 61.0, "season_k_pct": 70.0}},
            "c6": {"signals": {"avg_outs_per_start": 55.0}},
            "c7": {"projected_pa": 4.2, "lineup_assumed": False, "signals": {}}}


def capture(archive, **kw):
    return FDC.sweep(archive, make_fetch(**kw), clock=make_clock())

