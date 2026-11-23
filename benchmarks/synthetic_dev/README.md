# Synthetic DEV acceptance

The canonical eight-case fixture and four-role matrix are defined by
`bananloop.agent_benchmark.prepare_demo`. Run a fresh CPU-only smoke as
documented in the [role benchmark README](../agent_capability_v0.2/README.md).
The fixture CLI completed 8 of 8 cases and reported `DEV_DIAGNOSTIC_ONLY`.

The runner tests cover wrong replies, empty responses, invalid evaluator
contracts, a runtime scorer failure, an interrupt during scoring, and an
attempt to overwrite existing results. The two interruption paths leave
no success report. The no-provider workflow runs the same tests.

```sh
python -m pytest -q tests/test_agent_capability_benchmark_spec.py \
  tests/test_agent_benchmark_runner.py tests/test_agent_benchmark_metrics.py
```

Verification covered 29 tests in these three suites. The fixture mode
made zero provider calls.
