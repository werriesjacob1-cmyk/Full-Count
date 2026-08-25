/**
 * worker.js -- Priority 3 external freshness-recovery heartbeat.
 *
 * Built 2026-08-25 after direct, live evidence that a GitHub-schedule-based
 * watchdog (live-freshness-watchdog.yml) shares its protected workflow's
 * (dashboard-live.yml) failure domain: both went silent on their own
 * 5-minute crons simultaneously (see
 * backtest/live_incident_2026-08-25_0329.md), and a manual dispatch of the
 * protected workflow did NOT restore either recurring schedule. This is
 * Option A from independent_recovery_design_2026-08-25.md: an external
 * heartbeat, on infrastructure with NO dependency on GitHub Actions'
 * scheduled-trigger delivery, that checks freshness and dispatches recovery
 * via the same `workflow_dispatch` path already proven reliable (every
 * manual dispatch this session ran within seconds, including under the
 * exact conditions the scheduled trigger was failing).
 *
 * WHAT THIS DOES, exactly, per the standing instruction's own 6-point scope
 * ("the external component should do only..."):
 *   1. Read the public, authoritative freshness signal (docs/live.json's
 *      own `updated_at`, served by GitHub Pages -- no auth needed to read).
 *   2. Determine whether it exceeds the stale SLA (matches
 *      dashboard/check_live_freshness.py's own 15-minute threshold exactly
 *      -- not a new number invented here).
 *   3. Fresh -> no-op, log and return.
 *   4. Stale -> authenticated workflow_dispatch of dashboard-live.yml (the
 *      existing canonical recovery workflow -- NOT a new writer).
 *   5. Cooldown: never dispatch twice within COOLDOWN_MS of the last
 *      dispatch this Worker itself made (tracked in Workers KV -- durable
 *      across invocations, unlike in-memory state which a Worker cannot
 *      rely on between cron ticks).
 *   6. Records what it did: every tick's outcome (fresh/stale/dispatched/
 *      skipped-cooldown/error) is written to KV under a rolling key, and
 *      logged via console.log (visible in `wrangler tail` / the Cloudflare
 *      dashboard's real-time logs).
 *
 * WHAT THIS DELIBERATELY DOES NOT DO (explicit standing constraints):
 *   - Never writes docs/live.json itself -- no second live writer, no
 *     duplicate-writer race with dashboard-live.yml's own
 *     merge_live_files.py logic.
 *   - Never touches any GitHub Actions SCHEDULE trigger -- only ever calls
 *     the workflow_dispatch REST endpoint, the one proven-reliable path.
 *   - Never generates/regenerates any board, price, or grade data.
 *
 * DRY_RUN mode (an env var, see wrangler.toml): when "true", a would-be
 * dispatch is logged and recorded but the actual GitHub API call is
 * skipped -- lets this be deployed and observed safely before ever really
 * triggering a recovery, and is also how the pure logic below is exercised
 * by the local test harness (see test_worker.mjs) without any network
 * calls or real credentials at all.
 */

// Matches dashboard/check_live_freshness.py's own SLA exactly -- not a new
// threshold invented for this Worker.
export const STALE_THRESHOLD_MS = 15 * 60 * 1000;
// Never dispatch more than once per this window, even if multiple stale
// ticks occur in a row before dashboard-live.yml's own commit lands and
// updated_at moves forward again -- avoids a dispatch storm if the
// recovery run itself is slow or the underlying cause hasn't cleared yet.
export const COOLDOWN_MS = 20 * 60 * 1000;

export const LIVE_JSON_URL = "https://werriesjacob1-cmyk.github.io/Full-Count/live.json";
const GITHUB_OWNER = "werriesjacob1-cmyk";
const GITHUB_REPO = "Full-Count";
const RECOVERY_WORKFLOW = "dashboard-live.yml";

/**
 * Pure decision function -- no I/O, no fetch, no KV. Exported so the local
 * test harness can prove every branch with synthetic inputs, matching this
 * project's own "sign-reversal" testing discipline used throughout the
 * Python side of this session (assert the OLD/wrong behavior fails the
 * test, the fix passes it).
 *
 * @param {number} nowMs - current time, epoch ms
 * @param {string|null} updatedAtIso - docs/live.json's own `updated_at`, or
 *   null if the fetch itself failed (never a naive default value)
 * @param {number|null} lastDispatchMs - epoch ms of this Worker's own last
 *   real dispatch, or null if it has never dispatched (from KV)
 * @returns {{action: "no_op_fresh"|"no_op_fetch_failed"|"dispatch"|"no_op_cooldown",
 *            ageMs: number|null, reason: string}}
 */
export function decide(nowMs, updatedAtIso, lastDispatchMs) {
  if (!updatedAtIso) {
    return { action: "no_op_fetch_failed", ageMs: null,
             reason: "could not read docs/live.json's own updated_at -- " +
                     "GitHub Pages/API itself may be unreachable; do nothing " +
                     "rather than dispatch on an assumption." };
  }
  const updatedAtMs = Date.parse(updatedAtIso);
  if (Number.isNaN(updatedAtMs)) {
    return { action: "no_op_fetch_failed", ageMs: null,
             reason: `updated_at was not a parseable timestamp: ${JSON.stringify(updatedAtIso)}` };
  }
  const ageMs = nowMs - updatedAtMs;
  if (ageMs < STALE_THRESHOLD_MS) {
    return { action: "no_op_fresh", ageMs,
             reason: `${Math.round(ageMs / 1000)}s old, under the ${STALE_THRESHOLD_MS / 1000}s SLA` };
  }
  if (lastDispatchMs !== null && (nowMs - lastDispatchMs) < COOLDOWN_MS) {
    return { action: "no_op_cooldown", ageMs,
             reason: `stale (${Math.round(ageMs / 1000)}s old) but a recovery dispatch ` +
                     `already fired ${Math.round((nowMs - lastDispatchMs) / 1000)}s ago -- ` +
                     `within the ${COOLDOWN_MS / 1000}s cooldown, not dispatching again yet.` };
  }
  return { action: "dispatch", ageMs,
           reason: `stale: ${Math.round(ageMs / 1000)}s old, exceeds the ${STALE_THRESHOLD_MS / 1000}s SLA.` };
}

async function fetchUpdatedAt(env) {
  try {
    const resp = await fetch(LIVE_JSON_URL, { cf: { cacheTtl: 0 } });
    if (!resp.ok) return null;
    const data = await resp.json();
    return typeof data.updated_at === "string" ? data.updated_at : null;
  } catch (err) {
    console.log(`fetchUpdatedAt failed: ${err}`);
    return null;
  }
}

async function dispatchRecovery(env) {
  // Least-privilege by construction: GITHUB_TOKEN is a Worker secret
  // (never in source, never logged), scoped to exactly this one repo's
  // Actions:write permission -- see README.md's deployment steps for how
  // it must be created. This calls ONLY the workflow_dispatch endpoint for
  // ONLY dashboard-live.yml; it has no code path that could touch any
  // other workflow or repo even if the token were scoped more broadly than
  // intended.
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
              `/actions/workflows/${RECOVERY_WORKFLOW}/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "fullcount-external-heartbeat",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  return { status: resp.status, ok: resp.ok };
}

async function recordOutcome(env, outcome) {
  // A short rolling history in KV -- "record what it did" (standing
  // requirement #6). Not a metrics platform; just enough for a human to
  // open the KV namespace and see the last N ticks' decisions.
  if (!env.HEARTBEAT_KV) return;
  const key = `tick:${new Date().toISOString()}`;
  await env.HEARTBEAT_KV.put(key, JSON.stringify(outcome), { expirationTtl: 7 * 24 * 3600 });
}

export default {
  async scheduled(event, env, ctx) {
    const nowMs = Date.now();
    const updatedAtIso = await fetchUpdatedAt(env);
    const lastDispatchRaw = env.HEARTBEAT_KV ? await env.HEARTBEAT_KV.get("last_dispatch_ms") : null;
    const lastDispatchMs = lastDispatchRaw ? parseInt(lastDispatchRaw, 10) : null;

    const decision = decide(nowMs, updatedAtIso, lastDispatchMs);
    const dryRun = env.DRY_RUN === "true";
    let dispatchResult = null;

    if (decision.action === "dispatch") {
      if (dryRun) {
        console.log(`DRY_RUN: would dispatch ${RECOVERY_WORKFLOW} -- ${decision.reason}`);
      } else {
        dispatchResult = await dispatchRecovery(env);
        if (dispatchResult.ok && env.HEARTBEAT_KV) {
          await env.HEARTBEAT_KV.put("last_dispatch_ms", String(nowMs));
        }
        console.log(`Dispatched ${RECOVERY_WORKFLOW}: ${JSON.stringify(dispatchResult)} -- ${decision.reason}`);
      }
    } else {
      console.log(`${decision.action}: ${decision.reason}`);
    }

    await recordOutcome(env, { ...decision, nowMs, updatedAtIso, dryRun, dispatchResult });
  },

  // Manual HTTP trigger for on-demand verification (e.g. `curl` the
  // deployed Worker's URL) -- runs the identical logic as the cron tick,
  // useful for the deployment verification procedure in README.md without
  // waiting for the next scheduled tick.
  async fetch(request, env, ctx) {
    await this.scheduled(null, env, ctx);
    return new Response("heartbeat tick complete -- see wrangler tail for the decision\n");
  },
};
