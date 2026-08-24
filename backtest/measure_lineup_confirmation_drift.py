#!/usr/bin/env python3
"""measure_lineup_confirmation_drift.py -- 2026-08-24/25 accuracy investigation,
lineup-timing follow-up.

THE QUESTION: how much does a candidate's predicted probability actually
change between an early snapshot (when its lineup slot is still a
projection) and a later one from the SAME real day (once the real lineup
has posted), for the SAME real player/prop? And where a real graded
outcome exists, does the early or late read end up closer to right?

DATA SOURCE: docs/data.json, the live dashboard's full prop-universe
payload (1700+ props, not just the top-10 board) -- committed to git on
every "Dashboard refresh"/"Dashboard live update" run, multiple times an
hour on a real game day. This is a MUCH denser, richer time series than
output/picks_{date}_{timestamp}.json (5x/day, top-10-board-only, whose
picks aren't even a stable set of players run to run). Reading historical
commits via `git show <sha>:docs/data.json` costs nothing -- no network
calls, no re-scoring, just real numbers this project already produced and
committed.

TIER SEPARATION -- NOT POSSIBLE, stated honestly rather than faked.
generate_picks.py's quality_control() collapses the real 4-way lineup
source (official / MLB.com fallback / Rotowire same-day projection /
last-known-lineup carryover) into a single boolean lineup_assumed before
it ever reaches this payload (confirmed directly this same investigation,
see the accompanying report) -- so this script can only ever measure
"assumed vs not assumed" as one pool, never which assumed tier a given
early read came from. That real limitation is the reason
backtest/measure_last_known_lineup_accuracy.py exists as a SEPARATE,
last-known-tier-specific measurement instead.

    /tmp/mlbvenv/bin/python3 backtest/measure_lineup_confirmation_drift.py
"""
import json
import os
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every day from the current (2026-08-15+) architecture with real
# docs/data.json git history -- broadened from an initial 9-day spot
# check specifically because that first pass showed ALL of the
# early_assumed=True graded matches concentrated on a single day
# (2026-08-19), which is too easy to confound with "was that day's slate
# just unusually bad" -- more days lets the transitions/hit-rate table
# below actually separate a real lineup-state effect from a single day's
# variance.
SAMPLE_DATES = [
    "2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19",
    "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24",
]


def git_log_shas_for_day(date):
    """Every docs/data.json commit SHA touching this real calendar date,
    oldest first."""
    out = subprocess.run(
        ["git", "log", "--reverse", "--pretty=format:%H",
         "--since", f"{date} 00:00", "--until", f"{date} 23:59:59",
         "--", "docs/data.json"],
        cwd=ROOT, check=False, text=True, capture_output=True).stdout
    return [l for l in out.splitlines() if l.strip()]


def load_data_json_at(sha):
    out = subprocess.run(["git", "show", f"{sha}:docs/data.json"],
                         cwd=ROOT, check=False, text=True, capture_output=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def key_of(p):
    proj = p.get("projection") or {}
    return (p.get("player_id"), p.get("stat") or proj.get("stat"), proj.get("needs"))


def load_grades(date):
    path = os.path.join(ROOT, "results", f"grades_{date}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        d = json.load(f)
    out = {}
    for p in d.get("picks", []):
        proj = p.get("projection") or {}
        k = (p.get("player_id"), proj.get("stat"), proj.get("needs"))
        out[k] = p.get("grade")
    return out


def main():
    transitions = defaultdict(int)
    prob_deltas = []  # (delta, early_assumed, late_assumed) for keys present both times
    became_confirmed_deltas = []  # specifically assumed->confirmed transitions
    graded_matches = []  # (date, name, stat, early_assumed, late_assumed, early_prob, late_prob, grade)

    for date in SAMPLE_DATES:
        shas = git_log_shas_for_day(date)
        if len(shas) < 2:
            print(f"{date}: only {len(shas)} docs/data.json commit(s) this day -- skipping "
                  f"(need at least an early and a late snapshot)")
            continue
        early = load_data_json_at(shas[0])
        late = load_data_json_at(shas[-1])
        if not early or not late:
            print(f"{date}: could not load early/late snapshot -- skipping")
            continue
        early_by_key = {key_of(p): p for p in early.get("props", [])}
        late_by_key = {key_of(p): p for p in late.get("props", [])}
        grades = load_grades(date)

        n_common = 0
        for k, ep in early_by_key.items():
            lp = late_by_key.get(k)
            if lp is None:
                continue
            n_common += 1
            ea = bool(ep.get("lineup_assumed"))
            la = bool(lp.get("lineup_assumed"))
            transitions[(ea, la)] += 1
            eprob, lprob = ep.get("hit_probability"), lp.get("hit_probability")
            if eprob is not None and lprob is not None:
                delta = lprob - eprob
                prob_deltas.append((delta, ea, la))
                if ea and not la:
                    became_confirmed_deltas.append(delta)
            grade = grades.get(k)
            if grade is not None:
                graded_matches.append((date, ep.get("name"), ep.get("stat"), ea, la, eprob, lprob, grade))

        print(f"{date}: early={early.get('generated_at')} late={late.get('generated_at')} "
              f"({len(shas)} snapshots, {n_common} common props)")

    print(f"\n{'='*90}\nLINEUP-STATE TRANSITIONS (early snapshot -> late snapshot), pooled across "
          f"{len(SAMPLE_DATES)} sample days\n{'='*90}")
    total = sum(transitions.values())
    for (ea, la), n in sorted(transitions.items(), key=lambda kv: -kv[1]):
        label = {
            (True, False): "assumed -> CONFIRMED (real lineup posted)",
            (True, True): "assumed -> still assumed (never posted / scratched)",
            (False, False): "confirmed -> confirmed (already known both times)",
            (False, True): "confirmed -> assumed (should not really happen -- flag if large)",
        }[(ea, la)]
        print(f"  {label:55s} n={n:5d}  ({n/total:.1%})")

    print(f"\n{'='*90}\nPROBABILITY DRIFT: how much hit_probability actually moved between the "
          f"early and late snapshot, split by whether the lineup state changed\n{'='*90}")
    import statistics
    for label, subset in [
        ("ALL pairs (any lineup-state combo)", [d for d, _, _ in prob_deltas]),
        ("assumed -> CONFIRMED only", became_confirmed_deltas),
        ("stayed confirmed->confirmed (baseline/noise floor)",
         [d for d, ea, la in prob_deltas if not ea and not la]),
        ("stayed assumed->assumed (baseline/noise floor)",
         [d for d, ea, la in prob_deltas if ea and la]),
    ]:
        if not subset:
            print(f"  {label}: no data")
            continue
        mean_abs = statistics.mean(abs(x) for x in subset)
        mean_signed = statistics.mean(subset)
        stdev = statistics.pstdev(subset) if len(subset) > 1 else 0.0
        print(f"  {label} (n={len(subset)}):")
        print(f"    mean |delta| = {mean_abs:.4f}   mean signed delta = {mean_signed:+.4f}   "
              f"stdev = {stdev:.4f}")

    print(f"\n{'='*90}\nREAL GRADED OUTCOMES for props whose lineup state was tracked "
          f"(n={len(graded_matches)} matches)\n{'='*90}")
    if not graded_matches:
        print("  No matches -- the officially-graded set (top-10/best-of-category/moonshot, "
              "~74/day) essentially never overlaps with the full early/late payload set for "
              "props that were still assumed early in the day. This is the real, honest "
              "limitation: hit-rate impact cannot be measured this way with current data "
              "volume -- see the report's recommendation for what would close this gap.")
    else:
        by_transition = defaultdict(list)
        for date, name, stat, ea, la, eprob, lprob, grade in graded_matches:
            by_transition[(ea, la)].append(grade)
        for (ea, la), grades_list in by_transition.items():
            hits = sum(1 for g in grades_list if g == "hit")
            n = len(grades_list)
            print(f"  early_assumed={ea} late_assumed={la}: n={n} hit_rate={hits/n:.1%}" if n
                  else "")

        print("\n  By-day breakdown (checks whether the pooled gap is really one day's slate, "
              "not a real lineup-state effect):")
        by_day_transition = defaultdict(lambda: defaultdict(list))
        for date, name, stat, ea, la, eprob, lprob, grade in graded_matches:
            by_day_transition[date][ea].append(grade)
        for date in sorted(by_day_transition):
            parts = []
            for ea, grades_list in sorted(by_day_transition[date].items()):
                n = len(grades_list)
                hits = sum(1 for g in grades_list if g == "hit")
                if n:
                    parts.append(f"early_assumed={ea}: n={n} hit_rate={hits/n:.0%}")
            print(f"    {date}: " + "  |  ".join(parts))

        print("\n  Individual matches:")
        for date, name, stat, ea, la, eprob, lprob, grade in graded_matches:
            print(f"    {date} {name:20s} {stat:15s} early_assumed={ea} late_assumed={la} "
                  f"prob {eprob}->{lprob}  grade={grade}")


if __name__ == "__main__":
    main()
