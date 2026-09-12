// FULL COUNT live heartbeat -- clocks only, never business logic.
//
// Every ~5 minutes, wake dashboard-live.yml. At seven exact NFL pre-lock
// timestamps on Sunday/Monday UTC, also wake the NFL shadow-board workflow.
// The target workflows remain responsible for all data, modeling, evidence,
// and publication work; this Worker only dispatches GitHub Actions.
//
// MLB behavior is intentionally unchanged: dashboard-live.yml is still
// dispatched on every Cloudflare cron tick. NFL dispatch is additive and
// bounded to the exact freeze schedule already declared in the NFL workflow.

const OWNER = 'werriesjacob1-cmyk';
const REPO = 'Full-Count';
const MLB_WORKFLOW_FILE = 'dashboard-live.yml';
const NFL_WORKFLOW_FILE = 'nfl-live-passing-yards-shadow-board.yml';
const REF = 'main';
const MLB_DISPATCH_URL =
  `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${MLB_WORKFLOW_FILE}/dispatches`;
const NFL_DISPATCH_URL =
  `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${NFL_WORKFLOW_FILE}/dispatches`;

// UTC day:hour:minute. Mirrors the NFL workflow schedule exactly.
// Sunday = 0, Monday = 1 in Date#getUTCDay().
const NFL_FREEZE_KEYS = new Set([
  '0:15:40',
  '0:16:50',
  '0:19:5',
  '0:19:55',
  '0:20:15',
  '0:23:0',
  '1:0:10',
]);

const RETRYABLE_STATUS = new Set([500, 502, 503, 504]);

function buildRequest(token, url) {
  if (!token) throw new Error('missing GITHUB_PAT');
  return new Request(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'fc-live-heartbeat-worker',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ref: REF }),
  });
}

export function buildDispatchRequest(token) {
  return buildRequest(token, MLB_DISPATCH_URL);
}

export function buildNflDispatchRequest(token) {
  return buildRequest(token, NFL_DISPATCH_URL);
}

export function isNflFreezeDue(scheduledTime) {
  const dt = new Date(Number(scheduledTime));
  if (!Number.isFinite(dt.getTime())) return false;
  const key = `${dt.getUTCDay()}:${dt.getUTCHours()}:${dt.getUTCMinutes()}`;
  return NFL_FREEZE_KEYS.has(key);
}

export function classifyResponse(response) {
  const status = response.status;
  if (status === 204) return { ok: true, status, retryable: false, reason: 'dispatched' };
  if (status === 401 || status === 403) return { ok: false, status, retryable: false, reason: 'auth_failure' };
  if (status === 404) return { ok: false, status, retryable: false, reason: 'not_found_or_no_access' };
  if (RETRYABLE_STATUS.has(status)) return { ok: false, status, retryable: true, reason: 'transient_server_error' };
  return { ok: false, status, retryable: false, reason: 'unexpected_status' };
}

async function dispatchWithBuilder(token, builder, fetchImpl = fetch) {
  for (let attempt = 1; attempt <= 2; attempt++) {
    let response;
    try {
      response = await fetchImpl(builder(token));
    } catch (err) {
      if (attempt === 1) continue;
      return { ok: false, status: null, retryable: false, reason: 'network_error', error: String(err) };
    }
    const outcome = classifyResponse(response);
    if (outcome.ok || !outcome.retryable || attempt === 2) return outcome;
  }
  return { ok: false, status: null, retryable: false, reason: 'unreachable' };
}

export async function dispatchWithOneRetry(token, fetchImpl = fetch) {
  return dispatchWithBuilder(token, buildDispatchRequest, fetchImpl);
}

export async function dispatchNflWithOneRetry(token, fetchImpl = fetch) {
  return dispatchWithBuilder(token, buildNflDispatchRequest, fetchImpl);
}

export default {
  async scheduled(event, env, ctx) {
    const startedAt = new Date().toISOString();
    if (!env.GITHUB_PAT) {
      console.error(JSON.stringify({ at: startedAt, ok: false, reason: 'missing_secret' }));
      return;
    }

    // Preserve the existing MLB heartbeat on every cron tick.
    const mlb = await dispatchWithOneRetry(env.GITHUB_PAT);

    // NFL is additive and only fires at the exact pre-registered freeze.
    const nflDue = isNflFreezeDue(event.scheduledTime);
    const nfl = nflDue
      ? await dispatchNflWithOneRetry(env.GITHUB_PAT)
      : null;

    console.log(JSON.stringify({
      at: startedAt,
      cron: event.cron,
      mlb,
      nfl_due: nflDue,
      nfl,
    }));

    if (!mlb.ok) {
      throw new Error(`MLB heartbeat dispatch failed: ${mlb.reason} (status ${mlb.status})`);
    }
    if (nfl && !nfl.ok) {
      throw new Error(`NFL shadow dispatch failed: ${nfl.reason} (status ${nfl.status})`);
    }
  },

  async fetch() {
    return new Response(
      'fc-live-heartbeat: cron-triggered GitHub Actions dispatcher. No public API.',
      { status: 200, headers: { 'Content-Type': 'text/plain' } },
    );
  },
};
