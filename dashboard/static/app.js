"use strict";
/* Full Count — Phase 4 application shell.
   No framework, no build step (see dashboard/build_dashboard.py's module
   docstring for why) -- plain DOM rendering, keyed by each prop's real
   `id` field. One canonical DATA.props array; every view (Top Picks,
   Leans, Value, a single stat family, the watchlist) is a FILTER over
   that one array, never a separately-fetched or separately-duplicated
   list -- see build_payload() for the server-side half of this. */

// ══════════════════════════════════════════════════════════════════════
//  STATE
// ══════════════════════════════════════════════════════════════════════
let DATA = null;
let PROPS_BY_ID = new Map();
let route = "today";
let watchlist = new Set();
let watchSnapshot = {}; // id -> {status, odds, lineup_assumed} at time-of-star, for change detection
let filters = { search: "", family: "all", status: "all", evidence: "all", sort: "default" };
let lastPollStamp = null;
let lastFullFetchAt = 0;
let lastFocusedEl = null; // element to restore focus to when a modal sheet/dialog closes
let LIVE_CACHE = {
  updated_at: null, prices_updated_at: null, grades_updated_at: null,
  grades_checked_at: null, prices_checked_at: null, props: {},
};

const ROUTES = ["today", "props", "games", "performance", "watchlist"];
const LONGSHOT_PROB_CEILING = 0.35; // display-only split of the real "value" status into
                                    // Best Value (>=35% win prob) vs Longshots (<35%) --
                                    // NOT a model field. Direct spec: "Best Value... may
                                    // have lower hit probability" vs "Longshots... the
                                    // event remains unlikely." Both are recommendation_
                                    // status === "value"; this is presentation only.

// ══════════════════════════════════════════════════════════════════════
//  SMALL UTILITIES
// ══════════════════════════════════════════════════════════════════════
function $(sel, root) { return (root || document).querySelector(sel); }
function $all(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}
function fmtOdds(v) {
  if (v === null || v === undefined) return null;
  return v > 0 ? "+" + v : String(v);
}
function pct(v, digits) {
  if (v === null || v === undefined) return "—";
  return (v * 100).toFixed(digits ?? 0) + "%";
}
function pctBig(v) {
  if (v === null || v === undefined) return "—";
  return Math.round(v * 100) + "%";
}
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
function safeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
function safeGetJSON(k, fallback) {
  try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch (e) { return fallback; }
}

// Local-timezone-first date formatting -- direct instruction: "Stop
// assuming every user wants Eastern Time... display ET as secondary
// baseball-standard context." Every game_start on the payload is a real
// ISO-8601 UTC timestamp; Intl.DateTimeFormat with no explicit timeZone
// resolves to the VIEWER's own timezone automatically.
const ET_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
const LOCAL_TIME_FMT = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const LOCAL_DATE_FMT = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
function gameTimeLabel(iso) {
  if (!iso) return "Time TBD";
  const d = new Date(iso);
  if (isNaN(d)) return "Time TBD";
  const local = LOCAL_TIME_FMT.format(d), et = ET_FMT.format(d);
  // A large share of visitors ARE already in Eastern time -- showing
  // "7:05 PM · 7:05 PM ET" to every one of them is just clutter, not
  // "secondary baseball-standard context." Collapse to one value when
  // they're identical; keep both only when they actually differ.
  return local === et ? `${local} ET` : `${local} · ${et} ET`;
}
function _agoText(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return null;
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// ══════════════════════════════════════════════════════════════════════
//  EVIDENCE QUALITY — a direct, honest translation of the real reliability
//  grade generate_picks.attach_reliability() already computes from real
//  sample size (games/starts observed) -- NOT a new invented composite.
//  Direct instruction: "Avoid another vague Confidence score unless it has
//  a precise definition... only expose it if it has a defensible
//  interpretation." A/B/C/D and their floors are generate_picks.py's own
//  RELIABILITY_TIERS/PITCHER_STARTS_RELIABILITY_TIERS, restated in plain
//  language here, never re-derived or fuzzed.
// ══════════════════════════════════════════════════════════════════════
const EVIDENCE_QUALITY = {
  A: { label: "Strong evidence", tone: "strong", blurb: "Backed by a season-long real track record." },
  B: { label: "Solid evidence", tone: "solid", blurb: "A solid real sample, not yet a full season." },
  C: { label: "Developing evidence", tone: "developing", blurb: "A thinner sample — read this number as approximate." },
  D: { label: "Limited evidence", tone: "limited", blurb: "A very thin sample — this is barely more than a league base rate." },
};
function evidenceQuality(p) {
  return EVIDENCE_QUALITY[p.reliability] || null;
}

// ══════════════════════════════════════════════════════════════════════
//  RECOMMENDATION STATUS — display metadata for the four real states
// ══════════════════════════════════════════════════════════════════════
const STATUS_META = {
  top_pick: { label: "Top Pick", short: "TOP PICK" },
  lean: { label: "Lean", short: "LEAN" },
  value: { label: "Value", short: "VALUE" },
  neutral: { label: "No Strong Lean", short: "NEUTRAL" },
};
function isLongshot(p) {
  return p.recommendation_status === "value" && (p.hit_probability ?? 1) < LONGSHOT_PROB_CEILING;
}
function statusLabel(p) {
  if (isLongshot(p)) return "Longshot";
  return (STATUS_META[p.recommendation_status] || {}).label || "Unrated";
}
function statusChip(p) {
  const st = p.recommendation_status || "neutral";
  const label = isLongshot(p) ? "LONGSHOT" : (STATUS_META[st] || {}).short || "—";
  return `<span class="chip chip-${st}">${label}</span>`;
}
function lineupChip(p) {
  if (p.lineup_assumed === true) return `<span class="chip chip-lineup-assumed">Assumed Lineup</span>`;
  if (p.lineup_assumed === false) return `<span class="chip chip-lineup-confirmed">Confirmed Lineup</span>`;
  return "";
}
function staleChip(p) {
  return p.stale ? `<span class="chip chip-stale">Stale Data</span>` : "";
}
function evidenceChip(p) {
  const eq = evidenceQuality(p);
  return eq ? `<span class="chip chip-evidence-${eq.tone}">${eq.label}</span>` : "";
}
function settlementState(p) {
  const state = p.settlement_state || "open";
  return ["open", "provisional_hit", "provisional_miss", "hit", "miss", "void", "ungraded"].includes(state)
    ? state : "ungraded";
}
function gameState(p) {
  const state = p.game_state || "unknown";
  return ["pregame", "live", "delayed", "suspended", "postponed", "final", "cancelled", "unknown"].includes(state)
    ? state : "unknown";
}
function lifecycleState(p) {
  const settlement = settlementState(p);
  if (settlement !== "open") return settlement;
  return gameState(p);
}
function lifecycleClass(p) {
  const state = lifecycleState(p);
  if (state === "provisional_hit") return "lifecycle-hit";
  if (state === "provisional_miss") return "lifecycle-miss";
  return `lifecycle-${state}`;
}
function gradeChip(p) {
  const state = lifecycleState(p);
  if (state === "pregame") return "";
  if (state === "live") return `<span class="chip chip-grade-live">Live</span>`;
  if (state === "delayed") return `<span class="chip chip-grade-ungraded">Delayed</span>`;
  if (state === "suspended") return `<span class="chip chip-grade-ungraded">Suspended</span>`;
  if (state === "postponed") return `<span class="chip chip-grade-ungraded">Postponed</span>`;
  if (state === "cancelled") return `<span class="chip chip-grade-ungraded">Cancelled · Awaiting settlement</span>`;
  if (state === "unknown") return `<span class="chip chip-grade-ungraded">Status unavailable</span>`;
  if (state === "final") return `<span class="chip chip-grade-ungraded">Final · Awaiting grade</span>`;
  if (state === "provisional_hit") return `<span class="chip chip-grade-hit">Cashed · Awaiting final</span>`;
  if (state === "provisional_miss") return `<span class="chip chip-grade-miss">Trending miss · Awaiting final</span>`;
  if (state === "hit") return `<span class="chip chip-grade-hit">Hit ✓</span>`;
  if (state === "miss") return `<span class="chip chip-grade-miss">Miss</span>`;
  if (state === "void") return `<span class="chip chip-grade-void">Void</span>`;
  if (state === "ungraded") return `<span class="chip chip-grade-ungraded">Ungraded</span>`;
  return "";
}

// ══════════════════════════════════════════════════════════════════════
//  PLAIN-ENGLISH REASON TRANSLATION (ported from the pre-rebuild page --
//  genuinely valuable, unrelated to the redesign itself. Rewrites the
//  pipeline's own why[]/watchouts[] strings into readable sentences;
//  numbers always come straight from the data, an unrecognized bullet
//  passes through unchanged rather than being guessed at.)
// ══════════════════════════════════════════════════════════════════════
const PITCH_NAMES = {
  FF: "four-seam fastball", SI: "sinker", FC: "cutter", SL: "slider", ST: "sweeper",
  CU: "curveball", KC: "knuckle curve", CH: "changeup", FS: "splitter", FO: "forkball",
  SC: "screwball", KN: "knuckleball", EP: "eephus",
};
function pitchName(code) { return PITCH_NAMES[code] || code; }
const REASON_RULES = [
  [/^(.+?) scores off (.+?) \((away|home) SP\) in the (top|bottom) 1st: ([\d.]+)% \(shrunk, (\d+) starts\)$/,
   m => `${m[1]} have scored off ${m[2]} in the ${m[4]} of the 1st inning in ${m[5]}% of his last ${m[6]} starts`],
  [/^Pitch-type exploit: RV\/100 ([+-][\d.]+) vs (\w+) \(opposing SP throws it ([\d.]+)% of the time\)$/,
   m => `he's historically done real damage against the ${pitchName(m[2])} (a ${m[1]} run-value edge per 100 pitches seen), and tonight's opposing pitcher throws that pitch ${m[3]}% of the time`],
  [/^Opposing bullpen fatigue: (\d+)\/(\d+) relievers over 60 pitches in L7 \((tired pen — favorable late|fresh pen)\)$/,
   m => `${m[1]} of the other team's last ${m[2]} relievers used have been worked hard recently (60+ pitches within the last week)` + (m[3].startsWith("tired") ? ", which tends to favor hitters late in the game" : ", though their bullpen is otherwise fresh")],
  [/^Sharp money (backing|fading) (.+?) \(money% ([+-]?\d+) pts vs ticket%\)$/,
   m => `the money being wagered on ${m[2]} is running ${Math.abs(parseInt(m[3]))} points ${m[1] === "backing" ? "ahead of" : "behind"} the share of bets placed on them -- a sign bigger, sharper bettors are ${m[1]} this side`],
  [/^Public heavy on (.+?) \(money% trails tickets% by (\d+) pts\)/,
   m => `most of the tickets on ${m[1]} are small public bets rather than sharp money -- the dollars wagered trail the number of bets by ${m[2]} points, a classic public-side signal worth a discount`],
  [/^BvP: (\d+)-for-(\d+) vs (.+?) \(standard error ±(\d+) pts on a (\d+)-AB career sample.*\)$/,
   m => `he's ${m[1]}-for-${m[2]} in his career at-bats against tonight's starter, ${m[3]} -- the standard error on a ${m[5]}-AB sample runs about ±${m[4]} points, so his true rate against this pitcher could plausibly sit anywhere in that band, weighted lightly for exactly that reason`],
  [/^Recency-weighted K rate ([\d.]+)% \(exp\. decay, halflife 30d, (\d+) real starts \/ (\d+) BF\) — drives the strikeout probability model$/,
   m => `his strikeout rate over his ${m[2]} most recent starts (${m[3]} batters faced), weighted so his newest starts count for more, comes in at ${m[1]}% -- this is the number the strikeout probability itself is built from`],
  [/^L14 K% ([\d.]+) \((\d+) PA\)$/, m => `over his last 14 days he's struck out ${m[1]}% of the ${m[2]} batters he's faced`],
  [/^HP ump accuracy ([\d.]+)%.*$/, m => `tonight's home plate umpire has called ${m[1]}% of pitches correctly this season -- a more accurate ump tends to mean a tighter, more predictable strike zone`],
  [/^Projected ([\d.]+) PA \(slot (\d+), ([\d.]+)-run implied team total\)$/,
   m => `he's projected for about ${m[1]} plate appearances tonight batting ${m[2]} in the order, in a lineup the market expects to score around ${m[3]} runs`],
  [/^Projected ([\d.]+) PA \(slot (\d+), league-average run environment.*\)$/,
   m => `he's projected for about ${m[1]} plate appearances tonight batting ${m[2]} in the order, in a game with no market run total posted yet so a league-average environment is assumed`],
  [/^Opposing SP ERA ([\d.]+)$/, m => `the opposing starting pitcher has a ${m[1]} ERA`],
  [/^L7 avg EV ([\d.]+)mph \(league ~([\d.]+)\)$/, m => `over his last 7 days his average exit velocity is ${m[1]}mph, a bit below the league average of about ${m[2]}mph`],
  [/^L7 barrel% ([\d.]+)$/, m => `${m[1]}% of his batted balls over the last 7 days have been barreled up`],
  [/^Season barrel% ([\d.]+)/, m => `he's barreling up ${m[1]}% of his batted balls this season`],
  [/^Platoon: L bat vs LHP \((\w+)\)$/, m => `he's a lefty hitter facing a left-handed pitcher tonight, ${m[1] === "unfavorable" ? "typically a tougher matchup" : "typically a good matchup for him"}`],
  [/^Platoon: R bat vs RHP \((\w+)\)$/, m => `he's a righty hitter facing a right-handed pitcher tonight, ${m[1] === "unfavorable" ? "typically a tougher matchup" : "typically a good matchup for him"}`],
  [/^Platoon: L bat vs RHP \((\w+)\)$/, m => `he's a lefty hitter facing a right-handed pitcher tonight, ${m[1] === "favorable" ? "usually the easier side of the platoon for him" : "a tougher matchup than his platoon splits suggest"}`],
  [/^Platoon: R bat vs LHP \((\w+)\)$/, m => `he's a righty hitter facing a lefty tonight, ${m[1] === "favorable" ? "usually the easier side of the platoon for him" : "a tougher matchup than his platoon splits suggest"}`],
  [/^Market implied team total ([\d.]+) runs/, m => `the betting market expects his team to score about ${m[1]} runs tonight`],
  [/^Wind blowing OUT \((\d+)mph\)/, m => `the wind is blowing out at ${m[1]}mph, which helps the ball carry`],
  [/^Wind blowing IN \((\d+)mph\)/, m => `the wind is blowing in at ${m[1]}mph, which knocks the ball down`],
  [/^Opposing bullpen fatigue: (\d+)\/(\d+) relievers over 60 pitches in L7/, m => `${m[1]} of the other team's last ${m[2]} relievers used have been worked hard recently, which tends to favor hitters late`],
  [/^Season SB: (\d+)$/, m => `he already has ${m[1]} stolen bases this season`],
  [/^Sprint speed ([\d.]+)ft\/s \(league ~([\d.]+)\)$/, m => `he's a genuinely fast runner (${m[1]} ft/s, vs. a league-average runner around ${m[2]})`],
  [/^Opposing catcher pop time ([\d.]+)s to 2B \(league ~([\d.]+)s\)$/, m => `the catcher behind the plate tonight is slow getting the ball to second (${m[1]}s, vs. a league-average catcher around ${m[2]}s)`],
  [/^Opposing team throws out (\d+)% of runners/, m => `the opposing team throws out ${m[1]}% of runners who try to steal, a genuinely tough team to run on`],
  [/^AVG vs xBA: ([\d.]+) vs ([\d.]+) \(([+-][\d.]+)\)/, m => `his batting average (${m[1]}) is running ${parseFloat(m[3]) > 0 ? "a bit above" : "a bit below"} what the quality of his contact suggests (${m[2]}), ${parseFloat(m[3]) > 0 ? "a mild regression risk" : "a sign he may be due for better luck"}`],
];
function humanizeReason(s) {
  for (const [re, fn] of REASON_RULES) {
    const m = s.match(re);
    if (m) return fn(m);
  }
  return s.charAt(0).toLowerCase() + s.slice(1);
}
function capSentence(s) {
  if (!s) return s;
  const t = s.charAt(0).toUpperCase() + s.slice(1);
  return /[.!?]$/.test(t) ? t : t + ".";
}

// ══════════════════════════════════════════════════════════════════════
//  DATA LOADING / INDEXING
// ══════════════════════════════════════════════════════════════════════
async function fetchJSON(path) {
  const res = await fetch(path + "?t=" + Date.now(), { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}
function indexProps() {
  const next = new Map();
  for (const p of (DATA.props || [])) {
    if (!p.id || next.has(p.id)) throw new Error(`Invalid or duplicate canonical prop id: ${p.id}`);
    next.set(p.id, p);
  }
  PROPS_BY_ID = next;
}
function gameHasStarted(p) {
  if (["live", "suspended", "final", "cancelled"].includes(gameState(p))) return true;
  if (!p.game_start) return false;
  const start = new Date(p.game_start);
  return !isNaN(start) && start <= new Date();
}
function publicProps() {
  return (DATA.props || []).filter(p => !gameHasStarted(p)
    || (!!p.published_top_pick_at && !!p.publication_artifact_id)
    || !!p.publication_candidate_token);
}
// FROZEN_PUBLICATION_FIELDS: audit/settlement-critical facts that must
// never regress after a game starts -- the bet a user actually saw.
// Deliberately excludes presentation (why/watchouts), which must keep
// reflecting the CURRENT generator's routing rather than whatever text
// existed at first publication. Mirrors dashboard/live_state.py's
// FROZEN_PUBLICATION_FIELDS exactly (2026-08-25 Weston Wilson
// investigation) -- kept in sync by hand since this is a small, rarely-
// changing contract, not a shared build artifact between Python and JS.
const FROZEN_PUBLICATION_FIELDS = new Set([
  "id", "identity_version", "game_pk", "player_id", "combo_player_ids",
  "type", "name", "team", "matchup", "game_start", "stat", "market_side",
  "bet_side", "direction", "lean", "projection", "prop",
  "hit_probability", "market_odds", "market_implied", "market_edge",
  "market_hold", "price_clears", "recommendation_status", "status_reasons",
  "prob_ci", "reliability", "reliability_note", "sample_n", "lineup_assumed",
  "base_rate", "lift", "lift_reference_rate", "stable_lift",
  "published_top_pick_at",
]);
function freezePublishedSnapshot(p) {
  if (!gameHasStarted(p) || !p.publication_snapshot) return;
  if (!((p.published_top_pick_at && p.publication_artifact_id)
        || p.publication_candidate_token)) return;
  for (const [field, value] of Object.entries(p.publication_snapshot)) {
    if (FROZEN_PUBLICATION_FIELDS.has(field)) p[field] = value;
  }
}
function refreshSummary() {
  const props = publicProps();
  DATA.summary = DATA.summary || {};
  DATA.summary.n_props = props.length;
  DATA.summary.n_top_pick = props.filter(p => p.recommendation_status === "top_pick").length;
  DATA.summary.n_lean = props.filter(p => p.recommendation_status === "lean").length;
  DATA.summary.n_value = props.filter(p => p.recommendation_status === "value").length;
}

// ══════════════════════════════════════════════════════════════════════
//  WATCHLIST (localStorage) — item 14: notice odds moved, lineup confirmed,
//  lean changed, became a top pick, or the game started, since the user
//  starred it. A lightweight snapshot per id, diffed against the live
//  prop on every render -- no server, no accounts, exactly what a static
//  localStorage-only architecture can honestly support.
// ══════════════════════════════════════════════════════════════════════
const WATCH_KEY = "fc_watchlist_v1";
const WATCH_SNAP_KEY = "fc_watch_snapshot_v1";
function loadWatchlist() {
  watchlist = new Set(safeGetJSON(WATCH_KEY, []));
  watchSnapshot = safeGetJSON(WATCH_SNAP_KEY, {});
}
function saveWatchlist() {
  safeSet(WATCH_KEY, JSON.stringify([...watchlist]));
  safeSet(WATCH_SNAP_KEY, JSON.stringify(watchSnapshot));
}
function toggleWatch(id) {
  const p = PROPS_BY_ID.get(id);
  if (watchlist.has(id)) {
    watchlist.delete(id);
    delete watchSnapshot[id];
  } else {
    watchlist.add(id);
    if (p) watchSnapshot[id] = snapshotOf(p);
  }
  saveWatchlist();
  updateWatchCount();
  $all(`[data-star="${id}"]`).forEach(btn => btn.setAttribute("aria-pressed", String(watchlist.has(id))));
  if (route === "watchlist") renderWatchlist();
}
function snapshotOf(p) {
  return { status: p.recommendation_status, odds: p.market_odds, lineup_assumed: p.lineup_assumed,
          started: !!(p.game_start && new Date(p.game_start) <= new Date()) };
}
function watchChanges(p) {
  const snap = watchSnapshot[p.id];
  if (!snap) return [];
  const changes = [];
  if (snap.odds !== p.market_odds && p.market_odds != null) changes.push("Price moved");
  if (snap.status !== "top_pick" && p.recommendation_status === "top_pick") changes.push("Became a Top Pick");
  if (snap.status !== p.recommendation_status) changes.push(`Now: ${statusLabel(p)}`);
  if (snap.lineup_assumed === true && p.lineup_assumed === false) changes.push("Lineup confirmed");
  const startedNow = !!(p.game_start && new Date(p.game_start) <= new Date());
  if (!snap.started && startedNow) changes.push("Game started");
  return changes;
}
function updateWatchCount() {
  const el = document.getElementById("watchlist-count");
  el.textContent = String(watchlist.size);
  el.hidden = watchlist.size === 0;
}

// ══════════════════════════════════════════════════════════════════════
//  THEME
// ══════════════════════════════════════════════════════════════════════
function systemTheme() { return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"; }
function applyTheme(theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}
function initTheme() {
  const saved = safeGet("fc_theme") || "system";
  applyTheme(saved);
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || systemTheme();
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    safeSet("fc_theme", next);
  });
}

// ══════════════════════════════════════════════════════════════════════
//  ROUTER
// ══════════════════════════════════════════════════════════════════════
function initRouter() {
  window.addEventListener("hashchange", onRouteChange);
  onRouteChange();
}
function onRouteChange() {
  const h = (location.hash.replace(/^#\/?/, "") || "today").split("?")[0];
  route = ROUTES.includes(h) ? h : "today";
  $all(".main-nav a").forEach(a => a.classList.toggle("active", a.dataset.route === route));
  $all(".page").forEach(p => p.hidden = true);
  document.getElementById(`page-${route}`).hidden = false;
  renderRoute();
}
function go(newRoute) { location.hash = "#/" + newRoute; }

function renderRoute() {
  if (!DATA) return;
  if (route === "today") renderToday();
  else if (route === "props") renderProps();
  else if (route === "games") renderGames();
  else if (route === "performance") renderPerformance();
  else if (route === "watchlist") renderWatchlist();
}

// ══════════════════════════════════════════════════════════════════════
//  CARD / ROW BUILDERS
// ══════════════════════════════════════════════════════════════════════
function marketBlock(p) {
  const marketOdds = fmtOdds(p.market_odds);
  if (marketOdds === null) {
    return `<div class="pc-market"><span class="m-detail">Not yet posted on FanDuel</span></div>`;
  }
  const edge = p.market_edge;
  const edgeText = edge == null ? "—" : (edge >= 0 ? "+" : "") + Math.round(edge * 100) + " pts";
  const edgeClass = edge == null ? "" : (edge >= 0 ? "pos" : "neg");
  return `<div class="pc-market">
    <div><span class="book-price">${marketOdds}</span> <span class="m-detail">FanDuel</span></div>
    <div class="m-detail">Market: ${pct(p.market_implied, 0)}</div>
    <div class="pc-edge ${edgeClass}">${edgeText} edge</div>
  </div>`;
}
function pickCard(p, opts) {
  opts = opts || {};
  // Evidence quality is deliberately NOT repeated here -- it's one tap away
  // in the detail sheet's "Underlying data," and showing it on every single
  // card in a grid of a dozen-plus picks was pure chip clutter, not a
  // decision a viewer needs to make before opening a card.
  const chips = [statusChip(p), lineupChip(p), staleChip(p), liveStaleChip(p), gradeChip(p)].filter(Boolean).join("");
  const rankBadge = opts.rank ? `<span class="pc-rank">TOP PICK #${opts.rank}</span>` : "";
  const why = (p.why || [])[0] ? `<div class="pc-why">${esc(capSentence(humanizeReason(p.why[0])))}</div>` : "";
  const starred = watchlist.has(p.id);
  return `<button class="pick-card status-${p.recommendation_status || "neutral"} ${lifecycleClass(p)}${p.stale ? " status-stale" : ""}" data-open="${p.id}">
    <div class="pc-top">
      <div>
        <div class="pc-name">${esc(p.name)}</div>
        <div class="pc-sub">${esc(p.team || p.matchup || "")}</div>
      </div>
      ${rankBadge}
    </div>
    <div class="pc-prop">${esc(p.prop)}</div>
    <div class="pc-prob-row">
      <span class="pc-prob">${pctBig(p.hit_probability)}</span>
      <span class="pc-prob-label">Full Count<br>Probability</span>
    </div>
    ${marketBlock(p)}
    <div class="pc-chips">${chips}</div>
    ${why}
  </button>`;
}
function propRow(p) {
  const chips = [statusChip(p), lineupChip(p), staleChip(p), liveStaleChip(p), gradeChip(p)].filter(Boolean).join("");
  return `<button class="prop-row ${lifecycleClass(p)}" data-open="${p.id}">
    <div class="pr-main">
      <div class="pr-name">${esc(p.name)}</div>
      <div class="pr-prop">${esc(p.prop)}</div>
    </div>
    <div class="pr-prob">${pctBig(p.hit_probability)}</div>
    <div class="pr-price">${fmtOdds(p.market_odds) ?? "—"}</div>
    <div class="pr-status">${chips}</div>
  </button>`;
}

// Categorizes the real status_reasons text classify_recommendation() already
// attached to every non-top-pick candidate, so an empty Top Picks section can
// explain WHY honestly instead of just showing a generic "nothing today"
// message. Matched against the exact phrasing recommendation.py emits --
// see classify_recommendation()'s _result() calls -- so this only ever
// reports real, already-computed reasons, never an invented category.
function topPickGapSummary(props) {
  const counts = { lineupPending: 0, pricePending: 0, closeRead: 0, thinSample: 0, other: 0 };
  for (const p of props) {
    if (p.recommendation_status === "top_pick") continue;
    const reason = (p.status_reasons || [])[0] || "";
    if (reason.includes("lineup slot is still a projection")) counts.lineupPending++;
    else if (reason.includes("no market price is posted yet")) counts.pricePending++;
    else if (reason.includes("doesn't clear every Top Pick requirement")) counts.closeRead++;
    else if (reason.includes("too thin a sample")) counts.thinSample++;
    else counts.other++;
  }
  let earliestStart = null;
  for (const g of (DATA.schedule || [])) {
    if (!g.game_start) continue;
    const d = new Date(g.game_start);
    if (!isNaN(d) && (!earliestStart || d < earliestStart)) earliestStart = d;
  }
  return { counts, earliestStart };
}

function topPickGapExplainer(props) {
  const { counts, earliestStart } = topPickGapSummary(props);
  const lines = [];
  if (counts.lineupPending) {
    lines.push(`<li><b>${counts.lineupPending}</b> props are waiting on a confirmed starting lineup — Full Count won't call a Top Pick off a projected lineup slot.</li>`);
  }
  if (counts.pricePending) {
    lines.push(`<li><b>${counts.pricePending}</b> real, positive reads have no live sportsbook price posted yet to grade against.</li>`);
  }
  if (counts.closeRead) {
    lines.push(`<li><b>${counts.closeRead}</b> props have a real, positive read that falls just short of the full Top Pick bar (these are today's Leans).</li>`);
  }
  if (counts.thinSample) {
    lines.push(`<li><b>${counts.thinSample}</b> props look promising but rest on too thin a track record to stand behind yet.</li>`);
  }
  const startNote = earliestStart
    ? `First pitch tonight is ${gameTimeLabel(earliestStart.toISOString())} — lineups typically confirm shortly before then, which is when most of the "waiting on lineup" props above can resolve one way or the other.`
    : "";
  return `<div class="empty-state">
    <div class="es-icon">⚾</div>
    <h3>No bets currently meet Full Count's Top Pick standards.</h3>
    <p>That's a real, honest result — not every slate has one. Here's exactly where tonight's candidates stand:</p>
    ${lines.length ? `<ul class="gap-reasons">${lines.join("")}</ul>` : ""}
    ${startNote ? `<p class="gap-start-note">${esc(startNote)}</p>` : ""}
    <p>Explore Leans below, or browse the full research board for every prop Full Count can analyze tonight.</p>
    <div class="es-cta">
      <a class="btn btn-primary" href="#/props">Browse All Props</a>
    </div>
  </div>`;
}

// ══════════════════════════════════════════════════════════════════════
//  TODAY PAGE
// ══════════════════════════════════════════════════════════════════════
function renderToday() {
  const el = document.getElementById("page-today");
  const props = publicProps();
  const topPicks = props.filter(p => p.recommendation_status === "top_pick")
    .sort((a, b) => (b.market_edge || 0) - (a.market_edge || 0));
  const valueAll = props.filter(p => p.recommendation_status === "value" && !isLongshot(p))
    .sort((a, b) => (b.market_edge || 0) - (a.market_edge || 0));
  const longshotsAll = props.filter(isLongshot)
    .sort((a, b) => (b.market_edge || 0) - (a.market_edge || 0));
  const leansAll = props.filter(p => p.recommendation_status === "lean")
    .sort((a, b) => (b.lift || 0) - (a.lift || 0));
  const value = valueAll.slice(0, 6);
  const longshots = longshotsAll.slice(0, 6);
  const leans = leansAll.slice(0, 6);
  // Full Count Radar: the real remainder of tonight's Lean/Value pool
  // beyond the handful already featured as full cards/rows above -- not a
  // new bucket, not an invented "almost qualified" tier, just the rest of
  // recommendation_status "lean"/"value" that real board volume otherwise
  // buries in All Props. Sorted by the same real edge/lift fields.
  const radar = [...leansAll.slice(6), ...valueAll.slice(6), ...longshotsAll.slice(6)]
    .sort((a, b) => (b.market_edge ?? b.lift ?? 0) - (a.market_edge ?? a.lift ?? 0));

  const summary = DATA.summary || {};
  let html = `
    <div class="stat-row">
      <div class="stat-tile"><span class="n">${summary.n_top_pick ?? 0}</span><span class="l">Top Picks tonight</span></div>
      <div class="stat-tile"><span class="n">${summary.n_lean ?? 0}</span><span class="l">Leans on the board</span></div>
      <div class="stat-tile"><span class="n">${summary.n_value ?? 0}</span><span class="l">Value / Longshots</span></div>
      <div class="stat-tile"><span class="n">${summary.n_games ?? 0}</span><span class="l">Games tonight</span></div>
    </div>`;

  html += `<section class="section"><div class="section-head"><h2>Top Picks</h2>
    <span class="section-sub">Full Count's official recommendations — probability, evidence, price, and freshness all cleared.</span></div>`;
  if (topPicks.length) {
    html += `<div class="card-grid">${topPicks.map((p, i) => pickCard(p, { rank: i + 1 })).join("")}</div>`;
  } else {
    html += topPickGapExplainer(props);
  }
  html += `</section>`;

  if (value.length || longshots.length) {
    html += `<div class="value-explainer">
      <b>Probability and value are different questions.</b> Probability asks "will this happen?" Value asks
      "does the price pay fairly for that chance?" A Top Pick can win often at a mediocre price; a Longshot can be
      a smart bet despite being unlikely to hit. Every card below shows both numbers separately — never one standing in for the other.
    </div>`;
    html += `<section class="section"><div class="section-head"><h2>Best Value</h2>
      <span class="section-sub">Real sportsbook mispricing — the price pays more than the win probability alone would justify.</span></div>`;
    html += value.length
      ? `<div class="card-grid">${value.map(p => pickCard(p)).join("")}</div>`
      : `<p class="section-sub">Nothing clears Full Count's value bar right now.</p>`;
    html += `</section>`;

    html += `<section class="section"><div class="section-head"><h2>Longshots &amp; High Variance</h2>
      <span class="section-sub">Home runs, steals, and other low-probability plays where Full Count sees real price value — never a recommendation that this is likely to win.</span></div>`;
    html += longshots.length
      ? `<div class="card-grid">${longshots.map(p => pickCard(p)).join("")}</div>`
      : `<p class="section-sub">No standout longshots on tonight's board.</p>`;
    html += `</section>`;
  }

  html += `<section class="section"><div class="section-head"><h2>Leans</h2>
    <span class="section-sub">Full Count's data favors a side — not an official recommendation.</span>
    <a class="see-all" href="#/props?status=lean">See all research →</a></div>`;
  html += leans.length
    ? `<div class="prop-list">${leans.map(propRow).join("")}</div>`
    : `<p class="section-sub">No strong leans on tonight's board yet.</p>`;
  html += `</section>`;

  if (radar.length) {
    html += `<section class="section"><div class="section-head"><h2>Full Count Radar</h2>
      <span class="section-sub">Everything else on tonight's board with a real Lean or Value read, beyond the featured picks above — same real numbers, just more of the board.</span>
      <a class="see-all" href="#/props?status=lean">See all research →</a></div>
      <div class="prop-list">${radar.slice(0, 24).map(propRow).join("")}</div></section>`;
  }

  if (DATA.suggested_parlay && DATA.suggested_parlay.legs && DATA.suggested_parlay.legs.length) {
    html += suggestedParlayBlock(DATA.suggested_parlay);
  }

  if ((DATA.streaks || []).length) {
    html += `<section class="section"><div class="section-head"><h2>Hot Streaks</h2></div>
      <div class="streak-strip">${DATA.streaks.slice(0, 12).map(streakChip).join("")}</div></section>`;
  }

  if ((DATA.schedule || []).length) {
    html += `<section class="section"><div class="section-head"><h2>Tonight's Games</h2>
      <a class="see-all" href="#/games">See all →</a></div>
      <div class="schedule-strip">${DATA.schedule.slice(0, 15).map(scheduleChip).join("")}</div></section>`;
  }

  el.innerHTML = html;
  wireCardOpeners(el);
}
// Real bug, found 2026-08-24: a player can carry several distinct
// streak entries (e.g. Chandler Simpson: 14 straight games with a hit,
// 14 straight with a single, 14 straight with a hit+run+RBI -- three
// genuinely different, real streaks_compute_streaks() correctly tracks
// separately via streak_stat). The chip rendered identical text for all
// three ("14 straight — Chandler Simpson"), with no way to tell them
// apart -- misleading, not just repetitive. Every streak's own linked
// prop (p.prop) already carries the exact real market text that
// distinguishes it ("Over 0.5 Hits" vs "Over 0.5 Singles" vs "Over 0.5
// Hits+Runs+RBIs"); stripping its "Over/Under <line> " prefix reuses
// that same real text as a compact market label instead of inventing a
// new one.
function streakMarketLabel(p) {
  return (p.prop || "").replace(/^(Over|Under)\s+[\d.]+\s+/i, "");
}
function streakChip(s) {
  const p = PROPS_BY_ID.get(s.id);
  if (!p) return "";
  const label = streakMarketLabel(p);
  return `<button class="streak-chip" data-open="${p.id}"><b>${s.streak}</b> straight —
    <span class="streak-chip-name">${esc(p.name)}</span>${label ? ` <span class="streak-chip-stat">(${esc(label)})</span>` : ""}</button>`;
}
function scheduleChip(g) {
  return `<button class="schedule-chip" data-game="${g.game_pk}">
    <div class="sc-time">${gameTimeLabel(g.game_start)}</div>
    <div class="sc-teams">${esc(g.away_team || "")} @ ${esc(g.home_team || "")}</div>
  </button>`;
}
function suggestedParlayBlock(parlay) {
  const legs = (parlay.legs || []).map(l =>
    `<div class="parlay-leg"><span>${esc(l.name)} — ${esc(l.prop)}</span><span>${fmtOdds(l.american) ?? ""}</span></div>`).join("");
  return `<section class="section"><div class="parlay-card">
    <div class="section-head"><h2 style="font-size:16px">Suggested Parlay</h2></div>
    <div class="parlay-legs">${legs}</div>
    <div class="pc-sub">Combined: ${fmtOdds(parlay.combined_american) ?? "—"}</div>
  </div></section>`;
}

// ══════════════════════════════════════════════════════════════════════
//  ALL PROPS PAGE
// ══════════════════════════════════════════════════════════════════════
// DATA.families' "Home Runs" entry carries stat: "moonshot" -- that's
// dashboard/build_dashboard.py's internal result-dict grouping key
// (kept separate from select_best_by_category's own "home_runs" list only
// to avoid double-counting the same real candidates twice; see that
// file's build_payload() comment). It never appears on any actual prop
// row: every real home-run candidate's own p.stat is "home_runs" (see
// score_batter/select_moonshots' real row construction). Every family
// filter value used against p.stat must go through this single mapping so
// the "Home Runs" tab/option filters real rows instead of matching zero.
function familyFilterValue(stat) {
  return stat === "moonshot" ? "home_runs" : stat;
}
function applyFilters(props) {
  let rows = props;
  if (filters.family !== "all") rows = rows.filter(p => p.stat === filters.family);
  if (filters.status !== "all") {
    rows = filters.status === "longshot" ? rows.filter(isLongshot)
         : filters.status === "value" ? rows.filter(p => p.recommendation_status === "value" && !isLongshot(p))
         : rows.filter(p => p.recommendation_status === filters.status);
  }
  if (filters.evidence !== "all") rows = rows.filter(p => p.reliability === filters.evidence);
  if (filters.search) {
    const q = filters.search.toLowerCase();
    rows = rows.filter(p => (p.name || "").toLowerCase().includes(q)
      || (p.team || "").toLowerCase().includes(q) || (p.matchup || "").toLowerCase().includes(q)
      || (p.prop || "").toLowerCase().includes(q));
  }
  const sorters = {
    default: (a, b) => (a.market_odds == null) - (b.market_odds == null) || (b.market_edge || 0) - (a.market_edge || 0),
    probability: (a, b) => (b.hit_probability || 0) - (a.hit_probability || 0),
    edge: (a, b) => (b.market_edge || 0) - (a.market_edge || 0),
    lean: (a, b) => (b.lift || 0) - (a.lift || 0),
    price: (a, b) => (a.market_odds ?? 9999) - (b.market_odds ?? 9999),
    player: (a, b) => (a.name || "").localeCompare(b.name || ""),
  };
  return rows.slice().sort(sorters[filters.sort] || sorters.default);
}
function renderProps() {
  const el = document.getElementById("page-props");
  const families = DATA.families || [];
  const visible = publicProps();
  const rows = applyFilters(visible);

  el.innerHTML = `
    <div class="section-head"><h2>All Props</h2><span class="section-sub">${rows.length} of ${visible.length} props</span></div>
    <div class="filter-bar">
      <div class="filter-inline" style="display:flex;gap:8px;flex-wrap:wrap;">
        <select class="filter-select" id="f-family" aria-label="Filter by prop type">
          <option value="all">All prop types</option>
          ${families.map(f => `<option value="${familyFilterValue(f.stat)}">${esc(f.label)} (${f.count})</option>`).join("")}
        </select>
        <select class="filter-select" id="f-status" aria-label="Filter by recommendation">
          <option value="all">Any status</option>
          <option value="top_pick">Top Pick</option>
          <option value="lean">Lean</option>
          <option value="value">Value</option>
          <option value="longshot">Longshot</option>
          <option value="neutral">No Strong Lean</option>
        </select>
        <select class="filter-select" id="f-evidence" aria-label="Filter by evidence quality">
          <option value="all">Any evidence</option>
          <option value="A">Strong evidence</option>
          <option value="B">Solid evidence</option>
          <option value="C">Developing evidence</option>
          <option value="D">Limited evidence</option>
        </select>
        <select class="filter-select" id="f-sort" aria-label="Sort by">
          <option value="default">Sort: Recommended</option>
          <option value="probability">Sort: Highest probability</option>
          <option value="edge">Sort: Strongest edge</option>
          <option value="lean">Sort: Strongest lean</option>
          <option value="price">Sort: Price</option>
          <option value="player">Sort: Player name</option>
        </select>
      </div>
      <button class="filter-chip-btn mobile-only filter-more-btn" id="f-open-sheet">Filters</button>
      <span class="filter-count desktop-only">${activeFilterCount()} active</span>
    </div>
    <div class="prop-list" id="props-list"></div>
  `;
  $("#f-family", el).value = filters.family;
  $("#f-status", el).value = filters.status;
  $("#f-evidence", el).value = filters.evidence;
  $("#f-sort", el).value = filters.sort;
  $("#f-family", el).addEventListener("change", e => { filters.family = e.target.value; renderProps(); });
  $("#f-status", el).addEventListener("change", e => { filters.status = e.target.value; renderProps(); });
  $("#f-evidence", el).addEventListener("change", e => { filters.evidence = e.target.value; renderProps(); });
  $("#f-sort", el).addEventListener("change", e => { filters.sort = e.target.value; renderProps(); });
  $("#f-open-sheet", el).addEventListener("click", () => openFilterSheet());

  const list = $("#props-list", el);
  list.innerHTML = rows.length ? rows.map(propRow).join("")
    : `<div class="empty-state"><div class="es-icon">🔍</div><h3>No props match these filters</h3><p>Try widening your search or clearing a filter.</p></div>`;
  wireCardOpeners(el);
}
function activeFilterCount() {
  return ["family", "status", "evidence"].filter(k => filters[k] !== "all").length + (filters.search ? 1 : 0);
}
function openFilterSheet() {
  const backdrop = document.createElement("div");
  backdrop.className = "filter-sheet-backdrop";
  const sheet = document.createElement("div");
  sheet.className = "filter-sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-labelledby", "filter-sheet-title");
  sheet.setAttribute("tabindex", "-1");
  const families = DATA.families || [];
  sheet.innerHTML = `
    <h3 id="filter-sheet-title">Filter &amp; sort</h3>
    <div class="filter-sheet-group"><div class="label">Prop type</div>
      <div class="filter-sheet-options">${[{ value: "all", label: "All" }, ...families.map(f => ({ value: familyFilterValue(f.stat), label: f.label }))].map(({ value, label }) =>
        `<button class="filter-chip-btn" data-k="family" data-v="${value}" aria-pressed="${filters.family === value}">${esc(label)}</button>`).join("")}</div></div>
    <div class="filter-sheet-group"><div class="label">Status</div>
      <div class="filter-sheet-options">${[["all", "Any"], ["top_pick", "Top Pick"], ["lean", "Lean"], ["value", "Value"], ["longshot", "Longshot"], ["neutral", "No Strong Lean"]].map(([v, l]) =>
        `<button class="filter-chip-btn" data-k="status" data-v="${v}" aria-pressed="${filters.status === v}">${l}</button>`).join("")}</div></div>
    <div class="filter-sheet-group"><div class="label">Evidence quality</div>
      <div class="filter-sheet-options">${[["all", "Any"], ["A", "Strong"], ["B", "Solid"], ["C", "Developing"], ["D", "Limited"]].map(([v, l]) =>
        `<button class="filter-chip-btn" data-k="evidence" data-v="${v}" aria-pressed="${filters.evidence === v}">${l}</button>`).join("")}</div></div>
    <button class="btn btn-primary" id="f-sheet-apply" style="width:100%;margin-top:6px;">Show results</button>
  `;
  document.body.append(backdrop, sheet);
  document.body.style.overflow = "hidden";
  function close() {
    backdrop.remove(); sheet.remove(); document.body.style.overflow = "";
    document.removeEventListener("keydown", onKeydown);
    closeModal();
  }
  function onKeydown(e) {
    if (e.key === "Escape") { close(); return; }
    trapFocus(sheet, e);
  }
  openModal(sheet);
  document.addEventListener("keydown", onKeydown);
  backdrop.addEventListener("click", close);
  $all("[data-k]", sheet).forEach(btn => btn.addEventListener("click", () => {
    filters[btn.dataset.k] = btn.dataset.v;
    $all(`[data-k="${btn.dataset.k}"]`, sheet).forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
  }));
  $("#f-sheet-apply", sheet).addEventListener("click", () => { close(); renderProps(); });
}

// ══════════════════════════════════════════════════════════════════════
//  GAMES PAGE
// ══════════════════════════════════════════════════════════════════════
function renderGames() {
  const el = document.getElementById("page-games");
  const games = DATA.schedule || [];
  if (!games.length) {
    el.innerHTML = `<div class="empty-state"><div class="es-icon">🗓️</div><h3>No games with a research breakdown yet</h3><p>Check back once tonight's games are set.</p></div>`;
    return;
  }
  el.innerHTML = `<div class="section-head"><h2>Games</h2><span class="section-sub">${games.length} games tonight</span></div>
    <div class="game-list">${games.map(gameCard).join("")}</div>`;
}
function gameCard(g) {
  const wx = g.weather;
  let wxText = "";
  if (wx) {
    wxText = wx.dome ? "Dome — weather neutral"
      : `${wx.temp ?? "—"}°F${wx.wind_mph ? `, wind ${wx.wind_mph}mph ${wx.wind_effect || ""}` : ""}`;
  }
  const ump = g.umpire ? `HP Ump: ${esc(g.umpire.name)} (${pct(g.umpire.k_pct, 1)} K, ${pct(g.umpire.bb_pct, 1)} BB)` : "";
  const picks = (g.picks || []).map(p => `<div class="game-pick-line">
      <span>${esc(p.name)} — ${esc(p.prop)}</span>
      <span>${pctBig(p.hit_probability)}${p.market_odds != null ? " · " + fmtOdds(p.market_odds) : ""}</span>
    </div>`).join("");
  return `<div class="game-card">
    <div class="game-card-head">
      <div class="game-teams">${esc(g.away_team || "")} @ ${esc(g.home_team || "")}</div>
      <div class="game-time">${gameTimeLabel(g.game_start)}</div>
    </div>
    <div class="game-meta-row">
      ${g.away_sp ? `<span>${esc(g.away_sp)} vs ${esc(g.home_sp || "TBD")}</span>` : ""}
      ${wxText ? `<span>${esc(wxText)}</span>` : ""}
      ${ump ? `<span>${esc(ump)}</span>` : ""}
    </div>
    ${picks ? `<div class="game-picks">${picks}</div>` : `<p class="section-sub">No standout research for this game yet.</p>`}
  </div>`;
}

// ══════════════════════════════════════════════════════════════════════
//  PERFORMANCE PAGE — item 9/10: current vs legacy, never blended,
//  translated into plain language.
// ══════════════════════════════════════════════════════════════════════
function renderPerformance() {
  const el = document.getElementById("page-performance");
  const tr = DATA.track_record || {};
  const cur = tr.current;
  const leg = tr.legacy;

  let html = `<div class="section-head"><h2>Performance</h2>
    <span class="section-sub">The current recommendation architecture and the legacy system it replaced, kept completely separate.</span></div>`;

  html += `<div class="perf-block"><span class="perf-tag tag-current">Current Full Count</span>
    <h3 style="margin-top:10px;">2026-08-15 architecture forward</h3>
    <p class="perf-sub">The Top Pick/Lean/Value/Neutral system in place today. Every number below is only from picks made under this exact system.</p>`;
  if (cur && cur.n > 0) {
    html += `<div class="perf-metric-grid">
      <div class="perf-metric"><div class="pm-n">${pct(cur.hit_rate, 1)}</div><div class="pm-l">Top Pick hit rate</div></div>
      <div class="perf-metric"><div class="pm-n">${cur.hits}-${cur.misses}</div><div class="pm-l">Record</div></div>
      <div class="perf-metric"><div class="pm-n">${cur.n}</div><div class="pm-l">Graded picks</div></div>
      ${cur.last_14d_hit_rate != null ? `<div class="perf-metric"><div class="pm-n">${pct(cur.last_14d_hit_rate, 1)}</div><div class="pm-l">Last 14 days (n=${cur.last_14d_n})</div></div>` : ""}
    </div>`;
  } else {
    html += `<div class="empty-state">
      <div class="es-icon">📊</div>
      <h3>Zero graded days so far — and that's the honest, correct starting point.</h3>
      <p>The current architecture shipped on 2026-08-15. Grading lags one day behind, so its real track record starts accumulating from here. Nothing is being hidden or blended in from the old system to fill the gap.</p>
    </div>`;
  }
  html += `</div>`;

  html += `<div class="perf-block legacy"><span class="perf-tag tag-legacy">Legacy Full Count</span>
    <h3 style="margin-top:10px;">Pre-rebuild system (through 2026-08-14)</h3>
    <p class="perf-sub">The old price-clears/quality-score system, before the recommendation-layer rebuild. A real historical record — never evidence about the current system.</p>`;
  if (leg && leg.n > 0) {
    html += `<div class="perf-metric-grid">
      <div class="perf-metric"><div class="pm-n">${pct(leg.hit_rate, 1)}</div><div class="pm-l">Main-board hit rate</div></div>
      <div class="perf-metric"><div class="pm-n">${leg.hits}-${leg.misses}</div><div class="pm-l">Record</div></div>
      <div class="perf-metric"><div class="pm-n">${leg.n}</div><div class="pm-l">Graded picks</div></div>
      ${leg.last_14d_hit_rate != null ? `<div class="perf-metric"><div class="pm-n">${pct(leg.last_14d_hit_rate, 1)}</div><div class="pm-l">Last 14 days</div></div>` : ""}
    </div>`;
  } else {
    html += `<p class="section-sub">No legacy record on file.</p>`;
  }
  html += `</div>`;

  html += `<div class="perf-block">
    <h3>What these numbers mean</h3>
    <div class="perf-explain">
      <p><b>Calibration</b> — when Full Count says 70%, how often do those bets actually win? A well-calibrated 70% wins about 70% of the time. See this project's full calibration audit for the honest answer, including where the sample is still too thin to say.</p>
    </div>
    <div class="perf-explain">
      <p><b>Market comparison</b> — does Full Count improve on the sportsbook's own implied probability? As of the latest measurement, this is genuinely <b>inconclusive</b> — the model runs roughly even with the market on scoring rules, and the one statistical test built to answer this directly (a regression of outcomes on the market's price plus Full Count's disagreement with it) is not yet significant. That is an honest "not proven either way," not a hidden negative result.</p>
    </div>
  </div>`;

  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════
//  WATCHLIST PAGE
// ══════════════════════════════════════════════════════════════════════
function renderWatchlist() {
  const el = document.getElementById("page-watchlist");
  const items = [...watchlist].map(id => PROPS_BY_ID.get(id)).filter(Boolean);
  if (!items.length) {
    el.innerHTML = `<div class="empty-state"><div class="es-icon">☆</div><h3>Your watchlist is empty</h3>
      <p>Star any player or prop to track it here — you'll see when the price moves, the lineup confirms, or it becomes a Top Pick.</p>
      <div class="es-cta"><a class="btn btn-primary" href="#/props">Browse All Props</a></div></div>`;
    return;
  }
  el.innerHTML = `<div class="section-head"><h2>Watchlist</h2><span class="section-sub">${items.length} saved</span></div>
    <div class="prop-list">${items.map(p => {
      const changes = watchChanges(p);
      return `<div class="watchlist-item">${changes.length ? `<span class="watch-change-badge">${esc(changes[0])}</span>` : ""}${propRow(p)}</div>`;
    }).join("")}</div>`;
  wireCardOpeners(el);
}

// ══════════════════════════════════════════════════════════════════════
//  MODAL FOCUS MANAGEMENT — shared by the detail sheet and the mobile
//  filter sheet (item 17: "keyboard nav... focus states... as part of the
//  design, not an afterthought"). Moves focus into the dialog on open,
//  keeps Tab from escaping to the page behind it, and restores focus to
//  whatever triggered it on close -- the WAI-ARIA dialog pattern's three
//  baseline requirements a role="dialog" alone doesn't get you for free.
// ══════════════════════════════════════════════════════════════════════
function trapFocus(container, e) {
  if (e.key !== "Tab") return;
  const focusable = $all('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])', container)
    .filter(el => el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
}
function openModal(container) {
  lastFocusedEl = document.activeElement;
  (container.querySelector("[autofocus]") || container).focus?.();
}
function closeModal() {
  lastFocusedEl?.focus?.();
  lastFocusedEl = null;
}

// ══════════════════════════════════════════════════════════════════════
//  DETAIL SHEET (progressive disclosure — item 7/20)
// ══════════════════════════════════════════════════════════════════════
function wireCardOpeners(root) {
  $all("[data-open]", root).forEach(btn => {
    btn.addEventListener("click", () => openDetail(btn.dataset.open));
  });
  $all("[data-game]", root).forEach(btn => {
    btn.addEventListener("click", () => { go("games"); });
  });
}
function openDetail(id) {
  const p = PROPS_BY_ID.get(id);
  if (!p) return;
  document.getElementById("detail-body").innerHTML = detailBody(p);
  const sheet = document.getElementById("detail-sheet");
  const panel = sheet.querySelector(".detail-sheet-panel");
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  const star = $("#detail-star");
  if (star) star.addEventListener("click", () => { toggleWatch(id); star.setAttribute("aria-pressed", String(watchlist.has(id))); star.querySelector(".star-label").textContent = watchlist.has(id) ? "Saved to Watchlist" : "Save to Watchlist"; });
  $("#detail-underlying-toggle")?.addEventListener("click", (e) => {
    const box = document.getElementById("detail-underlying");
    const open = box.hidden;
    box.hidden = !open;
    e.currentTarget.setAttribute("aria-expanded", String(open));
    e.currentTarget.querySelector(".u-caret").textContent = open ? "▾" : "▸";
  });
  openModal(panel);
  panel.addEventListener("keydown", _detailTrapHandler);
}
function _detailTrapHandler(e) {
  trapFocus(document.querySelector("#detail-sheet .detail-sheet-panel"), e);
}
function closeDetail() {
  const sheet = document.getElementById("detail-sheet");
  if (sheet.hidden) return;
  sheet.hidden = true;
  sheet.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  closeModal();
}
// Cross-references a prop's own game_pk against DATA.schedule (already
// real, already computed by dashboard/build_dashboard.py's game_context --
// see gameCard() above, which shows the same fields on the Games page).
// Not a new data source: this only surfaces, inside the prop detail sheet,
// context that already exists elsewhere in the same payload for the same
// game. Never fabricates a value that isn't already on the schedule entry.
function gameContextFor(p) {
  if (!p.game_pk) return null;
  return (DATA.schedule || []).find(g => g.game_pk === p.game_pk) || null;
}
function weatherText(wx) {
  if (!wx) return null;
  if (wx.dome) return "Dome — weather neutral";
  const bits = [];
  if (wx.temp != null) bits.push(`${wx.temp}°F`);
  if (wx.wind_mph != null) bits.push(`wind ${wx.wind_mph}mph${wx.wind_effect ? " " + wx.wind_effect : ""}`);
  return bits.length ? bits.join(", ") : null;
}
function detailBody(p) {
  const eq = evidenceQuality(p);
  const game = gameContextFor(p);
  const reasons = (p.why || []).map(w => `<div class="reason-item positive"><span class="r-icon">＋</span><span>${esc(capSentence(humanizeReason(w)))}</span></div>`).join("");
  const watchouts = (p.watchouts || []).map(w => `<div class="reason-item negative"><span class="r-icon">－</span><span>${esc(capSentence(humanizeReason(w)))}</span></div>`).join("")
    + (p.sample_n != null && p.sample_n > 0 && p.sample_n < 30
      ? `<div class="reason-item negative"><span class="r-icon">－</span><span>This read leans on a smaller sample (${p.sample_n} games) — treat it with a little extra caution.</span></div>` : "")
    + (p.stale ? `<div class="reason-item negative"><span class="r-icon">－</span><span>${esc((p.status_reasons || [])[0] || "Underlying data is stale.")}</span></div>` : "");

  // Real game-level facts already on the schedule entry for this prop's
  // game_pk -- starters, park/weather, home-plate umpire -- shown here so
  // the reasoning above ("Platoon: R bat vs LHP", "Opposing SP ERA 3.72")
  // has the actual matchup it refers to right next to it, instead of only
  // living on the separate Games page.
  const contextRows = [];
  if (game) {
    if (game.away_sp || game.home_sp) {
      contextRows.push(["Starters", `${esc(game.away_sp || "TBD")} @ ${esc(game.home_sp || "TBD")}`]);
    }
    const wx = weatherText(game.weather);
    if (wx) contextRows.push(["Park / weather", esc(wx)]);
    if (game.umpire) {
      contextRows.push(["HP umpire", `${esc(game.umpire.name)} — ${pct(game.umpire.k_pct, 1)} K, ${pct(game.umpire.bb_pct, 1)} BB rate`]);
    }
  }

  return `
    <div class="detail-head">
      <h2 id="detail-title">${esc(p.name)}</h2>
      <div class="d-sub">${esc(p.prop)} · ${esc(p.team || p.matchup || "")}</div>
    </div>
    <div class="detail-hero">
      <div>
        <div class="prob-big">${pctBig(p.hit_probability)}</div>
        <div class="hero-metric-label">Full Count win probability</div>
      </div>
      <div class="hero-meta">
        <div><b>${esc(statusLabel(p))}</b></div>
        <div>Market: ${fmtOdds(p.market_odds) ?? "not posted"} ${p.market_implied != null ? `(${pct(p.market_implied, 0)} implied${p.market_hold != null ? ", exact no-vig" : ""})` : ""}</div>
        <div class="hero-value-line">Betting value: <b>${p.market_edge != null ? (p.market_edge >= 0 ? "+" : "") + Math.round(p.market_edge * 100) + " pts" : "—"}</b>
          <span class="hero-value-note">— how much better this price pays than the win probability alone justifies. A different question from probability itself.</span></div>
      </div>
    </div>
    <div class="pc-chips" style="margin-bottom:18px;">${[statusChip(p), lineupChip(p), evidenceChip(p), staleChip(p), liveStaleChip(p), gradeChip(p)].filter(Boolean).join("")}</div>

    ${reasons ? `<div class="detail-section"><h3>Why Full Count Likes It</h3><div class="reason-list">${reasons}</div></div>` : ""}
    ${watchouts ? `<div class="detail-section"><h3>What Could Go Wrong</h3><div class="reason-list">${watchouts}</div></div>` : ""}
    ${contextRows.length ? `<div class="detail-section"><h3>Game Context</h3>
      <div class="underlying-data">${contextRows.map(([k, v]) => `<div class="ud-item"><div class="k">${esc(k)}</div><div class="v" style="font-family:var(--font-body);font-weight:500;">${v}</div></div>`).join("")}</div>
    </div>` : ""}

    <div class="detail-section">
      <button class="underlying-toggle" id="detail-underlying-toggle" aria-expanded="false">
        <span class="u-caret">▸</span> Underlying data
      </button>
      <div class="underlying-data" id="detail-underlying" hidden>
        <div class="ud-item"><div class="k">Evidence quality</div><div class="v">${eq ? eq.label : "—"}</div></div>
        <div class="ud-item"><div class="k">Sample size</div><div class="v">${p.sample_n ?? "—"}</div></div>
        <div class="ud-item"><div class="k">Base rate</div><div class="v">${pct(p.base_rate, 1)}</div></div>
        <div class="ud-item"><div class="k">Lift vs. base rate</div><div class="v">${p.lift != null ? (p.lift >= 0 ? "+" : "") + Math.round(p.lift * 100) + " pts" : "—"}</div></div>
        <div class="ud-item"><div class="k">95% interval</div><div class="v">${p.prob_ci ? pct(p.prob_ci[0], 0) + "–" + pct(p.prob_ci[1], 0) : "Not defensible for this line"}</div></div>
        <div class="ud-item"><div class="k">Quality score</div><div class="v">${p.score ?? "—"}/100</div></div>
      </div>
    </div>

    <button class="btn watchlist-toggle-btn" id="detail-star" aria-pressed="${watchlist.has(p.id)}">
      <span class="star-label">${watchlist.has(p.id) ? "Saved to Watchlist" : "Save to Watchlist"}</span>
    </button>
  `;
}

// ══════════════════════════════════════════════════════════════════════
//  GLOBAL SEARCH
// ══════════════════════════════════════════════════════════════════════
function initSearch() {
  const input = document.getElementById("global-search");
  const results = document.getElementById("search-results");
  const run = debounce(() => {
    const q = input.value.trim().toLowerCase();
    if (q.length < 2) { results.hidden = true; results.innerHTML = ""; return; }
    const props = publicProps().filter(p =>
      (p.name || "").toLowerCase().includes(q) || (p.team || "").toLowerCase().includes(q)
      || (p.matchup || "").toLowerCase().includes(q)).slice(0, 8);
    const games = (DATA.schedule || []).filter(g =>
      `${g.away_team} ${g.home_team}`.toLowerCase().includes(q)).slice(0, 4);
    if (!props.length && !games.length) {
      results.innerHTML = `<div class="search-empty">No matches for "${esc(input.value)}"</div>`;
    } else {
      let html = "";
      if (props.length) {
        html += `<div class="search-group-label">Players &amp; Props</div>`;
        html += props.map(p => `<button class="search-item" data-open="${p.id}">
          <span>${esc(p.name)} — ${esc(p.prop)}</span><span class="s-sub">${pctBig(p.hit_probability)}</span></button>`).join("");
      }
      if (games.length) {
        html += `<div class="search-group-label">Games</div>`;
        html += games.map(g => `<button class="search-item" data-game="${g.game_pk}">
          <span>${esc(g.away_team)} @ ${esc(g.home_team)}</span><span class="s-sub">${gameTimeLabel(g.game_start)}</span></button>`).join("");
      }
      results.innerHTML = html;
    }
    results.hidden = false;
    $all("[data-open]", results).forEach(b => b.addEventListener("click", () => { openDetail(b.dataset.open); results.hidden = true; input.blur(); }));
    $all("[data-game]", results).forEach(b => b.addEventListener("click", () => { go("games"); results.hidden = true; input.blur(); }));
  }, 150);
  input.addEventListener("input", run);
  input.addEventListener("focus", run);
  document.addEventListener("click", e => {
    if (!e.target.closest(".header-search")) results.hidden = true;
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Escape") { results.hidden = true; input.blur(); }
  });
}

// ══════════════════════════════════════════════════════════════════════
//  FRESHNESS
// ══════════════════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════════════════
//  LIVE FRESHNESS — 2026-08-19 Live Integrity PR 1. Deterministic,
//  wall-clock-relative staleness for LIVE GAME data specifically --
//  distinct from p.stale (recommendation.py's PREGAME board/price
//  freshness gate for Top Pick eligibility; a different concept, already
//  handled by staleChip() below).
//
//  Never trust a backend-computed boolean for this: a boolean written by a
//  scheduler that has stopped running would go stale right along with
//  everything else it wrote. Instead this compares wall-clock `now`
//  (always available to the browser, even if every backend workflow has
//  silently stopped) against grades_checked_at -- a heartbeat that
//  dashboard/live_state.py's touch_heartbeat() advances every time
//  refresh_grades.py completes a REAL attempt against the live MLB feed,
//  whether or not anything actually changed (see that function's own
//  docstring). grades_updated_at is deliberately NOT used here: it only
//  advances when a fact changed, so a long scoreless stretch would look
//  identical to a stopped scheduler if this used that field instead.
//
//  THRESHOLD BASIS (not an assumed SLA): dashboard-live.yml declares a
//  */5 cron, but real measured gaps during the 2026-08-18 Chase Meidroth
//  incident investigation (27 real observed gaps that day) were median
//  30.3min / p90 46.8min / p95 50.8min / max 57.9min, with 27/27 exceeding
//  10 minutes and 26/27 exceeding 20 minutes -- GitHub's own cron
//  scheduling alone cannot be trusted to fire every 5 minutes. 15 minutes
//  is chosen because it sits well inside that measured failure band (so
//  this correctly flags STALE during the exact kind of gap that produced
//  the incident) while staying loose enough that one ordinary retry-cycle
//  blip does not flicker the banner. Revisit once the independent
//  scheduling watchdog is live and a fresh healthy-state cadence has
//  actually been measured against it.
// ══════════════════════════════════════════════════════════════════════
const LIVE_STALE_THRESHOLD_SECONDS = 15 * 60;
const LIVE_IN_PROGRESS_GAME_STATES = new Set(["live", "delayed", "suspended", "unknown"]);

// Pure and deterministic: `now` is always an explicit argument, never
// Date.now() read internally, so this can be exercised with a fake clock
// (see test_live_freshness.py) instead of approximated.
function liveFreshnessState(nowMs, doc, props) {
  const anyInProgress = (props || []).some(p => LIVE_IN_PROGRESS_GAME_STATES.has(p.game_state));
  if (!anyInProgress) {
    return { applicable: false, stale: false, ageSeconds: null, reason: null };
  }
  const checkedAtMs = timeMs(doc && doc.grades_checked_at);
  if (checkedAtMs == null) {
    // A live/delayed/suspended/unknown game exists, but this browser has
    // no record of the grading channel ever having checked it -- honestly
    // uncertain, not confidently fresh. Never silently show a settlement
    // state as current when there is no evidence anyone has verified it.
    return { applicable: true, stale: true, ageSeconds: null, reason: "never_checked" };
  }
  const ageSeconds = Math.max(0, Math.round((nowMs - checkedAtMs) / 1000));
  const stale = ageSeconds > LIVE_STALE_THRESHOLD_SECONDS;
  return { applicable: true, stale, ageSeconds, reason: stale ? "age_exceeded" : null };
}

let LIVE_FRESHNESS = { applicable: false, stale: false, ageSeconds: null, reason: null };

function liveFreshnessAgoText(seconds) {
  if (seconds == null) return "unknown";
  const mins = Math.round(seconds / 60);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

// Per-prop chip, deliberately separate markup/copy from staleChip() above
// (which is p.stale, a different concept) even though it reuses the same
// visual treatment -- both mean "do not trust this number at face value,"
// which is exactly the shared affordance a viewer should learn to read.
function liveStaleChip(p) {
  if (!LIVE_FRESHNESS.applicable || !LIVE_FRESHNESS.stale) return "";
  if (!LIVE_IN_PROGRESS_GAME_STATES.has(p.game_state)) return "";
  const label = LIVE_FRESHNESS.reason === "never_checked" ? "Live Status Unknown" : "Live Data Stale";
  return `<span class="chip chip-stale">${label}</span>`;
}

function renderFreshness() {
  const bar = document.getElementById("freshness-bar");
  const parts = [];
  if (DATA.generated_at) parts.push(`Board built ${_agoText(DATA.generated_at)}`);
  if (DATA.prices_updated_at) parts.push(`odds updated ${_agoText(DATA.prices_updated_at)}`);
  const dateLabel = DATA.date ? LOCAL_DATE_FMT.format(new Date(DATA.date + "T12:00:00Z")) : "";
  const wasStale = LIVE_FRESHNESS.applicable && LIVE_FRESHNESS.stale;
  LIVE_FRESHNESS = liveFreshnessState(Date.now(), DATA, [...PROPS_BY_ID.values()]);
  let staleHtml = "";
  if (LIVE_FRESHNESS.applicable && LIVE_FRESHNESS.stale) {
    const msg = LIVE_FRESHNESS.reason === "never_checked"
      ? "LIVE DATA STATUS UNKNOWN — no live check on record"
      : `LIVE DATA STALE — last verified ${liveFreshnessAgoText(LIVE_FRESHNESS.ageSeconds)}`;
    staleHtml = ` · <span class="stale-flag">${esc(msg)}</span>`;
  }
  bar.innerHTML = `${esc(dateLabel)}${dateLabel ? " · " : ""}${esc(parts.join(" · "))}${staleHtml}`;
  // Only re-render prop cards when the verdict actually flips -- avoids a
  // needless full re-render every 60s while still guaranteeing per-prop
  // chips (liveStaleChip) never lag more than one tick behind the bar.
  if (LIVE_FRESHNESS.applicable && LIVE_FRESHNESS.stale !== wasStale) renderRoute();
}

// ══════════════════════════════════════════════════════════════════════
//  LIVE UPDATES — item 11/13: small polled delta, no full-page reload.
//  live.json carries ONLY the fields that change between full rebuilds
//  (price/status/grade), keyed by the same stable id every prop already
//  carries -- see dashboard/refresh_prices.py / refresh_grades.py.
// ══════════════════════════════════════════════════════════════════════
const LIVE_PRICE_FIELDS = new Set([
  "market_odds", "market_implied", "market_edge", "price_clears", "market_hold",
  "recommendation_status", "status_reasons", "stale", "market_observation_state",
  "market_observed_at", "market_family", "market_fetch_state", "market_fetch_checked_at",
  "market_fetch_failed_at", "market_failure_reason",
  "price_basis_board_generated_at",
]);
const LIVE_SETTLEMENT_FIELDS = new Set([
  "settlement_state", "settlement_authority", "settlement_observed_at",
  "settlement_source", "result_actual", "result_reason",
]);
const LIVE_GAME_FIELDS = new Set(["game_state", "game_state_observed_at", "game_state_source"]);
const RESULT_AUTHORITY = { none: 0, live_observation: 1, official_final: 2 };
function timeMs(iso) {
  const ms = Date.parse(iso || "");
  return Number.isFinite(ms) ? ms : null;
}
function newerStamp(a, b) {
  const am = timeMs(a), bm = timeMs(b);
  if (bm == null) return a || null;
  return am == null || bm > am ? b : a;
}
function incomingFieldStamp(doc, delta, field) {
  return (delta._field_updated_at || {})[field]
    || (LIVE_SETTLEMENT_FIELDS.has(field) || LIVE_GAME_FIELDS.has(field) ? doc.grades_updated_at : null)
    || (LIVE_PRICE_FIELDS.has(field) ? doc.prices_updated_at : null)
    || doc.updated_at;
}
function sameSettlement(a, b) {
  return [...LIVE_SETTLEMENT_FIELDS].every(field => (a[field] ?? null) === (b[field] ?? null));
}
function acceptSettlement(current, incoming) {
  if (!current.settlement_state) return true;
  const priorRank = RESULT_AUTHORITY[current.settlement_authority] ?? -1;
  const nextRank = RESULT_AUTHORITY[incoming.settlement_authority] ?? -1;
  if (priorRank !== nextRank) return nextRank > priorRank;
  const priorAt = timeMs(current.settlement_observed_at);
  const nextAt = timeMs(incoming.settlement_observed_at);
  if (nextAt == null) return false;
  if (priorAt == null || nextAt > priorAt) return true;
  return nextAt === priorAt && sameSettlement(current, incoming);
}
function applyFact(target, source, fields, stamp) {
  target._field_updated_at = target._field_updated_at || {};
  for (const field of fields) {
    if (Object.hasOwn(source, field)) target[field] = source[field];
    else delete target[field];
    if (stamp) target._field_updated_at[field] = stamp;
  }
}
function ingestLiveDocument(fresh) {
  for (const [id, delta] of Object.entries(fresh.props || {})) {
    const cached = LIVE_CACHE.props[id] || (LIVE_CACHE.props[id] = { _field_updated_at: {} });
    cached._field_updated_at = cached._field_updated_at || {};
    if (delta.settlement_state && acceptSettlement(cached, delta)) {
      applyFact(cached, delta, LIVE_SETTLEMENT_FIELDS, delta.settlement_observed_at);
    }
    if (delta.game_state) {
      const priorAt = timeMs(cached.game_state_observed_at);
      const nextAt = timeMs(delta.game_state_observed_at);
      const preservesKnown = delta.game_state === "unknown" && cached.game_state && cached.game_state !== "unknown";
      const regressesFinal = cached.game_state === "final" && delta.game_state !== "final";
      if (!preservesKnown && !regressesFinal && nextAt != null && (priorAt == null || nextAt >= priorAt)) {
        applyFact(cached, delta, LIVE_GAME_FIELDS, delta.game_state_observed_at);
      }
    }
    for (const [field, value] of Object.entries(delta)) {
      if (field === "_field_updated_at" || LIVE_SETTLEMENT_FIELDS.has(field) || LIVE_GAME_FIELDS.has(field)) continue;
      const nextAt = incomingFieldStamp(fresh, delta, field);
      const priorAt = cached._field_updated_at[field];
      if (timeMs(priorAt) != null
          && (timeMs(nextAt) == null || timeMs(nextAt) < timeMs(priorAt))) continue;
      cached[field] = value;
      if (nextAt) cached._field_updated_at[field] = nextAt;
    }
  }
  for (const key of ["updated_at", "prices_updated_at", "grades_updated_at",
                     "grades_checked_at", "prices_checked_at"]) {
    LIVE_CACHE[key] = newerStamp(LIVE_CACHE[key], fresh[key]);
  }
}
function applyCachedLive() {
  let changed = 0;
  const boardOddsAt = timeMs(DATA.odds_fetched_at || DATA.generated_at);
  for (const [id, delta] of Object.entries(LIVE_CACHE.props || {})) {
    const p = PROPS_BY_ID.get(id);
    if (!p) continue;
    if (delta.settlement_state && acceptSettlement(p, delta)) {
      for (const field of LIVE_SETTLEMENT_FIELDS) {
        const before = p[field];
        if (Object.hasOwn(delta, field)) p[field] = delta[field]; else delete p[field];
        if (p[field] !== before) changed++;
      }
    }
    if (delta.game_state) {
      const priorAt = timeMs(p.game_state_observed_at);
      const nextAt = timeMs(delta.game_state_observed_at);
      const preservesKnown = delta.game_state === "unknown" && p.game_state && p.game_state !== "unknown";
      const regressesFinal = p.game_state === "final" && delta.game_state !== "final";
      if (!preservesKnown && !regressesFinal && nextAt != null && (priorAt == null || nextAt >= priorAt)) {
        for (const field of LIVE_GAME_FIELDS) {
          const before = p[field];
          if (Object.hasOwn(delta, field)) p[field] = delta[field]; else delete p[field];
          if (p[field] !== before) changed++;
        }
      }
    }
    // The board may have been repriced or reclassified before this browser
    // learned that the game crossed its wagering boundary. Restore the exact
    // deployment-proven recommendation snapshot before considering any live
    // price fields. Game and settlement facts remain independently mutable.
    freezePublishedSnapshot(p);
    for (const [field, value] of Object.entries(delta)) {
      if (field === "_field_updated_at" || LIVE_SETTLEMENT_FIELDS.has(field) || LIVE_GAME_FIELDS.has(field)) continue;
      const frozenExposure = gameHasStarted(p)
        && ((!!p.published_top_pick_at && !!p.publication_artifact_id)
          || !!p.publication_candidate_token);
      if (frozenExposure && LIVE_PRICE_FIELDS.has(field)) continue;
      const fieldAt = timeMs((delta._field_updated_at || {})[field]);
      if (LIVE_PRICE_FIELDS.has(field) && boardOddsAt != null && fieldAt != null && fieldAt < boardOddsAt) continue;
      if (p[field] !== value) changed++;
      p[field] = value;
    }
  }
  if (LIVE_CACHE.prices_updated_at) DATA.prices_updated_at = LIVE_CACHE.prices_updated_at;
  if (LIVE_CACHE.grades_updated_at) DATA.grades_updated_at = LIVE_CACHE.grades_updated_at;
  if (LIVE_CACHE.grades_checked_at) DATA.grades_checked_at = LIVE_CACHE.grades_checked_at;
  if (LIVE_CACHE.prices_checked_at) DATA.prices_checked_at = LIVE_CACHE.prices_checked_at;
  refreshSummary();
  return changed;
}
async function pollLive() {
  try {
    const fresh = await fetchJSON("live.json");
    // grades_checked_at/prices_checked_at MUST be part of this dedup key,
    // not just the *_updated_at triplet: a heartbeat-only poll (system
    // healthy, nothing else changed this cycle) would otherwise be
    // silently dropped by the stamp===lastPollStamp guard below, meaning
    // ingestLiveDocument() never runs and the live freshness contract's
    // whole recovery path -- the browser noticing the backend is checking
    // again -- would never fire. The stale banner would then stay stuck
    // forever even once the backend is perfectly healthy again.
    const stamp = [fresh.updated_at, fresh.prices_updated_at, fresh.grades_updated_at,
                   fresh.grades_checked_at, fresh.prices_checked_at].join("|");
    if (stamp === lastPollStamp) return;
    lastPollStamp = stamp;
    ingestLiveDocument(fresh);
    const changed = applyCachedLive();
    if (changed > 0) { renderRoute(); }
    renderFreshness();
  } catch (e) { /* a missed poll just tries again next interval */ }
}

// Replaces the old forced `location.reload()` every 30 minutes: fetch the
// full board again, and only swap it in (preserving route/filters/scroll)
// if it's actually a NEW board (generated_at changed) -- otherwise this is
// a no-op. Direct instruction: "eliminating forced full-page reloads...
// preserving user state during updates."
async function pollFullBoard() {
  try {
    const fresh = await fetchJSON("data.json");
    const freshVersion = fresh.lifecycle_prepared_at || fresh.generated_at;
    const currentVersion = DATA.lifecycle_prepared_at || DATA.generated_at;
    if (freshVersion === currentVersion) return;
    const scrollY = window.scrollY;
    DATA = fresh;
    indexProps();
    applyCachedLive();
    renderFreshness();
    renderRoute();
    updateWatchCount();
    window.scrollTo(0, Math.min(scrollY, document.body.scrollHeight));
    showToast("Board updated with the latest picks.");
  } catch (e) { /* try again next interval */ }
}
function showToast(msg) {
  let t = document.getElementById("fc-toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "fc-toast";
    t.setAttribute("role", "status");
    t.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--ink);color:var(--ground);padding:10px 18px;border-radius:999px;font-size:13px;z-index:100;box-shadow:var(--shadow-lift);";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = "1";
  clearTimeout(t._hideTimer);
  t._hideTimer = setTimeout(() => { t.style.opacity = "0"; }, 4000);
}

// ══════════════════════════════════════════════════════════════════════
//  BOOT
// ══════════════════════════════════════════════════════════════════════
async function boot() {
  loadWatchlist();
  initTheme();
  const main = document.getElementById("main");
  for (const r of ROUTES) document.getElementById(`page-${r}`).innerHTML = `<div class="loading-state"><div class="spinner" role="status" aria-label="Loading"></div><p>Loading tonight's board…</p></div>`;
  document.getElementById(`page-today`).hidden = true;

  try {
    DATA = await fetchJSON("data.json");
  } catch (e) {
    document.getElementById("page-today").hidden = false;
    document.getElementById("page-today").innerHTML = `<div class="error-state">
      <h3>Couldn't load tonight's board</h3>
      <p>${esc(e.message || "Unknown error")}. <button class="btn" onclick="location.reload()">Try again</button></p>
    </div>`;
    return;
  }
  indexProps();
  updateWatchCount();
  initRouter();
  initSearch();
  renderFreshness();
  setInterval(renderFreshness, 60000);
  setInterval(pollLive, 3 * 60000);
  // Was 10 minutes -- the actual data.json publish cadence in production
  // (odds/lineup workflows dispatching Dashboard Refresh) runs as often as
  // every 15-20 minutes, so a 10-minute poll left an already-open tab up to
  // ~10 min behind a fresh board even when everything upstream (build,
  // deploy, CDN) was already healthy -- a real, bounded staleness window,
  // not a defect in the fetch/compare logic itself (fetchJSON already
  // cache-busts with ?t=Date.now() + {cache:"no-store"}, and pollFullBoard
  // already compares generated_at correctly). Matching pollLive's own 3-
  // minute cadence tightens that window without adding real load: this is
  // one small JSON GET, not the multi-minute FanGraphs/Statcast/FanDuel
  // pull Dashboard Refresh itself avoids running too often for.
  setInterval(pollFullBoard, 3 * 60000);
  pollLive();

  document.querySelectorAll("[data-close-detail]").forEach(el => el.addEventListener("click", closeDetail));
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDetail(); });
}

document.addEventListener("DOMContentLoaded", boot);
