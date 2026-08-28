#!/usr/bin/env python3
"""Fail closed for real, and on every surface -- not just a banner.

The first P0 pass added a warning strip above cards that still rendered
exactly as before: same prices, same edges, same "Top Pick" chips. That is
a description of a problem, not a refusal to act on it. A customer scanning
cards does not re-read a banner before each one, and the cards themselves
still asserted a currency they could not back.

It also left the DERIVED surfaces alone. One corrected canonical prop can
still appear as current through a copy: the suggested parlay is built once
during full generation and frozen into the payload, so its legs keep
advertising generation-time prices while the live overlay corrects the real
props underneath them.

Real browser, real docs/ build. Requires Playwright + Chromium and fails
loudly rather than skipping -- a surface that silently presents stale
numbers as current is exactly what this file exists to catch.
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
DOCS_DIR = os.path.join(ROOT, "docs")
PORT = 8941
BASE = f"http://127.0.0.1:{PORT}"
CHROMIUM = "/opt/pw-browsers/chromium"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass


_httpd = http.server.ThreadingHTTPServer(
    ("127.0.0.1", PORT), functools.partial(_Quiet, directory=DOCS_DIR))
threading.Thread(target=_httpd.serve_forever, daemon=True).start()

_pw = sync_playwright().start()
try:
    _browser = _pw.chromium.launch()
except Exception:
    _browser = _pw.chromium.launch(executable_path=CHROMIUM)


def page_at(route="#/today", block_live=False):
    ctx = _browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    if block_live:
        pg.route("**/live.json*", lambda r: r.abort())
    pg.goto(f"{BASE}/index.html{route}")
    pg.wait_for_selector(".pick-card, .empty-state, .prop-row, .fail-closed",
                         timeout=20000, state="attached")
    return ctx, pg


# ── 1. an unverifiable overlay stops the picks being presented ──────────
print("-- unverifiable board fails closed on Today")
ctx, pg = page_at(block_live=True)
try:
    check(pg.evaluate("() => boardIsActionable()") is False,
          "boardIsActionable() is false when the live overlay could not be applied")
    check(pg.locator(".fail-closed").count() > 0,
          "a fail-closed panel is rendered in place of the picks")
    check(pg.locator(".pick-card").count() == 0,
          "NO ordinary pick cards are rendered while the board is unverifiable",
          f"{pg.locator('.pick-card').count()} cards still rendered")
    text = pg.inner_text(".fail-closed")
    check("nothing here is being offered as a bet" in text.lower(),
          "the panel withdraws the recommendation in plain words", text[:200])
    check(pg.locator(".parlay-card").count() == 0,
          "the suggested parlay is not rendered either")
finally:
    ctx.close()

# ── 2. the research is reachable, and labelled ──────────────────────────
print("-- research stays reachable behind an explicit choice")
ctx, pg = page_at(block_live=True)
try:
    pg.click(".fail-closed button")
    pg.wait_for_timeout(300)
    check(pg.evaluate("() => SHOW_UNVERIFIED") is True,
          "the viewer can opt in to see the unverified board")
    check(pg.locator(".freshness-bar").inner_text().find("PRICES UNVERIFIED") >= 0,
          "the bar still flags the board as unverified after opting in",
          pg.locator(".freshness-bar").inner_text())
finally:
    ctx.close()

# ── 3. All Props declares itself unverified rather than hiding ──────────
print("-- All Props declares, does not hide")
ctx, pg = page_at(route="#/props", block_live=True)
try:
    check(pg.locator(".fail-closed-inline").count() > 0,
          "All Props carries an explicit unverified notice")
    check(pg.locator(".prop-row").count() > 0,
          "All Props still lists rows -- browsing the board is its purpose")
finally:
    ctx.close()

# ── 4. LINE_MOVED renders on a COMPACT card, not just the detail sheet ──
print("-- compact card renders LINE_MOVED explicitly")
ctx, pg = page_at()
try:
    moved = pg.evaluate("""() => marketBlock({
        market_odds: null, market_fetch_state: "LINE_MOVED",
        market_posted_line: 14.5, market_posted_over: -132 })""")
    check("Line moved" in moved, "the compact card says the line moved", moved)
    check("14.5" in moved, "it names the line FanDuel actually posts", moved)
    check("Not yet posted on FanDuel" not in moved,
          "it does NOT fall through to the generic not-posted copy", moved)
    check("Not bettable at our number" in moved,
          "it says the displayed number cannot be bet", moved)
    plain = pg.evaluate("""() => marketBlock({ market_odds: null,
        market_fetch_state: "NOT_POSTED" })""")
    check("Not yet posted on FanDuel" in plain,
          "a genuine absence still reads as not posted", plain)
finally:
    ctx.close()

# ── 5. no derived surface can outlive a correction ─────────────────────
print("-- derived surfaces cannot outlive a correction")
ctx, pg = page_at()
try:
    # Every rendered surface must draw its price from the live prop map,
    # never from a frozen copy. Prove it by corrupting the live prop and
    # confirming the parlay refuses rather than showing the frozen price.
    # The checked-in board is the real, very stale production payload, so
    # board-level suppression would fire before any per-leg check. Make the
    # board actionable first so this exercises the LEG path specifically.
    res = pg.evaluate("""() => {
      DATA.generated_at = new Date().toISOString();
      DATA.prices_updated_at = new Date().toISOString();
      DATA.freshness = null; DATA.reconciliation = null;
      LIVE_OVERLAY_STATE = "applied";
      const id = [...PROPS_BY_ID.keys()].find(k => {
        const v = PROPS_BY_ID.get(k);
        return v.market_odds != null && !v.lineup_assumed;
      });
      const live = PROPS_BY_ID.get(id);
      const frozen = { legs: [{ id, name: live.name, prop: live.prop,
                                market_odds: -999 }],
                       combined_american_odds: -999 };
      const actionable = boardIsActionable();
      const before = suggestedParlayBlock(frozen);
      live.market_fetch_state = "LINE_MOVED";
      live.market_odds = null;
      live.market_posted_line = 99.5;
      const after = suggestedParlayBlock(frozen);
      return { actionable, before, after };
    }""")
    check(res["actionable"] is True,
          "fixture precondition: board made actionable so the LEG path is exercised")
    check("-999" not in res["before"],
          "neither a leg price nor the combined figure comes from the frozen copy",
          res["before"][:260])
    check("moved off the line" in res["after"],
          "once the underlying prop is corrected the parlay suppresses itself",
          res["after"][:220])
    check("-999" not in res["after"],
          "and it certainly never shows the obsolete price", res["after"][:220])
finally:
    ctx.close()

# ── 6. the four preserved P0 guarantees still hold ─────────────────────
print("-- previously accepted P0 fixes preserved")
ctx, pg = page_at()
try:
    check(pg.evaluate("() => LIVE_OVERLAY_STATE") == "applied",
          "first-paint live overlay still applied before render")
    clocks = pg.evaluate("() => boardClocks(DATA)")
    check(set(clocks) == {"model_basis_at", "lineups_observed_at",
                          "market_prices_at", "live_game_observed_at"},
          "the four clocks are still distinct", clocks)
    with open(os.path.join(DOCS_DIR, "data.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    ids = [r["id"] for r in payload["props"]]
    check(len(set(ids)) == len(ids) and all(ids), "no synthetic or duplicate prop ids")
    # Deliberately NOT "Walker Jenkins is absent". That was true of the
    # payload this work started from and is no longer true of any payload:
    # MLB added him to the roster (player_id 805805), his id now resolves,
    # and his rows are legitimately published. Asserting his absence would
    # be asserting a fact about one afternoon's data, and it would go on
    # "passing" long after it had stopped testing anything.
    #
    # The real invariant is that nothing reaches the board without a subject
    # that can actually settle it. A row with neither a player nor a combo
    # nor a game-level subject is exactly what canonical_prop_id refuses to
    # mint an id for, and what the quarantine drops.
    orphans = [r for r in payload["props"]
               if not r.get("player_id") and not r.get("combo_player_ids")
               and not str(r.get("id", "")).startswith("fc2:")]
    check(not orphans,
          "no published row lacks a settleable subject (the quarantine invariant)",
          f"{len(orphans)} orphan row(s), e.g. {orphans[:1]}")
    check(all(str(r.get("id", "")).startswith("fc2:") for r in payload["props"]),
          "every published id is a real canonical id, never synthesized")
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
        print("  FAILED: %s\n          %s" % (msg, str(detail)[:400]))
sys.exit(0 if passed == len(_results) else 1)
