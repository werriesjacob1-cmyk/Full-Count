/* Mock-DOM greeting contract — no customer messages are sent.
 * These selectors mirror the actual inspected CarNow chat markup.
 * Run: node test/greeting.test.mjs
 */
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import assert from 'node:assert/strict';

const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'auto_greeting.js'), 'utf8');
function fixture({ owner = true, draft = '', enabled = true, sendCount = 1,
                   sendTitle = 'Send', correctButton = true, composerCount = 1 } = {}) {
  let sends = 0;
  let typed = '';
  const ledger = {};
  class Textarea {
    constructor() { this._value = draft; }
    get value() { return this._value; }
    set value(next) { this._value = next; }
    getAttribute(k) {
      if (k === 'placeholder') return 'Enter a message';
      if (k === 'ng-model') return 'chatData.message.body';
      return null;
    }
    getBoundingClientRect() { return { width: 120, height: 30 }; }
    focus() {}
    dispatchEvent() {}
  }
  const editor = Object.assign(new Textarea(), {
    id: 'chat_message_body', className: 'chat-bottom-bar__input__field',
    isConnected: true, disabled: false, readOnly: false,
    isContentEditable: false, tagName: 'TEXTAREA'
  });
  const send = {
    isConnected: true, disabled: false, textContent: '',
    getAttribute(k) {
      if (k === 'data-original-title') return sendTitle;
      if (k === 'ng-click') return correctButton ? 'postMessage()' : 'otherAction()';
      return null;
    },
    querySelector(selector) { return selector === 'i.icon-v3-send' ? {} : null; },
    getBoundingClientRect() { return { width: 20, height: 20 }; },
    click() { sends++; typed = editor.value; editor.value = ''; }
  };
  const document = {
    body: { innerText: owner
      ? 'Details Claimed Jacob Werries Customer Information Notes'
      : 'Details Claimed Another Seller Customer Information Notes' },
    querySelector(selector) { return selector === '#chat_message_body' ? editor : null; },
    querySelectorAll(selector) {
      if (selector.includes('textarea#chat_message_body')) {
        return Array(composerCount).fill(editor);
      }
      if (selector.includes('button.chat-bottom-bar__input__send')) {
        return correctButton ? Array(sendCount).fill(send) : [];
      }
      return [];
    }
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
    Event: class {},
    HTMLTextAreaElement: Textarea, HTMLInputElement: Textarea,
    console: { info() {}, warn() {} },
    setTimeout
  });
  return { attempt: (signature = 'lead-123') => window.__carnowGreeting.attempt({
    signature, ts: Date.now(), dryRun: false
  }), sends: () => sends, typed: () => typed, ledger };
}

const normal = fixture();
assert.equal(await normal.attempt(), 'composer-cleared-delivery-unverified');
assert.equal(normal.sends(), 1);
assert.equal(normal.typed(), 'hi there');
await normal.attempt();
assert.equal(normal.sends(), 1, 'one lead must not be greeted twice');
assert.equal((normal.ledger.carnowGreetingLedgerV1 || []).length, 1);

const other = fixture({ owner: false });
assert.equal(await other.attempt(), 'owner-not-confirmed');
assert.equal(other.sends(), 0);

const existingDraft = fixture({ draft: 'My manual message' });
assert.equal(await existingDraft.attempt(), 'no-input');
assert.equal(existingDraft.sends(), 0);

const disabled = fixture({ enabled: false });
assert.equal(await disabled.attempt(), 'disabled');
assert.equal(disabled.sends(), 0);

const ambiguousButton = fixture({ sendCount: 2 });
assert.equal(await ambiguousButton.attempt(), 'no-send-control');
assert.equal(ambiguousButton.sends(), 0);

const wrongControl = fixture({ correctButton: false });
assert.equal(await wrongControl.attempt(), 'no-send-control');
assert.equal(wrongControl.sends(), 0);

const wrongTitle = fixture({ sendTitle: 'Archive' });
assert.equal(await wrongTitle.attempt(), 'no-send-control');
assert.equal(wrongTitle.sends(), 0);

const ambiguousComposer = fixture({ composerCount: 2 });
assert.equal(await ambiguousComposer.attempt(), 'no-composer');
assert.equal(ambiguousComposer.sends(), 0);

console.log('Greeting contract: 8 mock-DOM cases passed (chat composer settled; live delivery unverified).');
