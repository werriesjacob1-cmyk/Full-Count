#!/usr/bin/env python3
"""test_market_edge_semantics.py — regression coverage for the 2026-08-2X
market-edge-semantics fix (P0-6 data-integrity audit).

REAL BUG: odds_fanduel.attach_market_prices() and several standalone
market_odds/market_implied assignments in generate_picks.py (score_
combined_strikeouts, select_moonshots, select_deep_moonshots,
select_best_by_category's re-price loop) all set `market_implied` and
`market_edge` under the SAME field name/meaning regardless of market
family -- but for the genuinely two-sided markets (pitcher_outs,
strikeouts, nrfi_combined), market_implied is prop_probability.
devig_two_sided()'s EXACT no-vig fair probability, while for every
one-sided market (the majority of the real board: hits/total_bases/
home_runs/RBIs/runs/stolen_base/singles/doubles/triples/hits_runs_rbis/
combined_strikeouts/lasers/moonshots) it is the RAW posted implied
probability, INCLUDING the book's ~8% assumed hold, never de-vigged.
market_edge = model_prob - market_implied was computed identically and
shown to users as one undifferentiated "edge" comparable across markets,
when it structurally was not -- eval_lib.market_probability() already
built the exact right distinction for backtest analysis (see its own
docstring) but the live product never got the same honesty.

FIX: posted_implied (the raw, always-available number) / market_fair (the
honest fair-value comparator) / market_fair_method ("exact_two_sided" |
"assumed_hold") / edge_vs_fair (model - market_fair, the one edge number
honestly comparable across every market family) added everywhere
market_implied/market_edge are set, purely additive -- market_odds/
market_implied/market_edge keep their exact pre-existing meaning and
values, unchanged.

    /tmp/mlbvenv/bin/python3 test_market_edge_semantics.py
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
import odds_fanduel as fd  # noqa: E402
import prop_probability as pp  # noqa: E402

head("1. odds_fanduel.attach_market_prices(): pitcher_outs (two-sided) gets "
     "market_fair_method='exact_two_sided' and market_fair == market_implied")

po_candidate = {"name": "Test Pitcher", "projection": {"stat": "pitcher_outs", "needs": 15},
                 "hit_probability": 0.60}
po_prices = {fd.normalize_name("Test Pitcher"): {"over": -140, "under": 110, "needs": 15,
             "true_over": 0.62, "true_under": 0.38, "hold": 0.055}}
out, matched = fd.attach_market_prices([po_candidate], prices={}, k_prices={}, fi_prices={},
                                        combined_k_prices={}, po_prices=po_prices)
c = out[0]
check(matched == 1, "the pitcher_outs candidate matched a real price")
check(c["market_fair_method"] == "exact_two_sided", "pitcher_outs is labeled exact",
      f"got {c.get('market_fair_method')}")
check(c["market_fair"] == c["market_implied"] == 0.62,
      "market_fair equals the exact de-vigged true_over, same as market_implied",
      f"market_fair={c.get('market_fair')} market_implied={c.get('market_implied')}")
check(c["edge_vs_fair"] == c["market_edge"],
      "edge_vs_fair equals market_edge exactly when market_fair==market_implied (exact markets)",
      f"edge_vs_fair={c.get('edge_vs_fair')} market_edge={c.get('market_edge')}")
check(c["posted_implied"] == round(pp.implied_probability(-140), 4),
      "posted_implied is the RAW one-sided reading off the real posted price, distinct from "
      "the exact de-vigged market_fair", f"got posted_implied={c.get('posted_implied')} "
      f"vs raw={round(pp.implied_probability(-140), 4)}")
check(c["posted_implied"] != c["market_fair"],
      "posted_implied (raw, includes hold) and market_fair (exact, hold removed) genuinely "
      "differ for a real two-sided market with a nonzero hold",
      f"posted_implied={c['posted_implied']} market_fair={c['market_fair']}")

head("2. odds_fanduel.attach_market_prices(): a one-sided batter market (the generic "
     "branch -- covers hits/total_bases/home_runs/RBIs/runs/stolen_base/singles/doubles/"
     "triples/hits_runs_rbis/lasers/moonshots) gets market_fair_method='assumed_hold' and "
     "market_fair != market_implied (market_implied still includes the assumed hold)")

hit_candidate = {"name": "Test Batter", "projection": {"stat": "hits", "needs": 1},
                  "hit_probability": 0.65}
prices = {fd.normalize_name("Test Batter"): {("hits", 1): -150}}
out2, matched2 = fd.attach_market_prices([hit_candidate], prices=prices, k_prices={}, fi_prices={},
                                          combined_k_prices={}, po_prices={})
c2 = out2[0]
check(matched2 == 1, "the hits candidate matched a real price")
check(c2["market_fair_method"] == "assumed_hold", "a one-sided batter market is labeled assumed",
      f"got {c2.get('market_fair_method')}")
check(c2["posted_implied"] == c2["market_implied"],
      "posted_implied equals the existing (unchanged) market_implied for a one-sided market",
      f"posted_implied={c2.get('posted_implied')} market_implied={c2.get('market_implied')}")
check(c2["market_fair"] == round(pp.devig(c2["market_implied"]), 4),
      "market_fair is the assumed-hold de-vigged number, LOWER than the raw posted_implied "
      "(the hold inflates the raw number above true fair value)",
      f"market_fair={c2.get('market_fair')} posted_implied={c2.get('posted_implied')}")
check(c2["market_fair"] < c2["posted_implied"],
      "REGRESSION GUARD: the fair value must be strictly below the raw hold-inclusive number "
      "for a real positive-odds-implied favorite", f"market_fair={c2['market_fair']} "
      f"posted_implied={c2['posted_implied']}")
check(c2["edge_vs_fair"] > c2["market_edge"],
      "edge_vs_fair (against the true fair price) is LARGER than market_edge (against the "
      "hold-inflated raw price) -- the raw-based edge was UNDERSTATING the model's real edge "
      "for a one-sided market, not overstating it", f"edge_vs_fair={c2['edge_vs_fair']} "
      f"market_edge={c2['market_edge']}")

head("3. odds_fanduel.attach_market_prices(): strikeouts (two-sided) and nrfi_combined "
     "(two-sided) both get market_fair_method='exact_two_sided' too")

k_candidate = {"name": "Test Starter", "projection": {"stat": "strikeouts", "needs": 6},
               "hit_probability": 0.55}
k_prices = {fd.normalize_name("Test Starter"): {"over": -120, "under": 100, "needs": 6,
            "true_over": 0.545, "true_under": 0.455, "hold": 0.045}}
out3, _ = fd.attach_market_prices([k_candidate], prices={}, k_prices=k_prices, fi_prices={},
                                   combined_k_prices={}, po_prices={})
check(out3[0]["market_fair_method"] == "exact_two_sided", "strikeouts is labeled exact",
      f"got {out3[0].get('market_fair_method')}")

nrfi_candidate = {"name": "N/A", "matchup": "Away @ Home", "lean": "YRFI",
                   "projection": {"stat": "nrfi_combined"}, "hit_probability": 0.5}
fi_prices = {"Away @ Home": {"over": -110, "under": -110, "true_over": 0.5, "true_under": 0.5,
             "hold": 0.048}}
out4, _ = fd.attach_market_prices([nrfi_candidate], prices={}, k_prices={}, fi_prices=fi_prices,
                                   combined_k_prices={}, po_prices={})
check(out4[0]["market_fair_method"] == "exact_two_sided", "nrfi_combined is labeled exact",
      f"got {out4[0].get('market_fair_method')}")

head("4. odds_fanduel.attach_market_prices(): combined_strikeouts (a one-sided ladder, "
     "NOT the same as the genuinely two-sided pitcher-strikeouts market above) gets "
     "market_fair_method='assumed_hold'")

combo_candidate = {"name": "A & B", "matchup": "Away @ Home",
                    "projection": {"stat": "combined_strikeouts", "needs": 13},
                    "hit_probability": 0.5}
combined_k_prices = {"Away @ Home": {"rungs": {13: -130}}}
out5, _ = fd.attach_market_prices([combo_candidate], prices={}, k_prices={}, fi_prices={},
                                   combined_k_prices=combined_k_prices, po_prices={})
check(out5[0]["market_fair_method"] == "assumed_hold",
      "combined_strikeouts (one-sided ladder) is labeled assumed, not exact -- it must not be "
      "confused with the genuinely two-sided pitcher-strikeouts market despite the similar name",
      f"got {out5[0].get('market_fair_method')}")

head("5. generate_picks.select_moonshots()/select_deep_moonshots(): a one-sided HR market "
     "gets the same honest assumed-hold labeling, and edge_vs_fair is real")

moonshot_c = {
    "type": "batter", "name": "Slugger", "player_id": 5, "team": "Athletics",
    "matchup": "Athletics @ Astros", "game_pk": 900001, "score": 70,
    "confidence": "Medium", "notable_signals": 1, "signals": {},
    "line_options": [{"stat": "home_runs", "needs": 1, "prob": 0.18, "base_rate": 0.10,
                       "lift": 0.08, "basis": "modelled", "empirical": None, "modelled": 0.18}],
}
ms_prices = {fd.normalize_name("Slugger"): {("home_runs", 1): 550}}
out6 = gp.select_moonshots([moonshot_c], ms_prices, fd)
c6 = out6[0]
check(c6["market_fair_method"] == "assumed_hold", "select_moonshots labels HR odds as assumed",
      f"got {c6.get('market_fair_method')}")
check(c6["market_fair"] == round(pp.devig(c6["market_implied"]), 4),
      "select_moonshots' market_fair is the real de-vigged value", f"got {c6}")
check(c6["edge_vs_fair"] == round(c6["hit_probability"] - c6["market_fair"], 4),
      "select_moonshots' edge_vs_fair is model probability minus the real fair value")

deep_c = {
    "type": "batter", "name": "Slugger", "player_id": 5, "team": "Athletics",
    "matchup": "Athletics @ Astros", "game_pk": 900001,
    "projection": {"stat": "moonshot_420", "value": 1, "needs": 1},
    "hit_probability": 0.08, "score": 60, "confidence": "Medium",
}
deep_prices = {fd.normalize_name("Slugger"): {("moonshot_420", 1): 900}}
out7 = gp.select_deep_moonshots([deep_c], deep_prices, fd)
c7 = out7[0]
check(c7["market_fair_method"] == "assumed_hold", "select_deep_moonshots labels odds as assumed",
      f"got {c7.get('market_fair_method')}")
check(c7["market_fair"] == round(pp.devig(c7["market_implied"]), 4),
      "select_deep_moonshots' market_fair is the real de-vigged value")

head("6. generate_picks.score_combined_strikeouts(): the module-level candidate creation "
     "itself carries the honest fields (not just the later re-price paths)")

GM = {"matchup": "Away @ Home", "away_team": "Away", "home_team": "Home", "game_pk": 1,
      "series_game": 1, "home_sp": "H", "away_sp": "A"}
away_pc = {"expected_bf": 24.0, "k_rate": 0.24, "confidence": "High"}
home_pc = {"expected_bf": 22.0, "k_rate": 0.22, "confidence": "High"}
combined_prices = {"Away @ Home": {"pitchers": ("A", "H"),
                    "rungs": {12: -110, 13: 105, 14: 160}}}
c8 = gp.score_combined_strikeouts(GM, away_pc, home_pc, combined_prices)
check(c8 is not None, "a real combined-strikeouts candidate was created")
check(c8["market_fair_method"] == "assumed_hold",
      "score_combined_strikeouts itself (not just the re-price loop) carries the honest label",
      f"got {c8.get('market_fair_method') if c8 else None}")
check(c8["posted_implied"] == c8["market_implied"],
      "posted_implied matches the existing market_implied for this one-sided ladder market")

head("7. dashboard/build_dashboard.py's clean() allowlist carries the four new fields through")

import re  # noqa: E402
src = open("dashboard/build_dashboard.py").read()
for field in ("posted_implied", "market_fair", "market_fair_method", "edge_vs_fair"):
    check(re.search(rf'"{field}":\s*r\.get\("{field}"\)', src) is not None,
          f"clean() reads r.get('{field}') into the public payload -- the same "
          f"'computed, then discarded' boundary market_hold was already fixed for",
          f"field={field} not found in clean()'s allowlist")

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
