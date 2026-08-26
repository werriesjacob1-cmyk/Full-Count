// FULL COUNT live heartbeat -- a clock, nothing more.
//
// Every ~5 minutes, wake dashboard-live.yml (the sole writer of
// docs/live.json) via a GitHub Actions workflow_dispatch. That workflow
// remains 100% responsible for fetching MLB/FanDuel data, grading, pricing,
// and writing state -- this Worker never touches any of that, never talks
// to any LLM, and is not itself part of Full Count's predictive/live logic.
//
// WHY THIS EXISTS: GitHub's own `schedule` trigger for dashboard-live.yml
// is observed firing every 34-136 minutes in this repo (see
// P0_LIVE_LIFECYCLE_INCIDENT_2026-08-26.md), not its configured 5 minutes --
// a repo-wide GitHub-side scheduling throttle, not a bug in the workflow.
// An independent scheduler outside GitHub's own cron infrastructure is the
// fix; Cloudflare Cron Triggers are believed independent of that throttle
// (this is the hypothesis the soak test in the README is designed to
// confirm -- treat "independent" as unproven until that soak test passes).
//
// SAFETY: the target workflow already declares
//   concurrency: { group: dashboard-live-observation, cancel-in-progress: false }
// so GitHub itself guarantees at most one execution of dashboard-live.yml
// at a time, coalescing extra pending triggers -- this Worker firing
// alongside GitHub's own schedule or the watchdog's recovery dispatch
// cannot cause two runs to execute concurrently. See infra/live-heartbeat/
// README.md for the full argument and evidence.

const OWNER = 'werriesjacob1-cmyk';
const REPO = 'Full-Count';
const WORKFLOW_FILE = 'dashboard-live.yml';
const REF = 'main';
const DISPATCH_URL =
  `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;

const RETRYABLE_STATUS = new Set([500, 502, 503, 504]);

/**
 * Build the exact request this Worker sends to GitHub. Pure and
 * side-effect-free so it's directly testable without any network access.
 * @param {string} token
 * @returns {Request}
 */
export function buildDispatchRequest(token) {
  if (!token) {
    throw new Error('missing GITHUB_PAT');
  }
  return new Request(DISPATCH_URL, {
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

/**
 * Classify a GitHub response into a small, log-safe outcome. Never includes
 * the request itself (which carries the Authorization header) in anything
 * returned or logged.
 * @param {Response} response
 * @returns {{ok: boolean, status: number, retryable: boolean, reason: string}}
 */
export function classifyResponse(response) {
  const status = response.status;
  if (status === 204) {
    return { ok: true, status, retryable: false, reason: 'dispatched' };
  }
  if (status === 401 || status === 403) {
    return { ok: false, status, retryable: false, reason: 'auth_failure' };
  }
  if (status === 404) {
    return { ok: false, status, retryable: false, reason: 'not_found_or_no_access' };
  }
  if (RETRYABLE_STATUS.has(status)) {
    return { ok: false, status, retryable: true, reason: 'transient_server_error' };
  }
  return { ok: false, status, retryable: false, reason: 'unexpected_status' };
}

/**
 * Dispatch dashboard-live.yml once, with one small retry on a transient
 * 5xx/network error (not on 401/403/404 -- those are configuration
 * problems a retry cannot fix). The next 5-minute cron tick is itself the
 * real backstop; this is not a general-purpose retry engine.
 * @param {string} token
 * @param {typeof fetch} fetchImpl injected for testability
 */
export async function dispatchWithOneRetry(token, fetchImpl = fetch) {
  for (let attempt = 1; attempt <= 2; attempt++) {
    let response;
    try {
      response = await fetchImpl(buildDispatchRequest(token));
    } catch (err) {
      // Network-level failure (DNS, connection reset, etc.) -- no status
      // code to classify. Retry once, then surface it.
      if (attempt === 1) continue;
      return { ok: false, status: null, retryable: false, reason: 'network_error', error: String(err) };
    }
    const outcome = classifyResponse(response);
    if (outcome.ok || !outcome.retryable || attempt === 2) {
      return outcome;
    }
    // one retryable attempt remains
  }
  // unreachable, but keeps the type checker happy
  return { ok: false, status: null, retryable: false, reason: 'unreachable' };
}

export default {
  async scheduled(event, env, ctx) {
    const startedAt = new Date().toISOString();
    if (!env.GITHUB_PAT) {
      // Fail visibly. Never throw a value that could carry the secret --
      // there is none to carry here since env.GITHUB_PAT is never
      // interpolated into this message.
      console.error(JSON.stringify({ at: startedAt, ok: false, reason: 'missing_secret' }));
      return;
    }
    const outcome = await dispatchWithOneRetry(env.GITHUB_PAT);
    // Log a small, secret-free structured line. This is the only production
    // observability this Worker has -- Cloudflare's dashboard/logs surface
    // it, no external logging destination is wired.
    console.log(JSON.stringify({ at: startedAt, cron: event.cron, ...outcome }));
    if (!outcome.ok) {
      // Visible failure per the contract -- Cloudflare surfaces a thrown
      // error from scheduled() as a failed invocation.
      throw new Error(`heartbeat dispatch failed: ${outcome.reason} (status ${outcome.status})`);
    }
  },

  // No production HTTP surface. This exists only so `wrangler dev` and a
  // manual browser hit have something safe to see -- it never touches
  // GitHub, never reads the secret, and returns no baseball data.
  async fetch() {
    return new Response(
      'fc-live-heartbeat: cron-triggered GitHub Actions dispatcher. No public API.',
      { status: 200, headers: { 'Content-Type': 'text/plain' } },
    );
  },
};
