#!/usr/bin/env python3
"""Join the frozen week-3 forward shadow to REAL captured FanDuel receptions
offers. Descriptive and exploratory only: this rule is written and committed
before it is run, but after week-3 outcomes exist, so it is NOT
pre-registered and carries no confirmatory weight.

Rules (fixed in this commit, before the first run):
- Offers: the `snapshot.records` of the archived `NFL Live Receptions Shadow
  Board Audit` workflow artifacts in wk3_real_offers/ (hashes in SHA256SUMS),
  market == "receptions", shape == "primary", non-null line and both odds.
- Pregame only: FULL COUNT's own `captured_at` must be strictly before the
  event's `event_open_date` (kickoff). This is our observation time; the book
  exposes no quote-origin time, so it proves only that we saw the price
  before kickoff.
- One offer per (gsis_id, event_id): the latest pregame capture.
- Join to frozen candidates by player_id == gsis_id and team == team.
  Only rows graded by grade_forward_shadow.py (a real week-3 stats row)
  count; frozen VOIDs stay VOID. No line is invented or interpolated.
- Side per model: OVER if projection > line, UNDER if projection < line.
  One unit at the captured American odds of that side. Pushes cannot occur
  on half-point lines.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFERS = HERE / "wk3_real_offers"
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))


def _check_hashes():
    for line in (OFFERS / "SHA256SUMS").read_text().splitlines():
        want, name = line.split()
        if hashlib.sha256((OFFERS / name).read_bytes()).hexdigest() != want:
            raise SystemExit(f"offer archive hash mismatch: {name}")


def pregame_offers():
    best = {}
    for path in sorted(OFFERS.glob("run_*.json")):
        for r in json.loads(path.read_text())["snapshot"]["records"]:
            if r.get("market") != "receptions" or r.get("shape") != "primary":
                continue
            if r.get("line") is None or r.get("over_odds") is None or r.get("under_odds") is None:
                continue
            if not r["captured_at"] < r["event_open_date"].replace(".000Z", "Z"):
                continue
            key = (r["gsis_id"], r["event_id"])
            if key not in best or r["captured_at"] > best[key]["captured_at"]:
                best[key] = {**r, "artifact": path.name}
    return list(best.values())


def profit(odds, won):
    if not won:
        return -1.0
    return odds / 100.0 if odds > 0 else 100.0 / (-odds)


def main():
    _check_hashes()
    grade = json.loads((HERE / "forward_shadow_2026_wk3_grade.json").read_text())
    artifact = json.loads((HERE / "forward_shadow_2026_wk3.json").read_text())
    void_ids = {v["player_id"] for v in grade["void_players"]}
    from data_cache import load_player_weeks
    live = load_player_weeks(artifact["target"]["season"])
    if live["rows_sha256"] != grade["outcome_source"]["rows_sha256"]:
        raise SystemExit("outcome rows differ from the graded snapshot")
    week = artifact["target"]["week"]
    realized = {(r["player_id"], r["team"]): r["receptions"] for r in live["rows"] if r["week"] == week}
    cands = {(c["player_id"], c["team"]): c for c in artifact["candidates"]}
    offers = pregame_offers()
    joined, unjoined = [], []
    for o in offers:
        c = cands.get((o["gsis_id"], o["team"]))
        if c is None:
            unjoined.append({"gsis_id": o["gsis_id"], "player_name": o["player_name"], "reason": "not a frozen candidate"})
            continue
        if c["player_id"] in void_ids or (c["player_id"], c["team"]) not in realized:
            unjoined.append({"gsis_id": o["gsis_id"], "player_name": o["player_name"], "reason": "VOID"})
            continue
        y = realized[(c["player_id"], c["team"])]
        row = {"gsis_id": o["gsis_id"], "player_name": o["player_name"], "game_id": c["game_id"],
               "captured_at": o["captured_at"], "kickoff": o["event_open_date"], "artifact": o["artifact"],
               "observation_id": o["observation_id"], "line": o["line"], "over_odds": o["over_odds"],
               "under_odds": o["under_odds"], "realized_receptions": y}
        for m in ("c1", "b0"):
            proj = c[f"{m}_projection"]
            side = "OVER" if proj > o["line"] else "UNDER" if proj < o["line"] else None
            won = None if side is None else (y > o["line"]) == (side == "OVER")
            odds = o["over_odds"] if side == "OVER" else o["under_odds"]
            row[m] = {"projection": proj, "side": side, "won": won,
                      "units": None if side is None else profit(odds, won)}
        joined.append(row)
    summary = {}
    for m in ("c1", "b0"):
        bets = [r[m] for r in joined if r[m]["side"]]
        summary[m] = {"n": len(bets), "wins": sum(b["won"] for b in bets),
                      "units": sum(b["units"] for b in bets),
                      "roi": (sum(b["units"] for b in bets) / len(bets)) if bets else None}
    disagree = [r for r in joined if r["c1"]["side"] != r["b0"]["side"]]
    report = {"evidence_class": "EXPLORATORY_DESCRIPTIVE_REAL_LINE_JOIN_NOT_PREREGISTERED",
              "offer_archive": (OFFERS / "SHA256SUMS").read_text().split("\n")[:-1],
              "n_pregame_offers": len(offers), "n_joined_graded": len(joined), "unjoined": unjoined,
              "summary": summary,
              "disagreements": {"n": len(disagree),
                                "c1_wins": sum(bool(r["c1"]["won"]) for r in disagree),
                                "b0_wins": sum(bool(r["b0"]["won"]) for r in disagree)},
              "rows": joined}
    (HERE / "forward_shadow_2026_wk3_real_lines.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("rows", "offer_archive")}, indent=2))


if __name__ == "__main__":
    main()
