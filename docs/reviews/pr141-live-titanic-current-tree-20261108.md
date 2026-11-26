# PR #141 fresh Linux live Optimization smoke

Date: 2026-11-08  
PR #141 head tested: `f9f677f67b5df2c8fb86b701c18c703efa4dc8ec`  
Provider: Sign in with ChatGPT / GPT-6 Luna.

The live acceptance's optimizer prompt, Optimization runtime, Titanic test, and
its shared integration-runtime builder match the PR head's Git blob identities.
The local-only addition to `tests/test_evaluator_verification.py` was the
prompt-contract regression; it passed separately. Thus the live path exercised
the PR #141 code, while the additional regression is not claimed as part of
that head.

## Result

On the current working tree, the one-inference live Titanic v2 Optimization
acceptance passed:

```text
BANANLOOP_TITANIC_LIVE=1 BANANLOOP_TITANIC_LIVE_EPOCHS=1 \
  .venv/bin/python -m pytest -o addopts= -q -ra \
  tests/test_titanic_benchmark.py::test_titanic_v2_optimization_campaign_runs_to_terminal_holdout
1 passed in 18.41s
```

The campaign reached `ALL_NODES_COMPLETE`. The single real optimizer response
was applied and evaluated through trusted DEV, independent verification,
VALIDATION, and HOLDOUT. Trusted HOLDOUT evaluation produced accuracy
`0.7806122448979592` (78.06%). This confirms execution, not the 95% stretch
target. No automatic retries occurred and no budget limit was breached.

## Provider receipt

- Request: `resp_0ed62730aebfa4ae016af096c4049c87d2b2dad897eddeb0f7`
- Status: `COMPLETED`, dispatched once
- Usage: 13,741 input + 342 output = 14,083 tokens
- Campaign ledger: one agent call, 14,083 model tokens
- Monetary subscription cost: `UNKNOWN`

Machine-readable result from the test run:
`/tmp/pytest-of-sonik/pytest-93/test_titanic_v2_optimization_c0/campaign-state/live-smoke-result.json`.

## Offline check and environment limitation

The same offline campaign test was also invoked with the integration verifier
prompt regression. The prompt regression passed; the campaign test was skipped
because this host did not permit production bubblewrap namespaces. The live
acceptance above did run to completion with the trusted evaluator available.

```text
1 passed, 1 skipped in 0.38s
```

An earlier attempt in the restricted network sandbox stopped before model
inference because DNS access to `api.openai.com` was unavailable. The successful
run used network access for the already-authorized SIWC request; that first
attempt dispatched no inference and was not retried.
