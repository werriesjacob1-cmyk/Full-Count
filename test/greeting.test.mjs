/* Deterministic contract tests for auto_greeting.js. No customer messages sent.
 * Run: node test/greeting.test.mjs
 */
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import assert from 'node:assert/strict';

const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'auto_greeting.js'), 'utf8');
function fixture({ owner = true, draft = '', enabled = true, sendCount = 1 } = {}) {
  let sends = 0;
  let typed = '';
  const ledger = {};
  class Textarea {
    constructor() { this._value = draft; }
    get value() { return this._value; }
    set value(next) { this._value = next; }
    getAttribute(k) { return k === 'placeholder' ? 'Type your message' : null; }
    getBoundingClientRect() { return { width: 120, height: 30 }; }
    focus() {}
    dispatchEvent() {}
  }
  const editor = Object.assign(new Textarea(), {
    isConnected: true, disabled: false, readOnly: false,
    isContentEditable: false, tagName: 'TEXTAREA'
  });
  const send = {
    isConnected: true, disabled: false, textContent: 'Send',
    getAttribute(k) { return k === 'aria-label' ? 'Send' : null; },
    getBoundingClientRect() { return { width: 20, height: 20 }; },
    click() { sends++; typed = editor.value; }
  };
  const panel = { querySelectorAll() { return Array(sendCount).fill(send); } };
  editor.closest = () => panel;
  editor.parentElement = panel;
  const document = {
    body: { innerText: owner
      ? 'Details Claimed Jacob Werries Customer Information Notes'
      : 'Details Claimed Another Seller Customer Information Notes' },
    querySelectorAll(query) { return query.includes('textarea') ? [editor] : []; }
  };
  const window = {}; window.top = window;
  const chrome = { storage: {
    sync: { async get() { return { autoGreeting: enabled, autoClaim: true, dryRun: false, myName: 'Jacob Werries' }; } },
    local: {
      async get() { return { ...ledger }; },
      async set(value) { Object.assign(ledger, value); }
    }
  } };
  runInNewContext(src, {
    window, document, chrome,
    getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
    Event: class { },
    HTMLTextAreaElement: Textarea, HTMLInputElement: Textarea,
    console: { info() { }, warn() { } },
    setTimeout
  });
  return { send: (signature = 'lead-123') => window.__carnowGreeting.attempt({
    signature, ts: Date.now(), dryRun: false
  }), sends: () => sends, typed: () => typed, ledger };
}

const normal = fixture();
assert.equal(await normal.send(), 'send-clicked-unverified');
assert.equal(normal.sends(), 1);
assert.equal(normal.typed(), 'hi there');
await normal.send();
assert.equal(normal.sends(), 1, 'one lead must not be greeted twice');
assert.equal((normal.ledger.carnowGreetingLedgerV1 || []).length, 1);

const other = fixture({ owner: false });
assert.equal(await other.send(), 'owner-not-confirmed');
assert.equal(other.sends(), 0);

const existingDraft = fixture({ draft: 'My manual message' });
assert.equal(await existingDraft.send(), 'no-input');
assert.equal(existingDraft.sends(), 0);

const disabled = fixture({ enabled: false });
assert.equal(await disabled.send(), 'disabled');
assert.equal(disabled.sends(), 0);

const ambiguous = fixture({ sendCount: 2 });
assert.equal(await ambiguous.send(), 'no-send-control');
assert.equal(ambiguous.sends(), 0);

const stale = fixture();
assert.equal(await stale.send('lead-456'), 'send-clicked-unverified');
console.log('Greeting contract: 5 cases passed (mock DOM only; CarNow UI still requires live verification).');
