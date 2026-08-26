#!/usr/bin/env python3
"""test_ci_provenance.py — regression coverage for the 2026-08-2X CI/
uncertainty provenance-honesty fix (P0-7 data-integrity audit).

Direct instruction: "prob_ci_source must be preserved and labeled
honestly -- player empirical vs historical reliability-band vs none.
Never show a generic '95% interval' label if the underlying meaning
differs; show none if no defensible interval exists."

REAL GAP found while tracing this: prob_ci_source WAS being set correctly
on a candidate's primary line (generate_picks.attach_reliability, fixed
in an earlier commit this session) and on select_best_by_category()'s
per-line-option path (_batter_options' options.append, apply_calibration's
historical-band reassignment) -- but three separate "computed, then
discarded" boundaries dropped it before it ever reached a reader:
_keep_options() (builds c["line_options"] from those same per-option
dicts), select_best_by_category()'s own by_category dict, and two
generic candidate-forwarding dicts (the shadow "alternates" path and
write_json's ctx dict) that all copy prob_ci without also copying
prob_ci_source. Each is exactly the same class of bug this file's own
"Real bug, found 2026-08-15 audit" comment already documents for prob_ci
itself -- ci_source just never got the same treatment when it was added.

    /tmp/mlbvenv/bin/python3 test_ci_provenance.py
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

COMP = {"singles_rate": 0.15, "double_rate": 0.05, "triple_rate": 0.004, "hr_rate": 0.035}
# Deliberately no total_bases_* entries here -- attach_hit_probabilities'
# MODEL_SHRINK_K path only fires when a true_league_rates entry exists for
# this exact key, which produces basis="modelled_shrunk" (no CI concept).
# Leaving them out forces the plain _blend(empirical, modelled) path,
# producing basis="blended" -- the real class this fix's Wilson-interval
# branch (basis in ("empirical", "blended")) actually covers.
TRUE_LEAGUE = {}


def batter_c(pid=5, stat="total_bases", pa=4.3, **over):
    c = {"type": "batter", "player_id": pid, "projected_pa": pa,
         "projection": {"stat": stat, "value": 1.5, "needs": 2}, "prop": "Over 1.5 Total Bases",
         "name": "Test Batter", "team": "Athletics", "matchup": "Athletics @ Astros",
         "game_pk": 900001, "score": 65, "confidence": "Medium", "notable_signals": 0,
         "signals": {}, "reliability": "B", "sample_n": 100, "why": [], "watchouts": []}
    c.update(over)
    return c


head("1. _batter_options (inside attach_hit_probabilities): a real per-player Wilson "
     "interval on a genuinely empirical/blended line gets ci_source='player_empirical', "
     "surviving into c['line_options'] (NOT dropped by _keep_options' own field trim -- "
     "the exact class of bug this fix closes, same as the 2026-08-15 'ci' fix above it)")

c = batter_c(pid=5)
emp = {5: {"games": 100, "rates": {
    "total_bases_2plus": {"p_hat": 0.75, "p": 0.75, "league_p": 0.30, "n": 100},
    "total_bases_3plus": {"p_hat": 0.30, "p": 0.30, "league_p": 0.15, "n": 100},
}}}
out = gp.attach_hit_probabilities([c], {5: COMP}, emp, {}, league_rates=TRUE_LEAGUE)
c_out = out[0]
line_opts = c_out.get("line_options") or []
check(len(line_opts) > 0, "the candidate has real line_options", f"got {c_out}")
with_ci = [o for o in line_opts if o.get("ci") is not None]
check(len(with_ci) > 0, "at least one line_option has a real Wilson CI (n=100 backs it)",
      f"got line_options={line_opts}")
check(all(o.get("ci_source") == "player_empirical" for o in with_ci),
      "REGRESSION GUARD: every line_option carrying a real ci also carries "
      "ci_source='player_empirical' -- not dropped by _keep_options' field trim",
      f"got {with_ci}")

head("2. select_best_by_category(): the by-category board row carries prob_ci_source "
     "alongside prob_ci (both from the SAME winning line_option) -- previously only "
     "prob_ci itself made it into this dict")

import odds_fanduel as fd  # noqa: E402
out2 = gp.select_best_by_category([c_out], {}, fd)
tb_rows = out2.get("total_bases") or []
check(len(tb_rows) > 0, "a real total_bases by-category row was produced", f"got {out2}")
row = tb_rows[0]
check(row.get("prob_ci") is not None, "the by-category row carries a real prob_ci",
      f"got {row}")
check(row.get("prob_ci_source") == "player_empirical",
      "REGRESSION GUARD: the by-category row's prob_ci_source matches its prob_ci's real "
      "source, not silently dropped at this dict-construction boundary", f"got {row}")

head("3. no-CI markets (e.g. nrfi_combined's standalone candidate creation) explicitly "
     "carry prob_ci_source=None rather than omitting the key entirely -- an honest "
     "'none' per the task directive, not an ambiguous absence")

fi_away = {"type": "pitcher", "name": "Away SP", "projection": {"stat": "first_inning_run"},
           "side": "away", "game_pk": 42, "team": "Away", "lean": "NRFI",
           "hit_probability": 0.72, "signals": {"fi_n_starts": 8}}
fi_home = {"type": "pitcher", "name": "Home SP", "projection": {"stat": "first_inning_run"},
           "side": "home", "game_pk": 42, "team": "Home", "lean": "NRFI",
           "hit_probability": 0.70, "signals": {"fi_n_starts": 6}}
combined = gp._build_combined_nrfi([fi_away, fi_home])
check(len(combined) == 1, "a real combined-NRFI candidate was produced from both real halves",
      f"got {combined}")
check(all("prob_ci_source" in row for row in combined),
      "every nrfi_combined candidate explicitly carries the prob_ci_source key "
      "(as None, since this market has no defensible CI concept), not omitted",
      f"got {combined}")
check(all(row.get("prob_ci_source") is None for row in combined),
      "and its value is honestly None -- no fabricated CI-source label for a market with "
      "no real interval concept", f"got {combined}")

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
