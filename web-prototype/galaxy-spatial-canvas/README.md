# BananLoop — UI-01.3 Spatial Research Canvas

**Status:** React Flow implementation candidate, design-approved direction **Banana Noir** (with more restrained Atelier-style typography). This code is a *read-only research graph demo*; it intentionally does not start campaigns, call LLMs, authenticate a ChatGPT subscription, or mutate BananLoop Engine.

## Features

- React Flow 12: drag nodes and gates, wheel-to-zoom, pinch zoom, pan, fit, minimap.
- Directional Engine edges with separate styles for REQUIRES, DERIVED_FROM, RELATED_TO, group membership.
- Double-click blank canvas to add a personal note. Edit notes inline, delete individual notes without affecting Engine.
- Select `Arrow`, then click source and destination to add a **personal annotation**, never a research dependency.
- Collapsible rail with pointer/keyboard resizing. Inspector slides into view, explains statuses.
- Browser-local, campaign-scoped persistence for node positions, notes, annotation arrows, viewport, and sidebar.
- Desktop-first design (1440 / 1024 / 768 target). Reduced motion respected.

## Run locally

```sh
npm install
npm run dev
npm run typecheck
npm test
npm run build
```

**Important:** npm registry was unreachable in the author's environment (`EAI_AGAIN`). **No React Flow dependencies were installed, so `npm run dev`, full TypeScript typecheck, actual React runtime, or Vite production build could not be verified.** The included plain-TypeScript state-model unit tests run with the existing Node 22 runtime (`npm test`), while the earlier standalone HTML preview remains browser-tested. Treat this as a code implementation pending real build verification, not as a production-ready release.

The source-only code uses version ranges until `npm install` generates a lockfile; commit the lockfile after resolving dependencies. The `Button` is a locally authored component inspired by the shadcn source-owned component model, **not** claimed to be a generated shadcn component.

## Security and semantic boundaries

```text
BananLoop Engine Graph (read-only projection, FUTURE UI-03)
  └─ immutable fixture.nodes/edges/gates (NOW: synthetic only)
      └─ React Flow visual nodes, arrows, inspector, status tooltip

Personal Canvas (browser localStorage, versioned, campaign scoped)
  ├─ positions for Engine graph objects
  ├─ notes (up to 60; max 2000 chars each)
  ├─ annotations (up to 100; validated source/target)
  ├─ viewport and left rail layout
  └─ never persisted into Engine GraphStore
```

`READY` means eligible, not running; `PENDING` does not automatically mean human approval; `VERIFIED` means completion backed by verification. These labels currently demonstrate **synthetic** states only. No VALIDATION/HOLDOUT values are exposed. Real data and durable per-user annotation API are separate future RPDI tasks.

## Tests and acceptance

- `node --experimental-strip-types --test tests/*.test.mjs`: validates storage isolation, input normalization, arrow constraints, note deletion, read-only boundary checks.
- Syntax-only TypeScript transpilation is possible without npm but is **not equivalent to a real build or typecheck**.
- Full browser acceptance on compiled React Flow is blocked until dependencies are available; test zoom at cursor, drag and viewport restoration, note edit/save/reload, right-panel and rail responsive behavior, 100/300 nodes, keyboard navigation, performance and reduced motion when running locally.

## Integration guidance

This implementation is intended to replace the prior hand-written SVG canvas only after a proper React Flow runtime smoke test and QA. Use a typed, safely redacted `GraphViewDTO` when UI-03 is ready. Keep `PersonalCanvas` independent of graph revision and evaluator isolation, resolve positions when nodes enter or exit the graph, and do **not** enable `onConnect` for authoritative edges.