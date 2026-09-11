/* =====================================================================
 * CarNow Lead Auto-Claimer — options page
 * Toggles persist to chrome.storage.sync; history is read from local.
 * ===================================================================== */

'use strict';

const DEFAULTS = Object.freeze({
  autoClaim: true,
  soundAlert: true,
  debug: false
});

const TOGGLES = Object.keys(DEFAULTS);
const $ = (id) => document.getElementById(id);

/* -------------------------------------------------------------------- */
/* Settings                                                             */
/* -------------------------------------------------------------------- */

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  for (const key of TOGGLES) {
    $(key).checked = Boolean(stored[key]);
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
      flashSaved();
    });
  }

  // Keep the UI honest if another options tab or device changes a value.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'sync') {
      for (const key of TOGGLES) {
        if (key in changes) $(key).checked = Boolean(changes[key].newValue);
      }
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
    source.textContent = entry.source || '—';

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
})();
