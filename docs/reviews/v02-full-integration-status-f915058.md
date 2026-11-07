# v0.2 Full CLI-to-evaluator E2E — 2026-11-08

Implementation revision: `f915058e6e60ca2dc526686839e09419a3f01bf3`.

## Full Optimization child through the owner CLI

The RPDI-13 integrated acceptance test now runs the approved child via parsed user-facing `bananloop v2 optimize` arguments and `cmd_v2_optimize`, with a mock SIWC optimizer and a separately pinned mock SIWC integration verifier. The CLI uses the real frozen role registry, TeamFactory/manifest, durable worker queue, candidate adapter, Optimization runtime, graph lifecycle, trusted evaluator adapter, and report writer. It enforces the approved two-call allowance.

The test confirms the optimizer sees only the allowlisted source file and selected research hypothesis. The verifier receives only the Core public evidence summary and returns a typed `IntegrationVerifierOutputV1`. DEV evaluation, VALIDATION comparison, champion promotion, trusted HOLDOUT, final report persistence, and API/CLI read-model agreement all execute through the child.

Both acceptance variants passed: deterministic test runner and production `TrustedEvaluatorRunner` using real bubblewrap isolation. No external model calls were made. The separately tested model adapters remain the official SIWC transport; only the integration fixture replaces the transport.

## Verification

Full offline Linux suite on the exact implementation revision:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 63.60 seconds. Skips: unavailable pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` and Windows ACL/reparse-point contract. Focused CLI, Optimization, lifecycle, and integrated acceptance suite: **42 passed**, including both integrated modes.

No subscription calls were made. Titanic HOLDOUT remains 153/196 (78.06%), below the aspirational 95% target.

## Remaining limitations

This verifies the full mocked-transport Linux path through `v2 optimize`; the single authorized Titanic run did not reach 95%, and full live multi-iteration Exploration remains constrained by the already-spent call allowance. Windows remains deferred. PR #135 remains Draft and unmerged; this report does not claim full v0.2 acceptance.
