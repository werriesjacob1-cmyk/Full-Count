/* CarNow Auto-Claimer — background health monitor / self-healing */

const HEALTH_STALE_MS = 20000;
const RELOAD_COOLDOWN_MS = 2 * 60 * 1000;
const HEALTH_PREFIX = 'carnowHealthTab:';
const RELOAD_PREFIX = 'carnowHealthReload:';
const healthByTab = new Map();
const lastReloadByTab = new Map();

async function appendHealthDiagnostic(type, detail = {}) {
  try {
    const key = 'carnowDiagnosticLog';
    const stored = await chrome.storage.local.get({ [key]: [] });
    const log = [...(stored[key] || []), { ts: Date.now(), type, build: '1.6.1', ...detail }].slice(-500);
    await chrome.storage.local.set({ [key]: log });
  } catch { /* storage unavailable */ }
}

async function restoreSessionHealth() {
  try {
    const all = await chrome.storage.session.get(null);
    for (const [key, value] of Object.entries(all)) {
      if (key.startsWith(HEALTH_PREFIX)) {
        const tabId = Number(key.slice(HEALTH_PREFIX.length));
        if (Number.isFinite(tabId) && value) healthByTab.set(tabId, value);
      } else if (key.startsWith(RELOAD_PREFIX)) {
        const tabId = Number(key.slice(RELOAD_PREFIX.length));
        if (Number.isFinite(tabId)) lastReloadByTab.set(tabId, Number(value || 0));
      }
    }
  } catch { /* session storage unavailable */ }
}

function persistTabHealth(tabId, entry) {
  chrome.storage.session.set({ [HEALTH_PREFIX + tabId]: entry }).catch(() => {});
}

function persistReload(tabId, ts) {
  chrome.storage.session.set({ [RELOAD_PREFIX + tabId]: ts }).catch(() => {});
}

function healthVerdict(entry, now = Date.now()) {
  if (!entry) return 'DEAD';
  if (now - entry.lastSeen > HEALTH_STALE_MS) return 'DEAD';
  const p = entry.payload || {};
  if (!p.ready) return 'DEGRADED';
  if (p.shouldWatch && (!p.armed || p.online === false)) return 'DEGRADED';
  if (p.claimInFlight && !p.onClaimDetailPage && now - (entry.inFlightSince || now) > 10000) return 'DEGRADED';
  return 'HEALTHY';
}

async function maybeRecoverTab(tabId, entry) {
  if (!entry || !entry.payload || !entry.payload.shouldWatch) return;
  if (entry.payload.claimInFlight || entry.payload.onClaimDetailPage) return;

  const now = Date.now();
  if (now - entry.lastSeen <= HEALTH_STALE_MS) return;
  const lastReload = Number(lastReloadByTab.get(tabId) || 0);
  if (now - lastReload < RELOAD_COOLDOWN_MS) return;

  lastReloadByTab.set(tabId, now);
  persistReload(tabId, now);
  await appendHealthDiagnostic('self-heal-reload', {
    tabId,
    staleMs: now - entry.lastSeen,
    lastUrl: entry.payload.url || null,
    visibility: entry.payload.visibility || null
  });

  try {
    await chrome.tabs.reload(tabId, { bypassCache: true });
  } catch (err) {
    await appendHealthDiagnostic('self-heal-reload-failed', {
      tabId,
      error: err && err.message ? err.message : String(err)
    });
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== 'object') return false;

  if (message.type === 'HEALTH_PING') {
    const tabId = sender.tab && sender.tab.id;
    if (typeof tabId !== 'number') {
      sendResponse({ ok: false });
      return false;
    }

    const payload = message.payload || {};
    const previous = healthByTab.get(tabId);
    const was = healthVerdict(previous);
    const inFlightSince = payload.claimInFlight
      ? (previous && previous.inFlightSince ? previous.inFlightSince : Date.now())
      : 0;

    const entry = { lastSeen: Date.now(), payload, inFlightSince };
    healthByTab.set(tabId, entry);
    persistTabHealth(tabId, entry);
    const nowVerdict = healthVerdict(entry);

    if (was !== nowVerdict) {
      void appendHealthDiagnostic('health-transition', {
        tabId,
        from: was,
        to: nowVerdict,
        url: payload.url || null,
        visibility: payload.visibility || null,
        armed: Boolean(payload.armed),
        claimInFlight: Boolean(payload.claimInFlight)
      });
    }

    sendResponse({ ok: true, health: nowVerdict, ts: Date.now() });
    return false;
  }

  if (message.type === 'GET_HEALTH') {
    const now = Date.now();
    const tabs = [];
    for (const [tabId, entry] of healthByTab.entries()) {
      tabs.push({
        tabId,
        health: healthVerdict(entry, now),
        ageMs: now - entry.lastSeen,
        payload: entry.payload
      });
    }
    const watching = tabs.filter((t) => t.payload && t.payload.shouldWatch);
    const overall = !watching.length
      ? 'IDLE'
      : watching.some((t) => t.health === 'DEAD')
        ? 'DEAD'
        : watching.some((t) => t.health === 'DEGRADED')
          ? 'DEGRADED'
          : 'HEALTHY';
    sendResponse({ ok: true, overall, tabs, ts: now });
    return false;
  }

  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  healthByTab.delete(tabId);
  lastReloadByTab.delete(tabId);
  chrome.storage.session.remove([HEALTH_PREFIX + tabId, RELOAD_PREFIX + tabId]).catch(() => {});
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (!alarm || alarm.name !== 'carnow-keepalive') return;
  const now = Date.now();

  for (const [tabId, entry] of healthByTab.entries()) {
    if (now - entry.lastSeen > 10 * 60 * 1000) {
      healthByTab.delete(tabId);
      lastReloadByTab.delete(tabId);
      chrome.storage.session.remove([HEALTH_PREFIX + tabId, RELOAD_PREFIX + tabId]).catch(() => {});
      continue;
    }
    await maybeRecoverTab(tabId, entry);
  }
});

void restoreSessionHealth();
