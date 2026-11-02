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

`MutateGraph(command_id, idempotency_key, expected_graph_revision, changes, evidence_refs)` is synchronous in this spike. Evidence references are validated as non-empty strings, sorted/deduplicated, included in the canonical payload hash, and persisted in both the response and event. A repeated key with the same canonical payload returns the original response; the same key with a different payload is `IDEMPOTENCY_CONFLICT`. A stale expected revision or failed one-row revision CAS is `REVISION_CONFLICT`.

The graph has typed nodes and `REQUIRES`, `DERIVED_FROM`, and `RELATED_TO` edges. Only `REQUIRES` blocks scheduling and must be acyclic. AND groups require every required member; OR groups require one acceptable member. Group readiness must be based on verified status/evidence, never worker prose. Graph revision, command result, and event commit atomically. This spike exercises node/edge mutation and cycle rollback; AND/OR readiness and full typed API schemas remain design outputs for RPDI-01/02.

## Spec and phase contract

`CampaignSpecV2` is discriminated by `schema_version=2` and `mode=EXPLORATION|OPTIMIZATION`. Exploration permits `evaluation=null` and produces evidence-only packages without a synthetic reward. Optimization requires an immutable frozen evaluator reference. Existing v1 serialization and approved hashes remain byte-for-byte untouched by future adapters.

`NodeResearchPhase` is independent of node execution status and legacy Engine attempt stage. The evidence-only terminal path represented here is `EXPLORATION -> CONSOLIDATION`, with evidence references, artifact digests, limitations, and a next experiment; its package carries `protocol_id=null` and no `reward`.

## Job, lease, and budget status contract

| Entity | States exercised by this spike | Guard / consequence |
|---|---|---|
| Job | `READY -> LEASED -> DISPATCHED -> COMPLETED|UNKNOWN`; expired pre-dispatch `LEASED -> READY` | Claim atomically allocates a monotonically increasing fence, lease, immutable request hash, per-fence reservation, and event. Dispatch is a separately persisted boundary. |
| Reservation | `RESERVED -> RELEASED` before dispatch; `RESERVED -> DISPATCHED -> SETTLED|UNKNOWN` after dispatch | `spent + reserved + unknown + requested <= cap`; unknown usage is retained against the cap. Every claim generation has a distinct reservation. |
| Worker result | current, unexpired fence only | Stale or expired fence cannot publish. Result, event, and measured budget settlement commit atomically. |

The prototype's atomic claim uses one Core-owned SQLite transaction. A deterministic injected exception before commit verifies that no job state, reservation, or event leaks. Concurrent claims are serialized by SQLite and cannot over-reserve. A recovery pass releases only expired leases that have no durable dispatch marker; an expired dispatched job becomes `UNKNOWN`, remains charged against the cap, and is not claimable. External providers may be called only after `DispatchJob` commits. Result settlement is fenced and idempotent. Provider-authoritative reconciliation of `UNKNOWN`, lease renewal, multi-process worker supervision, and production caller ordering remain gated work.

## Child campaign budget escrow

Parent-side allocation is reserved before touching the registry or child DB. The parent persists the stable allocation ID, child ID, canonical child DB path, parent identity, amount, approved scope JSON/digest, and immutable child-spec JSON/digest. The scope digest is recomputed from canonical scope JSON, and the child spec's autonomy digest must match. The child DB stores the same lineage tuple; SQLite triggers reject lineage updates/deletes. The registry mirrors it as a locator. A mismatch moves the parent allocation to `RECONCILIATION_REQUIRED` and leaves escrow held. The stages are `RESERVED -> REGISTRY_INITIALIZING -> CHILD_READY -> ACTIVE`; reconciliation repeats committed steps without downgrading parent state. The registry is activated before the parent publishes `ACTIVE`; admission requires all three records to agree. The deterministic spike injects crashes after reserve, registry insertion, child bootstrap, registry activation, and parent activation. Parent root budget counts escrow until an authoritative settlement policy permits return. A child cap is no greater than its allocation. Unknown external spend is not refundable.

The prototype injects crashes at each committed boundary, reconciles twice, and asserts one allocation, one registry row, matching child lineage, and unchanged root escrow. It also checks that an activation gap denies child admission, scope/spec mismatches are rejected before escrow, lineage is immutable, and registry identity conflicts fail closed without refund. It does not implement settlement/refund or claim crash proof on Windows or across real power loss.

## v4→v5 migration spike

The fixture is built through the repository's real v4 `CampaignStore` and populated with a v1 campaign, evaluator binding, action, reservation, event, and evaluation receipt. The migration first obtains SQLite's single-writer reservation with `BEGIN IMMEDIATE`; while that reservation is held, a separate read connection copies a consistent pre-migration backup. A deterministic competing-writer test pauses immediately after lock acquisition, proves the writer remains blocked, and verifies the backup contains the pre-writer state while the live DB receives the later write. The migration then adds v0.2 graph/job tables and advances `user_version` only at the commit boundary. A failure injected after table creation must roll back the new schema and preserve v4. The success fixture compares legacy `spec_hash`, evaluator binding hash, receipt bytes, actions, reservations, events, and the pre-upgrade backup, and checks referential integrity. The v1 `CampaignStore` intentionally rejects schema v5; opening upgraded databases through a v2 adapter remains a required RPDI-02 integration proof.

## Contract gate evidence and remaining proof

Run from repository root:

```sh
python3 -m unittest discover -s spikes/rpdi00 -v
```

The tests cover graph state/event rollback and idempotency, stale revision, reservation/lease rollback and recovery, parallel budget caps, unknown usage, fenced result settlement, evidence-only spec/package behavior, child escrow recovery, and v4 migration preservation/rollback. A successful local run is necessary but not sufficient to authorize RPDI-01/02: a reviewer must accept this contract package, then platform CI must repeat the suite on Linux and Windows. Production API transport, provider reconciliation, AND/OR readiness, and production worker supervision remain gated work.

## Proposed typed API boundary

```text
MutateGraph(command_id, idempotency_key, expected_graph_revision, changes, evidence_refs)
ClaimJob(worker_id, job_id, expected_job_revision, capability_version)
DispatchJob(job_id, fence)
CompleteJob(job_id, fence, output_digest, receipt, evidence_refs, protocol_version?)
ProposeProtocol(proposal) -> independent verifier -> FreezeProtocol(version, digest)
CreateChild(allocation_id, parent_campaign_id, child_id, approved_scope, approved_scope_digest, child_spec, amount) -> recoverable saga
```

Stable error codes: `REVISION_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `STALE_LEASE`, `INVALID_GRAPH`, `CAPABILITY_DENIED`, `BLOCKED_UNSAFE`, `BUDGET_EXHAUSTED`. State changes and errors are persisted where a command may have been accepted. SSE is at-least-once notification; durable event IDs are per campaign, and snapshot/replay share an authorized revision-consistent cursor.
