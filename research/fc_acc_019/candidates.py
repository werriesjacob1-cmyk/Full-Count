#!/usr/bin/env python3
"""FC-ACC-019 R2 candidate set (PREREG §1-2). Outcome-free.

Per regular-season game: the LATEST pregame production dashboard state that contains that game's
rows (last `docs/data.json` commit <= first_pitch - 10 min, look-back <= 8 h, payload date == slate).
Gates: priced, Over, lineup confirmed, payload odds_fetched_at <= 45 min before that commit, not
live/final, gradable market. No price cap. Each row carries commit SHA/time and its no-vig basis.

Usage: python3 candidates.py <partial clone> <raw StatsAPI dir> <slates json>
"""
from __future__ import annotations

import bisect
import json
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "fc_acc_018"))
import universe as U18  # noqa: E402  (FC-ACC-018 helpers, read-only)

MARKETS = ("hits", "hits_runs_rbis", "total_bases", "singles", "doubles", "triples", "home_runs", "runs", "rbis",
           "stolen_base", "strikeouts", "pitcher_outs")
LOOKBACK, MARGIN, MAX_AGE = timedelta(hours=8), timedelta(minutes=10), timedelta(minutes=45)
HOLD = 1.08
LAST = "2026-09-27"


def raw_implied(a):
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def no_vig(row):
    """PREREG §2: recorded fair value when it is not the raw price, else raw/1.08 (approximate)."""
    raw = raw_implied(row["market_odds"])
    m = row.get("market_implied")
    if row.get("market_fair_method") == "exact_two_sided" or (m and abs(raw / m - 1) > 0.002):
        return (m if m else raw / HOLD), ("exact_or_recorded" if m else "assumed_hold")
    return raw / HOLD, "assumed_hold"


def main(clone, raw_dir, slates_path):
    slates = [d for d in json.loads(Path(slates_path).read_text()) if d <= LAST]
    ctimes, chashes = U18.commits(clone)
    sched = U18.schedule(raw_dir)
    cache, out, diag = {}, [], {}

    def payload(h):
        if h not in cache:
            txt = subprocess.run(["git", "-C", clone, "show", f"{h}:docs/data.json"], capture_output=True, text=True).stdout
            try:
                cache[h] = json.loads(txt)
            except ValueError:
                cache[h] = {}
        return cache[h]
    for d in slates:
        dg = Counter()
        for gpk, start in sched.get(d, []):
            i = bisect.bisect_right(ctimes, start - MARGIN) - 1
            found = None
            while i >= 0 and ctimes[i] >= start - LOOKBACK:
                pay = payload(chashes[i])
                if pay.get("date") == d:
                    rows = [p for p in pay.get("props") or [] if p.get("game_pk") is not None and int(p["game_pk"]) == gpk]
                    if rows:
                        found = (chashes[i], ctimes[i], pay, rows)
                        break
                i -= 1
            if not found:
                dg["game_without_pregame_rows"] += 1
                continue
            h, ct, pay, rows = found
            dg["games_with_rows"] += 1
            dg["lag_minutes_sum"] += int((start - ct).total_seconds() // 60)
            of = pay.get("odds_fetched_at")
            fresh = bool(of) and U18.T(of) <= ct and ct - U18.T(of) <= MAX_AGE
            for p in rows:
                pr = p.get("projection") or {}
                why = []
                if pr.get("stat") not in MARKETS:
                    why.append("ungradable_market")
                if p.get("market_side") not in (None, "over"):
                    why.append("not_over")
                if p.get("market_odds") is None:
                    why.append("unpriced")
                if p.get("lineup_assumed"):
                    why.append("lineup_not_confirmed")
                if not fresh:
                    why.append("stale_odds")
                if any(x in str(p.get("game_state") or "").lower() for x in ("live", "in_progress", "final")):
                    why.append("not_pregame")
                for w in why:
                    dg[f"fail_{w}"] += 1
                if why:
                    continue
                nv, basis = no_vig(p)
                out.append({"slate": d, "game_pk": gpk, "first_pitch": start.isoformat(), "commit": h,
                            "commit_time": ct.isoformat(), "odds_fetched_at": of, "id": p.get("id"),
                            "player_id": p.get("player_id"), "type": p.get("type"), "stat": pr["stat"],
                            "needs": pr.get("needs"), "line": pr.get("value"), "american": p["market_odds"],
                            "raw_implied": raw_implied(p["market_odds"]), "no_vig": nv, "no_vig_basis": basis,
                            "model": p.get("hit_probability"), "status": p.get("recommendation_status"),
                            "reliability": p.get("reliability")})
        diag[d] = dict(dg)
        print(d, dict(dg), flush=True)
    seen, uniq = set(), []
    for x in out:
        if (x["slate"], x["id"]) not in seen:
            seen.add((x["slate"], x["id"]))
            uniq.append(x)
    res = {"payloads_read": len(cache), "per_slate": diag, "candidates": uniq}
    (HERE / "out" / "candidates.json").write_text(json.dumps(res))
    return res


if __name__ == "__main__":
    main(*sys.argv[1:4])
