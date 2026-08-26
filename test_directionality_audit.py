#!/usr/bin/env python3
"""test_directionality_audit.py -- regression coverage for the 2026-08-24/25
directionality audit: a real, live complaint (Weston Wilson's Top Pick card
showing batting-8th/low-implied-total/unfavorable-platoon/tough-opposing-SP/
cold-recent-EV all as green "why" reasons) escalated into a full trace of
whether the same false-direction logic could be influencing actual scoring,
probability, ranking, or Top Pick classification -- not just presentation.

FINDING (see the accompanying report for the full trace): every one of the
five suspect signals is EXPLANATION-ONLY (category A). score_batter()'s own
weighted formula (matchup 4%, form 3%, env 20%, skill -9%, context 64%) was
ALREADY correctly, monotonically signed for all of them -- verified here by
running the REAL score_batter()/attach_hit_probabilities()/attach_reliability()
/recommendation.classify_recommendation() pipeline end to end, holding every
other input constant and varying one dimension through favorable->neutral->
unfavorable. The bug was purely that score_batter()'s `why`-list construction
stated these facts without applying the SAME directional judgment the score
formula already computes internally (sp_weak, lineup_context, run_env,
sc_l7_ev, sc_l7_barrel, platoon -- all already real, already scored numbers,
now reused to route each fact to why/watchouts/neutral instead of restated
blind). This file locks in both halves: the monotonicity proof (so a future
change can't silently reverse a real sign) and the explanation-routing fix
(so a future change can't silently reintroduce a wrong-heading fact).

Mocked boundary, same as the rest of this suite's score_batter/score_pitcher
tests (test_score_batter.py, test_score_pitcher.py): raw external-source
dicts (comp_table/emp_batters/batter_season/batter_l7/park_wx) are hand-built
rather than fetched live. calibrator=None (apply_calibration's own real,
legitimate no-op state) since that layer was exhaustively audited separately
this session (H1/CI-path work) and is orthogonal to this investigation.

    /tmp/mlbvenv/bin/python3 test_directionality_audit.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


import generate_picks as gp  # noqa: E402
import recommendation as rec  # noqa: E402

GM = {"matchup": "Away @ Home", "away_team": "Away", "home_team": "Home",
      "game_pk": 900001, "series_game": 1, "home_sp": "H", "away_sp": "A",
      "venue": "Neutral Park"}
PID = 555
COMP = {"singles_rate": 0.15, "double_rate": 0.05, "triple_rate": 0.004, "hr_rate": 0.035}
EMP = {PID: {"games": 80, "rates": {
    "hits_1plus": {"p_hat": 0.62, "p": 0.62, "league_p": 0.60, "n": 80},
    "hits_2plus": {"p_hat": 0.20, "p": 0.20, "league_p": 0.18, "n": 80}}}}
TRUE_LEAGUE = {"hits_1plus": 0.60, "hits_2plus": 0.18}
MARKET_ODDS = -115


def build(*, sp_era=4.25, order=5, l7_ev=88.5, implied_total=4.245,
          bats="R", opp_sp_hand="R", bullpen_fatigue_pct=None, bp_era=None,
          wind_dir=None, dome=False, l7_barrel=8):
    """One call through the REAL pipeline. wind_dir, when given, is routed
    through the REAL park_hr_index() computation (temp/wsp/wdir/humid/
    cf_deg/elev) -- score_batter only ever reads park_wx["park_hr_index"],
    never a hand-set wind label, so that is what must be varied."""
    batter = {"name": "Counterfactual Batter", "id": PID, "team": "Away", "bats": bats, "order": order}
    opp_sp_row = {"ERA": sp_era}
    batter_season = {"wRC+": 100, "ISO": 0.16, "Barrel%": 8}
    batter_l7 = {"avg_EV": l7_ev, "barrel_pct": l7_barrel, "PA": 20}
    park_wx = {"dome": dome}
    if wind_dir is not None and not dome:
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
    c["recommendation_status"] = verdict["status"]
    return c


head("1. WESTON WILSON, real live card reconstruction (2026-08-24, Over 0.5 "
     "Hits+Runs+RBIs Top Pick): batting 8th, 3.63 proj. PA, 3.08-run implied "
     "team total, R bat vs RHP (unfavorable), opposing SP ERA 2.92, L7 avg EV "
     "82.8mph vs 88.5 league. Every one of these is neutral-to-negative for "
     "an 'over' -- none may appear in `why` unqualified.")

weston = gp.score_batter(
    {"name": "Weston Wilson", "id": 642215, "team": "Seattle Mariners", "bats": "R", "order": 8},
    {"matchup": "Philadelphia Phillies @ Seattle Mariners", "away_team": "Philadelphia Phillies",
     "home_team": "Seattle Mariners", "game_pk": 823097, "series_game": 1,
     "home_sp": "H", "away_sp": "A", "venue": "T-Mobile Park"},
    {"ERA": 2.92}, None, "R", {},
    {"wRC+": 100, "ISO": 0.15, "Barrel%": 8},
    {"avg_EV": 82.8, "barrel_pct": 16.7, "PA": 6},
    {}, {}, {}, extras={}, sharp_bias={"implied_total": 3.08})

check(not any("Opposing SP ERA" in w for w in weston["why"]),
      "the elite opposing SP's ERA does not appear in why", f"why={weston['why']}")
check(any("Opposing SP ERA 2.92" in w and "elite pitcher" in w for w in weston["watchouts"]),
      "...it appears in watchouts instead, correctly framed", f"watchouts={weston['watchouts']}")
check(not any(w.startswith("Projected") and "batting slot 8" in w for w in weston["why"]),
      "the bottom-of-the-order PA projection does not appear in why unqualified",
      f"why={weston['why']}")
check(any("batting slot 8" in w and "tough lineup slot" in w for w in weston["watchouts"]),
      "...it appears in watchouts, correctly framed as a tough slot", f"watchouts={weston['watchouts']}")
check(not any("Team implied for 3.08" in w for w in weston["why"]),
      "the below-average implied team total does not appear in why unqualified",
      f"why={weston['why']}")
check(any("Team implied for 3.08" in w and "weak offensive environment" in w for w in weston["watchouts"]),
      "...it appears in watchouts, correctly framed as weak", f"watchouts={weston['watchouts']}")
check(not any("Platoon" in w for w in weston["why"]),
      "the unfavorable platoon does not appear in why", f"why={weston['why']}")
check(any("Platoon" in w and "unfavorable" in w for w in weston["watchouts"]),
      "...it appears in watchouts", f"watchouts={weston['watchouts']}")
check(not any("L7 avg EV" in w for w in weston["why"]),
      "the cold recent EV (82.8 vs 88.5 league) does not appear in why", f"why={weston['why']}")
check(any("L7 avg EV 82.8" in w and "cold recent contact" in w for w in weston["watchouts"]),
      "...it appears in watchouts, correctly framed as cold", f"watchouts={weston['watchouts']}")
check(weston["score"] < 20,
      "with every one of these five signals genuinely unfavorable, the real score formula "
      "(context 64%% weight is batting-order-driven and dominates) already produced a very "
      "low score BEFORE this fix -- confirms the SCORE was never the bug, only the "
      "explanation routing", f"score={weston['score']}")

head("2. JAKE MCCARTHY, real historical card reconstruction (2026-08-21, Over 0.5 Hits): "
     "L7 avg EV 78.3mph vs 88.5 league (9.4mph cold) shown as a plain fact in `why` with "
     "no directional framing in the real production output that day.")

mccarthy = gp.score_batter(
    {"name": "Jake McCarthy", "id": 693319, "team": "Away", "bats": "L", "order": 1},
    GM, {"ERA": 3.80}, None, "L", {},
    {"wRC+": 105, "ISO": 0.14, "Barrel%": 7},
    {"avg_EV": 78.3, "barrel_pct": 16.7, "PA": 20},
    {}, {}, {}, extras={}, sharp_bias={"implied_total": 4.77})

check(not any("L7 avg EV" in w for w in mccarthy["why"]),
      "the cold recent EV (78.3 vs 88.5 league, a 9.4mph gap) does not appear in why",
      f"why={mccarthy['why']}")
check(any("L7 avg EV 78.3" in w and "cold recent contact" in w for w in mccarthy["watchouts"]),
      "...it appears in watchouts, correctly framed as cold", f"watchouts={mccarthy['watchouts']}")
check(mccarthy["score"] > 60,
      "McCarthy's leadoff slot (lineup_context, the 64%%-weighted dominant component) still "
      "correctly drives a HIGH score despite the cold EV note moving to watchouts -- proves "
      "the fix is presentation-only and does not silently punish a real leadoff-spot "
      "advantage", f"score={mccarthy['score']}")

head("3. MONOTONICITY: score_batter's real formula is directionally correct for every "
     "suspect signal -- proven by strict inequality on the REAL function's output, not "
     "code reading. Any sign flip in the underlying scale()/weight would fail these.")

s_era_elite = build(sp_era=2.50)["score"]
s_era_avg = build(sp_era=4.50)["score"]
s_era_weak = build(sp_era=6.50)["score"]
check(s_era_elite < s_era_avg < s_era_weak,
      "score strictly increases as the opposing SP's ERA worsens (better for the batter)",
      f"{s_era_elite} < {s_era_avg} < {s_era_weak}")

s_order1 = build(order=1)
s_order5 = build(order=5)
s_order9 = build(order=9)
check(s_order9["score"] < s_order5["score"] < s_order1["score"],
      "score strictly decreases from leadoff to the bottom of the order",
      f"{s_order9['score']} < {s_order5['score']} < {s_order1['score']}")
check(s_order9["hit_probability"] < s_order5["hit_probability"] < s_order1["hit_probability"],
      "hit_probability ALSO strictly decreases from leadoff to the bottom of the order -- "
      "batting order is a real input to probability (via projected PA), not just score",
      f"{s_order9['hit_probability']} < {s_order5['hit_probability']} < {s_order1['hit_probability']}")
check(s_order9["recommendation_status"] != "top_pick" or s_order1["recommendation_status"] == "top_pick",
      "Full Count never becomes MORE confident in a batter over merely because he moves "
      "toward the bottom of the order", f"order9={s_order9['recommendation_status']} "
      f"order1={s_order1['recommendation_status']}")

s_ev_cold = build(l7_ev=82)["score"]
s_ev_avg = build(l7_ev=89)["score"]
s_ev_hot = build(l7_ev=95)["score"]
check(s_ev_cold < s_ev_avg < s_ev_hot,
      "score strictly increases with recent exit velocity", f"{s_ev_cold} < {s_ev_avg} < {s_ev_hot}")

s_total_low = build(implied_total=3.0)
s_total_mid = build(implied_total=4.5)
s_total_high = build(implied_total=6.0)
check(s_total_low["score"] < s_total_mid["score"] < s_total_high["score"],
      "score strictly increases with the team's implied run total",
      f"{s_total_low['score']} < {s_total_mid['score']} < {s_total_high['score']}")
check(s_total_low["hit_probability"] < s_total_mid["hit_probability"] < s_total_high["hit_probability"],
      "hit_probability ALSO strictly increases with implied team total (via projected PA)",
      f"{s_total_low['hit_probability']} < {s_total_mid['hit_probability']} < {s_total_high['hit_probability']}")

s_platoon_fav = build(bats="L", opp_sp_hand="R")["score"]
s_platoon_unk = build(bats="?", opp_sp_hand="?")["score"]
s_platoon_unfav = build(bats="R", opp_sp_hand="R")["score"]
check(s_platoon_unfav < s_platoon_unk < s_platoon_fav,
      "score strictly decreases as platoon shifts from favorable to unfavorable",
      f"{s_platoon_unfav} < {s_platoon_unk} < {s_platoon_fav}")

s_bp_elite = build(bullpen_fatigue_pct=10, bp_era=3.0)["score"]
s_bp_avg = build(bullpen_fatigue_pct=40, bp_era=4.20)["score"]
s_bp_weak = build(bullpen_fatigue_pct=70, bp_era=5.50)["score"]
check(s_bp_elite < s_bp_avg < s_bp_weak,
      "score strictly increases as the opposing bullpen gets worse/more fatigued "
      "(worse bullpen is better for the batter)", f"{s_bp_elite} < {s_bp_avg} < {s_bp_weak}")

s_wind_in = build(wind_dir=0)["score"]
s_wind_neutral = build(wind_dir=None)["score"]
s_wind_out = build(wind_dir=180)["score"]
check(s_wind_in < s_wind_neutral < s_wind_out,
      "score strictly increases from wind blowing in to neutral to blowing out "
      "(real park_hr_index() computation, not a hand-set label)",
      f"{s_wind_in} < {s_wind_neutral} < {s_wind_out}")

head("4. MONOTONICITY, pitcher strikeouts: score_pitcher's real formula, same methodology.")


def build_pitcher(*, opp_k_pct=22.0, bf_per_start=23.0, season_k_pct=22.0, csw=27.0, stuff=100):
    ps_lookup = {777: {"K%": season_k_pct, "CSW%": csw, "Stuff+": stuff, "ERA": 4.00}}
    l14 = {"Counterfactual Starter": {"bf_per_start": bf_per_start, "n_starts": 4}}
    opp_lineup = [{"bats": "R", "id": i} for i in range(4)] + [{"bats": "L", "id": i} for i in range(4, 9)]
    gm = dict(GM, hp_ump="H. Plate", home_sp="Counterfactual Starter")
    c = gp.score_pitcher("Counterfactual Starter", 777, "R", gm, "home", ps_lookup, l14,
                         opp_lineup, opp_k_pct, {}, opp_k_source="team")
    c["type"] = "pitcher"
    c["player_id"] = 777
    out = gp.attach_hit_probabilities([c], {}, {}, {}, league_rates=None)
    c = out[0]
    gp.attach_reliability([c], {}, {777: {"starts": 12, "rates": {}}})
    c["market_odds"] = MARKET_ODDS
    c["market_implied"] = round(gp.pp.implied_probability(MARKET_ODDS), 4)
    c["price_clears"] = gp.pp.price_is_acceptable(MARKET_ODDS, c.get("hit_probability") or 0)
    return c


p_klow = build_pitcher(opp_k_pct=17.0)["score"]
p_kavg = build_pitcher(opp_k_pct=22.0)["score"]
p_khigh = build_pitcher(opp_k_pct=28.0)["score"]
check(p_klow < p_kavg < p_khigh,
      "score strictly increases with opponent K%%", f"{p_klow} < {p_kavg} < {p_khigh}")

p_bf_short = build_pitcher(bf_per_start=18.0)
p_bf_avg = build_pitcher(bf_per_start=23.0)
p_bf_long = build_pitcher(bf_per_start=28.0)
check(p_bf_short["hit_probability"] < p_bf_avg["hit_probability"] < p_bf_long["hit_probability"],
      "hit_probability strictly increases with expected workload (more batters faced -> "
      "more chances to strike batters out)", f"{p_bf_short['hit_probability']} < "
      f"{p_bf_avg['hit_probability']} < {p_bf_long['hit_probability']}")

p_skill_weak = build_pitcher(season_k_pct=16.0, csw=14.4, stuff=80)
p_skill_avg = build_pitcher(season_k_pct=22.0, csw=19.8, stuff=98)
p_skill_strong = build_pitcher(season_k_pct=30.0, csw=27.0, stuff=122)
check(p_skill_weak["score"] < p_skill_avg["score"] < p_skill_strong["score"],
      "score strictly increases with the pitcher's own K skill",
      f"{p_skill_weak['score']} < {p_skill_avg['score']} < {p_skill_strong['score']}")
check(p_skill_weak["hit_probability"] < p_skill_avg["hit_probability"] < p_skill_strong["hit_probability"],
      "hit_probability ALSO strictly increases with the pitcher's own K skill (season K%% "
      "feeds k_rate directly when no exp_k/L14 data exists)",
      f"{p_skill_weak['hit_probability']} < {p_skill_avg['hit_probability']} < "
      f"{p_skill_strong['hit_probability']}")

head("5. 2026-08-25 release-readiness audit: wind blowing IN is a real, self-contradictory "
     "placement bug found live in 163 currently-published props (docs/data.json) across "
     "Hits/Total Bases/Doubles/Hits+Runs+RBIs -- the note's own text said 'power suppressed' "
     "while it rendered under `why`, the positive-reasons list. Fixed by moving the wind-in "
     "branch to watchouts (wind-out, whose own text is genuinely positive -- 'HR boost' -- "
     "correctly stays in why).")

wind_in_candidate = build(wind_dir=0)
check(not any("Wind blowing IN" in w for w in wind_in_candidate["why"]),
      "REGRESSION GUARD: a self-contradictory 'power suppressed' note must never appear in "
      "why -- the positive-reasons list", f"why={wind_in_candidate['why']}")
check(any("Wind blowing IN" in w and "power suppressed" in w for w in wind_in_candidate["watchouts"]),
      "...it appears in watchouts instead, where its own negative text is honestly placed",
      f"watchouts={wind_in_candidate['watchouts']}")

wind_out_candidate = build(wind_dir=180)
check(any("Wind blowing OUT" in w and "HR boost" in w for w in wind_out_candidate["why"]),
      "wind blowing OUT correctly stays in why -- its own text is genuinely positive",
      f"why={wind_out_candidate['why']}")
check(not any("Wind blowing OUT" in w for w in wind_out_candidate["watchouts"]),
      "wind blowing OUT never lands in watchouts", f"watchouts={wind_out_candidate['watchouts']}")

head("6. 2026-08-25 release-readiness audit: season wRC+ closed proactively (not yet observed "
     "live, but the identical unconditional-append shape as the fixed bugs above).")

wrc_weak = gp.score_batter(
    {"name": "Weak Bat", "id": 700, "team": "Away", "bats": "R", "order": 5},
    GM, {"ERA": 4.25}, None, "R", {},
    {"wRC+": 60, "ISO": 0.10, "Barrel%": 5}, {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20},
    {}, {}, {}, extras={}, sharp_bias={"implied_total": 4.5})
check(not any("Season wRC+ 60" in w for w in wrc_weak["why"]),
      "REGRESSION GUARD: a genuinely below-average wRC+ (60) must not land in why",
      f"why={wrc_weak['why']}")
check(any("Season wRC+ 60" in w and "below-average" in w for w in wrc_weak["watchouts"]),
      "...it lands in watchouts instead, honestly labeled", f"watchouts={wrc_weak['watchouts']}")

wrc_strong = gp.score_batter(
    {"name": "Strong Bat", "id": 701, "team": "Away", "bats": "R", "order": 5},
    GM, {"ERA": 4.25}, None, "R", {},
    {"wRC+": 150, "ISO": 0.22, "Barrel%": 12}, {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20},
    {}, {}, {}, extras={}, sharp_bias={"implied_total": 4.5})
check(any("Season wRC+ 150" in w and "above-average" in w for w in wrc_strong["why"]),
      "a genuinely above-average wRC+ (150) is labeled as such in why",
      f"why={wrc_strong['why']}")

head("7. 2026-08-25 explanation-quality fix: bullpen fatigue / bullpen ERA / sharp money "
     "must not land unqualified in `why` when they're actually UNFAVORABLE readings. Real "
     "complaint: Jacob saw a 'fresh pen' bullpen note under 'Why It Could Hit' -- a fresh, "
     "rested bullpen is tougher for the batter late, not a reason to like the pick. Same "
     "bug shape as the wind-in/SP-ERA/L7-EV fixes above, in the block right after them.")

c7_fresh_pen = build(bullpen_fatigue_pct=10, bp_era=4.20)
check(not any("Opposing bullpen fatigue" in w for w in c7_fresh_pen["why"]),
      "REGRESSION GUARD: a fresh/rested bullpen (10% fatigued) must NOT appear in why",
      f"why={c7_fresh_pen['why']}")
check(any("Opposing bullpen fatigue" in w and "fresh pen" in w and "tougher matchup late" in w
          for w in c7_fresh_pen["watchouts"]),
      "...it appears in watchouts instead, honestly framed as tougher late",
      f"watchouts={c7_fresh_pen['watchouts']}")

c7_tired_pen = build(bullpen_fatigue_pct=70, bp_era=4.20)
check(any("Opposing bullpen fatigue" in w and "tired pen" in w and "favorable late" in w
          for w in c7_tired_pen["why"]),
      "a genuinely tired bullpen (70% fatigued) is labeled favorable in why",
      f"why={c7_tired_pen['why']}")
check(not any("Opposing bullpen fatigue" in w for w in c7_tired_pen["watchouts"]),
      "a tired bullpen never lands in watchouts", f"watchouts={c7_tired_pen['watchouts']}")

c7_elite_pen = build(bullpen_fatigue_pct=40, bp_era=3.00)
check(not any("Opposing bullpen ERA" in w for w in c7_elite_pen["why"]),
      "REGRESSION GUARD: an elite opposing bullpen ERA (3.00, well below league) must NOT "
      "appear in why", f"why={c7_elite_pen['why']}")
check(any("Opposing bullpen ERA 3.0" in w and "elite pen" in w for w in c7_elite_pen["watchouts"]),
      "...it appears in watchouts instead, honestly labeled elite", f"watchouts={c7_elite_pen['watchouts']}")

c7_shaky_pen = build(bullpen_fatigue_pct=40, bp_era=5.50)
check(any("Opposing bullpen ERA 5.5" in w and "shaky pen" in w for w in c7_shaky_pen["why"]),
      "a genuinely shaky opposing bullpen ERA (5.50) is labeled shaky in why",
      f"why={c7_shaky_pen['why']}")
check(not any("Opposing bullpen ERA" in w for w in c7_shaky_pen["watchouts"]),
      "a shaky bullpen never lands in watchouts", f"watchouts={c7_shaky_pen['watchouts']}")

sharp_fading = gp.score_batter(
    {"name": "Sharp Test Batter", "id": 702, "team": "Away", "bats": "R", "order": 5},
    GM, {"ERA": 4.25}, None, "R", {},
    {"wRC+": 100, "ISO": 0.16, "Barrel%": 8}, {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20},
    {}, {}, {}, extras={}, sharp_bias={"implied_total": 4.5, "sharp_divergence": -15})
check(not any("Sharp money" in w for w in sharp_fading["why"]),
      "REGRESSION GUARD: sharp money FADING this side (-15 pts) must NOT appear in why -- "
      "it is not a reason to like the pick", f"why={sharp_fading['why']}")
check(any("Sharp money fading Away" in w and "smart money moving away" in w
          for w in sharp_fading["watchouts"]),
      "...it appears in watchouts instead, honestly framed", f"watchouts={sharp_fading['watchouts']}")

sharp_backing = gp.score_batter(
    {"name": "Sharp Test Batter 2", "id": 703, "team": "Away", "bats": "R", "order": 5},
    GM, {"ERA": 4.25}, None, "R", {},
    {"wRC+": 100, "ISO": 0.16, "Barrel%": 8}, {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20},
    {}, {}, {}, extras={}, sharp_bias={"implied_total": 4.5, "sharp_divergence": 15})
check(any("Sharp money backing Away" in w and "+15" in w for w in sharp_backing["why"]),
      "sharp money genuinely BACKING this side (+15 pts) is labeled as such in why",
      f"why={sharp_backing['why']}")
check(not any("Sharp money" in w for w in sharp_backing["watchouts"]),
      "sharp money backing this side never lands in watchouts", f"watchouts={sharp_backing['watchouts']}")

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
