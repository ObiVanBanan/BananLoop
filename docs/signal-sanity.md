# Signal sanity trust boundary

Bootstrap may declare positive and negative evaluator `case_id` groups only when it can point to explicit project evidence. BananLoop independently checks that the evaluator's primary metric separates those groups in the expected direction on every A/A smoke repeat.

This check is **diagnostic only**. The bootstrap agent selects the case IDs, and the evaluator supplies the measurements; the core does not independently verify that the labels or values are grounded in trusted fixtures. A passing separation check therefore does not establish that the scorer measures the intended quality and must not upgrade a campaign's final assessment. Reports label this result `DIAGNOSTIC_ONLY` / `diagnostic_only_unverified_labels`.

When defensible examples are unavailable, the plan records `UNAVAILABLE` with a reason and no case IDs. The campaign may still run as DEV-only, and a target-reaching holdout remains reported as `DEV_ONLY`. Legacy campaigns with no signal-sanity fields retain their historical report semantics.

Promoting signal sanity to independent evidence requires a future core-verifiable provenance contract for fixtures and labels. Until then, do not interpret `DIAGNOSTIC_ONLY` as evaluator validation.
