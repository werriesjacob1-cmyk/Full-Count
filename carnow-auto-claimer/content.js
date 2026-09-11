/* =====================================================================
 * CarNow Lead Auto-Claimer — content script
 * ---------------------------------------------------------------------
 * Injected into every frame of https://*.carnow.com/* at document_idle.
 *
 * Detection runs on three independent paths so a lead is never missed:
 *   1. MutationObserver on the document (and on every open shadow root)
 *      -> synchronous click, typically <2ms after the node lands.
 *   2. A 500ms fallback sweep driven by a Web Worker timer (Worker timers
 *      are exempt from background-tab throttling; setInterval is not).
 *   3. A "SWEEP" push from the service worker alarm, which survives even
 *      when the page's own timers are throttled to 1/min.
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
    soundAlert: true,
    debug: false
  });

  const POLL_INTERVAL_MS   = 500;             // fallback sweep cadence
  const SHADOW_SCAN_MS     = 2000;            // deep shadow-root discovery
  const PURGE_EVERY_MS     = 60 * 1000;       // stale-signature GC cadence
  const SIGNATURE_TTL_MS   = 10 * 60 * 1000;  // 10 minutes, per spec
  const WORKER_WATCHDOG_MS = 3000;            // Worker timer health check
  const MAX_LABEL_LEN      = 60;              // ignore text longer than this
  const MAX_SHADOW_NODES   = 4000;            // perf guard on deep scans

  const CANDIDATE_SELECTOR = [
    'button',
    'a',
    '[role="button"]',
    '.btn',
    'input[type="button"]',
    'input[type="submit"]'
  ].join(',');

  // Whole-word match. \b keeps "claimed" and "unclaim" from matching, since
  // there is no word boundary between "claim" and a neighbouring letter.
  const CLAIM_RE = /\b(claim|accept)\b/i;

  // Hard veto: never click these, even if a claim word is present.
  const VETO_RE = new RegExp([
    '\\b(claimed|unclaim|reclaim|decline|reject|dismiss|cancel|history|report|undo)\\b',
    'cookie', 'consent', 'terms', 'privacy', 'policy', 'agreement',
    'newsletter', 'subscribe', 'marketing'
  ].join('|'), 'i');

  const EXCLUDE_SELECTOR = [
    '[disabled]',
    '.disabled',
    '.claimed',
    '[aria-disabled="true"]',
    '[data-claimed="true"]',
    '[data-disabled="true"]'
  ].join(',');

  // Attributes that tend to carry a stable lead identity on CarNow markup.
  const ID_ATTRS = [
    'data-lead-id', 'data-leadid', 'data-lead',
    'data-conversation-id', 'data-conversationid',
    'data-chat-id', 'data-session-id', 'data-guest-id',
    'data-id', 'data-key', 'data-testid'
  ];

  /* ---------------------------------------------------------------- */
  /* State                                                             */
  /* ---------------------------------------------------------------- */

  let settings = { ...DEFAULTS };

  /** signature -> timestamp of the claim. Purged after SIGNATURE_TTL_MS. */
  const claimedSignatures = new Map();

  /** Element identity guard. Weak, so detached nodes cannot leak. */
  const clickedElements = new WeakSet();

  /** Live MutationObservers (document + one per open shadow root). */
  const observers = new Set();

  /** Shadow roots we have already attached an observer to. */
  const observedRoots = new WeakSet();

  /** Strong list of known shadow roots, pruned when their hosts detach. */
  let knownRoots = [];

  let pollWorker   = null;
  let pollTimer    = null;
  let shadowTimer  = null;
  let purgeTimer   = null;
  let watchdogTimer = null;
  let lastWorkerTick = 0;
  let audioCtx = null;
  let torndown = false;

  const FRAME = (() => {
    try { return window.top === window ? 'top' : 'frame:' + location.pathname; }
    catch { return 'frame:cross-origin'; }
  })();

  /* ---------------------------------------------------------------- */
  /* Logging                                                           */
  /* ---------------------------------------------------------------- */

  function log(...args) {
    if (settings.debug) console.log('%c[CarNow AC]', 'color:#f97316;font-weight:bold', FRAME, ...args);
  }
  function warn(...args) {
    console.warn('[CarNow AC]', FRAME, ...args);
  }

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
      } catch {
        resolve(settings);
      }
    });
  }

  try {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'sync') return;
      for (const [key, { newValue }] of Object.entries(changes)) {
        if (key in DEFAULTS) settings[key] = newValue;
      }
      log('settings updated', settings);
    });
  } catch { /* extension context unavailable */ }

  /* ---------------------------------------------------------------- */
  /* Element matching                                                  */
  /* ---------------------------------------------------------------- */

  /** Short, normalized label for an element: attributes first, then text. */
  function labelOf(el) {
    const attrs = [
      el.getAttribute && el.getAttribute('aria-label'),
      el.getAttribute && el.getAttribute('title'),
      el.getAttribute && el.getAttribute('data-action'),
      el.getAttribute && el.getAttribute('data-testid'),
      el.tagName === 'INPUT' ? el.value : null
    ].filter(Boolean).join(' ');

    // innerText reflects rendered text but forces layout; textContent does not.
    // textContent is enough here and is the cheaper of the two.
    const raw = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const text = raw.length <= MAX_LABEL_LEN ? raw : '';

    return (attrs + ' ' + text).replace(/\s+/g, ' ').trim();
  }

  /**
   * offsetParent is null for display:none subtrees AND for position:fixed
   * elements, which CarNow modals commonly are — so fixed gets a rect check.
   */
  function isVisible(el) {
    if (el.offsetParent !== null) return true;
    let cs;
    try { cs = getComputedStyle(el); } catch { return false; }
    if (!cs || cs.position !== 'fixed') return false;
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function isExcluded(el) {
    if (el.disabled === true) return true;
    try {
      if (el.matches(EXCLUDE_SELECTOR)) return true;
      if (el.closest(EXCLUDE_SELECTOR)) return true;
    } catch { /* malformed selector context */ }
    return false;
  }

  function isClaimCandidate(el) {
    if (!el || el.nodeType !== 1) return false;
    try { if (!el.matches(CANDIDATE_SELECTOR)) return false; } catch { return false; }

    const label = labelOf(el);
    if (!label) return false;
    if (VETO_RE.test(label)) return false;
    if (!CLAIM_RE.test(label)) return false;
    if (isExcluded(el)) return false;
    if (!isVisible(el)) return false;
    return true;
  }

  /* ---------------------------------------------------------------- */
  /* Lead signatures (loop prevention)                                 */
  /* ---------------------------------------------------------------- */

  function domPath(el) {
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 6; depth++) {
      const parent = node.parentElement;
      const index = parent ? Array.prototype.indexOf.call(parent.children, node) : 0;
      parts.push(node.tagName + ':' + index);
      node = parent;
    }
    return parts.join('>');
  }

  /**
   * Identity for a lead. Prefers a real id from the surrounding card so the
   * same lead re-rendered as a fresh node is still recognized; falls back to
   * label + DOM position.
   */
  function signatureOf(el) {
    for (const attr of ID_ATTRS) {
      let holder = null;
      try { holder = el.closest('[' + attr + ']'); } catch { /* ignore */ }
      const value = holder && holder.getAttribute(attr);
      if (value) return attr + '=' + value;
    }
    const card = el.closest('[id]');
    if (card && card.id) return 'id=' + card.id + '|' + labelOf(el);
    return 'path=' + domPath(el) + '|' + labelOf(el);
  }

  function alreadyHandled(el, signature) {
    if (clickedElements.has(el)) return true;
    const at = claimedSignatures.get(signature);
    return at !== undefined && (Date.now() - at) < SIGNATURE_TTL_MS;
  }

  /* ---------------------------------------------------------------- */
  /* Claiming                                                          */
  /* ---------------------------------------------------------------- */

  function fireClick(el) {
    const opts = { bubbles: true, cancelable: true, composed: true, view: window };
    // Some CarNow widgets bind mousedown/pointerdown rather than click, so
    // send the full sequence before falling back to the native .click().
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
      try { el.dispatchEvent(new (type.startsWith('pointer') ? PointerEvent : MouseEvent)(type, opts)); }
      catch { /* PointerEvent unsupported in this frame */ }
    }
    el.click();
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
    } catch (err) {
      log('beep unavailable', err && err.message);
    }
  }

  function report(record) {
    // background.js is the single writer for chrome.storage.local so that
    // claims fired from several frames/tabs at once cannot clobber each other.
    try {
      chrome.runtime.sendMessage({ type: 'LEAD_CLAIMED', payload: record }, () => {
        if (chrome.runtime.lastError) persistLocally(record);
      });
    } catch {
      persistLocally(record);
    }
  }

  /** Only used if the service worker is unreachable. */
  function persistLocally(record) {
    try {
      chrome.storage.local.get({ claimHistory: [] }, (data) => {
        if (chrome.runtime.lastError) return;
        const history = [record, ...(data.claimHistory || [])].slice(0, 200);
        chrome.storage.local.set({ claimHistory: history });
      });
    } catch { /* context invalidated */ }
  }

  function claim(el, source) {
    const started = performance.now();
    const signature = signatureOf(el);

    if (alreadyHandled(el, signature)) return false;

    // Mark BEFORE clicking: the click can synchronously re-render the DOM and
    // re-enter this function through the MutationObserver.
    clickedElements.add(el);
    claimedSignatures.set(signature, Date.now());

    const label = labelOf(el);
    try {
      fireClick(el);
    } catch (err) {
      warn('click failed', err);
      return false;
    }

    const elapsed = performance.now() - started;
    const record = {
      ts: Date.now(),
      label: label.slice(0, MAX_LABEL_LEN),
      signature,
      url: location.href,
      source,
      elapsedMs: Math.round(elapsed * 100) / 100
    };

    log('CLAIMED via ' + source + ' in ' + record.elapsedMs + 'ms —', label);
    beep();
    report(record);
    return true;
  }

  /* ---------------------------------------------------------------- */
  /* Scanning                                                          */
  /* ---------------------------------------------------------------- */

  function candidatesIn(root) {
    let found = [];
    try {
      if (root.nodeType === 1 && isClaimCandidate(root)) found.push(root);
      const nested = root.querySelectorAll ? root.querySelectorAll(CANDIDATE_SELECTOR) : [];
      for (const el of nested) if (isClaimCandidate(el)) found.push(el);
    } catch { /* root detached mid-scan */ }

    // Prefer the innermost match: a div[role="button"] wrapping a real
    // <button> would otherwise produce two clicks for one lead.
    return found.filter((el) => !found.some((other) => other !== el && el.contains(other)));
  }

  function scan(root, source) {
    if (!settings.autoClaim || torndown) return 0;
    let claims = 0;
    for (const el of candidatesIn(root)) {
      if (claim(el, source)) claims++;
    }
    return claims;
  }

  function sweep(source) {
    if (!settings.autoClaim || torndown) return;
    scan(document, source);
    for (const root of knownRoots) {
      if (root && root.host && root.host.isConnected) scan(root, source);
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
        const shadow = el.shadowRoot;           // open roots only; closed are unreachable
        if (shadow && !observedRoots.has(shadow)) {
          observedRoots.add(shadow);
          knownRoots.push(shadow);
          observe(shadow);
          scan(shadow, 'shadow-attach');
          walk(shadow);
        }
      }
    };
    if (root && root.nodeType === 1 && root.shadowRoot && !observedRoots.has(root.shadowRoot)) {
      observedRoots.add(root.shadowRoot);
      knownRoots.push(root.shadowRoot);
      observe(root.shadowRoot);
      scan(root.shadowRoot, 'shadow-attach');
    }
    walk(root);
  }

  function pruneShadowRoots() {
    knownRoots = knownRoots.filter((r) => r && r.host && r.host.isConnected);
  }

  /* ---------------------------------------------------------------- */
  /* Observation                                                       */
  /* ---------------------------------------------------------------- */

  function onMutations(records) {
    if (!settings.autoClaim || torndown) return;

    for (const record of records) {
      if (record.type === 'childList') {
        for (const node of record.addedNodes) {
          if (node.nodeType !== 1) continue;
          scan(node, 'observer');          // synchronous: no timer in the hot path
          discoverShadowRoots(node);
        }
      } else if (record.type === 'attributes') {
        // A card can flip from disabled/hidden to claimable without any node
        // being added — re-check the element and its subtree.
        const target = record.target;
        if (target && target.nodeType === 1) scan(target, 'observer-attr');
      }
    }
  }

  function observe(root) {
    const observer = new MutationObserver(onMutations);
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class', 'disabled', 'aria-disabled', 'style', 'hidden', 'data-claimed']
    });
    observers.add(observer);
    return observer;
  }

  /* ---------------------------------------------------------------- */
  /* Fallback polling (throttle-resistant)                             */
  /* ---------------------------------------------------------------- */

  function startIntervalPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => sweep('poll'), POLL_INTERVAL_MS);
    log('fallback polling: setInterval');
  }

  function startWorkerPolling() {
    // Timers inside a Worker are NOT subject to background-tab throttling,
    // so the 500ms sweep keeps its cadence when the tab is hidden.
    try {
      const source = 'let id=null;self.onmessage=function(e){' +
        'if(e.data&&e.data.t==="start"){clearInterval(id);id=setInterval(function(){self.postMessage("tick");},e.data.ms);}' +
        'else if(e.data&&e.data.t==="stop"){clearInterval(id);id=null;}};';
      const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
      pollWorker = new Worker(url);
      URL.revokeObjectURL(url);
      pollWorker.onmessage = () => { lastWorkerTick = Date.now(); sweep('worker-poll'); };
      pollWorker.onerror = () => { teardownWorker(); startIntervalPolling(); };
      pollWorker.postMessage({ t: 'start', ms: POLL_INTERVAL_MS });
      lastWorkerTick = Date.now();

      // If the page CSP silently blocks blob workers, fall back.
      watchdogTimer = setInterval(() => {
        if (Date.now() - lastWorkerTick > WORKER_WATCHDOG_MS) {
          warn('worker timer stalled; switching to setInterval');
          teardownWorker();
          startIntervalPolling();
        }
      }, WORKER_WATCHDOG_MS);
      log('fallback polling: Worker timer');
    } catch (err) {
      log('worker unavailable, using setInterval', err && err.message);
      startIntervalPolling();
    }
  }

  function teardownWorker() {
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
    if (pollWorker) {
      try { pollWorker.postMessage({ t: 'stop' }); pollWorker.terminate(); } catch { /* already dead */ }
      pollWorker = null;
    }
  }

  /* ---------------------------------------------------------------- */
  /* Memory hygiene                                                    */
  /* ---------------------------------------------------------------- */

  function purge() {
    const cutoff = Date.now() - SIGNATURE_TTL_MS;
    let removed = 0;
    for (const [signature, ts] of claimedSignatures) {
      if (ts < cutoff) { claimedSignatures.delete(signature); removed++; }
    }
    pruneShadowRoots();
    if (removed) log('purged ' + removed + ' stale signatures; ' + claimedSignatures.size + ' retained');
  }

  function teardown() {
    if (torndown) return;
    torndown = true;
    for (const observer of observers) {
      try { observer.disconnect(); } catch { /* already disconnected */ }
    }
    observers.clear();
    teardownWorker();
    if (pollTimer)   { clearInterval(pollTimer);   pollTimer = null; }
    if (shadowTimer) { clearInterval(shadowTimer); shadowTimer = null; }
    if (purgeTimer)  { clearInterval(purgeTimer);  purgeTimer = null; }
    claimedSignatures.clear();
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
    try {
      port = chrome.runtime.connect({ name: 'carnow-keepalive' });
    } catch {
      return; // extension reloaded; the page will need a refresh
    }
    port.onDisconnect.addListener(() => {
      if (chrome.runtime.lastError) { /* expected on SW recycle */ }
      if (!torndown) setTimeout(connectKeepAlive, 1000);
    });
    // Ports are severed after 5 minutes; reconnecting resets the SW idle timer.
    setTimeout(() => { try { port.disconnect(); } catch { /* noop */ } }, 4 * 60 * 1000);
  }

  try {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || typeof message !== 'object') return;
      if (message.type === 'SWEEP') {
        // Pushed by the background alarm; works even when page timers are throttled.
        sweep('alarm');
        sendResponse({ ok: true, frame: FRAME });
      } else if (message.type === 'PING') {
        sendResponse({ ok: true, frame: FRAME, tracked: claimedSignatures.size });
      }
      return false;
    });
  } catch { /* context invalidated */ }

  /* ---------------------------------------------------------------- */
  /* Boot                                                              */
  /* ---------------------------------------------------------------- */

  function boot() {
    torndown = false;
    return loadSettings().then(() => {
      if (torndown) return;
      log('active on', location.href, settings);

      const root = document.documentElement || document;
      observe(root);
      discoverShadowRoots(root);
      sweep('initial');

      startWorkerPolling();
      shadowTimer = setInterval(() => { pruneShadowRoots(); discoverShadowRoots(document.documentElement); }, SHADOW_SCAN_MS);
      purgeTimer  = setInterval(purge, PURGE_EVERY_MS);

      connectKeepAlive();
    });
  }

  boot();

  // pagehide also fires when the page enters the back/forward cache, so the
  // teardown has to be undone if the user navigates back to a live page.
  window.addEventListener('pagehide', teardown);
  window.addEventListener('pageshow', (event) => { if (event.persisted) boot(); });

  // Exposed for manual poking from the DevTools console.
  window.__carnowAutoClaimer = {
    sweep,
    settings: () => ({ ...settings }),
    tracked: () => Array.from(claimedSignatures.entries()),
    candidates: () => candidatesIn(document),
    roots: () => knownRoots.length,
    teardown
  };
})();
