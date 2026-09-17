/* CarNow Auto-Claimer — production reliability policy
 *
 * These values are deliberately conservative about duplicate clicks but
 * aggressive about distinct new leads. The old 10-second global throttle
 * could make a second legitimate lead wait long enough to lose the race.
 */
(() => {
  'use strict';
  if (window.top !== window) return;
  if (window.__carnowReliabilityPolicyLoaded) return;
  window.__carnowReliabilityPolicyLoaded = true;

  async function applyPolicy() {
    try {
      const current = await chrome.storage.sync.get({
        minClaimIntervalSec: 10,
        maxClaimsPerSession: 200
      });
      const patch = {};

      // 500ms is long enough to stop a single evaluation pass from dispatching
      // two navigation-producing clicks, but short enough that a second real
      // lead is not forced to sit behind the old 10-second throttle.
      if (Number(current.minClaimIntervalSec) > 0.5) patch.minClaimIntervalSec = 0.5;
      if (Number(current.maxClaimsPerSession) < 200) patch.maxClaimsPerSession = 200;

      if (Object.keys(patch).length) await chrome.storage.sync.set(patch);
    } catch { /* extension context unavailable */ }
  }

  void applyPolicy();
})();
