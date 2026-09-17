/* CarNow Auto-Claimer — preflight state hygiene */
(() => {
  'use strict';
  if (window.top !== window) return;

  // The claim guard returns to Conversations with __ac_return. Any ordinary
  // load/refresh of Conversations is NOT a claim return and must start from a
  // clean baseline; otherwise stale sessionStorage can make an existing row
  // look new and produce an "initial" claim attempt.
  try {
    const url = new URL(location.href);
    const isConversationList = url.pathname.startsWith('/conversations');
    const isGuardReturn = url.searchParams.has('__ac_return');
    if (isConversationList && !isGuardReturn) {
      sessionStorage.removeItem('__carnowACPending');
      sessionStorage.removeItem('__carnowACBaseline');
      sessionStorage.removeItem('__carnowACGuardRetries');
    }
  } catch { /* no-op */ }
})();
