/* CarNow Auto-Claimer — durable extension/service-worker link */
(() => {
  'use strict';
  if (window.top !== window) return;
  if (window.__carnowConnectionGuardLoaded) return;
  window.__carnowConnectionGuardLoaded = true;

  const CHECK_MS = 2000;
  const STALE_MS = 45000;
  let port = null;
  let lastContact = 0;
  let torndown = false;
  let worker = null;

  function connect() {
    if (torndown || port) return;
    try {
      const next = chrome.runtime.connect({ name: 'carnow-keepalive' });
      port = next;
      lastContact = Date.now();

      next.onMessage.addListener(() => {
        lastContact = Date.now();
      });

      next.onDisconnect.addListener(() => {
        void chrome.runtime.lastError;
        if (port === next) port = null;
      });
    } catch {
      port = null;
    }
  }

  function check() {
    if (torndown) return;

    if (!port) {
      connect();
      return;
    }

    // The background worker sends HEARTBEAT about every 30 seconds. If that
    // traffic disappears, rebuild the port instead of leaving a zombie link.
    if (lastContact && Date.now() - lastContact > STALE_MS) {
      try { port.disconnect(); } catch { /* noop */ }
      port = null;
      connect();
    }
  }

  // Use a Worker timer so Chrome background-tab throttling cannot postpone
  // recovery for minutes. This is intentionally independent of content.js's
  // legacy port, which used to rotate itself every four minutes.
  try {
    const src = 'setInterval(function(){postMessage("tick")},' + CHECK_MS + ')';
    const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
    worker = new Worker(url);
    URL.revokeObjectURL(url);
    worker.onmessage = check;
  } catch {
    setInterval(check, CHECK_MS);
  }

  connect();

  window.addEventListener('pagehide', () => {
    torndown = true;
    if (worker) {
      try { worker.terminate(); } catch { /* noop */ }
      worker = null;
    }
    if (port) {
      try { port.disconnect(); } catch { /* noop */ }
      port = null;
    }
  });
})();
