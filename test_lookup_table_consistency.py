#!/usr/bin/env python3
"""test_lookup_table_consistency.py — mechanically checks the lookup tables
this project keeps duplicating against each other and against
mlb_sources._PROP_THRESHOLDS (the closest thing this codebase has to a
single source of truth for "which batter stat families exist").

WHY THIS EXISTS. Every real bug found in the 2026-08-12 audit pass was the
same shape: a stat name spelled two ways ("home_run" vs "home_runs"), or a
market added in one place and never added to a lookup table living
somewhere else (CATEGORY_LABELS duplicated three times across
generate_picks.py/render_board.py/render_full_board.py; CURRENT_WEIGHTS
missing six batter markets; a 2+/3+ home-run threshold priced and rated but
never actually offered as a candidate). Every one of those was found by a
human reading files and happening to compare the right two things. This
test makes that comparison automatic and permanent, so the next new market
gets caught by CI instead of by someone remembering to check three files.

This is not exhaustive -- it checks the specific tables already found
drifting, not a general "all dicts must agree" rule (some, like
_PITCHER_STATS_OPPOSE_HITTERS, are deliberately a strict SUBSET of the
stat universe and have no "should contain everything" obligation). Extend
it as new lookup tables are added, the same way this project's other
tests grew alongside real bugs.

    /tmp/mlbvenv/bin/python3 test_lookup_table_consistency.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import generate_picks as gp
import render_board as rb
import render_full_board as rfb
import mlb_sources as src
import odds_fanduel as fd

sys.path.insert(0, "backtest")
import signals as bt_signals

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


# All nine batter stat families _PROP_THRESHOLDS/_batter_options actually
# compute a rate for. This is the closest thing this codebase has to a
# ground truth: it is what mlb_sources.empirical_batter_prop_rates()
# genuinely calculates from real game logs, independent of whether any
# other table has caught up to it yet.
BATTER_STATS = set(src._PROP_THRESHOLDS)

head("1. CATEGORY_LABELS: three independent copies of the same table")

gp_keys = set(gp.CATEGORY_LABELS)
rb_keys = set(rb.CATEGORY_LABELS)
rfb_keys = set(rfb.CATEGORY_LABELS)

# render_board.py alone carries two legacy keys (first_inning_run, walks)
# from before task #8 removed both from the board -- harmless there because
# CATEGORY_LABELS.get(stat, fallback) is used as a cosmetic label lookup in
# both render files, never as a filter (unlike generate_picks.py's own
# copy, which DOES filter on membership -- see select_best_by_category).
# Documented exception, not a bug: dead keys in a .get()-only table cost
# nothing.
RENDER_BOARD_LEGACY_EXTRAS = {"first_inning_run", "walks"}

check(gp_keys == rfb_keys,
      "generate_picks.py and render_full_board.py's CATEGORY_LABELS have the same keys",
      f"generate_picks only: {gp_keys - rfb_keys or None}, "
      f"render_full_board only: {rfb_keys - gp_keys or None}")

check(gp_keys == (rb_keys - RENDER_BOARD_LEGACY_EXTRAS),
      "render_board.py's CATEGORY_LABELS matches generate_picks.py's, "
      "modulo the documented legacy extras",
      f"generate_picks only: {gp_keys - rb_keys or None}, "
      f"render_board only (beyond the documented extras): "
      f"{(rb_keys - RENDER_BOARD_LEGACY_EXTRAS) - gp_keys or None}")

# The specific bug this audit found: home_runs is a real, live batter
# market (mlb_sources._PROP_THRESHOLDS has always carried it) and
# select_best_by_category's own docstring names it as the exact example of
# a family that must not be structurally excluded -- so every batter stat
# family must appear in generate_picks.py's own CATEGORY_LABELS.
#   - walks: excluded on purpose -- score_walk is never called any more
#     (see build_candidates' own comment) -- not expected to appear here.
#   - stolen_bases: _PROP_THRESHOLDS/mlb_sources use the plural (matching
#     FanDuel's own market name), but every OTHER table in this codebase
#     (CATEGORY_LABELS, CURRENT_WEIGHTS, attach_hit_probabilities' own
#     branch) uses generate_picks.py's internal singular "stolen_base" --
#     see odds_fanduel.STAT_ALIASES' own comment on exactly this split.
#     Checked separately below instead of expecting the plural key here.
EXPECTED_IN_CATEGORY_LABELS = BATTER_STATS - {"walks", "stolen_bases"}
missing = EXPECTED_IN_CATEGORY_LABELS - gp_keys
check(not missing,
      "every live batter stat family (mlb_sources._PROP_THRESHOLDS, minus "
      "the retired walks market and the stolen_base/stolen_bases spelling "
      "split) has a CATEGORY_LABELS entry in generate_picks.py",
      f"missing: {missing or None}")
check("stolen_base" in gp_keys,
      "stolen_base (singular -- this codebase's internal spelling) has a "
      "CATEGORY_LABELS entry")

head("2. CURRENT_WEIGHTS (backtest/signals.py) batter coverage")

# Every batter family shares score_batter's one composite formula (see
# backtest/signals.py's own comment on this, added the same audit pass that
# found the six missing entries), so every _PROP_THRESHOLDS batter stat
# should resolve to a real weight table.
cw_missing = EXPECTED_IN_CATEGORY_LABELS - set(bt_signals.CURRENT_WEIGHTS)
check(not cw_missing,
      "every live batter stat family has a CURRENT_WEIGHTS entry",
      f"missing: {cw_missing or None}")

# The other direction matters too: a stray/misspelled key (the exact
# "home_run" vs "home_runs" bug found this pass) is invisible to a
# missing-key check, since the wrong key still LOOKS present. Catch it by
# requiring every non-exempt CURRENT_WEIGHTS key to be a real, live stat.
CURRENT_WEIGHTS_NON_BATTER_EXEMPT = {"strikeouts", "stolen_base", "walks", "first_inning_run"}
cw_stray = (set(bt_signals.CURRENT_WEIGHTS) - BATTER_STATS - CURRENT_WEIGHTS_NON_BATTER_EXEMPT)
check(not cw_stray,
      "no CURRENT_WEIGHTS key is a stray/misspelled stat name",
      f"unexpected keys: {cw_stray or None}")

head("3. odds_fanduel.MARKET_MAP: every priced batter stat is reachable")

# MARKET_MAP is keyed by FanDuel market TYPE, not by stat -- collect the
# stats that appear as a mapped value anywhere in it.
market_map_stats = {stat for stat, _needs in fd.MARKET_MAP.values()}
# stolen_base only ever reaches FanDuel's price feed via STAT_ALIASES
# (generate_picks.py's internal singular "stolen_base" -> the market's
# plural "stolen_bases"), so check for either spelling.
market_map_stats |= {fd.STAT_ALIASES.get(s, s) for s in list(market_map_stats)}

mm_missing = EXPECTED_IN_CATEGORY_LABELS - {"stolen_base"} - market_map_stats
# stolen_base itself: confirm the alias resolves to something MARKET_MAP covers.
check(fd.STAT_ALIASES.get("stolen_base") in market_map_stats or "stolen_base" in market_map_stats,
      "stolen_base resolves (directly or via STAT_ALIASES) to a real MARKET_MAP entry")
check(not mm_missing,
      "every live batter stat family (except stolen_base, checked separately) "
      "has at least one MARKET_MAP entry -- i.e. FanDuel's real price is reachable",
      f"missing: {mm_missing or None}")

head("4. generate_picks._batter_options offers every real, priced threshold")

# _batter_options is the function that actually turns a rate into a
# candidate probability -- a threshold that is a REAL FanDuel market
# (present in MARKET_MAP) and has an empirical rate available
# (_PROP_THRESHOLDS) but is absent from _batter_options' own families list
# is exactly the "computed, then discarded" bug class this project keeps
# finding (most recently: home_runs 2+/3+, and five more found by this
# exact check the same pass it was written -- hits 4+, total_bases 5+,
# runs 3+, rbis 3+/4+, hits_runs_rbis 4+).
#
# Ground truth is MARKET_MAP, not the raw _PROP_THRESHOLDS list: some
# thresholds (total_bases 1+) have an empirical rate computed but no real
# FanDuel market to bet them against -- _PROP_THRESHOLDS tracking more than
# is bettable is fine (it costs one extra number per game log already
# fetched); _batter_options not offering a threshold nobody can price is
# not a bug, only not offering one that IS priced would be.
market_map_thresholds = set(fd.MARKET_MAP.values())

# Extracted by calling the real function with a synthetic pool and reading
# which (stat, needs) pairs it is even capable of returning, across every
# threshold this file tracks so each has a chance to surface.
_emp = {"games": 200, "rates": {
    f"{stat}_{n}plus": {"p_hat": 0.5, "p": 0.5, "n": 200, "hit": 100}
    for stat, ns in src._PROP_THRESHOLDS.items() for n in ns
}}
_opts = gp._batter_options({"projected_pa": 4.5},
                           {"singles_rate": 0.16, "double_rate": 0.05,
                            "triple_rate": 0.004, "hr_rate": 0.045}, _emp)
_offered = {(o["stat"], o["needs"]) for o in _opts}

for stat, needs in sorted(market_map_thresholds):
    if stat not in BATTER_STATS - {"walks", "stolen_bases"}:
        continue  # hard_hit_105/110 etc. are not _batter_options families -- own scorer (score_laser).
    check((stat, needs) in _offered,
          f"_batter_options offers {stat} {needs}+ (a real, priced MARKET_MAP threshold)",
          f"_batter_options only offered: {sorted(s for s in _offered if s[0] == stat)}")

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
