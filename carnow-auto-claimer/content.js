/* =====================================================================
 * CarNow Lead Auto-Claimer — content script
 * ---------------------------------------------------------------------
 * CarNow has no "Claim" button: a salesperson claims a lead by TAPPING
 * THE ROW. That makes a naive sweep dangerous — every unclaimed row on
 * screen is a claimable target, including months of backlog.
 *
 * So this script never asks "is this row claimable?". It asks "did this
 * row appear AFTER I started watching?" Everything present at startup is
 * baselined and permanently ignored.
 *
 * Five gates stand between a detected row and a click:
 *   1. ARMED      — a stable baseline has been captured.
 *   2. BURST      — at most MAX_NEW_PER_TICK new rows at once. A filter
 *                   change, sort, page turn or reload makes the whole
 *                   list look new; this vetoes all of them.
 *   3. PAGE       — pagination must be on page 1.
 *   4. FRESHNESS  — the row's timestamp must be recent.
 *   5. RATE LIMIT — min interval between claims, max claims per session.
 * ===================================================================== */

(() => {
  'use strict';

  if (window.__carnowAutoClaimerLoaded) return;
  window.__carnowAutoClaimerLoaded = true;

  /* ---------------------------------------------------------------- */
  /* Configuration                                                     */
  /* ---------------------------------------------------------------- */

  const DEFAULTS = Object.freeze({
    autoClaim: true,
    dryRun: true,              // detect + alert, never click
    soundAlert: true,
    debug: false,
    maxLeadAgeMin: 5,
    minClaimIntervalSec: 10,
    maxClaimsPerSession: 10
  });

  const POLL_INTERVAL_MS   = 500;
  const SHADOW_SCAN_MS     = 2000;
  const PURGE_EVERY_MS     = 60 * 1000;
  const KEY_TTL_MS         = 10 * 60 * 1000;
  const WORKER_WATCHDOG_MS = 3000;
  const MAX_SHADOW_NODES   = 4000;

  /** Baseline settles once the row set stops changing for this long. */
  const BASELINE_QUIET_MS  = 2000;
  const BASELINE_MAX_MS    = 15000;

  /** More new rows than this at once means the VIEW changed, not a lead. */
  const MAX_NEW_PER_TICK   = 3;

  const ROW_SELECTOR = 'tbody tr, [role="row"], [role="listitem"], li[data-id], tr[data-id]';

  const ID_ATTRS = [
    'data-lead-id', 'data-leadid', 'data-lead',
    'data-conversation-id', 'data-conversationid',
    'data-chat-id', 'data-session-id', 'data-guest-id',
    'data-id', 'data-key', 'data-row-key'
  ];

  /**
   * Substrings that change while a row stays the same lead. These are
   * stripped from the key, not matched whole-cell: CarNow packs a date AND
   * a time into one "Last Update" cell, and unread-count badges live inside
   * the name cell. If any of it leaked into the key, an existing row would
   * get a new key the moment a customer replied — and then look like a
   * brand-new lead to the baseline check.
   */
  const VOLATILE_PATTERNS = [
    /\d{1,2}\/\d{1,2}\/\d{2,4}/g,                         // 09/09/2026
    /\d{1,2}:\d{2}(:\d{2})?\s*[ap]\.?m\.?/gi,              // 09:55 am
    /\d+\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)\s*ago/gi,
    /\b\d+[smhdw]\s*ago\b/gi,                              // 2d ago
    /\b\d{1,3}\b/g                                         // unread badges
  ];

  const ABS_TIME_RE = /(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(\d{1,2}):(\d{2})\s*([ap])\.?m\.?/i;
  const REL_TIME_RE = /(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\s+ago/i;

  /* ---------------------------------------------------------------- */
  /* State                                                             */
  /* ---------------------------------------------------------------- */

  let settings = { ...DEFAULTS };

  /** Row keys present at startup. Never claimed, ever. */
  const baseline = new Set();
  let armed = false;

  /** key -> first time we saw it, for keys seen after arming. */
  const seen = new Map();

  /** Keys we have already acted on. */
  const acted = new Set();

  let claimsThisSession = 0;
  let lastClaimAt = 0;

  const observers = new Set();
  const observedRoots = new WeakSet();
  let knownRoots = [];

  let pollWorker = null, pollTimer = null, shadowTimer = null;
  let purgeTimer = null, watchdogTimer = null, baselineTimer = null;
  let lastWorkerTick = 0;
  let audioCtx = null;
  let torndown = false;

  const FRAME = (() => {
    try { return window.top === window ? 'top' : 'frame'; } catch { return 'frame'; }
  })();

  const log  = (...a) => { if (settings.debug) console.log('%c[CarNow AC]', 'color:#f97316;font-weight:bold', ...a); };
  const warn = (...a) => console.warn('[CarNow AC]', ...a);

  /* ---------------------------------------------------------------- */
  /* Settings                                                          */
  /* ---------------------------------------------------------------- */

  function loadSettings() {
    return new Promise((resolve) => {
      try {
        chrome.storage.sync.get(DEFAULTS, (stored) => {
          if (!chrome.runtime.lastError && stored) settings = { ...DEFAULTS, ...stored };
          resolve(settings);
        });
      } catch { resolve(settings); }
    });
  }

  try {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'sync') return;
      for (const [k, { newValue }] of Object.entries(changes)) {
        if (k in DEFAULTS) settings[k] = newValue;
      }
      log('settings updated', settings);
    });
  } catch { /* no extension context */ }

  /* ---------------------------------------------------------------- */
  /* Row identity                                                      */
  /* ---------------------------------------------------------------- */

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

  function stripVolatile(text) {
    let out = text;
    for (const re of VOLATILE_PATTERNS) out = out.replace(re, ' ');
    return out.replace(/\s+/g, ' ').trim();
  }

  /**
   * A key that survives the list's periodic re-render. Timestamps are
   * excluded: "Last Update" ticks on refresh, and a changing key would
   * make an existing row look brand new.
   */
  function rowKey(row) {
    for (const attr of ID_ATTRS) {
      const v = row.getAttribute && row.getAttribute(attr);
      if (v) return attr + '=' + v;
    }
    const link = row.querySelector && row.querySelector('a[href]');
    const href = link && link.getAttribute('href');
    if (href && href !== '#') return 'href=' + href;

    const cells = Array.from(row.children || [])
      .map((c) => stripVolatile(norm(c.textContent)))
      .filter(Boolean);

    if (!cells.length) return null;
    return 'cells=' + cells.join('|').slice(0, 200);
  }

  /** Age of the row from its timestamp cell; null when unparseable. */
  function rowAgeMs(row) {
    const text = norm(row.textContent);

    const rel = text.match(REL_TIME_RE);
    if (rel) {
      const n = parseInt(rel[1], 10);
      const unit = rel[2].toLowerCase();
      const mult = unit.startsWith('sec') ? 1e3
                 : unit.startsWith('min') ? 6e4
                 : unit.startsWith('hour') || unit.startsWith('hr') ? 36e5
                 : 864e5;
      return n * mult;
    }

    const abs = text.match(ABS_TIME_RE);
    if (abs) {
      let hour = parseInt(abs[4], 10) % 12;
      if (abs[6].toLowerCase() === 'p') hour += 12;
      const d = new Date(
        parseInt(abs[3], 10), parseInt(abs[1], 10) - 1, parseInt(abs[2], 10),
        hour, parseInt(abs[5], 10)
      );
      const age = Date.now() - d.getTime();
      return Number.isFinite(age) ? age : null;
    }
    return null;
  }

  function isVisible(el) {
    if (el.offsetParent !== null) return true;
    let cs;
    try { cs = getComputedStyle(el); } catch { return false; }
    if (!cs || cs.position !== 'fixed') return false;
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  /* ---------------------------------------------------------------- */
  /* Row collection                                                    */
  /* ---------------------------------------------------------------- */

  function collectRows() {
    const rows = [];
    const push = (root) => {
      let found;
      try { found = root.querySelectorAll(ROW_SELECTOR); } catch { return; }
      for (const row of found) {
        // Header rows have no data cells worth keying.
        if (row.querySelector('th') && !row.querySelector('td')) continue;
        if (!isVisible(row)) continue;
        const key = rowKey(row);
        if (key) rows.push({ row, key });
      }
    };
    push(document);
    for (const root of knownRoots) {
      if (root && root.host && root.host.isConnected) push(root);
    }
    return rows;
  }

  /* ---------------------------------------------------------------- */
  /* Gates                                                             */
  /* ---------------------------------------------------------------- */

  /** Pagination must be on page 1, when we can determine it at all. */
  function onFirstPage() {
    let current = document.querySelector('[aria-current="page"]');
    if (!current) {
      const container = document.querySelector('.pagination, [class*="paginat"], nav[aria-label*="agination" i]');
      if (container) {
        current = container.querySelector('.active, .selected, [class*="active"], [class*="current"]');
      }
    }
    if (!current) return true;               // no pagination detected
    const label = norm(current.textContent);
    return label === '' || label === '1';
  }

  function rateLimitOk() {
    if (claimsThisSession >= settings.maxClaimsPerSession) {
      warn('session claim cap reached (' + settings.maxClaimsPerSession + ') — standing down');
      return false;
    }
    const since = Date.now() - lastClaimAt;
    if (lastClaimAt && since < settings.minClaimIntervalSec * 1000) {
      log('rate limited; ' + Math.round((settings.minClaimIntervalSec * 1000 - since) / 1000) + 's to go');
      return false;
    }
    return true;
  }

  function freshEnough(row) {
    const age = rowAgeMs(row);
    if (age === null) return true;           // unparseable; baseline already gates it
    const limit = settings.maxLeadAgeMin * 60 * 1000;
    if (age > limit) {
      log('row too old (' + Math.round(age / 60000) + 'm) — skipping');
      return false;
    }
    return true;
  }

  /* ---------------------------------------------------------------- */
  /* Claiming                                                          */
  /* ---------------------------------------------------------------- */

  /** The tappable element inside the row — the link if there is one. */
  function clickTargetFor(row) {
    const link = row.querySelector('a[href]:not([href="#"])');
    if (link && isVisible(link)) return link;
    const button = row.querySelector('[role="button"], button');
    if (button && isVisible(button)) return button;
    const firstCell = row.querySelector('td, [role="cell"], [role="gridcell"]');
    if (firstCell && isVisible(firstCell)) return firstCell;
    return row;
  }

  function fireClick(el) {
    const opts = { bubbles: true, cancelable: true, composed: true, view: window };
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
      try {
        const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
        el.dispatchEvent(new Ctor(type, opts));
      } catch { /* PointerEvent unsupported */ }
    }
    if (typeof el.click === 'function') el.click();
    else el.dispatchEvent(new MouseEvent('click', opts));
  }

  function beep() {
    if (!settings.soundAlert) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.22);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + 0.25);
    } catch (err) { log('beep unavailable', err && err.message); }
  }

  function report(record) {
    try {
      chrome.runtime.sendMessage({ type: 'LEAD_CLAIMED', payload: record }, () => {
        if (chrome.runtime.lastError) persistLocally(record);
      });
    } catch { persistLocally(record); }
  }

  function persistLocally(record) {
    try {
      chrome.storage.local.get({ claimHistory: [] }, (data) => {
        if (chrome.runtime.lastError) return;
        const history = [record, ...(data.claimHistory || [])].slice(0, 200);
        chrome.storage.local.set({ claimHistory: history });
      });
    } catch { /* context invalidated */ }
  }

  function act(entry, source) {
    const started = performance.now();
    const { row, key } = entry;

    acted.add(key);

    const label = norm(row.textContent).slice(0, 80);
    const dry = settings.dryRun;

    if (!dry) {
      const target = clickTargetFor(row);
      try {
        fireClick(target);
      } catch (err) {
        warn('click failed', err);
        return;
      }
      claimsThisSession++;
      lastClaimAt = Date.now();
    }

    const record = {
      ts: Date.now(),
      label,
      signature: key,
      url: location.href,
      source,
      dryRun: dry,
      elapsedMs: Math.round((performance.now() - started) * 100) / 100
    };

    log((dry ? 'WOULD CLAIM (dry run)' : 'CLAIMED') + ' via ' + source +
        ' in ' + record.elapsedMs + 'ms —', label);
    beep();
    report(record);
  }

  /* ---------------------------------------------------------------- */
  /* Detection                                                         */
  /* ---------------------------------------------------------------- */

  function evaluate(source) {
    if (torndown || !settings.autoClaim) return;

    const rows = collectRows();
    if (!rows.length) return;

    if (!armed) { captureBaseline(rows); return; }

    const fresh = rows.filter((e) => !baseline.has(e.key) && !acted.has(e.key));
    if (!fresh.length) return;

    for (const entry of fresh) {
      if (!seen.has(entry.key)) seen.set(entry.key, Date.now());
    }

    // GATE 2 — a burst means the view changed (filter, sort, page, reload).
    // Re-baseline to the new view and claim nothing from it.
    if (fresh.length > MAX_NEW_PER_TICK) {
      warn(fresh.length + ' new rows at once — treating as a view change, re-baselining');
      for (const entry of rows) baseline.add(entry.key);
      return;
    }

    // GATE 3
    if (!onFirstPage()) { log('not on page 1 — standing down'); return; }

    for (const entry of fresh) {
      if (acted.has(entry.key)) continue;
      if (!freshEnough(entry.row)) { acted.add(entry.key); continue; }  // GATE 4
      if (!rateLimitOk()) return;                                       // GATE 5
      act(entry, source);
    }
  }

  /* ---------------------------------------------------------------- */
  /* Baseline                                                          */
  /* ---------------------------------------------------------------- */

  let baselineStarted = 0;
  let lastBaselineSize = -1;
  let lastBaselineChange = 0;

  function captureBaseline(rows) {
    const now = Date.now();
    if (!baselineStarted) { baselineStarted = now; lastBaselineChange = now; }

    for (const entry of rows) baseline.add(entry.key);

    if (baseline.size !== lastBaselineSize) {
      lastBaselineSize = baseline.size;
      lastBaselineChange = now;
    }

    const quiet = now - lastBaselineChange >= BASELINE_QUIET_MS;
    const timedOut = now - baselineStarted >= BASELINE_MAX_MS;

    if (quiet || timedOut) {
      armed = true;
      if (baselineTimer) { clearInterval(baselineTimer); baselineTimer = null; }
      console.log('%c[CarNow AC] ARMED', 'color:#16a34a;font-weight:bold',
        '— baseline of ' + baseline.size + ' existing rows ignored.' +
        (settings.dryRun ? ' DRY RUN: will alert only, no clicks.' : ' LIVE: will click new rows.'));
    }
  }

  /* ---------------------------------------------------------------- */
  /* Shadow DOM                                                        */
  /* ---------------------------------------------------------------- */

  function discoverShadowRoots(root) {
    let scanned = 0;
    const walk = (node) => {
      let elements;
      try { elements = node.querySelectorAll('*'); } catch { return; }
      for (const el of elements) {
        if (++scanned > MAX_SHADOW_NODES) return;
        const shadow = el.shadowRoot;
        if (shadow && !observedRoots.has(shadow)) {
          observedRoots.add(shadow);
          knownRoots.push(shadow);
          observe(shadow);
          walk(shadow);
        }
      }
    };
    walk(root);
  }

  const pruneShadowRoots = () => {
    knownRoots = knownRoots.filter((r) => r && r.host && r.host.isConnected);
  };

  /* ---------------------------------------------------------------- */
  /* Observation                                                       */
  /* ---------------------------------------------------------------- */

  function onMutations(records) {
    if (torndown || !settings.autoClaim) return;
    let relevant = false;
    for (const record of records) {
      if (record.type === 'childList' && record.addedNodes.length) {
        for (const node of record.addedNodes) {
          if (node.nodeType === 1) { relevant = true; discoverShadowRoots(node); }
        }
      }
    }
    if (relevant) evaluate('observer');
  }

  function observe(root) {
    const observer = new MutationObserver(onMutations);
    observer.observe(root, { childList: true, subtree: true });
    observers.add(observer);
    return observer;
  }

  /* ---------------------------------------------------------------- */
  /* Fallback polling                                                  */
  /* ---------------------------------------------------------------- */

  function startIntervalPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => evaluate('poll'), POLL_INTERVAL_MS);
    log('fallback polling: setInterval');
  }

  function startWorkerPolling() {
    try {
      const src = 'let id=null;self.onmessage=function(e){' +
        'if(e.data&&e.data.t==="start"){clearInterval(id);id=setInterval(function(){self.postMessage("tick");},e.data.ms);}' +
        'else if(e.data&&e.data.t==="stop"){clearInterval(id);id=null;}};';
      const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
      pollWorker = new Worker(url);
      URL.revokeObjectURL(url);
      pollWorker.onmessage = () => { lastWorkerTick = Date.now(); evaluate('worker-poll'); };
      pollWorker.onerror = () => { teardownWorker(); startIntervalPolling(); };
      pollWorker.postMessage({ t: 'start', ms: POLL_INTERVAL_MS });
      lastWorkerTick = Date.now();
      watchdogTimer = setInterval(() => {
        if (Date.now() - lastWorkerTick > WORKER_WATCHDOG_MS) {
          warn('worker timer stalled; switching to setInterval');
          teardownWorker();
          startIntervalPolling();
        }
      }, WORKER_WATCHDOG_MS);
      log('fallback polling: Worker timer');
    } catch (err) {
      log('worker unavailable', err && err.message);
      startIntervalPolling();
    }
  }

  function teardownWorker() {
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
    if (pollWorker) {
      try { pollWorker.postMessage({ t: 'stop' }); pollWorker.terminate(); } catch { /* dead */ }
      pollWorker = null;
    }
  }

  /* ---------------------------------------------------------------- */
  /* Memory hygiene                                                    */
  /* ---------------------------------------------------------------- */

  function purge() {
    const cutoff = Date.now() - KEY_TTL_MS;
    let removed = 0;
    for (const [key, ts] of seen) {
      if (ts < cutoff) { seen.delete(key); removed++; }
    }
    pruneShadowRoots();
    if (removed) log('purged ' + removed + ' stale keys');
  }

  function teardown() {
    if (torndown) return;
    torndown = true;
    for (const o of observers) { try { o.disconnect(); } catch { /* noop */ } }
    observers.clear();
    teardownWorker();
    for (const t of [pollTimer, shadowTimer, purgeTimer, baselineTimer]) if (t) clearInterval(t);
    pollTimer = shadowTimer = purgeTimer = baselineTimer = null;
    seen.clear();
    knownRoots = [];
    if (audioCtx) { try { audioCtx.close(); } catch { /* noop */ } audioCtx = null; }
    log('torn down');
  }

  /* ---------------------------------------------------------------- */
  /* Service-worker channel                                            */
  /* ---------------------------------------------------------------- */

  function connectKeepAlive() {
    if (torndown) return;
    let port;
    try { port = chrome.runtime.connect({ name: 'carnow-keepalive' }); } catch { return; }
    port.onDisconnect.addListener(() => {
      void chrome.runtime.lastError;
      if (!torndown) setTimeout(connectKeepAlive, 1000);
    });
    setTimeout(() => { try { port.disconnect(); } catch { /* noop */ } }, 4 * 60 * 1000);
  }

  try {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || typeof message !== 'object') return false;
      if (message.type === 'SWEEP') {
        evaluate('alarm');
        sendResponse({ ok: true, armed, baseline: baseline.size });
      } else if (message.type === 'PING') {
        sendResponse({ ok: true, armed, baseline: baseline.size, claims: claimsThisSession });
      }
      return false;
    });
  } catch { /* no context */ }

  /* ---------------------------------------------------------------- */
  /* Boot                                                              */
  /* ---------------------------------------------------------------- */

  function boot() {
    torndown = false;
    armed = false;
    baseline.clear();
    baselineStarted = 0;
    lastBaselineSize = -1;

    return loadSettings().then(() => {
      if (torndown) return;
      const root = document.documentElement || document;
      observe(root);
      discoverShadowRoots(root);

      // Poll until the list settles, then arm.
      baselineTimer = setInterval(() => evaluate('baseline'), 250);
      evaluate('initial');

      startWorkerPolling();
      shadowTimer = setInterval(() => { pruneShadowRoots(); discoverShadowRoots(document.documentElement); }, SHADOW_SCAN_MS);
      purgeTimer  = setInterval(purge, PURGE_EVERY_MS);
      connectKeepAlive();
      log('watching', location.href, settings);
    });
  }

  boot();

  window.addEventListener('pagehide', teardown);
  window.addEventListener('pageshow', (e) => { if (e.persisted) boot(); });

  window.__carnowAutoClaimer = {
    status: () => ({
      armed,
      dryRun: settings.dryRun,
      baselineRows: baseline.size,
      claimsThisSession,
      newSinceArmed: Array.from(seen.keys()),
      onFirstPage: onFirstPage()
    }),
    rows: () => collectRows().map((e) => ({
      key: e.key,
      ageMin: (() => { const a = rowAgeMs(e.row); return a === null ? null : Math.round(a / 60000); })(),
      baselined: baseline.has(e.key),
      text: norm(e.row.textContent).slice(0, 90)
    })),
    settings: () => ({ ...settings }),
    rebaseline: () => { armed = false; baseline.clear(); baselineStarted = 0; lastBaselineSize = -1; evaluate('manual'); },
    evaluate,
    teardown
  };
})();
