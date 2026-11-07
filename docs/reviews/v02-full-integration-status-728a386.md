# BananLoop v0.2 Linux acceptance — refutation, memory and full CLI route

## Exact implementation/test revision

`728a386dc2e3185c936cd9502d5466200bd0a25f`

The integrated single-campaign acceptance now exercises three Exploration iterations and demonstrates a failed hypothesis feeding a changed next decision:

1. `v2 explore` preflight creates the frozen campaign without model dispatch.
2. First matched-control hypothesis is recorded as `REFUTES` in durable public Research Ledger evidence.
3. On the next invocation, the researcher sees the refutation. It prefers an unrelated hypothesis, but Core selects a different evidence-linked follow-up. That second control also produces `REFUTES`.
4. The third iteration receives both refutations and executes the narrowed independent-condition follow-up, which produces `SUPPORTS` evidence for a distinct conditional claim. Convergence detection stops a repeated next action and the report records all three completed experiments.
5. The same campaign's protocol proposal is approved by a separate pinned verifier; owner approval and pivot execution create a distinct Optimization child through the API.
6. Parsed `v2 optimize` executes candidate generation, trusted DEV/VALIDATION/HOLDOUT, independent integration verification, champion promotion, and final reporting.

Only the SIWC transports are mocked. Campaign/role registry, queue, capability checks, runtime, SQLite ledger, independent verifier authorities, API, CLI, evaluator and report flow run as real components. Deterministic and production bubblewrap variants both pass; no external model requests are made in offline acceptance.

## Tests and CI

Full offline Linux suite on this exact SHA:

```sh
PYTHONPATH=tests/plugins:tests:src:. .venv/bin/python -m pytest -p isolate_home -o addopts='' -q -ra --tb=short
```

Result: **954 passed, 2 skipped, 7 subtests passed** in 64.13 seconds. Skips: pinned selector source commit `cd753c97c62148c3ccd3f030251e96a4d19b23bd` unavailable; Windows ACL/reparse-point contract deferred.

GitHub CI on the exact code/test SHA passed Linux Python 3.11, 3.12 and 3.13, Chromium and mutate-policy. Windows Python 3.12 remains deferred for this stage.

## Live benchmark and limits

The single authorized Titanic run scored **153/196 = 78.06%** on trusted HOLDOUT, below the 95% target. No retry or additional paid calls were made; unreported monetary cost remains `UNKNOWN`. This does not prove a successful 95% benchmark or live multi-iteration run. Remaining v0.2 Full criteria remain open. PR #135 stays Draft and unmerged.
