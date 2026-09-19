/* CarNow Auto-Claimer — black-box diagnostics + fast heartbeat */
(() => {
  'use strict';
  if (window.top !== window) return;
  if (window.__carnowDiagnosticsLoaded) return;
  window.__carnowDiagnosticsLoaded = true;

  const BUILD = '1.6.2';
  const LOG_KEY = 'carnowDiagnosticLog';
  const MAX_LOG = 500;
  const FAST_SCAN_MS = 250;
  const HEARTBEAT_MS = 2000;
  const SNAPSHOT_LOG_MS = 15000;

  let queue = [];
  let writeChain = Promise.resolve();
  let lastSnapshot = null;
  let lastHeartbeat = 0;
  let lastSnapshotLog = 0;
  let worker = null;
  let stopped = false;

  const hash = (value) => {
    const s = String(value || '');
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16).padStart(8, '0');
  };

  function safeSnapshot() {
    const ac = window.__carnowAutoClaimer;
    if (!ac || typeof ac.status !== 'function') {
      return {
        build: BUILD,
        ready: false,
        ts: Date.now(),
        url: location.pathname,
        visibility: document.visibilityState,
        online: navigator.onLine
      };
    }

    let status = {};
    let settings = {};
    let rows = [];
    try { status = ac.status() || {}; } catch { /* noop */ }
    try { settings = typeof ac.settings === 'function' ? ac.settings() : {}; } catch { /* noop */ }
    try { rows = typeof ac.rows === 'function' ? ac.rows() : []; } catch { /* noop */ }

    const top = rows[0] || null;
    const scheduleAllowed = settings.scheduleEnabled === false || status.scheduleActive === true;
    const shouldWatch = settings.autoClaim === true && settings.dryRun !== true && scheduleAllowed;

    return {
      build: BUILD,
      ready: true,
      ts: Date.now(),
      url: location.pathname,
      visibility: document.visibilityState,
      online: navigator.onLine,
      shouldWatch,
      autoClaim: settings.autoClaim === true,
      dryRun: settings.dryRun === true,
      scheduleEnabled: settings.scheduleEnabled !== false,
      scheduleActive: status.scheduleActive === true,
      armed: status.armed === true,
      claimInFlight: status.claimInFlight === true,
      onClaimDetailPage: status.onClaimDetailPage === true,
      liveHelpList: status.liveHelpList === true,
      baselineRows: Number(status.baselineRows || 0),
      claimsThisSession: Number(status.claimsThisSession || 0),
      rowCount: rows.length,
      topKey: top ? hash(top.key) : null,
      topKeyType: top && top.key ? String(top.key).split('=')[0] : null,
      topBaselined: top ? Boolean(top.baselined) : null,
      topAgeMin: top && top.ageMin !== undefined ? top.ageMin : null
    };
  }

  function meaningfulDiff(a, b) {
    if (!a || !b) return true;
    const keys = [
      'ready','url','visibility','online','shouldWatch','autoClaim','dryRun',
      'scheduleEnabled','scheduleActive','armed','claimInFlight',
      'onClaimDetailPage','liveHelpList','baselineRows','claimsThisSession',
      'rowCount','topKey','topBaselined'
    ];
    return keys.some((k) => a[k] !== b[k]);
  }

  function record(type, detail = {}) {
    queue.push({ ts: Date.now(), type, build: BUILD, ...detail });
    if (queue.length > 40) void flush();
  }

  function flush() {
    if (!queue.length) return writeChain;
    const batch = queue.splice(0, queue.length);
    writeChain = writeChain.then(async () => {
      try {
        const stored = await chrome.storage.local.get({ [LOG_KEY]: [] });
        const log = [...(stored[LOG_KEY] || []), ...batch].slice(-MAX_LOG);
        await chrome.storage.local.set({ [LOG_KEY]: log });
      } catch { /* extension context unavailable */ }
    });
    return writeChain;
  }

  async function sendHeartbeat(snapshot) {
    try {
      await chrome.runtime.sendMessage({ type: 'HEALTH_PING', payload: snapshot });
    } catch { /* worker restarting */ }
  }

  function tick() {
    if (stopped) return;

    const ac = window.__carnowAutoClaimer;
    if (ac && typeof ac.evaluate === 'function') {
      try { ac.evaluate('reliability-fast'); } catch { /* main script between routes */ }
    }

    const now = Date.now();
    const snapshot = safeSnapshot();

    if (meaningfulDiff(lastSnapshot, snapshot)) {
      record('state-change', snapshot);
      lastSnapshot = snapshot;
    }

    if (now - lastHeartbeat >= HEARTBEAT_MS) {
      lastHeartbeat = now;
      void sendHeartbeat(snapshot);
    }

    if (now - lastSnapshotLog >= SNAPSHOT_LOG_MS) {
      lastSnapshotLog = now;
      record('heartbeat', snapshot);
    }
  }

  function startWorker() {
    try {
      const src = 'setInterval(function(){postMessage("tick")},' + FAST_SCAN_MS + ')';
      const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
      worker = new Worker(url);
      URL.revokeObjectURL(url);
      worker.onmessage = tick;
    } catch {
      setInterval(tick, FAST_SCAN_MS);
    }
  }

  document.addEventListener('visibilitychange', () => {
    record('visibility', { visibility: document.visibilityState, snapshot: safeSnapshot() });
    void flush();
  });
  window.addEventListener('online', () => record('network-online', safeSnapshot()));
  window.addEventListener('offline', () => record('network-offline', safeSnapshot()));
  window.addEventListener('pageshow', (e) => record('pageshow', { persisted: Boolean(e.persisted), snapshot: safeSnapshot() }));
  window.addEventListener('pagehide', (e) => {
    record('pagehide', { persisted: Boolean(e.persisted), snapshot: safeSnapshot() });
    void flush();
  });
  window.addEventListener('freeze', () => { record('freeze', safeSnapshot()); void flush(); });
  window.addEventListener('resume', () => record('resume', safeSnapshot()));
  window.addEventListener('error', (e) => record('page-error', { message: String(e.message || ''), snapshot: safeSnapshot() }));
  window.addEventListener('unhandledrejection', (e) => record('unhandled-rejection', { reason: String(e.reason || ''), snapshot: safeSnapshot() }));

  setInterval(() => { void flush(); }, 3000);
  startWorker();
  tick();
  record('diagnostics-start', safeSnapshot());

  window.__carnowDiagnostics = {
    build: BUILD,
    snapshot: safeSnapshot,
    recent: async (limit = 100) => {
      const stored = await chrome.storage.local.get({ [LOG_KEY]: [] });
      return (stored[LOG_KEY] || []).slice(-Math.max(1, Math.min(500, Number(limit) || 100)));
    },
    export: async () => {
      const stored = await chrome.storage.local.get({ [LOG_KEY]: [] });
      return JSON.stringify({ build: BUILD, exportedAt: new Date().toISOString(), log: stored[LOG_KEY] || [] }, null, 2);
    },
    clear: async () => chrome.storage.local.set({ [LOG_KEY]: [] })
  };

  window.addEventListener('pagehide', () => {
    stopped = true;
    if (worker) {
      try { worker.terminate(); } catch { /* noop */ }
      worker = null;
    }
  });
})();
