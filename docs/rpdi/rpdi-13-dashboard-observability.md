# RPDI-13 Dashboard and observability contract

**Status:** acceptance contract. Implementation remains incomplete until every
acceptance row below has implementation evidence, passing tests, review, and
exact-head CI.

## Decision

The Dashboard is a local operator client of BananLoop's versioned HTTP API. It
does not read or mutate SQLite directly, run campaign workers, or make
research/evaluation decisions. Existing application services remain the only
authority for campaign state. The Dashboard may display durable operator
controls and submit pause, resume, and stop through the API's idempotent
command routes.

Observability is generated from persisted Core state and explicitly safe
projections. It helps an operator understand progress, resource use, recovery,
and blocked work. It is not a telemetry export, a source of evaluator truth,
or a path around campaign policy.

## Dashboard requirements

- Discover and select campaigns through a new bounded, paginated,
  application-backed `GET /v1/campaigns` read model. It returns opaque campaign
  IDs and safe summaries for both legacy v1 and v2 campaigns. Never serialize a
  registry entry or expose its filesystem state path, raw goal text, request
  key, or goal fingerprint. Order pages deterministically by creation time and
  campaign ID; opaque continuation tokens preserve that order, and listing
  never changes the active campaign. v1 is always labeled legacy and is never
  inferred to be v2 Full. Show mode, status,
  `schema_version`, persisted profile, `flag_hash`, `approved_spec_hash`,
  active phase, and last-update time. A v2 campaign is labeled Full only when
  those persisted fields identify the canonical Full profile and its exact
  approved hashes; never infer Full from a partial list of enabled flags.
- Show the canonical AND/OR research graph, including phase, status,
  dependency readiness, blockers, and completion evidence references. The view
  must distinguish `REQUIRES` from nonblocking knowledge edges and must not
  infer readiness from worker-authored data.
- Show the event timeline from the durable `events.seq` cursor. Reconnection
  with `Last-Event-ID` resumes without losing or duplicating committed events.
  Event reads return the current cursor and retention floor. A cursor below the
  floor returns `410` with the safe floor/current cursor and requires a fresh
  snapshot before streaming again; a cursor ahead of the current sequence
  returns `409` with the safe current cursor. The Dashboard resynchronizes from
  a fresh snapshot and does not silently skip or invent events. Completion
  evidence references are opaque IDs, never filesystem paths.
- Show exploration findings and causal-memory records only from
  `ResearchLedger.export_knowledge` PUBLIC_DEV projections. Keep role findings
  marked unverified and causal findings marked unverified inference, with safe
  source campaign/node/protocol provenance and contradiction links. Never show
  raw worker results. Show optimization and evaluator-verification state from
  Core receipts and graph projections, never from agent summaries.
- Show child campaigns, pivot proposals, owner decisions, and frozen evaluator
  protocol handoff without suggesting that approval alone executed a child.
- Submit pause, resume, and stop only through typed API commands with durable
  idempotency receipts. Replays render the same receipt; conflicting reuse is
  reported as a conflict. A successful response means operator intent was
  recorded, not that a supervisor acted on it or that campaign execution
  changed. Show pending/accepted intent until a later durable campaign event
  confirms the state change. The Dashboard never invokes a CLI or worker
  process.
- Clearly represent loading, stale, disconnected, unauthorized, and server
  error states. A transport failure must never look like a stopped or completed
  campaign.
- Support keyboard navigation, screen-reader labels, and a narrow viewport;
  core status and safety indicators cannot depend on color alone.

## Observability requirements

- Provide bounded, campaign-scoped views of queue depth/state, worker lease and
  fence state, retries/recoveries, scheduler wait reasons, phase transitions,
  evaluator-verification state, and budget spent/reserved/available by
  dimension. New API read models obtain authoritative records through
  `CampaignApplication`, `Registry`, and Core authority services; the browser
  does not read campaign, registry, queue, budget-ledger, research-ledger, or
  evaluator-authority databases directly.
- Derive counters and durations from durable receipts/events or explicitly
  labeled live observations. Every value carries source (`PERSISTED` or
  `OBSERVED`), capture time, and relevant event cursor; distinguish unavailable
  values from zero and show staleness for observations.
- Keep metric labels bounded. Do not use campaign IDs, node IDs, paths, prompts,
  case IDs, or error text as global metric labels.
- Expose no agent prompts, raw worker outputs, source diffs, credentials,
  secret values, evaluator source, validation/holdout inputs or scores, or
  hidden case identifiers. Research findings stay marked unverified; metrics
  cannot convert them into trusted evaluation evidence.
- Do not send telemetry to a third party. Any future export requires a separate
  explicit protocol and owner choice.
- Serve the Dashboard from the API's same origin. Preserve the existing
  loopback-only API boundary, optional bearer-token
  behavior, event allowlists, `Cache-Control: no-store`, and exact campaign
  scoping. Dashboard assets must not introduce a remote script, analytics
  beacon, or credential-bearing URL. Never put bearer tokens in query strings
  or persistent browser storage. Token entry/bootstrap is in-memory and never
  propagates through a URL. Reject wildcard CORS and validate Host for all
  requests. For browser mutations with an `Origin` header, require it to match
  the API origin and enforce a same-origin CSRF defense. Requests without an
  `Origin` remain supported for CLI/local API clients; when bearer-token mode
  is enabled they still require the bearer token.

## Compatibility and architecture

- Keep API-first behavior: browser views and controls consume documented API
  routes; they do not query SQLite or duplicate application logic.
- Extend the API with explicit versioned read models where existing snapshots,
  graph, campaign discovery, event, or observability data are insufficient.
  Unknown fields remain rejected on mutation routes; read-model extensions are
  documented in OpenAPI. Campaign discovery is bounded and paginated; campaign
  observability and research/evaluator summaries are campaign-scoped, redacted,
  timestamped, and carry their authoritative source/cursor.
- Existing CLI commands and the v1 Engine/SQLite adapters remain usable. The
  dashboard does not reinterpret legacy v1 campaign data as v2 Full.
- Dashboard and observability feature flags remain hash-bound to the campaign.
  A view must show the actual profile and flag hash; it must not label a
  diagnostic or partially enabled campaign as Full.

## Acceptance evidence

Current partial evidence and remaining gaps are tracked in the
[RPDI-13 evidence register](../reviews/rpdi13-evidence-register.md). That
register is not a substitute for the acceptance rows below.

| Requirement | Required evidence |
| --- | --- |
| API-only client | Tests prove Dashboard has no store/SQLite access and every read/control uses the API/application boundary |
| Campaign discovery and status | API/OpenAPI, app, CLI, and Dashboard tests show bounded deterministic pagination, v1/v2 summaries, safe-field omission, no active-campaign mutation, explicit selection, and correct mode, persisted profile, flag hash, approved-spec hash, and status |
| Graph and evidence view | Tests cover AND/OR readiness, dependency kinds, provenance, stale snapshots, and unverified Research Intelligence labels |
| Durable event stream | Tests cover `events.seq` cursor replay, reconnect, below-floor `410`, ahead-of-head `409`, resnapshot recovery, ordering, and duplicate suppression |
| Controls | Pause/resume/stop use idempotency keys and durable receipts; UI distinguishes recorded intent from supervisor execution; conflicts and authorization failures are visible and safe |
| Observability | Tests reconcile displayed queue, phase, recovery, evaluator, and per-dimension budget values against authoritative source records and verify source, cursor, timestamps, and staleness |
| Privacy and security | Negative tests cover prompts, raw outputs, diffs, paths, secrets, evaluator source, validation/holdout, hidden case IDs, artifact paths, XSS, URL/persistent-token leakage, CORS/Host/Origin/CSRF, unauthorized reads, and remote telemetry |
| Compatibility and accessibility | Legacy/v2 API fixtures, CLI smoke, automated axe/keyboard checks, and narrow viewport checks pass. A manual screen-reader walkthrough is explicitly not required for RPDI-13 under the 2026-11-05 owner-approved scope decision below |
| Integrated acceptance | E2E demonstrates Exploration → Research Intelligence → Optimization/Verification → pivot child in Full profile, with API/Dashboard/CLI views agreeing on durable state |
| DoD | Exact-head CI on supported Linux/Windows Python versions, technical review, and updated evidence register |

## Owner-approved accessibility scope adjustment (2026-11-05)

For this local Research Preview dashboard, the project owner waived the *manual*
screen-reader walkthrough as an RPDI-13 acceptance gate. This is a deliberate
acceptance-scope reduction, **not** a passing manual accessibility test and
not a claim that assistive-technology compatibility was verified. Retain the
existing screen-reader labels, keyboard navigation, focus management, automated
Playwright/axe WCAG A/AA checks, and responsive viewport tests. No accessibility
code or tests are to be removed as part of this waiver. Record the residual risk:
real screen-reader behavior (including live announcements and navigation) is
unverified, and revisit it before making public accessibility claims or when
shipping a broadly available user-facing dashboard.

The independent blind Optimization and Exploration benchmarks and Closed
Research Preview approval remain separate release gates. Dashboard readiness
or a green RPDI-13 CI run cannot claim those gates have passed.
