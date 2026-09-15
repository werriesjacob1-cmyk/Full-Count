/* =====================================================================
 * CarNow Lead Auto-Claimer — service worker
 * ---------------------------------------------------------------------
 * Responsibilities:
 *   - Keep the MV3 service worker warm (alarm + long-lived ports).
 *   - Push a SWEEP to every CarNow tab on each alarm tick, so detection
 *     survives background-tab timer throttling.
 *   - Act as the single writer for chrome.storage.local claim history, so
 *     simultaneous claims from multiple frames/tabs cannot clobber it.
 *   - Raise a desktop notification for each claim.
 * ===================================================================== */

const DEFAULTS = Object.freeze({
  autoClaim: true,
  scheduleEnabled: true,
  dryRun: true,
  soundAlert: true,
  debug: false,
  phonePushEnabled: true,
  phoneRemoteEnabled: true,
  maxLeadAgeMin: 5,
  minClaimIntervalSec: 10,
  maxClaimsPerSession: 200,
  returnToList: true,
  returnDelaySec: 5,
  myName: ''
});

const KEEPALIVE_ALARM = 'carnow-keepalive';
const ALARM_PERIOD_MIN = 0.5;      // 30s — below the SW's 30s idle timeout
const HISTORY_LIMIT = 200;
const CARNOW_FILTER = { url: ['https://*.carnow.com/*'] };

/** Serializes every storage.local read-modify-write. */
let writeChain = Promise.resolve();

/* -------------------------------------------------------------------- */
/* Helpers                                                              */
/* -------------------------------------------------------------------- */

async function getSettings() {
  try {
    return { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  } catch {
    return { ...DEFAULTS };
  }
}

async function debugLog(...args) {
  const { debug } = await getSettings();
  if (debug) console.log('[CarNow AC / sw]', ...args);
}

function todayKey(ts = Date.now()) {
  return new Date(ts).toISOString().slice(0, 10);
}

/* -------------------------------------------------------------------- */
/* Claim history + badge                                                */
/* -------------------------------------------------------------------- */

function appendClaim(record) {
  writeChain = writeChain.then(async () => {
    const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
    const history = [record, ...claimHistory].slice(0, HISTORY_LIMIT);
    await chrome.storage.local.set({ claimHistory: history, lastClaimAt: record.ts });
    return history;
  }).catch((err) => {
    console.warn('[CarNow AC / sw] history write failed', err);
    return [];
  });
  return writeChain;
}

async function refreshBadge(history) {
  const today = todayKey();
  const count = history.filter((entry) => todayKey(entry.ts) === today).length;
  try {
    await chrome.action.setBadgeBackgroundColor({ color: '#f97316' });
    await chrome.action.setBadgeText({ text: count ? String(count) : '' });
    await chrome.action.setTitle({
      title: `CarNow Lead Auto-Claimer — ${count} claim${count === 1 ? '' : 's'} today`
    });
  } catch { /* action API unavailable during teardown */ }
}

/* -------------------------------------------------------------------- */
/* Notifications                                                        */
/* -------------------------------------------------------------------- */

/** notificationId -> tabId, so clicking the toast focuses the right tab. */
const notificationTargets = new Map();

async function notifyClaim(record, tabId) {
  const when = new Date(record.ts).toLocaleTimeString();
  const id = `carnow-claim-${record.ts}-${Math.random().toString(36).slice(2, 8)}`;
  try {
    await chrome.notifications.create(id, {
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icons/icon128.png'),
      title: record.dryRun ? 'New lead detected (dry run)' : 'Lead claimed',
      message: record.dryRun
        ? `Would have claimed: ${record.label || 'new lead'} — tap it yourself`
        : `${record.label || 'Lead'} claimed at ${when} (${record.elapsedMs}ms)`,
      contextMessage: record.url ? new URL(record.url).hostname : 'carnow.com',
      priority: 2,
      requireInteraction: false
    });
    if (typeof tabId === 'number') notificationTargets.set(id, tabId);
    // Chrome keeps toasts in the tray indefinitely otherwise.
    setTimeout(() => {
      chrome.notifications.clear(id).catch(() => {});
      notificationTargets.delete(id);
    }, 15000);
  } catch (err) {
    console.warn('[CarNow AC / sw] notification failed', err);
  }
}

chrome.notifications.onClicked.addListener(async (id) => {
  const tabId = notificationTargets.get(id);
  if (typeof tabId !== 'number') return;
  try {
    const tab = await chrome.tabs.get(tabId);
    await chrome.windows.update(tab.windowId, { focused: true });
    await chrome.tabs.update(tabId, { active: true });
  } catch { /* tab closed */ }
  chrome.notifications.clear(id).catch(() => {});
});

/* -------------------------------------------------------------------- */
/* Phone push via ntfy                                                  */
/* -------------------------------------------------------------------- */

async function sendPhonePush({ title, message, priority = 'default', tags = 'white_check_mark', actions = '', force = false }) {
  const { phonePushEnabled } = await getSettings();
  if (!phonePushEnabled && !force) return { ok: false, skipped: 'disabled' };

  const { ntfyTopic = '' } = await chrome.storage.local.get({ ntfyTopic: '' });
  if (!ntfyTopic) return { ok: false, skipped: 'no-topic' };

  try {
    const response = await fetch('https://ntfy.sh/' + encodeURIComponent(ntfyTopic), {
      method: 'POST',
      headers: {
        'Title': title,
        'Priority': priority,
        'Tags': tags,
        ...(actions ? { 'Actions': actions } : {})
      },
      body: message
    });
    if (!response.ok) throw new Error('ntfy HTTP ' + response.status);
    await debugLog('phone push sent', title);
    return { ok: true };
  } catch (err) {
    console.warn('[CarNow AC / sw] phone push failed', err);
    return { ok: false, error: err && err.message ? err.message : String(err) };
  }
}

async function sendConfirmedClaimPush(payload) {
  const sourceRaw = payload && payload.source ? String(payload.source) : 'CarNow';
  const source = sourceRaw === 'live-help'
    ? 'Live Help Needed'
    : sourceRaw.includes('observer') || sourceRaw.includes('poll') || sourceRaw.includes('alarm')
      ? 'Conversations'
      : sourceRaw;
  const when = new Date(payload && payload.ts ? payload.ts : Date.now()).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit'
  });

  // Intentionally excludes customer names/phone numbers.
  return sendPhonePush({
    title: 'CarNow lead claimed ✅',
    message: 'Confirmed as yours • ' + source + ' • ' + when,
    priority: 'high',
    tags: 'white_check_mark,car'
  });
}

/* -------------------------------------------------------------------- */
/* Phone remote control via a second private ntfy topic                 */
/* -------------------------------------------------------------------- */

const REMOTE_COMMANDS = Object.freeze({
  CARNOW_ON: { autoClaim: true, scheduleEnabled: false, dryRun: false, label: 'LIVE MANUAL' },
  CARNOW_SCHEDULE: { autoClaim: true, scheduleEnabled: true, dryRun: false, label: 'FOLLOW SCHEDULE' },
  CARNOW_OFF: { autoClaim: false, label: 'OFF' }
});

function randomTopic(prefix) {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  let token = '';
  for (const b of bytes) token += b.toString(16).padStart(2, '0');
  return prefix + '-' + token;
}

async function ensureControlTopic() {
  let { ntfyControlTopic = '' } = await chrome.storage.local.get({ ntfyControlTopic: '' });
  if (!ntfyControlTopic) {
    ntfyControlTopic = randomTopic('carnow-control');
    await chrome.storage.local.set({ ntfyControlTopic });
  }
  return ntfyControlTopic;
}

async function applyRemoteCommand(command, eventId) {
  const spec = REMOTE_COMMANDS[command];
  if (!spec) return false;

  const patch = { autoClaim: spec.autoClaim };
  if ('scheduleEnabled' in spec) patch.scheduleEnabled = spec.scheduleEnabled;
  if ('dryRun' in spec) patch.dryRun = spec.dryRun;
  await chrome.storage.sync.set(patch);

  const { processedRemoteIds = [] } = await chrome.storage.local.get({ processedRemoteIds: [] });
  if (eventId && !processedRemoteIds.includes(eventId)) {
    processedRemoteIds.push(eventId);
    await chrome.storage.local.set({ processedRemoteIds: processedRemoteIds.slice(-50) });
  }

  await sendPhonePush({
    title: 'CarNow remote applied',
    message: spec.label + ' • command received by work PC',
    priority: 'high',
    tags: spec.autoClaim ? 'white_check_mark,computer' : 'stop_sign,computer',
    force: true
  });
  await debugLog('remote command applied', command, patch);
  return true;
}

async function pollRemoteCommands() {
  const { phoneRemoteEnabled } = await getSettings();
  if (!phoneRemoteEnabled) return;

  const topic = await ensureControlTopic();
  const { processedRemoteIds = [] } = await chrome.storage.local.get({ processedRemoteIds: [] });
  const seenIds = new Set(processedRemoteIds);

  try {
    const response = await fetch(
      'https://ntfy.sh/' + encodeURIComponent(topic) + '/json?poll=1&since=all',
      { cache: 'no-store' }
    );
    if (!response.ok) throw new Error('ntfy control HTTP ' + response.status);
    const text = await response.text();
    const events = text.split(/\r?\n/).filter(Boolean).map((line) => {
      try { return JSON.parse(line); } catch { return null; }
    }).filter(Boolean);

    for (const event of events) {
      if (event.event !== 'message' || !event.id || seenIds.has(event.id)) continue;
      const command = String(event.message || '').trim().toUpperCase();
      if (!REMOTE_COMMANDS[command]) continue;
      await applyRemoteCommand(command, event.id);
      seenIds.add(event.id);
    }
  } catch (err) {
    await debugLog('remote poll failed', err && err.message ? err.message : err);
  }
}

async function sendRemoteControlPanel() {
  const { phoneRemoteEnabled } = await getSettings();
  if (!phoneRemoteEnabled) return { ok: false, skipped: 'disabled' };

  const topic = await ensureControlTopic();
  const makeUrl = (command) =>
    'https://ntfy.sh/' + encodeURIComponent(topic) +
    '/publish?message=' + encodeURIComponent(command) +
    '&title=' + encodeURIComponent('CarNow Remote') +
    '&tags=' + encodeURIComponent('computer');

  const actions = [
    'http, ON NOW, ' + makeUrl('CARNOW_ON') + ', method=GET',
    'http, SCHEDULE, ' + makeUrl('CARNOW_SCHEDULE') + ', method=GET',
    'http, OFF, ' + makeUrl('CARNOW_OFF') + ', method=GET'
  ].join('; ');

  return sendPhonePush({
    title: 'CarNow remote controls',
    message: 'Control your work-PC claimer from your phone. Commands are picked up within about 30 seconds.',
    priority: 'default',
    tags: 'computer,iphone',
    actions,
    force: true
  });
}

/* -------------------------------------------------------------------- */
/* Keep-alive                                                           */
/* -------------------------------------------------------------------- */

/*
 * Two mechanisms, because neither is sufficient alone:
 *   1. chrome.alarms fires even after the worker has been evicted, which
 *      restarts it. This is the only officially supported wake-up.
 *   2. Long-lived ports from content.js reset the 30s idle timer while a
 *      CarNow tab is open, so the worker stays resident between alarms.
 */

const livePorts = new Set();

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'carnow-keepalive') return;
  livePorts.add(port);
  port.onDisconnect.addListener(() => {
    livePorts.delete(port);
    void chrome.runtime.lastError; // reading it suppresses the console noise
  });
  port.onMessage.addListener(() => {
    try { port.postMessage({ type: 'PONG', ts: Date.now() }); } catch { /* closed */ }
  });
});

async function ensureAlarm() {
  const existing = await chrome.alarms.get(KEEPALIVE_ALARM);
  if (!existing) {
    await chrome.alarms.create(KEEPALIVE_ALARM, {
      periodInMinutes: ALARM_PERIOD_MIN,
      delayInMinutes: ALARM_PERIOD_MIN
    });
  }
}

/** Nudges every CarNow tab to re-scan, bypassing throttled page timers. */
async function pushSweep() {
  const { autoClaim } = await getSettings();
  if (!autoClaim) return;
  let tabs = [];
  try { tabs = await chrome.tabs.query(CARNOW_FILTER); } catch { return; }
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;
    chrome.tabs.sendMessage(tab.id, { type: 'SWEEP' })
      .catch(() => { /* no content script in this tab yet */ });
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== KEEPALIVE_ALARM) return;
  await debugLog('alarm tick; ports=' + livePorts.size);
  for (const port of livePorts) {
    try { port.postMessage({ type: 'HEARTBEAT', ts: Date.now() }); }
    catch { livePorts.delete(port); }
  }
  await pushSweep();
  await pollRemoteCommands();
});

/* -------------------------------------------------------------------- */
/* Messages                                                             */
/* -------------------------------------------------------------------- */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== 'object') return false;

  if (message.type === 'LEAD_CLAIMED') {
    const record = message.payload || {};
    if (!record.ts) record.ts = Date.now();
    record.tabId = sender.tab && sender.tab.id;

    (async () => {
      const history = await appendClaim(record);
      await refreshBadge(history);
      await notifyClaim(record, record.tabId);
      await debugLog('claim recorded', record);
      sendResponse({ ok: true, total: history.length });
    })();
    return true; // async sendResponse
  }

  if (message.type === 'CLAIM_VERIFIED') {
    const { signature, verified, claimedText, mine } = message.payload || {};
    (async () => {
      // Stamp the verdict onto the history row this claim created.
      writeChain = writeChain.then(async () => {
        const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
        const entry = claimHistory.find((e) => e.signature === signature);
        if (entry) {
          entry.verified = verified;
          await chrome.storage.local.set({ claimHistory });
        }
        return claimHistory;
      }).catch(() => []);
      await writeChain;

      if (verified) {
        await sendConfirmedClaimPush(message.payload || {});
      }

      // Silence is fine when it worked; a failure needs to be seen.
      if (!verified) {
        const why = !claimedText
          ? 'the detail page never showed "Claimed"'
          : mine === false
            ? 'it was claimed, but not under your name'
            : 'unknown';
        try {
          await chrome.notifications.create(`carnow-unverified-${Date.now()}`, {
            type: 'basic',
            iconUrl: chrome.runtime.getURL('icons/icon128.png'),
            title: 'Claim may not have registered',
            message: `Tapped the lead but ${why}. Check it manually.`,
            priority: 2,
            requireInteraction: true
          });
        } catch { /* notifications unavailable */ }
      }
      await debugLog('claim verification', message.payload);
      sendResponse({ ok: true });
    })();
    return true;
  }

  if (message.type === 'HEARTBEAT') {
    sendResponse({ ok: true, ts: Date.now() });
    return false;
  }

  if (message.type === 'TEST_NTFY') {
    (async () => {
      const result = await sendPhonePush({
        title: 'CarNow notifications are working ✅',
        message: 'Your phone will be notified after a lead is confirmed as yours.',
        priority: 'high',
        tags: 'white_check_mark,car'
      });
      sendResponse(result);
    })();
    return true;
  }

  if (message.type === 'SEND_REMOTE_PANEL') {
    (async () => {
      const result = await sendRemoteControlPanel();
      sendResponse(result);
    })();
    return true;
  }

  if (message.type === 'GET_STATUS') {
    (async () => {
      const [{ claimHistory = [] }, settings] = await Promise.all([
        chrome.storage.local.get({ claimHistory: [] }),
        getSettings()
      ]);
      sendResponse({ ok: true, settings, ports: livePorts.size, history: claimHistory });
    })();
    return true;
  }

  return false;
});

/* -------------------------------------------------------------------- */
/* Lifecycle                                                            */
/* -------------------------------------------------------------------- */

chrome.runtime.onInstalled.addListener(async (details) => {
  // Seed only the keys that are missing, so an update never resets choices.
  const stored = await chrome.storage.sync.get(null);
  const seed = {};
  for (const [key, value] of Object.entries(DEFAULTS)) {
    if (!(key in stored)) seed[key] = value;
  }
  if (Object.keys(seed).length) await chrome.storage.sync.set(seed);

  await ensureAlarm();
  await ensureControlTopic();
  const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
  await refreshBadge(claimHistory);
  console.log('[CarNow AC / sw] installed:', details.reason);
});

chrome.runtime.onStartup.addListener(async () => {
  await ensureAlarm();
  await ensureControlTopic();
  const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
  await refreshBadge(claimHistory);
});

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

// Runs on every worker start, including a restart after eviction.
void ensureAlarm();
void ensureControlTopic();
void pollRemoteCommands();
