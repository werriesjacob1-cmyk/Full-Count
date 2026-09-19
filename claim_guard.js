/* =====================================================================
 * CarNow Lead Auto-Claimer — claim lifecycle guard
 * ---------------------------------------------------------------------
 * Hardens the v1.5.x claim flow against CarNow detail views that render
 * without a normal URL transition. The main content script deliberately
 * blocks all additional claims while claimInFlight=true; this guard makes
 * sure that latch can never strand the watcher indefinitely.
 *
 * Rules:
 *   - Reaching the customer Details screen means the claim succeeded.
 *   - Return to the watch list almost immediately after success.
 *   - Preserve the pre-claim baseline across the forced return so leads that
 *     arrived during the detour still look new.
 *   - If no Details screen appears within 8s, allow exactly one clean retry.
 *   - After one failed retry, stand down for that lead rather than spam it.
 * ===================================================================== */

(() => {
  'use strict';

  if (window.top !== window) return;
  if (window.__carnowClaimGuardLoaded) return;
  window.__carnowClaimGuardLoaded = true;

  const PENDING_KEY = '__carnowACPending';
  const BASELINE_KEY = '__carnowACBaseline';
  const RETRY_KEY = '__carnowACGuardRetries';
  const STUCK_MS = 8000;
  const SUCCESS_RETURN_MS = 300;

  let handling = false;
  let inFlightSince = 0;

  const readJson = (key) => {
    try {
      const raw = sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  };

  const writeJson = (key, value) => {
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* noop */ }
  };

  function getRetries() {
    return readJson(RETRY_KEY) || {};
  }

  function setRetry(signature, count) {
    if (!signature) return;
    const retries = getRetries();
    if (count <= 0) delete retries[signature];
    else retries[signature] = count;
    writeJson(RETRY_KEY, retries);
  }

  function targetFromState(pending, baselineState) {
    return (pending && pending.returnTo) ||
      (baselineState && baselineState.url) ||
      'https://app.carnow.com/conversations';
  }

  function forcedReturnUrl(target) {
    try {
      const url = new URL(target, location.origin);
      url.searchParams.set('__ac_return', String(Date.now()));
      return url.toString();
    } catch {
      return 'https://app.carnow.com/conversations?__ac_return=' + Date.now();
    }
  }

  function retargetBaseline(baselineState, nextUrl, removeSignature = null) {
    if (!baselineState || !Array.isArray(baselineState.keys)) return;
    baselineState.url = nextUrl;
    baselineState.ts = Date.now();
    if (removeSignature) {
      baselineState.keys = baselineState.keys.filter((key) => key !== removeSignature);
    }
    writeJson(BASELINE_KEY, baselineState);
  }

  function reportVerified(pending, verified) {
    if (!pending || !pending.signature) return;
    try {
      chrome.runtime.sendMessage({
        type: 'CLAIM_VERIFIED',
        payload: {
          ...pending,
          verified,
          claimedText: verified,
          mine: verified
        }
      }, () => void chrome.runtime.lastError);
    } catch { /* extension context unavailable */ }
  }

  function hardReturn(pending, { retry = false } = {}) {
    const baselineState = readJson(BASELINE_KEY);
    const target = targetFromState(pending, baselineState);
    const nextUrl = forcedReturnUrl(target);
    retargetBaseline(baselineState, nextUrl, retry && pending ? pending.signature : null);
    try { sessionStorage.removeItem(PENDING_KEY); } catch { /* noop */ }
    location.replace(nextUrl);
  }

  function handleSuccess(status) {
    if (handling) return;
    handling = true;

    // On SPA navigation, content.js may have consumed sessionStorage first.
    // Take the in-memory handoff instead; NEVER greet a lead without one.
    const ac = window.__carnowAutoClaimer;
    const pending = readJson(PENDING_KEY) ||
      (ac && typeof ac.greetingHandoff === 'function' ? ac.greetingHandoff() : null);
    if (pending && pending.signature) {
      setRetry(pending.signature, 0);
      reportVerified(pending, true);
    }

    void (async () => {
      // Greeting is opt-in and bounded. No generic Details screen is enough:
      // auto_greeting.js independently verifies "Claimed <my name>".
      if (pending && pending.signature && window.__carnowGreeting) {
        let timeout;
        try {
          // Messaging must never indefinitely strand the lead watcher.
          await Promise.race([
            window.__carnowGreeting.attempt(pending),
            new Promise((resolve) => {
              timeout = setTimeout(() => resolve('greeting-deadline'), 4100);
            })
          ]);
        } catch (err) {
          console.warn('[CarNow AC] greeting skipped', err);
        } finally {
          if (timeout) clearTimeout(timeout);
        }
      }
      // Return to watching regardless of the messaging outcome; do not let
      // a missing composer or disabled feature block the next lead.
      if (handling) setTimeout(() => hardReturn(pending), SUCCESS_RETURN_MS);
    })();
  }

  function handleStuckAttempt() {
    if (handling) return;
    handling = true;

    const pending = readJson(PENDING_KEY);
    if (!pending || !pending.signature) {
      // We cannot safely retry without knowing which row was attempted. Reset
      // the page so the watcher is not dead forever, but do not fabricate a
      // successful claim.
      hardReturn(pending);
      return;
    }

    const retries = getRetries();
    const count = Number(retries[pending.signature] || 0);
    if (count < 1) {
      setRetry(pending.signature, count + 1);
      // Remove only the attempted lead from the carried baseline. After the
      // reload it will still look new and get one more claim attempt.
      hardReturn(pending, { retry: true });
      return;
    }

    // One retry already failed. Record the failure and keep the lead baselined
    // so we do not hammer CarNow indefinitely.
    reportVerified(pending, false);
    setRetry(pending.signature, 0);
    hardReturn(pending);
  }

  setInterval(() => {
    const ac = window.__carnowAutoClaimer;
    if (!ac || typeof ac.status !== 'function') return;

    let status;
    try { status = ac.status(); } catch { return; }

    if (!status.claimInFlight) {
      inFlightSince = 0;
      handling = false;
      return;
    }

    if (!inFlightSince) inFlightSince = Date.now();

    if (status.onClaimDetailPage) {
      handleSuccess(status);
      return;
    }

    if (Date.now() - inFlightSince >= STUCK_MS) {
      handleStuckAttempt();
    }
  }, 100);
})();
