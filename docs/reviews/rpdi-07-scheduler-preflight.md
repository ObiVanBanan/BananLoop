# RPDI-07 preflight

## Source and ownership

- Base is `main@70f015bbd7390d9870f6498529f1e8c808e50611` (RPDI-06, PR #123).
- `GraphRepository` remains the only owner of graph revisions, typed node state, and graph events.
- `DurableWorkerQueue` remains the owner of lease, fence, dispatch, unknown-outcome, and claim-time budget reservation.
- `BudgetLedger` remains the only budget authority. Planning snapshots are advisory; queue claim performs the atomic reservation.
- The scheduler is deterministic policy/orchestration only. It does not add a second queue, budget ledger, event log, or phase owner.

## Before / after

| Area | Before RPDI-07 | After RPDI-07 |
|---|---|---|
| Phase/status | v5 stored phase and free-form status; only exploration completion changed them | typed CAS status and phase transitions, transition guards/evidence in the atomic graph event, fail-closed status validation |
| Readiness | AND/OR accepted a caller-supplied completed-ID set; no scheduler readiness query | REQUIRES and groups are evaluated against persisted Core-completed nodes; READY transition rejects unmet dependencies |
| Scheduling | no runnable-node planner | deterministic resource-aware plan; independent nodes in different phases can be selected together |
| Stopping | no scheduler stop result | deterministic continue/wait/all-complete/budget-exhausted/dependency-blocked/no-work/stop-requested result |
| Execution | RPDI-05 exploration adapter enqueued a node directly | adapter checks graph readiness, marks the node READY with CAS, then enqueues idempotently; worker queue still owns dispatch |

## Phase and status guards

Phase progression is per node and independent of legacy Engine stages:

| From → to | Guard | Actor | Resulting status |
|---|---|---|---|
| EXPLORATION → METRIC_DISCOVERY | METRIC_PROPOSAL | CORE | PENDING |
| EXPLORATION → CONSOLIDATION | verified exploration completion API | Core projection | COMPLETED |
| METRIC_DISCOVERY → OPTIMIZATION | PROTOCOL_FROZEN | VERIFIER | PENDING |
| METRIC_DISCOVERY → EXPLORATION | PROTOCOL_REJECTED | VERIFIER | PENDING |
| OPTIMIZATION → VERIFICATION | CANDIDATE_READY | CORE | PENDING |
| OPTIMIZATION → EXPLORATION | REFRAME | CORE | PENDING |
| VERIFICATION → OPTIMIZATION | REGRESSION_FAILED | VERIFIER | PENDING |
| VERIFICATION → CONSOLIDATION | VERIFIED_RESULT | VERIFIER | COMPLETED |

The phase-transition caller is trusted Core code. This layer checks the typed
guard/actor/evidence envelope and CAS; it does not itself verify evaluator
provenance. RPDI-11 owns independent evaluator/protocol verification.

Status transitions are PENDING→READY only when persisted dependencies pass;
READY↔PAUSED uses human control; READY→COMPLETED/FAILED requires Core evidence;
PENDING/READY/PAUSED→CANCELLED is explicit human cancellation. Terminal status
does not change scientific phase. A later approved phase transition reopens a
nonterminal phase as PENDING. Adding REQUIRES/group dependencies after a node
leaves PENDING is rejected.

For readiness, `REQUIRES(A,B)` blocks B until A has a persisted verified
completion envelope. Every AND group must be satisfied; one completed member
satisfies OR, while failed alternatives remain in the graph. `DERIVED_FROM` and
`RELATED_TO` do not block scheduling.

## Deterministic planning and adaptive stop

Priority is descending `payload.scheduler.priority` (integer -1000..1000),
then phase policy (EXPLORATION, METRIC_DISCOVERY, VERIFICATION, OPTIMIZATION),
then smaller resource upper bound, then node ID. Resource defaults are
`agent_calls` for evidence lanes, `evaluator_calls` for verification, and
`experiments` for optimization. A node may set `payload.scheduler.upper_bound`
(positive integer <= 1,000,000). Planned candidates consume remaining budget in
that order; the durable queue repeats the hard check when claiming each job.

Adaptive stop is deterministic and not inferred from model prose: an explicit
stop request wins; active jobs yield WAITING; no eligible node plus zero
remaining resource yields BUDGET_EXHAUSTED; unmet prerequisites yield
DEPENDENCIES_BLOCKED; all nodes completed yields ALL_NODES_COMPLETE; otherwise
the scheduler reports NO_REMAINING_WORK. This PR does not invent numeric
plateau/quality thresholds for evidence-only research.

## PR #47/#52/#53 transfer reconciliation

- #47 `b009d6f`: read-only protected context path changes in the legacy Engine;
  no node phase or scheduling behavior to transfer. Revisit only when worker
  context assembly is routed through graph jobs.
- #52 `caafe5e`: `DiagnosticCapability` controls evaluator diagnostic
  visibility, not node scheduling/readiness. Keep for later evaluator work.
- #53 `fc0ab76`: causal-memory structure belongs to the Research Ledger and
  RPDI-10, not the scheduler.

No schema migration is added: v5 already stores node phase, status, payload,
revision and the canonical event log. Unknown persisted statuses fail closed
when the scheduler builds a snapshot. The event payload now carries the typed
changes needed to audit/replay transitions.

## Known execution-lane limit

The planner emits `EVIDENCE_WORKER`, `METRIC_DISCOVERY_WORKER`,
`LEGACY_ENGINE_ADAPTER`, and `INDEPENDENT_VERIFIER` lane labels. Only the
RPDI-05 evidence-only exploration adapter is executable in this codebase;
metric-discovery, optimization, and independent verifier adapters remain future
implementation work. This PR adds no claim of v2 optimization execution.
