# Design exploration review — BananLoop Galaxy

Date: 2026-11-07 · Synthetic fixtures · No changes to BananLoop Engine

## Why this study exists

UI-01.2 had three concrete UX problems: graph nodes were fixed, the zoom gesture was not intuitive, and too many decorative/functionally redundant controls made the UI feel inexpensive. The user also requested personal notes/arrows, an adjustable left rail, a richer animated ASCII backdrop, clearer AND/OR and execution semantics, and a visible future AI-provider settings entry.

## Three visual directions — assessment

| Criterion | 01 Atelier | 02 Banana Noir | 03 Signal Room |
| --- | --- | --- | --- |
| Visual positioning | Calm editorial / research studio | Brand-specific, restrained spatial tool | Technical mission station / terminal |
| Dark-space identity | Low | High | Medium |
| Long-session comfort | High | High | Medium |
| Node/arrow contrast | High | High with distinct color keys | High, but busy in dense graphs |
| Premium impression | Spacious typography, quiet shadows | Fine borders, golden highlights, subtly animated ASCII | Schematic grid, high contrast and geometric forms |
| Primary risk | Looks generic and off-brand | Status accents and brand accent could compete | Excessive green/CRT feel and eye fatigue |

**Suggested starting point: 02 Banana Noir**, borrowing the generous negative space and editorial typography of Atelier. Do not adopt Signal Room wholesale. Leave the final choice to visual review.

## Prototype design critique (manual, NOT an Impeccable run)

1. **Hierarchy**: Large page title and a quiet secondary toolbar replace stacks of equivalent yellow-framed boxes. Individual nodes use restrained semantic top rails.
2. **Controls**: Remove the UI-01.2 `Chat / Split / Research` presets from Galaxy; provide direct manipulation and compact, contextual actions. Motion Lab is not a production top-level control.
3. **Connections**: Solid arrows = research dependencies; dashed cyan arrows = personal annotation. Gate cards explicitly say `ANY INPUT` or `ALL INPUTS`. Still needs production Graph DTO and edge labels/tooltips for `DERIVED_FROM` and `RELATED_TO`.
4. **Data semantics**: READY means eligible, not running. PENDING may mean dependencies or scheduling, not necessarily awaiting a person. Verified completion is distinct from the scheduler status.
5. **Visual debt**: This design still requires a component-level audit with Impeccable `critique`, `distill`, `polish`, and `harden` once installed. We could not execute the skill locally because the npm registry did not resolve.
6. **Responsive behavior**: At 768px the rail becomes compact and Galaxy remains navigable, but long sessions at narrow sizes warrant further mobile-specific design. The intended target is desktop first.

## Reuse vs adapt (sources inspected)

| Source | License verified via repository | What to learn/reuse | What NOT to copy |
| --- | --- | --- | --- |
| [React Flow / xyflow](https://github.com/xyflow/xyflow) | MIT | Directed edges, ports/handles, wheel zoom, pan, node drag, MiniMap; use its library instead of our own renderer | Never allow free `onConnect` to mutate Engine topology |
| [CanvasTTY](https://github.com/howdeploy/CanvasTTY) | MIT | Spatial gestures, stored camera, sticky-note interactions, pixel-frame notions | Do not pull in Electron/PTy/security runtime |
| [Whiteboard](https://github.com/devdotfast/whiteboard) | MIT | Linking visual research artifacts and decisions with real evidence | Do not turn research graph into a source-code editor |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | MIT | Source-owned accessible UI primitives; controlled variants and spacing | Avoid blanket default rounded dashboard theme |
| [Impeccable](https://github.com/pbakaus/impeccable) | Apache-2.0 | `critique`, `distill`, `layout`, `polish`, `harden` audit workflow | Do not use dangerous live-mode bypass flags on untrusted code |
| [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | MIT | Design-system choice and typography/spacing guidance | Do not generate a generic SaaS landing page instead of an app |
| [tldraw](https://github.com/tldraw/tldraw) | SDK licensing is not ordinary MIT | Annotation UX reference | Do not import SDK without reviewing production license |

## Proposed design-engineering loop

1. Read `README.md` and current project `UI Design Spec` and produce an explicit component inventory.
2. Freeze canonical, synthetic test fixtures and define the design target (desktop Galaxy with 8 nodes, 2 gates, one note).
3. Present three screenshots from the same fixture, with the same viewport and controls.
4. Select a direction; install Impeccable in a local checkout (only in a network-enabled environment) and run `init`, `document`, `critique`, `distill`, `audit`, `polish` and `harden` narrowly on components.
5. Re-run Chromium visual scenarios at 768/1024/1440/1920, long text, increased text zoom, reduced motion, keyboard navigation, 100/300 nodes, and disconnect/resume.
6. Merge the chosen style only after UX and technical acceptance; leave external-auth flows and Graph DTO for separate RPDI.

## Suggested Codex usage

```bash
# In a network-enabled *local* checkout after reviewing the tool's installation behavior:
npx impeccable install
# In Codex after restart:
# $impeccable init
# $impeccable document
# $impeccable critique Research Galaxy node cards
# $impeccable distill Galaxy toolbar
# $impeccable audit
# $impeccable polish
```

**Security:** Impeccable `live` can choose an agent fallback with bypassed approvals/sandbox in some circumstances; only run it after inspecting flags, or configure it to stay in the chat session. No skill installer or live mode was executed in this task.

## Production model boundary

```
BananLoop Engine (immutable graph / evaluator isolation)
   │ read-only trusted projection
   ▼
GraphViewDTO: nodes, typed edges, AND/OR, statuses, revision
   │
   ├── React Flow renderer (positions, zoom, selection are visual only)
   │
   └── PersonalCanvasStore (user/campaign notes, arrows, node positions)
             ▲
       user-only edits; never graph mutations
```

Questions before production: per-user/local or server-side annotation storage, persistence and export, coordinate conflicts when nodes are added by Engine, websocket event volume/decimation, and security of note content. Choose one before adding write APIs.