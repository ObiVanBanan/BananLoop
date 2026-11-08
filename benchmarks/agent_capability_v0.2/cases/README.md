# Public case layout for PR #135

**No scored cases, gold labels, or hidden evaluator assets are committed by this specification.**

Example public DEV-only directories, once tasks and rights have been reviewed:

```text
cases/
├── researcher/
├── optimizer/
├── independent_verifier/
└── integration_verifier/
```

These role IDs were observed in PR #135's integrated offline acceptance; their actual identities must be bound to an immutable `RoleRegistryV1` and `TeamManifestV1` for each campaign. The older PR #125 proposer/selector/diagnostic/implementer/bootstrapper/pivot-advisor designs must not be treated as separate runtime roles without explicit registry definitions and independently verified contracts.

A public case must pass `../schemas/case.schema.json` and carry provenance, source/license review, evaluator identity, capability grant, frozen role identity and resource caps.

Keep hidden tests, gold/oracle data, private VALIDATION/HOLDOUT cases and credentials in a separate evaluator-only store. Never relabel public examples as a sealed HOLDOUT.
