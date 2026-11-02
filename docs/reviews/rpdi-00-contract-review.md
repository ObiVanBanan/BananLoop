# RPDI-00 Contract Review

**Review date:** 2026-11-02  
**Pull request:** [#117 — RPDI-00 contract spikes](https://github.com/ObiVanBanan/BananLoop/pull/117)  
**Reviewed base:** `71fb3581805fd799edfa71f05960387eccf751dd` (`codex/rpdi00-contract-spikes`)  
**Disposition:** `CHANGES_REQUIRED`  
**Review type:** internal implementation review; this is not an independent reviewer sign-off.

## Summary

The draft provides executable evidence for graph transaction/idempotency behavior, per-fence budget reservations, lease recovery and fenced result settlement, a representative v4-store migration fixture, and child-saga recovery across injected committed boundaries. The spike is isolated from `src/bananloop` and does not change current Engine behavior.

The implementation still does not establish production evaluator trust/capability enforcement, evidence provenance, provider-authoritative UNKNOWN reconciliation, child settlement/refund, or production authorization wiring. Migration compatibility through the future v2 adapter remains unverified. Those gaps make `CHANGES_REQUIRED` the appropriate disposition for the contract gate. Do not treat this review as approval to start RPDI-01/02; independent reviewer acceptance remains outstanding.

## Findings

### P1 — Migration backup ordering needs validation and v2-adapter reopen proof

At the reviewed base SHA, `spikes/rpdi00/migration.py:17-30` checked integrity and took the backup before `BEGIN IMMEDIATE`, allowing a concurrent writer to commit after the backup. The follow-up patch obtains the SQLite writer reservation before backup and copies from a second read connection. The fixture now creates a campaign through the real v4 `CampaignStore`, compares legacy values, and checks integrity and foreign keys. Current PR head `746e6f5de7e557abc5943b8426172fba5685d3a1` passes platform CI; opening through the future v2 adapter remains for RPDI-02.

**Still required:** add a deterministic concurrent-writer regression test and open the migrated v5 database through the eventual v2 adapter. The v1 `CampaignStore` correctly rejects schema v5, so v2 reopen is an RPDI-02 integration proof and remains unverified here. The full local suite cannot run in this environment because `pydantic`/`pytest` are unavailable; CI provides the full-suite evidence instead.

### P1 — Migration test does not yet establish post-migration adapter compatibility

At the reviewed base SHA, the fixture was synthetic and did not establish preservation against the actual v4 schema. The follow-up worktree patch now creates a v4 campaign through `CampaignStore`, exercises evaluator binding/action/receipt writes, and compares the spec hash, binding hash, receipt bytes, action/reservation/event counts, and foreign-key check after migration. This patch has not yet passed the test suite in the current environment because `pydantic` is unavailable.

**Still required:** verify required indexes and reopen through the supported v2 adapter once that adapter exists. Do not claim complete migration compatibility from the current spike alone.

### P1 — Child escrow settlement and trusted approval integration remain incomplete

The follow-up worktree now persists parent campaign identity, child path, allocation, approved scope and immutable spec/digests; requires a durable parent approval record before escrow; bootstraps child schema and lineage in one transaction; validates immutable lineage triggers; and rejects populated DBs without matching lineage or with extra lineage records. Identity conflicts quarantine the allocation with escrow held. Recovery is exercised at committed saga boundaries, including registry activation, and child admission checks parent, registry, and child DB agreement.

**Still required:** settlement/refund and authoritative child-spend reconciliation are not implemented. The approval record is a trusted-Core prototype and is not wired into a production operator/API authorization path. Child-saga tests pass locally and are covered by current-head CI.

### P1 — Lease fencing needs provider reconciliation and production boundary proof

At the reviewed base SHA, the spike had no lease reclaim or fenced result-commit path, and `mark_dispatched()` did not check lease expiry. The follow-up adds expiry recovery, fenced completion, idempotent result replay, and budget settlement in one transaction. Eighteen non-migration tests pass locally, covering these transitions and child saga recovery.

**Still required:** provider-authoritative UNKNOWN reconciliation, proof that each real dispatch caller waits for the durable dispatch commit, lease renewal for long calls, and multi-process/Windows failure testing. The spike's deterministic state machine is not evidence for production worker supervision or provider-specific recovery.

### P1 — Evaluator trust and protocol freeze are not executable

`validate_spec()` accepts any dictionary as an optimization evaluator reference (`core.py:27-40`). It does not check an immutable evaluator ID/version/digest, verifier receipt, or capabilities. There is no executable boundary showing that workers cannot access hidden evaluator data, and no protocol proposal/independent-verifier/freeze flow despite the ownership requirements in `CONTRACTS.md:10, 22, 63`.

**Required:** define the frozen evaluator reference and verifier-owned approval record, validate it in the spec path, and document/test the capability boundary. If this is explicitly deferred, mark the contract as unresolved and keep the contract gate closed until an owner accepts the deferred proof.

### P2 — Graph spike covers transaction basics, not full DAG semantics or typed API validation

The follow-up now accepts evidence references, validates/canonicalizes them, binds them into the idempotency payload, persists them in the result/event, and checks the revision CAS row count. The prototype still supports AddNode/AddEdge only; typed node validation, AND/OR readiness, authorization/scope checks, and typed API request/response validation remain unimplemented. The contract labels those capabilities as deferred design outputs; this spike must not be described as exercising the full graph/API contract.

**Required:** keep the deferred semantics explicit and require their executable tests in RPDI-01/02 before production claims.

### P2 — Evidence-only completion returns an unchecked, non-persisted package

`complete_exploration()` (`core.py:42-53`) checks only that evidence refs, artifact digest strings, and next experiment are non-empty. It returns a dictionary without verifying referenced artifacts/digests or persisting a receipt/event linked to the action, graph snapshot, or protocol. This is a useful shape sketch but does not establish provenance or integrity for completion evidence.

**Required:** specify what binds evidence to an action and graph revision, validate artifact digests/provenance at the trusted Core boundary, and persist an immutable completion record before claiming evidence integrity.

## Positive evidence

- Graph mutation and event insertion are in one transaction; tests cover evidence refs in idempotency/result/event, replay, stale revision rejection, and rollback on invalid/cyclic mutations.
- Claim-time reservation and lease creation are transactional; tests cover rollback before commit and concurrent budget-cap enforcement.
- Unknown spend remains charged against the cap in the exercised transition.
- Exploration output contains no synthetic reward field.
- The injected crash after child DB initialization can be reconciled without creating a second allocation in that specific scenario.
- The reviewed base head was a draft and kept spike code outside `src/bananloop`. Current head `746e6f5de7e557abc5943b8426172fba5685d3a1` passed all four Linux/Windows and Python CI jobs: 469 passed, 1 skipped, 7 subtests passed per job; critical mutation smoke passed. No production Engine files were changed.

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

After the reviewed base SHA, the worktree moved backup creation under the SQLite writer reservation, avoids backup path overwrite races, checks integrity and foreign keys before commit, adds migration-table foreign keys, replaces the synthetic fixture with one built through the real v4 store, adds lease expiry/recovery/fenced-completion behavior, and hardens child approval/lineage recovery. Current head `746e6f5de7e557abc5943b8426172fba5685d3a1` passed all four CI jobs with 469 passed, 1 skipped, and 7 subtests passed per job; critical mutation smoke passed. Eighteen non-migration tests pass locally; `py_compile` and `git diff --check` also pass. The full local suite cannot run because `pydantic` and `pytest` are unavailable and DNS access to PyPI failed. The real-store fixture starts from the repository's current v4 `CampaignStore` rather than a checked-in historical v3→v4 fixture, and the v2 adapter reopen proof remains for RPDI-02. The disposition remains `CHANGES_REQUIRED` because evaluator trust/capabilities, evidence provenance, provider reconciliation, child settlement/refund, production authorization, and independent reviewer acceptance remain unresolved.
