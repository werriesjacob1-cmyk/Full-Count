---
name: fc-ux-audit
description: Visual and interaction QA pass for a frontend branch — exploratory Chromium check plus the deterministic E2E suite. Use before asking for visual sign-off.
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# fc-ux-audit

Delegate to `fc-ux`, the only agent that may touch `dashboard/static/*`.

## Source, not output

`dashboard/static/{index.html,app.css,app.js}` is the **only** real source.
`docs/` is build output — `copy_static_assets()` in
`dashboard/build_dashboard.py` overwrites it. A change made in `docs/` looks
right locally and vanishes on the next build.

`test_build_dashboard.py`'s `StaticSourceParityTests` enforces the parity. A
failure there means edit the source and re-sync — **never** patch `docs/`.

## What to check, in this order

1. **Mobile first.** Narrow viewport before desktop, not after.
2. **Honesty of the board.** A pick below the main board's own probability floor
   carries a ⚠ for a reason. Never make the board look more confident than the
   model is; removing a caveat because it looks untidy is a product change.
3. **Odds visibility** — the real price, and an honest "not priced" where there
   is none. Never a fabricated percentage.
4. **Market/category exploration** — every market reachable, the top-5-per-market
   fallback present, HR/moonshot framing not overstated.
5. **Search relevance** — player and team names, partial matches, no dead ends.
6. **Technical explanations** — the reasoning shown maps to real underlying data.
7. **Accessibility** — real contrast ratios, keyboard reachability, visible
   focus, labels a screen reader can use. Not optional.

## Chromium — already installed, never install it

Chromium 141 lives at `/opt/pw-browsers` with `PLAYWRIGHT_BROWSERS_PATH` set.
**Never run `playwright install`.** If a project pins a different Playwright
version, launch with `executablePath: '/opt/pw-browsers/chromium'`.

## Exploratory finds, deterministic proves

Exploratory Chromium checking is for *finding* problems. The deterministic suite
is what proves they stay fixed:

```bash
python3 test_browser_e2e.py
python3 test_build_dashboard.py     # includes StaticSourceParityTests
python3 test_frontend_lifecycle.py
```

**A visual pass without the deterministic suite is not sign-off.** Screenshots
before and after, at the viewport where the bug actually appears.

## Not yours

Anything predictive, research or settlement. If the honest fix for a display bug
is that the underlying number is wrong, say that and stop. Do not paper over a
wrong number with presentation.
