# Issue #50 R1 diagnostic capabilities QA

## Scope

R1 adds typed, evaluator-declared aggregate diagnostic capabilities for ranking,
classification, performance, and regression-test evidence. It does not change
primary metric scoring, validation promotion, or holdout stopping rules.

## Data contract and isolation

- `CampaignSpec.agent_visible_diagnostic_capabilities` is a frozen allowlist;
  empty values are omitted from approved hashes to preserve legacy campaign hashes.
- `EvaluationResult.diagnostic_evidence` accepts only aggregate numeric values
  tied to a protocol SHA-256. Invalid optional diagnostics are dropped without
  invalidating the primary metric result.
- Adaptive agent views include evidence only when tier is DEV, the capability
  name/kind/metric matches the frozen allowlist, the evidence protocol digest
  matches the evaluation protocol, and the payload contains no extra fields.
- The agent view exposes only the aggregate value and core-attested DEV protocol
  provenance. It never exposes case IDs, row values, or labels. Validation and
  holdout views drop typed diagnostics.
- Ranking guidance identifies cutoff-only evidence as insufficient. Without
  metric-specific ranking diagnostics, analyst capsules are marked as
  unverified conjectures and their confidence is capped at 0.35. Matching
  aggregate ranking evidence removes that cap.

## Acceptance checks

- PR-AUC cutoff-only case: verified unknown evidence status, confidence ceiling,
  and conjecture labeling.
- Aggregate ranking evidence: verified capability and protocol matching allows
  a measured ranking hypothesis to retain its confidence.
- Provenance/isolation: tested exact protocol match, mismatched protocol,
  undeclared diagnostic, extra case-ID field, and validation/holdout tiers.
- Scoring invariance: identical metric/check inputs produce identical policy
  decisions and target status with or without typed diagnostic evidence.
- Hidden per-case inputs remain stripped from ordinary agent views; raw DEV
  examples remain limited to the isolated analyst path.

## Verification

- Focused suite covering research views, engine analysis, bootstrap, policy,
  Codex/Pi adapters, and metric semantics passed.
- Full repository test suite passed (`PYTHONPATH=src pytest -q`); the final
  analyst evidence/discriminator assertion passed independently after the suite
  run.
- `git diff --check` passed.

## Limitations

Diagnostic capability values describe evaluator-provided aggregates; this layer
does not compute ranking statistics itself. Evaluators must already support the
declared statistic and emit it under the frozen protocol.
