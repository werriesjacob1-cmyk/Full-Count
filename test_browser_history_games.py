#!/usr/bin/env python3
"""Real-browser (Playwright/Chromium) checks for the 2026-09-24 History and
Games fixes, against the real docs/ build.

History: an open page must pick up provisional and then official results
without a reload, keep the reader's open day sections, survive a route switch,
and never move backwards on an older history.json. Games: a highlight whose
FanDuel line is not posted renders as a labelled research projection with the
specific reason, never "not priced".

history.json and live.json are served through Playwright route interception
so each step controls exactly which document the page sees; data.json is the
checked-in board with its freshness clocks rebased to now (same reasoning as
test_browser_e2e._rebased) so the board is actionable.

    python3 test_browser_history_games.py [-v]
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

VERBOSE = "-v" in sys.argv
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(ROOT, "docs")
PORT = 8936
BASE = f"http://127.0.0.1:{PORT}"
CHROMIUM_PATH = "/opt/pw-browsers/chromium"
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}" + (f"\n         {detail}" if detail and not cond else ""))


def iso(delta_minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat()


def rebased_board():
    with open(os.path.join(DOCS_DIR, "data.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    stamp = iso()
    for scope in (doc, doc.get("freshness"), doc.get("reconciliation")):
        if isinstance(scope, dict):
            for key in list(scope):
                if key.endswith("_at") and isinstance(scope[key], str):
                    scope[key] = stamp
    return doc


def live_doc(props):
    stamp = iso(1)
    with open(os.path.join(DOCS_DIR, "live.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    for key in list(doc):
        if key.endswith("_at") and isinstance(doc[key], str):
            doc[key] = stamp
    for channel in (doc.get("channels") or {}).values():
        if isinstance(channel, dict):
            for key in list(channel):
                if key.endswith("_at") and isinstance(channel[key], str):
                    channel[key] = stamp
    doc["props"] = props
    return doc


ID_A = "fc2:900001:player-1:hits:1:over"
ID_C = "fc2:900001:player-3:hits:1:over"
ID_D = "fc2:900000:player-4:hits:1:over"


def pick(pid, name, grade=None):
    return {"id": pid, "name": name, "prop": "Over 0.5 Hits", "matchup": "A @ B",
            "hit_probability": 0.7, "market_odds": -150, "grade": grade,
            "settlement_state": grade, "why": []}


def history(generated_at, a_grade):
    picks = [pick(ID_A, "Pick Alpha", a_grade), pick(ID_C, "Pick Charlie", "hit")]
    hits = sum(p["grade"] == "hit" for p in picks)
    misses = sum(p["grade"] == "miss" for p in picks)
    return {"schema_version": 1, "generated_at": generated_at, "retention_days": 45, "days": [
        {"date": "2026-09-23", "picks": picks, "hits": hits, "misses": misses,
         "hit_rate": hits / (hits + misses) if hits + misses else None},
        {"date": "2026-09-22", "picks": [pick(ID_D, "Pick Delta", "miss")],
         "hits": 0, "misses": 1, "hit_rate": 0.0},
    ]}


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

served = {}


def serve(route):
    name = os.path.basename(route.request.url.split("?")[0])
    route.fulfill(status=200, content_type="application/json", body=json.dumps(served[name]))


try:
    board = rebased_board()
    served["data.json"] = board
    served["history.json"] = history("2026-09-24T01:00:00+00:00", None)
    served["live.json"] = live_doc({ID_A: {
        "settlement_state": "provisional_hit", "settlement_authority": "live_observation",
        "settlement_observed_at": iso(1), "settlement_source": "fixture",
        "result_actual": 1, "result_reason": "fixture"}})

    ctx = browser.new_context(viewport={"width": 390, "height": 700})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    for name in ("data.json", "history.json", "live.json"):
        page.route(f"**/{name}*", serve)

    print("-- History: provisional, same-session final, preserved sections, route switch, stale guard")
    page.goto(f"{BASE}/index.html#/history")
    page.wait_for_selector(".history-day", timeout=20000)
    chip_a = lambda: page.locator(f'[data-pick-id="{ID_A}"] .pc-chips').text_content()
    record = lambda d: page.locator(f'details[data-date="{d}"] .history-day-record').inner_text()
    is_open = lambda d: page.evaluate(f'() => document.querySelector(\'details[data-date="{d}"]\').open')
    check("Cashed — awaiting official final" in chip_a(), "provisional hit shows as awaiting official final", chip_a())
    check(record("2026-09-23") == "1-0", "official day record counts durable grades only", record("2026-09-23"))
    check("1 cashed" in page.locator('details[data-date="2026-09-23"] summary').inner_text(),
          "pending tally shown beside, not inside, the record")

    page.evaluate("() => { window.__noReload = 'still-here'; }")
    page.locator('details[data-date="2026-09-23"] summary').click()
    page.locator('details[data-date="2026-09-22"] summary').click()
    check(not is_open("2026-09-23") and is_open("2026-09-22"), "reader toggled sections (setup)")
    page.evaluate("() => window.scrollTo(0, 120)")
    scroll_before = page.evaluate("() => window.scrollY")

    served["history.json"] = history("2026-09-24T05:00:00+00:00", "hit")
    page.evaluate("() => pollHistory()")
    page.wait_for_function(f'() => document.querySelector(\'[data-pick-id="{ID_A}"] .pc-chips\').textContent.includes("Hit")', timeout=5000)
    check(chip_a().strip() == "Hit ✓", "open page moved to the official final without reload", chip_a())
    check(record("2026-09-23") == "2-0", "official record updated from the new durable document", record("2026-09-23"))
    check(page.evaluate("() => window.__noReload") == "still-here", "no page reload happened")
    check(not is_open("2026-09-23") and is_open("2026-09-22"), "reader's open/closed sections preserved across refresh")
    check(page.evaluate("() => window.scrollY") == scroll_before, "scroll position preserved across refresh",
          f"{scroll_before} -> {page.evaluate('() => window.scrollY')}")

    page.evaluate("() => { location.hash = '#/games'; }")
    page.wait_for_timeout(300)
    page.evaluate("() => { location.hash = '#/history'; }")
    page.wait_for_selector(f'[data-pick-id="{ID_A}"]', timeout=5000, state="attached")
    check(chip_a().strip() == "Hit ✓", "route switch keeps the updated state")
    check(page.evaluate("() => window.__noReload") == "still-here", "route switch did not reload")

    served["history.json"] = history("2026-09-24T01:00:00+00:00", None)  # an old cached copy
    page.evaluate("() => pollHistory()")
    page.wait_for_timeout(400)
    check(chip_a().strip() == "Hit ✓", "an older history.json never moves the page backwards", chip_a())

    print("-- Games: an unposted line is a labelled research projection, never 'not priced'")
    game = next((g for g in board.get("schedule") or [] for s in g.get("pick_sections") or []
                 for p in s.get("picks") or [] if p.get("id")), None)
    check(game is not None, "the checked-in board has a game highlight to exercise")
    if game is not None:
        target = next(p["id"] for s in game["pick_sections"] for p in s["picks"] if p.get("id"))
        stamp = iso(2)
        served["live.json"] = live_doc({target: {
            "market_odds": None, "market_implied": None, "price_clears": None,
            "market_fetch_state": "NOT_POSTED", "market_fetch_checked_at": stamp,
            "_field_updated_at": {"market_odds": stamp, "market_implied": stamp,
                                  "price_clears": stamp, "market_fetch_state": stamp,
                                  "market_fetch_checked_at": stamp}}})
        page.evaluate("() => pollLive()")
        page.wait_for_timeout(300)
        line = page.evaluate(f"""() => {{
            const g = DATA.schedule.find(g => g.game_pk === {int(game['game_pk'])});
            const p = g.pick_sections.flatMap(s => s.picks).find(p => p.id === {json.dumps(target)});
            return gamePickLine(p); }}""")
        page.evaluate("() => { location.hash = '#/games'; }")
        page.wait_for_timeout(400)
        page_text = page.locator("#page-games").inner_text()
        check("research projection · Not yet posted on FanDuel" in line,
              "NOT_POSTED highlight is a research projection with the specific reason", line)
        check("not priced" not in page_text, "the Games page never renders the generic 'not priced'")
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
