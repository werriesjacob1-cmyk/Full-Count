#!/usr/bin/env python3
"""Real-browser (Chromium) check of the Central-midnight Today contract.

A fake browser clock starts at 11:55 pm CDT on Sept 23 with a board built
after the UTC rollover (build date Sept 24, display date Sept 23). Sept 23's
published picks -- two final, one live -- must show under "Today · Wed,
Sep 23". After the clock passes Central midnight, with no new deploy and no
reload, the open tab must drop the settled ones and keep only the live pick
("Still open from Wed, Sep 23"), without errors.

    python3 test_browser_today_central.py [-v]
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

VERBOSE = "-v" in sys.argv
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(ROOT, "docs")
PORT = 8937
BASE = f"http://127.0.0.1:{PORT}"
CHROMIUM_PATH = "/opt/pw-browsers/chromium"
BEFORE_MIDNIGHT = datetime(2026, 9, 24, 4, 55, tzinfo=timezone.utc)   # 11:55 pm CDT Sept 23
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}" + (f"\n         {detail}" if detail and not cond else ""))


def restamp(doc, stamp):
    for scope in (doc, doc.get("freshness"), doc.get("reconciliation"), *(doc.get("channels") or {}).values()):
        if isinstance(scope, dict):
            for key in list(scope):
                if key.endswith("_at") and isinstance(scope[key], str):
                    scope[key] = stamp


def build_fixture():
    stamp = BEFORE_MIDNIGHT.isoformat()
    with open(os.path.join(DOCS_DIR, "data.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    restamp(data, stamp)
    data.update({"date": "2026-09-24", "display_date": "2026-09-23",
                 "display_timezone": "America/Chicago"})
    # This tests browser rollover, not today's changing production pick volume.
    # Synthetic, uniquely identified rows keep it reproducible when a live
    # build publishes fewer than four Top Picks (including zero).
    def fixture_pick(number):
        return {
            "id": f"fc2:900001:player-{number}:hits:1:over",
            "name": f"Fixture Player {number}",
            "prop": "Over 0.5 Hits",
            "game_pk": 900001,
            "matchup": "Fixture A @ Fixture B",
            "recommendation_status": "top_pick",
            "hit_probability": 0.7,
            "market_odds": -150,
            "game_start": "2026-09-24T00:10:00Z",
            "why": [],
        }

    final_a, final_b, live_c, early = (fixture_pick(n) for n in range(1, 5))
    published = {"published_slate_date": "2026-09-23", "published_top_pick_at": "2026-09-23T20:00:00+00:00",
                 "publication_artifact_id": "f" * 64, "game_start": "2026-09-24T00:10:00Z",
                 "game_state_observed_at": stamp, "settlement_observed_at": stamp}
    for row, state, settle in ((final_a, "final", "hit"), (final_b, "final", "miss"), (live_c, "live", "open")):
        row.update(published)
        row.update({"game_state": state, "settlement_state": settle,
                    "settlement_authority": "official_final" if state == "final" else "none"})
        row["publication_snapshot"] = {k: row[k] for k in ("id", "recommendation_status", "market_odds")}
    early["game_start"] = "2026-09-25T00:10:00Z"
    early["game_state"] = "pregame"
    # Exclude live board props entirely: their counts, game states and
    # publication metadata must not affect this fixed browser scenario.
    data["props"] = [final_a, final_b, live_c, early]
    with open(os.path.join(DOCS_DIR, "live.json"), encoding="utf-8") as fh:
        live = json.load(fh)
    restamp(live, stamp)
    live["props"] = {}
    return data, live, (final_a["id"], final_b["id"], live_c["id"])


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass


httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(Quiet, directory=DOCS_DIR))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
pw = sync_playwright().start()
try:
    browser = pw.chromium.launch()
except Exception:
    browser = pw.chromium.launch(executable_path=CHROMIUM_PATH)

try:
    data, live, (final_a, final_b, live_c) = build_fixture()
    served = {"data.json": data, "live.json": live}
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    def serve_as(name):
        def handler(route, _request=None):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(served[name]))
        return handler

    for name in served:
        page.route(f"**/{name}*", serve_as(name))
    page.clock.install(time=BEFORE_MIDNIGHT)
    page.goto(f"{BASE}/index.html#/today")
    page.wait_for_selector("#page-today .section-head", timeout=20000)
    page.wait_for_timeout(300)

    def card(pid):
        return page.locator(f'#page-today [data-open="{pid}"]').count()

    def heads():
        return page.locator("#page-today .top-pick-group-head").all_text_contents()

    print("-- before Central midnight (11:55 pm CDT)")
    check(page.evaluate("() => displayToday()") == "2026-09-23", "browser slate day is Sept 23")
    check(all(card(i) for i in (final_a, final_b, live_c)), "all three Sept 23 published picks on Today")
    check(any(h.startswith("Today · Wed, Sep 23") for h in heads()), "grouped under 'Today · Wed, Sep 23'", str(heads()))
    check(any(h.startswith("Early picks for Thu, Sep 24") for h in heads()), "next-slate pick under 'Early picks'", str(heads()))

    page.evaluate("() => { window.__noReload = 1; }")
    page.clock.run_for("10:00")   # 12:05 am CDT Sept 24; the minute tick runs renderFreshness
    page.wait_for_timeout(300)

    print("-- after Central midnight, same tab, no deploy")
    check(page.evaluate("() => displayToday()") == "2026-09-24", "browser slate day turned over to Sept 24")
    check(page.evaluate("() => window.__noReload") == 1, "no reload happened")
    check(card(final_a) == 0 and card(final_b) == 0, "settled Sept 23 picks left Today")
    check(card(live_c) == 1, "the still-live Sept 23 pick stays")
    check(any(h.startswith("Still open from Wed, Sep 23") for h in heads()), "live pick under 'Still open from'", str(heads()))
    check(not any(h.startswith("Today · Wed, Sep 23") for h in heads()), "no stale 'Today · Wed, Sep 23' heading", str(heads()))
    check(any(h.startswith("Today · Thu, Sep 24") for h in heads()), "the Sept 24 pick is now today's", str(heads()))
    check(not errors, "no page errors", str(errors))
    ctx.close()
finally:
    browser.close()
    pw.stop()
    httpd.shutdown()

n_pass = sum(ok for ok, _, _ in _results)
print(f"RESULT: {n_pass}/{len(_results)} checks passed")
for ok, msg, detail in _results:
    if not ok:
        print(f"  FAILED: {msg} {detail}")
sys.exit(0 if n_pass == len(_results) else 1)
