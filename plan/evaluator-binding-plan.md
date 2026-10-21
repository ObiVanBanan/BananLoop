# Implementation plan: EvaluatorBinding and evaluation receipts

## Scope

Implement the next P1 roadmap stage (`#33`) on top of PR #32 commit `9e2bb0f`. Add an immutable, versioned evaluator binding and end-to-end evaluation receipts. Preserve historical campaign hashes and durable action replay. Leave stochastic readiness/calibration, role profiles, hooks, MCP, and sandbox backends for later stages.

## M1: durable binding and evaluation provenance

1. **Contracts and canonical identity**
   - Add strict versioned Pydantic models for binding and receipt.
   - Derive a stable binding ID from the existing approved spec hash plus bounded runtime descriptor and runner configuration; do not change `CampaignSpec` hashes.
   - Derive case/input, result and artifact-manifest hashes from canonical structures.
2. **Additive state persistence**
   - Add immutable evaluator-binding and per-action receipt records to SQLite with additive schema creation/migration.
   - Persist the evaluator action response and receipt atomically.
   - Keep legacy action rows and campaigns readable; missing historical receipts remain absent.
3. **Engine integration**
   - Create/verify one binding before evaluation begins and stop with `PAUSED_INFRA` on a binding/runtime mismatch.
   - Attach receipts to baseline, DEV, validation and final holdout evaluator actions.
   - Write canonical receipt sidecars next to existing action artifacts.
   - Verify frozen evaluator assets again after the process returns and before persisting the measurement.
   - Preserve existing action request hashes and replay behavior.
4. **Operator reporting**
   - Include binding identity and receipt digests/status in JSON and Markdown reports without exposing hidden inputs, raw credentials, or new payloads.
5. **Regression tests and review**
   - Cover canonical hashes, receipt links, old DB migration, completed-action replay, missing legacy receipts, sidecar tampering, runtime mismatch, post-run asset mutation, and every tier.
   - Run focused tests and the full suite; compare any failures with platform baseline. Perform one review pass and a focused security pass.

## Definition of done

**Milestone status: implemented locally on `codex/evaluator-binding-v1`; not committed or pushed.**

- Reopening an existing campaign does not alter its `spec_hash` or action `request_hash` values.
- A new completed evaluator action has exactly one receipt tied to its action, candidate snapshot, binding and tier.
- A replay returns the same evaluation and receipt without invoking the evaluator again.
- A legacy completed evaluator action without a receipt is readable and is reported as legacy/unreceipted.
- Binding/runtime mismatch or evaluator-asset drift after execution is an infrastructure pause; it never creates a scientific accept/reject.
- JSON and Markdown reports expose hashes/status only for new receipt fields.
- Focused and full tests pass, except separately documented baseline platform failures.

## Out of scope

Automatic package/dependency graph attestation, remote/GPU runtime attestation, changing evaluator protocol versions mid-campaign, stochastic A/A calibration, signal sanity, product model profiles, hooks, MCP, and OS/container sandboxing.
