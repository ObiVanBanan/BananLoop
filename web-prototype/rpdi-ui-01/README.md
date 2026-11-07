# BananLoop UI — RPDI-UI-01

**Interactive synthetic prototype. Not connected to BananLoop Engine.**

Open [index.html](./index.html) locally in a browser, or serve this directory:

```bash
cd web-prototype/rpdi-ui-01
python3 -m http.server 4173 --bind 127.0.0.1
# http://127.0.0.1:4173
```

The HTML is self-contained: inline CSS, fixture data, and JavaScript; no npm install, external assets, network calls, or build step.

## Available

- Mission Control: campaign list, campaign-local chat drafts/history, result and decision cards, compact dashboard, create-draft modal.
- Research Galaxy: canonical fixture edges, AND/OR groups, node inspector, pan, Ctrl+wheel/controls zoom, Fit.
- Desktop 1366×768 and laptop 1024×768 layouts; wide 1920×1080; overlay panels on narrower screens.
- Settings: exercise normal, stale, disconnected, partial and empty-graph states.
- Explicit `SYNTHETIC PROTOTYPE` in the header. Actions update local UI state only.

## Engine integration boundary

This slice intentionally performs **zero** Engine/API calls. Live graph projection is not available via the current public v2 read API: it exposes node statuses and blockers but not all canonical edges and AND/OR groups. A later RPDI must add a safe, revision-consistent **read-only** DTO from the existing graph store. The graph fixtures are not claimable live telemetry.

Launch, stop, pause, change evaluator, mutation and persistent model chat are **not implemented**. A user click cannot fabricate a command receipt or make a model decision. In particular global v2 pause/resume/stop must not be enabled merely because v1 CLI controls exist.

## Verification

The standalone prototype was browser-smoke-tested at 1366×768, 1024×768, and 1920×1080: no page overflow; bottom-pinned chat input; campaign/tab navigation; graph (8 nodes + 3 gates); node inspector/Escape; draft retention; local chat send; state settings. AND/OR fixture rules tested separately with Node's built-in test runner.

**Reference specification:** `docs/ui/ui-design-spec-v1.md` in this PR. Original mockups are *visual references*, not runtime graph or telemetry.
