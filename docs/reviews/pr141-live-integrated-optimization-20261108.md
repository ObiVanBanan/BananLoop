# PR #141 Linux live integrated Optimization acceptance — 2026-11-08

## Result

The opt-in integrated acceptance passed on Linux with the production bubblewrap evaluator:

```text
BANANLOOP_LIVE_INTEGRATED_OPTIMIZATION=1 \
  .venv/bin/python -m pytest -o addopts= -q -s \
  'tests/test_rpdi13_integrated_acceptance.py::test_exploration_research_handoff_child_agrees_through_api_and_cli[real-bwrap]'
1 passed in 19.54s
```

This exercised durable Exploration evidence, a separate pinned protocol approval, API owner approval of the frozen child, the Optimization CLI, a real SIWC Luna optimizer response, a real SIWC Luna integration-verifier response, trusted DEV/VALIDATION/HOLDOUT evaluation, campaign report generation, and API/CLI read-model agreement. The child campaign finished `ALL_NODES_COMPLETE`; its candidate became champion revision 1 and passed HOLDOUT at `0.8` on the one-case synthetic fixture (two repeatability runs).

The Exploration researcher and independent protocol verifier in this harness are deterministic fixtures. This is a live-model-backed integrated Optimization child, not an autonomous live Exploration campaign. The tiny synthetic evaluator result is execution evidence only, not a benchmark quality claim.

## Live calls

Both calls used the existing Sign in with ChatGPT connection and GPT-6 Luna. There were no automatic retries.

| Role | Request ID | Input | Output | Total |
| --- | --- | ---: | ---: | ---: |
| optimizer | `resp_0954bcbb72400549016af0921ca0e487d2bb27e4a8f97228bc` | 325 | 234 | 559 |
| integration verifier | `resp_007a75ada111b61d016af0922595b087d2a18cbc323430baf1` | 643 | 152 | 795 |

Total provider-reported usage was 1,354 tokens. Monetary subscription cost is `UNKNOWN`.

## Fix found during the live run

The first integrated live attempt returned all six integration checks as a JSON object of booleans. The strict `IntegrationVerifierOutputV1` contract requires an array of check-name strings, so Core correctly rejected that response and marked the dispatched verifier outcome `UNKNOWN`; it did not replay the call. The SIWC prompt now explicitly requires a JSON array and lists the exact six allowed names. A prompt-contract regression test was added, and the fresh campaign above passed. Two diagnostic verifier responses before the fix were not retried.

## Durable evidence

- Parent Exploration campaign: `c-fcd40493afb05da11f0f4f996f817c33` (`CONVERGED_REPEATED_EXPERIMENT`, three persisted findings; deterministic researcher fixture).
- Optimization child: `c-905465efabb50fe34983871bd3fb9ee5` (`ALL_NODES_COMPLETE`).
- Candidate snapshot: `b6b6b843335c91e0dcde0a04d85e2afc76ece8c9`; edited file content was `baseline ranker = 1`.
- Integration receipt: `f47adc8d5601386b04098debfb41d1ca1f49e339ed7e38754e152a45bdbaf6ba`.
- HOLDOUT receipt: `0299430e090a853a193d71c51b8bd7890820b47f0200515cfe03a4b8aa9d1f03`.
- Test state and SQLite receipts were under `/tmp/pytest-of-sonik/pytest-90/test_exploration_research_hand0/`; model output text was not copied into this report.

