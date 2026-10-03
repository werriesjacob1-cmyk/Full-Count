#!/usr/bin/env python3
"""Mission 1.2 §2 — does PA-v1's days_rest feature mean the same thing
historically and live?

Drives the REAL production worker `mlb_sources._rest_batter_one` under both
reference clocks, with a stubbed game log so the only variable is the clock.
No network, no fixtures invented beyond the game dates themselves.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import mlb_daily as m                       # noqa: E402
import mlb_sources as ms                    # noqa: E402
from backtest.pa_v1_fit import days_rest_group  # noqa: E402
from generate_picks import clamp            # noqa: E402

D = dt.date(2026, 7, 15)                    # the slate date being predicted


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def run_worker(last_game_dates, *, today, asof):
    """Call the real worker with a stubbed gameLog."""
    payload = {"stats": [{"splits": [{"date": d.isoformat()}
                                     for d in last_game_dates]}]}
    saved_get, saved_today = m.retry_get, m.TODAY
    m.retry_get = lambda *a, **k: _Resp(payload)
    m.TODAY = today.isoformat()
    try:
        return ms._rest_batter_one((1, "Test", asof))
    finally:
        m.retry_get, m.TODAY = saved_get, saved_today


def stored_signal(raw):
    """The value _sig() actually stores (generate_picks.py:2088)."""
    return None if raw is None else clamp((raw - 1) * 2, -3, 4)


def bucket(sig):
    return days_rest_group({"days_rest": sig}) if sig is not None else None


def main():
    rows = []
    cases = [("D-1", [D - dt.timedelta(days=1)]),
             ("D-2", [D - dt.timedelta(days=2)]),
             ("D-3", [D - dt.timedelta(days=3)]),
             ("D-4", [D - dt.timedelta(days=4)]),
             ("D-5", [D - dt.timedelta(days=5)]),
             ("D-7", [D - dt.timedelta(days=7)]),
             # Doubleheader: two games on the same prior calendar day.
             ("DH on D-1", [D - dt.timedelta(days=1)] * 2),
             # A same-day earlier game (game 1 of a DH on the slate date).
             ("same-day (DH g1)", [D - dt.timedelta(days=2), D])]

    for label, dates in cases:
        # HISTORICAL: PointInTime sets m.TODAY = D-1 (engine.py:420,476) and
        # passes asof = that same cutoff.
        cutoff = (D - dt.timedelta(days=1)).isoformat()
        h = run_worker(dates, today=D - dt.timedelta(days=1), asof=cutoff)
        # LIVE: m.TODAY = D, asof=None.
        l = run_worker(dates, today=D, asof=None)

        h_raw = (h or {}).get("days_since_last_game")
        l_raw = (l or {}).get("days_since_last_game")
        h_sig, l_sig = stored_signal(h_raw), stored_signal(l_raw)
        h_b, l_b = bucket(h_sig), bucket(l_sig)
        rows.append({"circumstance": label,
                     "hist_raw": h_raw, "hist_signal": h_sig, "hist_bucket": h_b,
                     "live_raw": l_raw, "live_signal": l_sig, "live_bucket": l_b,
                     "same_cell": h_b == l_b})

    mismatches = [r for r in rows if not r["same_cell"]]
    print(f"{'circumstance':<18} {'H.raw':>5} {'H.sig':>6} {'H.bucket':<16}"
          f" {'L.raw':>5} {'L.sig':>6} {'L.bucket':<16} match")
    for r in rows:
        print(f"{r['circumstance']:<18} {str(r['hist_raw']):>5} "
              f"{str(r['hist_signal']):>6} {str(r['hist_bucket']):<16} "
              f"{str(r['live_raw']):>5} {str(r['live_signal']):>6} "
              f"{str(r['live_bucket']):<16} {'OK' if r['same_cell'] else 'DIFFER'}")
    print()
    print(f"VERDICT: {'PARITY HOLDS' if not mismatches else 'PARITY DEFECT CONFIRMED'}"
          f" ({len(mismatches)}/{len(rows)} circumstances map to different "
          f"PA-v1 fitted cells)")
    out = {"slate_date": D.isoformat(), "rows": rows,
           "mismatch_count": len(mismatches),
           "verdict": "PARITY HOLDS" if not mismatches else "PARITY DEFECT CONFIRMED"}
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as fh:
            json.dump(out, fh, indent=2)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
