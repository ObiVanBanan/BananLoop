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
- Added `IntegrationVerificationAdapter` to create Core-derived evidence from
  paired baseline/candidate evaluator receipts, enqueue a pinned tool-free
  verifier, validate the stored evidence and role binding before dispatch, and
  persist an immutable decision receipt. A role PASS cannot override Core FAIL.
- Guarded `OPTIMIZATION → VERIFICATION` candidate readiness with exactly one
  `evaluator://receipt/<digest>` reference. Core checks the durable receipt
  against the campaign, frozen protocol, source snapshot, and candidate snapshot;
  the transition fails without it and leaves graph revision unchanged.
- Candidate readiness accepts DEV receipts only. Integration regression
  evidence accepts VALIDATION receipts only; both the adapter and storage
  authority reject HOLDOUT evidence from graph decisions.

## Implemented lifecycle slice

- Added `OptimizationAttemptCoordinator.evaluate_candidate` as a Core-owned
  entry point. It accepts a graph node ID, dispatches deterministic DEV
  repeatability jobs only while READY, finalizes their durable receipt, and
  atomically commits READY → COMPLETED plus OPTIMIZATION → VERIFICATION with
  the exact evaluator receipt reference.
- A replay after the graph transition revalidates and returns the durable
  receipt without rerunning jobs or adding graph events. Replay validation
  checks the pinned protocol ID/version, source snapshot, and DEV tier as well
  as campaign/spec/approval/protocol-digest/candidate bindings.
  DISPATCHED/UNKNOWN jobs fail as
  indeterminate and are not redriven.
- Negative replay tests reject a receipt with mismatched protocol ID/version,
  source snapshot, or a non-DEV tier.
- Added `OptimizationAttemptCoordinator.verify_candidate`: it accepts a
  READY VERIFICATION node and baseline VALIDATION receipt digest, validates
  that baseline against the pinned campaign, then runs candidate VALIDATION
  repeatability through the TrustedEvaluatorAdapter. It dispatches the
  resulting durable VALIDATION receipt through the pinned Integration
  Verification Adapter and projects PASS/FAIL through VERIFIED_RESULT or
  REGRESSION_FAILED in one graph mutation. The candidate DEV receipt remains
  the evidence for CANDIDATE_READY.
- If a verifier job is already COMPLETED after a process interruption, the
  coordinator recovers the immutable receipt from the durable queue result
  instead of dispatching again. DISPATCHED/UNKNOWN states fail closed.
- Tests cover PASS and FAIL phase routing, graph replay without duplicate
  dispatch, completed-job recovery, and UNKNOWN behavior.
- A separate end-to-end fixture uses the real IntegrationVerificationAdapter,
  EvaluatorProtocolAuthority, DurableWorkerQueue, GraphRepository, durable
  integration receipt and pinned PinnedRoleExecutorV1 binding. A deterministic
  executor callback supplies the role output. It exercises enqueue → claim →
  dispatch → settlement → receipt persistence → guarded graph transition and
  replay without adding another job, receipt, or graph event.
- The real fixture covers reviewer PASS, reviewer FAIL, and Core regression
  FAIL despite reviewer PASS. It also verifies stored evidence and receipts
  reject mutation through SQLite immutability triggers.
- A full Optimization lifecycle fixture creates real Git source/candidate
  commits and content-addressed evaluator assets, runs candidate DEV and
  VALIDATION jobs plus baseline VALIDATION jobs through the real
  TrustedEvaluatorAdapter and DurableWorkerQueue with a deterministic runner
  stub, then runs integration verification and graph projection. The real
  adapter performs snapshot materialization, private result storage/loading,
  and evaluator budget proof checks; only the sandbox runner and integration
  role's external executor callback are deterministic test stubs.
- A separate real-queue protocol workflow test completes a fenced researcher
  job, commits its evidence to the graph, creates the metric proposal through
  `record_completed_proposal`, then queues/runs the independent verifier via
  `EvaluatorVerificationAdapter` and a distinct pinned role instance. It
  persists and reloads the approval receipt. Executors pinned to the
  researcher role or lacking a manifest pin are rejected before dispatch, and
  a stale researcher fence is rejected. Self-approval contract rejection
  remains covered by its dedicated negative test.

## Remaining blockers before RPDI-11 is complete

1. Exact-head CI must pass after the real protocol workflow test, including
   Windows. Local Python lacks Pydantic and pytest, so behavior tests rely on
   repository CI.
2. `CampaignSpecV2.mode` is immutable. Protocol approval is campaign-local;
   authorized handoff into a separate Optimization campaign is owned by
   RPDI-12. The graph must not silently switch an Exploration campaign into
   Optimization.

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
