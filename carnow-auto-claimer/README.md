# CarNow Lead Auto-Claimer

Manifest V3 Chrome extension that watches CarNow dashboards for incoming leads and
clicks the claim control automatically.

> **Before you run this on the floor:** automated claiming very likely conflicts with
> CarNow's terms of service and with your dealership's lead-distribution policy. Confirm
> with your manager first.

## Files

```
carnow-auto-claimer/
├── manifest.json          MV3 manifest
├── content.js             detection + clicking (all frames)
├── background.js          service worker: keep-alive, notifications, history
├── options.html           control panel
├── options.js             control panel logic
├── icons/                 16 / 48 / 128 px
└── test/mock-carnow.html  offline test harness
```

## Install

1. `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select the `carnow-auto-claimer/` folder.
3. Click the extension's **Details → Extension options** to open the control panel.

## How detection works

Three independent paths, because no single one is reliable in a background tab:

| Path | Trigger | Latency | Survives tab throttling |
|---|---|---|---|
| `MutationObserver` | node added / attribute changed | < 2 ms | yes (not a timer) |
| Worker-timer sweep | every 500 ms | ≤ 500 ms | yes (Worker timers are exempt) |
| Alarm push (`SWEEP`) | every 30 s from the service worker | ≤ 30 s | yes (alarms wake the worker) |

The observer is the hot path and clicks **synchronously** inside the callback — no
`setTimeout`, no debounce — which is what keeps it under the 50 ms budget. The other two
are safety nets.

## Matching rules

Candidates: `button`, `a`, `[role="button"]`, `.btn`, `input[type=button|submit]`.

- **Match:** `\b(claim|accept)\b` against `aria-label`, `title`, `data-action`,
  `data-testid`, `value`, and text content (text only if ≤ 60 chars, so a whole card's
  text can't trigger a match).
- **Veto:** `claimed`, `unclaim`, `reclaim`, `decline`, `reject`, `dismiss`, `cancel`,
  `history`, `report`, `undo`, plus `cookie`/`consent`/`terms`/`privacy`/`policy`/
  `agreement`/`newsletter`/`subscribe`/`marketing`.
- **Excluded:** `[disabled]`, `.disabled`, `.claimed`, `[aria-disabled="true"]`,
  `[data-claimed="true"]` — on the element *or any ancestor*.
- **Visible:** `offsetParent !== null`, with a `getBoundingClientRect()` fallback for
  `position: fixed` modals (which always report a null `offsetParent`).
- **Innermost wins:** a `div[role="button"]` wrapping a real `<button>` yields one click.

Word boundaries do the heavy lifting: `\bclaim\b` does not match `claimed`, `unclaim`,
`disclaimer`, or `exclaim`.

## Edge cases handled

- **iframes** — `all_frames: true` + `match_about_blank: true`; each frame runs its own
  observer.
- **Shadow DOM** — open roots are discovered on mutation and re-scanned every 2 s, each
  getting its own observer. Closed roots are unreachable by design.
- **Loop prevention** — a `WeakSet` of clicked elements (node identity) *and* a `Map` of
  lead signatures (survives re-renders). The element is marked **before** the click,
  because the click can synchronously re-render and re-enter the observer.
- **Memory** — signatures expire after 10 minutes, swept every 60 s; detached shadow
  roots are pruned; observers disconnect on `pagehide` and re-attach on bfcache restore.
- **Storage races** — `background.js` is the single writer for `chrome.storage.local`,
  with a serialized write chain, so simultaneous claims across frames can't clobber each
  other. `content.js` writes directly only if the worker is unreachable.

## Testing with Chrome DevTools

### 1. Offline, with the harness

`test/mock-carnow.html` reproduces every claim path. The content script only matches
`*.carnow.com`, so pick one:

- **Easier:** add `"file:///*"` to `content_scripts[0].matches` in `manifest.json`,
  reload the extension, and enable **Allow access to file URLs** in its Details page.
- **Closer to production:** map a hostname in your hosts file and serve the folder over
  HTTPS.

Then open the file and click the spawn buttons. The harness measures latency from DOM
insertion and prints `[PASS <50ms]`.

The **Spawn decoys** button is the important one — none of those six rows should ever
appear as CLAIMED.

**Remove the `file:///*` match before real use.**

### 2. Inspecting the content script

On a CarNow tab: **F12 → Console**, set the frame selector (top of the console) to the
frame you care about, then:

```js
__carnowAutoClaimer.settings()     // live settings
__carnowAutoClaimer.candidates()   // what it would click right now
__carnowAutoClaimer.tracked()      // [signature, timestamp] pairs
__carnowAutoClaimer.roots()        // open shadow roots under observation
__carnowAutoClaimer.sweep('manual')// force a scan
```

Turn on **Logging/Debug Mode** in the options page for orange `[CarNow AC]` logs.

### 3. Inspecting the service worker

`chrome://extensions` → the card → **service worker** link. That console shows alarm
ticks, claim records, and port counts. The worker going idle is normal — the alarm
restarts it within 30 s. To confirm it is alive:

```js
chrome.runtime.sendMessage({ type: 'GET_STATUS' }).then(console.log)
```

### 4. Verifying the 50 ms budget

**Performance** panel → record → spawn a lead → stop. Find the `Recalculate Style` /
mutation callback and confirm the click dispatch sits in the same task. Each entry in the
options page's history table also carries the measured `elapsedMs`.

### 5. Storage

**Application → Storage → Extension storage**, or:

```js
chrome.storage.local.get(console.log)   // claimHistory, lastClaimAt
chrome.storage.sync.get(console.log)    // autoClaim, soundAlert, debug
```

## Notes

- `declarativeNetRequest` is declared because it was requested, but nothing uses it — the
  extension never touches network requests. Dropping it from `permissions` reduces the
  install warning and changes no behavior.
- `alarms` and `notifications` were added beyond the original permission list; they are
  required for `chrome.alarms` and `chrome.notifications` respectively.
- No `tabs` permission is needed: the host permission for `*.carnow.com` is enough for
  `tabs.query({url})` and `tabs.sendMessage` on those tabs.
- The sound uses a WebAudio oscillator, so there's no asset to ship. Chrome's autoplay
  policy may keep the `AudioContext` suspended until you interact with the page once.
