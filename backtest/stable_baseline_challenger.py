#!/usr/bin/env python3
"""stable_baseline_challenger.py -- RESEARCH ONLY. Does not touch production.

Answers the authorized research question: if generate_picks.py's `base_rate`
for hits_runs_rbis/runs/rbis (currently mlb_sources._apply_shrinkage's
`league_p`, pooled ACROSS ONLY TONIGHT'S ~250-300 SLATE BATTERS -- see the
PRODUCTION TRACE section below) were instead a stable, point-in-time
historical baseline, what would actually change downstream: Lean
eligibility (the one real gate `lift` participates in today) and, as a
separate labeled research-only experiment, a simulated positive-lift Top
Pick requirement.

================================================================================
PRODUCTION TRACE: league_p -> base_rate -> lift (exact, from code, not guessed)
================================================================================
generate_picks.py's batter-stat families loop (~line 5006) calls
mlb_sources.empirical_batter_prop_rates(batter_ids=<tonight's slate>, asof=...)
for hits_runs_rbis/runs/rbis (fn=None -- no modelled PA-distribution term for
these three; see that loop's own comment on why runs/RBIs have none). That
function:
  1. _empirical_batter_one(pid, min_games, asof) pulls THIS SEASON's real
     game log for player pid, up to `asof`, and for each (prop, threshold)
     computes p = hits/n over that player's OWN games this season
     (min_games gate, default 20).
  2. _apply_shrinkage(table) then POOLS (hit, n) across EVERY PLAYER PASSED
     INTO THE CALL -- i.e. tonight's slate roster, NOT the whole league --
     to get `league = total_hits / total_n` for that key, fits a
     beta-binomial n0 from those same per-player pairs, and writes
     r["league_p"] = league, r["p_hat"] = shrunk estimate.
  3. Back in generate_picks.py, emp_p() copies r["league_p"] into
     base_rates[key] whenever no TRUE season-long league rate exists for
     that key (true_league_rates, sourced from mlb_sources.league_base_rates()
     -- confirmed absent for hits_runs_rbis/runs/rbis; that table has no
     entries for these three stats at all).
  4. options.append(..., "base_rate": base, "lift": prob - base) -- so for
     these three markets `base_rate` IS r["league_p"]: a SEASON-TO-DATE rate,
     but pooled across only whichever specific batters are playing tonight,
     recomputed fresh every night from a different roster.

WHAT THIS MEANS FOR THE STABLE-BASELINE COMPARISON: the variable actually
under test is POPULATION SCOPE (tonight's ~250-batter roster vs the whole
league) crossed with TIME WINDOW (season-to-date vs trailing/multi-season),
not the underlying mechanism -- both production and every stable candidate
below use the exact same "pool real per-game hit/attempt outcomes, up to
the date being scored, never after" definition of a rate. `predicted_prob`
(the number the model actually bet) is computed upstream of base_rate and does
NOT change under any candidate here -- only what it's being COMPARED against
does.

================================================================================
PARITY GAP, DISCLOSED RATHER THAN PAPERED OVER
================================================================================
backtest/rows.jsonl does not persist the live base_rate/lift fields (they are
computed but not written to backtest output), and reproducing tonight's-exact
roster pooling for 341+ historical dates would mean re-running
mlb_sources.empirical_batter_prop_rates(asof=...) with real MLB game-log
network fetches for every batter on every historical slate -- the same class
of expensive job the currently-running backfill/repair engine runs (hours,
not minutes). That was not re-run here.

Instead, "SLATE-SCOPED (candidate A)" below reconstructs the closest
available proxy from data already in rows.jsonl: the same-night
cross-sectional OUTCOME rate for the exact (date, stat, needs) key -- pooling
this season's real per-game results across the batters who actually appear as
graded rows that night, which is the identical "hit/n pooled across tonight's
roster" mechanism, differing from true production only in that rows.jsonl's
roster is "batters who became gradeable candidates" rather than "every
batter mlb_sources.empirical_batter_prop_rates() was asked about" (a strict
subset relationship, not a different population). The remaining uncertainty
this leaves unquantified: candidate A here may be slightly less noisy than
true production (a smaller candidate roster, already screened by
quality_control, versus the full nightly batter_ids list) -- if anything this
means production's TRUE historical league_p was likely NOISIER than
candidate A shows here, which would only strengthen (not weaken) the case
for a stable baseline. This is stated as a direction, not fabricated as a
number.

================================================================================
CANDIDATES (all point-in-time: only data dated STRICTLY BEFORE the row's own
date is ever used; no same-day outcomes, no future-season information)
================================================================================
  A) SLATE-SCOPED PROXY (closest available reconstruction of current prod)
  B) TRAILING 60 calendar days, pooled, this stat+needs
  C) SEASON-TO-DATE (this season only, from March 20 through yesterday)
  D) MULTI-SEASON BLEND: linear ramp over the first 60 days of a season from
     last season's full rate to this season's to-date rate

    /tmp/mlbvenv/bin/python3 backtest/stable_baseline_challenger.py
"""
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

ROWS_FILE = "/home/user/PROJECT-GRIDIRON/backtest/rows.jsonl"
STATS = ("hits_runs_rbis", "runs", "rbis")
TRAILING_DAYS = 60
LEAN_MIN_LIFT = 0.02
TOP_PICK_MIN_PROB = 0.60
NEARMISS_LO = 0.50


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


def season_start(dt):
    return datetime(dt.year, 3, 20)


def season_phase(dt):
    start = season_start(dt) if dt >= season_start(dt) else season_start(dt - timedelta(days=200))
    days_in = (dt - start).days
    if days_in < 30:
        return "early(<30d)"
    if days_in < 90:
        return "mid(30-90d)"
    return "late(90d+)"


def auc(pairs):
    pos = [s for s, o in pairs if o == 1]
    neg = [s for s, o in pairs if o == 0]
    if not pos or not neg:
        return None
    all_scores = sorted(s for s, _ in pairs)
    ranks = {}
    i, n = 0, len(all_scores)
    while i < n:
        j = i
        while j < n and all_scores[j] == all_scores[i]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[all_scores[k]] = avg_rank
        i = j
    rank_sum_pos = sum(ranks[s] for s in pos)
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def brier(pairs):
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


rows_by_stat = defaultdict(list)
with open(ROWS_FILE) as f:
    for line in f:
        row = json.loads(line)
        if row.get("prop_type") not in STATS:
            continue
        if row.get("outcome") is None or row.get("predicted_prob") is None:
            continue
        row["_dt"] = parse_date(row["date"])
        rows_by_stat[row["prop_type"]].append(row)

all_rows = []
for stat, rows in rows_by_stat.items():
    rows.sort(key=lambda r: r["_dt"])
    by_needs_date = defaultdict(list)
    for r in rows:
        by_needs_date[r["needs"]].append((r["_dt"], r["outcome"]))
    for k in by_needs_date:
        by_needs_date[k].sort()

    def trailing_rate(needs, dt, _bnd=by_needs_date):
        obs = _bnd.get(needs, [])
        lo = dt - timedelta(days=TRAILING_DAYS)
        window = [o for d, o in obs if lo <= d < dt]
        return (sum(window) / len(window)) if len(window) >= 30 else None

    def season_to_date_rate(needs, dt, _bnd=by_needs_date):
        obs = _bnd.get(needs, [])
        start = season_start(dt)
        if dt < start:
            start = season_start(dt - timedelta(days=200))
        window = [o for d, o in obs if start <= d < dt]
        return (sum(window) / len(window)) if len(window) >= 30 else None

    def prior_season_full_rate(needs, dt, _bnd=by_needs_date):
        obs = _bnd.get(needs, [])
        this_start = season_start(dt)
        prior_start = season_start(dt - timedelta(days=200))
        window = [o for d, o in obs if prior_start <= d < this_start]
        return (sum(window) / len(window)) if len(window) >= 100 else None

    def multiseason_blend(needs, dt):
        std = season_to_date_rate(needs, dt)
        prior = prior_season_full_rate(needs, dt)
        days_in = max(0, (dt - season_start(dt)).days)
        w_current = min(1.0, days_in / 60.0)
        if std is None and prior is None:
            return None
        if prior is None:
            return std
        if std is None:
            return prior
        return w_current * std + (1 - w_current) * prior

    slate_rate = {}
    by_date_needs = defaultdict(list)
    for r in rows:
        by_date_needs[(r["date"], r["needs"])].append(r["outcome"])
    for key, outcomes in by_date_needs.items():
        slate_rate[key] = sum(outcomes) / len(outcomes) if outcomes else None

    for r in rows:
        r["_base_A"] = slate_rate.get((r["date"], r["needs"]))
        r["_base_B"] = trailing_rate(r["needs"], r["_dt"])
        r["_base_C"] = season_to_date_rate(r["needs"], r["_dt"])
        r["_base_D"] = multiseason_blend(r["needs"], r["_dt"])
        r["_phase"] = season_phase(r["_dt"])
        r["_year"] = r["_dt"].year
        all_rows.append(r)

BASELINES = {"A_slate_scoped": "_base_A", "B_trailing60": "_base_B",
             "C_season_to_date": "_base_C", "D_multiseason": "_base_D"}

print("=" * 100)
print("PART 1: LEAN-GATE REPLAY (has_real_lean = lift is not None and lift >= 0.02)")
print("This is the ONLY place `lift` gates a real recommendation today.")
print("=" * 100)

for stat in STATS:
    stat_rows = [r for r in all_rows if r["prop_type"] == stat]
    print(f"\n--- {stat} (n={len(stat_rows)} rows) ---")
    for label, field in BASELINES.items():
        lean_hits, lean_n, notlean_hits, notlean_n = 0, 0, 0, 0
        lift_auc_pairs = []
        for r in stat_rows:
            b = r[field]
            if b is None:
                continue
            lift = r["predicted_prob"] - b
            lift_auc_pairs.append((lift, r["outcome"]))
            if lift >= LEAN_MIN_LIFT:
                lean_n += 1
                lean_hits += r["outcome"]
            else:
                notlean_n += 1
                notlean_hits += r["outcome"]
        if lean_n == 0:
            print(f"  {label:20} no rows ever cleared the Lean lift gate -- skipped")
            continue
        lean_hr = lean_hits / lean_n
        notlean_hr = notlean_hits / notlean_n if notlean_n else None
        la = auc(lift_auc_pairs)
        print(f"  {label:20} lean_eligible n={lean_n:6} hit_rate={lean_hr:.4f}   "
              f"NOT_lean_eligible n={notlean_n:6} hit_rate={notlean_hr:.4f}   "
              f"separation={lean_hr - notlean_hr:+.4f}   lift_AUC={la:.4f}")

print()
print("=" * 100)
print("PART 1b: CHRONOLOGICAL STABILITY of the Lean-gate separation, by year and season phase")
print("(does the ranking above hold up over time, or is it a pooled-only artifact?)")
print("=" * 100)
for stat in STATS:
    stat_rows = [r for r in all_rows if r["prop_type"] == stat]
    years = sorted(set(r["_year"] for r in stat_rows))
    phases = ["early(<30d)", "mid(30-90d)", "late(90d+)"]
    print(f"\n--- {stat} ---")
    for label, field in BASELINES.items():
        print(f"  {label}:")
        for y in years:
            for ph in phases:
                sub = [r for r in stat_rows if r["_year"] == y and r["_phase"] == ph and r[field] is not None]
                if len(sub) < 100:
                    continue
                lean = [r for r in sub if (r["predicted_prob"] - r[field]) >= LEAN_MIN_LIFT]
                notlean = [r for r in sub if (r["predicted_prob"] - r[field]) < LEAN_MIN_LIFT]
                if not lean or not notlean:
                    continue
                lhr = sum(r["outcome"] for r in lean) / len(lean)
                nhr = sum(r["outcome"] for r in notlean) / len(notlean)
                print(f"    {y} {ph:14} n={len(sub):5}  lean_n={len(lean):5} hr={lhr:.4f}  "
                      f"notlean_n={len(notlean):5} hr={nhr:.4f}  sep={lhr-nhr:+.4f}")

print()
print("=" * 100)
print("PART 2: TOP-PICK PROXY EXPERIMENT (RESEARCH ONLY -- current policy is UNCHANGED,")
print("lift does not gate Top Pick admission today and this script does not alter that).")
print("Proxy population = predicted_prob >= 0.60 (TOP_PICK_MIN_PROB), the one gate this")
print("data CAN reproduce without real historical market odds (evidence_ok/lineup_ok/")
print("clears_value all require data this backtest does not capture -- disclosed, not")
print("fabricated). Backfill pool = 0.50 <= predicted_prob < 0.60 near-misses, ranked by")
print("prob descending -- the most defensible available proxy for 'next eligible")
print("candidate' given no true nightly full-candidate ranking exists in this dataset.")
print("=" * 100)

for stat in STATS:
    stat_rows = [r for r in all_rows if r["prop_type"] == stat]
    by_date = defaultdict(list)
    for r in stat_rows:
        by_date[r["date"]].append(r)
    print(f"\n--- {stat} ---")
    for label, field in BASELINES.items():
        orig_hits, orig_n = 0, 0
        hard_hits, hard_n = 0, 0
        eqvol_hits, eqvol_n = 0, 0
        removed_total, backfilled_total, backfill_shortfall = 0, 0, 0
        for date, day_rows in by_date.items():
            top = [r for r in day_rows if r["predicted_prob"] >= TOP_PICK_MIN_PROB]
            nearmiss = sorted(
                [r for r in day_rows if NEARMISS_LO <= r["predicted_prob"] < TOP_PICK_MIN_PROB],
                key=lambda r: -r["predicted_prob"])
            top_scored = [(r, r[field]) for r in top if r[field] is not None]
            if not top_scored:
                continue
            orig_n += len(top_scored)
            orig_hits += sum(r["outcome"] for r, _ in top_scored)
            survivors = [r for r, b in top_scored if (r["predicted_prob"] - b) > 0]
            hard_n += len(survivors)
            hard_hits += sum(r["outcome"] for r in survivors)
            removed = len(top_scored) - len(survivors)
            removed_total += removed
            backfill = nearmiss[:removed]
            backfilled_total += len(backfill)
            backfill_shortfall += max(0, removed - len(backfill))
            eqvol_set = survivors + backfill
            eqvol_n += len(eqvol_set)
            eqvol_hits += sum(r["outcome"] for r in eqvol_set)
        if orig_n == 0:
            print(f"  {label:20} no rows -- skipped")
            continue
        orig_hr = orig_hits / orig_n
        hard_hr = hard_hits / hard_n if hard_n else None
        eqvol_hr = eqvol_hits / eqvol_n if eqvol_n else None
        pct_removed = 100 * removed_total / orig_n
        print(f"  {label:20} ORIGINAL(no gate)     n={orig_n:5} hit_rate={orig_hr:.4f}")
        print(f"  {'':20} HARD GATE (lift>0)    n={hard_n:5} hit_rate={hard_hr if hard_hr is None else round(hard_hr,4)}  "
              f"({pct_removed:.1f}% of picks removed, {removed_total} total)")
        print(f"  {'':20} EQUAL-VOLUME(backfill) n={eqvol_n:5} hit_rate={eqvol_hr if eqvol_hr is None else round(eqvol_hr,4)}  "
              f"(backfilled {backfilled_total}, shortfall {backfill_shortfall} nights had too few near-misses)")

print()
print("=" * 100)
print("PART 3: BASELINE-ITSELF SECONDARY METRICS (Brier of the baseline as a naive")
print("predictor, pooled -- sanity check only, NOT the primary metric)")
print("=" * 100)
for stat in STATS:
    stat_rows = [r for r in all_rows if r["prop_type"] == stat]
    print(f"\n--- {stat} ---")
    for label, field in BASELINES.items():
        pairs = [(r[field], r["outcome"]) for r in stat_rows if r[field] is not None]
        if len(pairs) < 50:
            continue
        print(f"  {label:20} n={len(pairs):6}  brier={brier(pairs):.5f}")
