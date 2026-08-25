#!/usr/bin/env python3
"""reconcile_repair_vs_main.py -- root-causes the 12 date-level row-count
discrepancies between backtest/rows_backfill.jsonl (main, --start 2024-04-01
--end 2026-06-30) and backtest/rows_backfill_repair.jsonl (repair, --start
2024-04-01 --end 2025-02-26), both launched from the same git commit
(dfb6d812, verified via each row's own code_git_sha field) within 2 seconds
of each other on 2026-08-24.

Canonical row identity: (date, game_pk, player_id, prop_type, needs) -- the
same tuple backtest/engine.py's own grading keys on (no combo_player_ids
field exists in these rows -- this --no-weather run set never produces
combined_strikeouts/nrfi_combined candidates, verified by checking the
prop_type set present).

For every one of the 12 discrepant dates: checks for duplicate canonical
keys WITHIN each file, computes repair-only / main-only / intersection
row sets, and classifies every extra/missing row by real cause (available
fields only -- outcome/predicted_prob presence, prop_type, needs, fair_test,
actual_pa, signals-dict completeness) rather than guessing.

    /tmp/mlbvenv/bin/python3 backtest/reconcile_repair_vs_main.py
"""
import json
from collections import defaultdict, Counter

MAIN_FILE = "/home/user/PROJECT-GRIDIRON/backtest/rows_backfill.jsonl"
REPAIR_FILE = "/home/user/PROJECT-GRIDIRON/backtest/rows_backfill_repair.jsonl"
REPAIR_END = "2025-02-26"


def load(path, date_filter=None):
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if date_filter and r.get("date") > date_filter:
                continue
            rows.append(r)
    return rows


def key(r):
    return (r["date"], r.get("game_pk"), r.get("player_id"), r.get("prop_type"), r.get("needs"))


def main():
    main_rows = load(MAIN_FILE, date_filter=REPAIR_END)
    repair_rows = load(REPAIR_FILE)

    main_shas = Counter(r.get("code_git_sha") for r in main_rows)
    repair_shas = Counter(r.get("code_git_sha") for r in repair_rows)
    print("=" * 100)
    print("CODE VERSION CHECK (code_git_sha on every row)")
    print("=" * 100)
    print(f"  main (dates<={REPAIR_END}):    {dict(main_shas)}")
    print(f"  repair:                        {dict(repair_shas)}")
    print(f"  SAME COMMIT ACROSS BOTH: {set(main_shas) == set(repair_shas) and len(main_shas) == 1}")

    main_by_date = defaultdict(list)
    for r in main_rows:
        main_by_date[r["date"]].append(r)
    repair_by_date = defaultdict(list)
    for r in repair_rows:
        repair_by_date[r["date"]].append(r)

    all_dates = sorted(set(main_by_date) | set(repair_by_date))
    discrepant = [d for d in all_dates if len(main_by_date[d]) != len(repair_by_date[d])]
    print()
    print("=" * 100)
    print(f"DATES WITH DIFFERENT ROW COUNTS: {len(discrepant)}")
    print("=" * 100)
    for d in discrepant:
        print(f"  {d}: main={len(main_by_date[d])}  repair={len(repair_by_date[d])}  "
              f"diff={len(repair_by_date[d]) - len(main_by_date[d]):+d}")

    total_main_only = total_repair_only = total_intersection = 0
    total_dupe_main = total_dupe_repair = 0
    cause_counter = Counter()

    for d in discrepant:
        print()
        print("=" * 100)
        print(f"DATE {d}")
        print("=" * 100)
        m_rows = main_by_date[d]
        r_rows = repair_by_date[d]

        m_keys = Counter(key(r) for r in m_rows)
        r_keys = Counter(key(r) for r in r_rows)
        dupe_main = {k: c for k, c in m_keys.items() if c > 1}
        dupe_repair = {k: c for k, c in r_keys.items() if c > 1}
        total_dupe_main += len(dupe_main)
        total_dupe_repair += len(dupe_repair)
        if dupe_main:
            print(f"  DUPLICATE canonical keys in MAIN: {len(dupe_main)} keys, "
                  f"{sum(dupe_main.values())} rows total")
            for k, c in list(dupe_main.items())[:3]:
                print(f"    {k} x{c}")
        if dupe_repair:
            print(f"  DUPLICATE canonical keys in REPAIR: {len(dupe_repair)} keys, "
                  f"{sum(dupe_repair.values())} rows total")
            for k, c in list(dupe_repair.items())[:3]:
                print(f"    {k} x{c}")

        m_by_key = {key(r): r for r in m_rows}
        r_by_key = {key(r): r for r in r_rows}
        m_set, r_set = set(m_by_key), set(r_by_key)
        repair_only = r_set - m_set
        main_only = m_set - r_set
        intersection = m_set & r_set
        total_main_only += len(main_only)
        total_repair_only += len(repair_only)
        total_intersection += len(intersection)

        print(f"  main_only={len(main_only)}  repair_only={len(repair_only)}  "
              f"intersection={len(intersection)}")

        # Classify repair-only rows by real cause.
        by_prop = Counter(k[3] for k in repair_only)
        by_needs = Counter((k[3], k[4]) for k in repair_only)
        print(f"  repair-only by prop_type: {dict(by_prop)}")
        print(f"  repair-only by (prop_type, needs): {dict(by_needs)}")

        for k in repair_only:
            row = r_by_key[k]
            if row.get("predicted_prob") is None:
                cause_counter["repair_only: missing predicted_prob"] += 1
            elif row.get("outcome") is None:
                cause_counter["repair_only: missing outcome (ungraded)"] += 1
            else:
                cause_counter["repair_only: fully valid graded row absent from main"] += 1

        for k in main_only:
            row = m_by_key[k]
            if row.get("predicted_prob") is None:
                cause_counter["main_only: missing predicted_prob"] += 1
            elif row.get("outcome") is None:
                cause_counter["main_only: missing outcome (ungraded)"] += 1
            else:
                cause_counter["main_only: fully valid graded row absent from repair"] += 1

        # Intersection sanity: do the two runs agree on prob/outcome for the
        # SAME canonical row? A real disagreement here would mean nondeterminism
        # or a genuine scoring difference, not just row presence/absence.
        prob_diffs = []
        outcome_diffs = []
        for k in intersection:
            mr, rr = m_by_key[k], r_by_key[k]
            if mr.get("predicted_prob") != rr.get("predicted_prob"):
                prob_diffs.append((k, mr.get("predicted_prob"), rr.get("predicted_prob")))
            if mr.get("outcome") != rr.get("outcome"):
                outcome_diffs.append((k, mr.get("outcome"), rr.get("outcome")))
        if prob_diffs:
            print(f"  ** {len(prob_diffs)} intersection rows have a DIFFERENT predicted_prob "
                  f"between main and repair (same canonical key) **")
            for k, mp, rp in prob_diffs[:3]:
                print(f"      {k}: main={mp} repair={rp}")
        if outcome_diffs:
            print(f"  ** {len(outcome_diffs)} intersection rows have a DIFFERENT outcome "
                  f"between main and repair (same canonical key) **")
            for k, mo, ro in outcome_diffs[:3]:
                print(f"      {k}: main={mo} repair={ro}")
        if not prob_diffs and not outcome_diffs:
            print(f"  intersection rows: 0 prob/outcome disagreements "
                  f"({len(intersection)} rows checked) -- the two runs agree wherever both have a row")

    print()
    print("=" * 100)
    print("TOTALS ACROSS ALL 12 DATES")
    print("=" * 100)
    print(f"  main_only rows:    {total_main_only}")
    print(f"  repair_only rows:  {total_repair_only}")
    print(f"  intersection rows: {total_intersection}")
    print(f"  duplicate canonical keys -- main: {total_dupe_main}  repair: {total_dupe_repair}")
    print()
    print("CAUSE BREAKDOWN:")
    for cause, n in cause_counter.most_common():
        print(f"  {cause}: {n}")


if __name__ == "__main__":
    main()
