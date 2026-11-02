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
- Added a Linux bubblewrap runner that resolves only the selected frozen tier,
  hashes the evaluator bundle, manifest, and case bytes, disables network, and
  bounds CPU, address space, output size, and output file count.
- Added `TrustedEvaluatorAdapter` to bind the Optimization spec to its stored
  approval, resolve content-addressed protocol assets, materialize candidate
  files from the approved Git commit under the campaign read-path allowlist,
  enqueue repeatability runs with vector budget reservations, verify durable
  queue settlements, and persist a final append-only evaluator run receipt.
- Evaluator output artifacts are retained in a private, content-addressed
  store. Public receipts and queue payloads contain digests, not artifact
  values, case IDs, scores, or evaluator logs.

## Remaining blockers before RPDI-11 is complete

1. The integration receipt projection still has no supported integration-
   verifier enqueue/run adapter. Core must derive deterministic candidate,
   evaluator run, budget, artifact-byte, and regression-policy evidence before
   an integration role's PASS can authorize a graph transition.
2. `CampaignSpecV2.mode` is immutable. Protocol approval is campaign-local;
   authorized handoff into a separate Optimization campaign is owned by
   RPDI-12. The graph must not silently switch an Exploration campaign into
   Optimization.
3. Exact-head CI must pass on the current revision. Earlier runs exposed two
   test-fixture/archive-mode failures; fixes are committed on the current head
   and CI is running. Local Python in this environment lacks Pydantic and pytest. Syntax
   compilation and AST parsing pass; behavior tests require the repository CI
   environment.

These blockers keep this branch in draft. The owner waiver allows work to
continue; it does not waive RPDI-11 technical evidence or convert role claims
into verified integration evidence.

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
