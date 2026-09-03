#!/usr/bin/env python3
"""test_browser_e2e.py — real browser (Playwright/Chromium) coverage for the
frontend interaction contracts the mega-directive named explicitly: "the site
cannot rely on manual Playwright checks anymore... do not make the major
interaction contracts manual-only." Everything in test_build_dashboard.py's
JS-in-a-Node-VM harnesses is real logic coverage but runs with no DOM, no
CSS, no real click/tap/scroll -- it cannot catch a layout bug (the mobile
FanDuel-price-hidden regression this same pass just found), a broken click
handler wiring, or an actual sticky/overflow/viewport failure. This file
drives the real docs/ build (the actual GitHub Pages output) in a real
headless Chromium, at desktop and at the three mobile widths Jacob's phone
actually uses (375/390/430) plus one short-height viewport.

Uses whatever docs/data.json is currently checked in -- works against both
the stale pre-fix payload and a freshly regenerated one; assertions are
written against structure/behavior, not specific player names or exact
counts, so this suite doesn't need to be re-written every time the slate
changes. The served copy's freshness clocks are rebased to test time so the
result does not depend on how long ago the fixture was committed -- see
_CLOCK_REBASED below for why that is required and what still covers the
fail-closed contract.

    python3 test_browser_e2e.py [-v]

Requires the `playwright` pip package with Chromium already fetched to
PLAYWRIGHT_BROWSERS_PATH (both true in this environment -- see the repo's
own environment notes). If Chromium truly isn't available, this file must
FAIL LOUDLY, not silently skip -- a frontend regression that this suite
would have caught is worse than a slow test run.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(ROOT, "docs")
PORT = 8934
BASE = f"http://127.0.0.1:{PORT}"
CHROMIUM_PATH = "/opt/pw-browsers/chromium"

# ── static server for the real docs/ build (the actual deployed output) ──


# FRESHNESS CLOCKS ARE REBASED TO TEST TIME (2026-08-28).
#
# Found by this suite going 91/98 with no code change at all -- only the
# clock moving. Before the P0 fail-closed work, a stale fixture still
# rendered cards, so serving docs/ verbatim was fine. Now it is not: the
# board fails closed once `prices_updated_at` is more than 45 minutes old
# (recommendation.py's rule, mirrored in app.js), every card disappears
# exactly as designed, and the seven card-dependent checks here hard-fail.
#
# That made this suite a function of HOW LONG AGO the fixture was committed.
# It passed in CI at 21:05 UTC with a 20:50 fixture and failed at 21:57 with
# the same bytes and the same code -- so it would have started failing every
# PR run for reasons unrelated to the diff, and worse, would have gone green
# again on a re-run after the next scheduled build refreshed docs/.
#
# So the served copy of live.json/data.json gets its `*_at` clocks rebased
# to now. This suite's job is the INTERACTION contracts -- routing, filters,
# drill-down, the detail sheet, mobile viewports -- which need a board that
# is actionable at all in order to be reachable. The fail-closed contract
# itself is not weakened by this: it is owned by test_fail_closed_surfaces.py
# and test_route_fail_closed.py, which build their own explicit stale
# fixtures and assert the suppression directly.
_CLOCK_REBASED = {"live.json", "data.json"}


def _rebased(raw):
    """Same document, customer-actionability clocks moved to now.

    P0 split freshness into scoped clocks. The first version of this helper
    rebased only top-level *_at fields, so nested freshness.model_basis_at
    continued aging in real time and the interaction suite eventually hid
    every card again. Rebase the clock-bearing scopes the frontend actually
    consumes, without recursively rewriting historical timestamps inside
    props/publication records.
    """
    doc = json.loads(raw)
    stamp = datetime.now(timezone.utc).isoformat()

    def _stamp(mapping):
        if not isinstance(mapping, dict):
            return
        for key in list(mapping):
            if key.endswith("_at") and isinstance(mapping[key], str):
                mapping[key] = stamp

    _stamp(doc)
    _stamp(doc.get("freshness"))
    _stamp(doc.get("reconciliation"))
    return json.dumps(doc).encode("utf-8")


# Pure regression guard for the clock fixture itself. This specifically catches
# the failure that let freshness.model_basis_at remain old while generated_at
# was moved forward.
_probe_old = "2000-01-01T00:00:00+00:00"
_probe = json.loads(_rebased(json.dumps({
    "generated_at": _probe_old,
    "freshness": {"model_basis_at": _probe_old, "lineups_observed_at": _probe_old},
    "reconciliation": {"checked_at": _probe_old},
}).encode("utf-8")))
check(_probe["generated_at"] != _probe_old
      and _probe["freshness"]["model_basis_at"] != _probe_old
      and _probe["freshness"]["lineups_observed_at"] != _probe_old
      and _probe["reconciliation"]["checked_at"] != _probe_old,
      "interaction fixture rebases top-level and scoped four-clock freshness timestamps")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def do_GET(self):
        name = os.path.basename(self.path.split("?")[0])
        if name in _CLOCK_REBASED:
            path = os.path.join(DOCS_DIR, name)
            try:
                with open(path, "rb") as fh:
                    body = _rebased(fh.read())
            except (OSError, ValueError):
                return super().do_GET()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


_handler = functools.partial(_QuietHandler, directory=DOCS_DIR)
_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), _handler)
_server_thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
_server_thread.start()

_pw = sync_playwright().start()
try:
    # Real CI (test.yml's own "Install Playwright Chromium" step) installs
    # the matching browser build to Playwright's normal default location --
    # try that first so this file is portable, not tied to one sandbox.
    _browser = _pw.chromium.launch()
except Exception:
    # This sandboxed dev environment pre-installs Chromium under a fixed
    # path (see the repo's own environment notes) at a browser revision this
    # pinned playwright version doesn't recognize by default -- fall back to
    # it explicitly rather than failing outright.
    _browser = _pw.chromium.launch(executable_path=CHROMIUM_PATH)


def new_page(width=1280, height=900):
    """A fresh browser context/page at the given viewport, with console
    errors and page errors collected onto page._console_errors."""
    context = _browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page._console_errors = errors
    return context, page


def load(page, route="#/today", wait_selector=".pick-card, .empty-state, .prop-row"):
    page.goto(f"{BASE}/index.html{route}")
    try:
        page.wait_for_selector(wait_selector, timeout=20000, state="attached")
    except Exception:
        pass  # some routes (Performance, empty Today) legitimately have none of these
    page.wait_for_timeout(150)  # let render settle


try:
    # ── 1. General: page loads, real title, zero console errors ──────────
    head("1. General: index.html loads via the real docs/ build, title is "
         "correct, DATA actually populates (not stuck on a loading/error "
         "state), and the initial load produces zero console errors.")
    ctx, page = new_page()
    load(page)
    check(page.title() == "Full Count", "page <title> is 'Full Count'", f"got {page.title()!r}")
    data_ok = page.evaluate("() => typeof DATA === 'object' && DATA !== null && Array.isArray(DATA.props)")
    check(data_ok, "DATA loaded and DATA.props is a real array (data.json actually fetched/parsed)")
    check(len(page._console_errors) == 0, "zero console errors on initial load",
          f"errors: {page._console_errors}")
    ctx.close()

    # ── 2. Routing: all 5 real routes navigate correctly ──────────────────
    head("2. Routing/Filters: each of the 5 real nav routes (today/props/games/"
         "performance/watchlist) shows the right page div and marks the right "
         "nav link active, with zero console errors along the way.")
    ctx, page = new_page()
    load(page)
    for route in ["today", "props", "games", "performance", "watchlist"]:
        page.evaluate(f"() => {{ location.hash = '#/{route}'; }}")
        page.wait_for_timeout(200)
        visible = page.eval_on_selector(f"#page-{route}", "el => !el.hidden")
        check(visible, f"#page-{route} is visible after navigating to #/{route}")
        # Performance moved to the header icon (#performance-link), not
        # .main-nav (UX decision, 2026-08-26) -- the other 4 routes are
        # still real .main-nav pills.
        sel = "#performance-link" if route == "performance" else f'.main-nav a[data-route="{route}"]'
        active = page.eval_on_selector(sel, "el => el.classList.contains('active')")
        check(active, f"nav link for {route} carries the active class")
    check(len(page._console_errors) == 0, "zero console errors across all 5 route navigations",
          f"errors: {page._console_errors}")

    head("2b. General: an unrecognized hash route falls back to Today (never "
         "a blank page or a broken route).")
    page.evaluate("() => { location.hash = '#/totally-not-a-real-route'; }")
    page.wait_for_timeout(200)
    visible_today = page.eval_on_selector("#page-today", "el => !el.hidden")
    check(visible_today, "an unrecognized hash route falls back to showing #page-today")
    ctx.close()

    # ── 3. Filters: multi-select deep link ────────────────────────────────
    head("3. Filters: a deep link with a comma-separated multi-select family "
         "list (?family=strikeouts,home_runs) actually applies both, and "
         "Clear all resets it.")
    ctx, page = new_page()
    load(page, "#/props?family=strikeouts,home_runs")
    page.wait_for_timeout(200)
    families = page.evaluate("() => [...filters.families]")
    check(set(families) == {"strikeouts", "home_runs"},
          "filters.families contains exactly the two families from the deep link",
          f"got {families}")
    clear_visible = page.eval_on_selector("#f-clear-all", "el => !el.hidden")
    check(clear_visible, "Clear all button is visible once a filter is active")
    page.click("#f-clear-all")
    page.wait_for_timeout(150)
    families_after = page.evaluate("() => [...filters.families]")
    check(families_after == [], "Clear all empties filters.families", f"got {families_after}")
    ctx.close()

    # ── 4. Games: clicking a real game opens that game, game_pk survives ──
    head("4. Games: the Games route is a real research path -- clicking a "
         "schedule entry opens THAT game (game_pk carried through, never "
         "discarded), and a real 'See all N props for this game' link "
         "actually scopes All Props to that game.")
    ctx, page = new_page()
    load(page, "#/games")
    n_games = page.evaluate("() => (DATA.schedule || []).length")
    if n_games > 0:
        first_pk = page.evaluate("() => DATA.schedule[0].game_pk")
        page.evaluate(f"() => {{ location.hash = '#/games?game_pk={first_pk}'; }}")
        page.wait_for_timeout(250)
        selected = page.evaluate("() => selectedGamePk")
        check(selected == first_pk, "selectedGamePk matches the real game_pk from the URL",
              f"got {selected} want {first_pk}")
        see_all = page.query_selector('#page-games a[href^="#/props?game_pk="]')
        if see_all:
            href = see_all.get_attribute("href")
            check(f"game_pk={first_pk}" in href,
                  "the 'See all N props for this game' link is scoped to the real game_pk",
                  f"got href={href!r}")
            see_all.click()
            page.wait_for_timeout(250)
            game_pk_filter = page.evaluate("() => filters.gamePk")
            check(game_pk_filter == first_pk,
                  "clicking through actually sets filters.gamePk on the Props page",
                  f"got {game_pk_filter} want {first_pk}")
        else:
            check(True, "no props for this particular game (real, valid state) -- link "
                  "correctly omitted rather than shown broken")
    else:
        check(True, "no games in current schedule (stale/off-day fixture) -- route "
              "structure still verified above, real-slate check deferred")
    ctx.close()

    # ── 5. Cards + Detail: opening/closing the detail sheet ───────────────
    head("5. Cards/Detail: clicking a real pick-card opens the detail sheet "
         "as a real modal, with the honest section structure, and every "
         "close affordance (Escape, backdrop, close button) actually closes "
         "it.")
    ctx, page = new_page()
    load(page, "#/today")
    card = page.query_selector(".pick-card[data-open], .prop-row[data-open]")
    if card:
        card.click()
        page.wait_for_timeout(250)
        sheet_hidden = page.eval_on_selector("#detail-sheet", "el => el.hidden")
        check(not sheet_hidden, "detail sheet is no longer hidden after clicking a card")
        aria_modal = page.eval_on_selector(".detail-sheet-panel", "el => el.getAttribute('aria-modal')")
        check(aria_modal == "true", "detail sheet panel is a real aria-modal dialog")
        n_sections = page.eval_on_selector_all(".detail-section", "els => els.length")
        check(n_sections >= 1, "at least one real .detail-section rendered", f"got {n_sections}")
        # Escape closes it
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check(page.eval_on_selector("#detail-sheet", "el => el.hidden"),
              "Escape key closes the detail sheet")
        # backdrop click closes it
        card.click()
        page.wait_for_timeout(200)
        page.click(".detail-sheet-backdrop", force=True)
        page.wait_for_timeout(200)
        check(page.eval_on_selector("#detail-sheet", "el => el.hidden"),
              "clicking the backdrop closes the detail sheet")
        # close button closes it
        card.click()
        page.wait_for_timeout(200)
        page.click(".detail-close")
        page.wait_for_timeout(200)
        check(page.eval_on_selector("#detail-sheet", "el => el.hidden"),
              "the explicit close button closes the detail sheet")
        check(len(page._console_errors) == 0,
              "zero console errors across the full open/close cycle",
              f"errors: {page._console_errors}")
    else:
        check(False, "at least one clickable pick-card/prop-row exists on Today to test "
              "detail-sheet open/close against")
    ctx.close()

    # ── 6. My Board (watchlist): save, individual unsave, Clear All ───────
    # Real bug found in THIS test (release-candidate review, 2026-08-26):
    # the old Clear All check's fallback assertion was
    #   confirmed is True or page.evaluate("() => watchlist.size") >= 0
    # -- the second half is ALWAYS true (a Set's .size can never be
    # negative), so this never actually failed even when Clear All did
    # nothing. Root cause of why Clear All silently did nothing: Playwright
    # auto-DISMISSES confirm()/alert() dialogs unless a handler is
    # registered, and #mb-clear-all's own click handler is
    # `if (!confirm(...)) return;` -- so every prior run of this test
    # clicked Clear All, had the browser auto-dismiss the confirm(), and
    # the handler returned immediately without clearing anything. Fixed by
    # registering a real dialog-accept handler before clicking, and
    # removing the tautological fallback entirely.
    head("6a. My Board: saving a prop from the detail sheet's real star "
         "button adds it, and the real available unsave control -- opening "
         "that same saved row from My Board and clicking the detail "
         "sheet's star again -- removes exactly that one id, updates the "
         "nav badge, and updates the rendered page.")
    ctx, page = new_page()
    load(page, "#/today")
    card = page.query_selector(".pick-card[data-open], .prop-row[data-open]")
    if card:
        prop_id = card.get_attribute("data-open")
        card.click()
        page.wait_for_timeout(200)
        star_btn = page.query_selector("#detail-star")
        check(star_btn is not None, "detail sheet has a real #detail-star save button")
        if star_btn:
            pressed_before = page.eval_on_selector("#detail-star", "el => el.getAttribute('aria-pressed')")
            star_btn.click()
            page.wait_for_timeout(200)
            pressed_after = page.eval_on_selector("#detail-star", "el => el.getAttribute('aria-pressed')")
            check(pressed_before != pressed_after, "aria-pressed flips after clicking the star button",
                  f"before={pressed_before} after={pressed_after}")
            check(page.evaluate(f"() => watchlist.has({prop_id!r})"),
                  "the real prop id is actually present in the watchlist Set")
            count_hidden = page.eval_on_selector("#watchlist-count", "el => el.hidden")
            check(not count_hidden, "the nav My Board count badge becomes visible once >0 saved")
            count_text = page.eval_on_selector("#watchlist-count", "el => el.textContent")
            check(count_text == "1", "the nav badge shows the real count (1)", f"got {count_text!r}")
            page.keyboard.press("Escape")
            page.evaluate("() => { location.hash = '#/watchlist'; }")
            page.wait_for_timeout(250)
            check(page.evaluate(
                f"() => [...document.querySelectorAll('#page-watchlist [data-open]')]"
                f".some(b => b.dataset.open === {prop_id!r})"),
                "the saved prop actually renders on the My Board page")
            # THE ACTUAL AVAILABLE UNSAVE CONTROL: My Board rows have no
            # inline star (propRow() is a single <button data-open>, see its
            # own comment on why a nested real <button> would be invalid,
            # inaccessible HTML) -- unsaving goes through opening the row
            # (which opens the SAME detail sheet) and clicking its star
            # again. This IS the real, only individual-unsave path; testing
            # it is testing real UX, not inventing a shortcut.
            #
            # Scoped to #page-watchlist specifically -- real bug found
            # writing this test: navigating away from Today does not clear
            # its DOM, only hides it ([hidden] on the container), so an
            # unscoped `[data-open="ID"]` selector can match the SAME real
            # prop's card still sitting hidden on #page-today and return
            # THAT element (wrong page, invisible) instead of the one on
            # My Board.
            try:
                row = page.wait_for_selector(f'#page-watchlist [data-open="{prop_id}"]',
                                              state="visible", timeout=8000)
            except Exception:
                row = None
            check(row is not None, "the saved row on My Board is itself openable")
            if row:
                row.click()
                page.wait_for_timeout(200)
                unstar_btn = page.query_selector("#detail-star")
                pressed_mid = page.eval_on_selector("#detail-star", "el => el.getAttribute('aria-pressed')")
                check(pressed_mid == "true", "re-opening the saved prop shows it as still saved "
                      "(aria-pressed=true) before unsaving", f"got {pressed_mid!r}")
                unstar_btn.click()
                page.wait_for_timeout(200)
                pressed_final = page.eval_on_selector("#detail-star", "el => el.getAttribute('aria-pressed')")
                check(pressed_final == "false", "REGRESSION GUARD: clicking the star a second time "
                      "un-presses it", f"got {pressed_final!r}")
                check(not page.evaluate(f"() => watchlist.has({prop_id!r})"),
                      "REGRESSION GUARD: that exact id is gone from the real watchlist Set after unsaving",
                      f"watchlist contents: {page.evaluate('() => [...watchlist]')}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
                check(not page.evaluate(
                    f"() => [...document.querySelectorAll('#page-watchlist [data-open]')]"
                    f".some(b => b.dataset.open === {prop_id!r})"),
                    "REGRESSION GUARD: the unsaved prop no longer renders on the "
                    "re-rendered My Board page")
                count_hidden_after = page.eval_on_selector("#watchlist-count", "el => el.hidden")
                check(count_hidden_after, "REGRESSION GUARD: the nav badge hides again once the "
                      "board is empty (0 saved)")
    else:
        check(False, "at least one clickable card exists to test My Board individual unsave against")
    ctx.close()

    head("6b. My Board: Clear All (with a real dialog-accept handler, not the "
         "auto-dismiss Playwright applies by default) removes every saved prop, "
         "the nav badge hides, localStorage reflects the empty state, and it "
         "STAYS empty after a real page reload (persistence, not just in-memory "
         "state).")
    ctx, page = new_page()
    load(page, "#/today")
    cards = page.query_selector_all(".pick-card[data-open], .prop-row[data-open]")
    ids = []
    for c in cards:
        pid = c.get_attribute("data-open")
        if pid not in ids:
            ids.append(pid)
        if len(ids) >= 2:
            break
    if len(ids) >= 2:
        for pid in ids:
            page.evaluate(f"(id) => toggleWatch(id)", pid)
        page.wait_for_timeout(150)
        check(page.evaluate("() => watchlist.size") == len(ids),
              f"REGRESSION GUARD setup: {len(ids)} distinct real props are actually saved before "
              "testing Clear All", f"watchlist={page.evaluate('() => [...watchlist]')}")
        page.evaluate("() => { location.hash = '#/watchlist'; }")
        page.wait_for_timeout(250)
        # Register the accept handler BEFORE the click that triggers confirm() --
        # this is the exact fix for the false-positive this check replaces.
        page.once("dialog", lambda dialog: dialog.accept())
        clear_btn = page.query_selector("#mb-clear-all")
        check(clear_btn is not None, "the real #mb-clear-all button exists")
        if clear_btn:
            clear_btn.click()
            page.wait_for_timeout(300)
            check(page.evaluate("() => watchlist.size") == 0,
                  "REGRESSION GUARD: watchlist.size is genuinely 0 after Clear All is confirmed "
                  "(this is the exact assertion the old tautological fallback never actually made)",
                  f"got size={page.evaluate('() => watchlist.size')}")
            check(page.eval_on_selector_all("#page-watchlist [data-open]", "els => els.length") == 0,
                  "no saved rows remain rendered on My Board")
            count_hidden = page.eval_on_selector("#watchlist-count", "el => el.hidden")
            check(count_hidden, "the nav badge is hidden with 0 saved")
            stored = page.evaluate("() => localStorage.getItem('fc_watchlist_v1')")
            check(stored is not None and json.loads(stored) == [],
                  "REGRESSION GUARD: localStorage's real persisted value is an empty array, "
                  "not just the in-memory Set", f"got stored={stored!r}")
            # Real reload -- proves persistence, not just that the in-memory
            # Set was cleared for the current page load.
            page.reload()
            page.wait_for_selector(".pick-card, .empty-state, .prop-row", timeout=20000)
            page.wait_for_timeout(200)
            check(page.evaluate("() => watchlist.size") == 0,
                  "REGRESSION GUARD: after a real browser reload, the watchlist is STILL empty -- "
                  "the clear genuinely persisted, it wasn't just cleared in memory for this session",
                  f"got size={page.evaluate('() => watchlist.size')}")
            page.evaluate("() => { location.hash = '#/watchlist'; }")
            page.wait_for_timeout(250)
            check(page.eval_on_selector_all("#page-watchlist [data-open]", "els => els.length") == 0,
                  "My Board still renders as empty after reload, not repopulated from stale state")
    else:
        check(False, "the current slate has at least 2 distinct real props to test Clear All "
              "against (needs >=2 to prove it clears more than one)")
    ctx.close()

    # ── 7. Parlay: Suggested Parlay never shows a broken/fabricated price ─
    head("7. Parlay: if a Suggested Parlay section renders on Today, every "
         "leg shows a real priced value (never a raw undefined/NaN), and "
         "the combined figure is honestly labeled, not presented as exact.")
    ctx, page = new_page()
    load(page, "#/today")
    parlay = page.query_selector(".parlay-card")
    if parlay:
        text = parlay.inner_text()
        check("undefined" not in text and "NaN" not in text,
              "no leg/combined price renders as literal undefined/NaN", f"text snippet: {text[:200]!r}")
        check("Estimated" in text or "combined" in text.lower(),
              "the combined odds figure carries honest 'Estimated' framing, not a bare number "
              "presented as exact", f"text snippet: {text[:300]!r}")
    else:
        check(True, "no Suggested Parlay rendered for the current slate (valid empty state, "
              "e.g. too few qualifying legs) -- nothing to check")
    ctx.close()

    # ── 8. Mobile: 375 / 390 / 430 + one short-height viewport ────────────
    for w, h, label in [(375, 812, "iPhone SE/mini width"), (390, 844, "iPhone 12/13/14 width"),
                         (430, 932, "iPhone Pro Max width"), (375, 600, "short-height viewport")]:
        head(f"8. Mobile ({w}x{h}, {label}): UX decision (2026-08-26) -- Today/Props/"
             "Games/My Board are ALL directly reachable with zero horizontal nav-scroll "
             "at this width (My Board is a primary destination and must never sit behind "
             "a swipe), Performance stays one tap away via the header icon, no page-level "
             "horizontal scroll, the detail sheet fits the viewport, and the props filter "
             "bar is actually sticky.")
        ctx, page = new_page(w, h)
        load(page, "#/today")
        # No page-level horizontal overflow anywhere on Today.
        overflow_x = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        check(overflow_x <= 1, f"no horizontal page overflow at {w}px (Today)", f"got overflow={overflow_x}")
        # REGRESSION GUARD (2026-08-26 UX decision): all 4 primary nav
        # destinations -- not just "not clipped," but actually WITHIN the
        # viewport bounds at the nav's own default (unscrolled) position --
        # are simultaneously reachable with no horizontal nav-scroll needed.
        # This is the exact real gap the earlier 5-item row had: "not
        # clipped" alone was true even when My Board sat off-screen, only
        # reachable via a horizontal swipe within the nav strip.
        nav_scroll_left = page.evaluate("() => document.querySelector('.main-nav').scrollLeft")
        check(nav_scroll_left == 0, "the nav's default scroll position is 0 (nothing is "
              "pre-scrolled to hide the start of the list)", f"got scrollLeft={nav_scroll_left}")
        primary_routes = ["today", "props", "games", "watchlist"]
        for route in primary_routes:
            box = page.evaluate(
                f"() => document.querySelector('.main-nav a[data-route=\"{route}\"]')"
                ".getBoundingClientRect()")
            check(box["width"] > 0, f"'{route}' nav destination has a nonzero rendered width",
                  f"got {box}")
            check(box["left"] >= 0 and box["right"] <= w,
                  f"REGRESSION GUARD: '{route}' nav destination is fully within the "
                  f"{w}px viewport with zero nav scroll -- not clipped, not off-screen, "
                  "not reachable only via a horizontal swipe", f"got box={box} viewport={w}")
        # "My Board" nav label specifically renders without CSS ellipsis-clipping.
        watch_link = page.query_selector('.main-nav a[data-route="watchlist"]')
        clipped = page.evaluate(
            "(el) => el.scrollWidth > el.clientWidth + 1", watch_link)
        check(not clipped, "'My Board' nav label is not clipped/ellipsis-truncated at this width",
              f"scrollWidth vs clientWidth mismatch: {clipped}")
        # Performance: no longer a primary-row pill, but still one tap away via
        # the always-visible header icon -- verify it's real, present, and links
        # to the real route (not silently buried/removed).
        perf_link = page.query_selector("#performance-link")
        check(perf_link is not None, "the Performance header icon link exists")
        if perf_link:
            perf_box = page.evaluate("(el) => el.getBoundingClientRect()", perf_link)
            check(perf_box["width"] > 0 and perf_box["left"] >= 0 and perf_box["right"] <= w,
                  "the Performance header icon is itself fully visible within the viewport, "
                  "not clipped off the header row", f"got box={perf_box} viewport={w}")
            href = perf_link.get_attribute("href")
            check(href == "#/performance", "the Performance icon links to the real route",
                  f"got href={href!r}")
        # Props page: filter bar sticky + no overflow.
        page.evaluate("() => { location.hash = '#/props'; }")
        page.wait_for_timeout(250)
        overflow_x_props = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        check(overflow_x_props <= 1, f"no horizontal page overflow at {w}px (All Props)",
              f"got overflow={overflow_x_props}")
        fb = page.query_selector(".filter-bar")
        if fb:
            pos = page.evaluate("(el) => getComputedStyle(el).position", fb)
            check(pos == "sticky", "the props filter bar uses position:sticky at this width",
                  f"got position={pos}")
        # Detail sheet fits the viewport (no horizontal overflow once opened).
        # A card handle queried right after a hash change can outlive its own
        # render pass under load (large real data.json, several contexts open
        # in this same run) -- wait for a real VISIBLE card instead of just
        # sleeping a fixed amount, and never let one missing card crash the
        # whole suite.
        page.evaluate("() => { location.hash = '#/today'; }")
        card = None
        try:
            card = page.wait_for_selector(
                "#page-today .pick-card[data-open], #page-today .prop-row[data-open]",
                timeout=8000, state="visible")
        except Exception:
            card = None
        if card:
            try:
                card.click(timeout=8000)
                page.wait_for_timeout(250)
                panel_w = page.eval_on_selector(".detail-sheet-panel", "el => el.getBoundingClientRect().width")
                check(panel_w <= w + 1, "detail sheet panel width fits within the viewport",
                      f"panel width={panel_w} viewport={w}")
                overflow_x_detail = page.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
                check(overflow_x_detail <= 1, "no horizontal overflow with the detail sheet open",
                      f"got overflow={overflow_x_detail}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
            except Exception as exc:
                check(False, "could open the detail sheet from a mobile viewport card", str(exc))
        else:
            check(False, "at least one visible card exists on Today to test the mobile "
                  "detail sheet against")
        check(len(page._console_errors) == 0, f"zero console errors at {w}x{h}",
              f"errors: {page._console_errors}")
        ctx.close()

    # ── 9. Source/docs parity sanity (already enforced in test_build_dashboard.py,
    #      re-verified here for good measure since this suite serves docs/ directly) ─
    head("9. General: the docs/ build actually served (index.html/app.js/app.css) is "
         "the real deployed shell -- byte parity with dashboard/static/ is the "
         "authoritative check in test_build_dashboard.py's StaticSourceParityTests; "
         "here we just confirm this suite is really exercising app.js (not a stale "
         "cached copy) by checking a function only the current app.js defines.")
    ctx, page = new_page()
    load(page)
    has_fn = page.evaluate("() => typeof gamePickSections === 'function'")
    check(has_fn, "the served app.js is the current build (gamePickSections() is defined -- "
          "a Part 2 addition, so this fails if an old cached bundle were served instead)")
    ctx.close()

finally:
    _browser.close()
    _pw.stop()
    _httpd.shutdown()

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
