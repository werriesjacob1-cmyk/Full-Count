#!/usr/bin/env python3
"""Counterfactual/monotonicity audit for score_pitcher -> attach_hit_
probabilities (strikeouts branch) -> attach_reliability ->
recommendation.classify_recommendation, the REAL production functions,
run end to end for pitcher strikeout-over props. Same methodology and
mocked boundary as audit_batter_counterfactual.py (raw source dicts
hand-built, not fetched live; calibrator skipped -- already audited
separately this session)."""
import sys
sys.path.insert(0, __file__.rsplit('/', 2)[0] if '/' in __file__ else '.')
import generate_picks as gp
import recommendation as rec

GM = {"matchup": "Away @ Home", "away_team": "Away", "home_team": "Home",
      "game_pk": 900002, "series_game": 1, "home_sp": "Counterfactual Starter",
      "away_sp": "X", "venue": "Neutral Park"}

PID = 777
MARKET_ODDS = -115


def build(*, opp_k_pct=22.0, bf_per_start=23.0, n_starts=4, season_k_pct=22.0,
          csw=27.0, stuff=100, same_hand_ratio=0.4, ump_k_pct=None,
          ump_league_k_pct=22.0):
    ps_lookup = {PID: {"K%": season_k_pct, "CSW%": csw, "Stuff+": stuff, "ERA": 4.00}}
    l14 = {"Counterfactual Starter": {"bf_per_start": bf_per_start, "n_starts": n_starts}}
    # Opposing lineup handedness: same_hand_ratio of batters share the
    # starter's own hand (unfavorable for a RHP starter -- same-handed
    # batters see the ball better), the rest opposite-handed.
    n_same = round(same_hand_ratio * 9)
    opp_lineup = ([{"bats": "R", "id": i} for i in range(n_same)]
                  + [{"bats": "L", "id": i} for i in range(n_same, 9)])
    ump_kbb = None
    if ump_k_pct is not None:
        ump_kbb = {"H. Plate": {"k_pct": ump_k_pct, "league_k_pct": ump_league_k_pct,
                                "bb_pct": 8.5, "league_bb_pct": 8.5}}
    gm = dict(GM, hp_ump="H. Plate")

    c = gp.score_pitcher("Counterfactual Starter", PID, "R", gm, "home", ps_lookup, l14,
                         opp_lineup, opp_k_pct, {}, opp_k_source="team", ump_kbb=ump_kbb)
    c["type"] = "pitcher"
    c["player_id"] = PID

    out = gp.attach_hit_probabilities([c], {}, {}, {}, league_rates=None)
    c = out[0]
    gp.attach_reliability([c], {}, {PID: {"starts": 12, "rates": {}}})
    c["market_odds"] = MARKET_ODDS
    c["market_implied"] = round(gp.pp.implied_probability(MARKET_ODDS), 4)
    c["price_clears"] = gp.pp.price_is_acceptable(MARKET_ODDS, c.get("hit_probability") or 0)
    verdict = rec.classify_recommendation(c)
    return c, verdict


def row(label, c, verdict):
    proj = c.get("projection") or {}
    print(f"  {label:<28} score={c['score']:>6.1f}  line={proj.get('value')}  "
          f"hit_prob={c.get('hit_probability')}  status={verdict['status']}")


print("=" * 100)
print("PITCHER STRIKEOUT-OVER COUNTERFACTUAL SWEEP -- real score_pitcher/"
      "attach_hit_probabilities/attach_reliability/classify_recommendation, "
      "market price fixed at", MARKET_ODDS)
print("=" * 100)

print("\n-- Opponent K%: low (17) -> league avg (22) -> high (28) --")
for k in (17.0, 22.0, 28.0):
    c, v = build(opp_k_pct=k)
    row(f"opp_k_pct={k}", c, v)

print("\n-- Expected BF/workload: short (18/start) -> average (23) -> long (28) --")
for bf in (18.0, 23.0, 28.0):
    c, v = build(bf_per_start=bf)
    row(f"bf_per_start={bf}", c, v)

print("\n-- Pitcher K% skill: weak (16) -> average (22) -> strong (30) --")
for k in (16.0, 22.0, 30.0):
    c, v = build(season_k_pct=k, csw=k * 0.9, stuff=80 + (k - 16) * 3)
    row(f"season_k_pct={k}", c, v)

print("\n-- Opponent lineup handedness: favorable (mostly opposite-handed, ratio 0.15) "
      "-> neutral (0.44) -> unfavorable (mostly same-handed, ratio 0.85) --")
for label, ratio in (("favorable", 0.15), ("neutral", 0.44), ("unfavorable", 0.85)):
    c, v = build(same_hand_ratio=ratio)
    row(label, c, v)

print("\n-- Umpire K environment: low-K (18%) -> neutral (22%) -> high-K (28%) "
      "(league_k_pct held at 22%) --")
for label, uk in (("low-K", 18.0), ("neutral", 22.0), ("high-K", 28.0)):
    c, v = build(ump_k_pct=uk)
    row(label, c, v)

print("\nDone.")
