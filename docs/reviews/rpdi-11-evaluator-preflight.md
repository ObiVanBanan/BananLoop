# RPDI-11 evaluator verification implementation status

**Base:** `main@21dfeb0c6f11dc96a3c2b3add9b46bee50bc0d1d` (RPDI-10)
**Status:** implementation in progress; this document does not claim RPDI-11 DoD.

## Owner-directed gate decision

The project owner explicitly waived the additional independent RPDI-00 review
step and directed work to proceed. The waiver is not independent approval and
does not waive RPDI-11 technical evidence, tests, CI, per-PR review, or DoD.

## Implemented foundations

- Added content-addressed evaluator protocol and metric-discovery proposal
  contracts, including tier-manifest identities and a strict command-vector
  shape.
- Added append-only proposal, verifier-receipt, and integration-receipt tables
  created lazily on first use. A proposal is projected only from a durable,
  fenced researcher result and its graph completion event.
- Added pinned-role checks for the independent protocol verifier, distinct
  proposer/verifier instances, durable fenced decisions, and immutable approval
  digests.
- Added optional `approval_digest` to frozen evaluator refs while preserving
  approved-hash bytes when the field is absent or null.
- Added graph campaign identity binding and receipt-aware graph guards. The
  guard rejects caller-supplied verifier labels without stored receipts.

## Remaining blockers before RPDI-11 is complete

1. A trusted Optimization evaluator execution path is not implemented. The
   evaluator bundle and tier manifests are content-addressed, but Core does
   not resolve/verify bundle bytes, execute smoke/repeatability checks, or
   enforce tier-specific private-data access.
2. The integration receipt projection exists, but there is no supported
   integration-verifier enqueue/run path. Core does not yet validate real
   evaluator-result, budget, and artifact-byte receipts before accepting the
   role's check names.
3. `CampaignSpecV2.mode` is immutable. Protocol approval is campaign-local;
   authorized handoff into a separate Optimization campaign is owned by
   RPDI-12. The graph must not silently switch an Exploration campaign into
   Optimization.
4. Local Python in this environment lacks both Pydantic and pytest. Syntax
   compilation and AST parsing pass; behavior tests require the repository CI
   environment.

These blockers keep this branch in draft until the trusted execution and
integration evidence paths are implemented or the contract is explicitly
re-scoped in a reviewed change. The owner waiver allows work to continue; it
does not convert declarative role claims into verified execution evidence.

## Required final evidence

- Self-approval, unpinned verifier, forged/tampered receipt, stale source/spec,
  stale fence, cross-campaign receipt, and changed protocol bytes fail closed.
- Approval/freeze and integration completion are immutable, idempotent, and
  survive reopen/replay; graph transitions require the exact durable receipt.
- Worker capabilities cannot reach validation/holdout material through paths,
  symlinks, artifacts, or environment variables; only the trusted evaluator
  boundary can resolve protected tiers.
- Integration verification binds candidate/source snapshots, frozen protocol,
  evaluator result, budget settlement, artifact integrity, and regression
  policy evidence.
- Historical v1 hashes/serialization and existing v2 contracts remain stable;
  exact-head Linux and Windows CI passes.
