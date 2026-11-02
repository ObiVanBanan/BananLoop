# RPDI-07: per-node phases and deterministic hybrid scheduler

## Graph lifecycle

`GraphRepository` keeps canonical phase/status changes in the graph transaction
and appends the typed mutation to the existing event log. Each status/phase
change carries the expected node revision and campaign graph revision. Replays
use the existing command/idempotency table. Phase is separate from a worker's
legacy Engine stage and from the worker queue's lease/status.

The phase matrix is documented in
[`../reviews/rpdi-07-scheduler-preflight.md`](../reviews/rpdi-07-scheduler-preflight.md).
Exploration evidence-only completion remains a trusted Core projection that
records hashes, fence, and evidence and moves the node directly to terminal
CONSOLIDATION without adding a score or evaluator requirement.

## Readiness and scheduling

- `REQUIRES` blocks its target until the source has a persisted verified
  completion envelope. Knowledge edges remain nonblocking.
- AND readiness requires every member; OR readiness requires one. The
  scheduler reads canonical node state instead of accepting completed IDs from
  a worker.
- Independent ready nodes from different phases can be planned at once. The
  deterministic priority, phase, budget and tie-break rule is described in the
  preflight record.
- `HybridScheduler.mark_ready()` commits PENDING→READY through graph CAS.
  `EvidenceOnlyAttemptAdapter.enqueue()` uses this gate before durable enqueue,
  with a node-revision key so unrelated graph edits do not create duplicate
  attempts.
- `DurableWorkerQueue.claim()` remains the final budget/lease/fence authority.
  A plan never reserves budget by itself.

## Adaptive stop

`SchedulerPlan.reason` reports `CONTINUE`, `WAITING_FOR_WORKERS`,
`ALL_NODES_COMPLETE`, `BUDGET_EXHAUSTED`, `DEPENDENCIES_BLOCKED`,
`NO_REMAINING_WORK`, or `STOP_REQUESTED`. It stops only on durable state,
budget snapshots, and explicit steering. Evidence-only work does not use
invented score-plateau thresholds.

## Limits

Only the evidence-only exploration lane has an executable adapter today.
Optimization and independent verification routes are explicit planner lanes,
not implemented execution adapters. The scheduler does not duplicate or
replace those components.

## Acceptance evidence

CI covers the guarded phase/status matrix, atomic revision/event behavior,
persisted REQUIRES and AND/OR readiness, independent nodes in different phases,
deterministic budget priority without plan overbooking, adapter readiness,
adaptive stop reasons, and rejection of late prerequisites.
