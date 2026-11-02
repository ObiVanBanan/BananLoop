# RPDI-02 — SQLite v5 and graph mutation CAS

**Status:** implementation in progress on `codex/rpdi02-sqlite-v5-and-dag-cas`, based on `main@82db20c`.  
**Dependency:** RPDI-01 is merged as `82db20c`; its strict v2 spec parser is available to the v5 adapter.  
**Owner waiver:** the project owner waived the extra independent RPDI-00 reviewer step on 2026-11-02. This does not waive review, CI, tests, or RPDI-02 Definition of Done.

## Scope

- Add an explicit, lock-gated v4→v5 migration. Opening a legacy store must never silently migrate a database to v5.
- Create canonical graph metadata, nodes, typed edges, AND/OR groups, ordered memberships, and graph command idempotency records.
- Apply graph changes with a short `BEGIN IMMEDIATE` transaction that commits graph rows, revision, command response, and an existing `events` row atomically.
- Preserve the v1 `CampaignStore` and all v1–v4 migration behavior. The legacy store must reject v5 without downgrading `user_version`.
- Provide a v5 adapter that reads both preserved v1 campaigns and strict v2 campaign specs.

## Migration invariants

- The caller must hold the actual `CampaignLock` at `<database directory>/campaign.lock`.
- The migrator reserves the SQLite writer before reading the authoritative version or taking a backup.
- Backups use SQLite's backup API, use collision-safe names, and retain the committed v4 state on failed upgrades.
- DDL is executed statement by statement inside one transaction. `user_version=5` is set last and commits with the complete schema.
- Integrity checks, foreign-key checks, required columns, and indexes are verified before commit.
- Existing `CampaignStore` openings acquire a SQLite writer reservation before their final version check, preventing a concurrent legacy open from resetting a newly migrated v5 database to v4.

## Graph command contract

`MutateGraph` binds `command_id`, `idempotency_key`, `expected_graph_revision`, ordered typed changes, and evidence references into a canonical payload hash. Evidence references are nonempty, sorted, and deduplicated before hashing and event persistence. RPDI-02 records them but does not verify provenance; verifier checks and capability authorization remain owned by RPDI-06/11.

- Replaying the same idempotency key and payload returns the stored response without another graph revision or event.
- Reusing an idempotency key with different content or reusing a command ID conflicts.
- A stale graph revision conflicts; no implicit merge or LLM reconciliation occurs.
- `REQUIRES` edges must be acyclic. `DERIVED_FROM` and `RELATED_TO` may form knowledge cycles.
- AND requires every member to have trusted verified completion; OR requires at least one. The repository evaluates only caller-supplied verified member IDs, not worker prose or raw node status.
- Foreign keys, uniqueness constraints, revision CAS, command persistence, and the event append share one transaction.

## Verification required

- v4 campaign/spec hash, action request/response, reservation, evaluator binding/hash, exact receipt JSON, events, and backup rows survive migration.
- Unlocked and wrong-lock attempts leave v4 unchanged.
- Injected failures leave v4 authoritative and retain a valid v4 backup.
- A competing writer cannot alter the backup snapshot while migration owns the SQLite writer reservation.
- The legacy store rejects v5 without mutation; the v5 adapter reopens both v1 and v2 specs.
- Graph tests cover random DAGs, multi-parent edges, knowledge cycles, REQUIRES cycle rollback, AND/OR behavior, idempotency, stale revisions, and concurrent proposals.
- Linux and Windows CI pass before merge.

## Known verification limit

The local container lacks `pytest` and `pydantic`; local syntax checks and standard-library migration smoke checks do not replace CI. The required test suite must pass on the PR head.
