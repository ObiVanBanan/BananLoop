# RPDI-00 Contract Review

**Review date:** 2026-11-02  
**Pull request:** [#117 — RPDI-00 contract spikes](https://github.com/ObiVanBanan/BananLoop/pull/117)  
**Reviewed base:** `71fb3581805fd799edfa71f05960387eccf751dd` (`codex/rpdi00-contract-spikes`)  
**Disposition:** `CHANGES_REQUIRED`  
**Review type:** internal implementation review; this is not an independent reviewer sign-off.

## Summary

The draft provides useful executable evidence for a narrow set of SQLite invariants: graph changes and events share a transaction, idempotent graph commands are replayed, a budget reservation and lease are atomic, unknown spend remains counted, and one injected child-campaign recovery boundary is repeatable. The draft is isolated from `src/bananloop` and does not change current Engine behavior.

The current evidence does not yet establish several ownership and recovery contracts needed to freeze the v0.2 design. In particular, the migration test uses a synthetic schema, the child allocation saga can leave escrow stranded on intermediate failures, and the lease contract has no fenced result-commit path. Evaluator trust and evidence integrity are mostly asserted by prose. These gaps make `CHANGES_REQUIRED` the appropriate disposition for the contract gate. Do not treat this review as approval to start RPDI-01/02; independent reviewer acceptance remains outstanding.

## Findings

### P1 — Migration backup ordering needs validation and v2-adapter reopen proof

At the reviewed base SHA, `spikes/rpdi00/migration.py:17-30` checked integrity and took the backup before `BEGIN IMMEDIATE`, allowing a concurrent writer to commit after the backup. A follow-up worktree patch now obtains the SQLite writer reservation before backup and copies from a second read connection. A local SQLite probe confirmed that this ordering blocks a concurrent writer while allowing the backup to complete. The migration test fixture has also been changed from handcrafted tables to a campaign created by the real v4 `CampaignStore`, with legacy rows compared after migration.

**Still required:** run the spike test suite on the patched worktree, add a deterministic concurrent-writer regression test, and open the migrated v5 database through the eventual v2 adapter. The v1 `CampaignStore` correctly rejects schema v5, so v2 reopen is an RPDI-02 integration proof and remains unverified here.

### P1 — Migration test does not yet establish post-migration adapter compatibility

At the reviewed base SHA, the fixture was synthetic and did not establish preservation against the actual v4 schema. The follow-up worktree patch now creates a v4 campaign through `CampaignStore`, exercises evaluator binding/action/receipt writes, and compares the spec hash, binding hash, receipt bytes, action/reservation/event counts, and foreign-key check after migration. This patch has not yet passed the test suite in the current environment because `pydantic` is unavailable.

**Still required:** execute the patched regression, verify required indexes, and reopen through the supported v2 adapter once that adapter exists. Do not claim complete migration compatibility from the current spike alone.

### P1 — Child saga can strand root escrow after an intermediate error

`spikes/rpdi00/core.py:262-321` commits the parent allocation as `RESERVED` before registry insertion and child initialization. A registry conflict, invalid child database, or initialization failure then raises without a durable failed/blocked state or compensation/reconciliation policy. The only injected crash point is after child initialization (`:305-306`), and the reconciler merely reruns the same function. The parent and registry are separately marked `ACTIVE` (`:307-315`) without checking affected rows or recording a shared transition event.

The proposed API includes `approved_scope_digest` (`CONTRACTS.md:64`), while the implementation accepts only IDs and amount. The child database also receives no immutable child spec, parent lineage, or allocation reference. Root escrow has no settlement/refund or authoritative spend-reconciliation path, so this spike does not yet establish child budget ownership semantics.

**Required:** specify durable saga states and recovery for every boundary; persist and validate immutable child scope/spec and lineage; make repeated reconciliation safe for all partial states; define settlement and unknown-spend accounting. Add fault injection across reserve, registry, child initialization, and activation boundaries.

### P1 — Lease fencing needs provider reconciliation and production boundary proof

At the reviewed base SHA, the spike had no lease reclaim or fenced result-commit path, and `mark_dispatched()` did not check lease expiry. The current worktree adds deterministic expiry recovery: pre-dispatch leases release their per-fence reservation and return to READY, while expired dispatched jobs become UNKNOWN and remain charged. It adds fenced completion, idempotent result replay, and budget settlement in one transaction. Nine non-migration spike tests pass locally, covering these transitions.

**Still required:** provider-authoritative UNKNOWN reconciliation, proof that each real dispatch caller waits for the durable dispatch commit, lease renewal for long calls, and multi-process/Windows failure testing. The spike's deterministic state machine is not evidence for production worker supervision or provider-specific recovery.

### P1 — Evaluator trust and protocol freeze are not executable

`validate_spec()` accepts any dictionary as an optimization evaluator reference (`core.py:27-40`). It does not check an immutable evaluator ID/version/digest, verifier receipt, or capabilities. There is no executable boundary showing that workers cannot access hidden evaluator data, and no protocol proposal/independent-verifier/freeze flow despite the ownership requirements in `CONTRACTS.md:10, 22, 63`.

**Required:** define the frozen evaluator reference and verifier-owned approval record, validate it in the spec path, and document/test the capability boundary. If this is explicitly deferred, mark the contract as unresolved and keep the contract gate closed until an owner accepts the deferred proof.

### P2 — Graph command omits evidence references and only tests a subset of the declared API

The declared signature includes `evidence_refs` (`CONTRACTS.md:16, 59`), but `mutate_graph()` accepts and hashes only `expected_revision` and `changes` (`core.py:109-112`). The prototype supports AddNode/AddEdge only; it does not implement evidence binding, typed node validation, AND/OR readiness, authorization/scope checks, or typed API request/response validation. The revision update at `core.py:143` does not verify the affected row count. These limitations are partly acknowledged in the document, but the implementation should not be described as exercising the complete declared command contract.

**Required:** align the frozen signature and implementation; bind evidence refs into the canonical command hash and persisted result, assert the CAS update, and label deferred graph semantics as design-only until tested.

### P2 — Evidence-only completion returns an unchecked, non-persisted package

`complete_exploration()` (`core.py:42-53`) checks only that evidence refs, artifact digest strings, and next experiment are non-empty. It returns a dictionary without verifying referenced artifacts/digests or persisting a receipt/event linked to the action, graph snapshot, or protocol. This is a useful shape sketch but does not establish provenance or integrity for completion evidence.

**Required:** specify what binds evidence to an action and graph revision, validate artifact digests/provenance at the trusted Core boundary, and persist an immutable completion record before claiming evidence integrity.

## Positive evidence

- Graph mutation and event insertion are in one transaction; the tests cover idempotent replay, stale revision rejection, and rollback on invalid/cyclic mutations.
- Claim-time reservation and lease creation are transactional; tests cover rollback before commit and concurrent budget-cap enforcement.
- Unknown spend remains charged against the cap in the exercised transition.
- Exploration output contains no synthetic reward field.
- The injected crash after child DB initialization can be reconciled without creating a second allocation in that specific scenario.
- The PR is a draft and keeps spike code outside `src/bananloop`; the reviewed head passed the recorded Linux and Windows CI runs, including the Windows full suite after fixing a backup connection handle leak.

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

After the reviewed base SHA, the local worktree was updated to move the SQLite backup under the migration writer reservation, add migration-table foreign keys, replace the synthetic migration fixture with one created through the real v4 store, and add lease expiry/recovery/fenced-completion behavior. Nine non-migration spike tests pass. The migration tests remain unverified because the environment lacks the project's `pydantic` dependency; `pytest` is also unavailable. These changes are uncommitted. The disposition remains `CHANGES_REQUIRED` until the full suite passes and the remaining gate evidence is obtained.
