# Case data layout

This directory is intentionally not populated with scored task payloads yet.

## Public development data

After source/license review, public DEV cases may be organized as:

```text
cases/
├── researcher/
├── proposer/
├── selector/
├── implementer/
├── diagnostic/
├── campaign_bootstrapper/
└── pivot_advisor/
```

Every public case must validate against `../schemas/case.schema.json` and bind an immutable source revision, evaluator digest, role/output contract, capability profile, and resource budget.

## Private evaluator data

Never commit any of the following to this public repository:

- expected answers or gold claim labels for private splits;
- hidden tests, test patches, reference solutions, or oracle candidate outcomes;
- VALIDATION or HOLDOUT task inputs/labels;
- evaluator secrets, credentials, private source annotations, or private incident data.

Keep private oracles and non-public splits in an access-controlled evaluator store. The public manifest may contain only opaque references and SHA-256 digests. The evaluator must verify that the private artifact matches the frozen digest before scoring.

A public DEV case is not a hidden test. Do not relabel public data as HOLDOUT.
