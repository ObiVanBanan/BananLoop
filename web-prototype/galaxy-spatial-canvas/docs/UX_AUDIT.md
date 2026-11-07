# UI-01.3 design review — manual (not an Impeccable run)

## Design north star
Banana Noir cosmic identity with editorial hierarchy, readable node cards, minimal chrome and unambiguous directed edges. Keep a single gold brand accent and separate semantic colors.

## Changes vs UI-01.2
- Removed `Chat / Split / Research` view presets and production-facing Motion Lab toolbar.
- Directional edges are always marked; personal dashed cyan arrows are distinct from Engine links.
- Left rail collapses, resizes via pointer and arrow keys, and persists locally.
- PENDING, READY and VERIFIED status descriptions are accessible in the inspector; READY ≠ running and PENDING ≠ human approval.
- Restyled note card and inspector; adding a note is direct double-click on empty canvas.
- Preserved keyboard focus, CSS `prefers-reduced-motion`, normal typography for content, and separate monospace chrome.

## Remaining issues
- Run Impeccable `critique`, `distill`, `audit`, `polish` against the real React Flow page after `npm install`; not run here.
- Test node keyboard navigation, 200% text zoom, long Russian names, 100/300 nodes, Safari/Firefox pointer gestures and device pixel scaling.
- Decide whether future annotations should sync across computers; localStorage is only a P0 prototype, not multi-user storage.
- Deep-link to real Engine and provider configuration are not in this phase.
- Need real screenshots of compiled React UI; this PR contains only approved standalone-browser screenshot references.