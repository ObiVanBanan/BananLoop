# Issue #33 security review: M0, first M1 slice, and M2 replay

## Trust boundary

Bootstrap agents can propose evaluator code, cases, labels, and signal-sanity groups. The BananLoop core runs readiness checks and records their results. Because the agent can choose the cases and labels, these checks do not establish independent business-goal validity.

## Findings and mitigations

- **Self-authored labels could be mistaken for trusted evidence.** Reports now keep bootstrap evidence at `UNVERIFIED`; a technical `GOAL_MET` is not rendered as a validated scientific outcome.
- **Readiness evidence could lose its provenance.** The campaign spec records hashes for the readiness receipt, protocol, case-input manifest, and runtime descriptor, alongside the checks that passed.
- **Evidence metadata could silently change campaign identity.** The additive evidence field is excluded from the approved spec hash, retaining compatibility with existing action hashes.
- **Replay could mutate campaign state or expose protected evaluator tiers.** Replay only accepts completed DEV actions or retained bootstrap smoke results. It validates receipt/result hashes and candidate linkage, runs in a disposable worktree, writes a separate replay receipt, and does not update campaign state. Validation and holdout replay requests are rejected.
- **Bootstrap smoke results could be unverifiable later.** The readiness runner retains evaluator assets and result sidecars under the state directory with hashes, case/input identity, and runtime metadata.
- **A malformed generated test fixture blocked CI collection.** The payload is now constructed before source interpolation; it no longer embeds a multiline expression inside the f-string.

## Limitations

No operator-approved frozen reference manifest or independent oracle is implemented by this slice. `CALIBRATED` and `INDEPENDENTLY_VALIDATED` remain schema values for future evidence, and this code does not assign them. Smoke/full command equivalence remains unimplemented. Existing leakage guards and hidden split behavior were not changed. Replay checks whether a result can be reproduced under the recorded runtime; it does not make self-authored cases or labels independent evidence.

## Review result

No new path exposes VALIDATION or HOLDOUT payloads to agents. Full local tests pass. Cross-platform CI for the replay increment remains pending.
