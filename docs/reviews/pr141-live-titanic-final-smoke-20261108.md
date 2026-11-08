# PR #141 Linux live Titanic Optimization smoke

Date: 2026-11-08  
Verified code revision: `a4d097fdc13bf4f1b143c2d37a988530f40fdbc0`  
Campaign: `c-22222222222222222222222222222222`  
Auth/model: Sign in with ChatGPT / GPT-6 Luna.

## Result

The current Linux one-inference E2E completed without errors:

```text
BANANLOOP_TITANIC_LIVE=1 BANANLOOP_TITANIC_LIVE_EPOCHS=1 \
  .venv/bin/python -m pytest -o addopts= -q -ra \
  tests/test_titanic_benchmark.py::test_titanic_v2_optimization_campaign_runs_to_terminal_holdout
1 passed in 13.89s
```

The v2 Optimization campaign reached `ALL_NODES_COMPLETE`. The real model
proposal was committed as a candidate, then passed trusted DEV evaluation,
independent verification, VALIDATION, and trusted HOLDOUT. HOLDOUT was
repeatable across two runs on 196 cases: accuracy `0.7806122448979592`
(78.06%). The 95% stretch target remains unmet.

## Provider and budget evidence

- One Luna response: `resp_07a2dff8fa94af0c016af08c4d163887d2af7fc0b10a4a2d99`.
- Provider usage: 13,741 input + 304 output = 14,045 total (125 reasoning).
- No retries and no separately billed API key.
- SQLite budget ledger: cap 65,000, spent 14,045, unknown 0, no breach.
- Monetary subscription cost: `UNKNOWN`.

The pytest artifact under the temporary campaign state recorded the provider
receipt, model proposal, candidate commit, final report, HOLDOUT receipt and
budget settlement. The candidate snapshot used the shortened content-key path
introduced for Windows-compatible Git access; this run verifies the same
current Linux path end to end.

## Boundaries

This confirms a real LLM-driven Optimization E2E on Linux, not a fully
autonomous live Exploration-to-Optimization campaign. Parent campaign
approvals and the integration-verifier response remain deterministic fixtures.
The held-out accuracy is below the requested stretch target, so the run proves
execution and verification, not achievement of 95% quality.
