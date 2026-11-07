# RPDI-UI-01.2 — Interactive Pixel Universe

This is the **synthetic-only, standalone** continuation of the UI-01.1 Pixel Universe design system. It builds on the [RPDI-UI-01 prototype](../rpdi-ui-01/).

## Open

Open [index.html](index.html) directly in a modern browser. All JavaScript, styles, and synthetic fixtures are embedded; there is no API server, CDN, model call, or build step.

## Interactions

- **Mission Control:** three layout presets (CHAT / SPLIT / RESEARCH); the dashboard can be resized with a pointer or keyboard at desktop widths. On compact screens the dashboard becomes an overlay drawer.
- **Progressive research cards:** inspect a fixture node and jump to its exact identity in Research Galaxy.
- **Research Galaxy:** retains pan, zoom, AND/OR gates and node inspector. Motion Lab offers four manually triggered **cosmetic demos** (node reveal, activity pulse, verified glow, branch reveal).
- **Motion modes:** Off / Subtle / Living, with `prefers-reduced-motion` override.
- **Decision Center:** selectable synthetic decision examples, with one linked graph node; approve/reject remain disabled.
- **Per-campaign UI continuity:** layout, chat draft, graph viewport and selected node are saved via localStorage when allowed. Local demo chat messages are session-only.

**Source of truth remains BananLoop Engine.** The synthetic `verified` fixture is not a production verdict. No Engine commands, event subscriptions, or modifications to canonical graph data occur.

## QA completed locally

- Graph-domain fixture tests: 4/4 passed.
- Chromium UI tests at 1366×768, 1024×768, 1920×1080 and 683×768: presets, node inspector, card expansion, Motion Lab, Decision Center and navigation passed; no horizontal overflow.
- Pointer and keyboard resizing passed on desktop.
- `prefers-reduced-motion` suppresses cosmetic motion.
- Preference round-trip passed with a mocked localStorage. Direct `file://` navigation was restricted by the browser test environment, so browser-origin persistence needs end-user verification.
- Error, stale, disconnected, partial, empty and legacy demonstration states passed.

Hosted CI was not run. Full editable source files and screenshots are delivered separately with the conversation artifact archive.

## Scope boundaries

This PR intentionally excludes the read model adapter, SSE, server-side Graph DTO, real operator chat and pivot approvals. Those belong to UI-02/03/04 and future slices. Real event-driven animation is intentionally not part of UI-01.2.
