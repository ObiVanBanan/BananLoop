# BananLoop v0.2 full CLI-to-CLI Linux acceptance — 2026-11-08

## Exact implementation/test revision

`17f91493068a4201bc3c6316228370775cbab5c7`

The cross-layer acceptance now exercises a single campaign through the owner CLI and API:

1. Parsed `v2 explore` preflight creates the frozen v2 campaign and reports `READY_FOR_OWNER_APPROVAL` without provider dispatch.
2. The same parsed command resumes it with a two-call allowance; two real v2 runtime iterations persist graph, queue and Research Ledger evidence, and the second decision receives prior public evidence.
3. A separately pinned independent verifier approves the public evaluator protocol through the CLI flow. The test asserts that its context excludes both DEV and HOLDOUT case content.
4. The API records and executes the owner-approved Optimization pivot as a separate child.
5. Parsed `v2 optimize` executes candidate generation, DEV/VALIDATION, integration verification, champion promotion and trusted HOLDOUT, then produces a final report.

Only the SIWC researcher, protocol-verifier, optimizer and integration-verifier transports are mocked. The real campaign registry, role manifests, durable queue, capability checks, runtime, SQLite state, verifier authorities, API, CLI, evaluator, and report path are exercised. The deterministic runner and production bubblewrap variants both pass; no external model calls occur in this offline acceptance.

## Verification

Full offline suite on this exact SHA:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 63.86 seconds. Skips: unavailable pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` and deferred Windows ACL/reparse-point contract.

CI on this exact code/test SHA passed Linux pytest on Python 3.11, 3.12 and 3.13, Chromium, and mutate-policy. Windows Python 3.12 was still running when checked and remains deferred for this Linux stage.

## Live benchmark and remaining limits

The previously authorized Titanic run scored **153/196 = 78.06%** on trusted HOLDOUT, below 95%. It was not retried; no additional paid calls were made, and unreported monetary cost remains `UNKNOWN`. This does not prove the aspirational target or a live multi-iteration campaign. Remaining v0.2 Full criteria are open. PR #135 stays Draft and unmerged.
