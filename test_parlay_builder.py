#!/usr/bin/env python3
"""test_parlay_builder.py — checks parlay_builder.py's request parsing, pool
loading, leg selection, and correlation screening against hand-built cases
with a known right answer, and against the real board where available.

    /tmp/mlbvenv/bin/python3 test_parlay_builder.py
    python3 test_parlay_builder.py -v
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import parlay_builder as pb

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


def batter(name, team, matchup, game_pk, stat, hit_probability, player_id=None):
    return {"name": name, "team": team, "matchup": matchup, "game_pk": game_pk,
            "type": "batter", "player_id": player_id or name,
            "projection": {"stat": stat}, "hit_probability": hit_probability}


def pitcher(name, team, matchup, game_pk, side, stat, hit_probability, player_id=None):
    return {"name": name, "team": team, "matchup": matchup, "game_pk": game_pk,
            "type": "pitcher", "side": side, "player_id": player_id or name,
            "projection": {"stat": stat}, "hit_probability": hit_probability}


head("1. parse_request -- phrasings this was scoped against")

r = pb.parse_request("2 home runs, 1 double, 1 triple")
check(r.prop_counts == {"home_runs": 2, "doubles": 1, "triples": 1},
      "counts multiple prop types with explicit quantities",
      f"got {r.prop_counts}")

r = pb.parse_request("$5 to $1,000 parlay, 2 home runs, 1 double, 1 triple")
check(r.stake == 5.0 and r.target_payout == 1000.0,
      "extracts a stake-to-payout range with a comma in the number",
      f"got stake={r.stake} target={r.target_payout}")
check(r.prop_counts == {"home_runs": 2, "doubles": 1, "triples": 1},
      "prop counts still parse correctly alongside a dollar range")

r = pb.parse_request("give me a riskier parlay, something the data doesn't "
                     "fully support but has some truth behind it")
check(r.risk_tier == "risky", "'riskier' language maps to the risky tier",
      f"got {r.risk_tier}")

r = pb.parse_request("best picks today")
check(r.risk_tier == "safest", "'best' language maps to the safest tier",
      f"got {r.risk_tier}")

r = pb.parse_request("2 home runs")
check(r.risk_tier == "balanced",
      "no risk language at all defaults to balanced, not a guess at either extreme",
      f"got {r.risk_tier}")

r = pb.parse_request("a triple and a stolen base")
check(r.prop_counts == {"triples": 1, "stolen_base": 1},
      "bare article ('a triple') is read as quantity 1",
      f"got {r.prop_counts}")

r = pb.parse_request("nonsense request with no recognizable props")
check(r.prop_counts == {}, "unrecognised text extracts nothing rather than guessing")

r = pb.parse_request("risk level 70, 2 home runs")
check(r.risk_level == 70.0, "an explicit numeric risk dial in text is parsed directly",
      f"got {r.risk_level}")
check(r.effective_risk_level() == 70.0,
      "an explicit risk_level wins over the word-based risk_tier default")

r = pb.parse_request("2 home runs")
check(r.risk_level is None and r.effective_risk_level() == 50,
      "no explicit dial value falls back to the word-based tier's anchor "
      "-- no risk words at all defaults to the balanced tier (50), same as risk_tier itself",
      f"risk_level={r.risk_level} effective={r.effective_risk_level()}")

head("2. risk_band -- continuous dial, and its named-tier anchors")

lo, hi = pb.RISK_BANDS["safest"]
check(lo == pb.MIN_LINE_PROB, "safest band floor matches MIN_LINE_PROB exactly",
      f"got {lo} vs {pb.MIN_LINE_PROB}")

check(pb.risk_band(0) == pb.RISK_BANDS["safest"],
      "risk_band(0) matches the safest named tier exactly")
check(pb.risk_band(50) == pb.RISK_BANDS["balanced"],
      "risk_band(50) matches the balanced named tier exactly")
check(pb.risk_band(100) == pb.RISK_BANDS["risky"],
      "risk_band(100) matches the risky named tier exactly")

lo25, hi25 = pb.risk_band(25)
lo0, hi0 = pb.risk_band(0)
lo50, hi50 = pb.risk_band(50)
check(lo0 > lo25 > lo50 and hi0 > hi25 > hi50,
      "a dial value between two anchors interpolates strictly between their bands, "
      "not just snapping to the nearer one",
      f"lo0={lo0} lo25={lo25} lo50={lo50} hi0={hi0} hi25={hi25} hi50={hi50}")

check(pb.risk_band(-10) == pb.risk_band(0) and pb.risk_band(150) == pb.risk_band(100),
      "out-of-range dial values clamp into [0, 100] instead of raising")

head("3. _select_legs -- risk bands and correlation screening")

a = batter("A. Batter", "Mets", "Mets @ Pirates", 1, "hits", 0.70)
b = batter("B. Batter", "Mets", "Mets @ Pirates", 1, "hits", 0.65)
# Same team as a/b, so his strikeout prop is POSITIVE (own hitters), not
# negative -- the clean case for "both legs fill".
p_own_team = pitcher("Away SP", "Mets", "Mets @ Pirates", 1, "away",
                     "strikeouts", 0.68)
# On the OPPOSING team, so his strikeout prop works against a/b -- negative.
p_opposing = pitcher("Home SP", "Pirates", "Mets @ Pirates", 1, "home",
                     "strikeouts", 0.66)
pool = [a, b, p_own_team]

legs, shortfalls = pb._select_legs(pool, {"hits": 1, "strikeouts": 1}, pb.RISK_TIER_LEVELS["safest"])
check(len(legs) == 2 and not shortfalls,
      "request across two stats fills both legs when the candidates are "
      "positively (not negatively) correlated",
      f"legs={[l['name'] for l in legs]}")
check(pb.corr.classify(legs[0], legs[1]).label != "negative",
      "the two selected legs are never a negative-correlation pair",
      f"{pb.corr.classify(legs[0], legs[1])}")

# c is on the Mets -- the team p_opposing (a Pirates pitcher) is FACING --
# so selecting both should be refused in favor of a shortfall, not silently
# building a bad parlay.
c = batter("C. Batter", "Mets", "Mets @ Pirates", 1, "hits", 0.72)
pool2 = [c, p_opposing]
legs2, shortfalls2 = pb._select_legs(pool2, {"hits": 1, "strikeouts": 1}, pb.RISK_TIER_LEVELS["safest"])
check(len(legs2) == 1 and len(shortfalls2) == 1,
      "when the only hits candidate is negatively correlated with the only "
      "strikeout candidate, one leg is dropped as a shortfall rather than "
      "building the bad pair",
      f"legs={[l['name'] for l in legs2]} shortfalls={shortfalls2}")

# duplicate-player guard: same player can't fill two different legs.
dup_pool = [dict(a, projection={"stat": "total_bases"}), a]
legs3, _ = pb._select_legs(dup_pool, {"hits": 1, "total_bases": 1}, pb.RISK_TIER_LEVELS["safest"])
names3 = [l["player_id"] for l in legs3]
check(len(names3) == len(set(names3)),
      "the same player is never selected twice across different legs",
      f"got {names3}")

head("4. build_parlay -- end to end with a synthetic pool, no network")

req = pb.ParlayRequest(prop_counts={"hits": 1, "strikeouts": 1}, risk_tier="safest")
res = pb.build_parlay(req, pool=[a, b, p_own_team], price_legs=False)
check(len(res["legs"]) == 2 and not res["shortfalls"],
      "build_parlay assembles a full 2-leg parlay from a clean synthetic pool")
check(res["naive_combined_probability"] is not None
     and 0 < res["naive_combined_probability"] < 1,
      "naive combined probability is a real product in (0, 1) when legs exist",
      f"got {res['naive_combined_probability']}")
check(res["combined_decimal_odds"] is None,
      "combined decimal odds stays None when price_legs=False -- never fabricated",
      f"got {res['combined_decimal_odds']}")

req_impossible = pb.ParlayRequest(prop_counts={"triples": 3}, risk_tier="safest")
res_impossible = pb.build_parlay(req_impossible, pool=[a, b, p_opposing], price_legs=False)
check(res_impossible["legs"] == [] and res_impossible["naive_combined_probability"] is None,
      "a request with zero matching candidates returns no legs and no fabricated probability",
      f"got legs={res_impossible['legs']} prob={res_impossible['naive_combined_probability']}")
check(res_impossible["shortfalls"] == [{"stat": "triples", "requested": 3, "found": 0}],
      "the impossible request is reported as an honest shortfall",
      f"got {res_impossible['shortfalls']}")

game_filtered = pb.build_parlay(
    pb.ParlayRequest(prop_counts={"hits": 1}, risk_tier="safest", game_filter=["Dodgers"]),
    pool=[a, b, p_opposing], price_legs=False)
check(game_filtered["legs"] == [] and game_filtered["shortfalls"],
      "a game filter that matches nothing in the pool yields a shortfall, not a leg from the wrong game")

# A numeric risk_level flows all the way through build_parlay, not just
# _select_legs directly -- e.g. dialed all the way to 100 (riskiest), the
# 0.70/0.65/0.68-probability synthetic pool has nothing below the risky
# band's 0.40 ceiling, so it should come back empty with a shortfall.
risky_dial = pb.build_parlay(
    pb.ParlayRequest(prop_counts={"hits": 1}, risk_level=100),
    pool=[a, b, p_own_team], price_legs=False)
check(risky_dial["legs"] == [] and risky_dial["shortfalls"],
      "dialing risk_level to 100 excludes safe-band candidates that don't belong in a risky parlay",
      f"legs={risky_dial['legs']} shortfalls={risky_dial['shortfalls']}")

head("5. format_parlay_text -- the CLI output")

req_fmt = pb.ParlayRequest(prop_counts={"hits": 1, "strikeouts": 1}, risk_tier="safest")
res_fmt = pb.build_parlay(req_fmt, pool=[a, b, p_own_team], price_legs=False)
text_fmt = pb.format_parlay_text(res_fmt)
check(a["name"] in text_fmt or b["name"] in text_fmt,
      "a filled leg's player name appears in the formatted text")
check("Naive combined probability" in text_fmt,
      "the combined probability is labelled as the floor it is, not a bare number")

empty_res = pb.build_parlay(
    pb.ParlayRequest(prop_counts={"triples": 3}, risk_tier="safest"),
    pool=[a, b, p_own_team], price_legs=False)
empty_text = pb.format_parlay_text(empty_res)
check("No legs could be filled" in empty_text and "triples" in empty_text,
      "an unfillable request explains itself in the text output rather than printing nothing useful",
      empty_text)

head("6. sanity against today's real pool, if it exists")
try:
    pool_real = pb.load_todays_pool()
    if pool_real:
        check(all(c.get("hit_probability") is not None for c in pool_real),
              "every candidate in the real pool has a real hit_probability "
              "(load_todays_pool's own filter)")
        req_real = pb.ParlayRequest(prop_counts={"hits": 1}, risk_tier="balanced")
        res_real = pb.build_parlay(req_real, pool=pool_real, price_legs=False)
        check(res_real["legs"] or res_real["shortfalls"],
              "a request against the real pool returns either legs or an honest shortfall, never silently nothing",
              f"legs={len(res_real['legs'])} shortfalls={res_real['shortfalls']}")
    else:
        print("  (no players data for today -- skipped)")
except Exception as e:
    print(f"  (real pool unavailable -- skipped: {e})")

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
