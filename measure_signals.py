#!/usr/bin/env python3
"""
measure_signals.py — grades every persisted signal against what actually
happened, so a signal earns its weight instead of being assumed to deserve one.

WHY THIS EXISTS.

Seventeen signals have been wired into scoring on the explicit promise that
they would be measured before being weighted: bvp_ops, platoon_barrel_pct,
platoon_xwoba, park_hand_index, opp_catcher_framing, days_rest,
consecutive_games, hard_hit_105_rate, pull_park_synergy, opp_team_cs_pct,
team_total_move, team_total_open, money_ticket_split, getaway_day,
series_game, ump_k_pct, ump_bb_pct. Every one is recorded through _sig()
without touching the score, precisely so its worth could be established from
outcomes rather than from how sensible it sounds.

That promise had no way to be kept. The measurement path did not exist:

  - backtest/engine.py builds its kwargs without `extras`, so all seventeen
    are absent from every backtested row. Not broken -- correct, since the
    tables behind them are season-to-date aggregates that cannot be rebuilt
    as of a past morning without lookahead, which is the exact thing the
    PointInTime guard exists to prevent.
  - results/grades_*.json holds ten picks a day, the published board. Ten
    rows a day cannot separate seventeen signals from noise.
  - data/players/*.json holds every candidate scored -- roughly 420 a day,
    signals included -- and nothing in this project has ever read it.

So the substrate was being written and thrown away, while the two things
that could have consumed it were structurally unable to. This joins the
third file to real box scores and measures each signal directly.

WHAT IS MEASURED, AND WHY AUC.

For each signal: the area under the ROC curve between its value and whether
the prop actually cashed. AUC answers the only question that matters here --
"if I draw one candidate that hit and one that missed, how often does this
signal rank the hit higher?" -- and it is unmoved by base rate, which matters
because these props range from 8% events to 75% events. 0.500 is a coin
flip. Anything within its own confidence interval of 0.500 has earned no
weight, however good the reasoning behind it was.

Sign is reported as measured, not as intended. A signal built to push one way
that measures the other is a finding, not a bug to be flipped away quietly.

THE HONEST CAVEAT.

This is a forward measurement and it starts from nothing. Signals began
persisting on 2026-08-07; before that the snapshots carry only score and
matchup. A signal needs a few hundred graded candidates before its interval
tightens enough to act on, so this prints n and the interval next to every
number and refuses to rank anything below --min-n.
"""
import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict

PLAYERS_DIR = os.environ.get("PLAYERS_DIR", "data/players")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")


def load_evaluations(since=None, until=None):
    """Every persisted candidate that carries at least one signal.

    Yields flat rows: one per (player, date, prop), with the signal bag and
    everything grade_pick() needs to settle it."""
    rows = []
    for path in sorted(glob.glob(os.path.join(PLAYERS_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        pid, name = hist.get("player_id"), hist.get("name")
        if not pid:
            continue
        for snap in hist.get("snapshots", []):
            date = snap.get("date")
            if not date or (since and date < since) or (until and date > until):
                continue
            for ev in snap.get("evaluations", []):
                # No signals means a snapshot written before signals were
                # persisted. Skipping it is right: it carries no information
                # about any signal, and counting it would only dilute n.
                if not ev.get("signals"):
                    continue
                rows.append({"date": date, "player_id": pid, "name": name, **ev})
    return rows


def grade_rows(rows, verbose=True):
    """Settle each row against its real box score, reusing the production
    grader rather than a second implementation of the same rules.

    A reimplementation here would be a test of the reimplementation. grade_pick
    already knows that a shortened game is not a fair miss, that first-inning
    props settle off the linescore by side, and a dozen other rules that were
    each found the hard way."""
    import grade_results as gr

    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)

    graded = []
    for date in sorted(by_date):
        try:
            statuses = gr.fetch_game_statuses(date)
        except Exception as e:
            if verbose:
                print(f"  {date}: game statuses unavailable ({e}) — skipped")
            continue
        n_ok = 0
        for r in by_date[date]:
            try:
                g = gr.grade_pick(r, statuses, date=date)
            except Exception:
                continue
            if g.get("grade") in ("hit", "miss"):
                graded.append({**r, "won": g["grade"] == "hit"})
                n_ok += 1
        if verbose:
            print(f"  {date}: {n_ok} of {len(by_date[date])} candidates settled")
    return graded


def auc(pairs):
    """Area under the ROC curve, by rank, with ties handled properly.

    Ties are not a detail here. Several of these signals are near-binary --
    getaway_day is 0 or 1 -- and scoring a tie as a win would hand a coin flip
    an AUC far above 0.500 purely from how the values clump. Midrank credits
    a tie as half, which is what it is worth."""
    ones = [v for v, y in pairs if y]
    zeros = [v for v, y in pairs if not y]
    if not ones or not zeros:
        return None
    order = sorted(pairs, key=lambda p: p[0])
    ranks, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
            j += 1
        midrank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = midrank
        i = j + 1
    rank_sum_pos = sum(ranks[k] for k, (_, y) in enumerate(order) if y)
    n1, n0 = len(ones), len(zeros)
    return (rank_sum_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def auc_se(a, n1, n0):
    """Standard error of AUC (Hanley-McNeil).

    Reported alongside every number because the whole point is to tell a
    real effect from a lucky one, and an AUC without its interval cannot do
    that. This estimator is mildly conservative, which is the direction to
    err when the answer decides whether something gets to move money."""
    if a is None or n1 == 0 or n0 == 0:
        return None
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    var = (a * (1 - a) + (n1 - 1) * (q1 - a * a) + (n0 - 1) * (q2 - a * a)) / (n1 * n0)
    return math.sqrt(var) if var > 0 else None


def measure(graded, min_n=100):
    """One row per signal: AUC, its interval, n, and the base rates."""
    by_signal = defaultdict(list)
    for g in graded:
        for name, val in (g.get("signals") or {}).items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                by_signal[name].append((float(val), bool(g["won"])))

    out = []
    for name, pairs in by_signal.items():
        n1 = sum(1 for _, y in pairs if y)
        n0 = len(pairs) - n1
        a = auc(pairs)
        se = auc_se(a, n1, n0)
        # An AUC below .500 is a real finding: the signal separates, but the
        # opposite way to how it was wired. Reported as measured.
        strength = abs(a - 0.5) if a is not None else None
        out.append({
            "signal": name, "n": len(pairs), "n_hit": n1, "n_miss": n0,
            "auc": a, "se": se,
            "lo": (a - 1.96 * se) if (a is not None and se) else None,
            "hi": (a + 1.96 * se) if (a is not None and se) else None,
            "separates": (bool(a is not None and se and
                               (a - 1.96 * se > 0.5 or a + 1.96 * se < 0.5))),
            "enough_data": len(pairs) >= min_n,
            "strength": strength,
        })
    out.sort(key=lambda r: (r["enough_data"], r["strength"] or 0), reverse=True)
    return out


def render(table, min_n):
    L = []
    L.append(f"{'signal':26s}{'n':>7s}{'hits':>7s}{'AUC':>8s}{'95% CI':>17s}  verdict")
    L.append("-" * 84)
    for r in table:
        if r["auc"] is None:
            L.append(f"{r['signal']:26s}{r['n']:7d}{r['n_hit']:7d}{'—':>8s}"
                     f"{'—':>17s}  no variation in outcome")
            continue
        ci = f"[{r['lo']:.3f}, {r['hi']:.3f}]" if r["lo"] is not None else "—"
        if not r["enough_data"]:
            verdict = f"too few ({r['n']} < {min_n})"
        elif not r["separates"]:
            verdict = "no measurable signal"
        elif r["auc"] > 0.5:
            verdict = "SEPARATES — earns weight"
        else:
            verdict = "SEPARATES BACKWARDS — sign is wrong"
        L.append(f"{r['signal']:26s}{r['n']:7d}{r['n_hit']:7d}{r['auc']:8.3f}"
                 f"{ci:>17s}  {verdict}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="earliest snapshot date to include (YYYY-MM-DD)")
    ap.add_argument("--until", help="latest snapshot date to include (YYYY-MM-DD)")
    ap.add_argument("--min-n", type=int, default=100,
                    help="graded candidates a signal needs before it is ranked "
                         "(default %(default)s)")
    args = ap.parse_args()

    rows = load_evaluations(args.since, args.until)
    if not rows:
        print(f"No persisted candidates carrying signals in {PLAYERS_DIR}.")
        print()
        print("This is expected until a pipeline run happens with signal")
        print("persistence in place. Snapshots written before then kept only")
        print("score and matchup, so there is nothing to measure yet — that is")
        print("a starting line, not a failure.")
        return 0

    dates = sorted({r["date"] for r in rows})
    print(f"{len(rows)} persisted candidates with signals across "
          f"{len(dates)} date(s): {dates[0]} to {dates[-1]}\n")

    graded = grade_rows(rows)
    if not graded:
        print("\nNothing settled. Games may not be final yet.")
        return 0

    print(f"\n{len(graded)} candidates settled against real box scores "
          f"({sum(1 for g in graded if g['won'])} hit).\n")
    table = measure(graded, args.min_n)
    print(render(table, args.min_n))

    ranked = [r for r in table if r["enough_data"]]
    if not ranked:
        print(f"\n  NOTHING IS RANKABLE YET. No signal has reached {args.min_n} graded")
        print(f"  candidates. Every AUC above is a real measurement of a real")
        print(f"  sample — it is just too small a sample to act on, and acting")
        print(f"  on it anyway is how a coin flip gets a weight.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, "signal_measurement.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"dates": dates, "n_graded": len(graded),
                   "hit_rate": sum(1 for g in graded if g["won"]) / len(graded),
                   "signals": table}, f, indent=2)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
