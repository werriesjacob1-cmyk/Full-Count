/* CarNow Auto-Claimer — opt-in, one-time chat greeting.
 * This module does not claim leads. It may send only after a fresh extension
 * claim handoff reaches the owner's Details page. Unknown chat UI => skip.
 */
(() => {
  'use strict';
  if (window.top !== window || window.__carnowGreeting) return;

  const MESSAGE = 'hi there';
  const LEDGER_KEY = 'carnowGreetingLedgerV1';
  const MAX_AGE_MS = 20000;
  const UI_WAIT_MS = 2800;
  const attempts = new Map();
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const esc = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function hash(value) {
    let h = 2166136261;
    for (const c of String(value)) {
      h ^= c.charCodeAt(0);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16).padStart(8, '0');
  }

  function visible(element) {
    if (!element || !element.isConnected) return false;
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function ownersDetailsPage(name) {
    if (!name || !document.body) return false;
    const text = norm(document.body.innerText || '');
    // A generic customer Details page alone is NOT enough to message someone.
    return new RegExp('\\bDetails\\s+Claimed\\s+' + esc(norm(name)) + '\\b', 'i').test(text)
      && /\bCustomer Information\b/i.test(text);
  }

  function findComposer() {
    const candidates = document.querySelectorAll(
      'textarea, input[type="text"], [contenteditable="true"], [role="textbox"]'
    );
    const matches = [];
    for (const el of candidates) {
      if (!visible(el) || el.disabled || el.readOnly) continue;
      const hint = norm([
        el.getAttribute('placeholder'), el.getAttribute('aria-label'),
        el.getAttribute('data-placeholder'), el.getAttribute('title')
      ].join(' '));
      if (/\b(note|search|subject|email|phone|filter)\b/i.test(hint)) continue;
      // Require an explicit message/reply/chat label or a chat-specific ancestor.
      const chatHost = el.closest(
        '[class*="chat" i], [id*="chat" i], [class*="messag" i], [id*="messag" i]'
      );
      if (!chatHost && !/\b(chat|message|reply)\b|type here/i.test(hint)) continue;
      if (el.tagName === 'INPUT' && !/\b(chat|message|reply)\b/i.test(hint)) continue;
      matches.push({ el, chatHost, hint });
    }
    // Ambiguous composers => do nothing, never type in an arbitrary input.
    return matches.length === 1 ? matches[0] : null;
  }

  function openChat() {
    const controls = document.querySelectorAll('button, [role="button"], [ng-click]');
    const matches = [...controls].filter((el) => visible(el) &&
      norm(el.textContent) === 'Chat' && el.getBoundingClientRect().width < 300);
    if (matches.length !== 1) return false;
    matches[0].click();
    return true;
  }

  function findSendButton(composer) {
    // Only controls inside the same chat panel as the verified composer.
    const { el, chatHost } = composer;
    const scopes = [];
    if (chatHost) scopes.push(chatHost);
    let parent = el.parentElement;
    for (let i = 0; parent && i < 3; i++, parent = parent.parentElement) {
      if (!scopes.includes(parent)) scopes.push(parent);
    }
    for (const scope of scopes) {
      const matches = [...scope.querySelectorAll('button, [role="button"]')]
        .filter((button) => visible(button) &&
          /^(send|send message|reply)$/i.test(norm(
            button.getAttribute('aria-label') || button.getAttribute('title') ||
            button.textContent
          )));
      if (matches.length === 1) return matches[0];
      if (matches.length > 1) return null;
    }
    return null;
  }

  function setMessage(el, message) {
    if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') {
      if (norm(el.textContent)) return false; // do not overwrite user draft
      el.focus();
      el.textContent = message;
    } else {
      if (norm(el.value)) return false;
      const proto = el.tagName === 'TEXTAREA'
        ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (!setter) return false;
      el.focus();
      setter.call(el, message);
    }
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    return norm(el.isContentEditable ? el.textContent : el.value) === message;
  }

  async function updateLedger(fingerprint, outcome) {
    try {
      const stored = await chrome.storage.local.get({ [LEDGER_KEY]: [] });
      const existing = stored[LEDGER_KEY] || [];
      const list = existing.filter((e) => e.id !== fingerprint);
      list.unshift({ id: fingerprint, ts: Date.now(), outcome });
      await chrome.storage.local.set({ [LEDGER_KEY]: list.slice(0, 500) });
    } catch { /* log unavailable: do not retry the send */ }
    console.info('[CarNow AC greeting]', outcome);
  }

  async function run(pending) {
    if (!pending || !pending.signature || pending.dryRun ||
        !pending.ts || Date.now() - pending.ts > MAX_AGE_MS) return 'no-fresh-handoff';

    const id = hash(pending.signature);
    const settings = await chrome.storage.sync.get({
      autoGreeting: false, autoClaim: false, dryRun: true, myName: ''
    });
    if (!settings.autoGreeting) return 'disabled';
    if (!settings.autoClaim || settings.dryRun) return 'not-live';
    const name = norm(settings.myName);
    if (!name || !ownersDetailsPage(name)) return 'owner-not-confirmed';

    const existing = await chrome.storage.local.get({ [LEDGER_KEY]: [] });
    if ((existing[LEDGER_KEY] || []).some((entry) => entry.id === id)) return 'already-attempted';

    const until = Date.now() + UI_WAIT_MS;
    let opened = false;
    let composer = null;
    while (Date.now() < until) {
      if (!ownersDetailsPage(name)) return 'left-owner-details';
      composer = findComposer();
      if (composer) break;
      if (!opened) opened = openChat();
      await delay(80);
    }
    if (!composer) { await updateLedger(id, 'no-verified-chat-composer'); return 'no-composer'; }
    if (!ownersDetailsPage(name)) return 'left-owner-details';

    // Record BEFORE any possible send: an uncertain click must NEVER be retried.
    await updateLedger(id, 'attempt-started');

    if (!setMessage(composer.el, MESSAGE)) {
      await updateLedger(id, 'draft-present-or-input-failed');
      return 'no-input';
    }

    let send = null;
    for (let i = 0; i < 5; i++) {
      send = findSendButton(composer);
      if (send && !send.disabled && send.getAttribute('aria-disabled') !== 'true') break;
      await delay(75);
    }
    if (!send || send.disabled || send.getAttribute('aria-disabled') === 'true') {
      // Leave the drafted text visible if CarNow has no identifiable Send.
      await updateLedger(id, 'send-button-unverified');
      return 'no-send-control';
    }
    const fresh = await chrome.storage.sync.get({ autoGreeting: false, autoClaim: false, dryRun: true });
    if (!fresh.autoGreeting || !fresh.autoClaim || fresh.dryRun || !ownersDetailsPage(name)) {
      await updateLedger(id, 'turned-off-before-send');
      return 'cancelled';
    }
    send.click(); // exactly one dispatch; delivery confirmation requires live CarNow verification
    await updateLedger(id, 'send-clicked-unverified');
    return 'send-clicked-unverified';
  }

  window.__carnowGreeting = {
    attempt(pending) {
      if (!pending?.signature) return Promise.resolve('no-signature');
      const id = hash(pending.signature);
      if (!attempts.has(id)) {
        attempts.set(id, run(pending).catch(async (error) => {
          await updateLedger(id, 'error-before-or-after-click');
          return 'error';
        }));
      }
      return attempts.get(id);
    }
  };
})();
