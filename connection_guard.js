/* CarNow Auto-Claimer — durable extension/service-worker link */
(() => {
  'use strict';
  if (window.top !== window) return;
  if (window.__carnowConnectionGuardLoaded) return;
  window.__carnowConnectionGuardLoaded = true;

  const CHECK_MS = 2000;
  let backupPort = null;
  let torndown = false;
  let worker = null;
  let checking = false;

  function connectBackup() {
    if (torndown || backupPort) return;
    try {
      const next = chrome.runtime.connect({ name: 'carnow-keepalive' });
      backupPort = next;
      next.onDisconnect.addListener(() => {
        void chrome.runtime.lastError;
        if (backupPort === next) backupPort = null;
      });
    } catch {
      backupPort = null;
    }
  }

  function disconnectBackup() {
    if (!backupPort) return;
    const old = backupPort;
    backupPort = null;
    try { old.disconnect(); } catch { /* noop */ }
  }

  async function check() {
    if (torndown || checking) return;
    checking = true;
    try {
      const status = await chrome.runtime.sendMessage({ type: 'GET_STATUS' });
      const ports = status && status.ok ? Number(status.ports || 0) : 0;

      // content.js has a legacy keepalive port that intentionally rotates
      // every four minutes. Keep one backup only while that primary link is
      // absent, then drop the backup as soon as the primary reconnects. This
      // keeps the worker reachable without permanently double-counting tabs.
      if (ports === 0) {
        connectBackup();
      } else if (backupPort && ports > 1) {
        disconnectBackup();
      }
    } catch {
      // sendMessage itself wakes a sleeping worker. If it still fails, keep a
      // durable port open and retry on the next Worker-driven tick.
      connectBackup();
    } finally {
      checking = false;
    }
  }

  // A Worker timer is used so a background CarNow tab does not wait minutes
  // for Chrome's normal setTimeout throttling before repairing its link.
  try {
    const src = 'setInterval(function(){postMessage("tick")},' + CHECK_MS + ')';
    const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
    worker = new Worker(url);
    URL.revokeObjectURL(url);
    worker.onmessage = check;
  } catch {
    setInterval(check, CHECK_MS);
  }

  void check();

  window.addEventListener('pagehide', () => {
    torndown = true;
    if (worker) {
      try { worker.terminate(); } catch { /* noop */ }
      worker = null;
    }
    disconnectBackup();
  });
})();
