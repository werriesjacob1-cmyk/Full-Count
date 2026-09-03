#!/usr/bin/env python3
"""First paint must not be fail-open. Real browser, real docs/ build.

Incident, 2026-08-28. boot() rendered the board from data.json and only
then fetched live.json, whose failure it swallowed silently. data.json is
the BASE payload: prices as of the last full build, stale=false on every
row, and none of the suppression reasons. All of that lives exclusively in
live.json.

Measured on the real production payload at the time of the incident:

    2,584 props
    1,691 showed a different market price in the base than the overlay
    1,897 rendered stale=false that the overlay marks stale
       47 carried a different recommendation_status

So every page load briefly showed a fail-OPEN board, and any browser that
could not reach live.json showed one indefinitely, with nothing on screen
admitting it. Kevin McGonigle's 1+ H+R+RBI read -260 (the 06:31 build)
while the overlay had -230 verified at 16:26.

Two guarantees, both asserted here against a real headless Chromium
driving the real docs/ output:

  1. the overlay is applied BEFORE the first paint
  2. when the overlay cannot be fetched, the board says so out loud

Requires Playwright + Chromium, same as test_browser_e2e.py, and fails
loudly rather than skipping if they are missing -- a fail-open board is
exactly what this file exists to catch.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
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
            print("         " + detail)


from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(ROOT, "docs")
PORT = 8937
BASE = f"http://127.0.0.1:{PORT}"
CHROMIUM_PATH = "/opt/pw-browsers/chromium"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass


_handler = functools.partial(_QuietHandler, directory=DOCS_DIR)
_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), _handler)
threading.Thread(target=_httpd.serve_forever, daemon=True).start()

_pw = sync_playwright().start()
try:
    _browser = _pw.chromium.launch()
except Exception:
    _browser = _pw.chromium.launch(executable_path=CHROMIUM_PATH)


def new_page():
    context = _browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    return context, page


# ── 1. constants must not drift from the Python that enforces them ──────
print("-- constant parity with recommendation.py")
import recommendation  # noqa: E402

with open(os.path.join(ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
    APP_JS = fh.read()


def js_const(name):
    m = re.search(rf"const {name} = ([0-9 */]+);", APP_JS)
    return eval(m.group(1)) if m else None  # noqa: S307 - arithmetic literal only


check(js_const("MAX_BOARD_AGE_SECONDS") == recommendation.MAX_BOARD_AGE_SECONDS,
      "app.js MAX_BOARD_AGE_SECONDS matches recommendation.py",
      f"js={js_const('MAX_BOARD_AGE_SECONDS')} py={recommendation.MAX_BOARD_AGE_SECONDS}")
check(js_const("MAX_PRICE_AGE_SECONDS") == recommendation.MAX_PRICE_AGE_SECONDS,
      "app.js MAX_PRICE_AGE_SECONDS matches recommendation.py",
      f"js={js_const('MAX_PRICE_AGE_SECONDS')} py={recommendation.MAX_PRICE_AGE_SECONDS}")

# ── 2. the overlay is applied before the first paint ────────────────────
print("-- overlay applied before first paint")
with open(os.path.join(DOCS_DIR, "data.json"), encoding="utf-8") as fh:
    DATA = json.load(fh)
with open(os.path.join(DOCS_DIR, "live.json"), encoding="utf-8") as fh:
    LIVE = json.load(fh)

# Build one deterministic in-memory overlay delta. The old test searched
# committed live.json for a naturally moved price, which made CI depend on
# the synthetic PR merge ref happening to pair two runtime artifacts with a
# disagreement. Exercise the same real boot/merge path, but manufacture the
# visible delta for this request only.
probe_row = next((r for r in DATA["props"] if r.get("id") and r.get("market_odds") is not None), None)

if probe_row is None:
    check(False, "found a priced prop for deterministic overlay probe",
          "data.json has no prop with both id and market_odds")
else:
    pid = probe_row["id"]
    base_odds = probe_row.get("market_odds")
    # Any different valid American price works; choose deterministically.
    live_odds = -101 if base_odds != -101 else -102
    synthetic_live = json.loads(json.dumps(LIVE))
    synthetic_live.setdefault("props", {})
    delta = dict(synthetic_live["props"].get(pid) or {})
    delta["market_odds"] = live_odds
    synthetic_live["props"][pid] = delta
    synthetic_body = json.dumps(synthetic_live).encode("utf-8")

    ctx, page = new_page()
    try:
        def _serve_synthetic_live(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=synthetic_body,
            )

        page.route("**/live.json*", _serve_synthetic_live)
        page.goto(f"{BASE}/index.html#/today")
        page.wait_for_function("() => window.PROPS_BY_ID !== undefined || document.getElementById('board-alert') !== null",
                               timeout=20000)
        # A fail-closed board renders NO pick cards by design, so the panel is
        # a legitimate terminal render state for this wait (2026-08-28 P0
        # follow-up). Waiting only for cards made this test depend on the
        # checked-in payload happening to be fresh.
        page.wait_for_selector(".pick-card, .empty-state, .prop-row, .fail-closed",
                               timeout=20000, state="attached")
        got = page.evaluate(
            "(id) => { const p = PROPS_BY_ID.get(id); return p ? p.market_odds : null; }", pid)
        check(got == live_odds,
              "first paint shows the OVERLAY price, not the base payload price",
              f"prop {pid}: base={base_odds} overlay={live_odds} rendered={got}")
        state = page.evaluate("() => LIVE_OVERLAY_STATE")
        check(state == "applied", "LIVE_OVERLAY_STATE is 'applied' after a successful boot",
              f"got {state!r}")
    finally:
        ctx.close()

# ── 3. an unreachable overlay fails CLOSED and says so ──────────────────
print("-- unreachable overlay fails closed")
ctx, page = new_page()
try:
    page.route("**/live.json*", lambda route: route.abort())
    page.goto(f"{BASE}/index.html#/today")
    page.wait_for_selector(".pick-card, .empty-state, .prop-row, .fail-closed",
                           timeout=20000, state="attached")
    state = page.evaluate("() => LIVE_OVERLAY_STATE")
    check(state == "unavailable",
          "LIVE_OVERLAY_STATE is 'unavailable' when live.json cannot be fetched",
          f"got {state!r}")
    alert_text = page.inner_text("#board-alert").strip()
    check(bool(alert_text), "a board-level alert is rendered when the overlay is unavailable",
          f"#board-alert text: {alert_text[:160]!r}")
    check("may not be current" in alert_text.lower() or "unverified" in alert_text.lower(),
          "the alert actually tells the customer the prices are unverified",
          f"text: {alert_text[:200]!r}")
    bar = page.inner_text("#freshness-bar")
    check("PRICES UNVERIFIED" in bar,
          "the freshness bar flags unverified prices too", f"bar: {bar!r}")
finally:
    ctx.close()

# ── 4. a stale board says so even when the overlay IS available ─────────
print("-- stale board banner")
ctx, page = new_page()
try:
    page.goto(f"{BASE}/index.html#/today")
    page.wait_for_selector(".pick-card, .empty-state, .prop-row, .fail-closed",
                           timeout=20000, state="attached")
    fresh = page.evaluate("() => boardFreshnessState(Date.now(), DATA)")
    check(fresh["state"] in ("fresh", "stale", "unknown"),
          "boardFreshnessState returns a declared state", f"got {fresh}")
    # Force the MODEL BASIS past the limit and confirm the banner appears.
    # boardFreshnessState() correctly prefers freshness.model_basis_at over
    # legacy generated_at, so age the canonical clock too. Clone freshness
    # before editing so this test never mutates the real in-page DATA object.
    forced = page.evaluate(
        "() => { const d = Object.assign({}, DATA);"
        "  const old = new Date(Date.now() - 11 * 3600 * 1000).toISOString();"
        "  d.generated_at = old;"
        "  d.freshness = Object.assign({}, d.freshness || {}, {model_basis_at: old});"
        "  return boardFreshnessState(Date.now(), d); }")
    check(forced["state"] == "stale" and forced["reason"] == "board_age_exceeded",
          "an 11-hour-old board is classified stale on board age", f"got {forced}")
    html = page.evaluate("() => boardStalenessBanner({state:'stale', ageSeconds: 36360,"
                         " priceAgeSeconds: 120, reason:'board_age_exceeded'})")
    check("out of date" in html.lower(),
          "the stale-board banner states the board is out of date")
    check("nothing here is being offered as a current recommendation" in html.lower(),
          "the banner withdraws the recommendation rather than merely noting the age",
          f"html: {html[:300]!r}")
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
        print("  FAILED: %s\n          %s" % (msg, detail))
sys.exit(0 if passed == len(_results) else 1)
