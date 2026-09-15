/* =====================================================================
 * CarNow Lead Auto-Claimer — options page
 * Toggles persist to chrome.storage.sync; history is read from local.
 * ===================================================================== */

'use strict';

const DEFAULTS = Object.freeze({
  autoClaim: true,
  scheduleEnabled: true,
  dryRun: true,
  soundAlert: true,
  debug: false,
  maxLeadAgeMin: 5,
  minClaimIntervalSec: 10,
  maxClaimsPerSession: 200,
  returnToList: true,
  returnDelaySec: 5,
  myName: ''
});

const TOGGLES = ['autoClaim', 'scheduleEnabled', 'dryRun', 'soundAlert', 'debug', 'returnToList'];
const NUMBERS = ['maxLeadAgeMin', 'minClaimIntervalSec', 'maxClaimsPerSession', 'returnDelaySec'];
const TEXTS = ['myName'];
const $ = (id) => document.getElementById(id);

const SCHEDULE_TZ = 'America/Chicago';
const WORK_SHIFTS = Object.freeze({
  mon: [11 * 60, 20 * 60],
  tue: [11 * 60, 20 * 60],
  wed: null,
  thu: [9 * 60 + 45, 17 * 60],
  fri: [9 * 60 + 45, 17 * 60],
  sat: [8 * 60 + 30, 19 * 60],
  sun: null
});

function scheduleActive(now = new Date()) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: SCHEDULE_TZ,
      hour12: false,
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit'
    }).formatToParts(now);
    const hour = parseInt(parts.find((p) => p.type === 'hour')?.value || '0', 10);
    const minute = parseInt(parts.find((p) => p.type === 'minute')?.value || '0', 10);
    const weekday = (parts.find((p) => p.type === 'weekday')?.value || '').toLowerCase();
    const shift = WORK_SHIFTS[weekday];
    if (!shift) return true;
    const minuteOfDay = hour * 60 + minute;
    return minuteOfDay < shift[0] || minuteOfDay >= shift[1];
  } catch {
    const keys = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
    const shift = WORK_SHIFTS[keys[now.getDay()]];
    if (!shift) return true;
    const minuteOfDay = now.getHours() * 60 + now.getMinutes();
    return minuteOfDay < shift[0] || minuteOfDay >= shift[1];
  }
}

/* -------------------------------------------------------------------- */
/* Settings                                                             */
/* -------------------------------------------------------------------- */

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  for (const key of TOGGLES) $(key).checked = Boolean(stored[key]);
  for (const key of NUMBERS) $(key).value = stored[key];
  for (const key of TEXTS) $(key).value = stored[key] || '';
  reflectArmState();
}

/** The banner has to make the current mode impossible to misread. */
function reflectArmState() {
  const enabled = $('autoClaim').checked;
  const followSchedule = $('scheduleEnabled').checked;
  const dry = $('dryRun').checked;
  const scheduledNow = scheduleActive();
  const banner = $('armState');

  if (!enabled) {
    banner.textContent = 'OFF — master switch is off';
    banner.className = 'banner idle';
  } else if (dry) {
    banner.textContent = 'DRY RUN — new leads are detected and announced, never clicked';
    banner.className = 'banner dry';
  } else if (!followSchedule) {
    banner.textContent = 'LIVE MANUAL — schedule override is OFF; auto-claim runs continuously';
    banner.className = 'banner live';
  } else if (scheduledNow) {
    banner.textContent = 'LIVE — outside scheduled work hours';
    banner.className = 'banner live';
  } else {
    banner.textContent = 'SCHEDULED STANDBY — paused during your work shift';
    banner.className = 'banner idle';
  }
}

let savedTimer = null;
function flashSaved() {
  const badge = $('saved');
  badge.classList.add('show');
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => badge.classList.remove('show'), 1200);
}

function wireToggles() {
  for (const key of TOGGLES) {
    $(key).addEventListener('change', async (event) => {
      await chrome.storage.sync.set({ [key]: event.target.checked });
      reflectArmState();
      flashSaved();
    });
  }

  for (const key of TEXTS) {
    $(key).addEventListener('change', async (event) => {
      await chrome.storage.sync.set({ [key]: event.target.value.trim() });
      flashSaved();
    });
  }

  for (const key of NUMBERS) {
    $(key).addEventListener('change', async (event) => {
      const min = Number(event.target.min);
      const value = Math.max(min, Number(event.target.value) || min);
      event.target.value = value;
      await chrome.storage.sync.set({ [key]: value });
      flashSaved();
    });
  }

  // Keep the UI honest if another options tab or device changes a value.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'sync') {
      for (const key of TOGGLES) {
        if (key in changes) $(key).checked = Boolean(changes[key].newValue);
      }
      for (const key of NUMBERS) {
        if (key in changes) $(key).value = changes[key].newValue;
      }
      reflectArmState();
    } else if (area === 'local' && 'claimHistory' in changes) {
      render(changes.claimHistory.newValue || []);
    }
  });
}

/* -------------------------------------------------------------------- */
/* Status + history                                                     */
/* -------------------------------------------------------------------- */

const dayKey = (ts) => new Date(ts).toISOString().slice(0, 10);

function relativeTime(ts) {
  const seconds = Math.round((Date.now() - ts) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return new Date(ts).toLocaleDateString();
}

function render(history, ports) {
  const today = dayKey(Date.now());
  const todayCount = history.filter((entry) => dayKey(entry.ts) === today).length;

  $('statToday').textContent = todayCount;
  $('statTotal').textContent = history.length;
  if (ports !== undefined) $('statPorts').textContent = ports;
  $('statLast').textContent = history.length ? relativeTime(history[0].ts) : '—';

  const body = $('historyBody');
  body.replaceChildren();

  if (!history.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.className = 'empty';
    cell.textContent = 'No claims recorded yet.';
    row.append(cell);
    body.append(row);
    return;
  }

  for (const entry of history.slice(0, 50)) {
    const row = document.createElement('tr');

    const time = document.createElement('td');
    time.className = 'mono';
    time.textContent = new Date(entry.ts).toLocaleTimeString();

    const label = document.createElement('td');
    label.className = 'label';
    label.textContent = entry.label || '—';
    label.title = entry.signature || '';

    const source = document.createElement('td');
    source.textContent = entry.dryRun ? 'dry run'
      : entry.verified === true ? 'confirmed'
      : entry.verified === false ? 'UNVERIFIED'
      : (entry.source || 'claimed');
    if (entry.dryRun) source.style.color = '#94a3b8';
    if (entry.verified === true) source.style.color = '#16a34a';
    if (entry.verified === false) source.style.color = '#dc2626';

    const speed = document.createElement('td');
    speed.className = 'mono';
    speed.textContent = entry.elapsedMs !== undefined ? `${entry.elapsedMs} ms` : '—';

    row.append(time, label, source, speed);
    body.append(row);
  }
}

async function refresh() {
  let ports;
  try {
    const status = await chrome.runtime.sendMessage({ type: 'GET_STATUS' });
    if (status && status.ok) {
      render(status.history || [], status.ports);
      return;
    }
  } catch {
    ports = 0; // service worker asleep or restarting
  }
  const { claimHistory = [] } = await chrome.storage.local.get({ claimHistory: [] });
  render(claimHistory, ports);
}

/* -------------------------------------------------------------------- */
/* Boot                                                                 */
/* -------------------------------------------------------------------- */

$('refresh').addEventListener('click', refresh);

$('clear').addEventListener('click', async () => {
  if (!confirm('Clear the claim history? Settings are not affected.')) return;
  await chrome.storage.local.set({ claimHistory: [] });
  try { await chrome.action.setBadgeText({ text: '' }); } catch { /* not available here */ }
  await refresh();
  flashSaved();
});

(async () => {
  await loadSettings();
  wireToggles();
  await refresh();
  setInterval(reflectArmState, 30 * 1000);
})();
