# Review report: M3 evaluator signal sanity

Date: 2026-10-05

## Finding

**P1, fixed:** bootstrap controls the positive/negative case IDs and the evaluator controls returned case metrics. The core could verify arithmetic separation but could not verify label provenance. Reporting such a pass as `signal_checked` would overstate the evidence.

## Resolution

The readiness receipt now marks passing group separation `DIAGNOSTIC_ONLY`. Campaign reports use `diagnostic_only_unverified_labels`; this status does not upgrade final assessment. A completion regression compares a target-reaching report before and after adding the diagnostic receipt and asserts the assessment is unchanged. `UNAVAILABLE` remains an explicit conservative path.

## Scope and validation

One review pass only; the implementation agent repaired the finding. No second review pass was performed. Core tests passed (50 passed, 1 skipped). Full suite retained the two known Windows child-process cancellation failures already present in the pre-M3 baseline.
