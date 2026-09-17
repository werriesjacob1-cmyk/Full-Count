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

      // Distinct leads may arrive back-to-back. Same-lead repeat protection is
      // handled by the baseline/acted/in-flight guards, not a long global wait.
      if (Number(current.minClaimIntervalSec) > 1) patch.minClaimIntervalSec = 1;
      if (Number(current.maxClaimsPerSession) < 200) patch.maxClaimsPerSession = 200;

      if (Object.keys(patch).length) await chrome.storage.sync.set(patch);
    } catch { /* extension context unavailable */ }
  }

  void applyPolicy();
})();
