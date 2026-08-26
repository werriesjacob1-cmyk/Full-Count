"use strict";
/* ============================================================================
   THIS IS THE SOURCE FILE. Edit here, not docs/app.js.
   dashboard/build_dashboard.py's copy_static_assets() overwrites docs/app.js
   from THIS file on every real dashboard build -- an edit made only to
   docs/app.js is silently lost the next time the site is rebuilt. (Real
   incident, 2026-08-25: a frontend fix initially landed only in docs/app.js.)
   test_build_dashboard.py's StaticSourceParityTests catches any drift
   between this file and docs/app.js on every test run -- resync from the
   repo root with:
       python3 -c "import sys; sys.path.insert(0,'dashboard'); \
           import build_dashboard as bd; bd.copy_static_assets(bd.REPO_ROOT+'/docs')"
   ============================================================================ */
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
// Multi-select prop filtering (Part 2, 2026-08-26): family/status/evidence
// are each a Set of selected values -- an EMPTY Set means unrestricted
// ("all"), matching the old sentinel string's meaning, so a user can filter
// to e.g. Hits + Home Runs, or Top Pick + Lean, simultaneously, instead of
// being forced to pick exactly one value per dimension.
let filters = { search: "", families: new Set(), statuses: new Set(), evidences: new Set(), gamePk: null, sort: "default" };
// selectedGamePk: which game (if any) the Games route is drilled into --
// separate from `filters` since it's the Games route's own concept, not an
// All Props filter (though a drill-down page can link INTO filters.gamePk
// via a real "See all props for this game" link -- see renderGameDetail()).
let selectedGamePk = null;
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
//  PROBABILITY BASIS — 2026-08-2X data-integrity fix (probability-drivers-
//  vs-matchup-context separation). Direct instruction: "do not let
//  contextual reasons imply they mathematically generated the headline
//  probability when they didn't." p.hit_probability (the big number) and
//  p.score (what ranks/labels the card) are TWO DIFFERENT numbers computed
//  by different mechanisms -- the "Why It Could Hit"/"Why It Could Miss"
//  facts below explain the SCORE and today's matchup context, not a
//  literal derivation of the probability. probability_basis/
//  probability_detail (newly exposed on the public payload -- see
//  dashboard/build_dashboard.py's clean()) are what actually produced the
//  probability, and belong here, in Evidence, not folded into Why.
const PROBABILITY_BASIS_LABELS = {
  empirical: "His own real rate this season",
  empirical_shrunk: "His own real rate, shrunk toward the league rate for a small sample",
  modelled: "A modelled projection (no direct empirical rate available)",
  modelled_shrunk: "A modelled projection, shrunk toward the league rate",
  blended: "A blend of his own real rate and a modelled projection",
  league_only: "The league rate — not enough of his own data to move off it",
  combined_shrunk: "A modelled combination for both starters, shrunk toward the league rate",
  modelled_independent_binomials: "A modelled combination for both starters (treated as independent)",
  unavailable: "Not available",
};
function probabilityBasisText(p) {
  const label = PROBABILITY_BASIS_LABELS[p.probability_basis];
  if (!label) return null;
  const detail = p.probability_detail || {};
  const parts = [];
  if (detail.empirical != null) parts.push(`his own rate ${pct(detail.empirical, 1)}`);
  if (detail.modelled != null) parts.push(`modelled ${pct(detail.modelled, 1)}`);
  return parts.length ? `${label} (${parts.join(", ")})` : label;
}
function probCiSourceText(p) {
  if (!p.prob_ci) return null;
  if (p.prob_ci_source === "historical_reliability_band") return "From this market's own historical track record, not this player's individual sample";
  if (p.prob_ci_source === "player_empirical") return "From this player's own real sample";
  return null;
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
// Real bug, found 2026-08-25: this only ever checked p.stale, a SEPARATE
// field from market_fetch_state -- dashboard/refresh_prices.py's
// FETCH_FAILED branch (a genuinely failed FanDuel re-fetch) never sets
// stale=True, it only sets market_fetch_state/market_failure_reason. So a
// price whose most recent fetch actually failed showed no chip at all on
// the compact card grid, looking identical to a freshly, successfully
// checked price -- even though priceFreshnessState() (the detail sheet)
// already correctly flagged this same row as "Last known - price fetch
// failed". Plain, simplified wording here (not the internal
// "FETCH_FAILED"/"market_failure_reason" jargon) so a viewer scanning
// cards, not opening every detail sheet, sees the same honest signal.
function staleChip(p) {
  if (p.market_fetch_state === "FETCH_FAILED") {
    return `<span class="chip chip-stale">Price May Be Outdated</span>`;
  }
  return p.stale ? `<span class="chip chip-stale">Stale Data</span>` : "";
}
// isTopPickSuspect(): true when classify_recommendation() flagged this Top
// Pick as one the market itself disagrees with (status_reasons carries a
// second entry -- see recommendation.py). PRODUCT DECISION (2026-08-26,
// direct instruction): the prior "⚠ Market Disagrees" badge + alarmist
// warning-box prose ("a large disagreement... is far more often a gap in
// the model than an edge in the market... size with that in mind") sat
// directly next to the TOP PICK badge and read as "this is one of our
// best picks, but maybe don't trust us" -- bad product hierarchy, and not
// something the (still fully open) locked disagreement research has
// earned the right to editorialize about yet. The signal itself is kept
// (still real, still computed, still available for internal/shadow use)
// -- only the customer-facing warning presentation is gone. Market
// disagreement is still shown, neutrally, via the Full Count vs. Market
// bars every prop already gets (modelVsMarketBlock()) -- that IS the
// honest "Full Count sees this differently from the market" context,
// without editorializing about which side is more likely right.
function isTopPickSuspect(p) {
  return p.recommendation_status === "top_pick" && (p.status_reasons || []).length > 1;
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
  // CI-provenance-honesty fix (P0-7) -- kept in sync with
  // dashboard/live_state.py's FROZEN_PUBLICATION_FIELDS by hand.
  "prob_ci_source",
  // 2026-08-2X market-edge-semantics fix (P0-6) -- kept in sync with
  // dashboard/live_state.py's FROZEN_PUBLICATION_FIELDS by hand, same as
  // every other entry here.
  "posted_implied", "market_fair", "market_fair_method", "edge_vs_fair",
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
//  MY BOARD (localStorage) -- "these are the props I'm considering
//  tonight," not a static bookmark list. A versioned snapshot per saved
//  id, captured once at save time, diffed against the live prop on every
//  render -- no server, no accounts, exactly what a static localStorage-
//  only architecture can honestly support. Route/storage keys keep their
//  original "watchlist" names (URLs and existing localStorage entries
//  stay valid); only user-facing text says "My Board." Real bug, found
//  2026-08-25: the save/detail-sheet star button's own label text was a
//  stray leftover that never got the "My Board" rename applied to it --
//  "Save to Watchlist"/"Saved to Watchlist" -- fixed to match this rule
//  the module itself already states.
//
//  SNAPSHOT VERSIONING (2026-08-25 expansion). v1 (pre-2026-08-25,
//  already live in real users' localStorage) only ever captured
//  {status, odds, lineup_assumed, started} -- genuinely audited before
//  touching this, not assumed: NO historical probability/edge/implied
//  was ever recorded, so "Since You Saved This" cannot show a
//  probability delta for a v1 save. normalizeSnapshot() below maps a v1
//  snapshot's fields onto the v2 shape it can honestly fill (status/
//  odds/lineup/started) and leaves everything v1 never captured
//  (hit_probability/market_implied/market_edge/saved_at) explicitly
//  undefined -- NEVER backfilled from the CURRENT prop and presented as
//  if it were the historical value, which would be a fabricated delta.
//  A fresh v2 save captures the full pregame-visible field set.
// ══════════════════════════════════════════════════════════════════════
const WATCH_KEY = "fc_watchlist_v1";
const WATCH_SNAP_KEY = "fc_watch_snapshot_v1";
const WATCH_SNAPSHOT_SCHEMA_VERSION = 2;
// Presentation-only thresholds: whether a real, structured delta is
// SURFACED as a "change" on My Board. These do NOT touch recommendation/
// model thresholds anywhere -- purely "is this movement big enough to
// bother telling the user about," to avoid alert fatigue on every
// sub-point wiggle. 2 percentage points is chosen as noticeably more than
// typical run-to-run float noise while still catching a real, meaningful
// move; both probability and edge share it since edge is itself a
// probability-scale quantity.
const WATCH_DISPLAY_THRESHOLD_PROB = 0.02;
const WATCH_DISPLAY_THRESHOLD_EDGE = 0.02;

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
  return {
    schema_version: WATCH_SNAPSHOT_SCHEMA_VERSION,
    saved_at: new Date().toISOString(),
    hit_probability: p.hit_probability,
    market_odds: p.market_odds,
    market_implied: p.market_implied,
    market_edge: p.market_edge,
    recommendation_status: p.recommendation_status,
    lineup_assumed: p.lineup_assumed,
    game_start: p.game_start,
    started: !!(p.game_start && new Date(p.game_start) <= new Date()),
  };
}
// Migrates a raw stored snapshot (v1 shape, or a hand-built v2 shape) to
// the one shape the rest of this module reads. Never crashes on an old
// entry, never invents a historical value a v1 save never captured.
function normalizeSnapshot(raw) {
  if (!raw) return null;
  if (raw.schema_version === WATCH_SNAPSHOT_SCHEMA_VERSION) return raw;
  // v1 (no schema_version field at all): map what genuinely corresponds,
  // leave the rest honestly absent.
  return {
    schema_version: 1,
    saved_at: null,
    hit_probability: null,
    market_odds: raw.odds ?? null,
    market_implied: null,
    market_edge: null,
    recommendation_status: raw.status ?? null,
    lineup_assumed: raw.lineup_assumed ?? null,
    game_start: null,
    started: !!raw.started,
  };
}
// "SINCE YOU SAVED THIS" -- a real, structured delta list, only for
// fields the snapshot actually captured, only past the presentation
// thresholds above (or, for categorical fields, any real change at all).
// Deterioration is never hidden: a shrinking edge or a falling
// probability renders exactly like an improving one, just with a
// different arrow/word, because trust is the entire point of showing
// this at all (direct instruction: "negative changes are part of what
// makes My Board trustworthy").
function sinceYouSavedChanges(p) {
  const snap = normalizeSnapshot(watchSnapshot[p.id]);
  if (!snap) return [];
  const changes = [];
  if (snap.hit_probability != null && p.hit_probability != null
      && Math.abs(p.hit_probability - snap.hit_probability) >= WATCH_DISPLAY_THRESHOLD_PROB) {
    changes.push({ key: "probability", label: "Model", from: pctBig(snap.hit_probability), to: pctBig(p.hit_probability),
      stronger: p.hit_probability > snap.hit_probability });
  }
  if (snap.market_odds != null && p.market_odds != null && snap.market_odds !== p.market_odds) {
    changes.push({ key: "odds", label: "FanDuel", from: fmtOdds(snap.market_odds), to: fmtOdds(p.market_odds) });
  }
  if (snap.market_implied != null && p.market_implied != null
      && Math.abs(p.market_implied - snap.market_implied) >= WATCH_DISPLAY_THRESHOLD_PROB) {
    changes.push({ key: "implied", label: "Market implied", from: pct(snap.market_implied, 0), to: pct(p.market_implied, 0) });
  }
  if (snap.market_edge != null && p.market_edge != null
      && Math.abs(p.market_edge - snap.market_edge) >= WATCH_DISPLAY_THRESHOLD_EDGE) {
    const fmt = v => (v >= 0 ? "+" : "") + Math.round(v * 100) + " pts";
    changes.push({ key: "edge", label: "Edge", from: fmt(snap.market_edge), to: fmt(p.market_edge),
      stronger: p.market_edge > snap.market_edge });
  }
  if (snap.lineup_assumed === true && p.lineup_assumed === false) {
    changes.push({ key: "lineup", label: "Lineup", from: "Projected", to: "Confirmed" });
  }
  if (snap.recommendation_status != null && snap.recommendation_status !== p.recommendation_status) {
    changes.push({ key: "status", label: "Status", from: STATUS_META[snap.recommendation_status]?.label || snap.recommendation_status,
      to: statusLabel(p) });
  }
  const startedNow = !!(p.game_start && new Date(p.game_start) <= new Date());
  if (!snap.started && startedNow) changes.push({ key: "started", label: "Game", from: "Not started", to: "Started" });
  return changes;
}
// Compact one-line summary for the collapsed My Board row -- "N changes"
// plus the most tellable single change, never every tiny wiggle spelled
// out. Deliberately favors a probability/edge direction word (matches the
// directive's own "Model stronger" / "Model weaker" examples) over a
// flatter fact like "Price moved" when both are present.
function changeSummary(changes) {
  if (!changes.length) return null;
  const strengthChange = changes.find(c => c.key === "probability" || c.key === "edge");
  let headline;
  if (strengthChange) {
    headline = `${strengthChange.label} ${strengthChange.stronger ? "stronger" : "weaker"}`;
  } else {
    const first = changes[0];
    headline = first.key === "status" ? `Now: ${first.to}` : `${first.label} changed`;
  }
  return changes.length > 1 ? `${changes.length} changes · ${headline}` : headline;
}
function updateWatchCount() {
  // Two nav instances now carry the My Board count -- .main-nav (desktop
  // pill row) and .bottom-nav (mobile, Part 3 of the UX revamp) -- both
  // share the .watchlist-count-el class instead of a single unique id.
  $all(".watchlist-count-el").forEach(el => {
    el.textContent = String(watchlist.size);
    el.hidden = watchlist.size === 0;
  });
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

// Mobile search collapse/expand (Part 4 of the UX revamp, 2026-08-26).
// #search-toggle only exists in the DOM below 560px in practice (CSS hides
// it above that width); wiring it unconditionally here is harmless since
// nothing calls .click() on a hidden button. Toggling .expanded on
// #header-search is a pure presentation change -- the input, its value,
// and every existing search listener (initSearch(), below) are untouched.
function initSearchToggle() {
  const toggle = document.getElementById("search-toggle");
  const wrap = document.getElementById("header-search");
  const input = document.getElementById("global-search");
  if (!toggle || !wrap || !input) return;
  const setExpanded = (open) => {
    wrap.classList.toggle("expanded", open);
    toggle.classList.toggle("active", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      // Wait for the expand transition to start before focusing so
      // iOS Safari doesn't jump-scroll the page while it's still 0-height.
      requestAnimationFrame(() => input.focus());
    } else {
      $("#search-results", wrap).hidden = true;
    }
  };
  toggle.addEventListener("click", () => setExpanded(!wrap.classList.contains("expanded")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && wrap.classList.contains("expanded")) setExpanded(false);
  });
  // Route changes (tapping a nav destination while search is open) should
  // collapse it back rather than leaving an empty expanded row behind.
  window.addEventListener("hashchange", () => setExpanded(false));
}

// ══════════════════════════════════════════════════════════════════════
//  ROUTER
// ══════════════════════════════════════════════════════════════════════
function initRouter() {
  window.addEventListener("hashchange", onRouteChange);
  onRouteChange();
}
// Real bug found 2026-08-25: the query-string half of a route hash
// (`#/props?status=lean`) was parsed off and thrown away here, so every
// "See all research →" link on the Today page silently landed on an
// UNFILTERED All Props page -- a link that looked like real navigation
// but did nothing. Now applied to `filters` on route entry.
//
// 2026-08-2X route-filter-leakage fix (Part 2 UX audit): the params were
// previously applied WITHOUT first resetting `filters`, on the theory that
// "an absent param must never silently clear a filter the user already
// set via the page's own UI" -- but renderProps()'s own checkbox/sort
// handlers mutate `filters` and re-render DIRECTLY (see the filter-dropdown
// checkbox/f-sort handlers below), never touching location.hash, so onRouteChange() is
// NEVER re-entered by on-page filter changes -- that worry described a
// path that doesn't exist. What DOES happen, confirmed live: click a
// status=top_pick tile -> filtered Props list -> navigate to Games ->
// click "All Props" in the main nav (a plain #/props link, no query) ->
// the OLD status=top_pick filter was still silently applied, with no
// visible reason and no easy way out ("Top Pick filter escape"). Every
// real hash-navigation INTO the props route is a fresh entry from outside
// the page, so it resets to defaults first, then applies whatever params
// this specific link actually carries -- a link can still pre-filter on
// purpose, it just can't leave a PREVIOUS visit's filter behind.
function onRouteChange() {
  const stripped = location.hash.replace(/^#\/?/, "") || "today";
  const [rawRoute, rawQuery] = stripped.split("?");
  route = ROUTES.includes(rawRoute) ? rawRoute : "today";
  if (route === "props") {
    filters = { search: "", families: new Set(), statuses: new Set(), evidences: new Set(), gamePk: null, sort: filters.sort };
    if (rawQuery) {
      const params = new URLSearchParams(rawQuery);
      // Multi-select fix (Part 2, 2026-08-26): a link can request more than
      // one value per dimension via a comma-separated list (e.g.
      // "?family=hits,home_runs"), same convention on both sides -- see
      // the "See all" link builders in initSearch()/renderToday() below,
      // which still emit a single value and work unchanged as a 1-entry set.
      if (params.has("family")) filters.families = new Set(params.get("family").split(",").filter(Boolean));
      if (params.has("status")) filters.statuses = new Set(params.get("status").split(",").filter(Boolean));
      // "See all N matching props" (global search, no single-market
      // intent) -- destination-integrity fix: this link used to carry no
      // filter at all for a plain name/team search, landing on the full,
      // unfiltered list instead of the N props it promised.
      if (params.has("search")) filters.search = params.get("search");
      // Games drill-down (Part 2, 2026-08-26): "See all N props for this
      // game" on a game's detail page scopes All Props to exactly that
      // real game_pk, reusing the same filtering engine multi-select
      // already runs through -- not a separate, second research surface.
      if (params.has("game_pk")) filters.gamePk = Number(params.get("game_pk"));
    }
  }
  if (route === "games") {
    // Games drill-down (Part 2, 2026-08-26): same route-filter-leakage
    // discipline as props above -- a fresh navigation into #/games (a
    // plain nav-bar click, no query) must show the real list, not silently
    // keep showing whatever game a PREVIOUS visit drilled into.
    const params = rawQuery ? new URLSearchParams(rawQuery) : null;
    selectedGamePk = params && params.has("game_pk") ? Number(params.get("game_pk")) : null;
  }
  // Performance moved out of .main-nav into the always-visible header icon
  // (UX decision, 2026-08-26) -- included here explicitly so it still gets
  // the real active-state indication every other primary destination does.
  // .bottom-nav (Part 3 of the UX revamp, 2026-08-26) shares the same
  // data-route contract as .main-nav, so one selector covers both --
  // whichever one is actually visible at the current viewport width shows
  // the right active state without any extra wiring.
  $all(".main-nav a, .bottom-nav a, #performance-link").forEach(a => {
    a.classList.toggle("active", a.dataset.route === route);
    if (a.classList.contains("bn-item")) a.setAttribute("aria-current", a.dataset.route === route ? "page" : "false");
  });
  $all(".page").forEach(p => p.hidden = true);
  document.getElementById(`page-${route}`).hidden = false;
  renderRoute();
}
function go(newRoute, query) { location.hash = "#/" + newRoute + (query ? "?" + query : ""); }

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
  // Real bug, found 2026-08-26 (Part 2 item 5, richer compact cards): this
  // used p.market_implied (the raw price-implied probability) and
  // p.market_edge unconditionally -- the SAME market-edge-semantics gap
  // the P0-6 fix already closed for the detail sheet (market_edge "mixes
  // exact and approximate comparators under one name," per
  // dashboard/build_dashboard.py's own clean() comment). detailBody()
  // already prefers market_fair/edge_vs_fair (the honest, market-hold-
  // aware comparator) when present, falling back to the older fields only
  // when it isn't -- mirrored here so the compact card and the detail
  // sheet never show two different "edge" numbers for the same prop.
  const marketProb = p.market_fair ?? p.market_implied;
  const edge = p.edge_vs_fair ?? p.market_edge;
  const edgeText = edge == null ? "—" : (edge >= 0 ? "+" : "") + Math.round(edge * 100) + " pts";
  const edgeClass = edge == null ? "" : (edge >= 0 ? "pos" : "neg");
  // Mini Full-Count-vs-Market bar (Part 12 of the UX revamp, 2026-08-26):
  // a filled track at Full Count's probability with a tick mark at the
  // market's fair probability -- the same two numbers already printed as
  // text just above, given a second, visual form. Skipped entirely when
  // either number is missing rather than drawing a misleading partial bar.
  const miniBar = (p.hit_probability != null && marketProb != null)
    ? `<div class="pc-mini-track" aria-hidden="true">
         <div class="pc-mini-fill" style="width:${Math.round(p.hit_probability * 100)}%"></div>
         <div class="pc-mini-mark" style="left:${Math.round(marketProb * 100)}%"></div>
       </div>` : "";
  return `<div class="pc-market">
    <div><span class="book-price">${marketOdds}</span> <span class="m-detail">FanDuel</span></div>
    <div class="m-detail">Market: ${pct(marketProb, 0)}</div>
    <div class="pc-edge ${edgeClass}">${edgeText} edge</div>
    ${miniBar}
  </div>`;
}
function pickCard(p) {
  // Evidence quality is deliberately NOT repeated here -- it's one tap away
  // in the detail sheet's "Underlying data," and showing it on every single
  // card in a grid of a dozen-plus picks was pure chip clutter, not a
  // decision a viewer needs to make before opening a card.
  const chips = [statusChip(p), lineupChip(p), staleChip(p), liveStaleChip(p), gradeChip(p)].filter(Boolean).join("");
  // No "TOP PICK #N" ordinal badge here (removed 2026-08-25). Audited
  // whether production has a real canonical order for this UNCAPPED
  // top_pick population and found it does not: classify_recommendation()
  // labels every candidate that clears the gates, with no top-N selection
  // at all (a real 15-Top-Pick night ships all 15, uncapped -- see
  // test_build_dashboard.py check "every real qualifying Top Pick ships,
  // uncapped"). generate_picks.rank_for_board()'s reliability/edge/
  // probability ordering is real, but belongs to the SEPARATE, capped
  // static top10 board pipeline (select_main_board(ranked, n=10)), not
  // this one. Claiming "#1 Top Pick" for this population would assert an
  // authoritative ranking that doesn't actually exist here -- exactly
  // what "do not invent #1/#2/#3" forbids. p.rank (still attached by
  // dashboard/build_dashboard.py's _assign_top_pick_rank()) is kept ONLY
  // as an internal display-order tiebreak for stable card ordering across
  // renders -- statusChip(p) above already shows "TOP PICK" once, which
  // is the one real, defensible claim this card makes.
  const why = (p.why || [])[0] ? `<div class="pc-why">${esc(capSentence(humanizeReason(p.why[0])))}</div>` : "";
  // Real bug, found 2026-08-26 (Part 2 item 5, richer compact cards): this
  // was computed and then never once used anywhere in the template below --
  // a viewer browsing a grid of a dozen-plus cards had no way to see which
  // ones they'd already saved to My Board without opening each one. Not
  // made independently clickable here (the whole card is already a single
  // <button data-open>, and nesting a real <button> inside it would be
  // invalid, inaccessible HTML) -- a plain visual indicator, same
  // "computed, then discarded" pattern already fixed elsewhere in this
  // project, just for a boolean instead of a sentence.
  const starred = watchlist.has(p.id);
  // Real bug, found 2026-08-26 (Part 2 item 5, richer compact cards): this
  // preferred p.team (the player's OWN team alone, e.g. "Athletics") over
  // p.matchup (the real game, "Athletics @ Astros") whenever both existed
  // -- which is every real row -- so the compact card never showed the
  // opponent at all, and never showed a start time despite p.game_start
  // already being on every row. A viewer had to open the detail sheet just
  // to see who a player was actually facing or when the game started.
  const subLine = esc(p.matchup || p.team || "") +
    (p.game_start ? ` · ${esc(gameTimeLabel(p.game_start))}` : "");
  return `<button class="pick-card status-${p.recommendation_status || "neutral"} ${lifecycleClass(p)}${p.stale ? " status-stale" : ""}" data-open="${p.id}">
    <div class="pc-top">
      <div>
        <div class="pc-name">${esc(p.name)}</div>
        <div class="pc-sub">${subLine}</div>
      </div>
      ${starred ? `<span class="pc-saved" aria-label="Saved to My Board">★</span>` : ""}
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

// Slate Pulse (Part 9 of the UX revamp, 2026-08-26): one glanceable strip
// of real facts about tonight's slate, replacing the plain "Today" heading
// this page used to open with -- date, games, top picks, how many games
// have a confirmed lineup (derived, not fabricated: a game counts as
// confirmed only once every batter prop attached to it has
// lineup_assumed === false; a game with zero batter props on the board
// yet is simply excluded from the denominator rather than guessed at),
// and how long ago prices were last checked (reuses _agoText/
// DATA.prices_updated_at, the same source priceFreshnessState() already
// trusts in the detail sheet). Every number here is a real count; nothing
// is invented "activity."
function slatePulse(props) {
  const dateLabel = DATA.date
    ? new Date(DATA.date + "T12:00:00Z").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
    : "";
  const nGames = (DATA.schedule || []).length;
  const nTopPick = (DATA.summary || {}).n_top_pick ?? 0;

  const byGame = new Map();
  for (const p of props) {
    if (p.type !== "batter" || !p.game_pk) continue;
    if (!byGame.has(p.game_pk)) byGame.set(p.game_pk, true);
    if (p.lineup_assumed === true) byGame.set(p.game_pk, false);
  }
  const gamesWithBatterProps = byGame.size;
  const gamesConfirmed = Array.from(byGame.values()).filter(Boolean).length;

  // "Odds updated Xm ago" deliberately NOT repeated here -- #freshness-bar
  // (rendered just above this, renderFreshness()) already owns that exact
  // fact, with the stale-data warning treatment it needs; duplicating it
  // in plainer styling right underneath would bury the one place that
  // warning is supposed to stand out.
  const facts = [];
  facts.push(`<span class="sp-fact"><b>${nGames}</b> game${nGames === 1 ? "" : "s"}</span>`);
  facts.push(`<span class="sp-fact"><b>${nTopPick}</b> Top Pick${nTopPick === 1 ? "" : "s"}</span>`);
  if (gamesWithBatterProps > 0) {
    facts.push(`<span class="sp-fact"><b>${gamesConfirmed}/${gamesWithBatterProps}</b> lineups confirmed</span>`);
  }
  return `<div class="slate-pulse">
    ${dateLabel ? `<span class="sp-date">${esc(dateLabel).toUpperCase()}</span>` : ""}
    ${facts.join(`<span class="sp-dot">·</span>`)}
  </div>`;
}

// ══════════════════════════════════════════════════════════════════════
//  TODAY PAGE
// ══════════════════════════════════════════════════════════════════════
// PASS 2 SIMPLIFICATION (2026-08-25): the Today page used to surface Top
// Picks / Best Value / Longshots / Leans / Full Count Radar / Suggested
// Parlay / Hot Streaks / Games as eight separate, competing concepts --
// too much taxonomy for a first glance. Nothing about the underlying
// recommendation_status states changed (every card still carries its own
// real Lean/Value/Longshot label via statusChip -- see pickCard/propRow),
// only how many top-level SECTIONS a visitor has to parse before reaching
// real research. New shape: glance tiles -> Best Bets (official Top
// Picks) -> Explore by Prop (quick market jump) -> More Picks (every
// other real Lean/Value/Longshot read, one merged list, still individually
// labeled) -> Tonight's Games -> Trends (renamed from Hot Streaks, framed
// explicitly as trend context, not a recommendation) -> Suggested Parlay
// (demoted to the very bottom -- real, but never the homepage's point).
function renderToday() {
  const el = document.getElementById("page-today");
  const props = publicProps();
  // Prefer the backend's own display order (dashboard/build_dashboard.py's
  // _assign_top_pick_rank()) for stable card ordering -- but this is NOT a
  // claim of an authoritative "#1 Top Pick" (re-audited 2026-08-25; see
  // that function's own docstring for why no such canonical order exists
  // for this uncapped population). pickCard() no longer renders an
  // ordinal for exactly that reason. The edge-only fallback below only
  // matters for a stale cached data.json from before `rank` existed.
  const topPicks = props.filter(p => p.recommendation_status === "top_pick")
    .sort((a, b) => (a.rank != null && b.rank != null) ? a.rank - b.rank
                    : (b.market_edge || 0) - (a.market_edge || 0));
  const valueAll = props.filter(p => p.recommendation_status === "value" && !isLongshot(p))
    .sort((a, b) => (b.market_edge || 0) - (a.market_edge || 0));
  const longshotsAll = props.filter(isLongshot)
    .sort((a, b) => (b.market_edge || 0) - (a.market_edge || 0));
  const leansAll = props.filter(p => p.recommendation_status === "lean")
    .sort((a, b) => (b.lift || 0) - (a.lift || 0));
  // MORE PICKS: every real Lean/Value/Longshot read in one list, ranked by
  // whichever real number each one actually has (edge for Value/Longshot,
  // lift for Lean -- never blended into one fake combined score). Each
  // card/row still shows its own real status chip, so nothing about which
  // KIND of read this is gets lost by merging the sections.
  const morePicks = [...leansAll, ...valueAll, ...longshotsAll]
    .sort((a, b) => (b.market_edge ?? b.lift ?? 0) - (a.market_edge ?? a.lift ?? 0));

  const summary = DATA.summary || {};
  // Value/Longshot count-integrity fix (Part 2 UX audit): this used to be
  // one combined "Value / Longshots" tile showing summary.n_value (EVERY
  // real recommendation_status==="value" row, longshots included) linking
  // to #/props?status=value -- but applyFilters()'s own "value" branch
  // explicitly EXCLUDES longshots (`!isLongshot(p)`), so the destination
  // page always showed FEWER rows than the tile promised. valueAll/
  // longshotsAll (computed above, the same real split "More Picks" itself
  // renders from) are the two real, mutually exclusive counts -- each now
  // gets its own tile linking to its own correctly-filtered destination.
  let html = slatePulse(props);
  html += `
    <div class="stat-row">
      <a class="stat-tile" href="#/props?status=top_pick"><span class="n">${summary.n_top_pick ?? 0}</span><span class="l">Top Picks tonight</span></a>
      <a class="stat-tile" href="#/props?status=lean"><span class="n">${summary.n_lean ?? 0}</span><span class="l">Leans on the board</span></a>
      <a class="stat-tile" href="#/props?status=value"><span class="n">${valueAll.length}</span><span class="l">Value bets</span></a>
      <a class="stat-tile" href="#/props?status=longshot"><span class="n">${longshotsAll.length}</span><span class="l">Longshots</span></a>
      <a class="stat-tile" href="#/games"><span class="n">${summary.n_games ?? 0}</span><span class="l">Games tonight</span></a>
    </div>`;

  html += `<section class="section"><div class="section-head"><h2>Best Bets</h2>
    <span class="section-sub">Full Count's official Top Picks — probability, evidence, price, and freshness all cleared.</span></div>`;
  if (topPicks.length) {
    html += `<div class="card-grid">${topPicks.map(p => pickCard(p)).join("")}</div>`;
  } else {
    html += topPickGapExplainer(props);
  }
  html += `</section>`;

  if ((DATA.families || []).length) {
    html += exploreByPropStrip(DATA.families);
  }

  if (morePicks.length) {
    html += `<section class="section"><div class="section-head"><h2>More Picks</h2>
      <span class="section-sub">Every other real read on tonight's board — Leans, Value, and Longshots, each still labeled on its own card. Probability asks "will this happen?" Value asks "does the price pay fairly for that chance?" — two different questions, never blended into one.</span>
      <a class="see-all" href="#/props">See all research →</a></div>
      <div class="prop-list">${morePicks.slice(0, 18).map(propRow).join("")}</div></section>`;
  }

  if ((DATA.schedule || []).length) {
    html += `<section class="section"><div class="section-head"><h2>Tonight's Games</h2>
      <a class="see-all" href="#/games">See all →</a></div>
      <div class="schedule-strip">${DATA.schedule.slice(0, 15).map(scheduleChip).join("")}</div></section>`;
  }

  if ((DATA.streaks || []).length) {
    html += `<section class="section"><div class="section-head"><h2>Trends</h2>
      <span class="section-sub">Real streaks worth knowing about — context, not a recommendation. A hot streak alone is never why Full Count likes a prop.</span></div>
      <div class="streak-strip">${DATA.streaks.slice(0, 12).map(streakChip).join("")}</div></section>`;
  }

  if (DATA.suggested_parlay && DATA.suggested_parlay.legs && DATA.suggested_parlay.legs.length) {
    html += suggestedParlayBlock(DATA.suggested_parlay);
  }

  el.innerHTML = html;
  wireCardOpeners(el);
}
// Mobile-first quick navigator (PASS 3): one tap from Today straight into
// a filtered All Props view for the highest-interest markets -- no wall of
// every family FanDuel prices. Real counts only (DATA.families, already
// deduplicated/computed server-side -- see build_dashboard.py's
// build_payload()); a family with zero real props tonight simply doesn't
// get a chip, never a dead tap. familyFilterValue() reused, not
// reimplemented, so this maps "moonshot" -> "home_runs" the exact same way
// the All Props filter dropdown already does.
const EXPLORE_PROP_CHIPS = [
  { family: "hits", label: "Hits" },
  { family: "total_bases", label: "2+ Bases" },
  { family: "home_runs", label: "HR" },
  { family: "hits_runs_rbis", label: "H+R+RBI" },
  { family: "strikeouts", label: "Ks" },
  { family: "pitcher_outs", label: "Outs" },
];
function exploreByPropStrip(families) {
  const countByFamily = {};
  for (const f of families) countByFamily[familyFilterValue(f.stat)] = f.count;
  const chips = EXPLORE_PROP_CHIPS.map(({ family, label }) => {
    const count = countByFamily[family];
    if (!count) return "";
    return `<a class="explore-chip" href="#/props?family=${family}">
      <span class="explore-chip-label">${esc(label)}</span>
      <span class="explore-chip-count">${count} tonight</span>
    </a>`;
  }).filter(Boolean).join("");
  if (!chips) return "";
  // Real bug, found 2026-08-25: on mobile, EXPLORE_PROP_CHIPS plus the
  // trailing "More" chip routinely overflows the viewport width (up to 7
  // chips at ~86px each, ~600px total, against a ~375-430px phone screen),
  // so the strip needed a horizontal scroll -- but overflow-x:auto alone
  // gives no visual hint that more chips (including "More," the one link to
  // the full All Props page) exist past the hard-cut right edge. Wrapped in
  // .explore-strip-wrap so app.css can add a real edge-fade affordance
  // (a common, well-understood "there's more this way" mobile pattern)
  // without changing the chip markup/behavior itself.
  return `<section class="section explore-by-prop"><div class="section-head"><h2>Explore by Prop</h2></div>
    <div class="explore-strip-wrap"><div class="explore-strip">${chips}<a class="explore-chip explore-chip-more" href="#/props">More</a></div></div></section>`;
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
// Real bug, found 2026-08-25: this read l.american and parlay.combined_american
// -- neither field exists. parlay_builder.py / dashboard/build_dashboard.py's
// _build_suggested_parlay() actually name them market_odds (per leg) and
// combined_american_odds (the combined figure). Every real, correctly priced
// leg silently rendered a blank price, and the "Combined:" line always fell
// back to "--" -- never a fabricated number, but a fully-priced real parlay
// looked broken/unpriced regardless. Also: naive_probability_note and
// correlation_notes -- the backend's own honesty context explaining that the
// combined figure assumes leg independence and is a conservative floor, not
// a final answer -- were computed and never reached the page at all (the
// same "computed, then discarded" bug class found repeatedly elsewhere in
// this project). Fixed field names, and the combined odds line is now
// explicitly labeled "Estimated" with that real caveat text surfaced.
function suggestedParlayBlock(parlay) {
  const legs = (parlay.legs || []).map(l =>
    `<div class="parlay-leg"><span>${esc(l.name)} — ${esc(l.prop)}</span><span>${fmtOdds(l.market_odds) ?? "—"}</span></div>`).join("");
  const combined = fmtOdds(parlay.combined_american_odds);
  const note = parlay.naive_probability_note
    ? `<p class="parlay-note">${esc(parlay.naive_probability_note)}</p>` : "";
  const corrNotes = (parlay.correlation_notes || []).length
    ? `<ul class="parlay-corr-notes">${parlay.correlation_notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
  return `<section class="section"><div class="parlay-card">
    <div class="section-head"><h2 style="font-size:16px">Suggested Parlay</h2></div>
    <div class="parlay-legs">${legs}</div>
    <div class="pc-sub">Estimated combined odds: ${combined ?? "unavailable"}</div>
    ${note}
    ${corrNotes}
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
// A row matches the status filter if it matches ANY selected status
// (OR, not AND -- "Top Pick + Lean" means show both, never neither).
// Longshot/Value each need their own real recommendation_status/probability
// check (see isLongshot()'s own definition), not a plain equality, so this
// is pulled out to a shared helper rather than duplicated per call site.
function matchesStatusFilter(p, statusSet) {
  for (const s of statusSet) {
    if (s === "longshot" ? isLongshot(p)
      : s === "value" ? (p.recommendation_status === "value" && !isLongshot(p))
      : p.recommendation_status === s) return true;
  }
  return false;
}
function applyFilters(props) {
  let rows = props;
  if (filters.gamePk != null) rows = rows.filter(p => p.game_pk === filters.gamePk);
  if (filters.families.size) rows = rows.filter(p => filters.families.has(p.stat));
  if (filters.statuses.size) rows = rows.filter(p => matchesStatusFilter(p, filters.statuses));
  if (filters.evidences.size) rows = rows.filter(p => filters.evidences.has(p.reliability));
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
// filterDropdown(): a native <details>/<summary> multi-select popover --
// checkboxes inside, real keyboard/screen-reader support for free (no
// bespoke open/close/outside-click JS needed), styled to match the site's
// existing pill-button filter chips. setKey is the filters.* Set this
// dropdown edits directly ("families"/"statuses"/"evidences").
function filterDropdown(setKey, label, options) {
  const selected = filters[setKey];
  return `<details class="filter-dropdown" data-set="${setKey}">
    <summary class="filter-select">${esc(label)}${selected.size ? ` (${selected.size})` : ""}</summary>
    <div class="filter-dropdown-panel">
      ${options.map(([v, l]) => `<label class="filter-dropdown-opt">
        <input type="checkbox" value="${esc(v)}"${selected.has(v) ? " checked" : ""}> ${esc(l)}
      </label>`).join("")}
    </div>
  </details>`;
}
const STATUS_FILTER_OPTIONS = [
  ["top_pick", "Top Pick"], ["lean", "Lean"], ["value", "Value"],
  ["longshot", "Longshot"], ["neutral", "No Strong Lean"],
];
const EVIDENCE_FILTER_OPTIONS = [
  ["A", "Strong evidence"], ["B", "Solid evidence"], ["C", "Developing evidence"], ["D", "Limited evidence"],
];
function renderProps() {
  const el = document.getElementById("page-props");
  const families = DATA.families || [];
  const familyOptions = families.map(f => [familyFilterValue(f.stat), `${f.label} (${f.count})`]);

  el.innerHTML = `
    <div class="section-head"><h2>All Props</h2><span class="section-sub" id="props-count"></span></div>
    <div class="filter-bar">
      <div class="filter-inline" style="display:flex;gap:8px;flex-wrap:wrap;">
        ${filterDropdown("families", "Prop type", familyOptions)}
        ${filterDropdown("statuses", "Status", STATUS_FILTER_OPTIONS)}
        ${filterDropdown("evidences", "Evidence", EVIDENCE_FILTER_OPTIONS)}
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
      <span class="filter-count desktop-only" id="props-active-count"></span>
      <button class="filter-chip-btn" id="f-clear-all" hidden>Clear all</button>
    </div>
    <div class="active-search-note section-sub" id="props-game-note" hidden></div>
    <div class="active-search-note section-sub" id="props-search-note" hidden></div>
    <div class="prop-list" id="props-list"></div>
  `;
  $("#f-sort", el).value = filters.sort;
  $("#f-sort", el).addEventListener("change", e => { filters.sort = e.target.value; refreshPropsList(el); });
  $("#f-open-sheet", el).addEventListener("click", () => openFilterSheet());
  // Multi-select fix (Part 2, 2026-08-26): a checkbox toggle only needs the
  // count/list to update, never a full innerHTML replace of the filter bar
  // -- replacing the <details> element the checkbox lives inside would
  // instantly close the dropdown the user is still interacting with.
  $all(".filter-dropdown", el).forEach(dd => {
    const setKey = dd.dataset.set;
    $all('input[type="checkbox"]', dd).forEach(cb => cb.addEventListener("change", () => {
      if (cb.checked) filters[setKey].add(cb.value); else filters[setKey].delete(cb.value);
      refreshPropsList(el);
    }));
  });
  // "Top Pick filter escape" fix (Part 2 UX audit): the filter dropdowns
  // were each individually resettable, but there was no single control to
  // exit a filtered view in one action -- a real gap once combined with
  // the route-filter-leakage fix above (that fix stops a STALE filter
  // from a past visit leaking in on fresh navigation, but a user still
  // deliberately filtering the page in the current visit needs a fast,
  // obvious way out). Persistent (2026-08-26): the button is always in the
  // DOM now (just hidden when nothing is active) rather than conditionally
  // inserted/removed, so refreshPropsList() can toggle it on every filter
  // change -- including a multi-select checkbox toggle -- without a full
  // renderProps() re-render.
  $("#f-clear-all", el).addEventListener("click", () => {
    filters = { search: "", families: new Set(), statuses: new Set(), evidences: new Set(), gamePk: null, sort: filters.sort };
    renderProps();
  });

  refreshPropsList(el);
  wireCardOpeners(el);
}
// The part of renderProps() that changes on every filter/sort interaction:
// the result count, each dropdown's own selected-count badge, the active-
// filter count, Clear-all visibility, the search note, and the list itself.
// Deliberately never touches the <details> elements' own open/closed state
// or rebuilds their checkbox markup -- see the checkbox handler's comment.
function refreshPropsList(el) {
  const visible = publicProps();
  const rows = applyFilters(visible);
  $("#props-count", el).textContent = `${rows.length} of ${visible.length} props`;
  $all(".filter-dropdown", el).forEach(dd => {
    const n = filters[dd.dataset.set].size;
    const base = dd.querySelector("summary").textContent.replace(/\s*\(\d+\)$/, "");
    dd.querySelector("summary").textContent = n ? `${base} (${n})` : base;
  });
  const activeCount = activeFilterCount();
  $("#props-active-count", el).textContent = `${activeCount} active`;
  $("#f-clear-all", el).hidden = activeCount === 0;
  // Games drill-down (Part 2, 2026-08-26): honest context for how a viewer
  // got here scoped to one real game -- same pattern as the search note,
  // with its own "clear" that only lifts the game scope (search/other
  // filters, if any, stay exactly as the viewer set them).
  const gameNote = $("#props-game-note", el);
  const scopedGame = filters.gamePk != null ? (DATA.schedule || []).find(g => g.game_pk === filters.gamePk) : null;
  if (scopedGame) {
    gameNote.hidden = false;
    gameNote.innerHTML = `Showing props for ${esc(scopedGame.away_team || "")} @ ${esc(scopedGame.home_team || "")} <button class="link-btn" id="f-clear-game">clear</button>`;
    $("#f-clear-game", gameNote).addEventListener("click", () => { filters.gamePk = null; refreshPropsList(el); });
  } else {
    gameNote.hidden = true;
    gameNote.innerHTML = "";
  }
  const searchNote = $("#props-search-note", el);
  if (filters.search) {
    searchNote.hidden = false;
    searchNote.innerHTML = `Filtered to "${esc(filters.search)}" <button class="link-btn" id="f-clear-search">clear</button>`;
    $("#f-clear-search", searchNote).addEventListener("click", () => { filters.search = ""; refreshPropsList(el); });
  } else {
    searchNote.hidden = true;
    searchNote.innerHTML = "";
  }
  const list = $("#props-list", el);
  list.innerHTML = rows.length ? rows.map(propRow).join("")
    : `<div class="empty-state"><div class="es-icon">🔍</div><h3>No props match these filters</h3><p>Try widening your search or clearing a filter.</p></div>`;
  wireCardOpeners(el);
}
function activeFilterCount() {
  return filters.families.size + filters.statuses.size + filters.evidences.size
    + (filters.search ? 1 : 0) + (filters.gamePk != null ? 1 : 0);
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
      <div class="filter-sheet-options">${families.map(f => ({ value: familyFilterValue(f.stat), label: f.label })).map(({ value, label }) =>
        `<button class="filter-chip-btn" data-set="families" data-v="${value}" aria-pressed="${filters.families.has(value)}">${esc(label)}</button>`).join("")}</div></div>
    <div class="filter-sheet-group"><div class="label">Status</div>
      <div class="filter-sheet-options">${STATUS_FILTER_OPTIONS.map(([v, l]) =>
        `<button class="filter-chip-btn" data-set="statuses" data-v="${v}" aria-pressed="${filters.statuses.has(v)}">${l}</button>`).join("")}</div></div>
    <div class="filter-sheet-group"><div class="label">Evidence quality</div>
      <div class="filter-sheet-options">${[["A", "Strong"], ["B", "Solid"], ["C", "Developing"], ["D", "Limited"]].map(([v, l]) =>
        `<button class="filter-chip-btn" data-set="evidences" data-v="${v}" aria-pressed="${filters.evidences.has(v)}">${l}</button>`).join("")}</div></div>
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
  // Multi-select fix (Part 2, 2026-08-26): toggles this chip's OWN
  // membership in filters[setKey] -- no longer clears sibling chips, so
  // e.g. Top Pick and Lean can both stay pressed at once.
  $all("[data-set]", sheet).forEach(btn => btn.addEventListener("click", () => {
    const set = filters[btn.dataset.set];
    const pressed = btn.getAttribute("aria-pressed") === "true";
    if (pressed) set.delete(btn.dataset.v); else set.add(btn.dataset.v);
    btn.setAttribute("aria-pressed", String(!pressed));
  }));
  $("#f-sheet-apply", sheet).addEventListener("click", () => { close(); renderProps(); });
}

// ══════════════════════════════════════════════════════════════════════
//  GAMES PAGE
// ══════════════════════════════════════════════════════════════════════
function gameWeatherText(g) {
  const wx = g.weather;
  if (!wx) return "";
  return wx.dome ? "Dome — weather neutral"
    : `${wx.temp ?? "—"}°F${wx.wind_mph ? `, wind ${wx.wind_mph}mph ${wx.wind_effect || ""}` : ""}`;
}
function gameUmpireText(g) {
  return g.umpire ? `HP Ump: ${esc(g.umpire.name)} (${pct(g.umpire.k_pct, 1)} K, ${pct(g.umpire.bb_pct, 1)} BB)` : "";
}
// Games drill-down (Part 2, 2026-08-26): direct request -- "I want people to
// be able to click on a game on the schedule, and get a breakdown." The list
// view existed, but nothing was actually clickable and game_pk never
// survived into the URL, so there was no real per-game page to link to,
// bookmark, or share. selectedGamePk (set by onRouteChange() from
// #/games?game_pk=X) switches this same route between the list and one
// game's detail view.
function renderGames() {
  const el = document.getElementById("page-games");
  const games = DATA.schedule || [];
  if (selectedGamePk != null) {
    const g = games.find(g => g.game_pk === selectedGamePk);
    if (g) { renderGameDetail(el, g); return; }
    // A real game_pk that doesn't match tonight's schedule (a stale link
    // from an earlier day, a typo, a game that's since started and dropped
    // off this pregame research surface) -- honest fallback, not a silent
    // blank page or a crash.
    el.innerHTML = `<div class="empty-state"><div class="es-icon">🗓️</div><h3>That game isn't on tonight's board</h3>
      <p>It may have already started, or this link is from an earlier day.</p>
      <div class="es-cta"><a class="btn btn-primary" href="#/games">See tonight's games</a></div></div>`;
    return;
  }
  if (!games.length) {
    el.innerHTML = `<div class="empty-state"><div class="es-icon">🗓️</div><h3>No games with a research breakdown yet</h3><p>Check back once tonight's games are set.</p></div>`;
    return;
  }
  el.innerHTML = `<div class="section-head"><h2>Games</h2><span class="section-sub">${games.length} games tonight</span></div>
    <div class="game-list">${games.map(gameCard).join("")}</div>`;
}
function gamePickLine(p) {
  return `<div class="game-pick-line">
      <span>${esc(p.name)} — ${esc(p.prop)}</span>
      <span>${pctBig(p.hit_probability)}${p.market_odds != null ? " · " + fmtOdds(p.market_odds) : ""}</span>
    </div>`;
}
// Real bug, found 2026-08-26 (games-drill-down honesty audit): the backend
// used to hand this a flat top-6-by-raw-probability list, which
// systematically favored hits_runs_rbis (clears on ANY hit, run, OR RBI --
// inherently higher raw probability than a single specific outcome like a
// home run) over every other market in the game. pick_sections (see
// dashboard/build_dashboard.py's _game_pick_sections()) replaces that with
// real, honestly-labeled sections (Best Overall Read / Best Batter Read /
// Best Pitcher Read / Best Power Angle / Other Props) -- a section only
// ever appears when a real, distinct candidate backs it, never manufactured
// to fill a slot. `headers` controls whether the section labels themselves
// render (the full drill-down) or just the pick lines, flattened (the
// compact schedule-list card, where there isn't room for section chrome
// but the underlying diversity fix still applies).
function gamePickSections(g, headers) {
  const sections = g.pick_sections || [];
  if (!sections.length) return "";
  if (!headers) {
    return `<div class="game-picks">${sections.flatMap(s => s.picks).map(gamePickLine).join("")}</div>`;
  }
  return sections.map(s => `<div class="game-pick-section">
      <div class="gps-label">${esc(s.label)}</div>
      <div class="game-picks">${s.picks.map(gamePickLine).join("")}</div>
    </div>`).join("");
}
function gameCard(g) {
  const wxText = gameWeatherText(g);
  const ump = gameUmpireText(g);
  const picks = gamePickSections(g, false);
  return `<a class="game-card" href="#/games?game_pk=${g.game_pk}">
    <div class="game-card-head">
      <div class="game-teams">${esc(g.away_team || "")} @ ${esc(g.home_team || "")}</div>
      <div class="game-time">${gameTimeLabel(g.game_start)}</div>
    </div>
    <div class="game-meta-row">
      ${g.away_sp ? `<span>${esc(g.away_sp)} vs ${esc(g.home_sp || "TBD")}</span>` : ""}
      ${wxText ? `<span>${esc(wxText)}</span>` : ""}
      ${ump ? `<span>${esc(ump)}</span>` : ""}
    </div>
    ${picks || `<p class="section-sub">No standout research for this game yet.</p>`}
  </a>`;
}
// Detailed bullpen presentation, direct instruction: "Jacob specifically
// wants names and context" -- real reliever names with a real, dated
// pitch-count/appearance fact each, not vague "bullpen is tired" copy.
// Never claims a reliever is "likely to appear" -- see
// dashboard/build_dashboard.py's _reliever_detail()/_team_bullpen_context()
// docstrings for why that would need a real, verified role model this
// codebase does not have. teamBullpen is null (not an empty object) when
// this team's bullpen genuinely wasn't fetchable tonight -- rendered as an
// honest omission, never a fabricated "no data" block for every game.
function bullpenTeamBlock(teamName, teamBullpen) {
  if (!teamBullpen) return "";
  const relLines = (teamBullpen.relievers || []).map(r => {
    const parts = [];
    if (r.pitches_last_outing != null) {
      const when = r.days_since_last_outing === 0 ? "today"
        : r.days_since_last_outing === 1 ? "yesterday"
        : r.days_since_last_outing != null ? `${r.days_since_last_outing}d ago` : null;
      parts.push(`${r.pitches_last_outing} pitches${when ? " " + when : ""}`);
    }
    if (r.appearances_l7 != null) {
      parts.push(`${r.appearances_l7} appearance${r.appearances_l7 === 1 ? "" : "s"} in L7`);
    }
    return `<div class="bullpen-reliever"><span class="br-name">${esc(r.name)}</span>` +
      (parts.length ? `<span class="br-detail">${esc(parts.join(" · "))}</span>` : "") + `</div>`;
  }).join("");
  return `<div class="bullpen-team">
    <div class="bullpen-team-head"><b>${esc(teamName)}</b><span>${esc(teamBullpen.fatigue_summary)}</span></div>
    ${relLines || `<p class="section-sub">No individual reliever usage found tonight.</p>`}
  </div>`;
}
function bullpenBlock(g) {
  const away = bullpenTeamBlock(g.away_team, g.away_team_bullpen);
  const home = bullpenTeamBlock(g.home_team, g.home_team_bullpen);
  if (!away && !home) return "";
  return `<div class="detail-section"><h3>Bullpen</h3>${away}${home}</div>`;
}
// The drill-down itself: everything gameCard() already shows, at full size
// (no truncation), plus a real "See all N props" link into All Props
// scoped to this exact game_pk -- the "in-game prop filtering" this page
// exists to lead into, reusing the same multi-select-capable applyFilters()
// engine rather than building a second, parallel prop list here.
function renderGameDetail(el, g) {
  const wxText = gameWeatherText(g);
  const ump = gameUmpireText(g);
  const picks = gamePickSections(g, true);
  // Real, honest total -- not the up-to-6 highlight count pick_sections
  // itself caps at (see _game_pick_sections()'s own docstring for why it
  // caps there: a curated highlight list, not the full research surface).
  const totalPropsForGame = publicProps().filter(p => p.game_pk === g.game_pk).length;
  el.innerHTML = `
    <a class="link-btn" href="#/games" style="display:inline-block;margin-bottom:14px;">← All games</a>
    <div class="section-head"><h2>${esc(g.away_team || "")} @ ${esc(g.home_team || "")}</h2>
      <span class="section-sub">${gameTimeLabel(g.game_start)}</span></div>
    <div class="game-meta-row" style="margin-bottom:16px;">
      ${g.away_sp ? `<span>${esc(g.away_sp)} vs ${esc(g.home_sp || "TBD")}</span>` : ""}
      ${wxText ? `<span>${esc(wxText)}</span>` : ""}
      ${ump ? `<span>${esc(ump)}</span>` : ""}
      ${g.is_getaway ? `<span>Getaway day</span>` : ""}
      ${g.is_opener ? `<span>Bullpen/opener day</span>` : ""}
    </div>
    ${picks || `<p class="section-sub">No standout research for this game yet.</p>`}
    ${bullpenBlock(g)}
    ${totalPropsForGame > 0 ? `<div style="margin-top:16px;"><a class="btn btn-primary" href="#/props?game_pk=${g.game_pk}">See all ${totalPropsForGame} props for this game →</a></div>` : ""}
  `;
}

// ══════════════════════════════════════════════════════════════════════
//  PERFORMANCE PAGE — item 9/10: current vs legacy, never blended,
//  translated into plain language.
// ══════════════════════════════════════════════════════════════════════
// Real bug, found 2026-08-25: the Performance page rendered a bare hit-rate
// percentage with no caveat at all, so an early "100%" off 2 graded picks
// looked exactly as trustworthy as a mature 500-pick record -- the opposite
// of what this page's own load_track_record() docstring already promises
// ("do not pretend the new architecture has proven itself before it has
// enough observations"). dashboard/build_dashboard.py now computes
// sample_label via eval_lib's own shared sample-size-honesty gate (the
// SAME thresholds/wording already used for calibration/Brier reporting
// elsewhere in this project, not a new invented one); this just surfaces it.
const SAMPLE_LABEL_CAVEATS = {
  insufficient: n => `Only ${n} graded pick${n === 1 ? "" : "s"} so far — far too few to mean anything. Read this as "no real signal yet," not as evidence either way.`,
  thin: n => `Only ${n} graded picks — still a thin sample. A single new outcome can swing this rate noticeably; treat it as a rough early read, not a settled record.`,
  directional: n => `${n} graded picks is enough to be directional, but not yet a large, confident sample.`,
  reportable: null,
};
function sampleLabelCaveat(tier, n) {
  const fn = SAMPLE_LABEL_CAVEATS[tier];
  return fn ? fn(n) : null;
}

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
    const curCaveat = sampleLabelCaveat(cur.sample_label, cur.n);
    html += `<div class="perf-metric-grid">
      <div class="perf-metric"><div class="pm-n">${pct(cur.hit_rate, 1)}</div><div class="pm-l">Top Pick hit rate</div></div>
      <div class="perf-metric"><div class="pm-n">${cur.hits}-${cur.misses}</div><div class="pm-l">Record</div></div>
      <div class="perf-metric"><div class="pm-n">${cur.n}</div><div class="pm-l">Graded picks</div></div>
      ${cur.last_14d_hit_rate != null ? `<div class="perf-metric"><div class="pm-n">${pct(cur.last_14d_hit_rate, 1)}</div><div class="pm-l">Last 14 days (n=${cur.last_14d_n})</div></div>` : ""}
    </div>`;
    if (curCaveat) html += `<p class="perf-sample-caveat">${esc(curCaveat)}</p>`;
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
    const legCaveat = sampleLabelCaveat(leg.sample_label, leg.n);
    html += `<div class="perf-metric-grid">
      <div class="perf-metric"><div class="pm-n">${pct(leg.hit_rate, 1)}</div><div class="pm-l">Main-board hit rate</div></div>
      <div class="perf-metric"><div class="pm-n">${leg.hits}-${leg.misses}</div><div class="pm-l">Record</div></div>
      <div class="perf-metric"><div class="pm-n">${leg.n}</div><div class="pm-l">Graded picks</div></div>
      ${leg.last_14d_hit_rate != null ? `<div class="perf-metric"><div class="pm-n">${pct(leg.last_14d_hit_rate, 1)}</div><div class="pm-l">Last 14 days</div></div>` : ""}
    </div>`;
    if (legCaveat) html += `<p class="perf-sample-caveat">${esc(legCaveat)}</p>`;
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
//  MY BOARD PAGE (route/DOM ids stay "watchlist" -- see the module header
//  above for why). Mental model: "these are the props I'm considering
//  tonight," with a real return-loop -- save, come back, see what
//  changed, re-evaluate.
// ══════════════════════════════════════════════════════════════════════
const MY_BOARD_SORT_KEY = "fc_my_board_sort_v1";
function myBoardSort() { return safeGet(MY_BOARD_SORT_KEY) || "game_time"; }
function setMyBoardSort(v) { safeSet(MY_BOARD_SORT_KEY, v); }
const MY_BOARD_SORTERS = {
  game_time: (a, b) => new Date(a.p.game_start || 0) - new Date(b.p.game_start || 0),
  probability: (a, b) => (b.p.hit_probability || 0) - (a.p.hit_probability || 0),
  recently_changed: (a, b) => b.changes.length - a.changes.length,
};
function renderWatchlist() {
  const el = document.getElementById("page-watchlist");
  const items = [...watchlist].map(id => PROPS_BY_ID.get(id)).filter(Boolean);
  if (!items.length) {
    // Real bug, found 2026-08-25: a saved id's canonical prop id bakes in
    // game_pk (see canonical_prop_id() / prop_identity_key() in
    // dashboard/live_state.py), so a prop saved on an earlier day can NEVER
    // resolve against today's PROPS_BY_ID again -- that game_pk simply
    // won't recur. Before this fix, that made the "My Board" nav badge
    // (updateWatchCount(), which reads the raw watchlist.size -- every id
    // ever saved) silently disagree with this page: the badge could say
    // "3" while the page claimed "My Board is empty," with nothing telling
    // the viewer why. Auto-pruning the stale ids was considered and
    // rejected: a prop can ALSO temporarily drop out of today's OWN board
    // mid-day (a late scratch, a lineup-window gap -- generate_picks.py's
    // own docs: "a player who doesn't end up in a real lineup simply isn't
    // generated as a candidate on the next rebuild"), and silently deleting
    // a user's save the moment that happens, with no way back, is worse
    // than an honest explanation. So: never delete anything, just tell the
    // truth about why the page looks empty when the badge doesn't.
    if (watchlist.size > 0) {
      el.innerHTML = `<div class="empty-state"><div class="es-icon">☆</div><h3>None of your ${watchlist.size} saved prop${watchlist.size === 1 ? "" : "s"} ${watchlist.size === 1 ? "is" : "are"} on tonight's board</h3>
        <p>They're either from an earlier day (a saved prop is tied to that exact game and can't carry over) or no longer a candidate tonight. Nothing was deleted -- save fresh picks from tonight's board below.</p>
        <div class="es-cta"><a class="btn btn-primary" href="#/props">Browse All Props</a></div></div>`;
      return;
    }
    el.innerHTML = `<div class="empty-state"><div class="es-icon">☆</div><h3>My Board is empty</h3>
      <p>Save any player or prop you're considering tonight -- come back later and see exactly what changed since you saved it.</p>
      <div class="es-cta"><a class="btn btn-primary" href="#/props">Browse All Props</a></div></div>`;
    return;
  }
  const sortKey = myBoardSort();
  const rows = items.map(p => ({ p, changes: sinceYouSavedChanges(p) }))
    .sort(MY_BOARD_SORTERS[sortKey] || MY_BOARD_SORTERS.game_time);

  el.innerHTML = `<div class="section-head"><h2>My Board</h2><span class="section-sub">${items.length} saved</span></div>
    <div class="filter-bar">
      <select class="filter-select" id="mb-sort" aria-label="Sort My Board">
        <option value="game_time">Sort: Game time</option>
        <option value="probability">Sort: Probability</option>
        <option value="recently_changed">Sort: Recently changed</option>
      </select>
      <button class="filter-chip-btn" id="mb-clear-all">Clear all</button>
    </div>
    <div class="prop-list my-board-list">${rows.map(({ p, changes }) => myBoardItem(p, changes)).join("")}</div>`;
  $("#mb-sort", el).value = sortKey;
  $("#mb-sort", el).addEventListener("change", e => { setMyBoardSort(e.target.value); renderWatchlist(); });
  $("#mb-clear-all", el).addEventListener("click", () => {
    if (!confirm(`Remove all ${items.length} saved props from My Board?`)) return;
    watchlist.clear();
    watchSnapshot = {};
    saveWatchlist();
    updateWatchCount();
    renderWatchlist();
  });
  wireCardOpeners(el);
}
// One saved prop's card: the compact row (unchanged, same component every
// other list uses) plus, only when something real changed, a genuine
// "Since You Saved This" breakdown -- every changed field shown, never
// hidden for a deterioration, and no field invented for one a v1 snapshot
// never captured.
function myBoardItem(p, changes) {
  const summary = changeSummary(changes);
  // A real v2 snapshot with a saved_at timestamp means we actually HAVE a
  // trustworthy baseline to compare against -- only then is "nothing has
  // changed" an honest claim. An old v1 save (no saved_at, no historical
  // probability) has nothing real to compare, so this stays silent rather
  // than assert "no changes" over data we never actually captured.
  const snap = normalizeSnapshot(watchSnapshot[p.id]);
  const hasRealBaseline = !!(snap && snap.schema_version === WATCH_SNAPSHOT_SCHEMA_VERSION && snap.saved_at);
  let sinceRows;
  if (changes.length) {
    sinceRows = `<div class="since-saved">
      <div class="since-saved-label">Since you saved this</div>
      ${changes.map(c => `<div class="since-row"><span class="since-field">${esc(c.label)}</span>
        <span class="since-delta"><span class="since-from">${esc(String(c.from))}</span> → <span class="since-to">${esc(String(c.to))}</span>${
          "stronger" in c ? ` <span class="since-arrow ${c.stronger ? "up" : "down"}">${c.stronger ? "↑" : "↓"}</span>` : ""
        }</span></div>`).join("")}
    </div>`;
  } else if (hasRealBaseline) {
    sinceRows = `<div class="since-saved since-saved-none">
      <div class="since-saved-label">Since you saved this</div>
      <div class="since-none">Nothing has changed since you saved this.</div>
    </div>`;
  } else {
    sinceRows = "";
  }
  return `<div class="watchlist-item">${summary ? `<div class="watch-change-badge">${esc(summary)}</div>` : ""}${propRow(p)}${sinceRows}</div>`;
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
  // Real bug, found 2026-08-26 (Games drill-down build): this discarded
  // the real game_pk sitting right in data-game and always sent the viewer
  // to the generic, unscoped Games list -- a schedule chip or search result
  // for one specific game silently lost which game it was for.
  $all("[data-game]", root).forEach(btn => {
    btn.addEventListener("click", () => { go("games", "game_pk=" + btn.dataset.game); });
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
  // Entrance transition (Part 18): [data-open] added one frame after
  // unhiding so the CSS transition actually has a starting state to
  // animate from, instead of jumping straight to its resting position.
  requestAnimationFrame(() => sheet.setAttribute("data-open", "true"));
  const star = $("#detail-star");
  if (star) star.addEventListener("click", () => { toggleWatch(id); star.setAttribute("aria-pressed", String(watchlist.has(id))); star.querySelector(".star-label").textContent = watchlist.has(id) ? "Saved to My Board" : "Save to My Board"; });
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
  sheet.removeAttribute("data-open");
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

// PRICE FRESHNESS STATE (detail-sheet PASS, 2026-08-25) -- market_fetch_state
// et al. are real fields dashboard/refresh_prices.py has written into the
// live overlay all along (MATCHED/NOT_POSTED/FETCH_FAILED/IN_PLAY -- see
// that file's own real state assignments) and app.js's LIVE_PRICE_FIELDS
// has merged them into every prop for just as long -- but nothing ever
// actually displayed them. This maps those REAL states to a plain label;
// it does not invent a state refresh_prices.py doesn't actually produce.
// A stale/failed price must never look equally current as a verified one.
function priceFreshnessState(p) {
  if (p.market_odds == null) {
    return { label: "Not posted", tone: "unposted", detail: "FanDuel hasn't posted a price for this line yet." };
  }
  const state = p.market_fetch_state;
  if (state === "FETCH_FAILED") {
    return { label: "Last known · price fetch failed", tone: "stale",
      detail: "The last successful FanDuel fetch is shown below; the most recent check didn't succeed." };
  }
  if (state === "IN_PLAY") {
    // 2026-08-25 release-readiness audit: traced this state to
    // dashboard/refresh_prices.py's real behavior -- once a game passes
    // the pregame wagering cutoff, this pipeline FREEZES the price and
    // never fetches a fresh one again (the prop is explicitly excluded
    // from the re-fetch loop; only game-state fields keep advancing).
    // The old wording ("In play... the price can move quickly") claimed
    // real-time in-game pricing capability this pipeline does not have --
    // it implied the NUMBER itself was live, when only the GAME is live
    // and the number is a preserved pregame snapshot. Full Count has no
    // live in-game repricing yet (that's the future Live differentiator,
    // not built now) -- say so honestly instead of implying it exists.
    return { label: "Game live · price locked pregame", tone: "live",
      detail: "This price was locked in before first pitch and won't update while the game is live -- check FanDuel directly for a current in-play line." };
  }
  if (p.stale) {
    return { label: "Last known", tone: "stale", detail: "This price is from an earlier check, not the most recent one." };
  }
  const checkedAgo = _agoText(p.market_fetch_checked_at || DATA.prices_updated_at || DATA.generated_at);
  return { label: checkedAgo ? `Current · checked ${checkedAgo}` : "Current",
    tone: "current", detail: null };
}

// WHY NOT A TOP PICK -- 2026-08-25. status_reasons is real, uncapped, and
// already plain English straight from recommendation.classify_recommendation()
// -- shown verbatim here, never re-derived or guessed at. Only surfaced for a
// candidate that already looks interesting (an actual Lean/Value read, or a
// Neutral with a real priced probability at or above the board's own 60%
// eligibility floor -- MIN_LINE_PROB in generate_picks.py, not a number
// invented here) so it answers "why isn't THIS a Top Pick" and doesn't
// clutter every long-shot Neutral on the board.
const WHY_NOT_TOP_PICK_MIN_PROB = 0.60;
function whyNotTopPickReason(p) {
  if (!p.recommendation_status || p.recommendation_status === "top_pick") return null;
  const interesting = p.recommendation_status === "lean" || p.recommendation_status === "value"
    || (p.hit_probability != null && p.hit_probability >= WHY_NOT_TOP_PICK_MIN_PROB);
  if (!interesting) return null;
  return (p.status_reasons || [])[0] || null;
}

// Model vs. Market visual bars (Part 6 of the UX revamp, 2026-08-26).
// Replaces a plain "Full Count 63% / Market fair 55% / Edge +7.7 pts" text
// row list with two horizontal bars plus one plain-English line, so a real
// disagreement reads as a shape (bar length + gap), not just three numbers
// a viewer has to subtract themselves. Every number is unchanged from the
// old row list -- p.hit_probability, p.market_fair ?? p.market_implied,
// p.edge_vs_fair ?? p.market_edge, still the exact same honest-devig-aware
// fields the compact card's marketBlock() already prefers. The
// assumed_hold/exact_two_sided distinction is preserved verbatim (never
// hidden), just translated into plainer wording per spec, with the raw
// field names still available one line down for anyone who wants them.
function modelVsMarketBlock(p, freshness) {
  const fcProb = p.hit_probability;
  const marketProb = p.market_fair ?? p.market_implied;
  const edge = p.edge_vs_fair ?? p.market_edge;
  const edgePts = edge != null ? Math.round(edge * 100) : null;
  const edgeClass = edgePts == null ? "" : (edgePts >= 0 ? "pos" : "neg");
  const edgeText = edgePts == null ? "—" : (edgePts >= 0 ? "+" : "") + edgePts + " pts";
  const bars = (fcProb != null && marketProb != null) ? `
      <div class="mvm-bar-row">
        <span class="mvm-bar-label">Full Count</span>
        <div class="mvm-bar-track"><div class="mvm-bar-fill fc" style="width:${Math.round(fcProb * 100)}%"></div></div>
        <span class="mvm-bar-pct">${pct(fcProb, 0)}</span>
      </div>
      <div class="mvm-bar-row">
        <span class="mvm-bar-label">Market fair</span>
        <div class="mvm-bar-track"><div class="mvm-bar-fill mkt" style="width:${Math.round(marketProb * 100)}%"></div></div>
        <span class="mvm-bar-pct">${pct(marketProb, 0)}</span>
      </div>
      <div class="mvm-gap-line">Gap <b class="${edgeClass}">${edgeText}</b>${Math.abs(edgePts || 0) >= 7 ? `<span class="mvm-gap-flag">⚠ Large disagreement</span>` : ""}</div>`
    : `<p class="section-sub">Market fair value not available for this line yet.</p>`;
  // Plain-English translation of the gap, direction-only (never a magnitude
  // claim beyond what the bars themselves already show) -- honest about
  // uncertainty rather than framing a gap as automatic value, per the
  // house rule against casino-style "the market is wrong, bet now" framing.
  let explain = "";
  if (edgePts != null && Math.abs(edgePts) >= 7) {
    explain = edgePts > 0
      ? "The sportsbook is materially less bullish than Full Count. Treat this as extra uncertainty, not automatic value."
      : "The sportsbook is materially more bullish than Full Count here — Full Count's own read is the more cautious one.";
  } else if (edgePts != null) {
    explain = "Full Count and the market are broadly in agreement on this one.";
  }
  return `
    <div class="detail-section">
      <h3>Full Count vs. Market</h3>
      <div class="model-vs-market">
        ${bars}
        ${explain ? `<p class="mvm-explain">${esc(explain)}</p>` : ""}
      </div>
      <p class="section-sub">FanDuel ${fmtOdds(p.market_odds) ?? "— not posted"}${p.posted_implied != null ? ` (${pct(p.posted_implied, 0)} raw)` : ""}${
        p.market_fair_method === "exact_two_sided" ? " · exact no-vig (both sides priced)"
        : p.market_fair_method === "assumed_hold" ? " · estimated no-vig (only one side posted)"
        : (p.market_hold != null ? " · exact no-vig" : "")
      }</p>
      <p class="price-freshness-note tone-${freshness.tone}">${esc(freshness.label)}${freshness.detail ? " — " + esc(freshness.detail) : ""}</p>
    </div>`;
}

function detailBody(p) {
  const eq = evidenceQuality(p);
  const game = gameContextFor(p);
  const freshness = priceFreshnessState(p);

  // WHY IT COULD HIT: why[] verbatim -- real, generated by the same code
  // that computes score, sign already correct (see
  // frontend/detail_sheet_data_audit_2026-08-25.md).
  const hitItems = (p.why || []).map(w => capSentence(humanizeReason(w)));

  // WHY IT COULD MISS: watchouts[] verbatim, plus exactly two SAFE
  // structured additions that need no fitted-weight sign to interpret --
  // thin sample (existing) and an unconfirmed lineup slot (new). Never
  // fabricated to force symmetry with the hit case -- an honest "no major
  // concern" message ships when this list is genuinely empty.
  const missItems = (p.watchouts || []).map(w => capSentence(humanizeReason(w)));
  if (p.sample_n != null && p.sample_n > 0 && p.sample_n < 30) {
    missItems.push(`This read leans on a smaller sample (${p.sample_n} games) — treat it with a little extra caution.`);
  }
  if (p.lineup_assumed === true) {
    missItems.push("The batting order slot is still a projection, not a confirmed lineup — it could change before first pitch.");
  }
  if (p.stale) {
    missItems.push((p.status_reasons || [])[0] || "Underlying data is stale.");
  }

  const whyNotTopPick = whyNotTopPickReason(p);
  // Real gap, found 2026-08-26 (deep-detail-views audit): recommendation.py's
  // classify_recommendation() already writes a real, honest "why this
  // qualified" sentence into status_reasons[0] for every genuine Top Pick
  // ("clears the real probability floor... a confirmed lineup, live
  // pricing, and the price/value test..." -- see that function's own
  // _result("top_pick", reasons) call) -- but whyNotTopPickReason() above
  // is deliberately null for every top_pick (it only ever answers "why
  // NOT"), so this real, already-computed sentence was never rendered
  // anywhere. The SUSPECT-specific second reason (status_reasons[1], when
  // isTopPickSuspect(p) is true) is intentionally excluded here too -- no
  // longer surfaced as customer-facing warning prose anywhere (2026-08-26
  // product decision, see isTopPickSuspect()'s own comment) -- only the
  // first, real "why this qualified" sentence renders.
  const whyTopPickQualified = p.recommendation_status === "top_pick"
    ? (p.status_reasons || [])[0] || null : null;

  // OPPORTUNITY: a plain fact (batting order), not a graded judgment --
  // see the audit doc for why the underlying cat_context component is NOT
  // safely gradable Supportive/Concern without the fitted score weights.
  // Batter markets only; never fabricated for a pitcher row.
  const opportunityRows = [];
  if (p.batting_order != null) {
    opportunityRows.push(["Batting order", `${esc(String(p.batting_order))}${_ordinalSuffix(p.batting_order)} in the order`]);
  }

  // MATCHUP: real game-level facts already on the schedule entry for this
  // prop's game_pk -- starters, park/weather, home-plate umpire.
  const matchupRows = [];
  if (game) {
    if (game.away_sp || game.home_sp) {
      matchupRows.push(["Starters", `${esc(game.away_sp || "TBD")} @ ${esc(game.home_sp || "TBD")}`]);
    }
    const wx = weatherText(game.weather);
    if (wx) matchupRows.push(["Park / weather", esc(wx)]);
    if (game.umpire) {
      matchupRows.push(["HP umpire", `${esc(game.umpire.name)} — ${pct(game.umpire.k_pct, 1)} K, ${pct(game.umpire.bb_pct, 1)} BB rate`]);
    }
  }

  // OPPOSING BULLPEN: real per-reliever detail, direct instruction: "Jacob
  // specifically wants names and context." Reuses the exact same game-level
  // bullpen data _team_bullpen_context() already attaches per game (see
  // dashboard/build_dashboard.py) -- gameContextFor(p) already resolves
  // p.game_pk to that same schedule entry, so no new data plumbing is
  // needed here, only pointing at the OPPOSING team's block: whichever
  // side p.team is NOT. Batter markets only -- a pitcher isn't facing a
  // bullpen himself, so this would be a non-sequitur on his own prop.
  let oppBullpenName = null, oppBullpen = null;
  if (game && p.type === "batter" && p.team) {
    if (p.team === game.away_team) { oppBullpenName = game.home_team; oppBullpen = game.home_team_bullpen; }
    else if (p.team === game.home_team) { oppBullpenName = game.away_team; oppBullpen = game.away_team_bullpen; }
  }

  // Visual signal rows (Part 7 of the UX revamp, 2026-08-26): a round
  // direction badge (↑ favorable / ↓ concern) replaces the old bare ＋/－
  // glyph, and app.css now gives each row its own card + colored left
  // rail instead of a flush-left text list. The sentence itself is still
  // exactly why[]/watchouts[] verbatim -- no new data, no re-derived
  // direction (the badge's meaning still comes from which list a given
  // item came from, same as the old glyph did).
  const renderReasons = (items, cls, icon, srLabel) => items.length
    ? `<div class="reason-list">${items.map(t => `<div class="reason-item ${cls}"><span class="r-icon" aria-hidden="true">${icon}</span><span><span class="sr-only">${srLabel}: </span>${esc(t)}</span></div>`).join("")}</div>` : "";
  const renderRows = rows => rows.length
    ? `<div class="underlying-data">${rows.map(([k, v]) => `<div class="ud-item"><div class="k">${esc(k)}</div><div class="v" style="font-family:var(--font-body);font-weight:500;">${v}</div></div>`).join("")}</div>` : "";

  // Decision Snapshot (Part 5/8 of the UX revamp, 2026-08-26): the first
  // viewport of the sheet should answer "what does Full Count like, how
  // much, what's the price, what does the market think, and how solid is
  // the evidence" without any scrolling or reading a sentence -- so the
  // hero now also carries market fair value + the gap (same numbers
  // modelVsMarketBlock() renders again, larger, further down for anyone
  // who wants the bar-chart form) and evidence quality, alongside the
  // status/price it already had. Nothing here is a new computation --
  // every value already existed in this function or evidenceQuality(p).
  const heroMarketProb = p.market_fair ?? p.market_implied;
  const heroEdge = p.edge_vs_fair ?? p.market_edge;
  const heroEdgeText = heroEdge == null ? null : (heroEdge >= 0 ? "+" : "") + Math.round(heroEdge * 100) + " pts";
  const heroEdgeClass = heroEdge == null ? "" : (heroEdge >= 0 ? "pos" : "neg");
  return `
    <div class="detail-head">
      <h2 id="detail-title">${esc(p.name)}</h2>
      <div class="d-sub">${esc(p.prop)} · ${esc(p.matchup || p.team || "")}${p.game_start ? ` · ${esc(gameTimeLabel(p.game_start))}` : ""}</div>
    </div>
    <div class="detail-hero">
      <div>
        <div class="prob-big">${pctBig(p.hit_probability)}</div>
        <div class="hero-metric-label">Full Count's read</div>
      </div>
      <div class="hero-meta">
        <div><b>${esc(statusLabel(p))}</b></div>
        <div>FanDuel: ${fmtOdds(p.market_odds) ?? "not posted"}</div>
        <div>Market fair: ${heroMarketProb != null ? pct(heroMarketProb, 0) : "—"}${heroEdgeText ? ` <span class="pc-edge ${heroEdgeClass}">(${heroEdgeText})</span>` : ""}</div>
        ${eq ? `<div>Evidence: ${esc(eq.label)}</div>` : ""}
      </div>
    </div>
    <div class="pc-chips" style="margin-bottom:18px;">${[statusChip(p), lineupChip(p), evidenceChip(p), staleChip(p), liveStaleChip(p), gradeChip(p)].filter(Boolean).join("")}</div>

    ${renderReasons(hitItems, "positive", "↑", "Favorable") ? `<div class="detail-section"><h3>Why It Could Hit</h3>
      <p class="section-sub">What shapes Full Count's read of this matchup — see Evidence below for what actually produced the probability number above.</p>
      ${renderReasons(hitItems, "positive", "↑", "Favorable")}</div>` : ""}
    <div class="detail-section"><h3>Why It Could Miss</h3>${
      missItems.length ? renderReasons(missItems, "negative", "↓", "Concern")
        : `<p class="section-sub">No major model-side concern beyond normal baseball variance.</p>`
    }</div>
    ${whyNotTopPick ? `<div class="detail-section why-not-top-pick"><h3>Why Not a Top Pick?</h3>
      <p class="section-sub">${esc(capSentence(whyNotTopPick))}</p></div>` : ""}
    ${whyTopPickQualified ? `<div class="detail-section"><h3>Why This Qualified</h3>
      <p class="section-sub">${esc(capSentence(whyTopPickQualified))}</p></div>` : ""}
    ${opportunityRows.length ? `<div class="detail-section"><h3>Opportunity</h3>${renderRows(opportunityRows)}</div>` : ""}
    ${matchupRows.length ? `<div class="detail-section"><h3>Matchup</h3>${renderRows(matchupRows)}</div>` : ""}
    ${oppBullpen ? `<div class="detail-section"><h3>Bullpen</h3>${bullpenTeamBlock(oppBullpenName, oppBullpen)}</div>` : ""}

    ${modelVsMarketBlock(p, freshness)}

    <div class="detail-section">
      <h3>Evidence</h3>
      <p class="section-sub">What actually produced the probability number above.</p>
      ${renderRows([
        ["What produced this number", probabilityBasisText(p) || "—"],
        ["Evidence quality", eq ? eq.label : "—"],
        ["Sample size", p.sample_n ?? "—"],
        ["95% interval", p.prob_ci ? pct(p.prob_ci[0], 0) + "–" + pct(p.prob_ci[1], 0) + (probCiSourceText(p) ? ` (${probCiSourceText(p)})` : "") : "Not defensible for this line"],
      ])}
    </div>

    <div class="detail-section">
      <button class="underlying-toggle" id="detail-underlying-toggle" aria-expanded="false">
        <span class="u-caret">▸</span> Deeper data
      </button>
      <div class="underlying-data" id="detail-underlying" hidden>
        <div class="ud-item"><div class="k">Base rate</div><div class="v">${pct(p.base_rate, 1)}</div></div>
        <div class="ud-item"><div class="k">Lift vs. base rate</div><div class="v">${p.lift != null ? (p.lift >= 0 ? "+" : "") + Math.round(p.lift * 100) + " pts" : "—"}</div></div>
        <div class="ud-item"><div class="k">Quality score</div><div class="v">${p.score ?? "—"}/100</div></div>
      </div>
    </div>

    <button class="btn watchlist-toggle-btn" id="detail-star" aria-pressed="${watchlist.has(p.id)}">
      <span class="star-label">${watchlist.has(p.id) ? "Saved to My Board" : "Save to My Board"}</span>
    </button>
  `;
}
function _ordinalSuffix(n) {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return "th";
  switch (n % 10) { case 1: return "st"; case 2: return "nd"; case 3: return "rd"; default: return "th"; }
}

// ══════════════════════════════════════════════════════════════════════
//  GLOBAL SEARCH
// ══════════════════════════════════════════════════════════════════════
// REDESIGNED 2026-08-25: structured baseball navigation (Teams/Games/
// Players/Props), not naive substring matching over one flat list. A
// small, deterministic scoring function -- no fuzzy-search dependency,
// per explicit direction "do not install a huge fuzzy search dependency."

// Standard MLB abbreviations -> a real, DISTINCTIVE nickname substring of
// that team's own name (never the exact full team-name string -- names
// can legitimately vary/change; matching on the stable nickname portion
// is robust to that, and still correctly finds "New York Yankees" for a
// "nyy" query without hardcoding a name that could go stale).
const TEAM_ABBR_ALIASES = {
  ari: "diamondbacks", atl: "braves", bal: "orioles", bos: "red sox",
  chc: "cubs", cws: "white sox", chw: "white sox", cin: "reds",
  cle: "guardians", col: "rockies", det: "tigers", hou: "astros",
  kc: "royals", laa: "angels", lad: "dodgers", mia: "marlins",
  mil: "brewers", min: "twins", nym: "mets", nyy: "yankees",
  oak: "athletics", ath: "athletics", phi: "phillies", pit: "pirates",
  sd: "padres", sf: "giants", sea: "mariners", stl: "cardinals",
  tb: "rays", tex: "rangers", tor: "blue jays", wsh: "nationals", was: "nationals",
};
// Market-intent aliases -- "customer navigation, not model logic": maps
// what a baseball fan actually types to the real family key
// familyFilterValue() already produces, never a new market invented here.
const MARKET_ALIASES = {
  hits: ["hits", "hit"],
  total_bases: ["total bases", "bases", "2+ bases", "tb"],
  home_runs: ["home run", "home runs", "hr", "homers", "homer"],
  hits_runs_rbis: ["h+r+rbi", "hits runs rbis", "hits+runs+rbis"],
  strikeouts: ["strikeouts", "strikeout", "ks", "k's"],
  pitcher_outs: ["outs", "pitcher outs", "outs recorded"],
  stolen_base: ["stolen base", "stolen bases", "steal", "steals", "sb"],
};

// Deterministic relevance score, most-specific first: exact match, then
// prefix, then any-word-prefix (so "harper" ranks "Bryce Harper" highly),
// then broad substring. Returns 0 (never shown) when nothing matches.
function _matchScore(text, q) {
  const t = (text || "").toLowerCase();
  if (!q || !t) return 0;
  if (t === q) return 100;
  if (t.startsWith(q)) return 80;
  if (t.split(/\s+/).some(w => w.startsWith(q))) return 60;
  if (t.includes(q)) return 40;
  return 0;
}
// A game's full searchable text, expanded with any abbreviation whose
// nickname is a real substring of either real team name -- so "NYY BOS"
// and "Yankees Red Sox" both resolve the same game.
function _gameSearchText(g) {
  const parts = [g.away_team || "", g.home_team || ""];
  for (const [abbr, nickname] of Object.entries(TEAM_ABBR_ALIASES)) {
    if ((g.away_team || "").toLowerCase().includes(nickname) || (g.home_team || "").toLowerCase().includes(nickname)) {
      parts.push(abbr);
    }
  }
  return parts.join(" ").toLowerCase();
}
// Real bug found 2026-08-2X (Part 2 UX audit): q.includes(a) was a blind
// substring check with NO word-boundary awareness -- a short alias like
// "hr" (home_runs) or "hit"/"ks"/"tb"/"sb" is itself just a fragment
// embedded inside many real MLB surnames ("Christian" contains "hr" --
// C-HR-istian; "Whitlock" contains "hit" -- w-HIT-lock; "Jenkins"/
// "Perkins"/"Hawkins" all contain "ks"). Searching a player whose name
// happened to contain one of these fragments silently became a MARKET
// filter instead of a name search -- "Christian Yelich" would show every
// home-run candidate on the slate ranked by probability, never Yelich
// himself. Fixed with a real word-boundary match: an alias only counts as
// present when it appears as its own bounded token in the query (a real
// short market query like "hr" or "hr tonight" still matches -- there IS
// a boundary around it there -- but the fragment buried mid-word in a
// surname no longer does).
function _escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function _marketFamilyForQuery(q) {
  for (const [family, aliases] of Object.entries(MARKET_ALIASES)) {
    if (aliases.some(a => new RegExp("\\b" + _escapeRegex(a) + "\\b").test(q))) return family;
  }
  return null;
}

// Pure ranking function, deliberately separate from DOM rendering so it's
// directly testable. Returns { teams, games, players, props, marketFamily }.
function runSearch(query, props, schedule) {
  const q = (query || "").trim().toLowerCase();
  if (q.length < 2) return { teams: [], games: [], players: [], props: [], marketFamily: null };

  const visible = props || [];
  const marketFamily = _marketFamilyForQuery(q);

  // TEAMS: unique real team names from the schedule, scored by name match
  // OR an abbreviation alias whose nickname the team name contains.
  const teamNames = new Set();
  for (const g of (schedule || [])) { if (g.away_team) teamNames.add(g.away_team); if (g.home_team) teamNames.add(g.home_team); }
  const aliasNickname = TEAM_ABBR_ALIASES[q] || null;
  const teams = [...teamNames]
    .map(name => ({ name, score: Math.max(_matchScore(name, q), aliasNickname && name.toLowerCase().includes(aliasNickname) ? 90 : 0) }))
    .filter(t => t.score > 0).sort((a, b) => b.score - a.score).slice(0, 3);

  // GAMES: every word in the query must appear somewhere in the game's
  // (alias-expanded) searchable text -- handles both "Yankees Red Sox"
  // and "NYY BOS" without full NLP.
  const qWords = q.split(/\s+/).filter(Boolean);
  const games = (schedule || [])
    .map(g => ({ g, text: _gameSearchText(g) }))
    .filter(({ text }) => qWords.every(w => text.includes(w)))
    .slice(0, 3).map(({ g }) => g);

  // PLAYERS: unique players (by id) ranked by their best name match; a
  // pure market-intent query (e.g. "home runs") correctly surfaces no
  // players, since a market name won't match any real player's name.
  // Real bug found 2026-08-25: a game-level combo market (e.g. NRFI's
  // "Philadelphia Phillies @ Seattle Mariners -- 1st Inning (Both Teams)")
  // has no individual player behind it -- team is null and player_id is a
  // synthetic "nrfi_<game_pk>" string, never a real MLB player id -- but
  // its `name` field is a full game description, so a team-name query like
  // "phillies" matched it by substring and it rendered under Players. Every
  // REAL individual-player prop always carries a real team; require that.
  const byPlayer = new Map();
  for (const p of visible) {
    if (!p.team) continue;
    const key = p.player_id ?? p.combo_player_ids ?? p.name;
    const score = _matchScore(p.name, q);
    if (score <= 0) continue;
    const cur = byPlayer.get(key);
    if (!cur || score > cur.score || (score === cur.score && (p.hit_probability || 0) > (cur.p.hit_probability || 0))) {
      byPlayer.set(key, { p, score });
    }
  }
  const players = [...byPlayer.values()].sort((a, b) => b.score - a.score).slice(0, 4).map(x => x.p);

  // PROPS: a real market-intent query ranks that whole family by
  // probability (the customer's actual question is "what's strongest for
  // HR tonight," not text search); otherwise rank by name/team/prop text
  // match, breaking ties toward the stronger real read. Deliberately does
  // NOT match against p.matchup here -- real bug found 2026-08-25:
  // matchup describes the whole game (e.g. "Philadelphia Phillies @
  // Seattle Mariners"), so a single-team query like "phillies" matched
  // it for BOTH teams' props, surfacing Seattle Mariners players under a
  // Phillies search. Full two-team game context is already handled by
  // the GAMES group above; PROPS stays scoped to the entity actually
  // named in the query.
  let propResults;
  if (marketFamily) {
    propResults = visible.filter(p => p.stat === marketFamily)
      .slice().sort((a, b) => (b.hit_probability || 0) - (a.hit_probability || 0));
  } else {
    propResults = visible
      .map(p => ({ p, score: Math.max(_matchScore(p.name, q), _matchScore(p.team, q), _matchScore(p.prop, q) * 0.9) }))
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score || (b.p.hit_probability || 0) - (a.p.hit_probability || 0))
      .map(x => x.p);
  }
  const propsTotal = propResults.length;
  const propsShown = propResults.slice(0, 5);

  return { teams, games, players, props: propsShown, propsTotal, marketFamily };
}

function initSearch() {
  const input = document.getElementById("global-search");
  const results = document.getElementById("search-results");
  const run = debounce(() => {
    const query = input.value.trim();
    if (query.length < 2) { results.hidden = true; results.innerHTML = ""; return; }
    const { teams, games, players, props, propsTotal, marketFamily } = runSearch(query, publicProps(), DATA.schedule || []);
    if (!teams.length && !games.length && !players.length && !props.length) {
      results.innerHTML = `<div class="search-empty">No matches for "${esc(query)}"</div>`;
    } else {
      let html = "";
      if (teams.length) {
        html += `<div class="search-group-label">Teams</div>`;
        html += teams.map(t => `<button class="search-item" data-team="${esc(t.name)}">
          <span>${esc(t.name)}</span></button>`).join("");
      }
      if (games.length) {
        html += `<div class="search-group-label">Games</div>`;
        html += games.map(g => `<button class="search-item" data-game="${g.game_pk}">
          <span>${esc(g.away_team)} @ ${esc(g.home_team)}</span><span class="s-sub">${gameTimeLabel(g.game_start)}</span></button>`).join("");
      }
      if (players.length) {
        html += `<div class="search-group-label">Players</div>`;
        html += players.map(p => `<button class="search-item" data-open="${p.id}">
          <span>${esc(p.name)}</span><span class="s-sub">${esc(p.team || "")}</span></button>`).join("");
      }
      if (props.length) {
        html += `<div class="search-group-label">${marketFamily ? "Props · " + esc(CATEGORY_LABEL_FOR_FAMILY(marketFamily)) : "Props"}</div>`;
        html += props.map(p => `<button class="search-item" data-open="${p.id}">
          <span>${esc(p.name)} — ${esc(p.prop)}</span><span class="s-sub">${pctBig(p.hit_probability)}</span></button>`).join("");
        if (propsTotal > props.length) {
          // destination-integrity fix: a market-intent query links to the
          // real family filter (unchanged); a plain name/team/prop-text
          // query now carries the search text itself, so this link lands
          // on (approximately) the same N props it promised, not the full
          // unfiltered list.
          const seeAllQuery = marketFamily ? "family=" + encodeURIComponent(marketFamily)
                                            : "search=" + encodeURIComponent(query);
          html += `<a class="search-item search-see-all" href="#/props?${seeAllQuery}">See all ${propsTotal} matching props →</a>`;
        }
      }
      results.innerHTML = html;
    }
    results.hidden = false;
    $all("[data-open]", results).forEach(b => b.addEventListener("click", () => { openDetail(b.dataset.open); results.hidden = true; input.blur(); }));
    $all("[data-game]", results).forEach(b => b.addEventListener("click", () => { go("games", "game_pk=" + b.dataset.game); results.hidden = true; input.blur(); }));
    $all("[data-team]", results).forEach(b => b.addEventListener("click", () => {
      input.value = b.dataset.team; results.hidden = true; input.blur(); go("props"); filters.search = b.dataset.team; renderProps();
    }));
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
function CATEGORY_LABEL_FOR_FAMILY(family) {
  const f = (DATA.families || []).find(x => familyFilterValue(x.stat) === family);
  return f ? f.label : family;
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
// Wording-only escalation point (Part 6 of the UX revamp, 2026-08-26) --
// does NOT change when a channel counts as "stale" for liveStaleChip()'s
// per-prop appearance or any other detection logic (that stays exactly
// LIVE_STALE_THRESHOLD_SECONDS, unchanged, per the explicit instruction
// not to weaken the underlying SLA just to make a warning quieter). It
// only decides which of two calm phrasings the sitewide bar uses: a
// "small delay" note (informational, no visual alarm) below this point,
// versus "updates delayed" (still calm, no red/yellow sitewide banner,
// no ALL-CAPS) above it.
const LIVE_INCIDENT_THRESHOLD_SECONDS = 30 * 60;
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
// Calm wording (Part 6, 2026-08-26): the trigger point is UNCHANGED
// (LIVE_FRESHNESS.stale, still the same LIVE_STALE_THRESHOLD_SECONDS) --
// only the label changed, from an engineering-style "Live Data Stale" /
// "Live Status Unknown" pair to one plain, non-alarming phrase. The fuller
// per-channel explanation lives in the sitewide bar (renderFreshness()),
// not duplicated here at chip scale.
function liveStaleChip(p) {
  if (!LIVE_FRESHNESS.applicable || !LIVE_FRESHNESS.stale) return "";
  if (!LIVE_IN_PROGRESS_GAME_STATES.has(p.game_state)) return "";
  return `<span class="chip chip-stale">Status Delayed</span>`;
}

// Calm, channel-specific freshness copy (Part 6 of the UX revamp,
// 2026-08-26; direct product decision). The OLD behavior showed a
// sitewide ALL-CAPS "LIVE DATA STALE"/"LIVE DATA STATUS UNKNOWN" alarm the
// instant the SLA was exceeded, with no distinction between "the odds
// feed is a little behind" and "we have no idea if this game ended." Real
// production incident (2026-08-26, see P0_LIVE_LIFECYCLE_INCIDENT doc on
// the P0 branch): a customer saw a finished, LOST Top Pick still marked
// "Live" + that same all-caps banner -- individually honest, but alarming
// and confusing together. The underlying SLA/detection is NOT weakened
// here (LIVE_STALE_THRESHOLD_SECONDS, the exact same 15-minute trigger,
// still drives every boolean below) -- only the WORDING changes: healthy
// shows nothing alarming, a small delay reads as a plain fact, and only a
// genuinely prolonged gap gets "delayed" language -- still calm, never a
// full-width red/yellow banner. The engineering-visible alert stays loud
// in Actions/logs (dashboard/check_live_freshness.py, unchanged); this is
// the customer-facing half only.
function freshnessBarMessage(now) {
  const oddsAt = timeMs(DATA.prices_updated_at);
  const oddsAgeSec = oddsAt != null ? Math.max(0, Math.round((now - oddsAt) / 1000)) : null;
  const oddsDelayed = oddsAgeSec != null && oddsAgeSec > LIVE_STALE_THRESHOLD_SECONDS;
  const oddsIncident = oddsAgeSec != null && oddsAgeSec > LIVE_INCIDENT_THRESHOLD_SECONDS;

  const game = LIVE_FRESHNESS;
  const gameDelayed = game.applicable && game.stale;
  const gameIncident = game.applicable
    && (game.reason === "never_checked" || (game.ageSeconds != null && game.ageSeconds > LIVE_INCIDENT_THRESHOLD_SECONDS));

  if (!oddsDelayed && !gameDelayed) return null; // healthy: nothing alarming to add

  if (gameIncident && oddsIncident) {
    return `Updates delayed · last verified ${game.reason === "never_checked" ? "unknown" : liveFreshnessAgoText(game.ageSeconds)}`;
  }
  if (gameIncident) {
    return game.reason === "never_checked"
      ? "Game updates delayed · no check on record yet"
      : `Game updates delayed · last checked ${liveFreshnessAgoText(game.ageSeconds)}`;
  }
  if (oddsIncident) {
    return `FanDuel prices delayed · game status current`;
  }
  // Small delay only (under the incident threshold): a plain fact, no
  // "delayed"/alarm framing at all -- exactly the numbers already shown
  // in the baseline "odds updated Xm ago" text, plus the equivalent for
  // game status when that specific channel is the one running behind.
  if (gameDelayed) return `Game status checked ${liveFreshnessAgoText(game.ageSeconds)}`;
  return null;
}

function renderFreshness() {
  const bar = document.getElementById("freshness-bar");
  const parts = [];
  if (DATA.generated_at) parts.push(`Board built ${_agoText(DATA.generated_at)}`);
  if (DATA.prices_updated_at) parts.push(`odds updated ${_agoText(DATA.prices_updated_at)}`);
  const dateLabel = DATA.date ? LOCAL_DATE_FMT.format(new Date(DATA.date + "T12:00:00Z")) : "";
  const wasStale = LIVE_FRESHNESS.applicable && LIVE_FRESHNESS.stale;
  LIVE_FRESHNESS = liveFreshnessState(Date.now(), DATA, [...PROPS_BY_ID.values()]);
  const msg = freshnessBarMessage(Date.now());
  const staleHtml = msg ? ` · <span class="stale-flag">${esc(msg)}</span>` : "";
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
  // market-edge-semantics fix (P0-6) -- kept in sync with
  // dashboard/live_state.py's PRICE_FIELDS by hand, same as every other
  // entry here.
  "posted_implied", "market_fair", "market_fair_method", "edge_vs_fair",
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
  initSearchToggle();
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
