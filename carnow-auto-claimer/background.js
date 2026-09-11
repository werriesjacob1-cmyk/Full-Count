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
  dryRun: true,
  soundAlert: true,
  debug: false,
  maxLeadAgeMin: 5,
  minClaimIntervalSec: 10,
  maxClaimsPerSession: 10
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

  if (message.type === 'HEARTBEAT') {
    sendResponse({ ok: true, ts: Date.now() });
    return false;
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
  const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
  await refreshBadge(claimHistory);
  console.log('[CarNow AC / sw] installed:', details.reason);
});

chrome.runtime.onStartup.addListener(async () => {
  await ensureAlarm();
  const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
  await refreshBadge(claimHistory);
});

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

// Runs on every worker start, including a restart after eviction.
void ensureAlarm();
