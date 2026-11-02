# RPDI-00 Contract Review

**Review date:** 2026-11-02  
**Pull request:** [#117 — RPDI-00 contract spikes](https://github.com/ObiVanBanan/BananLoop/pull/117)  
**Reviewed base:** `71fb3581805fd799edfa71f05960387eccf751dd` (`codex/rpdi00-contract-spikes`)  
**Disposition:** `CHANGES_REQUIRED`  
**Review type:** internal implementation review; this is not an independent reviewer sign-off.

## Summary

The draft provides executable evidence for graph transaction/idempotency behavior, per-fence budget reservations, lease recovery and fenced result settlement, a representative v4-store migration fixture, and child-saga recovery across injected committed boundaries. The spike is isolated from `src/bananloop` and does not change current Engine behavior.

The implementation still does not establish production evaluator trust/capability enforcement, evidence provenance, provider-authoritative UNKNOWN reconciliation, child settlement/refund, or production authorization wiring. Migration compatibility through the future v2 adapter remains unverified. Those gaps make `CHANGES_REQUIRED` the appropriate disposition for the contract gate. Do not treat this review as approval to start RPDI-01/02; independent reviewer acceptance remains outstanding.

## Proposed scope deferrals for independent reviewer disposition

These items are explicitly assigned to later rows of the approved RPDI backlog. This mapping records intended ownership; it does **not** waive their gates or claim they are implemented. The reviewer should accept or reject these deferrals before RPDI-01/02 begins.

| Open proof | Backlog owner | Required exit evidence |
|---|---|---|
| v2 adapter reopen and historical migration integration | RPDI-02 | v4→v5 golden DB opens through the v2 adapter; legacy hashes/receipts preserved; indexes/FKs verified; upgrade acquires the app-level `CampaignLock` and validates WAL-mode behavior. |
| Root/child budget settlement and safe return of unused escrow | RPDI-03 | Concurrent root+child accounting and crash reconciliation tests; UNKNOWN is never refunded without authoritative proof. |
| Provider-authoritative UNKNOWN reconciliation, dispatch ordering, renewal and worker restart supervision | RPDI-04 | Provider adapter reconciliation and worker-kill/restart tests; stale fences cannot commit. |
| Typed evaluator protocol, independent verifier approval and worker capability isolation | RPDI-06 and RPDI-11 | Self-scored protocols rejected; immutable verifier record and isolation tests pass; no hidden evaluator access. |
| Typed DAG node semantics, AND/OR readiness and full graph command validation | RPDI-02 | Property/concurrency tests cover cycles, multi-parent, OR failure, evidence binding, authorization and atomic event/CAS behavior. |
| Artifact-byte provenance and deterministic causal knowledge projections | RPDI-05 and RPDI-10 | Artifact bytes are verified against their digests and job/source snapshot; immutable evidence binds action/snapshot/protocol; holdout leakage tests and deterministic rebuild pass. |
| Production authorization path for supervised approvals and child pivot | RPDI-12 | Versioned scope + approval ID/digest are enforced by API/Core; unauthorized and out-of-scope paths fail closed. |

If a reviewer does not accept an item as a later-RPDI deferral, that proof must be brought into RPDI-00 before the contract gate can close.

## Findings

### P1 — Migration backup ordering needs validation and v2-adapter reopen proof

At the reviewed base SHA, `spikes/rpdi00/migration.py:17-30` checked integrity and took the backup before `BEGIN IMMEDIATE`, allowing a concurrent writer to commit after the backup. The follow-up patch obtains the SQLite writer reservation before backup and copies from a second read connection. The fixture now creates a campaign through the real v4 `CampaignStore`, compares legacy values, and checks integrity and foreign keys. Tested implementation head `bd12f466f5b88327bba4198e9e0279b76fe54024` passes platform CI; opening through the future v2 adapter remains for RPDI-02.

The follow-up now includes `test_v4_migration_backup_snapshot_precedes_blocked_concurrent_writer`, using a deterministic post-lock hook to prove a competing writer cannot commit before the backup snapshot. A standard-library concurrency probe passed locally, and the full regression passed in CI at `bd12f466f5b88327bba4198e9e0279b76fe54024`.

**Still required:** open the migrated v5 database through the eventual v2 adapter. The v1 `CampaignStore` correctly rejects schema v5, so v2 reopen is an RPDI-02 integration proof and remains unverified here.

### P1 — Migration test does not yet establish post-migration adapter compatibility

At the reviewed base SHA, the fixture was synthetic and did not establish preservation against the actual v4 schema. The follow-up worktree patch now creates a v4 campaign through `CampaignStore`, exercises evaluator binding/action/receipt writes, and compares the spec hash, binding hash, receipt bytes, action/reservation/event counts, and foreign-key check after migration. This patch has not yet passed the test suite in the current environment because `pydantic` is unavailable.

**Still required:** verify required indexes and reopen through the supported v2 adapter once that adapter exists. Do not claim complete migration compatibility from the current spike alone.

### P1 — Child escrow settlement and trusted approval integration remain incomplete

The follow-up worktree now persists parent campaign identity, child path, allocation, approved scope and immutable spec/digests; requires a durable parent approval record before escrow; bootstraps child schema and lineage in one transaction; validates immutable lineage triggers; and rejects populated DBs without matching lineage or with extra lineage records. Identity conflicts quarantine the allocation with escrow held. Recovery is exercised at committed saga boundaries, including registry activation, and child admission checks parent, registry, and child DB agreement.

**Still required:** settlement/refund and authoritative child-spend reconciliation are not implemented. The approval record is a trusted-Core prototype and is not wired into a production operator/API authorization path. Child-saga tests pass locally and are covered by current-head CI.

### P1 — Lease fencing needs provider reconciliation and production boundary proof

At the reviewed base SHA, the spike had no lease reclaim or fenced result-commit path, and `mark_dispatched()` did not check lease expiry. The follow-up adds expiry recovery, fenced completion, idempotent result replay, and budget settlement in one transaction. The local standard-library suite covers these transitions and child saga recovery; the full v4-fixture tests require the project runtime dependencies.

**Still required:** provider-authoritative UNKNOWN reconciliation, proof that each real dispatch caller waits for the durable dispatch commit, lease renewal for long calls, and multi-process/Windows failure testing. The spike's deterministic state machine is not evidence for production worker supervision or provider-specific recovery.

### P1 — Evaluator trust and protocol freeze are not executable

`validate_spec()` accepts any dictionary as an optimization evaluator reference (`core.py:27-40`). It does not check an immutable evaluator ID/version/digest, verifier receipt, or capabilities. There is no executable boundary showing that workers cannot access hidden evaluator data, and no protocol proposal/independent-verifier/freeze flow despite the ownership requirements in `CONTRACTS.md:10, 22, 63`.

**Required:** define the frozen evaluator reference and verifier-owned approval record, validate it in the spec path, and document/test the capability boundary. If this is explicitly deferred, mark the contract as unresolved and keep the contract gate closed until an owner accepts the deferred proof.

### P2 — Graph spike covers transaction basics, not full DAG semantics or typed API validation

The follow-up now accepts evidence references, validates/canonicalizes them, binds them into the idempotency payload, persists them in the result/event, and checks the revision CAS row count. The prototype still supports AddNode/AddEdge only; typed node validation, AND/OR readiness, authorization/scope checks, and typed API request/response validation remain unimplemented. The contract labels those capabilities as deferred design outputs; this spike must not be described as exercising the full graph/API contract.

**Required:** keep the deferred semantics explicit and require their executable tests in RPDI-01/02 before production claims.

### P2 — Evidence-only completion still needs artifact-byte verification

The original shape helper only checked non-empty fields. The follow-up adds `persist_exploration_completion()`: evidence refs must point to completed current-fence jobs for the same node; declared artifact digests must match their job results; each record stores the immutable request hash/fence; graph revision CAS, completion record, evidence rows, command result, and event commit in one transaction. Retries replay idempotently, and updates/deletes are rejected. Two new tests cover provenance, rollback, replay, and rejection of fabricated refs.

The spike still trusts the `output_digest` supplied to `complete_job()`; it does not open or hash artifact bytes or bind them to a source snapshot/protocol verifier. RPDI-05/10 must implement and test that artifact-store boundary before making production integrity claims.

## Positive evidence

- Graph mutation and event insertion are in one transaction; tests cover evidence refs in idempotency/result/event, replay, stale revision rejection, and rollback on invalid/cyclic mutations.
- Claim-time reservation and lease creation are transactional; tests cover rollback before commit and concurrent budget-cap enforcement.
- Migration holds the SQLite writer reservation through backup; deterministic concurrent-writer probe confirms backup reflects pre-writer state and the live database receives the later write.
- Evidence-only completion is persisted against current fenced job results with a graph revision, immutable provenance rows, and a replayable command/event.
- Unknown spend remains charged against the cap in the exercised transition.
- Exploration output contains no synthetic reward field.
- The injected crash after child DB initialization can be reconciled without creating a second allocation in that specific scenario.
- The reviewed base head was a draft and kept spike code outside `src/bananloop`. Tested implementation head `bd12f466f5b88327bba4198e9e0279b76fe54024` passed all four Linux/Windows and Python CI jobs: 470 passed, 1 skipped, 7 subtests passed per job; critical mutation smoke passed. No production Engine files were changed.

These are bounded prototype results. They do not resolve the P1 findings above or constitute production compatibility proof.

## Gate exit conditions

Before treating RPDI-00 as accepted:

1. Close or explicitly disposition each P1 finding with executable evidence or an accepted, named deferral that does not contradict the contract's normative claims.
2. Correct the migration lock/backup ordering and test a representative real v4 database through migration and reopen.
3. Complete the child saga state/recovery and ownership contract, including immutable scope/lineage and all relevant injected failure boundaries.
4. Demonstrate lease/fence checks at result publication and restart recovery for ambiguous dispatch.
5. Freeze the evaluator verifier/capability boundary and align the graph/evidence command contracts with what is actually implemented.
6. Obtain an independent reviewer acceptance of the contract package. The current PR has no recorded independent review; this report is not that acceptance.

Until those conditions are met and an independent reviewer accepts the package, the RPDI-00 contract gate remains closed and RPDI-01/02 should not begin.

## Follow-up worktree status

After the reviewed base SHA, the worktree moved backup creation under the SQLite writer reservation, avoids backup path overwrite races, checks integrity and foreign keys before commit, adds migration-table foreign keys, replaces the synthetic fixture with one built through the real v4 store, adds the deterministic competing-writer migration regression, adds lease expiry/recovery/fenced-completion behavior, hardens child approval/lineage recovery, binds graph evidence refs to idempotency/result/event with a checked revision CAS, and persists evidence-only completion provenance. Implementation head `f09796f3b2ef39b136070b77100f1ac9efd33568` passed all four CI jobs with 470 passed, 1 skipped, and 7 subtests passed per job; critical mutation smoke passed. The updated local worktree passes 21 non-migration tests (`PYTHONPATH=spikes/rpdi00` with migration fixtures excluded), the standard-library WAL concurrency probe, `py_compile`, and `git diff --check`. A full unittest discovery run executes 25 tests but four v4 migration fixture tests cannot import the project because `pydantic` is not installed in this environment. Commit `2723266cdb0e91469fdeffd0998999765c203cd5` adds the atomic node-state transition and legacy-node upgrade coverage; its Linux CI and mutation smoke passed, while Windows CI was still running at last check. The full local suite cannot run because `pydantic` and `pytest` are unavailable and DNS access to PyPI failed. The real-store fixture starts from the repository's current v4 `CampaignStore` rather than a checked-in historical v3→v4 fixture, and the v2 adapter reopen proof plus app-level `CampaignLock` integration remain for RPDI-02. The disposition remains `CHANGES_REQUIRED` because evaluator trust/capabilities, artifact-byte provenance, provider reconciliation, child settlement/refund, production authorization, and independent reviewer acceptance remain unresolved.

## Latest GitHub review status — 2026-11-02

GitHub recorded a `COMMENTED` review on PR #117 at `2026-11-02T08:17:07Z`, authored by `ObiVanBanan`, the same account that opened the PR. It is useful feedback but is **not independent sign-off** and does not change the `CHANGES_REQUIRED` disposition. The three code findings concern cap resets during `init_campaign()`, path-only registry uniqueness conflicts bypassing quarantine, and incomplete parent/registry/child path agreement during admission. Regression coverage and local fixes for these three findings are present in the current worktree; 24 non-migration tests pass. Four v4 migration tests remain unverified locally because `pydantic` is unavailable. CI for the follow-up changes has not yet run.

The evaluator/verifier finding is now called out directly in `spikes/rpdi00/CONTRACTS.md` as an unmet production invariant assigned to RPDI-06/11. `validate_spec()` still accepts arbitrary dictionaries, so this deferral remains subject to explicit independent reviewer acceptance. Keep PR #117 Draft and do not start RPDI-01/02 until that acceptance and all other gate conditions are satisfied.
