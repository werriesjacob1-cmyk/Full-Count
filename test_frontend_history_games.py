#!/usr/bin/env python3
"""Execute the History live-refresh and Games price-state contracts in Node.

History (2026-09-24): history.json was fetched once per session and every card
read only its durable grade, so cashed picks stayed "Ungraded" until reload.
Games (2026-09-24): every no-price state rendered as "not priced", and a 71%
research projection FanDuel was not posting led "Best Overall Read".
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "dashboard", "static", "app.js")

# A minimal DOM: one element per id, and a History element whose
# querySelectorAll("details.history-day") reflects its current innerHTML plus
# any open/closed toggles the "reader" made since the last render.
PRELUDE = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const elements = {};
function element(id) {
  if (!elements[id]) {
    const el = { id, innerHTML: "", _toggled: {} };
    el.querySelectorAll = (sel) => {
      if (sel !== "details.history-day") return [];
      const out = [];
      const re = /<details class="history-day" data-date="([^"]*)" (open)?>/g;
      let m;
      while ((m = re.exec(el.innerHTML))) {
        const date = m[1];
        const open = Object.hasOwn(el._toggled, date) ? el._toggled[date] : !!m[2];
        out.push({ open, dataset: { date } });
      }
      return out;
    };
    elements[id] = el;
  }
  return elements[id];
}
const document = {
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  getElementById: element, visibilityState: "visible",
  createElement() {
    let text = "";
    return {
      set textContent(v) { text = String(v); },
      get textContent() { return text; },
      get innerHTML() {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      },
    };
  },
};
const fetchQueue = [];
const fetchLog = [];
function fetchStub(url) {
  fetchLog.push(url.split("?")[0]);
  return new Promise(resolve => fetchQueue.push({ url, resolve }));
}
const scroll = { y: 0, restored: [] };
const context = {
  console, document,
  window: { get scrollY() { return scroll.y; }, scrollTo(x, y) { scroll.restored.push(y); scroll.y = y; } },
  localStorage: { getItem() { return null; }, setItem() {} },
  setTimeout, clearTimeout, setInterval() {}, fetch: fetchStub,
  Intl, Date, Map, Set, Object, Array, JSON, Math, Number, String, Promise,
};
vm.createContext(context);
vm.runInContext(source, context);
context.__respond = (i, body) => fetchQueue[i].resolve({ ok: true, json: async () => body });
context.__fetchQueue = fetchQueue;
context.__fetchLog = fetchLog;
context.__scroll = scroll;
context.__el = element;
const flush = () => new Promise(r => setTimeout(r, 0));
context.__flush = flush;
"""

RUN = r"""
(async () => {
  const result = await vm.runInContext(`(async () => { %s })()`, context);
  process.stdout.write(JSON.stringify(result));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

HISTORY_FIXTURES = r"""
const ID_A = "fc2:824951:player-545361:hits_runs_rbis:1:over";
const ID_B = "fc2:824951:player-660271:hits:1:over";
const ID_C = "fc2:824951:player-592450:total_bases:2:over";
function pick(id, extra) {
  return Object.assign({ id, name: "N " + id.slice(-12), prop: "Over 0.5 Hits", matchup: "A @ B",
    hit_probability: 0.7, market_odds: -150, grade: null, settlement_state: null, why: [] }, extra || {});
}
function historyDoc(generatedAt, picks, extraDays) {
  const graded = picks.filter(p => p.grade === "hit" || p.grade === "miss");
  const hits = graded.filter(p => p.grade === "hit").length;
  return { schema_version: 1, generated_at: generatedAt, retention_days: 45,
    days: [{ date: "2026-09-23", picks, hits, misses: graded.length - hits,
             hit_rate: graded.length ? hits / graded.length : null }].concat(extraDays || []) };
}
function liveDoc(stamp, props) {
  return { updated_at: stamp, grades_updated_at: stamp, prices_updated_at: stamp, props };
}
function settlement(state, authority, at) {
  return { settlement_state: state, settlement_authority: authority, settlement_observed_at: at,
           settlement_source: "fixture", result_actual: state.includes("hit") ? 1 : 0,
           result_reason: "fixture" };
}
async function openHistory(doc) {
  route = "history";
  const p = renderHistory();
  __respond(__fetchQueue.length - 1, doc);
  await p; await __flush();
  return __el("page-history");
}
"""


def run_node(body: str) -> dict:
    script = PRELUDE + (RUN % (HISTORY_FIXTURES + body).replace("`", "\\`").replace("${", "\\${"))
    completed = subprocess.run(
        ["node", "-e", script, APP], cwd=ROOT, check=False, text=True, capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class HistoryLiveStateTests(unittest.TestCase):
    def test_provisional_green_and_red_show_awaiting_final_and_are_not_counted(self):
        r = run_node(r"""
          ingestLiveDocument(liveDoc("2026-09-24T02:00:00Z", {
            [ID_A]: settlement("provisional_hit", "live_observation", "2026-09-24T02:00:00Z"),
            [ID_B]: settlement("provisional_miss", "live_observation", "2026-09-24T02:00:00Z"),
          }));
          const el = await openHistory(historyDoc("2026-09-24T01:00:00Z",
            [pick(ID_A), pick(ID_B), pick(ID_C, { grade: "hit" })]));
          return { html: el.innerHTML };
        """)
        html = r["html"]
        self.assertIn("Cashed — awaiting official final", html)
        self.assertIn("Trending miss — awaiting official final", html)
        # Official record counts the one durable hit only.
        self.assertIn('<span class="history-day-record">1-0</span>', html)
        self.assertIn("+ 1 cashed · 1 trending miss", html)

    def test_official_final_in_live_is_labelled_pending_record_not_counted(self):
        r = run_node(r"""
          ingestLiveDocument(liveDoc("2026-09-24T04:00:00Z", {
            [ID_A]: settlement("hit", "official_final", "2026-09-24T04:00:00Z"),
          }));
          const el = await openHistory(historyDoc("2026-09-24T01:00:00Z", [pick(ID_A)]));
          return { html: el.innerHTML };
        """)
        self.assertIn("Hit ✓ · Final, record updating", r["html"])
        self.assertIn('<span class="history-day-record">ungraded</span>', r["html"])
        self.assertIn("1 final, record updating", r["html"])

    def test_same_session_refresh_moves_to_official_final_without_reload(self):
        r = run_node(r"""
          ingestLiveDocument(liveDoc("2026-09-24T02:00:00Z", {
            [ID_A]: settlement("provisional_hit", "live_observation", "2026-09-24T02:00:00Z"),
          }));
          const el = await openHistory(historyDoc("2026-09-24T01:00:00Z", [pick(ID_A)]));
          const before = el.innerHTML;
          const p = pollHistory();
          __respond(__fetchQueue.length - 1,
            historyDoc("2026-09-24T05:00:00Z", [pick(ID_A, { grade: "hit" })]));
          await p; await __flush();
          return { before, after: el.innerHTML, fetches: __fetchLog };
        """)
        self.assertIn("Cashed — awaiting official final", r["before"])
        self.assertIn("Hit ✓", r["after"])
        self.assertNotIn("awaiting official final", r["after"])
        self.assertIn('<span class="history-day-record">1-0</span>', r["after"])
        self.assertEqual(r["fetches"], ["history.json", "history.json"])

    def test_live_settlement_arriving_via_pollLive_updates_open_history(self):
        r = run_node(r"""
          DATA = { generated_at: "2026-09-24T01:00:00Z", props: [], summary: {} };
          indexProps();
          const el = await openHistory(historyDoc("2026-09-24T01:00:00Z", [pick(ID_A)]));
          const before = el.innerHTML;
          HISTORY_FETCHED_AT = Date.now();  // no history.json refetch in this step
          const p = pollLive();
          __respond(__fetchQueue.length - 1, liveDoc("2026-09-24T02:10:00Z", {
            [ID_A]: settlement("provisional_hit", "live_observation", "2026-09-24T02:10:00Z"),
          }));
          await p; await __flush();
          return { before, after: el.innerHTML };
        """)
        self.assertIn("Ungraded", r["before"])
        self.assertIn("Cashed — awaiting official final", r["after"])

    def test_final_correction_replaces_earlier_final(self):
        r = run_node(r"""
          const el = await openHistory(historyDoc("2026-09-24T05:00:00Z", [pick(ID_A, { grade: "hit" })]));
          const before = el.innerHTML;
          const p = pollHistory();
          __respond(__fetchQueue.length - 1, historyDoc("2026-09-24T07:00:00Z",
            [pick(ID_A, { grade: "void", settlement_state: "void" })]));
          await p; await __flush();
          return { before, after: el.innerHTML };
        """)
        self.assertIn("Hit ✓", r["before"])
        self.assertIn(">Void<", r["after"])
        self.assertIn('<span class="history-day-record">ungraded</span>', r["after"])

    def test_stale_cached_and_out_of_order_responses_never_move_backwards(self):
        r = run_node(r"""
          const el = await openHistory(historyDoc("2026-09-24T05:00:00Z", [pick(ID_A, { grade: "hit" })]));
          // Two refreshes in flight; the NEWER request answers first.
          const older = pollHistory();
          const newer = pollHistory();
          const n = __fetchQueue.length;
          __respond(n - 1, historyDoc("2026-09-24T07:00:00Z", [pick(ID_A, { grade: "miss" })]));
          await newer; await __flush();
          // The slower, earlier request now lands with an older document.
          __respond(n - 2, historyDoc("2026-09-24T06:00:00Z", [pick(ID_A, { grade: "hit" })]));
          await older; await __flush();
          const afterRace = el.innerHTML;
          // A cached copy of the very first document also arrives later.
          const cached = pollHistory();
          __respond(__fetchQueue.length - 1, historyDoc("2026-09-24T05:00:00Z", [pick(ID_A, { grade: "hit" })]));
          await cached; await __flush();
          // Same generated_at, different content: equal is not newer.
          const equal = pollHistory();
          __respond(__fetchQueue.length - 1, historyDoc("2026-09-24T07:00:00Z", [pick(ID_A, { grade: "hit" })]));
          await equal; await __flush();
          return { afterRace, final: el.innerHTML, generated: HISTORY.generated_at };
        """)
        self.assertIn(">Miss<", r["afterRace"])
        self.assertIn(">Miss<", r["final"])
        self.assertEqual(r["generated"], "2026-09-24T07:00:00Z")

    def test_provisional_never_overrides_durable_or_newer_official_final(self):
        r = run_node(r"""
          // Official final first, then an OLDER provisional observation.
          ingestLiveDocument(liveDoc("2026-09-24T04:00:00Z", {
            [ID_B]: settlement("miss", "official_final", "2026-09-24T04:00:00Z"),
          }));
          ingestLiveDocument(liveDoc("2026-09-24T04:05:00Z", {
            [ID_B]: settlement("provisional_hit", "live_observation", "2026-09-24T04:05:00Z"),
            [ID_A]: settlement("provisional_miss", "live_observation", "2026-09-24T04:05:00Z"),
          }));
          const el = await openHistory(historyDoc("2026-09-24T05:00:00Z",
            [pick(ID_A, { grade: "hit" }), pick(ID_B)]));
          return { a: historyGradeChip(HISTORY.days[0].picks[0]),
                   b: historyGradeChip(HISTORY.days[0].picks[1]) };
        """)
        self.assertEqual(r["a"], '<span class="chip chip-grade-hit">Hit ✓</span>')
        self.assertIn("Miss · Final, record updating", r["b"])
        self.assertNotIn("Cashed", r["b"])

    def test_stable_id_join_only_never_by_name(self):
        r = run_node(r"""
          ingestLiveDocument(liveDoc("2026-09-24T02:00:00Z", {
            [ID_A]: settlement("provisional_hit", "live_observation", "2026-09-24T02:00:00Z"),
          }));
          // Same player name and prop as ID_A's live row, different game id.
          const other = pick("fc2:823087:player-545361:hits_runs_rbis:1:over",
                             { name: "N " + ID_A.slice(-12) });
          const noId = pick(ID_A); delete noId.id;
          return { other: historyGradeChip(other), noId: historyGradeChip(noId) };
        """)
        self.assertIn("Ungraded", r["other"])
        self.assertIn("Ungraded", r["noId"])

    def test_route_switch_and_open_sections_and_scroll_are_preserved(self):
        r = run_node(r"""
          const day2 = { date: "2026-09-22", picks: [pick(ID_C, { grade: "miss" })], hits: 0, misses: 1, hit_rate: 0 };
          const el = await openHistory(historyDoc("2026-09-24T01:00:00Z", [pick(ID_A)], [day2]));
          const firstRender = el.innerHTML;
          // The reader closes the newest day, opens the older one, scrolls.
          el._toggled = { "2026-09-23": false, "2026-09-22": true };
          __scroll.y = 640;
          // Leave and come back: no reload, cached document re-renders.
          route = "today"; route = "history";
          HISTORY_FETCHED_AT = Date.now();
          await renderHistory();
          el._toggled = {};
          const afterReturn = el.innerHTML;
          el._toggled = { "2026-09-23": false, "2026-09-22": true };
          const p = pollHistory();
          __respond(__fetchQueue.length - 1, historyDoc("2026-09-24T03:00:00Z",
            [pick(ID_A, { grade: "hit" })], [day2]));
          await p; await __flush();
          return { firstRender, afterReturn, afterRefresh: el.innerHTML,
                   scrollY: __scroll.y, fetches: __fetchLog.length };
        """)
        self.assertIn('data-date="2026-09-23" open>', r["firstRender"])
        self.assertIn('data-date="2026-09-22" >', r["firstRender"])
        for key in ("afterReturn", "afterRefresh"):
            self.assertIn('data-date="2026-09-23" >', r[key])
            self.assertIn('data-date="2026-09-22" open>', r[key])
        self.assertEqual(r["scrollY"], 640)
        self.assertEqual(r["fetches"], 2)

    def test_rendering_never_mutates_history_or_live_evidence(self):
        r = run_node(r"""
          ingestLiveDocument(liveDoc("2026-09-24T02:00:00Z", {
            [ID_A]: settlement("provisional_hit", "live_observation", "2026-09-24T02:00:00Z"),
          }));
          const doc = historyDoc("2026-09-24T01:00:00Z", [pick(ID_A), pick(ID_C, { grade: "hit" })]);
          const docBefore = JSON.stringify(doc);
          const liveBefore = JSON.stringify(LIVE_CACHE.props);
          const el = await openHistory(doc);
          renderHistoryContent(el);
          historyPendingText(HISTORY.days[0]);
          return { same: JSON.stringify(HISTORY) === docBefore,
                   liveSame: JSON.stringify(LIVE_CACHE.props) === liveBefore,
                   hits: HISTORY.days[0].hits, misses: HISTORY.days[0].misses };
        """)
        self.assertTrue(r["same"])
        self.assertTrue(r["liveSame"])
        self.assertEqual((r["hits"], r["misses"]), (1, 0))

    def test_failed_refresh_keeps_current_page(self):
        r = run_node(r"""
          const el = await openHistory(historyDoc("2026-09-24T01:00:00Z", [pick(ID_A, { grade: "hit" })]));
          const p = pollHistory();
          __fetchQueue[__fetchQueue.length - 1].resolve({ ok: false, status: 503 });
          await p; await __flush();
          return { html: el.innerHTML, error: HISTORY_ERROR };
        """)
        self.assertFalse(r["error"])
        self.assertIn("Hit ✓", r["html"])


GAMES_SETUP = r"""
SHOW_UNVERIFIED = true;
const TROUT = "fc2:823087:player-545361:hits_runs_rbis:1:over";
function prop(extra) {
  return Object.assign({ id: TROUT, name: "Mike Trout", prop: "Over 0.5 Hits+Runs+RBIs",
    game_pk: 823087, game_state: "pregame", hit_probability: 0.7095, market_odds: -475 }, extra || {});
}
function board(p) { DATA = { generated_at: "2026-09-24T01:05:53Z", props: [p], summary: {} }; indexProps(); }
const copy = { id: TROUT, name: "Mike Trout", prop: "Over 0.5 Hits+Runs+RBIs",
               hit_probability: 0.7095, market_odds: -475 };
"""


class GamesPriceStateTests(unittest.TestCase):
    def test_trout_not_posted_is_a_labelled_research_projection(self):
        r = run_node(GAMES_SETUP + r"""
          board(prop({ market_odds: null, market_fetch_state: "NOT_POSTED",
                       market_fetch_checked_at: "2026-09-24T01:46:06Z" }));
          return { line: gamePickLine(copy) };
        """)
        self.assertIn("research projection", r["line"])
        self.assertIn("Not yet posted on FanDuel", r["line"])
        self.assertNotIn("not priced", r["line"])
        self.assertNotIn("475", r["line"])  # the frozen copy's price is never shown

    def test_each_unpriced_state_has_its_own_wording(self):
        r = run_node(GAMES_SETUP + r"""
          const out = {};
          for (const state of ["NOT_POSTED", "FETCH_FAILED", "IN_PLAY", null]) {
            board(prop({ market_odds: null, market_fetch_state: state }));
            out[String(state)] = { game: gamePickLine(copy), card: marketBlock(PROPS_BY_ID.get(TROUT)),
                                   sheet: priceFreshnessState(PROPS_BY_ID.get(TROUT)).label };
          }
          return out;
        """)
        expected = {"NOT_POSTED": "Not yet posted on FanDuel", "FETCH_FAILED": "FanDuel check failed",
                    "IN_PLAY": "No pregame price captured", "null": "No FanDuel price at this line"}
        for state, text in expected.items():
            with self.subTest(state=state):
                self.assertIn(text, r[state]["game"])
                self.assertIn(text, r[state]["card"])  # Today/Props card agrees with Games
                self.assertEqual(r[state]["sheet"], text)  # and the detail sheet
                self.assertNotIn("not priced", r[state]["game"])

    def test_priced_line_shows_its_own_exact_line_price(self):
        r = run_node(GAMES_SETUP + r"""
          board(prop({ market_odds: -400, market_fetch_state: "MATCHED" }));
          return { line: gamePickLine(copy) };
        """)
        self.assertIn("FanDuel -400", r["line"])
        self.assertNotIn("research projection", r["line"])
        self.assertIn("game-pick-priced", r["line"])

    def test_started_game_withholds_price_and_line_moved_is_unchanged(self):
        r = run_node(GAMES_SETUP + r"""
          board(prop({ game_state: "live" }));
          const started = gamePickLine(copy);
          board(prop({ market_odds: null, market_fetch_state: "LINE_MOVED", market_posted_line: 1.5 }));
          return { started, moved: gamePickLine(copy) };
        """)
        self.assertIn("Pregame read · game underway", r["started"])
        self.assertNotIn("475", r["started"])
        self.assertIn("Line moved · FanDuel now Over 1.5", r["moved"])

    def test_highlight_with_unknown_id_never_binds_to_another_games_prop_by_name(self):
        r = run_node(GAMES_SETUP + r"""
          board(prop({ market_odds: -300 }));  // same name+prop, id TROUT (game 823087)
          const otherGame = Object.assign({}, copy, { id: "fc2:824951:player-545361:hits_runs_rbis:1:over" });
          const legacy = Object.assign({}, copy); delete legacy.id;
          return { other: gamePickLine(otherGame), legacy: gamePickLine(legacy) };
        """)
        self.assertIn("No longer on the board", r["other"])
        self.assertNotIn("-300", r["other"])
        self.assertIn("FanDuel -300", r["legacy"])  # legacy id-less copies keep name fallback


if __name__ == "__main__":
    unittest.main(verbosity=2)
