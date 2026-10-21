# ADR: immutable evaluator binding and receipts

## Status

Proposed P1 implementation on `codex/evaluator-binding-v1`, based on the user's roadmap for the next step after PR #32.

## Context

`CampaignSpec.approved_hash()` freezes the current evaluator commands, policy, budgets and evaluation assets as one campaign contract. Evaluations also have durable action requests, candidate proofs and stored results. Reports do not yet join those records into a versioned evaluator identity and a per-run provenance receipt. A reviewer cannot directly answer which candidate diff, protocol, runner/runtime descriptor, input manifest and result produced a stored decision.

## Decision

Add a versioned `EvaluatorBinding` derived from the immutable `CampaignSpec`, plus an `EvaluationReceipt` for every newly executed evaluator action (baseline, DEV, validation and holdout). Keep the existing campaign spec JSON, schema version and approved hash unchanged. Binding and receipts are additive records in SQLite and canonical JSON sidecars in the state artifact directory.

The first binding is immutable for the lifetime of a campaign. It records a stable binding ID, goal fingerprint, source snapshot, evaluator commands/timeouts by tier, protocol/spec hash, metric and decision policy, evaluation asset hashes, and the available runner/supervisor runtime descriptor. The runtime descriptor is intentionally bounded: it fingerprints the BananLoop interpreter, OS/architecture and resolved runner executable; it does not attest arbitrary child processes, GPU driver state, remote services or secret environment values.

An evaluation receipt records schema version, action ID, tier, candidate snapshot and candidate-proof hash, binding ID/hash, protocol hash, runtime fingerprint, case/input manifest hash, canonical result hash, artifact-manifest hash and successful exit code. Hashes use canonical UTF-8 JSON with sorted keys and compact separators. Receipt contents contain identifiers and digests, not hidden input payloads or credentials.

The receipt is saved atomically with the completed evaluator action in SQLite. A canonical `evaluation-receipt.json` sidecar is written under that action's existing private artifact directory. SQLite is authoritative if a crash leaves a sidecar missing; replay must use the durable action response and receipt rather than inventing a new measurement.

## Compatibility and recovery

- Do not add the binding ID to existing action request hashes; completed actions created before this change must remain replayable.
- Old campaigns receive an additive binding only when first opened by the new engine. Existing evaluations/actions with no historical runtime evidence remain explicitly unreceipted; never backfill them with a present-day runtime claim.
- New evaluator actions require the persisted binding to match the current bounded runtime/protocol fingerprint. A mismatch pauses as infrastructure and does not produce a scientific decision.
- Recheck frozen evaluator assets immediately after the evaluator returns and before accepting its result/receipt. Any drift discards the measurement, fails the action and pauses infrastructure.
- Existing `EvaluationResult`, acceptance policy, campaign schema hash, and campaign budgets stay unchanged. Stochastic calibration and signal sanity remain the following P1 step.

## Alternatives

Putting receipt hashes into `CampaignSpec` would change approved hashes and break old campaign identity. Reconstructing receipts for old measurements would claim runtime provenance that was never recorded. Storing receipt data only in the filesystem would separate it from the transactional action response. The additive binding/receipt tables preserve replay while giving new runs durable provenance.

## Security and validation

Keep evaluator result and artifact payloads operator-only. Verify receipt hashes and action/candidate/tier links before report or replay use. Test legacy databases, repeated initialization, action replay after a completed measurement, tampered sidecars, runtime mismatch, post-evaluation asset drift, and all evaluation tiers.
