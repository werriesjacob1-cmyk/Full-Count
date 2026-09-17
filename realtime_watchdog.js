/* CarNow Auto-Claimer — realtime delivery watchdog */
(() => {
  'use strict';
  if (window.top !== window) return;
  if (window.__carnowRealtimeWatchdogLoaded) return;
  window.__carnowRealtimeWatchdogLoaded = true;

  let timer = null;
  let observer = null;
  let torndown = false;

  function evaluateFast() {
    if (torndown) return;
    try {
      const ac = window.__carnowAutoClaimer;
      if (ac && typeof ac.evaluate === 'function') ac.evaluate('realtime-watchdog');
    } catch { /* main script may be between navigations */ }
  }

  // CarNow can update an existing row's text rather than insert a brand-new
  // row node. The main observer only watches added child nodes, so also react
  // to character-data changes here.
  try {
    observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData' ||
            (record.type === 'childList' && (record.addedNodes.length || record.removedNodes.length))) {
          evaluateFast();
          break;
        }
      }
    });
    observer.observe(document.documentElement || document, {
      childList: true,
      subtree: true,
      characterData: true
    });
  } catch { /* no-op */ }

  // 100ms backup scan. This does not replace MutationObserver; it closes the
  // gap when CarNow reuses DOM nodes or an observer event is missed.
  timer = setInterval(evaluateFast, 100);

  // A page restored from Chrome's back-forward cache can retain a dead CarNow
  // WebSocket. Force a real reload so CarNow reconnects before we trust the
  // page for new-lead delivery.
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) location.reload();
  });

  window.addEventListener('pagehide', () => {
    torndown = true;
    if (observer) observer.disconnect();
    if (timer) clearInterval(timer);
  });
})();
