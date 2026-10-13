"""SYNTHETIC fixtures with real FanDuel / MLB statsapi response SHAPES, for
tests and the explicitly non-live dry run. Nothing here is market evidence."""
import json

from fanduel import ROOT_PATH

SYNTHETIC = True
DATE = "2031-05-01"                                     # far-future: cannot be confused with real slates
GAMES = [{"game_pk": "900001", "matchup": "Alpha Aces @ Beta Bears", "first_pitch_utc": f"{DATE}T23:10:00Z"},
         {"game_pk": "900002", "matchup": "Gamma Goats @ Delta Dogs", "first_pitch_utc": f"{DATE}T23:40:00Z"}]
EVENTS = {"E1": ("Alpha Aces (A Pitcher) @ Beta Bears (B Pitcher)", f"{DATE}T23:11:00Z"),
          "E2": ("Gamma Goats (C Pitcher) @ Delta Dogs (D Pitcher)", f"{DATE}T23:41:00Z")}


def _odds(n):
    return {"americanDisplayOdds": {"americanOddsInt": n}}


def root_bytes():
    evs = {eid: {"eventId": eid, "name": nm, "openDate": od} for eid, (nm, od) in EVENTS.items()}
    evs["FUT"] = {"eventId": "FUT", "name": "World Series 2031 Winner", "openDate": f"{DATE}T00:00:00Z"}
    return json.dumps({"attachments": {"events": evs}}).encode()


def event_bytes(eid, tab, overrides=None):
    o = overrides or {}
    markets = {}
    if eid == "E1" and tab == "batter-props":
        markets["m1"] = {"marketId": "m1", "marketType": "PLAYER_TO_RECORD_A_HIT", "inPlay": False, "runners": [
            {"selectionId": 11, "runnerName": "Sam Slugger", "winRunnerOdds": _odds(o.get("slugger_hit", -250))},
            {"selectionId": 12, "runnerName": "Ted Toolsy", "winRunnerOdds": _odds(o.get("toolsy_hit", -180))},
            {"selectionId": 13, "runnerName": "Ugo Utility", "winRunnerOdds": _odds(-140)}]}
        markets["m2"] = {"marketId": "m2", "marketType": "PLAYER_TO_RECORD_1+_HITS+RUNS+RBIS", "inPlay": False,
                         "runners": [{"selectionId": 21, "runnerName": "Sam Slugger", "winRunnerOdds": _odds(-300)}]}
        markets["live"] = {"marketId": "live", "marketType": "PLAYER_TO_RECORD_A_HIT", "inPlay": True,
                           "runners": [{"selectionId": 99, "runnerName": "Ugo Utility", "winRunnerOdds": _odds(+500)}]}
    if eid == "E1" and tab == "pitcher-props":
        markets["k1"] = {"marketId": "k1", "marketType": "PITCHER_A_TOTAL_STRIKEOUTS", "inPlay": False, "runners": [
            {"selectionId": 31, "runnerName": "Ace Arm Over", "handicap": 5.5, "winRunnerOdds": _odds(-125)},
            {"selectionId": 32, "runnerName": "Ace Arm Under", "handicap": 5.5, "winRunnerOdds": _odds(+100)}]}
        markets["o1"] = {"marketId": "o1", "marketType": "PITCHER_A_OUTS_RECORDED_SB", "inPlay": False, "runners": [
            {"selectionId": 41, "runnerName": "Ace Arm Over 16.5", "winRunnerOdds": _odds(-110)},
            {"selectionId": 42, "runnerName": "Ace Arm Under 16.5", "winRunnerOdds": _odds(-115)}]}
    if eid == "E2" and tab == "batter-props":
        markets["m3"] = {"marketId": "m3", "marketType": "PLAYER_TO_RECORD_A_HIT", "inPlay": False, "runners": [
            {"selectionId": 51, "runnerName": "Vic Veteran", "winRunnerOdds": _odds(-200)},
            {"selectionId": 52, "runnerName": "Wes Whiffer", "winRunnerOdds": _odds(+120)}]}
    if eid == "E2" and tab == "popular" and o.get("conflict"):
        markets["m3dup"] = {"marketId": "m3dup", "marketType": "PLAYER_TO_RECORD_A_HIT", "inPlay": False,
                            "runners": [{"selectionId": 51, "runnerName": "Vic Veteran", "winRunnerOdds": _odds(-210)}]}
    return json.dumps({"attachments": {"markets": markets}}).encode()


def make_fetch(overrides=None, fail_paths=()):
    def fetch(path):
        if any(fp in path for fp in fail_paths):
            raise RuntimeError("synthetic fetch failure")
        if path == ROOT_PATH:
            return "synthetic", root_bytes()
        q = dict(kv.split("=", 1) for kv in path.split("?", 1)[1].split("&"))
        return "synthetic", event_bytes(q["eventId"], q["tab"], overrides)
    return fetch


def make_clock(start_minute=0):
    state = {"s": 0}
    def clock():
        state["s"] += 1
        m, s = divmod(state["s"], 60)
        return f"{DATE}T21:{start_minute + m:02d}:{s:02d}.000000Z"
    return clock


def _rec(cid, pk, name, pid, stat, side, needs, line, p, rel, status, odds, lineup_assumed=False, qc="kept"):
    return {"candidate_id": cid, "game_pk": pk, "player_name": name, "player_id": pid, "combo_player_ids": None,
            "stat": stat, "market_side": side, "needs": str(needs), "line": line,
            "prediction": {"hit_probability": p, "reliability": rel},
            "eligibility": {"qc_status": qc, "lineup_assumed": lineup_assumed},
            "selector": {"recommendation_status": status}, "market": {"market_odds": odds}}


def candidates():
    return [
        _rec("c1", "900001", "Sam Slugger", "1", "hits", "over", 1, 0.5, 0.74, "A", "lean", -250),
        _rec("c2", "900001", "Ted Toolsy", "2", "hits", "over", 1, 0.5, 0.66, "A", "top_pick", -180),
        _rec("c3", "900001", "Ugo Utility", "3", "hits", "over", 1, 0.5, 0.61, "B", "top_pick", -150),  # price differs
        _rec("c4", "900001", "Sam Slugger", "1", "hits_runs_rbis", "over", 1, 0.5, 0.80, "A", "lean", -300),
        _rec("c5", "900001", "Ace Arm", "9", "strikeouts", "over", 6, 5.5, 0.57, "A", "top_pick", -125),
        _rec("c6", "900001", "Ace Arm", "9", "pitcher_outs", "over", 17, 16.5, 0.55, "A", "lean", -110),
        _rec("c7", "900002", "Vic Veteran", "4", "hits", "over", 1, 0.5, 0.69, "A", "lean", -200),
        _rec("c8", "900002", "Wes Whiffer", "5", "hits", "over", 1, 0.5, 0.40, "C", "neutral", 120),
        _rec("c9", "900002", "Nobody Priced", "6", "hits", "over", 1, 0.5, 0.65, "A", "lean", -170),     # missing quote
        _rec("c10", "900001", "Lineup Guess", "7", "hits", "over", 1, 0.5, 0.70, "A", "lean", -200, lineup_assumed=True),
    ]


def gamelog_bytes():
    splits = [{"date": "2031-04-14", "stat": {"gamesStarted": 1, "outs": 18, "numberOfPitches": 98}},
              {"date": "2031-04-20", "stat": {"gamesStarted": 1, "outs": 15, "numberOfPitches": 91}},
              {"date": "2031-04-26", "stat": {"gamesStarted": 1, "inningsPitched": "5.2", "numberOfPitches": 87}},
              {"date": "2031-04-29", "stat": {"gamesStarted": 0, "outs": 3, "numberOfPitches": 15}},     # relief: ignored
              {"date": DATE, "stat": {"gamesStarted": 1, "outs": 21, "numberOfPitches": 101}}]           # same day: never used
    return json.dumps({"stats": [{"splits": splits}]}).encode()


def raw_candidates():
    return {"c1": {"projected_pa": 4.4, "lineup_assumed": False, "signals": {"lineup_slot": 87.5, "platoon": 80.0}},
            "c2": {"projected_pa": 4.1, "lineup_assumed": False, "signals": {"lineup_slot": 75.0}},
            "c3": {"lineup_assumed": False, "signals": {}},                                   # projected_pa missing
            "c4": {"projected_pa": 4.4, "lineup_assumed": False, "signals": {"lineup_slot": 87.5}},
            "c5": {"signals": {"opp_team_k_pct": 61.0, "season_k_pct": 70.0}},
            "c6": {"signals": {"avg_outs_per_start": 55.0}},
            "c7": {"projected_pa": 4.2, "lineup_assumed": False, "signals": {}}}
