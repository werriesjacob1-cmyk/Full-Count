# Automation Platform Decision Record

Written 2026-08-26. Evidence-based comparison for FULL COUNT's automation
needs, run specifically because the prior session's assumption that
Activepieces was the winner was flagged as unverified and reopened. This
does NOT decide the P0 live-freshness heartbeat -- that's Cloudflare
Workers, a data-plane primitive, not an automation platform (see "Why not
an automation platform for the heartbeat" below). This decides the
**control/automation plane**: alerts, admin tasks, recovery dispatch,
research-completion notifications, report generation -- things that can
tolerate SaaS credit limits and occasional latency.

## Why not an automation platform for the heartbeat

The heartbeat's only job is one authenticated HTTP call every 5 minutes,
288 times/day, forever, on the critical path of customer-facing freshness.
Putting that on a credit-metered SaaS platform makes an unrelated vendor's
quota/outage a P0 dependency for something a $0, un-metered Cloudflare
Worker already solves completely. This is a data-plane vs control-plane
distinction (see the governing prompt's section 42) -- the heartbeat stays
on Cloudflare regardless of what wins below.

## Candidates evaluated

| Candidate | Current plan | Free forever? | Execution quota | MCP | GitHub dispatch | Phone admin | Secrets | Logs | Portability | Role fit | Critical-path suitable |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Activepieces Cloud (Free)** | $0, no card | Yes, stated free-forever | **Daily credits, exact number NOT disclosed on the public pricing page** (confirmed via two direct checks) | Yes, built-in MCP server | Via a GitHub piece within a flow -- but the pricing page separately lists "GitHub/Git Sync integration" as **NOT included** on Free; this is likely the flow-versioning/CI-sync feature, not the in-flow GitHub API connector, but that distinction was not independently confirmed and should be checked in the account UI before relying on it | Yes -- web app works fine in Safari | Per-connection secret storage in the platform | Run history/logs in UI | Flows are JSON-exportable | Alerts, admin automation, low-frequency recovery, research-completion notifications | **NOT PROVEN** -- unknown daily quota means it cannot be shown sufficient for anything frequent |
| **Windmill Cloud (Community/Free)** | $0 | Yes, stated as free/open-source, "from $0/mo" | **"Unlimited executions" stated on the pricing page**, confirmed on a second, more targeted check -- no explicit monthly execution cap found (fair-use limits almost certainly still apply even though no number is published) | Yes | Via scripts calling GitHub's REST API directly (Windmill runs actual Python/TypeScript/Bash, not just no-code pieces) | Web app in Safari; less mobile-optimized UI than Activepieces by reputation, not independently verified this session | Native resource/variable secret storage | Full execution logs, versioned runs | Scripts are plain code -- the most portable of the candidates (no proprietary flow format to export) | Real technical challenger for anything needing actual code (Python/TS), not just no-code pieces -- good future fit for Live Brain's control-plane needs (health checks, alert routing) | **Best-evidenced free-tier execution capacity of the SaaS candidates**, but still newer to this evaluation than Activepieces -- worth a real trial task before calling it primary |
| **n8n Cloud** | Paid only | **No** -- confirmed no free-forever Cloud tier, trial only (Starter/Pro trial length unstated; Business trial is 14 days, requires a card) | Paid tiers: Starter 2,500/mo (~$20/mo billed annually), Pro 10,000/mo (~$50/mo), Business 40,000/mo (~$667/mo) | Yes | Yes, mature | Yes (web) | Yes | Yes | Yes (export workflows as JSON) | N/A while unpaid | **Rejected for now** -- not a permanent free hosted dependency, matches the concern already on record |
| **n8n Community (self-hosted)** | Free, open-source | Yes, but requires a host | Unlimited (your own compute) | Yes | Yes | No -- self-hosting from an iPhone is not realistic | You manage it | You manage it | Full (your own instance) | Viable **only if/when** a persistent host we actually want to maintain exists | Not now -- explicitly deferred per governing instructions, not discarded |
| **Pipedream Free** | $0 | Believed yes, historically credit-based | **Not independently verified this session** -- the pricing page did not render via the fetch tool available; do not treat any specific number as confirmed | Limited/varies | Yes | Yes (web) | Yes | Yes | Partial | Possible low-priority alerts/glue | Not evaluated deeply, per instruction to avoid platform sprawl on a candidate that wasn't obviously attractive to begin with |
| **Prefect Cloud (Hobby)** | Free tier exists | Not deeply evaluated | N/A | N/A | N/A | N/A | N/A | N/A | N/A | **Research/data-pipeline workflows only** (e.g., orchestrating a future multi-stage backtest pipeline), not general automation | Out of scope for this comparison -- only relevant if a specific research-reliability problem needs it, none identified yet |

## What was actually checked vs assumed

- Activepieces' undisclosed daily credit number: checked twice against the
  public pricing page directly, both times confirmed not stated. This
  matches the governing prompt's own suspicion -- **do not treat
  Activepieces as proven sufficient for anything frequent** until the
  actual number is read from a signed-in account (an authorization
  boundary -- see "Required Jacob actions" in the main report).
- Windmill's "unlimited executions" claim: checked twice with different,
  more targeted prompts, both returned the same answer. This is
  **evidence against** the governing prompt's own prior assumption
  ("~1,000 monthly executions... not enough") -- worth flagging plainly
  rather than deferring to the original assumption now that it's
  contradicted by the platform's own current pricing page.
- n8n Cloud's lack of a free-forever tier: confirmed directly, matches
  the concern already on record.
- Pipedream: genuinely not verified (tooling limitation, not a shortcut
  taken) -- reported as such rather than filled in with a plausible-
  sounding number.

## Recommendation

**No platform is adopted as a production dependency yet.** Evidence
supports a two-step path, not an immediate pick:

1. **Windmill Cloud Free** is the best-evidenced candidate for anything
   that needs real code and meaningful execution volume (its stated
   "unlimited executions" is the only quota claim among the SaaS
   candidates that isn't hedged) -- worth a real, low-stakes trial task
   (e.g., a daily "is the canonical rebuild still healthy" check that
   posts a message somewhere) before trusting it with anything more.
2. **Activepieces Free** stays worth keeping in view for its MCP
   integration and no-code speed for one-off admin tasks, but should NOT
   become a critical dependency until its actual daily credit number is
   confirmed from a real signed-in account.
3. **n8n** stays exactly where the governing prompt already put it:
   not deployed now, not paid for now, not discarded -- Community
   self-hosted remains the fallback if/when a persistent host exists.
4. **Pipedream and Prefect**: not adopted, not rejected -- insufficient
   evidence gathered to do either responsibly, and neither is obviously
   necessary right now.

This is deliberately not the "Cloudflare for heartbeat, Activepieces for
everything else" conclusion the governing prompt floated as a possible
outcome -- the evidence doesn't support picking Activepieces as primary
yet. Revisit once Jacob has checked Activepieces' real credit allotment
and/or a Windmill trial task has run for a week.
