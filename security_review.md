# Issue #33 security review

Date: 2026-10-09
Review base: `0690db48065cc91479836e3b7ac73980ddb2355f`; this review includes the receipt-persistence follow-up in the current diff.

## Trust boundaries reviewed

- Bootstrap agent declarations remain untrusted. `DIAGNOSTIC_ONLY` and `UNAVAILABLE` do not elevate evaluator trust.
- Reference trust requires all three explicit operator inputs: a manifest, its exact-byte SHA-256 digest, and an approver identity. Core rejects digest, protocol, case/input identity, baseline-value, and smoke-value mismatches.
- The candidate workspace does not receive the reference manifest. BananLoop stores its frozen bytes and the verification/readiness receipts under campaign state. Reports validate the manifest digest, self-hashed verification receipt, and readiness receipt hash; missing or altered sidecars force report trust to `UNVERIFIED`.
- Reference verification is scoped to DEV. It cannot establish validation/holdout trust, and a campaign with an unverified holdout remains `DEV_ONLY`.
- Replay remains restricted to DEV/bootstrap smoke, uses a disposable workspace, and does not mutate campaign science state. Hidden validation/holdout data remains outside agent-facing planning and replay.

## Findings and limits

No new exploitable security issue was found in the reviewed change. The operator identity is a recorded CLI assertion, not an externally authenticated signature; this contract proves pinning and consistency with measured inputs, not that the named person is authorized or that their labels are semantically correct. Campaign state is local to the operator account and is not protected against a hostile process with arbitrary filesystem access.

An integration test verifies successful receipt persistence and report linkage; a tamper test confirms trust downgrade. E0–E3 campaign evidence remains unverified until an actual operator-approved manifest is used. E4 was not run because this environment has no GPU/A1111 runtime.

Validation at review HEAD: full local test suite passed; CI run [37893403783](https://github.com/ObiVanBanan/BananLoop/actions/runs/37893403783) passed Ubuntu 3.11/3.12/3.13 and Windows 3.12.
