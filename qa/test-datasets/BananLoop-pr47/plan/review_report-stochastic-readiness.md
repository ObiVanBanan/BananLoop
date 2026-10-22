# Stochastic readiness review

## Findings and disposition

- **P1 — ignored state could cross A/A repeats (fixed):** repeats now use a fresh clone of the frozen bootstrap snapshot. Evaluator-referenced assets are copied into that clone as needed, and the source managed workspace is not reset or cleaned between runs. A regression proves ignored `.smoke-state` does not leak into the next repeat.
- **P1/P2 — paired noise used primary metric units (fixed):** paired-case range now uses the paired metric's own `worst_case_tolerance`; that threshold and result are included in the receipt. A regression uses distinct primary/paired scales.
- **Advisory — empirical bound terminology:** the reported bound is observed max-min over three runs, not a statistical confidence bound. Completion documentation states this limitation.
- **Coverage note:** after review, explicit tests were added for repeat bounds and unchanged `CampaignSpec.approved_hash()`.

The issues were repaired through the implementation agent after the single review pass. No second review was run. Final readiness/bootstrap selection: 27 passed, 1 platform-skipped.
