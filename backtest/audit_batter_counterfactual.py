#!/usr/bin/env python3
"""Counterfactual/monotonicity audit for score_batter -> attach_hit_probabilities
-> attach_reliability -> recommendation.classify_recommendation, the REAL
production functions (unmodified), run end to end. Holds every input constant
except one, varies it through favorable -> neutral -> unfavorable, and prints
score / hit_probability / prob_ci / recommendation_status at each step.

Mocked boundary: comp_table/emp_batters/batter_season/batter_l7/park_wx dicts
(the raw external-source shapes score_batter/attach_hit_probabilities/
attach_reliability normally receive from mlb_sources.py's real fetchers) are
hand-built here instead of hitting live network fetchers -- this is the same
boundary test_score_batter.py/test_attach_hit_probabilities.py already mock in
this codebase's own test suite. calibrator=None (a real, legitimate production
state -- see apply_calibration's own None short-circuit) so calibration is
skipped; that layer was already exhaustively audited earlier this session
(H1/CI-path work) and is orthogonal to the batting-order/ERA/EV/platoon
question this script investigates.
"""
import sys
sys.path.insert(0, __file__.rsplit('/', 2)[0] if '/' in __file__ else '.')
import generate_picks as gp
import recommendation as rec

GM = {"matchup": "Away @ Home", "away_team": "Away", "home_team": "Home",
      "game_pk": 900001, "series_game": 1, "home_sp": "H", "away_sp": "A",
      "venue": "Neutral Park"}

PID = 555
COMP = {"singles_rate": 0.15, "double_rate": 0.05, "triple_rate": 0.004, "hr_rate": 0.035}
EMP = {PID: {"games": 80, "rates": {
    "hits_1plus": {"p_hat": 0.62, "p": 0.62, "league_p": 0.60, "n": 80},
    "hits_2plus": {"p_hat": 0.20, "p": 0.20, "league_p": 0.18, "n": 80},
}}}
TRUE_LEAGUE = {"hits_1plus": 0.60, "hits_2plus": 0.18}
MARKET_ODDS = -115  # held fixed across every counterfactual, per the user's request


def build(*, sp_era=4.25, order=5, l7_ev=88.5, implied_total=4.245,
          bats="R", opp_sp_hand="R", bullpen_fatigue_pct=None, bp_era=None,
          wind_dir=None, dome=False):
    """One call through the REAL pipeline: score_batter (real matchup/context/
    form/env inputs) -> attach_hit_probabilities (real dist/pa-based modelled
    probability + real empirical blend) -> attach_reliability (real CI/grade)
    -> recommendation.classify_recommendation (real Top Pick/Lean/Value/
    Neutral gate) -- with a FIXED market price throughout."""
    batter = {"name": "Counterfactual Batter", "id": PID, "team": "Away", "bats": bats, "order": order}
    opp_sp_row = {"ERA": sp_era}
    batter_season = {"wRC+": 100, "ISO": 0.16, "Barrel%": 8}
    batter_l7 = {"avg_EV": l7_ev, "barrel_pct": 8, "PA": 20}
    park_wx = {"dome": dome}
    if wind_dir is not None and not dome:
        # Real production path: wind's effect on score runs through
        # park_hr_index() (temp/wsp/wdir/humid/cf_deg/elev), NOT through a
        # hand-set "wind_effect" string -- score_batter only ever reads
        # park_wx["park_hr_index"], so a synthetic test that sets
        # wind_effect without recomputing the index through the real
        # function would silently test nothing.
        idx, we = gp.park_hr_index(temp=75, wsp=12, wdir=wind_dir, humid=50,
                                   cf_deg=0, elev=0, dome=False)
        park_wx["park_hr_index"] = idx
        park_wx["wind_effect"] = we
        park_wx["wind_mph"] = 12
    sharp_bias = {"implied_total": implied_total} if implied_total is not None else None
    opp_bullpen = None
    if bullpen_fatigue_pct is not None:
        opp_bullpen = {"tracked": 8, "fatigued_relievers": round(bullpen_fatigue_pct / 100 * 8)}
    opp_bullpen_quality = {"era": bp_era} if bp_era is not None else None

    c = gp.score_batter(batter, GM, opp_sp_row, None, opp_sp_hand, park_wx,
                         batter_season, batter_l7, {}, {}, {}, extras={},
                         sharp_bias=sharp_bias, opp_bullpen=opp_bullpen,
                         opp_bullpen_quality=opp_bullpen_quality)
    c["type"] = "batter"
    c["player_id"] = PID

    out = gp.attach_hit_probabilities([c], {PID: COMP}, EMP, {}, league_rates=TRUE_LEAGUE)
    c = out[0]
    gp.attach_reliability([c], EMP, {})
    c["market_odds"] = MARKET_ODDS
    c["market_implied"] = round(gp.pp.implied_probability(MARKET_ODDS), 4)
    c["price_clears"] = gp.pp.price_is_acceptable(MARKET_ODDS, c.get("hit_probability") or 0)
    verdict = rec.classify_recommendation(c)
    return c, verdict


def row(label, c, verdict):
    print(f"  {label:<28} score={c['score']:>6.1f}  hit_prob={c.get('hit_probability')}"
          f"  ci={c.get('prob_ci')}  reliability={c.get('reliability')}"
          f"  status={verdict['status']}")


print("=" * 100)
print("BATTER COUNTERFACTUAL SWEEP -- real score_batter/attach_hit_probabilities/"
      "attach_reliability/classify_recommendation, market price fixed at", MARKET_ODDS)
print("=" * 100)

print("\n-- Opposing SP ERA: 2.50 (elite) -> 4.50 (average) -> 6.50 (weak) --")
for era in (2.50, 4.50, 6.50):
    c, v = build(sp_era=era)
    row(f"ERA={era}", c, v)

print("\n-- Batting order: 1st -> 5th -> 9th --")
for order in (1, 5, 9):
    c, v = build(order=order)
    row(f"order={order}", c, v)

print("\n-- Recent EV: 82 (cold) -> 89 (avg) -> 95 (hot) --")
for ev in (82, 89, 95):
    c, v = build(l7_ev=ev)
    row(f"L7 EV={ev}", c, v)

print("\n-- Implied team total: 3.0 -> 4.5 -> 6.0 --")
for t in (3.0, 4.5, 6.0):
    c, v = build(implied_total=t)
    row(f"implied_total={t}", c, v)

print("\n-- Platoon: favorable (L vs RHP) -> unknown -> unfavorable (R vs RHP) --")
for label, bats, hand in (("favorable", "L", "R"), ("neutral/unknown", "?", "?"),
                          ("unfavorable", "R", "R")):
    c, v = build(bats=bats, opp_sp_hand=hand)
    row(label, c, v)

print("\n-- Bullpen quality/fatigue: elite+fresh -> average -> weak+fatigued --")
for label, fat, era in (("elite/fresh", 10, 3.0), ("average", 40, 4.20), ("weak/fatigued", 70, 5.50)):
    c, v = build(bullpen_fatigue_pct=fat, bp_era=era)
    row(label, c, v)

print("\n-- Wind (real park_hr_index() computation, not a hand-set label): "
      "out -> neutral -> in --")
for label, wdir in (("out", 180), ("neutral", None), ("in", 0)):
    c, v = build(wind_dir=wdir)
    row(label, c, v)

print("\nDone.")
