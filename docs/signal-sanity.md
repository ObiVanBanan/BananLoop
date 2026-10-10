# Signal sanity trust boundary

Bootstrap may declare positive and negative evaluator `case_id` groups only when it can point to explicit project evidence. BananLoop independently checks that the evaluator's primary metric separates those groups in the expected direction on every A/A smoke repeat.

This check is **diagnostic only**. The bootstrap agent selects the case IDs, and the evaluator supplies the measurements; the core does not independently verify that the labels or values are grounded in trusted fixtures. A passing separation check therefore does not establish that the scorer measures the intended quality and must not upgrade a campaign's final assessment. Reports label this result `DIAGNOSTIC_ONLY` / `diagnostic_only_unverified_labels`.

When defensible examples are unavailable, the plan records `UNAVAILABLE` with a reason and no case IDs. The campaign may still run as DEV-only, and a target-reaching holdout remains reported as `DEV_ONLY`. Legacy campaigns with no signal-sanity fields retain their historical report semantics.

## Operator reference manifests

An operator can supply a frozen JSON manifest to `bananloop goal` or `bananloop start` with `--reference-manifest`, `--reference-manifest-sha256`, and `--reference-approved-by`. All three are required. The digest is computed over the exact file bytes and pins the operator's approval; BananLoop copies those bytes into campaign state and records the approver, manifest digest, and verification receipt.

The version 1 manifest contains `protocol_sha256`, `primary_metric`, `direction`, `case_source`, `label_source`, `reference_basis`, and `cases`. Each case row contains `case_id`, `repetition`, `input_sha256`, `label` (`positive` or `negative`), `expected_metric_value`, and optional `tolerance`. The core checks that the protocol and complete case/input set match the readiness baseline, every measured baseline and smoke value is within its approved tolerance, and smoke runs include separated positive and negative groups.

This currently validates the DEV tier only. A campaign with a holdout remains `DEV_ONLY` until holdout has its own independent provenance contract. An operator manifest is an explicit trust assertion: BananLoop verifies its pin and consistency with measurements, but cannot establish that the approver's fixtures or labels are meaningful. Do not use a constructed manifest to claim independent validation.
