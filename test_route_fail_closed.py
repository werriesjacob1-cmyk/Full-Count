#!/usr/bin/env python3
"""Every customer entry point, entered DIRECTLY.

Today being fail-closed is not enough: a customer can bookmark
#/watchlist, follow a link to #/games/823666, or open the detail sheet from
search without ever passing through the home page. On HEAD 2ee82ed7 only
Today and All Props carried the protection, so four routes -- Games list,
game detail, My Board and the detail sheet -- rendered probabilities,
FanDuel prices and Top Pick styling with nothing saying they were
unverified.

Every test here navigates straight to its route. A deep link must be
exactly as safe as arriving from the home page.

Run: python3 test_route_fail_closed.py [-v]
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
        if detail and (VERBOSE or not cond):
            print("         " + str(detail)[:400])


from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
PORT = 8944
BASE = f"http://127.0.0.1:{PORT}"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass


_httpd = http.server.ThreadingHTTPServer(
    ("127.0.0.1", PORT), functools.partial(_Quiet, directory=DOCS))
threading.Thread(target=_httpd.serve_forever, daemon=True).start()

_pw = sync_playwright().start()
try:
    _browser = _pw.chromium.launch()
except Exception:
    _browser = _pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")

with open(os.path.join(DOCS, "data.json"), encoding="utf-8") as fh:
    PAYLOAD = json.load(fh)
GAME_PK = (PAYLOAD.get("schedule") or [{}])[0].get("game_pk")
A_PROP = next((r for r in PAYLOAD["props"] if r.get("market_odds") is not None),
              PAYLOAD["props"][0])
WATCH_KEY = "fc_watchlist_v1"

# The four ways currency can fail to be proven. Each is injected in the
# browser BEFORE the route renders, so the route itself must react.
SCENARIOS = {
    "live.json unavailable on first paint": None,   # handled by route abort
    "board stale past actionability": """() => {
        DATA.generated_at = new Date(Date.now() - 9*3600*1000).toISOString();
        if (DATA.freshness) DATA.freshness.model_basis_at = DATA.generated_at; }""",
    "sportsbook price observation stale": """() => {
        DATA.prices_updated_at = new Date(Date.now() - 5*3600*1000).toISOString();
        DATA.odds_fetched_at = DATA.prices_updated_at;
        if (DATA.freshness) DATA.freshness.market_prices_at = DATA.prices_updated_at; }""",
    "open reconciliation mismatch": """() => {
        DATA.reconciliation = { checked_at: new Date().toISOString(),
          open: { "board_age:x": { kind: "board_age", detail: "stale",
                                   first_seen_at: new Date().toISOString() } } }; }""",
}

ROUTES = [
    ("Today", "#/today"),
    ("All Props", "#/props"),
    ("Games list", "#/games"),
    ("Game detail", f"#/games/{GAME_PK}"),
    ("My Board", "#/watchlist"),
]


def open_route(route, *, block_live=False, mutate=None, watch=None):
    ctx = _browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    if block_live:
        pg.route("**/live.json*", lambda r: r.abort())
    if watch:
        # An EMPTY My Board has nothing actionable to mislead anyone about,
        # so seed a real saved prop -- the case that actually carries risk.
        pg.add_init_script(
            f"try {{ localStorage.setItem({WATCH_KEY!r}, {json.dumps(watch)!r}); }} catch (e) {{}}")
    pg.goto(f"{BASE}/index.html{route}")
    pg.wait_for_selector(".pick-card, .prop-row, .game-card, .empty-state, .fail-closed,"
                         " .game-list, .watchlist-empty", timeout=20000, state="attached")
    if mutate:
        pg.evaluate(mutate)
        pg.evaluate("() => renderRoute()")
        pg.wait_for_timeout(150)
    return ctx, pg


print("-- every route, entered directly, under every unverifiable condition")
for scenario, mutate in SCENARIOS.items():
    block = mutate is None
    for name, route in ROUTES:
        ctx, pg = open_route(route, block_live=block, mutate=mutate,
                             watch=[A_PROP["id"]] if name == "My Board" else None)
        try:
            actionable = pg.evaluate("() => boardIsActionable()")
            check(actionable is False,
                  f"[{scenario}] {name}: board is classified NOT actionable")
            body = pg.inner_text("body")
            marked = (pg.locator(".fail-closed").count() > 0
                      or "PRICES UNVERIFIED" in body
                      or "BOARD OUT OF DATE" in body)
            check(marked, f"[{scenario}] {name}: route declares itself unverified",
                  body[:180])
            if name in ("Today",):
                check(pg.locator(".pick-card").count() == 0,
                      f"[{scenario}] {name}: no ordinary pick cards render")
        finally:
            ctx.close()

print("-- detail sheet, opened directly from a deep-linked route")
for scenario, mutate in SCENARIOS.items():
    ctx, pg = open_route("#/props", block_live=(mutate is None), mutate=mutate)
    try:
        html = pg.evaluate("(id) => detailBody(PROPS_BY_ID.get(id))", A_PROP["id"])
        check("fail-closed" in html,
              f"[{scenario}] detail sheet carries the unverified notice", html[:200])
        pos_notice = html.find("fail-closed")
        pos_hero = html.find("detail-hero")
        check(pos_notice != -1 and pos_notice < pos_hero,
              f"[{scenario}] the notice precedes the probability hero")
    finally:
        ctx.close()

print("-- search results, from a deep-linked route")
ctx, pg = open_route("#/props", block_live=True)
try:
    check(pg.evaluate("() => boardIsActionable()") is False, "search precondition")
    pg.fill("#global-search", A_PROP["name"][:6])
    pg.wait_for_timeout(400)
    res = pg.inner_text("#search-results")
    check("Research only" in res,
          "search results declare themselves unverified", res[:200])
finally:
    ctx.close()

print("-- suggested parlay is withheld on every unverifiable condition")
for scenario, mutate in SCENARIOS.items():
    ctx, pg = open_route("#/today", block_live=(mutate is None), mutate=mutate)
    try:
        check(pg.locator(".parlay-card:not(.parlay-card-suppressed)").count() == 0,
              f"[{scenario}] no live parlay is presented")
    finally:
        ctx.close()

print("-- a healthy board still presents normally (the control)")
ctx, pg = open_route("#/today")
try:
    healthy = pg.evaluate("""() => {
        DATA.generated_at = new Date().toISOString();
        DATA.prices_updated_at = new Date().toISOString();
        DATA.odds_fetched_at = DATA.prices_updated_at;
        DATA.freshness = null; DATA.reconciliation = null;
        LIVE_OVERLAY_STATE = "applied";
        renderRoute();
        return boardIsActionable(); }""")
    check(healthy is True, "a fresh, reconciled board is actionable")
    check(pg.locator(".fail-closed").count() == 0,
          "and shows no fail-closed panel -- the protection is conditional, not permanent")
finally:
    ctx.close()

_httpd.shutdown()
_browser.close()
_pw.stop()

passed = sum(1 for ok, _, _ in _results if ok)
print("\n" + "=" * 70)
print("RESULT: %d/%d checks passed" % (passed, len(_results)))
print("=" * 70)
for ok, msg, detail in _results:
    if not ok:
        print("  FAILED: %s\n          %s" % (msg, str(detail)[:300]))
sys.exit(0 if passed == len(_results) else 1)
