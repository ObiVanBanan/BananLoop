# Stochastic readiness security gate

## Outcome

No blocking or major security findings.

## Repair applied

The cleanup fallback originally risked following evaluator-created links while making read-only files writable. It now uses `lstat` and Windows reparse-point checks before permission recovery, refuses to chmod links/reparse points, and propagates cleanup failure safely. A sentinel-link regression exists, but this Windows host denied symlink creation with `WinError 1314`, so that test was skipped.

## Advisory

Evaluator process stdout/stderr can contain arbitrary untrusted text. The current `ProcessError` diagnostic is persisted in the readiness receipt and may be passed to the bootstrap repair agent. Avoid secrets in evaluator output; a future change could redact or explicitly frame diagnostics as untrusted data. This M2 change did not expand evaluator trust or modify that existing diagnostic pathway.
