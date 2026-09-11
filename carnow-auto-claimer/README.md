# CarNow Lead Auto-Claimer

Manifest V3 Chrome extension that watches `app.carnow.com` for a lead that **just
arrived** and claims it by tapping the row.

> **Before running this live:** automated claiming very likely conflicts with CarNow's
> terms of service and with your dealership's lead-distribution policy. Confirm with your
> manager first.

## The core problem

CarNow has no "Claim" button. A salesperson claims a lead by **tapping the row** while a
CarNow rep or the AI is talking to the customer. That makes naive detection dangerous:
every unclaimed row on screen is a valid target, and the All Leads view holds nine pages
of history. A sweep-based claimer would grab the entire backlog on its first tick.

So this extension never asks *"is this row claimable?"* It asks *"did this row appear
**after** I started watching?"* Everything present at startup is baselined and ignored
permanently.

## The five gates

A detected row must pass all five before anything is clicked:

| # | Gate | Blocks |
|---|---|---|
| 1 | **Armed** | Acting before the baseline has settled (2s quiet, 15s hard cap) |
| 2 | **Burst** | More than 3 new rows at once — a filter switch, sort, page turn or reload |
| 3 | **Page** | Anything not on page 1 of the pagination |
| 4 | **Freshness** | Rows whose Last Update is older than `maxLeadAgeMin` (default 5) |
| 5 | **Rate limit** | Faster than 1 per 10s, or more than 10 per session |

Gate 2 is the important one. Every scenario that makes the whole list look new — changing
to the Missed tab, re-sorting, turning a page, a hard refresh — produces many new keys at
once, and all of them get vetoed while the baseline resets to the new view.

## Row identity

Keys prefer a stable identifier and fall back to content:

1. `data-lead-id`, `data-conversation-id`, `data-id`, … if present
2. the row's `href` (e.g. `/conversations/48812`) — the usual case
3. otherwise, the row's cell text with volatile substrings stripped

That stripping matters more than it looks. CarNow packs a date **and** a time into one
Last Update cell, and unread-count badges sit inside the name cell. If either leaked into
the key, an existing lead would get a new key the moment a customer replied — and then
look brand new to the baseline check, and get claimed. Dates, times, `2d ago` forms, and
standalone 1–3 digit badge counts are all removed before keying.

Lead identifiers survive it: `Benton_747335` keeps its digits (no word boundary after the
underscore), as do 4-digit years and 7-digit stock numbers.

## Files

```
carnow-auto-claimer/
├── manifest.json          MV3
├── content.js             row detection + the five gates
├── background.js          keep-alive, notifications, history
├── options.html / .js     control panel
├── icons/
└── test/mock-carnow.html  offline harness
```

## Install

1. `chrome://extensions` → **Developer mode** on
2. **Load unpacked** → select `carnow-auto-claimer/`
3. **Details → Extension options**

Keep the folder somewhere permanent — Chrome loads unpacked extensions by path.

## Settings

| Setting | Default | Notes |
|---|---|---|
| Auto-Claim Active | ON | Master switch |
| **Dry Run** | **ON** | Detects and announces, never clicks. Turn off to arm. |
| Sound Alert | ON | WebAudio tone, no asset shipped |
| Logging/Debug | OFF | Orange `[CarNow AC]` console output |
| Maximum lead age | 5 min | Gate 4 |
| Min seconds between claims | 10 | Gate 5 |
| Max claims per session | 10 | Gate 5, resets on reload |

**Dry Run ships ON deliberately.** Run a shift with it on and confirm the extension flags
exactly the leads you'd have tapped yourself. One toggle arms it.

## Testing

### Offline

Open `test/mock-carnow.html`. It renders 10 baseline rows, then gives you five buttons:

- **Add ONE new lead** → should be claimed, and the page logs the latency
- **Add stale lead (3 days ago)** → must be skipped by gate 4
- **Bump an existing lead's timestamp** → must *not* re-claim (key stability)
- **Switch filter (replace all rows)** → must re-baseline via gate 2, claim nothing
- **Force re-render** → keys unchanged, claim nothing

Rows turn green when correctly claimed and red when wrongly claimed.

The content script only matches `*.carnow.com`, so add `"file:///*"` to
`content_scripts[0].matches` and enable **Allow access to file URLs** to run it — then
**remove that match before real use**.

### On the live page

Console on `app.carnow.com/conversations`:

```js
__carnowAutoClaimer.status()      // armed? dryRun? baseline size? claims so far?
__carnowAutoClaimer.rows()        // every row with its key, age, baselined flag
__carnowAutoClaimer.rebaseline()  // re-snapshot after changing filters
```

You want `status().armed === true` and `baselineRows` matching the visible row count
before you trust anything. Look for the green `[CarNow AC] ARMED` line in the console.

Service worker console: `chrome://extensions` → the **service worker** link.

## How the tap is delivered

Tapping the row is confirmed to be what claims the lead, so `clickTargetFor()` doesn't
guess which descendant carries the handler. It scrolls the row into view, then hits the
topmost element at a point inside it via `elementFromPoint` — exactly what a finger does.
The event bubbles up through cell, row and container, reaching the handler wherever it
actually lives.

Two safety properties fall out of that:

- **The aim point is left-of-centre**, in the name column. The Actions column on the
  right holds a red X (close/dismiss) that must never be clicked.
- **If anything is covering the row** — a modal, a dropdown, a tooltip —
  `elementFromPoint` returns an element outside the row, and the click is refused rather
  than delivered through the overlay. The row is un-marked so a later tick can retry once
  it's clear.

## Known unknown

**Can a mistaken claim be released?** If not, keep Dry Run on longer than feels
necessary — a wrong claim you can't hand back is the one failure with no undo.

## Notes

- `declarativeNetRequest` is declared because it was requested but nothing uses it.
  Dropping it removes an install warning and changes no behavior.
- `alarms` and `notifications` were added beyond the original list; they're required by
  `chrome.alarms` and `chrome.notifications`.
- No `tabs` permission needed — the `*.carnow.com` host permission covers
  `tabs.query({url})` and `tabs.sendMessage`.
