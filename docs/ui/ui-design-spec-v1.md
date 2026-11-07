# BananLoop UI Design Specification v1.0

**Status:** Proposed for approval / ready for prototype implementation after UX review  
**Prepared:** 2026-11-07  
**Reference baseline:** `BananLoop-UI-Design-Spec.md` from `BananLoop_UI_Design_Pack(1).zip`, Design Baseline v1.0 (2026-11-07)  
**Code baseline:** `ObiVanBanan/BananLoop`, `main` @ [`6c89975870b0da8060146953faea966e203ba857`](https://github.com/ObiVanBanan/BananLoop/commit/6c89975870b0da8060146953faea966e203ba857) (2026-11-05)  
**Targets:** local web UI, desktop-first, 1366×768 minimum for full three-pane Mission Control; Research Galaxy with faithful AND/OR semantics  
**Scope:** pixel layout, responsive behavior, states, components, read/write boundaries, API gaps, accessibility, RPDI implementation slices  
**Document authority:** design proposals below do not assert backend features exist. Backend contracts are only *verified* where backed by repository references.

## 0. Decisions inherited unchanged from the approved design baseline

1. Two explicit tabs: **Mission Control** (campaign chat + compact status) and **Research Galaxy** (actual research graph for the selected campaign).
2. **One campaign = one project goal and one primary conversation**. Sidebar contains *only campaign names* plus **+ New Campaign** and **Settings** at bottom; no metrics or status pills in the campaign list.
3. Main conversation is with the orchestrator/operator interface, **not** independent Live Agent Chat windows. Event summaries, experiment results, setup and explicit approvals appear as typed cards; don't expose hidden reasoning.
4. Dashboard answers "What is the selected campaign doing?" It is compact and collapsible; its data must be committed or explicitly unknown.
5. Research Galaxy displays actual graph state: typed relationships, AND/OR readiness, verified completions, blocked/paused/active branches. **No forced planetary topology** and no fake score/budget.
6. Visual language: space-station research console, dark engineering panels, Banana Gold, restrained pixel headers, readable body fonts. **No mascot** inside the application.
7. Local browser application; preserve Python Engine, SQLite state, evaluator isolation, immutable decisions, budgets, idempotency and explicit human approval.
8. A provider's model list/plan/quota appears only if reliably retrievable; ChatGPT subscription != API entitlement.

The two PNGs in the supplied Design Pack remain **visual references**, not data-model or API specifications. Illustrative screen numbers and connections must never appear in a live connected campaign as if measured.

## 1. Information architecture

```text
BananLoop Web App
├── App Shell: brand | Mission Control | Research Galaxy | connection/provider
├── Sidebar: + New Campaign | campaign names | Settings
├── Mission Control (selected campaign)
│   ├── Campaign header: goal, context, compact controls
│   ├── Conversation feed
│   │   ├── user message / operator reply / committed event summary
│   │   ├── experiment result card (DEV only, if available)
│   │   ├── setup draft card (validated before launch)
│   │   ├── pivot / action approval card (immutable identity)
│   │   └── connection/unknown/error banners
│   ├── Sticky composer
│   └── Collapsible Mission Dashboard
├── Research Galaxy (same campaign)
│   ├── Graph toolbar/HUD
│   ├── Pan/zoom graph canvas
│   ├── Node/edge/gate selection
│   └── Selected-item details drawer
└── Settings: basic / advanced; campaign settings remain contextual
```

**Navigation invariants:** selected `campaign_id` persists across tabs. Galaxy `viewport{scale,x,y}` and selected node ID persist per campaign within the session. The conversation's draft and scroll anchor persist per campaign, unless the user explicitly clears a draft. A new campaign initially stays in `DRAFT`, not `RUNNING`.

## 2. Exact layout proposals

All measures are **CSS px at 100% browser zoom**, not image pixels. These are newly proposed implementation tokens, **not** values already approved by the earlier baseline.

### 2.1 Mission Control wide desktop (viewport width ≥1366px)

| Region | Proposed size | Behavior |
| --- | --- | --- |
| Top bar | `height: 60px` | Fixed to viewport top; primary tab buttons always visible |
| Application padding | `12px` | Applies around workspace; no decorative outer canvas margins |
| Grid columns | `232px minmax(0, 1fr) 296px` | Sidebar / conversation / Mission Dashboard |
| Column gaps | `12px` | Fixed gutters, no splitters in v1 |
| Left sidebar | `232px` | Fixed width; campaign titles truncate with accessible tooltip; settings anchored bottom |
| Center conversation | `min-width: 0` with practical target ≥640px | Chat occupies **all remaining height and width** |
| Right dashboard | `296px` | Collapse button makes column zero-width and chat wider |
| Workspace height | `calc(100dvh - 72px)` | 60px header + 12px lower allowance; panels stretch to viewport bottom |
| Center header | `min-height: 88px`, max naturally based on content | Two-line goal clamp + explicit full goal view |
| Chat feed | `flex: 1; min-height: 0; overflow-y: auto` | Only feed scrolls; never grows main panel past viewport |
| Composer | `min-height: 76px`, growing to maximum 176px | Pinned to bottom; Enter sends / Shift+Enter newline (configurable) |
| Card content max width | `760px` within center, `width: 100%` | Avoid overlong text lines on 4K monitors |

At **1366×768**: 1366 − 24 outer margins − 232 sidebar − 296 dashboard − 24 gaps = **790px** center. It must not drop below 640px unless a browser zoom/accessibility setting increases occupied width. Chat feed remains usable above the composer; long histories scroll rather than push the input outside the viewport.

### 2.2 Research Galaxy wide desktop

- Header and left sidebar retain the same dimensions.
- Graph fills remaining viewport (`min-width: 0`, `min-height: 0`).
- Right selection inspector is a **320px** docked panel, **closed by default** (except when restoring an existing selection).
- Canvas toolbar: overlay `top: 16px`, `left: 16px`; zoom controls `right: 16px`, `bottom: 16px` if inspector closed; move them clear of inspector if open.
- Node target box: `min 168×72px`, maximum `248px` width, accessible focus ring at least 2px. Logical gates are `AND`/`OR` chips at least `64×36px`, with an interactive 44×44px hit area.
- Clicking a node opens inspector without changing zoom. Escape closes inspector; second click on empty canvas deselects; pressing Fit restores current graph bounds, not a fabricated root view.

### 2.3 Responsive breakpoints

| Viewport | Sidebar | Mission Dashboard / node inspector | Main behavior |
| --- | --- | --- | --- |
| `≥1600px` | 248px | Dashboard 312px / inspector 340px | Spacious chat, same visual hierarchy |
| `1366–1599px` | 232px | Dashboard 296px / inspector 320px | **Three-pane full layout** |
| `1100–1365px` | 200px | Dashboard hidden by default as on-demand 320px drawer; inspector overlay 320px | Prioritize chat/graph canvas; toggle visible |
| `900–1099px` | Collapsed rail 64px (names in popover/drawer) | On-demand 320px overlay | Chat fills space; tabs intact |
| `768–899px` | Overlay drawer | Overlay secondary panels | Limited tablet/window mode, best effort; not release target |
| `<768px` | Overlay drawer | Overlay | Non-target mobile viewport; no promise of complete usability in v1 |

Height <720px: reduce ornamental padding and dashboard card spacing; keep composer and graph controls on-screen. Respect browser 200% zoom and scrolling without hiding controls. Never hide the ability to open a collapsed sidebar/dashboard.

**CSS scaffold (normative intent, not a committed component):**

```css
.app { height: 100dvh; display: grid; grid-template-rows: 60px minmax(0, 1fr); }
.workspace { min-height: 0; padding: 0 12px 12px;
  display: grid; grid-template-columns: 232px minmax(0, 1fr) 296px; gap: 12px; }
.conversation { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.feed { flex: 1 1 auto; min-height: 0; overflow: auto; overscroll-behavior: contain; }
.composer { flex: none; }
@media (max-width: 1365px) { .workspace { grid-template-columns: 200px minmax(0,1fr); } }
@media (max-width: 1099px) { .workspace { grid-template-columns: 64px minmax(0,1fr); } }
@media (max-width: 899px) { .workspace { grid-template-columns: minmax(0,1fr); } }
```

The full implementation needs an explicit `galaxy` grid variant and collapse classes; do **not** leave a hidden 296px empty dashboard column at narrower widths.

## 3. Component and interaction contract

### 3.1 Application shell / sidebar

- `AppTopbar`: app mark; two semantic tabs; `Engine Connection` indicator (`connected | reconnecting | disconnected | unknown`); no invented credits balance.
- `CampaignSidebar`: title-only rows, truncation, selection, new campaign, bottom settings. Keyboard: Up/Down to navigate when sidebar focused, Enter selects, Escape closes mobile drawer.
- `CampaignHeader`: campaign name/goal, configuration access, verified mode; pause/resume/stop actions **only** if supported for that campaign schema and actual state. Unsupported commands render disabled with explanation, not a placebo button.
- `ConnectionBanner`: reason and `last synchronized at` timestamp; cached committed snapshot remains readable, flagged stale.

### 3.2 Chat/feed component types

| Component | Minimum content | Interaction / truth source |
| --- | --- | --- |
| `UserMessage` | user text, local/send status | Persist only after server acknowledgement; no duplicate sends on retry |
| `OperatorMessage` | sanitized reply, source/context and timestamp | Can be informational; never claims an action was executed if it wasn't |
| `EngineEventSummary` | event type, sequence/time, safe summary | Derived from committed allowlisted `/events`; no raw hidden payloads |
| `ExperimentResultCard` | experiment ID, DEV metric name, baseline, candidate, delta, direction, verdict if verified | Distinguish local candidate result from global champion; show `—` when missing |
| `HypothesisCard` | hypothesis/node ID, display title if safe, status, phase, refs to committed evidence | Opens Galaxy selection; no arbitrary unapproved source content |
| `CampaignSetupCard` | repo, goal, research mode, metric, evaluator, budget, guardrails, validation | Draft fields editable; `Launch Campaign` enabled only after server validation and explicit confirm |
| `ApprovalCard` | immutable proposal ID+digest, rationale, effect, scope, resource changes, current status | Approve/Reject requires confirmation and idempotent typed command |
| `ControlReceiptCard` | requested action, pending receipt, observed outcome | Don't declare pause/resume complete from HTTP receipt alone |
| `EmptyState` | precise missing capability/data | Not "No experiments" if graph endpoint is disconnected |

**Feed behavior:** auto-follow new messages only if within 80px of bottom; otherwise show a `New events ↓` control. Events must not steal input focus. Keep timestamps but collapse repeated telemetry where possible without hiding sequences. User text and model replies are untrusted: render escaped text/approved Markdown subset; prohibit raw HTML/script. Technical details and diffs may open a secondary view, but **not** a second agent chat.

**Important compatibility constraint:** the existing `operator-ui` Pi extension has a **read-only Codex chat** that can propose only a pause intent, with ephemeral thread and no durable campaign conversation. A real, persistent web orchestrator chat with setup and steering is **new backend work**, not currently proven by that adapter. Until built, label read-only conversation mode explicitly and disable unsupported composer intents.

### 3.3 Mission Dashboard

Display only fields available for the selected schema:

1. Campaign identity + goal if present;
2. Status with raw Engine value accessible in details; distinction between `PAUSED_USER`, `PAUSED_INFRA`, `PAUSED_NEEDS_INPUT` matters;
3. Current stage/phase **with source label**: legacy Engine stage vs v2 node phase. Never show v2 phase as a global campaign stage;
4. Experiment/attempt counts and budget by **separate units** (`agent_calls`, `evaluator_calls`, `evaluation_units`, `model_tokens`, experiments) if available;
5. Verified DEV baseline, champion and target with metric name and `maximize/minimize` direction (if exposed);
6. Active node count, pending approvals, recent safe event if available.

No ungrounded percent-progress bar. A finite budget ratio is allowed only when both used and cap are known and same unit. Entire dashboard collapses; expand/fold state persists.

### 3.4 Research Galaxy node inspector

Shows `id`, safe title or ID fallback, `phase`, `status`, `ready`, exact `blockers`, graph revision and node revision. Displays verified measurements/budget/evidence **only if explicitly provided** by safe projection. For a logical group, inspector shows `AND/OR`, owner, members, satisfaction policy and verified member counts. It **must not** show meaningless node budget/score where absent. `Open raw evidence` only via allowlisted safe link/descriptor; never disclose evaluator secrets, validation/HOLDOUT data.

## 4. Galaxy graph semantics — canonical model, not an illustration

Code baseline: [`src/bananloop/graph_store.py`](https://github.com/ObiVanBanan/BananLoop/blob/6c89975870b0da8060146953faea966e203ba857/src/bananloop/graph_store.py).

**Existing canonical entities:** `research_nodes` (`node_id`, `phase`, `status`, revision), `research_edges` (`REQUIRES`, `DERIVED_FROM`, `RELATED_TO`), and `graph_groups` (`AND`/`OR` with `owner_node_id` and members). The UI's visual nodes named Goal/Hypothesis/Experiment are **display roles**, not assumed backend `node_type` variants. Current schema defines five `phase` values: `EXPLORATION`, `METRIC_DISCOVERY`, `OPTIMIZATION`, `VERIFICATION`, `CONSOLIDATION`; status set: `PENDING`, `READY`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`. The backend's readiness is authoritative.

### 4.1 Edges and grouping

- `REQUIRES` — directed dependency arrow from **prerequisite source** to dependent target; can block readiness.
- `DERIVED_FROM` — directed provenance relation (dashed). Do **not** imply scheduling prerequisite.
- `RELATED_TO` — informational relation (dotted). Do **not** imply gating.
- `AND` group — **all** members must be verified completed for the owner to become ready.
- `OR` group — **at least one** member must be verified completed for the owner to become ready.
- Graph may contain shared members/dependencies, independent parallel subgraphs, and disconnected areas. Never attach unrelated branches to an invented root for aesthetics.

Render an AND/OR group as an explicitly labelled **visual gate** with lines `members → group gate → owner`. Gate ID is **UI-only**, for example `visual:group:<canonical_group_id>`; it must not be passed to Engine as a `node_id`. Store `canonical_group_id` separately. A group/edge modification must update the whole graph from the authoritative revision.

**Verified completion note:** in `GraphRepository.scheduling_snapshot()`, group satisfaction uses nodes with `COMPLETED` status **and** `_has_verified_completion(payload_json)`. Therefore do **not** mark a group satisfied merely by counting visually green `COMPLETED` nodes. Request `satisfied`, `verified_member_count`, `total_member_count` from server read projection.

### 4.2 Layout & interaction

- Default layout: layered dependency graph (Sugiyama/ELK-like); independent subgraphs are grouped but never connected by fake edges. One-time auto-fit on first load; preserve manual viewport thereafter.
- Nodes: clear phase badge + state badge; status not represented by color only. `PENDING` visually neutral; `READY` ready-not-running; no generic `active` mapping that conflates queue lease with graph state.
- Cursor hover: title/id, phase, status, blockers. Click/Enter: inspector. Multi-select and graph mutations by dragging are **out of scope**.
- Canvas: pan via background drag, zoom by wheel with Ctrl or explicit toolbar (to avoid hijacking scrolling), zoom bounds `0.35–2.5`, Fit Graph and Reset selection. Keyboard: Tab through focusable nodes, Enter inspect, `+/-` zoom when canvas focused, Home fit.
- Graph changes: on `GRAPH_MUTATED`, fetch full safe projection, compare `graph_revision`, apply new topology atomically; preserve selection when node still exists. On deletion/no access, clear selection with explanation.
- At 300 nodes or more, use culling/collapse based on **actual** subgraphs; the navigator shows real counts and never discards data silently. 300 is a performance test scenario, not a claimed backend graph size.

### 4.3 Graph unavailable states

- Legacy v1 campaign: show `Research Galaxy requires v2 graph data`; do not synthesize v2 graph from v1 hypotheses unless explicitly labelled as *alternative list*.
- v2 current endpoint with no edges/groups: show **Node Overview (partial graph)**, not a fabricated AND/OR graph.
- Empty graph: explain `No research nodes recorded` if server confirmed empty; errors/disconnected have separate states.
- Unknown node status/phase: display raw safe value, `unknown` styling and no unsupported actions.

## 5. States and permitted actions

| State | Mission Control | Research Galaxy | Actions |
| --- | --- | --- | --- |
| No campaign selected | Choose/new campaign | No graph placeholder | List/select/create (once implemented) |
| Draft incomplete | Setup card identifies missing fields | Not available | Edit/validate; launch disabled |
| Draft ready | Review full configuration | Not available | Explicit `Launch Campaign` after server confirmation |
| Running v1 | Feed+dashboard+DEV metric | V1 notice/alternative list | Typed v1 controls only if supported |
| Active v2 graph | Feed with committed event summaries; dashboard may be partial | Actual nodes/edges/gates when projected | Read; v2 node/pivot actions only with valid capability |
| Awaiting approval | Immutable proposal review card | Highlight affected IDs only if supplied | Approve/reject using proposal ID and digest; no hidden auto-pivot |
| Paused | Cause-specific label | Show canonical node/campaign statuses | Resume only via supported guarded command |
| Completed/goal met/stopped | Final committed summary | Read-only last graph | No misleading "running" controls |
| Failed/disconnected/stale | Last committed data + timestamp | Last graph + stale overlay | Retry read/reconnect, no success assumption |
| Partial telemetry | `Not available` on specific fields | Unknown score/budget explicitly absent | No inferred metrics |

Actions have phases `idle → awaiting_user_confirmation → submitting → receipt_recorded → observing_committed_state → confirmed | rejected | unknown`. Transport timeout never means command failed or succeeded. An HTTP `409` conflict means reload state and invalidate the pending confirmation. Mutation requests use server-required `Idempotency-Key` and revision/CAS checks. Read-only browsing must work without a live model account.

## 6. Visual design system

| Token | Proposed value | Use |
| --- | --- | --- |
| `--space` | `#101522` | Overall dark background (baseline anchor) |
| `--panel` | `#141C2B` | Standard panel |
| `--panel-raised` | `#1B2537` | Message/inspector card |
| `--stroke` | `#334254` | Low-emphasis separator |
| `--gold` | `#F5C842` | Banana Gold: selection and critical accents |
| `--text` | `#F0EFE5` | Primary text |
| `--muted` | `#8DA3B9` | Secondary text (baseline Steel) |
| `--success` | `#53D69A` | Verified completion only; with text/icon |
| `--danger` | `#F06B75` | Error/blocked; with text/icon |
| `--warning` | `#E9B95C` | Pending/review caution |
| `--focus` | `#FFE78C` | Keyboard focus ring |

Success/error/focus colors are **v1 proposals**, not yet approved baseline colors. Theme should pass WCAG AA: normal text 4.5:1 contrast, large 3:1, distinguish controls without color alone. Avoid body text in pixel font. Use locally bundled/license-compliant pixel face for very short labels and system sans (e.g. `Inter` if bundled) for chat; technical IDs in mono. No fonts depend on Google Fonts CDN at runtime.

- 8px spacing scale; card radius 8px, panel radius 12px; 1px understated edge + occasional short golden corner detail.
- Default body 14–15px, chat 15px/1.55, metadata 12px minimum; mission title 20–24px.
- Animations: navigation 180–260ms; status pulses ≤1.5s and only while meaningfully changing; graph new-node animation 180–300ms. Disable under `prefers-reduced-motion: reduce` and a user intensity setting.
- Space artwork must not degrade frame rate/readability. Animate CSS transforms/opacity rather than large repaints; use SVG/CSS lines and decorations selectively.

## 7. Data/API compatibility matrix (verified vs needed)

Verified from [`docs/api.md`](https://github.com/ObiVanBanan/BananLoop/blob/6c89975870b0da8060146953faea966e203ba857/docs/api.md), [`application.py`](https://github.com/ObiVanBanan/BananLoop/blob/6c89975870b0da8060146953faea966e203ba857/src/bananloop/application.py), [`operator_snapshot.py`](https://github.com/ObiVanBanan/BananLoop/blob/6c89975870b0da8060146953faea966e203ba857/src/bananloop/operator_snapshot.py), [`api.py`](https://github.com/ObiVanBanan/BananLoop/blob/6c89975870b0da8060146953faea966e203ba857/src/bananloop/api.py) and [`operator-ui/README.md`](https://github.com/ObiVanBanan/BananLoop/blob/6c89975870b0da8060146953faea966e203ba857/operator-ui/README.md).

| Requirement | Existing evidence in code | v1 implementation decision |
| --- | --- | --- |
| Read snapshot | `GET /v1/campaigns/{id}/snapshot`; v1 DEV metric/budget/history; v2 sparse node projection | Use as-is for supported fields; schema-discriminate |
| Read committed events | `GET /events`, `GET /events/stream` with sequence cursor; payload allowlist | Use SSE and after-cursor reconnect; re-read snapshot on event |
| Research nodes | v2 `GET /graph` returns `{schema_version,campaign_id,status,graph_revision,nodes:[node_id,phase,status,revision,ready,blockers]}` | Render **partial node overview** immediately |
| Graph connections and groups | Persisted canonical `REQUIRES/DERIVED_FROM/RELATED_TO`, AND/OR; **not returned** by current public graph projection | **GAP-01** add sanitized read-only topology endpoint/extended projection |
| Global v2 goal/metrics/budgets | Present in v2 spec/ledgers; not in current v2 public snapshot projection | **GAP-02** bounded safe campaign summary with units and verified source labels |
| Campaign listing | CLI `bananloop campaigns list --json`, but no equivalent HTTP route in current local API | **GAP-03** read-only listing route from registry; names may need durable metadata |
| Campaign creation/launch | CLI init/goal exists; no HTTP draft/validate/launch workflow | **GAP-04** draft validation and explicit controlled launch boundary |
| Conversation | Pi terminal Codex read-only chat exists, **ephemeral** and limited to pause proposal | **GAP-05** durable one-chat-per-campaign service and explicit permission/intent contract |
| v1 pause/resume/stop | HTTP typed control receipts; execution not performed in request thread | Show submitted/observing/confirmed; verify actual state; supervisor is separate |
| v2 global controls | `CampaignApplication.control` explicitly rejects v2 graph schema | **GAP-06** no v2 global pause/resume/stop buttons until backend contract; use guarded per-node commands where appropriate |
| v2 architecture pivot | HTTP v2 proposal/decision/execution endpoints; proposal IDs/digests | Approval card uses immutable identity; no automatic child execution merely from approve |
| Provider plan/quota | No verified general API for Codex/ChatGPT subscription quotas in these files | Show `Unknown` or omit; do not invent |

### 7.1 Proposed read-only graph projection contract, not currently implemented

Introduce a versioned, sanitized DTO (e.g. `GET /v1/campaigns/{id}/graph/view`) rather than bypassing the safe existing `/graph` endpoint. This is a **proposal** to be validated in RPDI-01 and does not expand evaluator data exposure.

```json
{
  "schema_version": 1,
  "campaign_id": "c-...",
  "graph_revision": 42,
  "nodes": [{"id": "h-1", "phase": "OPTIMIZATION", "status": "READY", "revision": 4,
             "ready": true, "blockers": [], "display_title": null}],
  "edges": [{"source_id": "h-0", "target_id": "h-1", "type": "REQUIRES"}],
  "groups": [{"id": "g-1", "owner_node_id": "h-1", "kind": "AND",
              "member_node_ids": ["h-0"], "satisfied": false,
              "verified_member_count": 0, "total_member_count": 1}],
  "as_of": "2026-11-07T00:00:00Z"
}
```

Example IDs/numbers are **schema examples, not BananLoop telemetry**. `display_title` is `null` unless safe projection exists. The backend must calculate group `satisfied` from verified completion, not the frontend. All arrays must be from a consistent SQLite revision; never give a page where groups refer to invisible nodes unless explicitly flagged as truncated. Use size limits, pagination if necessary, and no raw node `payload_json`. Keep scope strictly read-only.

### 7.2 Proposed typed front-end view-model

```ts
type DataOrigin = 'committed' | 'local-draft' | 'synthetic-prototype';
type LoadState = 'loading' | 'ready' | 'partial' | 'stale' | 'disconnected' | 'error';
type MetricView = { name: string; direction: 'maximize'|'minimize'; unit?: string;
  baseline?: number|null; champion?: number|null; target?: number|null; source: 'DEV' };
type NodeView = { nodeId: string; phase: string; status: string; revision: number;
  ready: boolean; blockers: string[]; title?: string|null };
type GraphView = { campaignId: string; graphRevision: number; nodes: NodeView[];
  edges: Array<{ sourceId: string; targetId: string; type: string }>;
  groups: Array<{ groupId: string; ownerNodeId: string; kind: 'AND'|'OR';
    memberNodeIds: string[]; satisfied?: boolean }> };
```

Never silently map an absent `champion` to zero, or `READY` to `RUNNING`.

## 8. Security and operational constraints

- Bind local API to loopback, follow bearer-token mode when configured; UI requests must not leak token into URLs/logs/browser history. Do not assume `localhost` means unauthenticated requests are safe. Protect mutations from cross-site request forgery; CORS same-origin by default.
- Read committed snapshots/events; never read SQLite directly from JavaScript or send raw source code/secrets to model chat. Backend handles authorized write commands.
- Host controls are typed, idempotent, revision-checked and permission-gated. Read-only Codex can **propose**, not execute. Never combine an LLM's natural-language answer with automatic `graph/commands` calls.
- `validation` and `HOLDOUT` scores remain private. Display DEV evaluation data only when backend safe projection explicitly allows it. Pivot decisions must match immutable proposal identity/digest.
- Redact sensitive file paths/identifiers in shared logs and error telemetry; sanitize all Markdown. Support no-network/offline operator mode for already committed data.
- `bananloop ui` startup command/port are **not yet shipped**. Preferred first delivery is a standalone local frontend served by a small Python/static asset integration or development server, with a later CLI convenience command. Final selection is an implementation decision, not assumed current capability.

## 9. Acceptance criteria / QA fixtures

1. **1366×768:** sidebar, chat, dashboard all visible; composer remains bottom-pinned; no horizontal page overflow; central feed alone scrolls.
2. **1024×768:** sidebar rail, dashboard on-demand; chat and selected campaigns reachable without hidden controls.
3. **1920×1080:** full-width workspace, capped readable card line length, no excessive empty page canvas.
4. **Mission Control:** long chat, new messages when scrolled up, pending confirmation, backend-rejected action, stale event stream, disconnected state, duplicate event replay. Composer text preserved on tab switch.
5. **Research Galaxy:** independently test REQUIRES, DERIVED_FROM, RELATED_TO, AND(all), OR(any), shared dependency, parallel branches, disconnected components, empty graph, partial projection, unknown phase, >300 synthetic nodes. Layout never adds canonical edges.
6. **Verified state:** `COMPLETED` without trusted verification does not satisfy an AND/OR gate. Unknown metric/budget remains unknown; no false champion claim.
7. **Actions:** stale `graph_revision` or proposal digest -> conflict, confirmation invalidated and state refetched. Repeated submit uses same idempotency key and cannot create duplicate effects.
8. **SSE:** ordered seq, reconnect via last cursor, drop duplicates and refresh snapshot after graph mutation. Long-running backend work never blocks UI.
9. **A11y:** keyboard focus, tab semantics, dialogs trap focus, Escape restores focus, reduced motion, 200% zoom, AA contrast and status text not color-only.
10. **Safety:** fail-closed read-only mode without required API; no validation/HOLDOUT leakage; prototype synthetic data is always clearly labelled and never mixed with connected campaign data.

Required outputs for each UI RPDI: concise design diff, UI screenshots at three target viewports, state-test fixtures, API DTO compatibility tests, accessibility smoke, updated risk register, and documented backend gaps.

## 10. Implementation plan by RPDI vertical slices

| Slice | Deliverable | Hard DoD / dependency |
| --- | --- | --- |
| **RPDI-UI-00** | Freeze this spec, baseline screenshots, UI fixtures and repository contract inventory | Assert code SHA; scope and unknowns documented |
| **RPDI-UI-01** | Interactive *synthetic* prototype of Mission Control + Galaxy shell | 1366×768 and 1024×768 validated; banner `SYNTHETIC PROTOTYPE`; interactions work, no fake API calls |
| **RPDI-UI-02** | Read-only connected Mission Control: selected campaign, real snapshot and SSE | REST reconnect/idempotent read tests; zero fabricated metrics; v1/v2 schema discrimination |
| **RPDI-UI-03** | Read-only graph-view DTO and real Research Galaxy | Server-sanitized edges/groups/verification; graph fixtures; no evaluator leakage |
| **RPDI-UI-04** | Durable approvals and typed control UX | v1 control receipt/observation, v2 pivot identity/digest and node guards; unsupported v2 globals disabled |
| **RPDI-UI-05** | Campaign list + create/validate/launch workflow | Registry-backed server contract, immutable validated spec, explicit launch; no UI-only execution |
| **RPDI-UI-06** | Web operator/orchestrator conversation with durable campaign history | Single conversation per campaign, strict read-only vs control boundary; permissioned intents; context redaction |
| **RPDI-UI-07** | Hardening / perf / accessibility / local launch packaging | CI/Playwright viewport states, SSE recovery, 300-node graph stress, security review and docs |

**Sequencing:** RPDI-UI-01 may be built in parallel with investigation of GAP-01/02/03. UI-02 and UI-03 can ship as **read-only** before UI-04–06. Do not prematurely bind the frontend to low-level internal SQLite tables. The interactive prototype must keep its fixture source separate from the API client so a change in backend contracts does not require redesigning UI structure.

## 11. Design sign-off checklist

- [ ] Approve proposed px layout, widths, minimum interaction sizes and responsive breakpoints.
- [ ] Approve dark tokens + success/error colors and font families after screenshot contrast review.
- [ ] Agree that partial v2 graph is presented honestly until GAP-01 is closed.
- [ ] Confirm that v2 global controls remain absent until explicit backend contract exists.
- [ ] Validate one-chat-per-campaign persistence and permissions in a separate architecture RPDI before declaring the chat production-ready.
- [ ] Use the two approved uploaded PNGs as references throughout RPDI-UI-01; do not copy illustrative graph and metrics into live data.

## Appendix: source map and discrepancy notes

- **Supplied reference design** (`BananLoop-UI-Design-Spec.md`, 2026-11-07): approved navigation, sidebar simplicity, chat centrality, compact dashboard, AND/OR semantics, visual language, no Live Agent Chat, desktop min 1366×768.
- **Current code** (`main@6c89975`, 2026-11-05): `api.py`, `application.py`, `operator_snapshot.py`, `graph_store.py`, `operator-ui/README.md`, `docs/rpdi/rpdi-07-node-scheduler.md`.
- **Critical discrepancy #1:** a public graph *read* exists but omits persisted topology; the approved Galaxy requires a new safe read projection.
- **Critical discrepancy #2:** v1 status/metric snapshot is rich but v2 public projection is sparse; dashboard must gate fields by schema.
- **Critical discrepancy #3:** the terminal operator chat is currently ephemeral, scoped and read-only; the envisioned web chat requires a new authorized persistence/control design.
- **Critical discrepancy #4:** HTTP `/commands/{pause|resume|stop}` is not an unconditional v2 campaign-control API; current v2 global control is rejected by the application service.
- **Critical discrepancy #5:** the existing `docs/VISUAL_DIRECTION.md` relates to README/marketing artwork and includes a mascot; the *later approved application UI baseline* explicitly excludes mascots inside the product.

**North star:** Mission Control = truthful conversation and operator action; Research Galaxy = faithful graph observability; Mission Dashboard = compact committed campaign state. UI follows Engine, never the reverse.