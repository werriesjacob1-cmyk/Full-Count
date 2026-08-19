/**
 * Full Count independent scheduling watchdog.
 *
 * WHY THIS EXISTS. Measured real gaps on 2026-08-18 for
 * .github/workflows/dashboard-live.yml's declared `star-slash-5 * * * *` cron: 27
 * real observed gaps that day, median 30.3min / p90 46.8min / p95 50.8min /
 * max 57.9min, with 27/27 exceeding 10 minutes and 26/27 exceeding 20
 * minutes. GitHub's own cron scheduling alone cannot be trusted to fire the
 * live-update workflow anywhere near every 5 minutes -- see the Chase
 * Meidroth incident investigation this was built in response to. A second
 * GitHub Actions workflow watching the first would share the exact same
 * failure domain (GitHub's own scheduler) and prove nothing.
 *
 * This Worker is a genuinely independent trigger: a different platform, a
 * different scheduler, a different failure domain, calling GitHub's REST
 * workflow_dispatch endpoint directly. It does not replace dashboard-
 * live.yml's own `star-slash-5` cron -- it is a second, independent path to the same
 * dispatch, so GitHub's scheduler failing does not leave the live board
 * unwatched. It has exactly one job: fire workflow_dispatch. It does not
 * read or write any application data.
 *
 * TWO INDEPENDENT TRIGGER PATHS, ONE SECRET:
 *   1. scheduled() -- Cloudflare's own native Cron Trigger (see
 *      wrangler.toml's [triggers] block). Independent scheduling failure
 *      domain from GitHub Actions.
 *   2. fetch() -- an HTTP route intended to be pinged by a free external
 *      monitor (e.g. cron-job.org, healthchecks.io) on an offset schedule.
 *      A second, ALSO-independent failure domain (neither Cloudflare's own
 *      cron nor GitHub's), reusing this same Worker and its already-stored
 *      secret -- the redundant pinger itself never sees or needs the
 *      GitHub token, so credential exposure is not doubled by adding this
 *      second path.
 * The GITHUB_PAT secret lives ONLY in Cloudflare's own secret store (set
 * via `wrangler secret put GITHUB_PAT`, never in this file, never in
 * wrangler.toml, never committed, never logged).
 *
 * CONCURRENCY SAFETY. dashboard-live.yml already declares
 * `concurrency: {group: dashboard-live-observation, cancel-in-progress:
 * false}` -- a redundant workflow_dispatch call from this Worker firing
 * close to a real scheduled run simply queues behind it rather than
 * colliding or duplicating work. This Worker does not need its own
 * deduplication logic as a result.
 */

const WORKFLOW_FILE = "dashboard-live.yml";
const REF = "main";

async function dispatchWorkflow(env) {
  const url = `https://api.github.com/repos/${env.REPO_OWNER}/${env.REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_PAT}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "full-count-freshness-watchdog",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: REF }),
  });
  // A successful dispatch returns 204 with an empty body -- there is
  // nothing else to read on success. Any other status is a real failure
  // this Worker should surface (to Cloudflare's own logs/observability),
  // never silently swallow -- this Worker's whole purpose is to be the
  // thing that notices when the primary path is failing, so it must not
  // itself fail silently.
  if (resp.status !== 204) {
    const body = await resp.text().catch(() => "<unreadable body>");
    throw new Error(`workflow_dispatch failed: HTTP ${resp.status}: ${body.slice(0, 500)}`);
  }
  return { dispatched: true, at: new Date().toISOString() };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      dispatchWorkflow(env).catch((err) => {
        console.error("scheduled dispatch failed", err);
      }),
    );
  },

  async fetch(request, env, ctx) {
    // Intentionally requires a shared secret query param, NOT the GitHub
    // PAT itself, so an external monitor can be configured to hit this
    // URL without ever holding (or being able to leak) real GitHub
    // credentials. Set via `wrangler secret put PING_TOKEN`.
    const url = new URL(request.url);
    if (url.pathname !== "/ping") {
      return new Response("not found", { status: 404 });
    }
    if (!env.PING_TOKEN || url.searchParams.get("token") !== env.PING_TOKEN) {
      return new Response("unauthorized", { status: 401 });
    }
    try {
      const result = await dispatchWorkflow(env);
      return new Response(JSON.stringify(result), {
        status: 200, headers: { "content-type": "application/json" },
      });
    } catch (err) {
      return new Response(JSON.stringify({ dispatched: false, error: String(err) }), {
        status: 502, headers: { "content-type": "application/json" },
      });
    }
  },
};
