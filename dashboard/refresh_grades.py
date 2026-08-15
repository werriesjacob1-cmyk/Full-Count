#!/usr/bin/env python3
"""dashboard/refresh_grades.py — live grading refresh for the Top Picks
board. Direct request, verbatim: "for the top picks, them to show when
it's cashed... make them go green if it hits" -- refined moments later to
"make the pick yellow when the game is happening. And have it turn green
if it cashes, red if it doesn't."

Reuses grade_results.grade_pick() -- the EXACT same function that grades
every pick every morning against real box scores -- called live throughout
the evening instead of waiting until tomorrow. No new grading logic here,
no risk of drifting from the one real settlement source of truth.

Three states written into a pick's "grade" field:
  (absent)  -- game hasn't started yet
  "live"    -- game in progress (or just went final but couldn't be
               graded, e.g. a scratch) -- shown so a pick never looks
               untouched right when a reader is watching it
  "hit"/"miss" -- game final and graded. Terminal: never re-checked once set.

Only ever touches Top Picks. Direct request: "as games start I want those
props removed" still applies to every other tab -- pruneStartedGames()
(client-side) and this script's own untouched general prop tabs both
leave that behavior alone. Top Picks is the one place a pick needs to
survive its own game, so it can show the color change.

    python3 dashboard/refresh_grades.py [--data docs/data.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _row_key(r):
    return (r.get("name"), r.get("prop"))


def _candidate_from_row(row):
    """Reshape a dashboard payload row back into the candidate dict shape
    grade_pick() expects. Only carries the fields grading actually reads --
    see grade_pick()'s own branches (generate_picks.py) for exactly which
    stat needs which field."""
    return {
        "type": row.get("type"), "game_pk": row.get("game_pk"),
        "player_id": row.get("player_id"), "team": row.get("team"),
        "matchup": row.get("matchup"), "side": row.get("side"),
        "lean": row.get("lean"), "projection": row.get("projection"),
        "combo_player_ids": row.get("combo_player_ids"),
    }


def refresh(data_path):
    with open(data_path, encoding="utf-8") as f:
        payload = json.load(f)

    top_picks = payload.get("data", {}).get("top_picks")
    if not top_picks:
        print(f"{data_path}: no top_picks to grade -- nothing to do.")
        return payload

    ungraded = [p for p in top_picks if p.get("grade") not in ("hit", "miss")]
    if not ungraded:
        print("Every current top pick is already terminally graded -- nothing to check.")
        return payload

    import grade_results as gr

    today = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
    statuses = gr.fetch_game_statuses(today)
    if not statuses:
        print(f"No game statuses returned for {today} -- nothing to grade this cycle.")
        return payload

    n_live, n_graded = 0, 0
    for row in ungraded:
        game_pk = row.get("game_pk")
        status = statuses.get(game_pk)
        if not status:
            continue
        abstract = status.get("abstractGameState")
        if abstract == "Preview":
            continue  # not started yet -- leave grade unset
        if abstract != "Final" and not gr.is_final(status):
            if row.get("grade") != "live":
                row["grade"] = "live"
                n_live += 1
            continue
        result = gr.grade_pick(_candidate_from_row(row), statuses, date=today)
        grade = result.get("grade")
        if grade in ("hit", "miss"):
            row["grade"] = grade
            n_graded += 1
        else:
            # Final but ungraded (scratched, box score not posted yet, etc.)
            # -- "live" rather than silence, so it doesn't look untouched.
            row["grade"] = "live"

    print(f"Graded {n_graded} pick(s) final, marked {n_live} live, "
          f"out of {len(ungraded)} still-open top pick(s).")

    # Propagate into every other tab the same pick also appears in --
    # same (name, prop) matching pattern refresh_prices.py already uses for
    # its own price-field propagation, since these are separate dict
    # objects across tabs (a fresh process each run, no in-memory sharing).
    graded_by_key = {_row_key(p): p.get("grade") for p in top_picks if p.get("grade")}
    for tab_name, rows in payload.get("data", {}).items():
        if tab_name == "top_picks" or not isinstance(rows, list):
            continue
        for r in rows:
            grade = graded_by_key.get(_row_key(r))
            if grade:
                r["grade"] = grade

    payload["grades_updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {data_path}.")
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"),
                    help="path to the payload build_dashboard.py's --data-out wrote")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        print(f"{args.data} doesn't exist yet -- nothing to grade until a full build runs first.")
        return 0

    refresh(args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
