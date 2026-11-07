# v0.2 Full Linux acceptance — 2026-11-08

## Exact revision

Runtime/test SHA: `a6748e2bef8e753f727a451829101771fe6e17e5`.

The Exploration resume regression now dispatches two bounded calls through the parsed `v2 explore` CLI (`parser()` → `cmd_v2_explore` → campaign runtime), not by calling `run_v2_explore` directly. The test confirms durable job completion and that the second invocation receives the prior public experiment as its next task/evidence. Only the SIWC transport is mocked.

## Linux verification

Full offline Linux suite on the exact SHA:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 63.79 seconds. Skips: the pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` is unavailable, and the Windows ACL/reparse-point contract is deferred.

The integrated cross-layer acceptance still covers Exploration/Research Ledger, an independently approved evaluator protocol, owner-reviewed Optimization pivot, and the actual parsed `v2 optimize` CLI with DEV, VALIDATION, trusted HOLDOUT, and final report. Its optimizer and verifier transports are mocked; production bubblewrap and deterministic evaluator variants both run. No external model calls occur in offline acceptance.

## CI

On this exact SHA, Linux pytest on Python 3.11, 3.12 and 3.13, Chromium, and mutate-policy all passed. Windows Python 3.12 remained in progress at report time and is deferred for this stage.

## Live benchmark and limitations

The previously authorized Titanic run scored **153/196 = 78.06%** trusted HOLDOUT, below the 95% target. No retry or extra paid call was made. Monetary cost is `UNKNOWN` where the provider did not report it. This evidence does not prove a successful 95% benchmark or a live multi-iteration Exploration run. Other v0.2 Full acceptance criteria remain open; PR #135 remains Draft and unmerged.
