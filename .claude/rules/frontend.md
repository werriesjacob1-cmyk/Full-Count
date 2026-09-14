---
globs: ["dashboard/static/**", "docs/**", "render_board.py", "render_full_board.py", "render_parlay.py", "final_card.py", "test_browser_e2e.py"]
---

# Frontend rule

## Source of truth

`dashboard/static/{index.html,app.css,app.js}` is the **only** real source for the
frontend shell. `docs/` is build output — `copy_static_assets()` in
`dashboard/build_dashboard.py` overwrites it. Editing `docs/` directly produces a
change that looks right locally and vanishes on the next build.
`test_build_dashboard.py`'s `StaticSourceParityTests` enforces the parity; a
failure there means edit the static source and re-sync, never patch `docs/`.

## Standing rules

- **Never make the board look more confident than the model is.** A pick below
  the main board's own probability floor carries a ⚠ for a reason. Removing a
  caveat because it looks untidy is a product change, not a design change.
- **Mobile is the primary surface.** Check narrow viewports first.
- **Accessibility is not optional**: real contrast ratios, keyboard reachability,
  visible focus, labels a screen reader can use.
- Chromium is pre-installed at `/opt/pw-browsers/chromium` with
  `PLAYWRIGHT_BROWSERS_PATH` already set. Never run `playwright install`.
- Exploratory Playwright checking *finds* problems; the deterministic E2E suite
  (`test_browser_e2e.py`) proves they stay fixed. A visual pass without the
  deterministic suite is not sign-off.
- Screenshots before and after, at the viewport where the bug appears.

## Not yours from here

Anything predictive, research, or settlement. If the honest fix for a display bug
is that the underlying number is wrong, say that and stop. Do not paper over a
wrong number with presentation.
