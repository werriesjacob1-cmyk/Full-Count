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

PHASE 4 REBUILD (2026-08-16): reads/writes payload["props"] (the one flat
array, keyed by each row's stable `id`) instead of the old per-tab
(name, prop) propagation -- there is exactly one copy of each row now, so
grading it once is grading it everywhere. Also writes the change into
live.json (merged by id, see refresh_prices.py's _load_live/_write_live)
so an already-open tab picks up the color change via app.js's pollLive()
without waiting for the next full rebuild or a page reload.

Only ever touches picks currently recommendation_status=="top_pick".
Direct request: "as games start I want those props removed" still applies
to every other prop -- this script leaves them alone. Top Picks is the
one place a pick needs to survive its own game, so it can show the color
change as it resolves.

    python3 dashboard/refresh_grades.py [--data docs/data.json] [--live docs/live.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _load_live(live_path):
    try:
        with open(live_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"prices_updated_at": None, "grades_updated_at": None, "props": {}}


def _write_live(live_path, live):
    with open(live_path, "w", encoding="utf-8") as f:
        json.dump(live, f, separators=(",", ":"))


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


def refresh(data_path, live_path=None):
    live_path = live_path or os.path.join(os.path.dirname(os.path.abspath(data_path)), "live.json")

    with open(data_path, encoding="utf-8") as f:
        payload = json.load(f)

    props = payload.get("props") or []
    top_picks = [r for r in props if r.get("recommendation_status") == "top_pick"]
    if not top_picks:
        print(f"{data_path}: no top picks to grade -- nothing to do.")
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

    changed = {}
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
                changed[row["id"]] = "live"
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
        changed[row["id"]] = row["grade"]

    print(f"Graded {n_graded} pick(s) final, marked {n_live} live, "
          f"out of {len(ungraded)} still-open top pick(s).")

    grades_updated_at = datetime.now(timezone.utc).isoformat()
    payload["grades_updated_at"] = grades_updated_at

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {data_path}.")

    if changed:
        live = _load_live(live_path)
        live["grades_updated_at"] = grades_updated_at
        live_props = live.setdefault("props", {})
        for pid, grade in changed.items():
            live_props.setdefault(pid, {}).update({"grade": grade})
        _write_live(live_path, live)
        print(f"Wrote {live_path} ({len(changed)} prop(s) changed)")

    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"),
                    help="path to the payload build_dashboard.py's --data-out wrote")
    ap.add_argument("--live", default=None,
                    help="path to the small delta file app.js's pollLive() fetches "
                         "(default: live.json next to --data)")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        print(f"{args.data} doesn't exist yet -- nothing to grade until a full build runs first.")
        return 0

    refresh(args.data, live_path=args.live)
    return 0


if __name__ == "__main__":
    sys.exit(main())
