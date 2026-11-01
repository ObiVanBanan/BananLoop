# RPDI-00 — Contract & Executable Spikes

**Disposition:** draft contract freeze candidate. This prototype is isolated from `src/bananloop`; it does not claim v0.2 production support or alter v1 storage.

## Contract ownership and safety boundary

- The per-campaign trusted Core is the only canonical writer for graph, jobs, reservations, results, and events. One serialized command writer owns a campaign SQLite database; workers receive immutable job inputs and return outputs through a future adapter, never a SQLite connection or write path.
- Mutations use short `BEGIN IMMEDIATE` transactions. No model, tool, filesystem checkout, or other remote/slow operation runs inside a write transaction.
- The scheduler may select admissible work, but cannot raise budgets, change evaluator protocols, expand campaign scope, or bypass role/task/backend permissions.
- Agents may propose graph changes, evidence, or protocols. Only Core applies graph commands; only an independent trusted verifier can approve a frozen evaluator protocol. Missing required isolation fails closed.
- External-call outcome after dispatch may be `UNKNOWN`; reserved worst-case spend remains committed until authoritative reconciliation. Lease expiry alone does not prove a paid call did not happen.
- Hidden evaluator data, holdout labels, secrets, and raw private logs are excluded from worker capabilities and public events.

## Frozen command and graph contract

`MutateGraph(command_id, idempotency_key, expected_graph_revision, changes, evidence_refs)` is synchronous in this spike. Canonical payload hash is computed before opening the transaction. A repeated key with the same payload returns the original response; the same key with a different payload is `IDEMPOTENCY_CONFLICT`. A stale expected revision is `REVISION_CONFLICT`.

The graph has typed nodes and `REQUIRES`, `DERIVED_FROM`, and `RELATED_TO` edges. Only `REQUIRES` blocks scheduling and must be acyclic. AND groups require every required member; OR groups require one acceptable member. Group readiness must be based on verified status/evidence, never worker prose. Graph revision, command result, and event commit atomically. This spike exercises node/edge mutation and cycle rollback; AND/OR readiness and full typed API schemas remain design outputs for RPDI-01/02.

## Spec and phase contract

`CampaignSpecV2` is discriminated by `schema_version=2` and `mode=EXPLORATION|OPTIMIZATION`. Exploration permits `evaluation=null` and produces evidence-only packages without a synthetic reward. Optimization requires an immutable frozen evaluator reference. Existing v1 serialization and approved hashes remain byte-for-byte untouched by future adapters.

`NodeResearchPhase` is independent of node execution status and legacy Engine attempt stage. The evidence-only terminal path represented here is `EXPLORATION -> CONSOLIDATION`, with evidence references, artifact digests, limitations, and a next experiment; its package carries `protocol_id=null` and no `reward`.

## Job, lease, and budget status contract

| Entity | States exercised by this spike | Guard / consequence |
|---|---|---|
| Job | `READY -> LEASED -> DISPATCHED -> UNKNOWN` | Claim atomically allocates a monotonically increasing fence, lease, immutable request hash, reservation, and event. Dispatch is a separately persisted boundary. |
| Reservation | `RESERVED -> DISPATCHED -> UNKNOWN` | `spent + reserved + unknown + requested <= cap`; unknown usage is retained against the cap. |
| Worker result | current fence only | Stale or expired fence must not publish, promote, access evaluator, or mutate graph. Quarantine is allowed for diagnostics. |

The prototype's atomic claim uses one Core-owned SQLite transaction. A deterministic injected exception before commit verifies that no job state, reservation, or event leaks. Concurrent claims are serialized by SQLite and cannot over-reserve. Lease expiry/reclaim policy and successful measured settlement require later executable spikes before production implementation.

## Child campaign budget escrow

Parent-side allocation is reserved before touching the registry or child DB. `allocation_id` and `child_id` are stable idempotency keys. The saga stages are `RESERVED -> registry INITIALIZING -> child DB initialized -> ACTIVE`; reconciliation repeats each idempotent step. Parent root budget counts escrow until an authoritative settlement policy permits return. A child cap is no greater than its allocation. Unknown external spend is not refundable.

The prototype injects a crash after child DB initialization, then reconciles and asserts one allocation, one registry row, and unchanged root escrow. It does not claim crash proof on Windows or across real power loss; the local deterministic test validates restart logic at that boundary.

## v4→v5 migration spike

The migration fixture exercises additive schema creation under an exclusive SQLite transaction. It runs `integrity_check`, takes a SQLite backup before migration, creates v0.2 graph/job tables, and advances `user_version` only at the commit boundary. A failure injected after table creation must roll back the new schema and preserve v4. The success fixture verifies the campaign `approved_hash`, legacy evaluator binding hash, evaluation receipt bytes, and pre-upgrade backup remain intact. This is a minimal contract fixture, not a complete production migration or a substitute for opening representative campaign databases.

## Contract gate evidence and remaining proof

Run from repository root:

```sh
python3 -m unittest discover -s spikes/rpdi00 -v
```

The eight tests cover graph state/event rollback and idempotency, stale revision, reservation/lease rollback, parallel budget caps, unknown usage, evidence-only spec/package behavior, child escrow recovery, and v4 migration preservation/rollback. A successful local run is necessary but not sufficient to authorize RPDI-01/02: a reviewer must accept this contract package, then platform CI must repeat the suite on Linux and Windows. Production API transport, AND/OR readiness, stale-worker completion fencing, and lease reclaim remain gated work.

## Proposed typed API boundary

```text
MutateGraph(command_id, idempotency_key, expected_graph_revision, changes, evidence_refs)
ClaimJob(worker_id, job_id, expected_job_revision, capability_version)
DispatchJob(job_id, fence)
CompleteJob(job_id, fence, output_digest, receipt, evidence_refs, protocol_version?)
ProposeProtocol(proposal) -> independent verifier -> FreezeProtocol(version, digest)
CreateChild(allocation_id, child_id, approved_scope_digest, amount) -> recoverable saga
```

Stable error codes: `REVISION_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `STALE_LEASE`, `INVALID_GRAPH`, `CAPABILITY_DENIED`, `BLOCKED_UNSAFE`, `BUDGET_EXHAUSTED`. State changes and errors are persisted where a command may have been accepted. SSE is at-least-once notification; durable event IDs are per campaign, and snapshot/replay share an authorized revision-consistent cursor.
