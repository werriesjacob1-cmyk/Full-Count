---
name: fc-ux-audit
description: Visual/interaction QA pass for a Full Count UX branch -- Playwright exploratory check plus confirming the deterministic E2E suite still passes. Use before asking Jacob for visual sign-off.
---

# fc-ux-audit

## Steps

1. Delegate to the `fc-ux` agent -- it's the only agent allowed to touch
   `dashboard/static/*`.
2. Confirm `dashboard/static/{index.html,app.css,app.js}` was resynced to
   `docs/*` via `copy_static_assets()` -- `test_build_dashboard.py`'s
   `StaticSourceParityTests` enforces this; a failure there means a
   forgotten resync, not a logic bug.
3. Run BOTH suites, not one instead of the other:
   - `test_browser_e2e.py` (deterministic Playwright regression suite).
   - Exploratory QA: screenshots, mobile viewport checks (minimum 375/390/430px
     plus one short-height viewport), console-error checks, light AND dark
     mode.
4. A real behavior change needs a real regression check added to
   `test_browser_e2e.py` -- a screenshot alone does not replace that.
5. Confirm no casino/neon/urgency-language drift: Full Count reads as a
   baseball intelligence product, not a sportsbook. Any generic UI-pattern
   suggestion (including from a design-critique skill) is an ADVISER;
   Full Count's own product rules in `fc-ux.md` always win on conflict.
6. This branch does not get merged or opened as "ready to merge" from this
   skill alone -- screenshots for review are the deliverable. Jacob signs
   off visually first.

## When NOT to use

Non-visual backend/data work -- this is specifically for
`dashboard/static/*` changes.
