> **Current branch / PR #142:** this is now the real-data Connected UI, not only the original UI-01.3 synthetic canvas. The program opens on Home without campaigns. Start the API with `bananloop serve --studio`, then use **New Campaign** to discuss a goal with Codex, review a proposed budget, and explicitly prepare/start the BananLoop CLI. The older material below documents the original UI-01.3 source milestone. See [Connected UI](CONNECTED_UI.md) and [Studio](../../docs/ui/studio-chat-first-launcher.md) for current capabilities, limits and launch instructions.

# Connected BananLoop UI — 08.11.2026

**Start here:** [CONNECTED_UI.md](CONNECTED_UI.md). The UI now reads real campaign data from the existing loopback BananLoop API through the Vite `/api` development proxy. React Flow Galaxy, Mission Control observation, Decision Center (existing v1 proposals only), and Codex CLI connection status are wired to real data. No synthetic fallbacks, no Engine writes, no in-browser ChatGPT OAuth yet.

## Start

```bash
# In terminal 1, for your target repository with campaigns:
bananloop serve --repo /absolute/path/to/your/research-project
# In terminal 2, this BananLoop checkout:
cd web-prototype/galaxy-spatial-canvas
npm install
npm test
npm run dev
```

---

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

## Personal arrow labels (UI-01.3 follow-up)

Select **Arrow**, click a source node, then click a target node. The new arrow opens its inspector with a **Название стрелки** field. Type a label (up to 80 characters); it appears directly on the dashed cyan arrow. To change or delete it later, **click the dashed arrow**. Labels save in the campaign-scoped local browser canvas alongside notes and positions. Research dependency edges from Engine remain immutable. Unlabeled arrows from an earlier UI-01.3 save still load.

Other navigation sections remain intentional noninteractive previews and will receive real functionality in later RPDI slices.