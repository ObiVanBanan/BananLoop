# Issue #33 security review

Date: 2026-10-10
Reviewed base: `61d427c3fe50dfef76bce36f437d4e5559757640`; scope also includes the documented bounded M5 A/B evidence.

## Trust boundaries reviewed

- Bootstrap agent declarations remain untrusted. `DIAGNOSTIC_ONLY` and `UNAVAILABLE` do not elevate evaluator trust.
- Reference trust requires all three explicit operator inputs: a manifest, its exact-byte SHA-256 digest, and an approver identity. Core rejects digest, protocol, case/input identity, baseline-value, and smoke-value mismatches.
- The candidate workspace does not receive the reference manifest. BananLoop stores its frozen bytes and the verification/readiness receipts under campaign state. Reports validate the manifest digest, self-hashed verification receipt, and readiness receipt hash; missing or altered sidecars force report trust to `UNVERIFIED`.
- Reference verification is scoped to DEV. It cannot establish validation/holdout trust, and a campaign with an unverified holdout remains `DEV_ONLY`.
- Replay remains restricted to DEV/bootstrap smoke, uses a disposable workspace, and does not mutate campaign science state. Hidden validation/holdout data remains outside agent-facing planning and replay.
- Model-role receipts must describe settings actually selected by a model-aware adapter. The reviewed diff rejects model/provider/reasoning metadata on deterministic command backends, preventing those profiles from being recorded as if the adapter had applied them. Codex role dispatch builds its argv from the selected role config; a local deterministic fixture verifies strong/high vs fast/low arguments without an external model call.
- Two authorized Pi Cloud attempts exposed a candidate-transfer error: `git apply` rejected the agent-workspace diff. The original handling allowed its `ProcessError` to escape and left the campaign marked `RUNNING` after the action was durably failed. The current fix converts Git process failures in `apply_agent_changes` to `WorkspaceError`; the engine records `CANDIDATE_TRANSFER_FAILED`, categorizes the experiment as `CANDIDATE_NOT_MATERIALIZED` / `INFRA_FAILED`, pauses the campaign, and preserves the champion. A regression verifies those durable state and accounting outcomes. The generated patch body was not retained in those two attempts, so the underlying malformed-diff cause remains unverified; no candidate was promoted.

## Findings and limits

No new exploitable security issue was found in the reviewed change. The operator identity is a recorded CLI assertion, not an externally authenticated signature; this contract proves pinning and consistency with measured inputs, not that the named person is authorized or that their labels are semantically correct. Campaign state is local to the operator account and is not protected against a hostile process with arbitrary filesystem access.

An integration test verifies successful receipt persistence and report linkage; a tamper test confirms trust downgrade. E0–E3 campaign evidence remains unverified until an actual operator-approved manifest is used. E4 was not run because this environment has no GPU/A1111 runtime.

Validation for this review: full local Windows `pytest tests` passed (exit 0) at `656b7c0`; three targeted regressions passed at `61d427c`. Four symlink-only cases separately skipped because Windows symlink creation is unavailable (`WinError 1314`). CI run [38045369671](https://github.com/ObiVanBanan/BananLoop/actions/runs/38045369671) for the reviewed base was in progress at report update.
