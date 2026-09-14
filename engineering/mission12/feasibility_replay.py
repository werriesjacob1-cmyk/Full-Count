"""Replay protocol section 7's designation rule over REAL committed history.

Answers one question with evidence rather than argument: at production's own
observed rate of exposing Hits Top Picks, how long does the section 13
promotion floor (30 primary dates AND 100 decided per arm) take to reach?

It reads every committed revision of docs/data.json -- every deployment, not
one per date -- because designation considers only epochs that actually SEALED.
A late refresh that carries already-exposed picks as live/final fails closed
and is simply not a designation candidate; the earlier all-pregame epoch is.
Sampling one commit per date gets this backwards and reports most dates dead.

UPPER BOUND, deliberately. A date counts here if some deployment exposed Hits
Top Picks that were all still pregame. The real N is <= this: the epoch must
also bind a hash-matched snapshot, survive the re-gate, and match volume.
"""

import collections
import json
import subprocess
import sys

MIN_PRIMARY_DATES = 30
MIN_DECIDED_PER_ARM = 100


def revisions(path="docs/data.json"):
    out = subprocess.run(["git", "log", "--format=%H", "--", path],
                         capture_output=True, text=True).stdout.split()
    for h in out:
        blob = subprocess.run(["git", "show", f"{h}:{path}"],
                              capture_output=True, text=True).stdout
        try:
            yield json.loads(blob)
        except Exception:
            continue


def main():
    per_date = collections.defaultdict(list)
    for payload in revisions():
        date = payload.get("date")
        if not date:
            continue
        tp = [p for p in (payload.get("props") or [])
              if ((p.get("projection") or {}).get("stat") or p.get("stat")) == "hits"
              and p.get("recommendation_status") == "top_pick"]
        states = collections.Counter(p.get("game_state") for p in tp)
        per_date[date].append((len(tp), states))

    rows, primary, zero, dead, total_n = [], 0, 0, 0, 0
    for date in sorted(per_date):
        deps = per_date[date]
        qualifying = [n for n, st in deps if n > 0 and not (set(st) - {"pregame", None})]
        best = max(qualifying, default=0)
        any_tp = max((n for n, _ in deps), default=0)
        if qualifying:
            verdict, primary, total_n = "PRIMARY EPOCH", primary + 1, total_n + best
        elif any_tp == 0:
            verdict, zero = "N=0 (no Hits Top Pick exposed)", zero + 1
        else:
            verdict, dead = "NO PRIMARY EPOCH", dead + 1
        rows.append({"slate_date": date, "deployments": len(deps),
                     "max_hits_top_picks": any_tp,
                     "qualifying_deployments": len(qualifying),
                     "n_upper_bound": best, "verdict": verdict})
        print(f"{date}  deployments={len(deps):3d}  maxTP={any_tp}  "
              f"qualifying={len(qualifying):3d}  N<={best}  {verdict}")

    days = len(per_date)
    print(f"\ncalendar days observed:        {days}")
    print(f"dates with a primary epoch:    {primary}")
    print(f"dates with N=0:                {zero}")
    print(f"dates with no primary epoch:   {dead}")
    print(f"champion selections (upper):   {total_n}")

    dates_per_day = primary / days if days else 0
    picks_per_day = total_n / days if days else 0
    print(f"\nrate: {dates_per_day:.3f} primary dates/day, "
          f"{picks_per_day:.3f} selections/day")
    if dates_per_day and picks_per_day:
        by_dates = MIN_PRIMARY_DATES / dates_per_day
        by_volume = MIN_DECIDED_PER_ARM / picks_per_day
        print(f"days to {MIN_PRIMARY_DATES} primary dates:      {by_dates:.0f}")
        print(f"days to {MIN_DECIDED_PER_ARM} decided per arm:   {by_volume:.0f} "
              f"(ignores voids/ungraded, so optimistic)")
        print(f"BINDING CONSTRAINT: {max(by_dates, by_volume):.0f} days")
    json.dump({"rows": rows, "days": days, "primary_dates": primary,
               "n_upper_bound_total": total_n},
              open("engineering/mission12/feasibility_result.json", "w"),
              indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
