# RPDI-13 evidence register

**Status: INCOMPLETE.** This register records the current implementation evidence;
it does not satisfy the RPDI-13 DoD or authorize a release.

## Implemented evidence

- `GET /v1/campaigns` is bounded and paginated; API coverage checks redaction and
  that listing does not change the active campaign.
- Graph and durable event views use existing application projections and cursor
  recovery behavior.
- `GET /v1/campaigns/{campaign_id}/research` reads through the read-only
  `ResearchLedger` path, exports only PUBLIC_DEV scopes, caps a projection at
  200 evidence rows, and omits job/request/output identifiers and affected paths.
  API tests cover the no-ledger-table read path, campaign isolation, unverified
  labels, invalid scopes, and private-field redaction.
- `GET /v1/campaigns/{campaign_id}/observability` reads campaign-filtered queue
  and budget data through read-only SQLite transactions. It also computes the
  bounded scheduler plan reason from the v2 graph, persisted budget snapshot,
  and at most 10,000 active campaign jobs with 64 KiB per-request and 1 MiB
  aggregate JSON byte caps; node/job identifiers stay internal. The scheduler
  view carries campaign and campaign-filtered worker event cursors for its
  source records.
  API tests reconcile READY/LEASED counts and budget values, cover
  `BUDGET_EXHAUSTED` and `WAITING_FOR_WORKERS`, assert campaign isolation,
  omit private worker data, mark incomplete required budget dimensions
  unavailable, and cover legacy or uninitialized campaigns as unavailable
  rather than empty. Missing queue/event authority and active request payloads
  above the bounded read limit also produce unavailable scheduler state.
  Queue recovery counts use the latest 10,000 events for that campaign (rather
  than project-global event IDs) and return a campaign-filtered worker cursor;
  regression coverage injects over 10,000 events into a different campaign.
  A second stress assertion injects 10,001 unrecognized events into the same
  campaign and verifies they consume the event window before known types are
  counted. The observability API regression creates a real expired
  pre-dispatch lease, a dispatched lease that expires to UNKNOWN, and a trusted
  reconciliation; it asserts the corresponding durable recovery-event counts
  and final job state. Both recovery and evaluator lifecycle API regressions
  passed locally, as did the Playwright/axe Dashboard suite with an assertion
  for the evaluator aggregate display.
- `GET /v1/campaigns/{campaign_id}/lifecycle` projects bounded pivot, child,
  phase, evaluator verification, and protocol handoff receipts. Tests cover
  read-only behavior and omission of pivot paths, request keys, and verifier job
  and role IDs. It also exposes campaign-scoped totals for verifier APPROVE /
  REJECT, integration PASS / FAIL, and evaluator run receipts, derived from the
  authoritative receipt tables; no score or run payload is included. The API
  regression reconciles both verifier decisions and both integration outcomes
  against persisted receipts, and proves a receipt for a different campaign is
  excluded. The Dashboard renders these as bounded aggregate counts.
- Dashboard asynchronous reads are discarded when campaign or research-scope
  selection changes. Static-asset tests cover labels, landmarks, live regions,
  responsive breakpoints, focus-visible styling, local-only assets, and
  non-persistent token handling.
- `bananloop campaigns inspect --campaign-id ID --json` now uses the shared
  `CampaignApplication` projections for safe campaign summary, snapshot, graph,
  PUBLIC_DEV scope index, observability, and lifecycle. Its output is compared
  with API projections in `tests/test_rpdi13_integrated_acceptance.py`.
- The integrated acceptance test runs a real deterministic Exploration worker,
  ingests a PUBLIC_DEV finding, reads Research Intelligence through the API,
  then performs a verified protocol handoff to a separate Optimization child
  through pivot API routes. The child executes its frozen DEV evaluator,
  validation baseline and candidate runs, and integration verifier; replay
  returns the same durable receipt. API and CLI child graph projections agree
  on the completed CONSOLIDATION node. The RPDI-12 handoff fix copies only
  digest/size-bound evaluator bundle, manifests, and case inputs into the child
  asset store. The direct evaluator-child proof is also covered by
  `tests/test_v2_pivot_saga.py`. With `BANANLOOP_BROWSER_LIVE_E2E=1`, this same
  integrated test launches Chromium against its live API server and verifies
  the actual child campaign's Full profile, completed CONSOLIDATION graph node,
  and PASS integration receipt in the Dashboard without API route mocks.
- On code head `cb60f15`, the full Python suite passed with one skipped test.
  `node --check`, OpenAPI JSON serialization, and `git diff --check` also passed.
  This is local evidence, not exact-head GitHub CI.
- After adding scheduler wait-reason projection, the full Python suite passed
  again with one skipped test. After hardening incomplete budget dimensions,
  the full Python suite passed again with one skipped test; `node --check`,
  OpenAPI JSON serialization, and `git diff --check` passed on the current
  working tree. This remains local evidence and is not exact-head GitHub CI.
- After adding the worker event cursor and bounding active request reads, all
  `tests/test_api.py` cases and the full Python suite passed; the suite includes
  one skipped test. The independent code review verified the read-only,
  campaign-scoped cursor and byte-bounded projection. This remains local
  evidence and is not exact-head GitHub CI.
- The campaign-scoped queue event-window regression, the integrated
  Exploration/Research Intelligence/pivot-child test, and the full Python suite
  passed after adding `campaigns inspect`; the suite includes one skipped test.
  The CLI parser route also passes the integrated test. `node --check`, OpenAPI
  serialization, and diff checks also pass locally.
- A pinned Playwright 1.64.1 + axe-core 4.14.0 browser test and separate
  Chromium CI workflow now exercise keyboard focus, campaign switching while a
  read is pending, `410` event recovery, durable command receipts, axe WCAG 2.1
  A/AA checks, and 320 px overflow. The browser workflow also runs the
  Exploration → Research Intelligence → Optimization/Verification acceptance
  test with a live Chromium probe against the real API server. Local Chromium
  and live integrated UI checks pass; GitHub exact-head CI has not run. The
  320 px check found and verified a CSS overflow fix.
- After adding RPDI-12 frozen asset transfer and extending integrated acceptance
  through Optimization evaluation and independent integration verification, the
  full Python suite passed locally with one skipped test. The complete pivot
  saga module also passed with temporary HOME and loopback access. Local
  Chromium/axe browser test passed again; `git diff --check` is clean. These are
  local results, not exact-head GitHub CI.
- On exact RPDI-13 head `e4568df2668426366dbb4c65ecb3c06cb8173de7`, hosted
  test run [41885087606](https://github.com/ObiVanBanan/BananLoop/actions/runs/41885087606)
  passed on Ubuntu Python 3.11/3.12/3.13 and Windows Python 3.12. Windows
  reported 757 passed, 1 skipped, and 7 subtests passed.
- Exact-head browser run
  [41885087550](https://github.com/ObiVanBanan/BananLoop/actions/runs/41885087550)
  and critical rule mutation smoke
  [41885087540](https://github.com/ObiVanBanan/BananLoop/actions/runs/41885087540)
  passed. This head fixes the Windows-only timing failure where the observability
  fixture's 60-second lease expired while it prepared 10,001 events; the fixture
  now uses a one-hour lease. Manual screen-reader evidence remains outstanding.

## Accessibility observation

A local browser smoke check at a 400 px viewport showed a single-column layout
with vertical scrolling. The automated Chromium suite additionally verifies
Tab order/focus visibility, interaction states, axe WCAG 2.1 A/AA, and a 320 px
no-overflow layout. These checks do not constitute manual screen-reader
sign-off.

Playwright and axe-core are pinned in `package-lock.json`. Exact-head browser CI
passed at `e4568df`. It also covers focus while Connect waits for the campaign
API, focus moving to the loaded campaign selector, stale campaign warnings
clearing after a switch, and focus returning to the token field after Clear
token. These automated checks do not constitute manual screen-reader sign-off.

## Remaining DoD and release evidence

- Record the manual screen-reader walkthrough on exact head `e4568df` for
  Connect, campaign selection, lifecycle status, live updates, controls,
  accessible labels, and focus order. No manual result is recorded yet.
- Exact-head Linux/Windows Python CI, browser CI, mutation smoke, and technical
  review are recorded above; automated accessibility checks cannot replace the
  pending human screen-reader evidence.

Independent blind benchmarks and Closed Research Preview remain separate gates.
